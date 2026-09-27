"""
Google Tasks: dedup index, task formatting, and the create / update / skip plan.
Everything except TasksClient is pure logic.

Task model (one task per order, split per extra package):
  - Order confirmation -> one task per order.
  - First shipment     -> that task gains tracking info.
  - Each extra package -> its own task, "(k of n)".

Tasks carry no machine-readable labels: which task belongs to which order/package is
kept in Supabase (order_tracker_tasks), so the description can be edited freely.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import config
from orders import Order, Shipment

API = "https://tasks.googleapis.com/tasks/v1"
ETA_UNAVAILABLE = "No ETA - check tracking"


# ── Index ────────────────────────────────────────────────────────────────────

def is_closed(task: dict) -> bool:
    """Completed or deleted by the user: never touched, never recreated."""
    return task.get("status") == "completed" or bool(task.get("deleted"))


@dataclass
class TaskIndex:
    by_tracking: dict = field(default_factory=dict)   # tracking -> task
    by_order: dict = field(default_factory=dict)      # (merchant, order#) -> [(task, link)]
    links_by_task: dict = field(default_factory=dict)  # task id -> link

    @classmethod
    def build(cls, tasks: list[dict], links: list[dict]) -> "TaskIndex":
        """links: rows of order_tracker_tasks {task_id, merchant, order_number, tracking}.

        A linked task no longer returned by the API (deleted and purged) counts as deleted,
        so it is never recreated.
        """
        by_id = {t["id"]: t for t in tasks}
        index = cls()
        for link in links:
            task = by_id.get(link["task_id"], {"id": link["task_id"], "deleted": True})
            index.links_by_task[link["task_id"]] = link
            if link.get("tracking"):
                index.by_tracking[link["tracking"]] = task
            index.by_order.setdefault((link["merchant"], link["order_number"]), []).append((task, link))
        return index


# ── Formatting ───────────────────────────────────────────────────────────────

def fmt_date(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def fmt_short(d: date) -> str:
    return f"{d:%b} {d.day}"


def _trusted_url(url: str | None) -> bool:
    """Only https links on an allow-listed retailer or carrier domain (the URL came from
    email text, so anything else could be a planted phishing link)."""
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    domains = {d for _, d, _ in config.ALLOWED_SENDERS} | config.CARRIER_DOMAINS
    # a retailer's sending subdomain (e.g. notify.macys.com) also trusts its parent domain
    domains |= {".".join(d.split(".")[-2:]) for d in domains}
    return parsed.scheme == "https" and any(host == d or host.endswith("." + d) for d in domains)


def tracking_link(sh: Shipment) -> str:
    carrier = (sh.carrier or "").lower()
    for key, pattern in config.CARRIER_TRACKING_URL_PATTERNS.items():
        if key in carrier:
            return pattern.format(tracking=sh.tracking)
    return sh.tracking_url if _trusted_url(sh.tracking_url) else ""


def _items(order: Order, sh: Shipment | None) -> list[tuple[str, int]]:
    if sh and sh.items:
        by_name = {v["name"]: v["qty"] for v in order.items.values()}
        return [(n, by_name.get(n, 1)) for n in sh.items]
    return [(v["name"], v["qty"]) for v in order.items.values()]


def render(order: Order, sh: Shipment | None, index: int, count: int,
           status_line: str | None) -> tuple[str, str]:
    """(title, notes).

    Title:  [2x Item, Item] - [Retailer] (k of n)
    Notes:  [status line, if any]
            confirmation: [Order #] / [Order date]
            shipped:      [Tracking #] / [Tracking link] / [Order #]
    """
    items = (", ".join(f"{q}x {n}" if q > 1 else n for n, q in _items(order, sh))
             or f"Order #{order.order_number}")
    title = f"{items} - {order.merchant}"
    if count > 1:
        title += f" ({index} of {count})"

    lines = [status_line] if status_line else []
    if sh:
        lines += [sh.tracking, tracking_link(sh), order.order_number]
    else:
        lines += [order.order_number, fmt_date(order.order_date)]
    return title, "\n".join(line for line in lines if line)


def _cancel_line(order: Order) -> str:
    amount = (order.refund_amount or "").strip().lstrip("$")
    return f"Cancelled - refunded ${amount}" if amount else "Cancelled"


def _existing_due(task: dict | None) -> date | None:
    if task and task.get("due"):
        return date.fromisoformat(task["due"][:10])
    return None


def shipment_due(sh: Shipment, lookup: dict | None, task: dict | None,
                 order_eta: date | None = None) -> date | None:
    if sh.status == "delivered":
        return sh.delivered_on
    if sh.status == "out_for_delivery":
        return sh.ofd_date
    if lookup and lookup.get("eta"):
        return lookup["eta"]                         # a lookup made today beats an older email date
    # the order's own estimate (e.g. confirmation "Arrives by Oct 2") when the shipping
    # email itself gives no date
    return sh.eta or order_eta or _existing_due(task)


# ── Planning ─────────────────────────────────────────────────────────────────

@dataclass
class Action:
    kind: str                 # create | update | unchanged | skip_closed | skip_no_task
    title: str
    notes: str = ""
    due: date | None = None
    task: dict | None = None
    eta_unavailable: bool = False
    link: dict | None = None  # {merchant, order_number, tracking} to store for this task
    relink: bool = False      # existing task's stored link needs its tracking set


def _decide(task: dict | None, title: str, notes: str, due: date | None, link: dict,
            no_task_needed: bool, eta_unavailable: bool = False, relink: bool = False) -> Action:
    if task and is_closed(task):
        return Action("skip_closed", title, task=task)
    if not task:
        if no_task_needed:
            return Action("skip_no_task", title)
        return Action("create", title, notes, due, eta_unavailable=eta_unavailable, link=link)
    changed = (task.get("title") != title or (task.get("notes") or "") != notes
               or _existing_due(task) != due)
    return Action("update" if changed or relink else "unchanged", title, notes, due, task,
                  eta_unavailable, link, relink)


def plan_order(order: Order, index: TaskIndex, lookups: dict) -> list[Action]:
    """lookups: {tracking: {"eta": date|None, "delivered_on": date|None}} from carrier_lookup."""
    order_tasks = index.by_order.get((order.merchant, order.order_number), [])
    # the confirmation-created task: this order's task with no tracking linked yet
    unassigned = [t for t, link in order_tasks if not link.get("tracking")]
    base_link = {"merchant": order.merchant, "order_number": order.order_number, "tracking": None}

    if not order.shipments:
        task = unassigned[0] if unassigned else None
        status = (_cancel_line(order) if order.cancelled
                  else f"Delivered {fmt_short(order.delivered_on)}" if order.delivered_on else None)
        title, notes = render(order, None, 1, 1, status)
        due = order.delivered_on or order.eta or _existing_due(task)
        return [_decide(task, title, notes, due, base_link,
                        no_task_needed=order.cancelled or bool(order.delivered_on))]

    actions = []
    shipments = list(order.shipments.values())
    for i, sh in enumerate(shipments, start=1):
        task = index.by_tracking.get(sh.tracking)
        relink = False
        if not task and unassigned:
            task = unassigned.pop(0)      # first package takes over the confirmation task
            relink = True
        lookup = lookups.get(sh.tracking)
        if lookup and lookup.get("delivered_on") and sh.status != "delivered":
            sh.status, sh.delivered_on = "delivered", lookup["delivered_on"]

        due = shipment_due(sh, lookup, task, order.eta)
        eta_unavailable = due is None and sh.status != "delivered" and not order.cancelled
        if order.cancelled:
            status = _cancel_line(order)
        elif sh.status == "delivered":
            status = f"Delivered {fmt_short(sh.delivered_on)}"
        else:
            status = ETA_UNAVAILABLE if eta_unavailable else None

        title, notes = render(order, sh, i, len(shipments), status)
        actions.append(_decide(task, title, notes, due, {**base_link, "tracking": sh.tracking},
                               no_task_needed=order.cancelled or sh.status == "delivered",
                               eta_unavailable=eta_unavailable, relink=relink))
    return actions


def lookup_priority(order: Order, sh: Shipment, index: TaskIndex, today: date) -> str | None:
    """"fresh" (no date anywhere yet), "stale" (open task quiet for a multiple of
    STALE_TASK_RECHECK_DAYS days, so each quiet task is re-checked once per period
    without extra state), or None."""
    if order.cancelled or sh.status in ("delivered", "out_for_delivery"):
        return None
    task = index.by_tracking.get(sh.tracking)
    if task and is_closed(task):
        return None
    if not sh.eta and not order.eta and (not task or not task.get("due")):
        return "fresh"
    if task:
        quiet_days = (today - date.fromisoformat(task["updated"][:10])).days
        if quiet_days >= config.STALE_TASK_RECHECK_DAYS and quiet_days % config.STALE_TASK_RECHECK_DAYS == 0:
            return "stale"
    return None


def stale_open_tasks(tasks: list[dict], now: datetime) -> list[dict]:
    cutoff = now - timedelta(days=config.STALE_TASK_RECHECK_DAYS)
    return [t for t in tasks if not is_closed(t)
            and datetime.fromisoformat(t["updated"].replace("Z", "+00:00")) < cutoff]


# ── Google Tasks API ─────────────────────────────────────────────────────────

def due_rfc3339(d: date | None) -> str | None:
    # Tasks API stores date only; time is ignored. Midnight UTC keeps the calendar date.
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if d else None


class TasksClient:
    def __init__(self, session):
        self.s = session

    def find_list(self, name: str) -> str | None:
        resp = self.s.get(f"{API}/users/@me/lists", params={"maxResults": 100})
        resp.raise_for_status()
        return next((l["id"] for l in resp.json().get("items", []) if l["title"] == name), None)

    def create_list(self, name: str) -> str:
        resp = self.s.post(f"{API}/users/@me/lists", json={"title": name})
        resp.raise_for_status()
        return resp.json()["id"]

    def list_tasks(self, list_id: str) -> list[dict]:
        tasks, page = [], None
        while True:
            params = {"showCompleted": "true", "showHidden": "true", "showDeleted": "true",
                      "maxResults": 100}
            if page:
                params["pageToken"] = page
            resp = self.s.get(f"{API}/lists/{list_id}/tasks", params=params)
            resp.raise_for_status()
            data = resp.json()
            tasks += data.get("items", [])
            page = data.get("nextPageToken")
            if not page:
                return tasks

    def apply(self, list_id: str, action: Action) -> str:
        """Create or update; returns the task id."""
        body = {"title": action.title, "notes": action.notes, "due": due_rfc3339(action.due)}
        if action.kind == "create":
            resp = self.s.post(f"{API}/lists/{list_id}/tasks", json=body)
        else:
            resp = self.s.patch(f"{API}/lists/{list_id}/tasks/{action.task['id']}", json=body)
        resp.raise_for_status()
        return resp.json()["id"]
