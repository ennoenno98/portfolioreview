"""Vanatari monthly portfolio review dashboard.

Reads the generated fact table + verdicts (run pipeline/build_portfolio.py and
pipeline/classify.py first) and renders the review: KPI band, verdict overview,
margin x volume cluster matrix, the SKU x market decision table, per-SKU
drill-down and the data-gaps panel.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

from pipeline.common import (
    FACTS_PATH,
    GENERATED_DIR,
    HISTORY_DIR,
    LAUNCH_PATH,
    RULES_YAML,
    TARGETS_JSON,
    VERDICT_ORDER,
    VERDICTS_PATH,
)

st.set_page_config(
    page_title="Vanatari · Portfolio Review", page_icon=":compass:", layout="wide"
)

# Vanatari Corporate Design 2026 — same treatment as the Nova / pricing apps.
VANATARI_CSS = """
<style>
:root {
  --vt-ink: #3c1826;
  --vt-ink-60: #8a6675;
  --vt-orange: #ff5c3e;
  --vt-beige: #fbf7f2;
  --vt-blue: #e3eef6;
  --vt-hair: #ecdfd5;
  --vt-serif: "Literata", "Charter", "Iowan Old Style", Georgia, ui-serif, serif;
}
h1, h2, h3, h4, [data-testid="stMetricValue"] {
  font-family: var(--vt-serif) !important;
  font-weight: 400 !important;
  letter-spacing: -0.02em;
  color: var(--vt-ink);
}
h1 { font-size: 2.4rem !important; }
[data-testid="stMetricValue"] { font-size: 1.8rem !important; }
[data-testid="stMetricLabel"] {
  text-transform: uppercase; letter-spacing: 0.14em;
  font-size: 0.68rem !important; color: var(--vt-ink-60) !important;
  font-weight: 500 !important;
}
[data-testid="stSidebar"] { background: var(--vt-blue); }
[data-testid="stSidebar"] * { color: var(--vt-ink); }
hr { border-color: var(--vt-hair); }
a { color: var(--vt-orange); }
</style>
"""
st.markdown(VANATARI_CSS, unsafe_allow_html=True)

# Verdict palette — categorical, validated with the dataviz six checks
# (worst all-pairs CVD ΔE 11.2 = floor band, legal because every verdict use is
# direct-labeled; amber/pink sit below 3:1 contrast, relieved by the table view).
VERDICT_COLORS = {
    "Scale": "#008300",
    "Defend": "#2a78d6",
    "Watch": "#e87ba4",
    "Incubate": "#4a3aa7",
    "Fix": "#eb6834",
    "Harvest": "#eda100",
    "Exit": "#e34948",
    "No data": "#9e9e9e",
}
# CM1 -> CM3 ordinal plum ramp (validated: monotone L, single hue)
CM_RAMP = {"cm1": "#c9a2b4", "cm2": "#8d5a75", "cm3": "#54233c"}
INK, INK60, ORANGE, HAIR = "#3c1826", "#8a6675", "#ff5c3e", "#ecdfd5"

PLOT_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=INK, size=13),
    margin=dict(l=10, r=10, t=36, b=10),
    hoverlabel=dict(bgcolor="white", font=dict(color=INK)),
)


@st.cache_data(show_spinner=False)
def load_data():
    verdicts = pd.read_csv(VERDICTS_PATH)
    facts = pd.read_csv(FACTS_PATH)
    history = pd.read_csv(GENERATED_DIR / "excel_history.csv.gz")
    launch = pd.read_csv(LAUNCH_PATH)
    with open(RULES_YAML) as fh:
        rules = yaml.safe_load(fh)
    with open(TARGETS_JSON) as fh:
        targets = json.load(fh)
    return verdicts, facts, history, launch, rules, targets


def eur(x, digits=0) -> str:
    if pd.isna(x):
        return "—"
    return f"€{x:,.{digits}f}"


def header(v: pd.DataFrame) -> None:
    left, right = st.columns([3, 1])
    with left:
        st.markdown(
            "<div style='font-family:var(--vt-serif);font-size:1.1rem;"
            f"color:{ORANGE};margin-bottom:-14px'>vanatari</div>",
            unsafe_allow_html=True,
        )
        st.title("Monthly Portfolio Review")
    with right:
        st.markdown(
            f"<div style='text-align:right;color:{INK60};padding-top:26px'>"
            f"Review month<br><span style='font-family:var(--vt-serif);"
            f"font-size:1.6rem;color:{INK}'>{v['review_month'].iloc[0]}</span></div>",
            unsafe_allow_html=True,
        )


def kpi_band(v: pd.DataFrame) -> None:
    scoped = v[v["verdict"] != "No data"]
    sales_ttm = v["net_sales_ttm"].sum()
    cm3_l6m = v["cm3_l6m"].sum()
    ns_l6m = v["net_sales_l6m"].sum()
    cm3_pct = cm3_l6m / ns_l6m * 100 if ns_l6m else float("nan")
    target = (v["cm3_target"] * v["net_sales_l6m"]).sum() / ns_l6m if ns_l6m else float("nan")
    coverage = scoped["net_sales_ttm"].sum() / sales_ttm * 100 if sales_ttm else 0

    c = st.columns(6)
    c[0].metric("Net sales · TTM", eur(sales_ttm / 1e6, 2).replace("€", "€") + "m")
    c[1].metric("CM3 · last 6m", eur(cm3_l6m / 1e6, 2) + "m")
    c[2].metric("CM3 % · last 6m", f"{cm3_pct:.1f}%",
                delta=f"{cm3_pct - target:+.1f}pp vs plan", delta_color="normal")
    c[3].metric("SKU × market", f"{len(v):,}")
    c[4].metric("Action needed", f"{int(v['verdict'].isin(['Fix', 'Exit']).sum()):,}",
                help="Fix + Exit verdicts")
    c[5].metric("Revenue classified", f"{coverage:.0f}%",
                help="Share of TTM revenue with enough data for a verdict")


def verdict_overview(v: pd.DataFrame) -> None:
    left, right = st.columns([3, 2])
    agg = (
        v.groupby("verdict")
        .agg(sales=("net_sales_ttm", "sum"), n=("sku", "count"))
        .reindex(VERDICT_ORDER)
        .dropna(how="all")
        .fillna(0)
    )
    with left:
        fig = go.Figure(
            go.Bar(
                y=agg.index[::-1],
                x=agg["sales"][::-1] / 1e6,
                orientation="h",
                marker=dict(
                    color=[VERDICT_COLORS[x] for x in agg.index[::-1]],
                    line=dict(width=2, color="#fbf7f2"),
                ),
                text=[f"€{s/1e6:,.1f}m · {int(n)} SKUs"
                      for s, n in zip(agg["sales"][::-1], agg["n"][::-1])],
                textposition="outside",
                textfont=dict(color=INK),
                hovertemplate="%{y}: €%{x:.2f}m TTM<extra></extra>",
            )
        )
        fig.update_layout(
            **PLOT_LAYOUT, height=330,
            title=dict(text="TTM revenue by verdict", font=dict(size=15)),
            xaxis=dict(title=None, showgrid=True, gridcolor=HAIR, zeroline=False,
                       range=[0, agg["sales"].max() / 1e6 * 1.35]),
            yaxis=dict(title=None, showgrid=False),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    with right:
        st.markdown("**What the verdicts mean**")
        rules = st.session_state.get("_rules", {})
        for name in VERDICT_ORDER:
            if name not in v["verdict"].values:
                continue
            desc = (rules.get("verdicts", {}).get(name) or "").strip()
            dot = (
                f"<span style='color:{VERDICT_COLORS[name]}'>&#9632;</span>"
            )
            st.markdown(
                f"{dot} **{name}** — <span style='color:{INK60};font-size:0.85rem'>"
                f"{desc.split('.')[0]}.</span>",
                unsafe_allow_html=True,
            )


def cluster_matrix(v: pd.DataFrame) -> str | None:
    st.markdown("#### Margin × volume clusters")
    st.caption(
        "Tier 1 = top third within its market, tier 3 = bottom third — the "
        "clustering carried over from Margin-Analytics. Click a cell to filter "
        "the table; click again to clear."
    )
    active = st.session_state.get("active_cluster")
    counts = v.groupby("cluster")["sku"].count()
    vol_labels = {1: "High sales", 2: "Mid sales", 3: "Low sales"}
    mar_labels = {1: "High margin", 2: "Mid margin", 3: "Low margin"}
    head = st.columns([1.2, 1, 1, 1])
    for i, vt in enumerate([1, 2, 3]):
        head[i + 1].markdown(f"<div style='text-align:center;color:{INK60}'>"
                             f"{vol_labels[vt]}</div>", unsafe_allow_html=True)
    for mt in [1, 2, 3]:
        cols = st.columns([1.2, 1, 1, 1])
        cols[0].markdown(f"<div style='padding-top:6px;color:{INK60}'>"
                         f"{mar_labels[mt]}</div>", unsafe_allow_html=True)
        for i, vt in enumerate([1, 2, 3]):
            code = f"{mt}-{vt}"
            n = int(counts.get(code, 0))
            label = f"{n}" + (" ●" if active == code else "")
            if cols[i + 1].button(label, key=f"cl_{code}", use_container_width=True):
                st.session_state["active_cluster"] = None if active == code else code
                st.rerun()
    return st.session_state.get("active_cluster")


def decision_table(v: pd.DataFrame) -> None:
    show = v.copy()
    show["market"] = show["channel"].where(show["channel"] != "Amazon", "AMZ ") + ""
    show = show[[
        "brand", "channel", "country", "sku", "product", "verdict", "reasons",
        "net_sales_ttm", "sales_trend_pct", "cm1_pct_l6m", "cm2_pct_l6m",
        "cm3_pct_l6m", "cm3_target", "launch_month", "age_months", "censored",
        "cluster", "cm_source", "manual",
    ]].sort_values("net_sales_ttm", ascending=False)
    st.dataframe(
        show,
        use_container_width=True,
        height=520,
        hide_index=True,
        column_config={
            "net_sales_ttm": st.column_config.NumberColumn("Net TTM €", format="€%.0f"),
            "sales_trend_pct": st.column_config.NumberColumn("Δ sales 3m", format="%.0f%%"),
            "cm1_pct_l6m": st.column_config.NumberColumn("CM1%", format="%.1f"),
            "cm2_pct_l6m": st.column_config.NumberColumn("CM2%", format="%.1f"),
            "cm3_pct_l6m": st.column_config.NumberColumn("CM3%", format="%.1f"),
            "cm3_target": st.column_config.NumberColumn("CM3% plan", format="%.1f"),
            "age_months": st.column_config.NumberColumn("Age (m)", format="%.0f"),
            "censored": st.column_config.CheckboxColumn("Launch < Jul 24?"),
            "manual": st.column_config.CheckboxColumn("Manual"),
            "reasons": st.column_config.TextColumn("Why", width="large"),
        },
    )
    st.download_button(
        "Download this view (CSV)",
        show.to_csv(index=False).encode(),
        file_name=f"portfolio_review_{v['review_month'].iloc[0]}.csv",
    )


def drilldown(v: pd.DataFrame, facts: pd.DataFrame, history: pd.DataFrame) -> None:
    opts = (
        v.assign(label=lambda d: d["sku"] + " · " + d["country"] + " · "
                 + d["product"].str.slice(0, 60))
        .sort_values("net_sales_ttm", ascending=False)
    )
    pick = st.selectbox("SKU × market", opts["label"], index=0)
    row = opts[opts["label"] == pick].iloc[0]
    sku, country = row["sku"], row["country"]

    st.markdown(
        f"**{row['product']}**  \n"
        f"<span style='color:{VERDICT_COLORS[row['verdict']]}'>&#9632;</span> "
        f"**{row['verdict']}** — {row['reasons']}  ·  launched {row['launch_month']}"
        + (" (censored: first month of history)" if row["censored"] else ""),
        unsafe_allow_html=True,
    )

    f = facts[(facts["sku"] == sku) & (facts["country"] == country)].sort_values("month")
    if f.empty:
        st.info("No monthly facts for this market.")
        return

    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure(
            go.Bar(
                x=f["month"], y=f["net_sales"],
                marker=dict(color="#2a78d6", line=dict(width=1, color="#fbf7f2")),
                hovertemplate="%{x}: €%{y:,.0f}<extra></extra>",
            )
        )
        fig.update_layout(
            **PLOT_LAYOUT, height=300, title=dict(text="Net sales · monthly", font=dict(size=14)),
            xaxis=dict(showgrid=False), yaxis=dict(gridcolor=HAIR, zeroline=False),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    with c2:
        fig = go.Figure()
        for key, label in [("cm1_pct", "CM1%"), ("cm2_pct", "CM2%"), ("cm3_pct", "CM3%")]:
            fig.add_trace(go.Scatter(
                x=f["month"], y=f[key], name=label, mode="lines",
                line=dict(width=2, color=CM_RAMP[key[:3]]),
                hovertemplate="%{x} " + label + ": %{y:.1f}%<extra></extra>",
            ))
        if pd.notna(row["cm3_target"]):
            fig.add_hline(y=row["cm3_target"], line=dict(color=ORANGE, dash="dot", width=2),
                          annotation_text="CM3 plan", annotation_font_color=ORANGE)
        fig.update_layout(
            **PLOT_LAYOUT, height=300,
            title=dict(text="Contribution margins · monthly", font=dict(size=14)),
            xaxis=dict(showgrid=False), yaxis=dict(gridcolor=HAIR, zeroline=True,
                                                   zerolinecolor=INK60),
            legend=dict(orientation="h", y=1.12),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    hist = history[(history["sku"] == sku)]
    if not hist.empty:
        c3, c4 = st.columns(2)
        with c3:
            spend = hist[hist["metric"] == "ad_spend"].groupby("month", as_index=False)["value"].sum()
            if not spend.empty and spend["value"].abs().sum() > 0:
                fig = go.Figure(go.Bar(
                    x=spend["month"], y=spend["value"],
                    marker=dict(color="#eb6834", line=dict(width=1, color="#fbf7f2")),
                    hovertemplate="%{x}: €%{y:,.0f}<extra></extra>",
                ))
                fig.update_layout(**PLOT_LAYOUT, height=280,
                                  title=dict(text="Ad spend · channel level (Excel history)",
                                             font=dict(size=14)),
                                  xaxis=dict(showgrid=False),
                                  yaxis=dict(gridcolor=HAIR), showlegend=False)
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        with c4:
            bsr = hist[hist["metric"] == "bsr"].groupby("month", as_index=False)["value"].mean()
            if not bsr.empty:
                fig = go.Figure(go.Scatter(
                    x=bsr["month"], y=bsr["value"], mode="lines",
                    line=dict(width=2, color="#4a3aa7"),
                    hovertemplate="%{x}: BSR %{y:,.0f}<extra></extra>",
                ))
                fig.update_layout(**PLOT_LAYOUT, height=280,
                                  title=dict(text="Amazon BSR (lower is better)", font=dict(size=14)),
                                  xaxis=dict(showgrid=False),
                                  yaxis=dict(gridcolor=HAIR, autorange="reversed"),
                                  showlegend=False)
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def data_gaps(v: pd.DataFrame, launch: pd.DataFrame) -> None:
    st.markdown("#### Where the review is flying blind")
    gaps = []
    nodata = v[v["verdict"] == "No data"]
    gaps.append((
        "No verdict possible",
        f"{len(nodata)} SKU × markets (€{nodata['net_sales_ttm'].sum():,.0f} TTM) "
        "have no margin data or under 3 months of history — includes ALL of "
        "Jasnum (no margin feed) and the wowtamins webshop (store not connected).",
    ))
    est = v[v["cm_source"].str.startswith("estimated", na=False)]
    gaps.append((
        "Shopify margins are estimates",
        f"{len(est)} webshop rows use COGS + placeholder fees from "
        "config/shopify_assumptions.yaml — replace with real payment/3PL numbers. "
        "Webshop CM3 = CM2 (no per-SKU marketing attribution yet).",
    ))
    cens = launch[launch["censored"]]
    gaps.append((
        "Launch dates are proxies",
        f"{len(cens)} of {len(launch)} SKUs already sold in Jul 2024 (start of the "
        "history) so their true launch is unknown; the rest use first-sale month. "
        "Correct any SKU in data/overrides/launch_dates_overrides.csv.",
    ))
    gaps.append((
        "wowtamins Amazon has no PPC data",
        "The wowtamins Nova account books zero ad spend, so CM2 = CM3 there and "
        "ROAS/TACOS checks are impossible. June 2025 is missing entirely in Nova.",
    ))
    gaps.append((
        "wowtamins DE supply collapse",
        "Account-wide sales fell ~97% May → June 2026 (stock-out pattern: storage "
        "fees on zero sales). Verdicts for these SKUs read 'supply stalled' — "
        "resolve availability before acting on margins.",
    ))
    gaps.append((
        "CM history depth",
        "Amazon CM1/2/3 exists from Jul 2025 (Nova margin export). Anything older "
        "is sales-only from the Excel workbook — fine for trends, blind on margin.",
    ))
    for title, body in gaps:
        st.markdown(f"**:warning: {title}**  \n{body}")
        st.markdown("---")


def main() -> None:
    if not VERDICTS_PATH.exists():
        st.error("Run the pipeline first: `python pipeline/build_portfolio.py && "
                 "python pipeline/classify.py`")
        st.stop()
    verdicts, facts, history, launch, rules, _targets = load_data()
    st.session_state["_rules"] = rules

    header(verdicts)

    with st.sidebar:
        st.markdown("**Filters**")
        brands = sorted(verdicts["brand"].dropna().unique())
        channels = sorted(verdicts["channel"].dropna().unique())
        countries = sorted(verdicts["country"].dropna().unique())
        f_brand = st.multiselect("Brand", brands, default=brands)
        f_channel = st.multiselect("Channel", channels, default=channels)
        f_country = st.multiselect("Market", countries, default=countries)
        f_verdict = st.multiselect("Verdict", VERDICT_ORDER, default=VERDICT_ORDER)
        st.markdown("---")
        st.caption(
            "Rules live in `config/rules.yaml`; manual calls in "
            "`data/overrides/verdict_overrides.csv`. Rerun "
            "`pipeline/classify.py` after editing."
        )

    v = verdicts[
        verdicts["brand"].isin(f_brand)
        & verdicts["channel"].isin(f_channel)
        & verdicts["country"].isin(f_country)
        & verdicts["verdict"].isin(f_verdict)
    ]
    if v.empty:
        st.warning("Nothing matches the current filters.")
        st.stop()

    kpi_band(v)
    st.markdown("---")

    tab_review, tab_drill, tab_gaps = st.tabs(
        [":compass: Review", ":mag: Drill-down", ":warning: Data gaps"]
    )
    with tab_review:
        verdict_overview(v)
        active = cluster_matrix(v)
        table = v[v["cluster"] == active] if active else v
        decision_table(table)
    with tab_drill:
        drilldown(v, facts, history)
    with tab_gaps:
        data_gaps(verdicts, launch)


main()
