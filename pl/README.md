# P&L Accounting — Google Sheets Sync

Pulls sales and purchases from Supabase and writes them into a Google Sheet where you do all your batch assignment and P&L work.

---

## How it works

Running `python pl/sync_to_sheets.py` writes three tabs to your Sheet:

| Tab | What it contains | What you edit |
|-----|-----------------|---------------|
| **Sales** | All orders from eBay | The `batch` column — type a batch name to group a sale |
| **Purchases** | All items from the import queue | The `batch` column — type the same batch name to link a cost |
| **P&L by Batch** | Auto-calculated summary | Nothing — formulas do the math |

Your batch assignments are **preserved across syncs** — if you've already typed batch names, they survive the next run.

---

## One-time setup

### 1. Create a Google Sheet

Create a new blank Google Sheet. Copy the document ID from the URL:

```
https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit
```

Add `SHEETS_DOC_ID=YOUR_ID_HERE` to your `.env` file.

### 2. Create a Google service account

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or use an existing one)
3. Enable the **Google Sheets API** and **Google Drive API**
4. Go to **IAM & Admin → Service Accounts → Create Service Account**
5. Give it any name (e.g. `ebay-tools-sheets`)
6. On the service account page, go to **Keys → Add Key → Create new key → JSON**
7. Save the downloaded JSON file as:
   ```
   pl/credentials/service_account.json
   ```
   This folder is gitignored — it will never be committed.

### 3. Share the Sheet with the service account

Open the JSON file and copy the `client_email` value (looks like `name@project.iam.gserviceaccount.com`).

Share your Google Sheet with that email address, giving it **Editor** access.

### 4. Add env var

In your `.env` file:
```
SHEETS_DOC_ID=your_sheet_id_here
GOOGLE_CREDS_PATH=pl/credentials/service_account.json   # optional, this is the default
```

---

## Running the sync

```bash
python pl/sync_to_sheets.py
```

Takes ~5 seconds. Run it whenever you want fresh data from Supabase in your Sheet.

---

## Batch assignment workflow

1. Run the sync to get fresh data
2. Open your Google Sheet
3. In the **Sales** tab, type a batch name in the `batch` column for the sales you want to group (e.g. `May Hobby Box Break`)
4. In the **Purchases** tab, type the **same batch name** in the `batch` column for the costs that belong to that batch
5. Check the **P&L by Batch** tab — it auto-updates with revenue, costs, profit, and margin for each batch

To see what still needs to be assigned, filter the `batch` column = blank.

---

## Running tests

```bash
python -m pytest tests/test_sync_to_sheets.py -v
```

No credentials needed — all DB and Sheets calls are mocked.
