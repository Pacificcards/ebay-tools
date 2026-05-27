-- ============================================================
-- LISTENER
-- ============================================================
CREATE TABLE IF NOT EXISTS listener_seen_items (
    item_id               TEXT PRIMARY KEY,
    watchlist_description TEXT,
    first_seen_at         TIMESTAMPTZ DEFAULT NOW()
);
