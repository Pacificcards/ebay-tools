# ebay-tools

eBay seller automation and analytics for Pacific Cards Co.

## Projects

| Project | Directory | Status |
|---|---|---|
| Analytics pipeline | `analytics/` | Active — daily cron, 8am UTC |
| Campaign scheduler | `scheduler/` | Active — pause/resume via cron-job.org |
| eBay purchase import | `analytics/fetch_ebay_purchases.py` | Active — weekly cron, Mondays |
| Underpriced card listener | `listener/` | Active — every 15 min via cron-job.org |
| P&L Google Sheets sync | `pl/` | Active — run manually with `python pl/sync_to_sheets.py` |
| Dashboard | `dashboard/` | Active — Streamlit |
| Listings publisher | `listings-publisher/` | Not started |

## Setup

```bash
cp .env.example .env
# Fill in credentials
pip install -r requirements.txt
```

## Analytics

Fetches daily traffic data (impressions, clicks, page views) per listing from the eBay Analytics API.

Daily ingest runs via GitHub Actions at 8am UTC. To run manually:

```bash
python -m analytics.run_ingest          # fetch yesterday's data
BACKFILL=1 python -m analytics.run_ingest   # backfill full 90-day history
python -m analytics.compute_metrics    # compute derived metrics
```

## Metabase

```bash
docker compose -f docker/docker-compose.yml up -d
```

Access at http://localhost:3000. On first launch, add Supabase as an external Postgres data source.

## Database

Apply the schema to Supabase:

```bash
psql $SUPABASE_DB_URL -f db/schema.sql
```
