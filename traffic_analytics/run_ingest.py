"""Entry point: fetch traffic and order data from eBay APIs and write to Supabase."""
from dotenv import load_dotenv

load_dotenv()

from traffic_analytics.sync_listings import sync as sync_listings
from traffic_analytics.fetch_analytics import fetch_and_store as fetch_analytics
from traffic_analytics.fetch_orders import fetch_and_store as fetch_orders

if __name__ == "__main__":
    print("=== Starting eBay analytics ingest ===")
    sync_listings()
    fetch_analytics()
    fetch_orders()
    print("=== Ingest complete ===")
