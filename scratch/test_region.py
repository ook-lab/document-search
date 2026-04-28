import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

def list_models():
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = "asia-northeast1"
    
    print(f"Listing models in region {location} for project {project}...")
    
    try:
        client = genai.Client(
            vertexai=True,
            project=project,
            location=location
        )
        for model in client.models.list():
            print(f"Model ID: {model.name}")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    list_models()
