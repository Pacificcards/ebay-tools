import os
import sys
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.db import get_connection

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

st.set_page_config(page_title="Pacific Cards", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600&display=swap');
* { font-family: 'Space Grotesk', sans-serif !important; }
[data-testid="stMetric"] {
    background: #1d293d;
    border: 1px solid #314158;
    border-radius: 10px;
    padding: 16px 20px;
    min-height: 110px;
}
[data-testid="stMetricLabel"] { font-size: 0.78rem; color: #94a3b8; letter-spacing: 0.05em; text-transform: uppercase; }
[data-testid="stMetricValue"] { font-size: 1.8rem; font-weight: 600; color: #e2e8f0; }
[data-testid="stMetricDelta"] svg { display: none; }
.block-container { padding-top: 2rem; padding-bottom: 2rem; }
hr { border-color: #314158; margin: 1.5rem 0; }
</style>
""", unsafe_allow_html=True)

# ── Chart constants ───────────────────────────────────────────────────────────

CARD_BG  = "#1d293d"
GRID_CLR = "#314158"

BASE_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor=CARD_BG,
    plot_bgcolor=CARD_BG,
    margin=dict(l=12, r=12, t=72, b=8),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=11)),
    xaxis=dict(gridcolor=GRID_CLR, zeroline=False),
    yaxis=dict(gridcolor=GRID_CLR, zeroline=False),
    font=dict(color="#8892a4"),
    title_font=dict(color="#e8eaf0", size=13),
)

# ── Data loaders ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_listings() -> pd.DataFrame:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT listing_id, title, sku, current_price, status
                FROM listing_metadata
                ORDER BY
                    CASE status WHEN 'active' THEN 0 WHEN 'active_hidden' THEN 1 ELSE 2 END,
                    title
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["listing_id", "title", "sku", "current_price", "status"])


@st.cache_data(ttl=300)
def load_metrics(listing_id: str, start: date, end: date) -> pd.DataFrame:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, impressions_total, views_total, ctr, orders
                FROM listing_metrics_raw
                WHERE listing_id = %s AND date BETWEEN %s AND %s
                ORDER BY date
            """, (listing_id, start, end))
            rows = cur.fetchall()
    finally:
        conn.close()
    df = pd.DataFrame(rows, columns=["date", "impressions_total", "views_total", "ctr", "orders"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=300)
def load_all_metrics_for_date(target_date: date) -> pd.DataFrame:
    """All listings' metrics for a single date, joined with metadata."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    m.listing_id,
                    lm.title,
                    lm.current_price,
                    lm.status,
                    m.impressions_total,
                    m.views_total,
                    m.orders
                FROM listing_metrics_raw m
                JOIN listing_metadata lm USING (listing_id)
                WHERE m.date = %s
                ORDER BY m.impressions_total DESC NULLS LAST
            """, (target_date,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=[
        "listing_id", "title", "current_price", "status",
        "impressions_total", "views_total", "orders",
    ])

# ── Helpers ───────────────────────────────────────────────────────────────────

def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["view_rate"] = df.apply(
        lambda r: r["views_total"] / r["impressions_total"]
        if r["impressions_total"] and r["impressions_total"] > 0 else None, axis=1
    )
    df["impressions_per_order"] = df.apply(
        lambda r: r["impressions_total"] / r["orders"]
        if r["orders"] and r["orders"] > 0 else None, axis=1
    )
    df["views_per_order"] = df.apply(
        lambda r: r["views_total"] / r["orders"]
        if r["orders"] and r["orders"] > 0 else None, axis=1
    )
    return df


def fmt_delta(cur, pri) -> str | None:
    if cur is None or pri is None:
        return None
    if pri > 0:
        return f"{(cur - pri) / pri:+.1%}"
    if pri == 0 and cur > 0:
        return "↑ new"
    return None


# ── Chart builders ────────────────────────────────────────────────────────────

def make_impressions_views_ctr_chart(current_df: pd.DataFrame, prior_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()

    if not current_df.empty:
        fig.add_trace(go.Bar(
            x=current_df["date"], y=current_df["impressions_total"],
            name="Impressions", yaxis="y1",
            marker_color="#3d3799",
            hovertemplate="%{x}: %{y:,}<extra>Impressions</extra>",
        ))
    if not prior_df.empty:
        fig.add_trace(go.Bar(
            x=prior_df["date"], y=prior_df["impressions_total"],
            name="Impressions (prior week)", yaxis="y1",
            marker_color="#27205e", opacity=0.6,
            hovertemplate="%{x}: %{y:,}<extra>Impressions (prior week)</extra>",
        ))
    if not current_df.empty:
        fig.add_trace(go.Scatter(
            x=current_df["date"], y=current_df["views_total"],
            name="Views", yaxis="y2",
            mode="lines+markers",
            line=dict(color="#615fff", width=2),
            marker=dict(size=5),
            hovertemplate="%{x}: %{y:,}<extra>Views</extra>",
        ))
    if not prior_df.empty:
        fig.add_trace(go.Scatter(
            x=prior_df["date"], y=prior_df["views_total"],
            name="Views (prior week)", yaxis="y2",
            mode="lines",
            line=dict(color="#615fff", width=1.5, dash="dot"),
            hovertemplate="%{x}: %{y:,}<extra>Views (prior week)</extra>",
        ))
    if not current_df.empty:
        fig.add_trace(go.Scatter(
            x=current_df["date"], y=current_df["view_rate"],
            name="CTR", yaxis="y3",
            mode="lines+markers",
            line=dict(color="#f97316", width=1.5),
            marker=dict(size=4),
            hovertemplate="%{x}: %{y:.1%}<extra>CTR</extra>",
        ))
    if not prior_df.empty:
        fig.add_trace(go.Scatter(
            x=prior_df["date"], y=prior_df["view_rate"],
            name="CTR (prior week)", yaxis="y3",
            mode="lines",
            line=dict(color="#f97316", width=1, dash="dot"),
            hovertemplate="%{x}: %{y:.1%}<extra>CTR (prior week)</extra>",
        ))

    layout = dict(BASE_LAYOUT)
    layout.update(
        title="Impressions / Views / CTR",
        xaxis=dict(domain=[0, 0.93], gridcolor=GRID_CLR, zeroline=False),
        yaxis=dict(title="Impressions", side="left", gridcolor=GRID_CLR, zeroline=False),
        yaxis2=dict(title="Views", side="right", overlaying="y", showgrid=False, zeroline=False),
        yaxis3=dict(overlaying="y", side="right", showgrid=False, showticklabels=False, showline=False, zeroline=False),
        barmode="overlay",
        height=360,
    )
    fig.update_layout(**layout)
    return fig


def make_chart(metric: str, title: str, current_df: pd.DataFrame, prior_df: pd.DataFrame, pct: bool = False) -> go.Figure:
    fig = go.Figure()
    fmt = ".1%" if pct else None

    if not current_df.empty:
        fig.add_trace(go.Scatter(
            x=current_df["date"], y=current_df[metric],
            name="Current",
            mode="lines+markers",
            line=dict(color="#615fff", width=2),
            marker=dict(size=5),
            hovertemplate=f"%{{x}}: %{{y:{fmt}}}<extra></extra>" if fmt else None,
        ))
    if not prior_df.empty:
        fig.add_trace(go.Scatter(
            x=prior_df["date"], y=prior_df[metric],
            name="Prior week",
            mode="lines",
            line=dict(color="#615fff", width=1.5, dash="dot"),
            opacity=0.45,
            hovertemplate=f"%{{x}}: %{{y:{fmt}}}<extra>Prior week</extra>" if fmt else None,
        ))

    layout = dict(BASE_LAYOUT)
    layout.update(
        title=title,
        yaxis=dict(tickformat=".1%" if pct else None, gridcolor=GRID_CLR, zeroline=False),
        height=300,
    )
    fig.update_layout(**layout)
    return fig


# ── Cost Entry data loaders ───────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_past_payment_methods() -> list[str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT payment_method
                FROM import_queue
                WHERE payment_method IS NOT NULL AND payment_method != ''
                ORDER BY payment_method
            """)
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

@st.cache_data(ttl=30)
def load_import_queue() -> pd.DataFrame:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, purchase_date, description, source_ref, quantity, unit_cost, total_cost, status
                FROM import_queue
                WHERE status != 'ignored'
                ORDER BY purchase_date DESC
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["id", "purchase_date", "description", "source_ref", "quantity", "unit_cost", "total_cost", "status"])


@st.cache_data(ttl=30)
def load_sales() -> pd.DataFrame:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    o.order_id,
                    o.order_date,
                    o.sale_price,
                    COALESCE(lm.title, o.listing_id) AS title,
                    COALESCE(SUM(pa.cost_allocated), 0) AS total_costs,
                    COUNT(pa.id) AS cost_count
                FROM orders_raw o
                LEFT JOIN listing_metadata lm USING (listing_id)
                LEFT JOIN purchase_allocations pa ON pa.order_id = o.order_id
                GROUP BY o.order_id, o.order_date, o.sale_price, lm.title, o.listing_id
                ORDER BY o.order_date DESC
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["order_id", "order_date", "sale_price", "title", "total_costs", "cost_count"])


@st.cache_data(ttl=30)
def load_sale_allocations(order_id: str) -> pd.DataFrame:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    pa.id,
                    pa.cost_allocated,
                    pa.notes,
                    COALESCE(iq.description, 'Unknown') AS description,
                    iq.source,
                    iq.purchase_date,
                    iq.vendor,
                    iq.payment_method
                FROM purchase_allocations pa
                LEFT JOIN import_queue iq ON iq.id = pa.queue_item_id
                WHERE pa.order_id = %s
                ORDER BY pa.created_at
            """, (order_id,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["id", "cost_allocated", "notes", "description", "source", "purchase_date", "vendor", "payment_method"])


def _update_queue_status(ids: list, status: str) -> None:
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE import_queue SET status = %s, reviewed_at = NOW() WHERE id = ANY(%s)",
                (status, ids),
            )
    finally:
        conn.close()


def _save_allocation_from_queue(queue_item_id: int, order_id: str, cost_allocated: float, notes: str) -> None:
    """Link an import queue item to a sale. Does not change queue status — user marks done manually."""
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO purchase_allocations (queue_item_id, order_id, cost_allocated, notes)
                VALUES (%s, %s, %s, %s)
                """,
                (queue_item_id, order_id, cost_allocated, notes or None),
            )
    finally:
        conn.close()


def _save_manual_cost(order_id: str, purchase_date, description: str, cost_allocated: float, vendor: str, payment_method: str, notes: str) -> None:
    """Create a manual import_queue entry and immediately allocate it to a sale in one transaction."""
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO import_queue
                    (source, status, purchase_date, description, quantity, unit_cost, total_cost, vendor, payment_method)
                VALUES ('manual', 'allocated', %s, %s, 1, %s, %s, %s, %s)
                RETURNING id
                """,
                (purchase_date, description, cost_allocated, cost_allocated, vendor or None, payment_method or None),
            )
            queue_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO purchase_allocations (queue_item_id, order_id, cost_allocated, notes)
                VALUES (%s, %s, %s, %s)
                """,
                (queue_id, order_id, cost_allocated, notes or None),
            )
    finally:
        conn.close()


def _remove_allocation(allocation_id: int) -> None:
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM purchase_allocations WHERE id = %s", (allocation_id,))
    finally:
        conn.close()


@st.cache_data(ttl=30)
def load_batches() -> pd.DataFrame:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    sb.id,
                    sb.name,
                    sb.notes,
                    COUNT(DISTINCT bs.order_id)         AS sale_count,
                    COALESCE(SUM(o.sale_price), 0)      AS total_revenue,
                    COALESCE(costs.total_costs, 0)      AS total_costs
                FROM sale_batches sb
                LEFT JOIN batch_sales bs ON bs.batch_id = sb.id
                LEFT JOIN orders_raw o   ON o.order_id  = bs.order_id
                LEFT JOIN (
                    SELECT bs2.batch_id, SUM(pa.cost_allocated) AS total_costs
                    FROM batch_sales bs2
                    JOIN purchase_allocations pa ON pa.order_id = bs2.order_id
                    GROUP BY bs2.batch_id
                ) costs ON costs.batch_id = sb.id
                GROUP BY sb.id, sb.name, sb.notes, costs.total_costs
                ORDER BY sb.id DESC
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["id", "name", "notes", "sale_count", "total_revenue", "total_costs"])


@st.cache_data(ttl=30)
def load_batch_sales(batch_id: int) -> pd.DataFrame:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    o.order_id,
                    COALESCE(lm.title, o.listing_id) AS title,
                    o.order_date,
                    o.sale_price,
                    COALESCE(SUM(pa.cost_allocated), 0) AS total_costs,
                    COUNT(pa.id)                        AS cost_count
                FROM batch_sales bs
                JOIN orders_raw o       ON o.order_id    = bs.order_id
                LEFT JOIN listing_metadata lm ON lm.listing_id = o.listing_id
                LEFT JOIN purchase_allocations pa ON pa.order_id = o.order_id
                WHERE bs.batch_id = %s
                GROUP BY o.order_id, o.listing_id, lm.title, o.order_date, o.sale_price
                ORDER BY o.order_date DESC
            """, (batch_id,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["order_id", "title", "order_date", "sale_price", "total_costs", "cost_count"])


@st.cache_data(ttl=60)
def load_past_batch_names() -> list[str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM sale_batches ORDER BY name")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _create_batch(name: str, order_ids: list[str], notes: str) -> None:
    """Create batch, or merge into existing batch with the same name."""
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM sale_batches WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                batch_id = row[0]
            else:
                cur.execute(
                    "INSERT INTO sale_batches (name, notes) VALUES (%s, %s) RETURNING id",
                    (name, notes or None),
                )
                batch_id = cur.fetchone()[0]
            for oid in order_ids:
                cur.execute(
                    "INSERT INTO batch_sales (batch_id, order_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (batch_id, oid),
                )
    finally:
        conn.close()


def _update_batch_sales(batch_id: int, order_ids: list[str]) -> None:
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM batch_sales WHERE batch_id = %s", (batch_id,))
            for oid in order_ids:
                cur.execute(
                    "INSERT INTO batch_sales (batch_id, order_id) VALUES (%s, %s)",
                    (batch_id, oid),
                )
    finally:
        conn.close()


def _delete_batch(batch_id: int) -> None:
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM sale_batches WHERE id = %s", (batch_id,))
    finally:
        conn.close()


# ── App shell ─────────────────────────────────────────────────────────────────

st.title("Pacific Cards Co.")

listings = load_listings()
if listings.empty:
    st.warning("No listings found. Run sync_listings first.")
    st.stop()

yesterday     = date.today() - timedelta(days=1)
last_week     = yesterday - timedelta(days=7)
yesterday_lbl = yesterday.strftime("%-d %b %Y")
last_week_lbl = last_week.strftime("%-d %b")

tab_mc, tab_dive, tab_cost = st.tabs([
    ":material/query_stats: Mission Control",
    ":material/inventory_2: Listing Deep Dive",
    ":material/receipt_long: Cost Entry",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Mission Control
# ══════════════════════════════════════════════════════════════════════════════

with tab_mc:
    hdr_col, toggle_col = st.columns([3, 1])
    with hdr_col:
        st.subheader(f"Yesterday — {yesterday_lbl}")
    with toggle_col:
        cmp_mode = st.radio("Compare to", ["Prior week", "Day before"],
                            horizontal=True, label_visibility="collapsed")

    day_before    = yesterday - timedelta(days=1)
    cmp_date      = last_week if cmp_mode == "Prior week" else day_before
    cmp_lbl       = cmp_date.strftime("%-d %b")
    cmp_col_label = "WoW" if cmp_mode == "Prior week" else "DoD"

    today_df    = load_all_metrics_for_date(yesterday)
    prior_df_mc = load_all_metrics_for_date(cmp_date)

    if today_df.empty:
        st.info("No data for yesterday yet — check back after the pipeline runs.")
    else:
        # ── Aggregate KPIs ────────────────────────────────────────────────────
        tot_impr   = int(today_df["impressions_total"].sum())
        tot_views  = int(today_df["views_total"].sum())
        tot_orders = int(today_df["orders"].sum())

        pri_impr   = int(prior_df_mc["impressions_total"].sum()) if not prior_df_mc.empty else None
        pri_views  = int(prior_df_mc["views_total"].sum())       if not prior_df_mc.empty else None
        pri_orders = int(prior_df_mc["orders"].sum())            if not prior_df_mc.empty else None

        def kpi_delta(cur, pri):
            d = fmt_delta(cur, pri)
            return f"{d} vs {cmp_lbl}" if d else None

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Total Impressions", f"{tot_impr:,}", delta=kpi_delta(tot_impr, pri_impr))
        with c2:
            st.metric("Total Views", f"{tot_views:,}", delta=kpi_delta(tot_views, pri_views))
        with c3:
            st.metric("Total Orders", f"{tot_orders:,}", delta=kpi_delta(tot_orders, pri_orders))

        st.markdown("---")

        # ── Build listings table ──────────────────────────────────────────────
        merged = today_df.copy()
        if not prior_df_mc.empty:
            merged = merged.merge(
                prior_df_mc[["listing_id", "impressions_total", "views_total", "orders"]],
                on="listing_id", how="left", suffixes=("", "_prior")
            )
        else:
            merged["impressions_total_prior"] = None
            merged["views_total_prior"]       = None
            merged["orders_prior"]            = None

        def cmp_pct(cur, pri):
            try:
                if pri and pri > 0:
                    return (cur - pri) / pri
            except Exception:
                pass
            return None

        merged["impr_cmp"]  = merged.apply(lambda r: cmp_pct(r["impressions_total"], r.get("impressions_total_prior")), axis=1)
        merged["views_cmp"] = merged.apply(lambda r: cmp_pct(r["views_total"],       r.get("views_total_prior")),       axis=1)
        merged["ctr"]       = merged.apply(
            lambda r: r["views_total"] / r["impressions_total"]
            if r["impressions_total"] and r["impressions_total"] > 0 else None, axis=1
        )

        def flag(row):
            flags = []
            if row["impr_cmp"] is not None and row["impr_cmp"] <= -0.5:
                flags.append("⚠ Traffic drop")
            if row["views_cmp"] is not None and row["views_cmp"] >= 0.5:
                flags.append("↑ Views spike")
            if row["views_total"] >= 10 and (row["orders"] is None or row["orders"] == 0):
                flags.append("0 orders")
            return "  ".join(flags) if flags else ""

        merged["flags"] = merged.apply(flag, axis=1)

        # ── Display table ─────────────────────────────────────────────────────
        display = merged[[
            "title", "impressions_total", "impr_cmp",
            "views_total", "views_cmp", "ctr", "orders", "flags"
        ]].copy()

        display.columns = ["Title", "Impressions", f"Impr {cmp_col_label}", "Views", f"Views {cmp_col_label}", "CTR", "Orders", "Flags"]

        def fmt_int(x):
            return "—" if pd.isna(x) or x == 0 else f"{int(x):,}"

        display["Impressions"]            = display["Impressions"].map(fmt_int)
        display["Views"]                  = display["Views"].map(fmt_int)
        display["Orders"]                 = display["Orders"].map(fmt_int)
        display[f"Impr {cmp_col_label}"]  = display[f"Impr {cmp_col_label}"].map(lambda x: f"{x:+.1%}" if pd.notna(x) else "—")
        display[f"Views {cmp_col_label}"] = display[f"Views {cmp_col_label}"].map(lambda x: f"{x:+.1%}" if pd.notna(x) else "—")
        display["CTR"]                    = display["CTR"].map(lambda x: f"{x:.1%}" if pd.notna(x) else "—")

        st.dataframe(display, use_container_width=True, hide_index=True,
                     column_config={
                         "Title": st.column_config.TextColumn(width="large"),
                         "Flags": st.column_config.TextColumn(width="medium"),
                     })

        st.caption(f"Sorted by impressions. {cmp_col_label} vs {cmp_lbl}. Flags: ⚠ Traffic drop = impressions down ≥50% · ↑ Views spike = views up ≥50% · 0 orders = ≥10 views but no sale.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Listing Deep Dive
# ══════════════════════════════════════════════════════════════════════════════

with tab_dive:

    # ── Controls ──────────────────────────────────────────────────────────────
    def listing_label(row):
        label = row["title"] or row["listing_id"]
        if row["status"] != "active":
            label += f" [{row['status']}]"
        return label

    listing_options = {listing_label(row): row["listing_id"] for _, row in listings.iterrows()}

    ctrl_l, ctrl_m, ctrl_r = st.columns([2, 1, 1])
    with ctrl_l:
        search = st.text_input("Search by title", placeholder="e.g. Charizard", label_visibility="collapsed")
        filtered = {
            label: lid for label, lid in listing_options.items()
            if search.lower() in label.lower()
        } if search else listing_options

        if not filtered:
            st.warning("No listings match your search.")
            st.stop()

        selected_label = st.selectbox(
            f"{len(filtered)} listing{'s' if len(filtered) != 1 else ''} found",
            list(filtered.keys()),
            label_visibility="collapsed",
        )
        selected_id = filtered[selected_label]

    with ctrl_m:
        end_default   = yesterday
        start_default = end_default - timedelta(days=29)
        start_date = st.date_input("From", value=start_default)

    with ctrl_r:
        end_date = st.date_input("To", value=end_default)

    if start_date >= end_date:
        st.error("'From' must be before 'To'.")
        st.stop()

    # ── Load data ─────────────────────────────────────────────────────────────
    prior_start = start_date - timedelta(days=7)
    prior_end   = end_date   - timedelta(days=7)

    current_df = load_metrics(selected_id, start_date, end_date)
    prior_df   = load_metrics(selected_id, prior_start, prior_end)

    if not prior_df.empty:
        prior_df = prior_df.copy()
        prior_df["date"] = prior_df["date"] + timedelta(days=7)

    current_df = add_derived(current_df)
    prior_df   = add_derived(prior_df)

    # ── Listing info ──────────────────────────────────────────────────────────
    meta      = listings[listings["listing_id"] == selected_id].iloc[0]
    price_str = f"${meta['current_price']:.2f}" if pd.notna(meta["current_price"]) else "—"
    sku_str   = meta["sku"] if pd.notna(meta["sku"]) and meta["sku"] else "—"
    st.caption(f"ID: {selected_id} · SKU: {sku_str} · Price: {price_str} · Status: {meta['status']}")

    # ── KPI cards (yesterday vs same day last week) ───────────────────────────
    yts = pd.Timestamp(yesterday)
    yesterday_cur = current_df[current_df["date"] == yts].iloc[0] if not current_df.empty and (current_df["date"] == yts).any() else None
    yesterday_pri = prior_df[prior_df["date"] == yts].iloc[0]     if not prior_df.empty   and (prior_df["date"] == yts).any()   else None

    st.subheader(f"Yesterday — {yesterday_lbl}")

    def kpi(label, col, pct=False):
        cur_val = yesterday_cur[col] if yesterday_cur is not None and pd.notna(yesterday_cur[col]) else None
        pri_val = yesterday_pri[col] if yesterday_pri is not None and pd.notna(yesterday_pri[col]) else None
        display = (f"{cur_val:.1%}" if pct else f"{cur_val:,.0f}") if cur_val is not None else "—"
        delta   = fmt_delta(cur_val, pri_val)
        if delta and pri_val is not None:
            delta = f"{delta} vs {last_week_lbl}"
        st.metric(label, display, delta=delta)

    if yesterday_cur is None:
        st.caption("No data for yesterday yet.")

    c1, c2, c3, c4 = st.columns(4)
    with c1: kpi("Impressions", "impressions_total")
    with c2: kpi("Views",       "views_total")
    with c3: kpi("CTR",         "view_rate", pct=True)
    with c4: kpi("Orders",      "orders")

    # ── Charts ────────────────────────────────────────────────────────────────
    if current_df.empty:
        st.info("No data found for this listing in the selected date range.")
    else:
        st.plotly_chart(make_impressions_views_ctr_chart(current_df, prior_df), use_container_width=True)

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.plotly_chart(make_chart("orders", "Daily Quantity Sold", current_df, prior_df), use_container_width=True)
        with col_b:
            st.plotly_chart(make_chart("impressions_per_order", "Impressions per Unit Sold", current_df, prior_df), use_container_width=True)
        with col_c:
            st.plotly_chart(make_chart("views_per_order", "Views per Unit Sold", current_df, prior_df), use_container_width=True)

    # ── Raw data ──────────────────────────────────────────────────────────────
    with st.expander("Raw data"):
        if current_df.empty:
            st.write("No rows.")
        else:
            display = current_df.copy()
            display["date"] = display["date"].dt.date
            display["ctr"]  = display["ctr"].map(lambda x: f"{x:.2%}" if pd.notna(x) else "—")
            st.dataframe(display, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Cost Entry
# ══════════════════════════════════════════════════════════════════════════════

with tab_cost:

    sales_df = load_sales()

    cost_sub_sales, cost_sub_batches = st.tabs([
        ":material/receipt_long: Sales",
        ":material/folder_special: Batches",
    ])

    # ══════════════════════════════════════════════════════════════════════════
    # Sub-tab: Sales
    # ══════════════════════════════════════════════════════════════════════════
    with cost_sub_sales:

        col_list, col_detail = st.columns([1, 2], gap="large")

        # ── Left: Sales list ──────────────────────────────────────────────────
        with col_list:
            st.markdown("**Sales**")

            if sales_df.empty:
                st.caption("No sales found.")
                sel_rows = []
            else:
                n_missing = int((sales_df["cost_count"] == 0).sum())
                st.caption(f"{len(sales_df)} sales · {n_missing} uncosted  ·  select multiple to batch")

                tbl_df = sales_df[["order_date", "title", "sale_price", "cost_count"]].copy()
                tbl_df["status"] = sales_df["cost_count"].apply(lambda x: "costed" if x > 0 else "—")

                event = st.dataframe(
                    tbl_df,
                    use_container_width=True,
                    hide_index=True,
                    selection_mode="multi-row",
                    on_select="rerun",
                    key="sales_table",
                    column_config={
                        "order_date":  st.column_config.DateColumn("Date",    width="small"),
                        "title":       st.column_config.TextColumn("Title"),
                        "sale_price":  st.column_config.NumberColumn("Revenue", format="$%.2f", width="small"),
                        "cost_count":  st.column_config.NumberColumn("Costs",   width="small"),
                        "status":      st.column_config.TextColumn("Status",   width="small"),
                    },
                )
                sel_rows = event.selection.rows

        # ── Right: detail / batch panel ───────────────────────────────────────
        with col_detail:
            if not sel_rows:
                st.info("Select a sale to view costs, or select multiple sales to create a batch.")

            elif len(sel_rows) > 1:
                # ── Multi-select: create batch ────────────────────────────────
                selected_sales = sales_df.iloc[sel_rows]
                st.markdown(f"### Create batch from {len(selected_sales)} sales")
                total_rev = float(selected_sales["sale_price"].sum())
                total_cst = float(selected_sales["total_costs"].sum())
                k1, k2, k3 = st.columns(3)
                with k1: st.metric("Combined Revenue", f"${total_rev:.2f}")
                with k2: st.metric("Combined Costs",   f"${total_cst:.2f}")
                with k3:
                    p = total_rev - total_cst
                    st.metric("Gross Profit", f"${p:.2f}",
                              delta=f"{p/total_rev:.0%} margin" if total_rev > 0 else None)

                st.dataframe(
                    selected_sales[["order_date", "title", "sale_price", "total_costs"]],
                    use_container_width=True, hide_index=True,
                    column_config={
                        "order_date":  st.column_config.DateColumn("Date", width="small"),
                        "title":       st.column_config.TextColumn("Title"),
                        "sale_price":  st.column_config.NumberColumn("Revenue", format="$%.2f", width="small"),
                        "total_costs": st.column_config.NumberColumn("Costs",   format="$%.2f", width="small"),
                    },
                )

                with st.form("create_batch_form"):
                    b_name = st.selectbox(
                        "Batch name",
                        options=load_past_batch_names(),
                        accept_new_options=True,
                        placeholder="e.g. March Hobby Box Break — or pick existing to merge",
                    )
                    b_notes = st.text_area("Notes (optional)", height=60)
                    if st.form_submit_button("Save to Batch", type="primary"):
                        if not b_name or not str(b_name).strip():
                            st.error("Batch name is required.")
                        else:
                            try:
                                _create_batch(str(b_name).strip(), selected_sales["order_id"].tolist(), b_notes)
                                load_batches.clear()
                                load_past_batch_names.clear()
                                st.success(f"Saved {len(selected_sales)} sales to batch '{b_name}'.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed: {e}")

            else:
                # ── Single select: sale detail ────────────────────────────────
                sale        = sales_df.iloc[sel_rows[0]]
                order_id    = sale["order_id"]
                allocations = load_sale_allocations(order_id)
                total_costs = float(sale["total_costs"])
                revenue     = float(sale["sale_price"] or 0)
                profit      = revenue - total_costs
                margin      = profit / revenue if revenue > 0 else None

                st.markdown(f"### {sale['title']}")
                st.caption(f"{sale['order_date']} &nbsp;·&nbsp; {order_id}")

                k1, k2, k3 = st.columns(3)
                with k1: st.metric("Revenue",      f"${revenue:.2f}")
                with k2: st.metric("Total Costs",  f"${total_costs:.2f}")
                with k3: st.metric("Gross Profit", f"${profit:.2f}",
                                   delta=f"{margin:.0%} margin" if margin is not None else None)

                st.markdown("---")

                if not allocations.empty:
                    st.markdown("**Assigned costs**")
                    for _, alloc in allocations.iterrows():
                        a1, a2, a3 = st.columns([5, 1, 1])
                        with a1:
                            src      = "eBay" if alloc.get("source") == "ebay_purchase" else "Manual"
                            meta     = " · ".join(filter(None, [
                                src,
                                str(alloc["purchase_date"]) if alloc.get("purchase_date") else None,
                                alloc.get("vendor"),
                                alloc.get("payment_method"),
                                alloc.get("notes"),
                            ]))
                            st.write(alloc["description"])
                            st.caption(meta)
                        with a2:
                            st.write(f"**${alloc['cost_allocated']:.2f}**")
                        with a3:
                            if st.button("✕", key=f"rm_{alloc['id']}", help="Remove"):
                                try:
                                    _remove_allocation(int(alloc["id"]))
                                    load_sales.clear()
                                    load_sale_allocations.clear()
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed: {e}")
                    st.divider()
                else:
                    st.info("No costs assigned yet.")

                st.markdown("**Add a cost**")
                add_mode = st.radio(
                    "add_mode", ["From import queue", "Enter manually"],
                    horizontal=True, label_visibility="collapsed", key="add_mode",
                )

                if add_mode == "From import queue":
                    queue_df  = load_import_queue()
                    available = queue_df[queue_df["status"].isin(["pending", "reviewed"])]

                    if available.empty:
                        st.info("Import queue is empty — use 'Enter manually' instead.")
                    else:
                        q_search = st.text_input(
                            "Search queue", placeholder="e.g. PSA, Topps, hobby box",
                            key="q_search",
                        )
                        if q_search:
                            available = available[available["description"].str.contains(q_search, case=False, na=False)]

                        if available.empty:
                            st.caption("No matches in queue.")
                        else:
                            q_labels = [
                                f"{r['description']}  —  ${r['total_cost'] or 0:.2f}  ({r['purchase_date']})"
                                for _, r in available.iterrows()
                            ]
                            q_ids   = available["id"].tolist()
                            q_costs = available["total_cost"].tolist()

                            with st.form("queue_alloc_form"):
                                sel_q      = st.selectbox("Purchase", q_labels)
                                q_idx      = q_labels.index(sel_q)
                                cost_input = st.number_input("Amount ($)", min_value=0.0, value=float(q_costs[q_idx] or 0), step=0.01, format="%.2f")
                                q_notes    = st.text_input("Notes (optional)")
                                if st.form_submit_button("Add Cost", type="primary"):
                                    try:
                                        _save_allocation_from_queue(int(q_ids[q_idx]), order_id, cost_input, q_notes)
                                        load_sales.clear()
                                        load_sale_allocations.clear()
                                        st.success("Cost added.")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Failed to save: {e}")

                else:  # manual
                    with st.form("manual_cost_form"):
                        mc1, mc2 = st.columns(2)
                        with mc1:
                            mc_date   = st.date_input("Date", value=date.today())
                        with mc2:
                            mc_amount = st.number_input("Amount ($)", min_value=0.0, value=0.0, step=0.01, format="%.2f")
                        mc_desc = st.text_input("Description", placeholder="e.g. PSA grading fee")
                        mc3, mc4 = st.columns(2)
                        with mc3:
                            mc_vendor  = st.text_input("Vendor", placeholder="e.g. PSA, USPS, Topps")
                        with mc4:
                            mc_payment = st.selectbox(
                                "Payment Method",
                                options=load_past_payment_methods(),
                                accept_new_options=True,
                                placeholder="e.g. Costco Visa, PayPal, Cash",
                            )
                        mc_notes = st.text_input("Notes (optional)")
                        if st.form_submit_button("Add Cost", type="primary"):
                            if not mc_desc.strip():
                                st.error("Description is required.")
                            elif mc_amount <= 0:
                                st.error("Amount must be greater than zero.")
                            else:
                                try:
                                    _save_manual_cost(order_id, mc_date, mc_desc.strip(), mc_amount, mc_vendor.strip() or None, mc_payment, mc_notes)
                                    load_sales.clear()
                                    load_sale_allocations.clear()
                                    st.success("Cost added.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to save: {e}")

        # ── Import Queue (full-width) ─────────────────────────────────────────
        st.divider()
        with st.expander(":material/inbox: Import Queue — unmatched purchases"):
            queue_df = load_import_queue()
            pending  = queue_df[queue_df["status"].isin(["pending", "reviewed"])]

            if pending.empty:
                st.info("No pending items in queue.")
            else:
                disp_q = pending.copy()
                disp_q.insert(0, "Select", False)

                edited_q = st.data_editor(
                    disp_q,
                    use_container_width=True,
                    hide_index=True,
                    key="queue_editor",
                    column_config={
                        "Select":        st.column_config.CheckboxColumn("", width="small"),
                        "id":            None,
                        "purchase_date": st.column_config.DateColumn("Date", width="small"),
                        "description":   st.column_config.TextColumn("Description", width="large"),
                        "source_ref":    st.column_config.TextColumn("Source", width="medium"),
                        "quantity":      st.column_config.NumberColumn("Qty", width="small"),
                        "unit_cost":     st.column_config.NumberColumn("Unit Cost", format="$%.2f", width="small"),
                        "total_cost":    st.column_config.NumberColumn("Total", format="$%.2f", width="small"),
                        "status":        st.column_config.TextColumn("Status", width="small"),
                    },
                    disabled=["purchase_date", "description", "source_ref", "quantity", "unit_cost", "total_cost", "status"],
                )

                q_mask    = edited_q["Select"] == True
                q_sel_ids = pending.loc[q_mask.values[:len(pending)], "id"].tolist()

                if q_sel_ids:
                    qb1, qb2, _ = st.columns([1, 1, 5])
                    with qb1:
                        if st.button("Mark ignored", key="q_ign"):
                            try:
                                _update_queue_status(q_sel_ids, "ignored")
                                st.success(f"Marked {len(q_sel_ids)} item(s) as ignored.")
                                if "queue_editor" in st.session_state:
                                    del st.session_state["queue_editor"]
                                load_import_queue.clear()
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed: {e}")
                    with qb2:
                        if st.button("Mark reviewed", key="q_rev"):
                            try:
                                _update_queue_status(q_sel_ids, "reviewed")
                                st.success(f"Marked {len(q_sel_ids)} item(s) as reviewed.")
                                if "queue_editor" in st.session_state:
                                    del st.session_state["queue_editor"]
                                load_import_queue.clear()
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # Sub-tab: Batches
    # ══════════════════════════════════════════════════════════════════════════
    with cost_sub_batches:

        batches_df = load_batches()

        bcol_list, bcol_detail = st.columns([1, 2], gap="large")

        with bcol_list:
            st.markdown("**Batches**")
            if batches_df.empty:
                st.caption("No batches yet. Select multiple sales in the Sales tab to create one.")
                b_sel_rows = []
            else:
                b_tbl = batches_df[["name", "sale_count", "total_revenue", "total_costs"]].copy()
                b_tbl["profit"] = b_tbl["total_revenue"] - b_tbl["total_costs"]
                b_event = st.dataframe(
                    b_tbl,
                    use_container_width=True,
                    hide_index=True,
                    selection_mode="single-row",
                    on_select="rerun",
                    key="batch_table",
                    column_config={
                        "name":          st.column_config.TextColumn("Batch"),
                        "sale_count":    st.column_config.NumberColumn("Sales",   width="small"),
                        "total_revenue": st.column_config.NumberColumn("Revenue", format="$%.2f", width="small"),
                        "total_costs":   st.column_config.NumberColumn("Costs",   format="$%.2f", width="small"),
                        "profit":        st.column_config.NumberColumn("Profit",  format="$%.2f", width="small"),
                    },
                )
                b_sel_rows = b_event.selection.rows

        with bcol_detail:
            if not b_sel_rows:
                st.info("Select a batch to view its details.")
            else:
                batch       = batches_df.iloc[b_sel_rows[0]]
                batch_id    = int(batch["id"])
                batch_sales = load_batch_sales(batch_id)

                total_rev = float(batch_sales["sale_price"].sum()) if not batch_sales.empty else 0.0
                total_cst = float(batch_sales["total_costs"].sum()) if not batch_sales.empty else 0.0
                profit    = total_rev - total_cst
                margin    = profit / total_rev if total_rev > 0 else None

                # Header row with delete button
                hcol, dcol = st.columns([4, 1])
                with hcol:
                    st.markdown(f"### {batch['name']}")
                    if batch.get("notes"):
                        st.caption(batch["notes"])
                with dcol:
                    if st.button("Delete batch", key="del_batch_btn"):
                        st.session_state["confirm_delete"] = batch_id
                    if st.session_state.get("confirm_delete") == batch_id:
                        st.warning(f"Delete '{batch['name']}'?")
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("Yes, delete", type="primary", key="confirm_del"):
                                try:
                                    _delete_batch(batch_id)
                                    load_batches.clear()
                                    load_batch_sales.clear()
                                    load_past_batch_names.clear()
                                    del st.session_state["confirm_delete"]
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed: {e}")
                        with c2:
                            if st.button("Cancel", key="cancel_del"):
                                del st.session_state["confirm_delete"]
                                st.rerun()

                k1, k2, k3 = st.columns(3)
                with k1: st.metric("Revenue",      f"${total_rev:.2f}")
                with k2: st.metric("Total Costs",  f"${total_cst:.2f}")
                with k3: st.metric("Gross Profit", f"${profit:.2f}",
                                   delta=f"{margin:.0%} margin" if margin is not None else None)

                st.divider()
                st.markdown("**Sales in this batch**")

                if batch_sales.empty:
                    st.caption("No sales assigned yet.")
                else:
                    st.dataframe(
                        batch_sales[["order_date", "title", "sale_price", "total_costs", "cost_count"]],
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "order_date":  st.column_config.DateColumn("Date",    width="small"),
                            "title":       st.column_config.TextColumn("Title"),
                            "sale_price":  st.column_config.NumberColumn("Revenue", format="$%.2f", width="small"),
                            "total_costs": st.column_config.NumberColumn("Costs",   format="$%.2f", width="small"),
                            "cost_count":  st.column_config.NumberColumn("# Costs", width="small"),
                        },
                    )

                # ── Add cost to a sale within this batch ──────────────────────
                with st.expander("Add a cost to this batch"):
                    if batch_sales.empty:
                        st.caption("Add sales to this batch first.")
                    else:
                        b_sale_label_map = {
                            row["order_id"]: f"{str(row['order_date'])[5:]} · {(row['title'] or '')[:40]} · ${row['sale_price'] or 0:.2f}"
                            for _, row in batch_sales.iterrows()
                        }
                        b_target = st.selectbox(
                            "Assign cost to which sale?",
                            options=list(b_sale_label_map.keys()),
                            format_func=lambda oid: b_sale_label_map.get(oid, oid),
                            key="batch_cost_target",
                        )
                        b_add_mode = st.radio(
                            "b_add_mode", ["From import queue", "Enter manually"],
                            horizontal=True, label_visibility="collapsed", key="b_add_mode",
                        )
                        if b_add_mode == "From import queue":
                            queue_df  = load_import_queue()
                            available = queue_df[queue_df["status"].isin(["pending", "reviewed"])]
                            if available.empty:
                                st.info("Import queue is empty — use 'Enter manually'.")
                            else:
                                bq_labels = [
                                    f"{r['description']}  —  ${r['total_cost'] or 0:.2f}  ({r['purchase_date']})"
                                    for _, r in available.iterrows()
                                ]
                                bq_ids   = available["id"].tolist()
                                bq_costs = available["total_cost"].tolist()
                                with st.form("batch_queue_form"):
                                    bq_sel   = st.selectbox("Purchase", bq_labels)
                                    bq_idx   = bq_labels.index(bq_sel)
                                    bq_amt   = st.number_input("Amount ($)", min_value=0.0, value=float(bq_costs[bq_idx] or 0), step=0.01, format="%.2f")
                                    bq_notes = st.text_input("Notes (optional)")
                                    if st.form_submit_button("Add Cost", type="primary"):
                                        try:
                                            _save_allocation_from_queue(int(bq_ids[bq_idx]), b_target, bq_amt, bq_notes)
                                            load_sales.clear()
                                            load_batch_sales.clear()
                                            load_batches.clear()
                                            st.success("Cost added.")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"Failed: {e}")
                        else:
                            with st.form("batch_manual_form"):
                                bm1, bm2 = st.columns(2)
                                with bm1:
                                    bm_date   = st.date_input("Date", value=date.today(), key="bm_date")
                                with bm2:
                                    bm_amount = st.number_input("Amount ($)", min_value=0.0, value=0.0, step=0.01, format="%.2f", key="bm_amount")
                                bm_desc    = st.text_input("Description", placeholder="e.g. PSA grading fee", key="bm_desc")
                                bm3, bm4 = st.columns(2)
                                with bm3:
                                    bm_vendor  = st.text_input("Vendor", placeholder="e.g. PSA", key="bm_vendor")
                                with bm4:
                                    bm_payment = st.selectbox(
                                        "Payment Method",
                                        options=load_past_payment_methods(),
                                        accept_new_options=True,
                                        placeholder="e.g. Costco Visa",
                                        key="bm_payment",
                                    )
                                bm_notes = st.text_input("Notes (optional)", key="bm_notes")
                                if st.form_submit_button("Add Cost", type="primary"):
                                    if not bm_desc.strip():
                                        st.error("Description is required.")
                                    elif bm_amount <= 0:
                                        st.error("Amount must be greater than zero.")
                                    else:
                                        try:
                                            _save_manual_cost(b_target, bm_date, bm_desc.strip(), bm_amount, bm_vendor.strip() or None, bm_payment, bm_notes)
                                            load_sales.clear()
                                            load_batch_sales.clear()
                                            load_batches.clear()
                                            st.success("Cost added.")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"Failed: {e}")

                # ── Manage sales membership ───────────────────────────────────
                with st.expander("Manage sales in batch"):
                    sale_label_map = {
                        row["order_id"]: f"{str(row['order_date'])[5:]} · {(row['title'] or '')[:40]} · ${row['sale_price'] or 0:.2f}"
                        for _, row in sales_df.iterrows()
                    }
                    current_ids = batch_sales["order_id"].tolist() if not batch_sales.empty else []
                    new_ids = st.multiselect(
                        "Sales assigned to this batch",
                        options=list(sale_label_map.keys()),
                        default=current_ids,
                        format_func=lambda oid: sale_label_map.get(oid, oid),
                    )
                    if st.button("Save changes", type="primary", key="update_batch"):
                        try:
                            _update_batch_sales(batch_id, new_ids)
                            load_batches.clear()
                            load_batch_sales.clear()
                            st.success("Batch updated.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed: {e}")
