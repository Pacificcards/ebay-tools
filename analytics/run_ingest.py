"""Entry point: fetch traffic data from eBay Analytics API and write to Supabase."""
from dotenv import load_dotenv

load_dotenv()

from analytics.fetch_analytics import fetch_and_store as fetch_analytics

if __name__ == "__main__":
    print("=== Starting eBay analytics ingest ===")
    fetch_analytics()
    print("=== Ingest complete ===")
