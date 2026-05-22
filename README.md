# ebay-tools

eBay seller automation and analytics for Pacific Cards Co.

## Projects

| Project | Directory | Status |
|---|---|---|
| Analytics | `analytics/` | Active |
| Campaign scheduler | `scheduler/` | Planned |
| P&L | `pl/` | Planned |
| Listings listener | `listener/` | Planned |
| Listings publisher | `listings-publisher/` | Planned |

## Setup

```bash
cp .env.example .env
# Fill in credentials
pip install -r requirements.txt
```

## Analytics

Daily ingest runs via GitHub Actions at 8am UTC. To run manually:

```bash
python analytics/run_ingest.py   # fetch from eBay APIs → Supabase
python analytics/compute_metrics.py  # transform raw → computed
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
