"""Google AI Studio API key helper."""
import os


def google_ai_studio_api_key() -> str:
    """Return GOOGLE_AI_API_KEY or empty string."""
    return (os.environ.get("GOOGLE_AI_API_KEY") or "").strip()
