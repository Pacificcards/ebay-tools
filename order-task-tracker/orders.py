"""
Fold cached per-email extractions into one state per order, oldest email first.

Pure logic (no network) so it's fully unit-testable.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config
from extract import normalize_merchant, normalize_order_number, resolve_date, resolve_range, slug

PACIFIC = ZoneInfo(config.TIMEZONE)

_IN_TRANSIT = {"shipped", "in_transit", "scheduled_tomorrow", "delayed"}


@dataclass
class Shipment:
    tracking: str
    carrier: str | None = None
    tracking_url: str | None = None
    items: list[str] = field(default_factory=list)
    status: str = "shipped"
    eta: date | None = None
    ofd_date: date | None = None
    delivered_on: date | None = None


@dataclass
class Order:
    order_number: str
    merchant: str
    order_date: date
    items: dict = field(default_factory=dict)        # slug -> {"name", "qty"}
    eta: date | None = None                          # order-level delivery estimate (pre-tracking)
    cancelled: bool = False
    refund_amount: str | None = None
    cancel_date: date | None = None
    delivered_on: date | None = None                 # order-level "delivered" without tracking
    shipments: dict = field(default_factory=dict)    # tracking -> Shipment (first-seen order)


def email_date(row: dict) -> date:
    return datetime.fromisoformat(row["received"]).astimezone(PACIFIC).date()


def _merchant(row: dict) -> str | None:
    return normalize_merchant(row["sender_domain"], row["display_name"], row["raw"].get("merchant"))


def _apply_status(sh: Shipment, status: str, ref: date, delivered_on: str | None) -> None:
    if sh.status == "delivered":
        return                                       # terminal
    if status == "delivered":
        sh.status = "delivered"
        sh.delivered_on = resolve_date(delivered_on, ref) or ref
    elif status == "out_for_delivery":
        sh.status, sh.ofd_date = status, ref         # overrides a prior Delayed
    elif status in _IN_TRANSIT:
        sh.status = status
        if status == "scheduled_tomorrow":
            sh.eta = ref + timedelta(days=1)         # an explicit date in the same email wins, below


def _apply_shipments(order: Order, raw: dict, ref: date) -> None:
    for s in raw.get("shipments", []):
        tracking = s["tracking"].strip()
        if not tracking or tracking.lstrip("#") == order.order_number:
            continue                                 # an order number is not a tracking number
        sh = order.shipments.setdefault(tracking, Shipment(tracking))
        sh.carrier = s.get("carrier") or sh.carrier
        sh.tracking_url = s.get("tracking_url") or sh.tracking_url
        for name in s.get("items", []):
            if name not in sh.items:
                sh.items.append(name)
        _apply_status(sh, s.get("status", "other"), ref, s.get("delivered_on"))
        eta = resolve_range(s.get("eta_start"), s.get("eta_end"), ref)
        if eta:
            sh.eta = eta                             # latest email wins (delays move it later)


def build_orders(rows: list[dict]) -> dict[tuple, Order]:
    """rows: cache rows (metadata + "raw"). Returns {(merchant, order_number): Order}.

    Keyed by merchant too: small shops commonly share order numbers like #1001.
    """
    start = datetime.fromisoformat(config.TRACKING_START)
    rows = sorted((r for r in rows if datetime.fromisoformat(r["received"]) >= start),
                  key=lambda r: r["received"])
    orders: dict[tuple, Order] = {}
    tracking_owner: dict[str, tuple] = {}

    # Pass 1: which orders exist, and which tracking numbers belong to them.
    for row in rows:
        raw = row["raw"]
        number = normalize_order_number(raw.get("order_number"))
        merchant = _merchant(row)
        if not (raw.get("is_order_email") and raw.get("is_purchase")
                and raw.get("is_physical_goods", True) and number and merchant):
            continue
        if merchant in config.EXCLUDED_MERCHANTS:
            continue
        key = (merchant, number)
        if key not in orders:
            orders[key] = Order(number, merchant, email_date(row))
        for s in raw.get("shipments", []):
            tracking_owner.setdefault(s["tracking"].strip(), key)

    # Pass 2: apply every email chronologically. Carrier-only emails (no order number)
    # attach through a tracking number; unknown trackings are ignored, which also drops
    # carrier mail about packages the user is sending out.
    for row in rows:
        raw = row["raw"]
        if not raw.get("is_order_email"):
            continue
        ref = email_date(row)
        key = (_merchant(row), normalize_order_number(raw.get("order_number")))
        if key not in orders:
            owners = {tracking_owner.get(s["tracking"].strip()) for s in raw.get("shipments", [])}
            owners.discard(None)
            if len(owners) != 1:
                continue
            key = owners.pop()
        order = orders[key]
        order.order_date = min(order.order_date, ref)
        if not order.items:
            # items come from the first email that lists any (usually the confirmation);
            # later emails often reword names, which would otherwise list items twice
            for item in raw.get("items", []):
                k = slug(item["name"])
                if k:
                    order.items.setdefault(k, {"name": item["name"], "qty": item.get("qty") or 1})
        eta = resolve_range(raw.get("eta_start"), raw.get("eta_end"), ref)
        if eta:
            order.eta = eta
        if raw.get("status") == "cancelled" and raw.get("full_cancellation"):
            order.cancelled = True
            order.refund_amount = raw.get("refund_amount")
            order.cancel_date = ref
        if raw.get("status") == "delivered" and not raw.get("shipments"):
            order.delivered_on = order.delivered_on or ref
        _apply_shipments(order, raw, ref)
    return orders
