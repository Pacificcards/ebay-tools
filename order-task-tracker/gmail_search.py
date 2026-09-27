"""Gmail query, message fetch/parse, and the false-positive + excluded-retailer filter."""

import base64
import hashlib
import html
import json
import re
import time
from datetime import datetime
from email.utils import parseaddr
from zoneinfo import ZoneInfo

import config
from extract import is_carrier_domain, normalize_merchant

API = "https://gmail.googleapis.com/gmail/v1/users/me"

PACIFIC = ZoneInfo(config.TIMEZONE)
SELLER_SUBJECT_RE = re.compile("|".join(config.SELLER_SUBJECT_PATTERNS), re.I)
NOISE_SUBJECT_RE = re.compile("|".join(config.NOISE_SUBJECT_PATTERNS), re.I)

# "Order #123", "Order No. 123", "Order Number: 123", "order 1316734627", "Confirmation #123"
ORDER_NUMBER_RE = re.compile(
    r"(order|confirmation)\s*(#|no\.?|number:?)?\s*[A-Z0-9-]*\d[A-Z0-9-]{3,}", re.I
)
SHIPPING_SUBJECT_RE = re.compile(
    r"ship|deliver|on (its|the) way|out for delivery|arriv|track|package|delay|order|"
    r"thank you for (your )?(shopping|purchase)|purchase|receipt|confirm", re.I
)


def _get(session, url: str, params: dict):
    """GET with backoff. Gmail's per-minute quota comes back as 403 "Quota exceeded" /
    "rateLimitExceeded" (plus 429 / 5xx); all transient, so wait (up to a minute) and retry."""
    attempts = 8
    for attempt in range(attempts):
        resp = session.get(url, params=params)
        transient = resp.status_code in (429, 500, 502, 503, 504) or (
            resp.status_code == 403 and ("ateLimitExceeded" in resp.text or "Quota exceeded" in resp.text))
        if not transient or attempt == attempts - 1:
            if resp.status_code >= 400:
                print(f"Gmail API error {resp.status_code}: {resp.text[:300]}")
            resp.raise_for_status()
            return resp
        time.sleep(min(60, 5 * 2 ** attempt))


def search_ids(session, query: str) -> list[str]:
    ids, page = [], None
    while True:
        params = {"q": query, "maxResults": 500}
        if page:
            params["pageToken"] = page
        data = _get(session, f"{API}/messages", params).json()
        ids += [m["id"] for m in data.get("messages", [])]
        page = data.get("nextPageToken")
        if not page:
            return ids


def _exclude_self() -> str:
    start = int(datetime.fromisoformat(config.TRACKING_START).timestamp())
    return f'after:{start} -from:me -subject:"{config.SUMMARY_EMAIL_SUBJECT}"'


def _allowed_from() -> str:
    domains = sorted({d for _, d, _ in config.ALLOWED_SENDERS} | config.CARRIER_DOMAINS)
    return f"from:({' OR '.join(domains)})"


def recent_order_query() -> str:
    """Allow list only: mail from the listed retailers (and carriers) in the lookback window."""
    return f"{_allowed_from()} newer_than:{config.EMAIL_LOOKBACK_DAYS}d {_exclude_self()}"


def order_ref_query(order_number: str) -> str:
    order_number = re.sub(r"[^A-Za-z0-9-]", "", order_number)   # came from email text
    return (f'{_allowed_from()} "{order_number}" newer_than:{config.STALE_ORDER_SEARCH_DAYS}d '
            f"{_exclude_self()}")


def fetch_headers(session, message_id: str) -> dict:
    """Stage 1: sender, subject, labels, date only - no body is downloaded."""
    msg = _get(session, f"{API}/messages/{message_id}",
               {"format": "metadata", "metadataHeaders": ["From", "Subject"]}).json()
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    display, address = parseaddr(headers.get("from", ""))
    return {
        "id": message_id,
        "from": headers.get("from", ""),
        "display_name": display,
        "sender_domain": address.rpartition("@")[2].lower(),
        "subject": headers.get("subject", ""),
        "labels": msg.get("labelIds", []),
        "received": datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=PACIFIC),
    }


def fetch_body(session, message_id: str) -> str:
    """Stage 2: full body, only for emails that passed the header screen."""
    msg = _get(session, f"{API}/messages/{message_id}", {"format": "full"}).json()
    return _body_text(msg["payload"])[: config.EMAIL_BODY_MAX_CHARS]


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _parts(payload):
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _parts(part)


def _body_text(payload) -> str:
    plain = html_body = ""
    for part in _parts(payload):
        data = part.get("body", {}).get("data")
        if not data:
            continue
        if part.get("mimeType") == "text/plain" and not plain:
            plain = _decode(data)
        elif part.get("mimeType") == "text/html" and not html_body:
            html_body = _decode(data)
    # some senders ship a stub text part ("We'll let you know when your items ship.")
    # next to the real HTML, so use whichever has more content
    stripped = _strip_html(html_body) if html_body else ""
    text = plain if len(plain) >= len(stripped) else stripped
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t ]+", " ", text)).strip()


def _strip_html(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", markup)
    # keep tracking link targets (they often live only in an href); drop other URLs,
    # which are mostly click-tracking noise that would eat the body-length cap
    markup = re.sub(r'(?is)<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', _keep_track_link, markup)
    return html.unescape(re.sub(r"<[^>]+>", " ", markup))


def _keep_track_link(m) -> str:
    href, text = m.group(1), m.group(2)
    return f"{text} ({href})" if "track" in href.lower() else text


def is_seller_mail(email: dict) -> bool:
    """Seller-side mail. Only checked for platforms the user sells on, so a retailer's
    "Your order will ship by Oct 3" is never mistaken for a sale."""
    merchant = normalize_merchant(email["sender_domain"], email["display_name"])
    return merchant in config.SELLER_PLATFORMS and bool(SELLER_SUBJECT_RE.search(email["subject"]))


def screen_signature() -> str:
    """Fingerprint of every header-screen setting. Emails screened out under a different
    fingerprint are screened again, so filter/allow-list edits apply to past emails too."""
    settings = [config.ALLOWED_SENDERS, sorted(config.EXCLUDED_MERCHANTS), sorted(config.CARRIER_DOMAINS),
                sorted(config.SELLER_PLATFORMS), config.SELLER_SUBJECT_PATTERNS,
                config.NOISE_SUBJECT_PATTERNS, SHIPPING_SUBJECT_RE.pattern, ORDER_NUMBER_RE.pattern]
    return hashlib.sha1(json.dumps(settings, default=str).encode()).hexdigest()[:12]


def is_candidate(email: dict) -> bool:
    """Header-only screen before the body is downloaded (Claude makes the final call).
    Not noise, and either Gmail's Purchases category or order/shipping words in the subject.
    (The allow list has already limited this to listed retailers and carriers.)"""
    if NOISE_SUBJECT_RE.search(email["subject"]):
        return False
    if "CATEGORY_PURCHASES" in email.get("labels", []):
        return True
    return bool(SHIPPING_SUBJECT_RE.search(email["subject"]) or ORDER_NUMBER_RE.search(email["subject"]))


def is_excluded(email: dict) -> bool:
    """Not on the allow list (e.g. another Shopify shop), or an excluded retailer."""
    if is_carrier_domain(email["sender_domain"]):
        return False
    merchant = normalize_merchant(email["sender_domain"], email["display_name"])
    return merchant is None or merchant in config.EXCLUDED_MERCHANTS
