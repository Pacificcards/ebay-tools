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

st.set_page_config(page_title="Pacific Cards — Listing Deep Dive", layout="wide")
st.title("Listing Deep Dive")


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


# ── Sidebar ──────────────────────────────────────────────────────────────────

listings = load_listings()

if listings.empty:
    st.warning("No listings found. Run sync_listings first.")
    st.stop()

def listing_label(row):
    label = row["title"] or row["listing_id"]
    if row["status"] != "active":
        label += f" [{row['status']}]"
    return label

listing_options = {listing_label(row): row["listing_id"] for _, row in listings.iterrows()}

with st.sidebar:
    st.header("Listing")
    search = st.text_input("Search by title", placeholder="e.g. Charizard")
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
    )
    selected_id = filtered[selected_label]

    st.header("Date range")
    end_default = date.today() - timedelta(days=1)
    start_default = end_default - timedelta(days=29)
    start_date = st.date_input("From", value=start_default)
    end_date = st.date_input("To", value=end_default)

    if start_date >= end_date:
        st.error("'From' must be before 'To'.")
        st.stop()

# ── Load data ─────────────────────────────────────────────────────────────────

window_days = (end_date - start_date).days + 1
prior_start = start_date - timedelta(days=7)
prior_end = end_date - timedelta(days=7)

current_df = load_metrics(selected_id, start_date, end_date)
prior_df = load_metrics(selected_id, prior_start, prior_end)

# Shift prior dates forward 7 days so they align with current on the x-axis
if not prior_df.empty:
    prior_df = prior_df.copy()
    prior_df["date"] = prior_df["date"] + timedelta(days=7)

# ── Listing info ──────────────────────────────────────────────────────────────

meta = listings[listings["listing_id"] == selected_id].iloc[0]
price_str = f"${meta['current_price']:.2f}" if pd.notna(meta["current_price"]) else "—"
sku_str = meta["sku"] if pd.notna(meta["sku"]) and meta["sku"] else "—"
st.caption(f"ID: {selected_id} · SKU: {sku_str} · Price: {price_str} · Status: {meta['status']}")

# ── KPI cards ─────────────────────────────────────────────────────────────────

def kpi(label, current_df, prior_df, col, fmt="{:,.0f}"):
    cur_val = current_df[col].sum() if not current_df.empty else 0
    pri_val = prior_df[col].sum() if not prior_df.empty else None

    if col == "ctr":
        cur_val = current_df[col].mean() if not current_df.empty else 0
        pri_val = prior_df[col].mean() if not prior_df.empty else None
        fmt = "{:.2%}"

    delta = None
    if pri_val is not None and pri_val > 0:
        delta = f"{(cur_val - pri_val) / pri_val:+.1%} vs prior week"
    elif pri_val == 0 and cur_val > 0:
        delta = "↑ from 0 prior week"

    st.metric(label, fmt.format(cur_val), delta=delta)


col1, col2, col3, col4 = st.columns(4)
with col1:
    kpi("Impressions", current_df, prior_df, "impressions_total")
with col2:
    kpi("Views", current_df, prior_df, "views_total")
with col3:
    kpi("Avg CTR", current_df, prior_df, "ctr")
with col4:
    kpi("Orders", current_df, prior_df, "orders")

# ── Charts ────────────────────────────────────────────────────────────────────

def make_impressions_views_ctr_chart(current_df: pd.DataFrame, prior_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()

    # Impressions — bars, left axis
    if not current_df.empty:
        fig.add_trace(go.Bar(
            x=current_df["date"], y=current_df["impressions_total"],
            name="Impressions", yaxis="y1",
            marker_color="#aec7e8",
            hovertemplate="%{x}: %{y:,}<extra>Impressions</extra>",
        ))
    if not prior_df.empty:
        fig.add_trace(go.Bar(
            x=prior_df["date"], y=prior_df["impressions_total"],
            name="Impressions (prior week)", yaxis="y1",
            marker_color="#d0e4f5", opacity=0.5,
            hovertemplate="%{x}: %{y:,}<extra>Impressions (prior week)</extra>",
        ))

    # Views — line, right axis
    if not current_df.empty:
        fig.add_trace(go.Scatter(
            x=current_df["date"], y=current_df["views_total"],
            name="Views", yaxis="y2",
            mode="lines+markers",
            line=dict(color="#1f77b4", width=2),
            marker=dict(size=5),
            hovertemplate="%{x}: %{y:,}<extra>Views</extra>",
        ))
    if not prior_df.empty:
        fig.add_trace(go.Scatter(
            x=prior_df["date"], y=prior_df["views_total"],
            name="Views (prior week)", yaxis="y2",
            mode="lines",
            line=dict(color="#1f77b4", width=1.5, dash="dot"),
            hovertemplate="%{x}: %{y:,}<extra>Views (prior week)</extra>",
        ))

    # CTR — line, hidden third axis
    if not current_df.empty:
        fig.add_trace(go.Scatter(
            x=current_df["date"], y=current_df["view_rate"],
            name="CTR", yaxis="y3",
            mode="lines+markers",
            line=dict(color="#d62728", width=1.5),
            marker=dict(size=4),
            hovertemplate="%{x}: %{y:.1%}<extra>CTR</extra>",
        ))
    if not prior_df.empty:
        fig.add_trace(go.Scatter(
            x=prior_df["date"], y=prior_df["view_rate"],
            name="CTR (prior week)", yaxis="y3",
            mode="lines",
            line=dict(color="#d62728", width=1, dash="dot"),
            hovertemplate="%{x}: %{y:.1%}<extra>CTR (prior week)</extra>",
        ))

    fig.update_layout(
        title="Impressions / Views / CTR",
        xaxis=dict(domain=[0, 0.95]),
        yaxis=dict(title="Impressions", side="left", showgrid=True),
        yaxis2=dict(title="Views", side="right", overlaying="y", showgrid=False),
        yaxis3=dict(overlaying="y", side="right", showgrid=False, showticklabels=False, showline=False),
        barmode="overlay",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=0, r=0, t=40, b=0),
        hovermode="x unified",
        height=350,
    )
    return fig


def make_chart(metric: str, title: str, current_df: pd.DataFrame, prior_df: pd.DataFrame, pct: bool = False) -> go.Figure:
    fig = go.Figure()

    fmt = ".1%" if pct else None

    if not current_df.empty:
        fig.add_trace(go.Scatter(
            x=current_df["date"], y=current_df[metric],
            name="Current",
            mode="lines+markers",
            line=dict(color="#1f77b4", width=2),
            marker=dict(size=5),
            hovertemplate=f"%{{x}}: %{{y:{fmt}}}<extra></extra>" if fmt else None,
        ))

    if not prior_df.empty:
        fig.add_trace(go.Scatter(
            x=prior_df["date"], y=prior_df[metric],
            name="Prior week",
            mode="lines",
            line=dict(color="#aec7e8", width=1.5, dash="dot"),
            hovertemplate=f"%{{x}}: %{{y:{fmt}}}<extra>Prior week</extra>" if fmt else None,
        ))

    fig.update_layout(
        title=title,
        xaxis_title=None,
        yaxis_title=None,
        yaxis=dict(tickformat=".1%" if pct else None),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=0, r=0, t=40, b=0),
        hovermode="x unified",
        height=300,
    )
    return fig


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


current_df = add_derived(current_df)
prior_df = add_derived(prior_df)

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

# ── Raw data table ────────────────────────────────────────────────────────────

with st.expander("Raw data"):
    if current_df.empty:
        st.write("No rows.")
    else:
        display = current_df.copy()
        display["date"] = display["date"].dt.date
        display["ctr"] = display["ctr"].map(lambda x: f"{x:.2%}" if pd.notna(x) else "—")
        st.dataframe(display, use_container_width=True, hide_index=True)
