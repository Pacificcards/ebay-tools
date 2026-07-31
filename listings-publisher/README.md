# listings-publisher

Publish fixed-price eBay listings from a Google Sheet + a folder of local photos.

**Status:** Built, awaiting write token + Cloudinary setup (see below).

---

## How it works

1. You fill a Google Sheet tab called **Listings** with one row per card.
2. You point the script at a local folder containing all the photos for that batch, taken in order.
3. The script sorts photos by EXIF capture timestamp, groups them (default: 2 per listing), and shows you a preview matching photos to rows.
4. You confirm → it uploads photos to Cloudinary (gets public URLs), creates each eBay listing, and writes the listing ID back to the sheet.

Rows with a Listing ID already filled in are skipped, so re-running after a partial failure is safe.

---

## Google Sheet setup

Create a new tab called **Listings** in your Google Sheet with these columns in order:

| Col | Header | Required? | Notes |
|-----|--------|-----------|-------|
| A | Title | No | Auto-generated from card attributes if blank |
| B | SKU | No | Auto-generated if blank |
| C | Sport | **Yes** | e.g. Baseball, Basketball, Football |
| D | Player/Athlete | **Yes** | Full player name |
| E | Manufacturer | **Yes** | e.g. Topps, Panini, Upper Deck |
| F | Set | No | e.g. Chrome, Prizm, Select |
| G | Year | No | e.g. 2023 |
| H | Team | No | e.g. Los Angeles Dodgers |
| I | Card Number | No | e.g. 1, HTA-1 |
| J | Features | No | Comma-separated: `Rookie, Autographed` |
| K | Condition | **Yes** | One of: `Near Mint or Better`, `Excellent`, `Very Good`, `Poor` |
| L | Price | **Yes** | USD number |
| M | Quantity | No | Defaults to 1 |
| N | Description | No | Full listing description |
| O | Shipping Policy ID | **Yes** | eBay shipping policy ID (varies per listing) |
| P | Return Policy ID | No | Overrides `EBAY_RETURN_POLICY_ID` from `.env` |
| Q | Payment Policy ID | No | Overrides `EBAY_PAYMENT_POLICY_ID` from `.env` |
| R | Listing ID | — | Written back by script |
| S | Status | — | Written back: `published` or `error: ...` |

**Category:** All listings use eBay category `261328` (Sports Trading Card Singles) by default. This tool is for ungraded raw cards only.

**Auto-generated title example:** `2023 Topps Chrome Shohei Ohtani #1 Baseball Card`

---

## One-time setup

### 1. eBay write-scoped refresh token

You need a refresh token with `sell.inventory` scope — different from the existing read token.

Re-run the OAuth flow in the eBay developer console selecting `sell.inventory` scope, and store the result as `EBAY_REFRESH_TOKEN_WRITE` in your `.env`.

### 2. eBay business policy IDs

The Inventory API requires three policy IDs from your eBay seller account:

1. Go to **My eBay → Account → Business Policies** (ebay.com/sbp)
2. Copy the ID for your default Shipping policy → `EBAY_FULFILLMENT_POLICY_ID`
3. Copy the ID for your Payment policy → `EBAY_PAYMENT_POLICY_ID`
4. Copy the ID for your Returns policy → `EBAY_RETURN_POLICY_ID`

### 3. Install new dependencies

```bash
pip install Pillow
```

---

## Running the script

```bash
# From the repo root
python listings-publisher/publish.py
```

Set `IMAGE_FOLDER` in your `.env` file, or pass it inline:

```bash
IMAGE_FOLDER=/path/to/your/photos python listings-publisher/publish.py
```

### Photo requirements

- Supported formats: `.jpg`, `.jpeg`, `.png`
- For iPhone photos: either use **JPEG** format (Settings → Camera → Formats → Most Compatible) or convert when importing to Mac via Image Capture (File → Export → JPEG)
- Photos are matched to listings by EXIF capture timestamp — earliest photos go to row 1, next batch to row 2, etc.
- If a photo has no EXIF data, file modification time is used as fallback

### Changing photos per listing

Set `PHOTOS_PER_LISTING=3` in `.env` (or any number). The default is 2.

---

## `.env` variables needed

```
EBAY_CLIENT_ID=
EBAY_CLIENT_SECRET=
EBAY_REFRESH_TOKEN_WRITE=
LISTINGS_SHEET_ID=
GOOGLE_CREDS_PATH=pl/credentials/service_account.json
IMAGE_FOLDER=
PHOTOS_PER_LISTING=2
EBAY_FULFILLMENT_POLICY_ID=
EBAY_PAYMENT_POLICY_ID=
EBAY_RETURN_POLICY_ID=
```
