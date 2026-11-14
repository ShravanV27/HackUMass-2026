import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("NESSIE_API_KEY")
base_url = os.getenv("NESSIE_BASE_URL")

if not api_key or not base_url:
    raise ValueError("Missing Nessie API configuration")

response = requests.get(
    f"{base_url}/accounts",
    headers={"Authorization": f"Bearer {api_key}"},
    timeout=15
)

print("Status:", response.status_code)
print("Response:", response.text)