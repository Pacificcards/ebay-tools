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
        lambda r: min(r["views_total"] / r["impressions_total"], 1.0)
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

tab_mc, tab_dive = st.tabs([
    ":material/query_stats: Mission Control",
    ":material/inventory_2: Listing Deep Dive",
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
