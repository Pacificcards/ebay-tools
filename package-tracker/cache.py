"""Supabase cache of per-email extractions (table: package_tracker_emails, see schema.sql)."""

import os

import psycopg2
from psycopg2.extras import Json

import config


# Tables renamed 2026-10-02 (Order Task Tracker -> Package Tracker). Renamed in place on the
# first connection, data untouched; a no-op once done.
_RENAMED_TABLES = [("order_tracker_emails", "package_tracker_emails"),
                   ("order_tracker_tasks", "package_tracker_tasks"),
                   ("order_tracker_sheet_orders", "package_tracker_sheet_orders")]


def connect():
    conn = psycopg2.connect(os.environ["SUPABASE_DB_URL"])
    migrate_names(conn)
    return conn


def migrate_names(conn) -> None:
    """Rename the old order_tracker_* tables and their indexes (including the primary-key and
    unique-constraint indexes) in one transaction: all or nothing."""
    with conn.cursor() as cur:
        for old, new in _RENAMED_TABLES:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL, to_regclass(%s) IS NOT NULL", (old, new))
            old_exists, new_exists = cur.fetchone()
            if old_exists and new_exists:
                raise RuntimeError(f"both {old} and {new} exist - resolve by hand before running")
            if old_exists:
                cur.execute(f"ALTER TABLE {old} RENAME TO {new}")
            cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s AND indexname LIKE %s",
                        (new, "order\\_tracker\\_%"))
            for (index,) in cur.fetchall():
                renamed = "package_tracker_" + index[len("order_tracker_"):]
                cur.execute(f'ALTER INDEX "{index}" RENAME TO "{renamed}"')
    conn.commit()


def cached_ids(conn, message_ids: list[str], screen_signature: str) -> set[str]:
    """Ids that need no work: read by Claude under the current EXTRACT_VERSION, or screened
    out under the current header-screen settings (older skips are screened again)."""
    if not message_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT message_id FROM package_tracker_emails
               WHERE message_id = ANY(%s::text[]) AND extract_version >= %s
                 AND (extraction->'raw'->>'skipped' IS NULL
                      OR extraction->'raw'->>'screen' = %s)""",
            (message_ids, config.EXTRACT_VERSION, screen_signature),
        )
        return {r[0] for r in cur.fetchall()}


def save(conn, rows: list[dict]) -> None:
    """rows: {"message_id", "received" (ISO), sender/subject metadata, "raw": Claude output}."""
    with conn.cursor() as cur:
        for r in rows:
            raw = r["raw"]
            cur.execute(
                """INSERT INTO package_tracker_emails
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
            """SELECT extraction FROM package_tracker_emails
               WHERE message_id = ANY(%s::text[]) OR order_number = ANY(%s::text[])
                  OR trackings && %s::text[]
               ORDER BY received_at""",
            (list(message_ids), list(order_numbers), list(trackings)),
        )
        return [r[0] for r in cur.fetchall()]


def load_links(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT task_id, merchant, order_number, tracking FROM package_tracker_tasks")
        return [dict(zip(("task_id", "merchant", "order_number", "tracking"), r)) for r in cur.fetchall()]


def save_link(conn, task_id: str, link: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO package_tracker_tasks (task_id, merchant, order_number, tracking)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (task_id) DO UPDATE SET tracking = EXCLUDED.tracking""",
            (task_id, link["merchant"], link["order_number"], link["tracking"]),
        )
    conn.commit()
