import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

print("1. Starting script...", flush=True)

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

if not api_key:
    raise ValueError("GEMINI_API_KEY not found in .env")

print("2. API key found!", flush=True)

client = genai.Client(
    api_key=api_key,
    http_options=types.HttpOptions(timeout=15000)
)

print("3. Sending request to Gemini...", flush=True)

try:
    response = client.models.generate_content(
        model=model,
        contents="Say hello."
    )

    print("4. Gemini responded!", flush=True)
    print(response.text)

except Exception as e:
    print("ERROR:", type(e).__name__, str(e))