"""Entry point: fetch from all eBay APIs and write raw data to Supabase."""
from dotenv import load_dotenv

load_dotenv()

from analytics.fetch_inventory import fetch_and_store as fetch_inventory
from analytics.fetch_orders import fetch_and_store as fetch_orders
from analytics.fetch_analytics import fetch_and_store as fetch_analytics

if __name__ == "__main__":
    print("=== Starting eBay analytics ingest ===")
    fetch_inventory()
    fetch_orders()
    fetch_analytics()
    print("=== Ingest complete ===")
