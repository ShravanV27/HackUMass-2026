import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

database_url = os.getenv("DATABASE_URL")

if not database_url:
    raise ValueError("DATABASE_URL missing from .env")

with psycopg.connect(database_url) as conn:
    with conn.cursor() as cursor:
        cursor.execute("SELECT CURRENT_DATE;")
        result = cursor.fetchone()

        print("Connected to TigerData!")
        print("Database date:", result[0])