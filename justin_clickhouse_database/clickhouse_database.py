"""
clickhouse_database.py
----------------------
Store and retrieve article credibility check results in ClickHouse,
following the Article Credibility Checker schema (schema-diagram-tavily.jsx).

Four tables mirror the four output sections:
    articles              - Article metadata (url, title, publisher, date, ...)
    publisher_credibility - Publisher score and bias
    article_credibility   - Overall article accuracy score and writing quality
    claims                - Individual claim breakdowns with sources

Requirements:
    pip install boto3 clickhouse-connect python-dotenv

Environment variables (.env or shell):
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY
    AWS_REGION
    AWS_BUCKET
    CLICKHOUSE_HOST
    CLICKHOUSE_PORT
    CLICKHOUSE_USER
    CLICKHOUSE_PASSWORD
"""

import os
import re
import json
import tempfile
from pathlib import Path
from datetime import date

import boto3
import requests
import clickhouse_connect
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION            = os.getenv("AWS_REGION", "ap-southeast-1")
AWS_BUCKET            = os.getenv("AWS_BUCKET")

CLICKHOUSE_HOST       = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT       = int(os.getenv("CLICKHOUSE_PORT", 8123))
CLICKHOUSE_USER       = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD   = os.getenv("CLICKHOUSE_PASSWORD", "")

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def get_s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION,
    )

def get_ch_client():
    return clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
    )

# ---------------------------------------------------------------------------
# Setup — create all schema tables
# ---------------------------------------------------------------------------

def setup(ch=None):
    """Create all ClickHouse tables for the article credibility schema."""
    ch = ch or get_ch_client()

    # articles — article metadata extracted by tavily-extract
    ch.command("""
        CREATE TABLE IF NOT EXISTS articles (
            id           UUID     DEFAULT generateUUIDv4(),
            url          String,
            title        String,
            publisher    String,
            date         Date,
            author       String,
            section      String,
            is_opinion   UInt8,
            checked_at   DateTime DEFAULT now()
        )
        ENGINE = MergeTree()
        ORDER BY checked_at
    """)

    # publisher_credibility — generated after check
    ch.command("""
        CREATE TABLE IF NOT EXISTS publisher_credibility (
            id                   UUID DEFAULT generateUUIDv4(),
            article_id           UUID,
            score                UInt8,
            rating               String,
            summary              String,
            bias                 String,
            known_issues         Array(String),
            fact_checker_ratings Array(String)
        )
        ENGINE = MergeTree()
        ORDER BY article_id
    """)

    # article_credibility — generated after check
    ch.command("""
        CREATE TABLE IF NOT EXISTS article_credibility (
            id                             UUID DEFAULT generateUUIDv4(),
            article_id                     UUID,
            score                          UInt8,
            rating                         String,
            summary                        String,
            total_claims_found             UInt32,
            claims_true                    UInt32,
            claims_false                   UInt32,
            claims_mixed                   UInt32,
            claims_unverified              UInt32,
            government_source_only_flag    UInt8,
            writing_quality_sensationalism String,
            writing_quality_named_sources  UInt8
        )
        ENGINE = MergeTree()
        ORDER BY article_id
    """)

    # claims — individual claim breakdown; sources stored as JSON array string
    ch.command("""
        CREATE TABLE IF NOT EXISTS claims (
            id                     UUID DEFAULT generateUUIDv4(),
            article_id             UUID,
            claim_summary          String,
            extract                String,
            verdict                String,
            reason                 String,
            government_source_only UInt8,
            sources_json           String
        )
        ENGINE = MergeTree()
        ORDER BY (article_id, id)
    """)

    print("ClickHouse tables ready: articles, publisher_credibility, article_credibility, claims")

# ---------------------------------------------------------------------------
# Upload — store a full article credibility result (+ optional raw file to S3)
# ---------------------------------------------------------------------------

def upload(
    article: dict,
    publisher_credibility: dict,
    article_credibility: dict,
    claims: list,
    raw_filepath: str = None,
    ch=None,
) -> str:
    """
    Store a complete article credibility check result in ClickHouse.
    Optionally uploads a raw file (e.g. scraped HTML/text) to S3.

    Args:
        article: {
            url (str), title (str), publisher (str),
            date (date | "YYYY-MM-DD"), author (str),
            section (str), is_opinion (bool)
        }
        publisher_credibility: {
            score (int 0-100),
            rating (str: highly_credible | credible | mostly_credible |
                         questionable | not_credible),
            summary (str),
            bias (str: far_left | left | center_left | center |
                       center_right | right | far_right),
            known_issues (list[str]),
            fact_checker_ratings (list[str])
        }
        article_credibility: {
            score (int 0-100),
            rating (str: credible | mostly_credible | mixed |
                         mostly_false | false),
            summary (str),
            total_claims_found (int),
            claims_true (int), claims_false (int),
            claims_mixed (int), claims_unverified (int),
            government_source_only_flag (bool),
            writing_quality (dict: sensationalism (str), named_sources (bool))
        }
        claims: list of {
            claim_summary (str), extract (str),
            verdict (str: true | mostly_true | mixed | mostly_false |
                          false | unverifiable),
            reason (str), government_source_only (bool),
            sources (list of {name, url, type, is_independent})
        }
        raw_filepath: Optional local path to a raw file to upload to S3.
        s3_prefix:    S3 key prefix for raw file (default: 'articles').

    Returns:
        The UUID string of the inserted article row.
    """
    s3_prefix: str = "database"
    ch = ch or get_ch_client()
    s3 = get_s3_client()

    # Fetch the article URL as an offline HTML file and upload to S3
    article_url = article["url"]
    safe_name = re.sub(r"[^\w\-]", "_", article.get("title") or article_url.split("//")[-1])[:100] + ".html"
    print(f"Fetching HTML from '{article_url}'...")
    try:
        response = requests.get(
            article_url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"},
        )
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="wb") as tmp:
            tmp.write(response.content)
            tmp_path = tmp.name
        s3_html_key = f"{s3_prefix}/{safe_name}"
        print(f"Uploading offline HTML to S3 as '{s3_html_key}'...")
        s3.upload_file(tmp_path, AWS_BUCKET, s3_html_key, ExtraArgs={"ContentType": "text/html"})
        Path(tmp_path).unlink(missing_ok=True)
        print(f"HTML S3 URL: https://{AWS_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_html_key}")
    except requests.HTTPError as e:
        print(f"Warning: could not fetch HTML ({e}). Skipping S3 HTML upload.")

    # Upload any additional raw file to S3 if provided
    if raw_filepath:
        path = Path(raw_filepath)
        s3_key = f"{s3_prefix}/{path.name}"
        print(f"Uploading '{path.name}' to S3...")
        s3.upload_file(str(path), AWS_BUCKET, s3_key)
        print(f"S3 URL: https://{AWS_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_key}")

    # Normalise date
    article_date = article.get("date")
    if isinstance(article_date, str):
        article_date = date.fromisoformat(article_date)
    elif article_date is None:
        article_date = date.today()

    # 1. Insert article row
    ch.insert(
        "articles",
        [[
            article_url,
            article.get("title", ""),
            article.get("publisher", ""),
            article_date,
            article.get("author", ""),
            article.get("section", ""),
            int(bool(article.get("is_opinion", False))),
        ]],
        column_names=["url", "title", "publisher", "date", "author", "section", "is_opinion"],
    )

    # Fetch generated article UUID
    result = ch.query(
        "SELECT id FROM articles WHERE url = {u:String} ORDER BY checked_at DESC LIMIT 1",
        parameters={"u": article["url"]},
    )
    article_id = str(result.result_rows[0][0])

    # 2. Insert publisher_credibility row
    pc = publisher_credibility
    ch.insert(
        "publisher_credibility",
        [[
            article_id,
            int(pc.get("score", 0)),
            pc.get("rating", ""),
            pc.get("summary", ""),
            pc.get("bias", ""),
            pc.get("known_issues", []),
            pc.get("fact_checker_ratings", []),
        ]],
        column_names=["article_id", "score", "rating", "summary", "bias",
                      "known_issues", "fact_checker_ratings"],
    )

    # 3. Insert article_credibility row
    ac = article_credibility
    wq = ac.get("writing_quality", {})
    ch.insert(
        "article_credibility",
        [[
            article_id,
            int(ac.get("score", 0)),
            ac.get("rating", ""),
            ac.get("summary", ""),
            int(ac.get("total_claims_found", 0)),
            int(ac.get("claims_true", 0)),
            int(ac.get("claims_false", 0)),
            int(ac.get("claims_mixed", 0)),
            int(ac.get("claims_unverified", 0)),
            int(bool(ac.get("government_source_only_flag", False))),
            wq.get("sensationalism", ""),
            int(bool(wq.get("named_sources", False))),
        ]],
        column_names=[
            "article_id", "score", "rating", "summary",
            "total_claims_found", "claims_true", "claims_false",
            "claims_mixed", "claims_unverified", "government_source_only_flag",
            "writing_quality_sensationalism", "writing_quality_named_sources",
        ],
    )

    # 4. Insert claims rows
    if claims:
        rows = [
            [
                article_id,
                c.get("claim_summary", ""),
                c.get("extract", ""),
                c.get("verdict", ""),
                c.get("reason", ""),
                int(bool(c.get("government_source_only", False))),
                json.dumps(c.get("sources", [])),
            ]
            for c in claims
        ]
        ch.insert(
            "claims",
            rows,
            column_names=[
                "article_id", "claim_summary", "extract",
                "verdict", "reason", "government_source_only", "sources_json",
            ],
        )

    print(f"Stored result for '{article.get('title', article['url'])}' (id={article_id})")
    return article_id

# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------

def retrieve(url: str, ch=None) -> dict | None:
    """
    Retrieve the full credibility check result for a given article URL.

    Returns a dict with keys: article, publisher_credibility, article_credibility, claims.
    Returns None if the URL has no stored result.
    """
    ch = ch or get_ch_client()

    art_rows = ch.query(
        "SELECT id, url, title, publisher, date, author, section, is_opinion, checked_at "
        "FROM articles WHERE url = {u:String} ORDER BY checked_at DESC LIMIT 1",
        parameters={"u": url},
    ).result_rows

    if not art_rows:
        return None

    r = art_rows[0]
    article_id = str(r[0])
    title = r[2]
    s3_html_key = f"articles/{re.sub(r'[^\w\-]', '_', title or r[1].split('//')[-1])[:100]}.html"
    article = {
        "id": article_id, "url": r[1], "title": title, "publisher": r[3],
        "date": str(r[4]), "author": r[5], "section": r[6],
        "is_opinion": bool(r[7]),
        "s3_html_key": s3_html_key,
        "s3_html_url": f"https://{AWS_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_html_key}",
        "checked_at": str(r[8]),
    }

    pc_rows = ch.query(
        "SELECT score, rating, summary, bias, known_issues, fact_checker_ratings "
        "FROM publisher_credibility WHERE article_id = {a:String} LIMIT 1",
        parameters={"a": article_id},
    ).result_rows
    publisher_credibility = {}
    if pc_rows:
        r = pc_rows[0]
        publisher_credibility = {
            "score": r[0], "rating": r[1], "summary": r[2], "bias": r[3],
            "known_issues": list(r[4]), "fact_checker_ratings": list(r[5]),
        }

    ac_rows = ch.query(
        "SELECT score, rating, summary, total_claims_found, claims_true, claims_false, "
        "claims_mixed, claims_unverified, government_source_only_flag, "
        "writing_quality_sensationalism, writing_quality_named_sources "
        "FROM article_credibility WHERE article_id = {a:String} LIMIT 1",
        parameters={"a": article_id},
    ).result_rows
    article_credibility = {}
    if ac_rows:
        r = ac_rows[0]
        article_credibility = {
            "score": r[0], "rating": r[1], "summary": r[2],
            "total_claims_found": r[3], "claims_true": r[4], "claims_false": r[5],
            "claims_mixed": r[6], "claims_unverified": r[7],
            "government_source_only_flag": bool(r[8]),
            "writing_quality": {"sensationalism": r[9], "named_sources": bool(r[10])},
        }

    claim_rows = ch.query(
        "SELECT id, claim_summary, extract, verdict, reason, "
        "government_source_only, sources_json "
        "FROM claims WHERE article_id = {a:String} ORDER BY id",
        parameters={"a": article_id},
    ).result_rows
    claims = [
        {
            "id": r[0], "claim_summary": r[1], "extract": r[2],
            "verdict": r[3], "reason": r[4],
            "government_source_only": bool(r[5]),
            "sources": json.loads(r[6]),
        }
        for r in claim_rows
    ]

    return {
        "article": article,
        "publisher_credibility": publisher_credibility,
        "article_credibility": article_credibility,
        "claims": claims,
    }

# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete(url: str, ch=None):
    """Delete all stored data for a given article URL."""
    ch = ch or get_ch_client()

    rows = ch.query(
        "SELECT id, title FROM articles WHERE url = {u:String}",
        parameters={"u": url},
    ).result_rows

    if not rows:
        raise FileNotFoundError(f"No record found for URL: {url}")

    s3 = get_s3_client()
    for article_id, title in rows:
        aid = str(article_id)
        s3_html_key = f"articles/{re.sub(r'[^\w\-]', '_', title or url.split('//')[-1])[:100]}.html"

        # Delete offline HTML from S3
        print(f"Deleting S3 object '{s3_html_key}'...")
        s3.delete_object(Bucket=AWS_BUCKET, Key=s3_html_key)

        for table in ("claims", "article_credibility", "publisher_credibility"):
            ch.command(
                f"ALTER TABLE {table} DELETE WHERE article_id = {{a:String}}",
                parameters={"a": aid},
            )
        ch.command(
            "ALTER TABLE articles DELETE WHERE id = {a:String}",
            parameters={"a": aid},
        )

    print(f"Deleted all records for: {url}")

# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def list_articles(limit: int = 50, ch=None):
    """List all checked articles with their overall credibility score."""
    ch = ch or get_ch_client()

    rows = ch.query(f"""
        SELECT a.url, a.title, a.publisher, a.checked_at, ac.score, ac.rating
        FROM articles a
        LEFT JOIN article_credibility ac ON a.id = ac.article_id
        ORDER BY a.checked_at DESC
        LIMIT {limit}
    """).result_rows

    if not rows:
        print("No articles found.")
        return []

    print(f"\n{'URL':<50} {'Title':<30} {'Score':>6}  {'Rating':<20}  Checked")
    print("-" * 120)
    for url, title, _, checked_at, score, rating in rows:
        print(f"{url[:49]:<50} {(title or '')[:29]:<30} {(score or 0):>6}  {(rating or ''):<20}  {checked_at}")

    return rows

# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    setup()

    upload(
        article={
            "url": "https://www.straitstimes.com/singapore/none-of-the-children-with-covid-19-are-seriously-ill-but-situation-is-still-worrying-chan",
            "title": "Example Article",
            "publisher": "Example News",
            "date": "2026-03-06",
            "author": "Jane Doe",
            "section": "Politics",
            "is_opinion": False,
        },
        publisher_credibility={
            "score": 72,
            "rating": "mostly_credible",
            "summary": "Generally reliable outlet with minor bias.",
            "bias": "center_left",
            "known_issues": ["occasional sensationalism"],
            "fact_checker_ratings": ["Mostly True - PolitiFact"],
        },
        article_credibility={
            "score": 65,
            "rating": "mostly_credible",
            "summary": "Most claims supported; one statistic is unverified.",
            "total_claims_found": 4,
            "claims_true": 3,
            "claims_false": 0,
            "claims_mixed": 0,
            "claims_unverified": 1,
            "government_source_only_flag": False,
            "writing_quality": {"sensationalism": "low", "named_sources": True},
        },
        claims=[
            {
                "claim_summary": "GDP grew 3% last year",
                "extract": "The economy expanded by 3% in 2025.",
                "verdict": "true",
                "reason": "Confirmed by Singapore Department of Statistics.",
                "government_source_only": True,
                "sources": [
                    {"name": "Singstat", "url": "https://singstat.gov.sg",
                     "type": "government", "is_independent": False},
                ],
            },
        ],
    )

    print(retrieve("https://www.straitstimes.com/singapore/none-of-the-children-with-covid-19-are-seriously-ill-but-situation-is-still-worrying-chan"))
    list_articles()
