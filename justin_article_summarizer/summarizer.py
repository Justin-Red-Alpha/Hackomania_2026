import sys
import urllib.request
import urllib.error
import justin_article_summarizer.ai_client as ai_client


# ── Config ─────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a helpful assistant that summarizes web articles and pages. "
    "You must only use information from the page content provided — do not add outside knowledge. "
    "If the content appears to be a login wall, bot-block page, or is too incomplete to summarize, "
    "say so explicitly instead of guessing. "
    "Your summary should include:\n"
    "- The main topic or purpose of the page\n"
    "- Key points or findings (3-5 bullet points)\n"
    "- A brief concluding takeaway\n"
    "- One or two direct quotes from the content that support your summary\n"
    "- Author Name (if available)\n"
    "- Notable Statistics or Data (if available)\n"
    "- Resembles AI-generated content (Yes/No)\n"
    "- Timestamp of article (if available)\n"
    "Keep the summary informative but concise (under 300 words)."
)

# Known phrases that indicate a blocked or incomplete page
BLOCK_PHRASES = [
    "access denied", "enable javascript", "sign in to continue",
    "please verify you are a human", "cf-browser-verification",
    "403 forbidden", "subscribe to read", "create an account",
    "log in to continue", "this page isn't available",
]

MIN_CONTENT_LENGTH = 500  # characters — below this is likely a block page


# ── 1. Content Validation ─────────────────────────────────

def validate_content(content: str) -> tuple[bool, str]:
    """
    Checks fetched content for signs of block pages or login walls.
    Returns (is_valid, reason). If invalid, reason explains why.
    """
    if len(content) < MIN_CONTENT_LENGTH:
        return False, f"Page content is too short ({len(content)} chars) — likely a block or redirect page."

    content_lower = content.lower()
    for phrase in BLOCK_PHRASES:
        if phrase in content_lower:
            return False, f"Page content contains block phrase: \"{phrase}\""

    return True, ""


# ── 2. URL Fetching ────────────────────────────────────────

def fetch_url_content(url: str) -> str:
    """Fetches and returns the raw text content of a URL, mimicking a real browser."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read()
        encoding = response.headers.get_content_charset() or "utf-8"
        return raw.decode(encoding, errors="replace")


# ── 3. Fallback Alternatives ───────────────────────────────

def build_fallback_urls(url: str) -> list[tuple[str, str]]:
    """Returns a list of (label, fallback_url) alternatives to try if the original is blocked."""
    return [
        ("archive.ph",  f"https://archive.ph/{url}"),
        ("AMP version", f"https://amp.{url.removeprefix('https://').removeprefix('http://')}"),
        ("Google Cache", f"https://webcache.googleusercontent.com/search?q=cache:{url}"),
    ]


def try_fallbacks(url: str) -> tuple[str, str] | None:
    """Tries each fallback URL in order. Returns (label, content) for the first that works, or None."""
    for label, fallback_url in build_fallback_urls(url):
        try:
            print(f"   ↪ Trying {label}: {fallback_url}")
            content = fetch_url_content(fallback_url)
            print(f"   ✅ Success via {label}!")
            return label, content
        except Exception:
            print(f"   ❌ {label} also blocked or unavailable.")
    return None


# ── 4. Blocked URL Detection ───────────────────────────────

def is_blocked_error(error: Exception) -> bool:
    """Returns True if the error looks like the site is blocking the scraper."""
    if isinstance(error, urllib.error.HTTPError) and error.code in (403, 429):
        return True
    if isinstance(error, urllib.error.URLError):
        error_msg = str(error.reason).lower()
        if "remote end closed" in error_msg or "connection" in error_msg:
            return True
    return False


# ── 5. Error Handling ──────────────────────────────────────

def handle_fetch_error(error: Exception, url: str, client) -> bool:
    """
    Handles URL fetch errors. If the site is blocking, attempts fallbacks.
    Returns True if a fallback succeeded and the summary was printed, False otherwise.
    """
    if is_blocked_error(error):
        code = error.code if isinstance(error, urllib.error.HTTPError) else "dropped"
        print(f"⚠️  Blocked ({code}): Site is refusing the request. Trying alternatives...")

        result = try_fallbacks(url)
        if result:
            label, content = result
            summary = ai_client.summarize(client, url, content, SYSTEM_PROMPT)
            print_summary(summary, source=label)
            return True
        else:
            print("❌ All fallbacks failed. The site may require a login or subscription.")
            return False

    elif isinstance(error, urllib.error.HTTPError):
        print(f"❌ HTTP error fetching URL: {error.code} {error.reason}")
    elif isinstance(error, urllib.error.URLError):
        print(f"❌ Could not fetch URL: {error.reason}")
    else:
        ai_client.handle_error(error)

    return False


# ── 6. Display ─────────────────────────────────────────────

def print_banner():
    """Prints the app header banner."""
    print("=" * 60)
    print("         🔗 Article Link Summarizer")
    print(f"         Powered by {ai_client.PROVIDER_NAME}")
    print("=" * 60)


def print_options():
    """Prints the available user options."""
    print("\nOptions:")
    print("  • Enter a URL to summarize")
    print("  • Type 'quit' or 'exit' to stop\n")


def print_summary(summary: str, source: str = None):
    """Prints the formatted summary to the console."""
    print("\n" + "─" * 60)
    label = f"📋 SUMMARY" + (f"  (via {source})" if source else "")
    print(label)
    print("─" * 60)
    print(summary)
    print("─" * 60)


# ── 7. Main Loop ───────────────────────────────────────────

def run_summarizer(client):
    """Main loop: repeatedly prompts for URLs and prints summaries."""
    while True:
        print_options()

        user_input = input("Enter URL: ").strip()

        if not user_input:
            print("⚠️  No input provided. Please enter a valid URL.")
            continue

        if user_input.lower() in ("quit", "exit"):
            print("\n👋 Goodbye!\n")
            sys.exit(0)

        if not user_input.startswith(("http://", "https://")):
            print("⚠️  Please include the full URL with http:// or https://")
            continue

        try:
            print(f"\n🔍 Fetching and analyzing: {user_input}\n")
            content = fetch_url_content(user_input)

            is_valid, reason = validate_content(content)
            if not is_valid:
                print(f"⚠️  Content validation failed: {reason}")
                print("   Attempting fallbacks...")
                result = try_fallbacks(user_input)
                if result:
                    label, content = result
                    is_valid, reason = validate_content(content)
                    if not is_valid:
                        print(f"❌ Fallback content also invalid: {reason}")
                        continue
                    summary = ai_client.summarize(client, user_input, content, SYSTEM_PROMPT)
                    print_summary(summary, source=label)
                else:
                    print("❌ All fallbacks failed. The site may require a login or subscription.")
                continue

            summary = ai_client.summarize(client, user_input, content, SYSTEM_PROMPT)
            print_summary(summary)
        except Exception as e:
            handle_fetch_error(e, user_input, client)


def main():
    print_banner()
    client = ai_client.init_client()
    run_summarizer(client)


if __name__ == "__main__":
    main()