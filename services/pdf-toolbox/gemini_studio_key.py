"""Google AI Studio API key helper (local copy for pdf-toolbox)."""
import os


def google_ai_studio_api_key() -> str:
    return (os.environ.get("GOOGLE_AI_API_KEY") or "").strip()
