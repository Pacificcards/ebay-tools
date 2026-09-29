#!/usr/bin/env python3
"""
Order Task Tracker: Gmail order/shipping emails -> Google Tasks ("Orders" list).

Usage:
    .venv/bin/python order-task-tracker/main.py            # live run
    .venv/bin/python order-task-tracker/main.py --dry-run  # print planned tasks, write nothing to Tasks
"""

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# Self-contained subproject: imports only from this folder (no shared.* dependency).
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import cache
import carrier_lookup
import config
import gmail_search
import google_auth
from claude_cli import ClaudeError
from extract import extract_batch, normalize_order_number
from orders import PACIFIC, build_orders
from tasks_sync import TaskIndex, TasksClient, needs_lookup, plan_order, stale_open_tasks


def _unique(ids):
    return list(dict.fromkeys(ids))


def _meta(email: dict) -> dict:
    return {"message_id": email["id"], "received": email["received"].isoformat(),
            "sender_domain": email["sender_domain"], "display_name": email["display_name"],
            "subject": email["subject"]}


def _load_related(conn, message_ids, order_numbers, trackings):
    """Cached rows for the window plus every other email of the same orders/shipments."""
    rows = cache.load(conn, message_ids, order_numbers, trackings)
    for _ in range(2):   # second hop: a carrier email's tracking -> its order -> that order's emails
        order_numbers = set(order_numbers) | {r["order_number"] for r in rows if r.get("order_number")}
        trackings = set(trackings) | {s["tracking"] for r in rows for s in r["raw"].get("shipments", [])}
        rows = cache.load(conn, message_ids, order_numbers, trackings)
    return rows


def run(dry_run: bool) -> int:
    conn = cache.connect()
    try:
        return _run(conn, dry_run)
    finally:
        conn.close()


def _run(conn, dry_run: bool) -> int:
    now = datetime.now(PACIFIC)
    session = google_auth.get_session()
    tasks_api = TasksClient(session)

    # Step 1: existing tasks (completed + deleted included) for dedup
    list_id = tasks_api.find_list(config.ORDERS_TASKLIST_NAME)
    if not list_id and not dry_run:
        list_id = tasks_api.create_list(config.ORDERS_TASKLIST_NAME)
    tasks = tasks_api.list_tasks(list_id) if list_id else []
    index = TaskIndex.build(tasks, cache.load_links(conn))

    # Step 2: stale open tasks get an order-number search
    stale = [index.links_by_task[t["id"]] for t in stale_open_tasks(tasks, now)
             if t["id"] in index.links_by_task]
    stale_orders = {link["order_number"] for link in stale}
    stale_trackings = {link["tracking"] for link in stale if link.get("tracking")}

    # Step 3: Gmail search
    ids = gmail_search.search_ids(session, gmail_search.recent_order_query())
    for number in sorted(stale_orders):
        ids += gmail_search.search_ids(session, gmail_search.order_ref_query(number))
    ids = _unique(ids)

    screen = gmail_search.screen_signature()
    done = cache.cached_ids(conn, ids, screen)
    skipped = Counter()
    to_extract, skip_rows = [], []
    for mid in ids:
        if mid in done:
            continue
        email = gmail_search.fetch_headers(session, mid)      # sender + subject only
        if gmail_search.is_excluded(email):
            reason = "excluded retailer"
        elif gmail_search.is_seller_mail(email):
            reason = "seller mail"
        elif not gmail_search.is_candidate(email):
            reason = "not an order"
        else:
            email["body"] = gmail_search.fetch_body(session, mid)  # only now read the email itself
            to_extract.append(email)
            continue
        skipped[reason] += 1
        # remembered so later runs don't re-open it (Gmail has a per-minute read quota)
        skip_rows.append({**_meta(email), "order_number": None,
                          "raw": {"is_order_email": False, "skipped": reason, "screen": screen,
                                  "shipments": []}})
    cache.save(conn, skip_rows)

    # Step 4: Claude extraction. Batches run EXTRACT_PARALLEL_CALLS at a time; each result
    # is cached as it comes back. A failing batch is retried one email at a time so one bad
    # email can't block the rest; emails that still fail (or that Claude leaves out) aren't
    # cached, so they're retried next run, and are listed in the summary.
    extracted, unreadable = 0, []
    batches = [to_extract[i:i + config.EXTRACT_BATCH_SIZE]
               for i in range(0, len(to_extract), config.EXTRACT_BATCH_SIZE)]

    def _extract(batch):
        try:
            return batch, extract_batch(batch), None
        except ClaudeError as exc:
            return batch, None, exc

    with ThreadPoolExecutor(max_workers=config.EXTRACT_PARALLEL_CALLS) as pool:
        while batches:
            retry = []
            for batch, results, exc in pool.map(_extract, batches):
                if exc:
                    if len(batch) > 1:
                        retry += [[e] for e in batch]
                    else:
                        print(f"WARNING: Claude could not read an email: {exc}")
                        unreadable.append(batch[0]["subject"])
                    continue
                rows = [{
                    **_meta(e),
                    "order_number": normalize_order_number(results[e["id"]].get("order_number")),
                    "raw": results[e["id"]],
                } for e in batch if e["id"] in results]
                unreadable += [e["subject"] for e in batch if e["id"] not in results]
                cache.save(conn, rows)
                extracted += len(rows)
            batches = retry

    rows = _load_related(conn, ids, stale_orders, stale_trackings)

    orders = build_orders(rows)

    # Step 5: carrier lookups for every open shipment (unsupported carriers are skipped)
    wanted = _unique([(sh.tracking, sh.carrier, sh.tracking_url) for o in orders.values()
                      for sh in o.shipments.values() if needs_lookup(o, sh, index)])
    lookups, lookup_warnings = carrier_lookup.lookup(wanted)
    for warning in lookup_warnings:
        print(f"WARNING: {warning}")
    supported = [t for t, c, url in wanted if carrier_lookup.carrier_of(t, c, url)]

    # Steps 6-8: plan + write
    actions = [a for o in orders.values() for a in plan_order(o, index, lookups)]
    for a in actions:
        if a.kind in ("create", "update") and not dry_run:
            task_id = tasks_api.apply(list_id, a)
            if a.kind == "create" or a.relink:
                cache.save_link(conn, task_id, a.link)

    # Step 9: summary
    counts = Counter(a.kind for a in actions)
    mode = "DRY RUN - nothing written" if dry_run else "live"
    print(f"Order Task Tracker ({mode}) - {now:%Y-%m-%d %H:%M} PT\n")
    print(f"Emails found: {len(ids)}  |  newly read by Claude: {extracted}  |  "
          f"skipped: {dict(skipped) or 0}")
    print(f"Carrier lookups: {len(lookups)} answered of {len(supported)} FedEx shipments checked "
          f"({len(wanted) - len(supported)} open shipments on carriers without a lookup)\n")
    if unreadable:
        print("Emails Claude could not read (will retry next run):")
        for subject in unreadable:
            print(f"  - {subject}")
        print()
    labels = {"create": "Created", "update": "Updated", "unchanged": "Unchanged",
              "skip_closed": "Skipped (completed/deleted)", "skip_no_task": "Skipped (delivered/cancelled, no task)"}
    for kind, label in labels.items():
        if counts[kind]:
            print(f"{label}: {counts[kind]}")
            for a in actions:
                if a.kind == kind:
                    flag = "  [ETA unavailable]" if a.eta_unavailable else ""
                    print(f"  - {a.title}{flag}")
    if dry_run:
        print("\n── Planned task contents ──")
        for a in actions:
            if a.kind in ("create", "update"):
                print(f"\n[{a.kind.upper()}] {a.title}\nDue: {a.due or '(none)'}\n{a.notes}")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--since", help="TESTING ONLY: override TRACKING_START, e.g. 2026-09-25")
    args = parser.parse_args()
    if args.since:
        config.TRACKING_START = f"{args.since}T00:00:00-07:00"
        print(f"TEST MODE: tracking emails since {args.since}\n")
    sys.exit(run(args.dry_run))


if __name__ == "__main__":
    main()
