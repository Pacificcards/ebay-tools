-- ============================================================
-- ANALYTICS
-- ============================================================
CREATE TABLE IF NOT EXISTS listing_metadata (
    listing_id      TEXT PRIMARY KEY,
    title           TEXT,
    sku             TEXT,
    category        TEXT,
    current_price   NUMERIC(10,2),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS listing_metrics_raw (
    id              SERIAL PRIMARY KEY,
    listing_id      TEXT NOT NULL,
    date            DATE NOT NULL,
    impressions     INTEGER,
    clicks          INTEGER,
    page_views      INTEGER,
    fetched_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(listing_id, date)
);

CREATE TABLE IF NOT EXISTS orders_raw (
    id              SERIAL PRIMARY KEY,
    order_id        TEXT NOT NULL UNIQUE,
    listing_id      TEXT NOT NULL,
    order_date      DATE NOT NULL,
    quantity        INTEGER,
    sale_price      NUMERIC(10,2),
    fetched_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS listing_metrics_computed (
    id                      SERIAL PRIMARY KEY,
    listing_id              TEXT NOT NULL,
    date                    DATE NOT NULL,
    ctr                     NUMERIC(6,4),
    orders_per_1k_impr      NUMERIC(8,4),
    revenue_per_impression  NUMERIC(10,6),
    conversion_rate         NUMERIC(6,4),
    orders                  INTEGER,
    revenue                 NUMERIC(10,2),
    computed_at             TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(listing_id, date)
);

-- Populated manually: one row per tag per listing (e.g. 'pokemon', 'graded', 'sports')
CREATE TABLE IF NOT EXISTS listing_tags (
    listing_id      TEXT NOT NULL,
    tag             TEXT NOT NULL,
    PRIMARY KEY (listing_id, tag)
);

-- ============================================================
-- P&L (scaffold — implement later)
-- ============================================================
-- purchase_costs (listing_id, purchase_date, cost_per_unit, quantity, source)
-- pl_summary (listing_id, period, revenue, cogs, gross_profit, fees, net_profit)

-- ============================================================
-- LISTINGS LISTENER (scaffold — implement later)
-- ============================================================
-- listener_watchlist (search_query, max_price, category)
-- listener_alerts (listing_id, title, price, reference_price, alerted_at)
