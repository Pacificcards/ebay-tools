# scheduler

eBay ad campaign automation — pause/resume Promoted Listings campaigns on a schedule.

## How it works

`campaign_control.py` accepts a single argument (`pause` or `resume`) and applies it to all campaigns listed in `scheduler/campaigns.json` (id + name).

The GitHub Actions workflow (`campaign-scheduler.yml`) is triggered externally by cron-job.org via `workflow_dispatch`:

| Action | Days | Time (PT) | Time (UTC) |
|--------|------|-----------|------------|
| Pause  | Daily | 5:00am | cron-job.org timezone America/Los_Angeles (DST-aware) |
| Resume | Daily | 6:00pm | cron-job.org timezone America/Los_Angeles (DST-aware) |

## Required secrets (GitHub Actions)

| Secret | Description |
|--------|-------------|
| `EBAY_CLIENT_ID` | eBay app client ID |
| `EBAY_CLIENT_SECRET` | eBay app client secret |
| `EBAY_REFRESH_TOKEN` | OAuth refresh token |
| `GMAIL_ADDRESS` | Gmail address for email notifications |
| `GMAIL_APP_PASSWORD` | Gmail app password for email notifications |

## Manual run

```bash
python3 -m scheduler.campaign_control pause
python3 -m scheduler.campaign_control resume
```

Or trigger via GitHub Actions → **eBay Campaign Scheduler** → **Run workflow**.
