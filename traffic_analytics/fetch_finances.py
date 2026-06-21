"""Fetch transaction fees from eBay Finances API and upsert into order_fees.

Uses getTransactions endpoint filtered to SALE transactions, which includes
the per-transaction fee breakdown (marketplace fees, shipping labels, etc.).

Fetches the last 2 days by default to catch late-arriving records.
Set BACKFILL=1 and BACKFILL_START=YYYY-MM-DD to pull historical data.
"""
import os
from datetime import date, datetime, timedelta, timezone

import requests

from shared.db import get_connection
from shared.ebay_auth import get_access_token

FINANCES_URL = "https://apiz.ebay.com/sell/finances/v1/transaction"
PAGE_SIZE = 200


def fetch_and_store() -> None:
    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"],
        os.environ["EBAY_CLIENT_SECRET"],
        os.environ["EBAY_REFRESH_TOKEN"],
    )

    now = datetime.now(timezone.utc)

    if os.environ.get("BACKFILL"):
        start = date.fromisoformat(os.environ.get("BACKFILL_START", "2026-01-01"))
        start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    else:
        start_dt = now - timedelta(days=2)

    filter_str = (
        f"transactionDate:[{start_dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')}"
        f"..{now.strftime('%Y-%m-%dT%H:%M:%S.000Z')}]"
        ",transactionType:{SALE|NON_SALE_CHARGE|SHIPPING_LABEL|REFUND|CREDIT|ADJUSTMENT}"
    )

    rows = _paginate(token, filter_str)
    _upsert(rows)
    print(f"[fetch_finances] upserted {len(rows)} fee rows")


def _paginate(token: str, filter_str: str) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    rows = []
    offset = 0

    while True:
        params = {
            "filter": filter_str,
            "limit": PAGE_SIZE,
            "offset": offset,
        }
        response = requests.get(FINANCES_URL, headers=headers, params=params)
        if response.status_code == 204:
            break
        if not response.ok:
            print(f"[fetch_finances] HTTP {response.status_code}: {response.text}")
            response.raise_for_status()

        data = response.json()
        batch = data.get("transactions", [])
        if not batch:
            break

        for txn in batch:
            rows.extend(_parse_fees(txn))
        offset += len(batch)

        if len(batch) < PAGE_SIZE:
            break

    return rows


def _parse_fees(txn: dict) -> list[dict]:
    """Extract one row per fee entry from a transaction."""
    order_id = txn.get("orderId") or None
    listing_id = (txn.get("references") or [{}])[0].get("referenceId") or None
    transaction_date = _parse_date(txn.get("transactionDate", ""))
    booking_entry = txn.get("bookingEntry")

    fees = txn.get("fees") or []
    if not fees:
        # store the transaction itself if no fee breakdown
        amount_obj = txn.get("amount", {})
        txn_id = txn.get("transactionId")
        if not txn_id:
            return []
        return [{
            "billing_transaction_id": txn_id,
            "order_id":               order_id,
            "listing_id":             listing_id,
            "transaction_date":       transaction_date,
            "fee_type":               txn.get("transactionType"),
            "fee_type_description":   None,
            "amount":                 _decimal(amount_obj.get("value")),
            "booking_entry":          booking_entry,
            "currency":               amount_obj.get("currency", "USD"),
        }]

    rows = []
    txn_id = txn.get("transactionId", "")
    for i, fee in enumerate(fees):
        amount_obj = fee.get("amount", {})
        rows.append({
            "billing_transaction_id": f"{txn_id}_{i}",
            "order_id":               order_id,
            "listing_id":             listing_id,
            "transaction_date":       transaction_date,
            "fee_type":               fee.get("feeType"),
            "fee_type_description":   None,
            "amount":                 _decimal(amount_obj.get("value")),
            "booking_entry":          fee.get("bookingEntry") or booking_entry,
            "currency":               amount_obj.get("currency", "USD"),
        })
    return rows


def _upsert(rows: list[dict]) -> None:
    if not rows:
        return
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            for row in rows:
                if not row.get("billing_transaction_id"):
                    continue
                cur.execute(
                    """
                    INSERT INTO order_fees (
                        billing_transaction_id, order_id, listing_id,
                        transaction_date, fee_type, fee_type_description,
                        amount, booking_entry, currency
                    ) VALUES (
                        %(billing_transaction_id)s, %(order_id)s, %(listing_id)s,
                        %(transaction_date)s, %(fee_type)s, %(fee_type_description)s,
                        %(amount)s, %(booking_entry)s, %(currency)s
                    )
                    ON CONFLICT (billing_transaction_id) DO UPDATE SET
                        amount               = EXCLUDED.amount,
                        fee_type_description = EXCLUDED.fee_type_description,
                        fetched_at           = NOW()
                    """,
                    row,
                )
    finally:
        conn.close()


def _parse_date(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return None


def _decimal(val: str | None) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    fetch_and_store()
