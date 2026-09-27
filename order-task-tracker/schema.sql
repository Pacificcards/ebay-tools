-- ============================================================
-- ORDER TASK TRACKER
-- One row per Gmail message examined, so Claude reads each email once.
-- extraction holds email metadata + Claude's raw output; normalization (merchant,
-- exclusions, dates) is applied when read, so config edits apply to old rows too.
-- Rows with extract_version below config.EXTRACT_VERSION are re-read by Claude.
-- ============================================================
CREATE TABLE IF NOT EXISTS order_tracker_emails (
    message_id    TEXT PRIMARY KEY,
    received_at   TIMESTAMPTZ NOT NULL,
    order_number  TEXT,
    trackings     TEXT[] NOT NULL DEFAULT '{}',
    extraction    JSONB NOT NULL,
    extract_version INTEGER NOT NULL,
    processed_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS order_tracker_emails_order_idx ON order_tracker_emails (order_number);
CREATE INDEX IF NOT EXISTS order_tracker_emails_trackings_idx ON order_tracker_emails USING GIN (trackings);

-- Which Google Task belongs to which order / package. Kept here instead of in the
-- task description so descriptions stay concise and can be edited freely.
CREATE TABLE IF NOT EXISTS order_tracker_tasks (
    task_id       TEXT PRIMARY KEY,
    merchant      TEXT NOT NULL,
    order_number  TEXT NOT NULL,
    tracking      TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS order_tracker_tasks_order_idx ON order_tracker_tasks (merchant, order_number);
