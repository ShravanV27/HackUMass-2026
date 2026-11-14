import os
import requests
from dotenv import load_dotenv

load_dotenv()

base_url = os.environ["NESSIE_BASE_URL"].rstrip("/")
api_key = os.environ["NESSIE_API_KEY"]

response = requests.post(
    f"{base_url}/data/seed",
    headers={
        "Authorization": f"Bearer {api_key}"
    },
    timeout=30
)

print("Status:", response.status_code)
print("Response:", response.text[:2000])