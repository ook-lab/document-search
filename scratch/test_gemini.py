import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add repo root to path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

load_dotenv(repo_root / ".env")

import google.generativeai as genai

api_key = os.getenv("GOOGLE_AI_API_KEY")
print(f"GOOGLE_AI_API_KEY: {api_key[:10] if api_key else 'None'}...")

genai.configure(api_key=api_key)

print("\n--- Testing Model List ---")
try:
    models = genai.list_models()
    for m in models:
        if 'generateContent' in m.supported_generation_methods:
            print(f"Model: {m.name}")
except Exception as e:
    print(f"Error listing models: {e}")

print("\n--- Testing Content Generation (gemini-3.5-flash) ---")
try:
    model = genai.GenerativeModel("gemini-3.5-flash")
    response = model.generate_content("Hello! Are you working?")
    print(f"Response: {response.text}")
    print(f"Usage metadata: {response.usage_metadata}")
except Exception as e:
    print(f"Error with gemini-3.5-flash: {e}")

print("\n--- Testing Content Generation (gemini-3.1-flash-lite) ---")
try:
    model = genai.GenerativeModel("gemini-3.1-flash-lite")
    response = model.generate_content("Hello! Are you working?")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error with gemini-3.1-flash-lite: {e}")
