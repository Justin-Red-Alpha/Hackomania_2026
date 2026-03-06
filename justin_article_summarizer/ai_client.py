"""
ai_client.py — Gemini AI provider
──────────────────────────────────
Implements the standard AI provider interface used by link_summarizer.py.

Standard interface any provider file must implement:
    PROVIDER_NAME : str                          - display name shown in banner
    init_client(filepath, model) -> any          - loads key, validates, returns client handle
    summarize(client, url, content) -> str       - sends content to AI, returns summary string
    handle_error(error) -> None                  - prints a friendly error message

To swap providers, replace this file with another that implements the same interface.
"""

import sys
from google import genai
from google.genai import types


# ── Config ─────────────────────────────────────────────────

PROVIDER_NAME = "Gemini AI"
DEFAULT_MODEL  = "models/gemini-2.5-flash"
DEFAULT_KEYFILE = "apikey.txt"


# ── Standard Interface ─────────────────────────────────────

def init_client(
    filepath: str = DEFAULT_KEYFILE,
    model: str = DEFAULT_MODEL,
) -> tuple[genai.Client, str]:
    """Reads the API key from a file, validates it, and returns a ready-to-use client handle."""
    try:
        with open(filepath, "r") as f:
            api_key = f.read().strip()
    except FileNotFoundError:
        print(f"❌ Could not find '{filepath}'. Please create it in the same folder as this script.")
        sys.exit(1)

    print("🔑 Validating API key...", end=" ", flush=True)
    try:
        client = genai.Client(api_key=api_key)
        client.models.generate_content(
            model=model,
            contents="hi",
            config=types.GenerateContentConfig(max_output_tokens=1),
        )
        print("✅ Valid!")
    except Exception as e:
        error_msg = str(e).lower()
        if "api key" in error_msg or "invalid" in error_msg or "permission" in error_msg:
            print("❌\n❌ API key is invalid. Please check the key in your apikey.txt file.")
        else:
            print(f"❌\n❌ Could not validate API key: {e}")
        sys.exit(1)

    return client, model


def summarize(client: tuple[genai.Client, str], url: str, content: str, SYSTEM_PROMPT: str) -> str:
    """Sends the URL and its content to Gemini and returns the summary as a string."""
    gemini_client, model = client
    prompt = (
        f"Please summarize the following web page.\n"
        f"URL: {url}\n\n"
        f"Page content (truncated to first 10,000 characters):\n{content[:10000]}"
    )
    response = gemini_client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    return response.text.strip()


def handle_error(error: Exception):
    """Handles known Gemini API errors with friendly messages."""
    error_msg = str(error).lower()
    if "api key" in error_msg or "permission" in error_msg:
        print("❌ Authentication error: Your API key may be invalid.")
        sys.exit(1)
    elif "quota" in error_msg or "rate" in error_msg:
        print("❌ Rate limit reached. Please wait a moment and try again.")
    else:
        print(f"❌ Unexpected AI error: {error}")