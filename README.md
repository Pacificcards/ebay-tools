# ebay-tools

eBay seller automation and analytics for Pacific Cards Co.

## Projects

| Project | Directory | Status |
|---|---|---|
| Campaign scheduler | `scheduler/` | Active |
| Analytics | `analytics/` | In Progress |
| P&L | `pl/` | Not Started |
| Listings listener | `listener/` | Not Started |
| Listings publisher | `listings-publisher/` | Not Started |

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
