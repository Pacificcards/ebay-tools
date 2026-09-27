"""Supabase cache of per-email extractions (table: order_tracker_emails, see schema.sql)."""

import os

import psycopg2
from psycopg2.extras import Json

import config


def connect():
    return psycopg2.connect(os.environ["SUPABASE_DB_URL"])


def cached_ids(conn, message_ids: list[str]) -> set[str]:
    """Ids already extracted with the current EXTRACT_VERSION."""
    if not message_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT message_id FROM order_tracker_emails
               WHERE message_id = ANY(%s::text[]) AND extract_version >= %s""",
            (message_ids, config.EXTRACT_VERSION),
        )
        return {r[0] for r in cur.fetchall()}


def save(conn, rows: list[dict]) -> None:
    """rows: {"message_id", "received" (ISO), sender/subject metadata, "raw": Claude output}."""
    with conn.cursor() as cur:
        for r in rows:
            raw = r["raw"]
            cur.execute(
                """INSERT INTO order_tracker_emails
                       (message_id, received_at, order_number, trackings, extraction, extract_version)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (message_id) DO UPDATE SET
                       order_number = EXCLUDED.order_number, trackings = EXCLUDED.trackings,
                       extraction = EXCLUDED.extraction, extract_version = EXCLUDED.extract_version,
                       processed_at = NOW()""",
                (r["message_id"], r["received"], r.get("order_number"),
                 [s["tracking"] for s in raw.get("shipments", [])], Json(r),
                 config.EXTRACT_VERSION),
            )
    conn.commit()


def load(conn, message_ids=(), order_numbers=(), trackings=()) -> list[dict]:
    """Rows matching any of the given message ids, order numbers or tracking numbers."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT extraction FROM order_tracker_emails
               WHERE message_id = ANY(%s::text[]) OR order_number = ANY(%s::text[])
                  OR trackings && %s::text[]
               ORDER BY received_at""",
            (list(message_ids), list(order_numbers), list(trackings)),
        )
        return [r[0] for r in cur.fetchall()]


def load_links(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT task_id, merchant, order_number, tracking FROM order_tracker_tasks")
        return [dict(zip(("task_id", "merchant", "order_number", "tracking"), r)) for r in cur.fetchall()]


def save_link(conn, task_id: str, link: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO order_tracker_tasks (task_id, merchant, order_number, tracking)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (task_id) DO UPDATE SET tracking = EXCLUDED.tracking""",
            (task_id, link["merchant"], link["order_number"], link["tracking"]),
        )
    conn.commit()
