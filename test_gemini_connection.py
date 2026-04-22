import os

from dotenv import load_dotenv
from google import genai

load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("GOOGLE_API_KEY NOT found in .env or environment")
    exit(1)

try:
    client = genai.Client(api_key=api_key)
    # Just try a simple call if possible or print that client was created
    print("Gemini Client successfully initialized")
    # Actually try to list models to verify connection
    models = client.models.list()
    print("Successfully listed models")
except Exception as e:
    print(f"Error connecting to Gemini API: {e}")
