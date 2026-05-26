-- ============================================================
-- ANALYTICS
-- ============================================================
CREATE TABLE IF NOT EXISTS listing_metadata (
    listing_id          TEXT PRIMARY KEY,
    title               TEXT,
    sku                 TEXT,
    current_price       NUMERIC(10,2),
    status              TEXT,                -- 'active', 'active_hidden', 'ended'
    hide_from_search    BOOLEAN,             -- HideFromSearch from Trading API
    hide_reason         TEXT,               -- ReasonHideFromSearch from Trading API
    last_synced_at      TIMESTAMPTZ,        -- when sync_listings last ran
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS listing_metrics_raw (
    id                  SERIAL PRIMARY KEY,
    listing_id          TEXT NOT NULL,
    date                DATE NOT NULL,
    ctr                 NUMERIC(8,6),
    impressions_total   INTEGER,
    impressions_search  INTEGER,
    impressions_store   INTEGER,
    views_total         INTEGER,
    views_search        INTEGER,
    views_store         INTEGER,
    views_direct        INTEGER,
    views_off_ebay      INTEGER,
    views_other_ebay    INTEGER,
    orders              INTEGER,
    fetched_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(listing_id, date)
);

CREATE TABLE IF NOT EXISTS orders_raw (
    id              SERIAL PRIMARY KEY,
    order_id        TEXT NOT NULL UNIQUE,
    listing_id      TEXT NOT NULL,
    order_date      DATE NOT NULL,
    quantity        INTEGER,
    sale_price      NUMERIC(10,2),
    shipping_price  NUMERIC(10,2),
    title           TEXT,
    fetched_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS listing_metrics_computed (
    id                  SERIAL PRIMARY KEY,
    listing_id          TEXT NOT NULL,
    date                DATE NOT NULL,
    ctr                 NUMERIC(8,6),
    orders              INTEGER,
    quantity            INTEGER,
    revenue             NUMERIC(10,2),
    conversion_rate     NUMERIC(8,6),
    units_per_view      NUMERIC(8,6),
    units_per_1k_impr   NUMERIC(8,4),
    computed_at         TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(listing_id, date)
);

-- Populated manually: one row per tag per listing (e.g. 'pokemon', 'graded', 'sports')
CREATE TABLE IF NOT EXISTS listing_tags (
    listing_id      TEXT NOT NULL,
    tag             TEXT NOT NULL,
    PRIMARY KEY (listing_id, tag)
);

-- ============================================================
-- P&L — PURCHASE IMPORT QUEUE
-- ============================================================

-- Raw buyer-side transactions from GetMyeBayBuying WonList.
-- Append-only staging table; used to detect new purchases between runs.
CREATE TABLE IF NOT EXISTS ebay_purchases_raw (
    id              SERIAL PRIMARY KEY,
    ebay_item_id    TEXT NOT NULL,
    transaction_id  TEXT NOT NULL,
    title           TEXT,
    seller_id       TEXT,
    purchase_date   DATE NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    item_cost       NUMERIC(10,2),
    shipping_cost   NUMERIC(10,2),
    total_cost      NUMERIC(10,2),
    fetched_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(ebay_item_id, transaction_id)
);

-- Unified queue for unreviewed cost records (eBay purchases + manual entries).
-- Auto-populated from ebay_purchases_raw; manual entries inserted directly.
CREATE TABLE IF NOT EXISTS import_queue (
    id              SERIAL PRIMARY KEY,
    source          TEXT NOT NULL,                      -- 'ebay_purchase', 'manual'
    status          TEXT NOT NULL DEFAULT 'pending',    -- 'pending', 'reviewed', 'allocated', 'ignored'
    purchase_date   DATE NOT NULL,
    description     TEXT NOT NULL,
    source_ref      TEXT,                               -- ebay_item_id or receipt ref
    quantity        INTEGER DEFAULT 1,
    unit_cost       NUMERIC(10,2),
    total_cost      NUMERIC(10,2),
    notes           TEXT,
    vendor          TEXT,
    payment_method  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at     TIMESTAMPTZ
);

-- Links a queue item (cost) to one or more sales. Supports all matching patterns.
CREATE TABLE IF NOT EXISTS purchase_allocations (
    id              SERIAL PRIMARY KEY,
    queue_item_id   INTEGER REFERENCES import_queue(id),
    order_id        TEXT REFERENCES orders_raw(order_id),
    cost_allocated  NUMERIC(10,2) NOT NULL,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Per-transaction fees from eBay Finances API (getBillingActivities).
CREATE TABLE IF NOT EXISTS order_fees (
    id                      SERIAL PRIMARY KEY,
    billing_transaction_id  TEXT NOT NULL UNIQUE,
    order_id                TEXT,
    listing_id              TEXT,
    transaction_date        DATE,
    fee_type                TEXT,
    fee_type_description    TEXT,
    amount                  NUMERIC(10,2),
    booking_entry           TEXT,              -- 'CREDIT' or 'DEBIT'
    currency                TEXT DEFAULT 'USD',
    fetched_at              TIMESTAMPTZ DEFAULT NOW()
);

-- Period-level non-COGS operating expenses.
CREATE TABLE IF NOT EXISTS operating_expenses (
    id              SERIAL PRIMARY KEY,
    expense_date    DATE NOT NULL,
    description     TEXT NOT NULL,
    category        TEXT,   -- 'shipping_supplies', 'storage', 'software', 'other'
    amount          NUMERIC(10,2) NOT NULL,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- SALE BATCHES
-- ============================================================

-- Named groups of sales for aggregated P&L (e.g. cards from one hobby box).
CREATE TABLE IF NOT EXISTS sale_batches (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Many-to-many: sales assigned to a batch.
CREATE TABLE IF NOT EXISTS batch_sales (
    batch_id    INTEGER REFERENCES sale_batches(id) ON DELETE CASCADE,
    order_id    TEXT REFERENCES orders_raw(order_id),
    PRIMARY KEY (batch_id, order_id)
);

-- ============================================================
-- LISTINGS LISTENER (scaffold — implement later)
-- ============================================================
-- listener_watchlist (search_query, max_price, category)
-- listener_alerts (listing_id, title, price, reference_price, alerted_at)
