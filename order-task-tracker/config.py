"""Constants for the order task tracker."""

TIMEZONE = "America/Los_Angeles"          # all date logic in Pacific, never UTC
EMAIL_LOOKBACK_DAYS = 14                  # newer_than:14d
STALE_TASK_RECHECK_DAYS = 14              # open tasks untouched this long get a forced re-lookup
STALE_ORDER_SEARCH_DAYS = 120             # how far back the stale recheck searches Gmail for an OrderRef

ORDERS_TASKLIST_NAME = "Orders (Claude)"

# Fresh start: emails received before this are ignored entirely (no backlog tasks).
TRACKING_START = "2026-09-25T00:00:00-07:00"

# ALLOW LIST: only mail from these senders is ever looked at.
# (retailer, sender domain, required sender display name or None). Subdomains match, e.g.
# "target.com" covers oe.target.com. Shared senders (Shopify) need the display name.
ALLOWED_SENDERS = [
    ("eBay", "ebay.com", None),                 # seller-side mail is filtered out separately
    ("Pokemon Center", "em.pokemon.com", None),
    ("Pokemon Center", "pokemoncenter.narvar.com", None),
    ("Target", "target.com", None),
    ("Costco", "orders.costco.com", None),
    ("Costco", "logistics.costco.com", None),
    ("Fanatics", "fanatics.com", None),
    ("Fanatics Collect", "fanaticscollect.com", None),
    ("Topps", "topps.com", None),
    ("Topps", "runfair.com", None),
    ("Topps", "shopifyemail.com", "Topps"),
    ("TAG", "taggrading.com", None),
    ("TAG", "shopifyemail.com", "TAG"),
    ("PSA", "psacard.com", None),
    ("Best Buy", "bestbuy.com", None),
    ("Walmart", "walmart.com", None),
    ("Macy's", "macys.com", None),
]

# Retailers to ignore even if allowed above.
EXCLUDED_MERCHANTS: set[str] = set()

# Carrier mail is also read, but can only UPDATE the task of an allowed retailer's order
# (matched by tracking number); it never creates a task. pirateship.com is deliberately
# absent: it's the user's own outgoing-label account.
CARRIER_DOMAINS = {"fedex.com", "ups.com", "usps.com", "dhl.com"}

# The inbox also receives the user's SELLER mail (eBay/WhatNot sales, payouts, labels).
# Subjects matching these never reach Claude; Claude's is_purchase check is the backstop.
SELLER_ACCOUNTS = ["pacificcardsco"]     # the user's own seller usernames: mail naming them is seller-side
SELLER_SUBJECT_PATTERNS = [
    r"pacificcardsco", r"sent (you )?a message", r"^re:\s",
    r"you made the sale", r"\byou sold\b", r"\bsold\b.*\bship\b", r"ship (it )?(now|by)",
    r"\bpayout", r"labels? (are|is) ready", r"order is ready to ship", r"new order from", r"(message|question) from (a |your )?buyer", r"label (purchased|created)",
]

# Subjects that are never purchase updates (checked on sender+subject, before any body is read)
NOISE_SUBJECT_PATTERNS = [
    r"\boffer\b", r"counteroffer", r"\bmatch(es)?\b", r"\bbid\b", r"daily digest",
    r"what do you think", r"\breview\b", r"rate your", r"survey",
]

CARRIER_TRACKING_URL_PATTERNS = {
    "fedex": "https://www.fedex.com/apps/fedextrack?trknbr={tracking}",
    "ups":   "https://www.ups.com/track?tracknum={tracking}",
    "usps":  "https://tools.usps.com/go/TrackConfirmAction?tLabels={tracking}",
    "dhl":   "https://www.dhl.com/us-en/home/tracking.html?tracking-id={tracking}",
    # Amazon Logistics has no public tracking page; use the email's own "Track Package" link
}

SUMMARY_EMAIL_SUBJECT = "Order Task Tracker"     # excluded from searches so the tool never reads its own report

# Claude (headless Claude Code on the Pro subscription)
EXTRACT_VERSION = 2                      # bump when the extraction schema/prompt changes -> cached rows re-read
EXTRACT_MODEL = "haiku"
LOOKUP_MODEL = "sonnet"
EXTRACT_BATCH_SIZE = 8                    # emails per Claude call
EMAIL_BODY_MAX_CHARS = 6000               # plain-text body cap sent to Claude
MAX_WEB_LOOKUPS_PER_RUN = 10
CLAUDE_TIMEOUT_SECONDS = 600
