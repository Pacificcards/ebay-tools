#!/usr/bin/env python3
"""
Daily analytics report — emails traffic + sales data for configured listings.

Usage:
    python -m traffic_analytics.send_daily_report

Required env vars: SUPABASE_DB_URL, GMAIL_ADDRESS, GMAIL_APP_PASSWORD
Listings configured in: traffic_analytics/report_listings.json
"""

import json
import os
import smtplib
import sys
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import psycopg2
from dotenv import load_dotenv

load_dotenv()

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "report_listings.json")


def load_config() -> list[dict]:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def fetch_data(conn, listing_id: str, dates: tuple) -> dict:
    """Returns {date: {impressions, clicks, orders, qty}} for the three dates."""
    cur = conn.cursor()

    cur.execute("""
        SELECT date, impressions_total, views_total, orders
        FROM listing_metrics_raw
        WHERE listing_id = %s AND date IN %s
    """, (listing_id, dates))
    result = {row[0]: {"impressions": row[1], "clicks": row[2], "orders": row[3], "qty": 0}
              for row in cur.fetchall()}

    cur.execute("""
        SELECT order_date, SUM(quantity)
        FROM orders_raw
        WHERE listing_id = %s AND order_date IN %s
        GROUP BY order_date
    """, (listing_id, dates))
    for order_date, qty in cur.fetchall():
        if order_date in result:
            result[order_date]["qty"] = int(qty)
        else:
            result[order_date] = {"impressions": None, "clicks": None, "orders": None, "qty": int(qty)}

    return result


def pct_change(cur, prior):
    if cur is None or prior is None or prior == 0:
        return None
    return (cur - prior) / prior


def fmt_pct(val) -> tuple[str, str]:
    if val is None:
        return "—", "#9ca3af"
    color = "#16a34a" if val > 0 else "#dc2626" if val < 0 else "#6b7280"
    sign = "+" if val > 0 else ""
    return f"{sign}{val * 100:.1f}%", color


def metric_row(label: str, v0, v1, v7) -> str:
    v0_str = f"{v0:,}" if v0 is not None else "—"
    dod_str, dod_color = fmt_pct(pct_change(v0, v1))
    wow_str, wow_color = fmt_pct(pct_change(v0, v7))
    return _tr(label, v0_str, dod_str, dod_color, wow_str, wow_color)


def ctr_row(r0: dict, r1: dict, r7: dict) -> str:
    def ctr(r):
        if not r.get("impressions"):
            return None
        return r["clicks"] / r["impressions"]

    c0, c1, c7 = ctr(r0), ctr(r1), ctr(r7)
    v0_str = f"{c0:.2%}" if c0 is not None else "—"
    dod_str, dod_color = fmt_pct(pct_change(c0, c1))
    wow_str, wow_color = fmt_pct(pct_change(c0, c7))
    return _tr("CTR", v0_str, dod_str, dod_color, wow_str, wow_color)


def ord_imp_row(r0: dict, r1: dict, r7: dict) -> str:
    def rate(r):
        if not r.get("impressions"):
            return None
        return (r.get("orders") or 0) / r["impressions"]

    c0, c1, c7 = rate(r0), rate(r1), rate(r7)
    v0_str = f"{c0 * 1000:.2f}" if c0 is not None else "—"
    dod_str, dod_color = fmt_pct(pct_change(c0, c1))
    wow_str, wow_color = fmt_pct(pct_change(c0, c7))
    return _tr("Ord/1k", v0_str, dod_str, dod_color, wow_str, wow_color)


def _tr(label, v0_str, dod_str, dod_color, wow_str, wow_color) -> str:
    cell = "padding:8px 12px;border-bottom:1px solid #e5e7eb"
    return f"""
      <tr>
        <td style="{cell};color:#374151;font-weight:500">{label}</td>
        <td style="{cell};text-align:right;color:#111827;font-weight:600">{v0_str}</td>
        <td style="{cell};text-align:right;color:{dod_color};font-weight:500">{dod_str}</td>
        <td style="{cell};text-align:right;color:{wow_color};font-weight:500">{wow_str}</td>
      </tr>"""


def listing_section(name: str, data: dict, d0: date, d1: date, d7: date) -> str:
    r0 = data.get(d0, {})
    r1 = data.get(d1, {})
    r7 = data.get(d7, {})

    hcell = "padding:8px 12px;color:#6b7280;font-weight:600;border-bottom:2px solid #e5e7eb;font-size:13px"
    rows = (
        metric_row("Impressions", r0.get("impressions"), r1.get("impressions"), r7.get("impressions")) +
        metric_row("Views",        r0.get("clicks"),      r1.get("clicks"),      r7.get("clicks")) +
        ctr_row(r0, r1, r7) +
        metric_row("Orders",      r0.get("orders"),      r1.get("orders"),      r7.get("orders")) +
        metric_row("Qty Sold",    r0.get("qty"),         r1.get("qty"),         r7.get("qty")) +
        ord_imp_row(r0, r1, r7)
    )

    return f"""
  <div style="margin-bottom:36px">
    <h2 style="margin:0 0 10px;font-size:15px;font-weight:600;color:#111827">{name}</h2>
    <table style="border-collapse:collapse;width:100%;font-family:Arial,sans-serif;font-size:14px">
      <thead>
        <tr style="background:#f9fafb">
          <th style="{hcell};text-align:left">Metric</th>
          <th style="{hcell};text-align:right">{d0.strftime('%-d %b')}</th>
          <th style="{hcell};text-align:right">vs {d1.strftime('%-d %b')}</th>
          <th style="{hcell};text-align:right">vs {d7.strftime('%-d %b')}</th>
        </tr>
      </thead>
      <tbody>{rows}
      </tbody>
    </table>
  </div>"""


def build_html(listings_data: list[tuple[str, dict]], d0: date, d1: date, d7: date) -> str:
    sections = "".join(listing_section(name, data, d0, d1, d7) for name, data in listings_data)
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:24px;background:#ffffff;color:#111827">
  <h1 style="font-size:20px;font-weight:700;margin:0 0 4px;color:#111827">Pacific Cards Co.</h1>
  <p style="margin:0 0 24px;color:#6b7280;font-size:14px">Daily Report &mdash; {d0.strftime('%A, %-d %B %Y')}</p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin-bottom:28px">
  {sections}
</body>
</html>"""


def send_email(subject: str, html: str, address: str, password: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Pacific Cards Analytics <{address}>"
    msg["To"] = address
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(address, password)
        server.sendmail(address, address, msg.as_string())


def main():
    gmail_address  = os.environ.get("GMAIL_ADDRESS", "").strip()
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    db_url         = os.environ.get("SUPABASE_DB_URL", "").strip()

    if not all([gmail_address, gmail_password, db_url]):
        print("ERROR: GMAIL_ADDRESS, GMAIL_APP_PASSWORD, and SUPABASE_DB_URL must be set")
        sys.exit(1)

    config = load_config()
    if not config:
        print("ERROR: report_listings.json is empty")
        sys.exit(1)

    today = date.today()
    d0 = today - timedelta(days=1)
    d1 = today - timedelta(days=2)
    d7 = today - timedelta(days=8)

    conn = psycopg2.connect(db_url)
    try:
        listings_data = []
        for listing in config:
            data = fetch_data(conn, listing["id"], (d0, d1, d7))
            listings_data.append((listing["name"], data))
            print(f"  Fetched: {listing['name']}")
    finally:
        conn.close()

    html = build_html(listings_data, d0, d1, d7)
    subject = f"Pacific Cards Co. — Daily Report | {d0.strftime('%A, %-d %B %Y')}"
    send_email(subject, html, gmail_address, gmail_password)
    print(f"Sent: {subject}")


if __name__ == "__main__":
    main()
