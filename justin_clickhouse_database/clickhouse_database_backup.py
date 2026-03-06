"""
media_store.py
--------------
Store, retrieve, and delete binary files (images, videos, etc.)
using AWS S3 for binary storage and ClickHouse for metadata/search.

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
from pathlib import Path
from datetime import datetime

import boto3
from botocore.exceptions import ClientError
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

MIME_MAP = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".mp4":  "video/mp4",
    ".mov":  "video/quicktime",
    ".avi":  "video/x-msvideo",
    ".pdf":  "application/pdf",
}

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
# Setup
# ---------------------------------------------------------------------------

def setup(ch=None):
    """Create the ClickHouse metadata table if it doesn't exist."""
    ch = ch or get_ch_client()
    ch.command("""
        CREATE TABLE IF NOT EXISTS media_files (
            id          UUID    DEFAULT generateUUIDv4(),
            filename    String,
            mime_type   String,
            file_size   UInt64,
            s3_key      String,
            s3_url      String,
            uploaded_at DateTime DEFAULT now(),
            tags        Map(String, String)
        )
        ENGINE = MergeTree()
        ORDER BY uploaded_at
    """)
    print("✅ ClickHouse table 'media_files' is ready.")

# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload(filepath: str, tags: dict = {}, s3_prefix: str = "media") -> str:
    """
    Upload a binary file to S3 and record its metadata in ClickHouse.

    Args:
        filepath:   Local path to the file.
        tags:       Optional key-value metadata (e.g. {'category': 'nature'}).
        s3_prefix:  S3 key prefix / folder (default: 'media').

    Returns:
        The public S3 URL of the uploaded file.
    """
    path      = Path(filepath)
    mime_type = MIME_MAP.get(path.suffix.lower(), "application/octet-stream")
    file_size = path.stat().st_size
    s3_key    = f"{s3_prefix}/{path.name}"

    s3 = get_s3_client()
    ch = get_ch_client()

    # 1. Upload binary to S3
    print(f"⬆️  Uploading '{path.name}' to S3...")
    s3.upload_file(
        str(path),
        AWS_BUCKET,
        s3_key,
        ExtraArgs={"ContentType": mime_type},
    )
    s3_url = f"https://{AWS_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"

    # 2. Store metadata in ClickHouse
    ch.insert(
        "media_files",
        [[path.name, mime_type, file_size, s3_key, s3_url, tags]],
        column_names=["filename", "mime_type", "file_size", "s3_key", "s3_url", "tags"],
    )

    print(f"✅ Uploaded '{path.name}' ({file_size:,} bytes)")
    print(f"   S3 URL: {s3_url}")
    return s3_url

# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------

def retrieve(filename: str, output_dir: str = ".") -> str:
    """
    Download a file from S3 using its metadata from ClickHouse.

    Args:
        filename:   The original filename (as stored during upload).
        output_dir: Local directory to save the downloaded file.

    Returns:
        Local path where the file was saved.
    """
    ch = get_ch_client()
    s3 = get_s3_client()

    # 1. Look up the S3 key from ClickHouse
    result = ch.query(
        "SELECT s3_key, file_size, mime_type FROM media_files "
        "WHERE filename = {f:String} ORDER BY uploaded_at DESC LIMIT 1",
        parameters={"f": filename},
    )

    if not result.result_rows:
        raise FileNotFoundError(f"No record found for '{filename}' in ClickHouse.")

    s3_key, file_size, mime_type = result.result_rows[0]

    # 2. Download binary from S3
    output_path = str(Path(output_dir) / filename)
    print(f"⬇️  Downloading '{filename}' from S3...")

    try:
        s3.download_file(AWS_BUCKET, s3_key, output_path)
    except ClientError as e:
        raise RuntimeError(f"S3 download failed: {e}")

    print(f"✅ Retrieved '{filename}' ({file_size:,} bytes, {mime_type})")
    print(f"   Saved to: {output_path}")
    return output_path

# ---------------------------------------------------------------------------
# Retrieve as presigned URL (no local download)
# ---------------------------------------------------------------------------

def get_presigned_url(filename: str, expires_in: int = 3600) -> str:
    """
    Generate a temporary presigned URL to access the file directly from S3.

    Args:
        filename:   The original filename.
        expires_in: URL expiry in seconds (default: 1 hour).

    Returns:
        A presigned S3 URL string.
    """
    ch = get_ch_client()
    s3 = get_s3_client()

    result = ch.query(
        "SELECT s3_key FROM media_files "
        "WHERE filename = {f:String} ORDER BY uploaded_at DESC LIMIT 1",
        parameters={"f": filename},
    )

    if not result.result_rows:
        raise FileNotFoundError(f"No record found for '{filename}' in ClickHouse.")

    s3_key = result.result_rows[0][0]
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": AWS_BUCKET, "Key": s3_key},
        ExpiresIn=expires_in,
    )

    print(f"🔗 Presigned URL (expires in {expires_in}s):\n   {url}")
    return url

# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete(filename: str):
    """
    Delete a file from both S3 and ClickHouse metadata.

    Args:
        filename: The original filename to delete.
    """
    ch = get_ch_client()
    s3 = get_s3_client()

    # 1. Look up S3 key in ClickHouse
    result = ch.query(
        "SELECT s3_key FROM media_files "
        "WHERE filename = {f:String}",
        parameters={"f": filename},
    )

    if not result.result_rows:
        raise FileNotFoundError(f"No record found for '{filename}' in ClickHouse.")

    s3_key = result.result_rows[0][0]

    # 2. Delete from S3
    print(f"🗑️  Deleting '{filename}' from S3...")
    try:
        s3.delete_object(Bucket=AWS_BUCKET, Key=s3_key)
    except ClientError as e:
        raise RuntimeError(f"S3 delete failed: {e}")

    # 3. Delete metadata from ClickHouse
    ch.command(
        "ALTER TABLE media_files DELETE WHERE filename = {f:String}",
        parameters={"f": filename},
    )

    print(f"✅ Deleted '{filename}' from S3 and ClickHouse.")

# ---------------------------------------------------------------------------
# Search / List
# ---------------------------------------------------------------------------

def list_files(limit: int = 50):
    """List all stored files with their metadata."""
    ch = get_ch_client()
    rows = ch.query(f"""
        SELECT filename, mime_type, file_size, uploaded_at, tags
        FROM media_files
        ORDER BY uploaded_at DESC
        LIMIT {limit}
    """).result_rows

    if not rows:
        print("No files found.")
        return []

    print(f"\n{'Filename':<30} {'Type':<20} {'Size':>10}  {'Uploaded':<20}  Tags")
    print("-" * 95)
    for filename, mime, size, uploaded_at, tags in rows:
        tag_str = ", ".join(f"{k}={v}" for k, v in tags.items())
        print(f"{filename:<30} {mime:<20} {size:>10,}  {str(uploaded_at):<20}  {tag_str}")

    return rows

def search_by_tag(key: str, value: str):
    """Search files by a metadata tag."""
    ch = get_ch_client()
    rows = ch.query(
        f"SELECT filename, s3_url, uploaded_at FROM media_files "
        f"WHERE tags['{key}'] = '{value}' ORDER BY uploaded_at DESC"
    ).result_rows

    print(f"\nResults for tag {key}={value}:")
    for row in rows:
        print(f"  {row[0]}  →  {row[1]}")
    return rows

# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    setup()
    upload("sunset.png", tags={"category": "nature", "location": "Singapore"})

    list_files()
    search_by_tag("category", "nature")

    get_presigned_url("sunset.png")
    retrieve("sunset.png", output_dir="./temp_dl")   # ← "." saves it in the project root
    # delete("sunset.png")