"""Vanatari monthly portfolio review dashboard.

Reads the generated fact table + verdicts (run pipeline/build_portfolio.py and
pipeline/classify.py first) and renders the review. The headline unit is
SKU x channel (Amazon vs Shopify, countries collapsed); each row also shows a
compact per-country verdict summary. The full per-country assessment lives in
its own tab, alongside a margin x volume quadrant map, per-SKU drill-down and
the data-gaps panel.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml
from plotly.subplots import make_subplots

from pipeline.common import (
    COUNTRY_VERDICTS_PATH,
    FACTS_PATH,
    GENERATED_DIR,
    HISTORY_DIR,
    LAUNCH_PATH,
    NO_DATA,
    OOS_PATH,
    RULES_YAML,
    TARGETS_JSON,
    VERDICT_ORDER,
    VERDICTS_PATH,
)

VERDICT_FILTER_OPTS = VERDICT_ORDER + [NO_DATA]

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
.vt-chips span {
  display:inline-block; padding:1px 7px; margin:2px 3px 2px 0; border-radius:10px;
  font-size:0.72rem; color:#fff; white-space:nowrap;
}
</style>
"""
st.markdown(VANATARI_CSS, unsafe_allow_html=True)

# Verdict palette — categorical, validated with the dataviz six checks
# (worst all-pairs CVD ΔE 11.2 = floor band, legal because every verdict use is
# direct-labeled; amber/pink sit below 3:1 contrast, relieved by the table view).
VERDICT_COLORS = {
    "Scale": "#008300",
    "Defend": "#2a78d6",
    "Fix": "#eb6834",
    "Harvest": "#eda100",
    "Exit": "#e34948",
    "No data": "#9e9e9e",
}
NEW_TAG_COLOR = "#4a3aa7"  # "New" (incubating) badge — plum
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
    country = pd.read_csv(COUNTRY_VERDICTS_PATH)
    facts = pd.read_csv(FACTS_PATH)
    history = pd.read_csv(GENERATED_DIR / "excel_history.csv.gz")
    launch = pd.read_csv(LAUNCH_PATH)
    oos = pd.read_csv(OOS_PATH) if OOS_PATH.exists() else pd.DataFrame()
    with open(RULES_YAML) as fh:
        rules = yaml.safe_load(fh)
    with open(TARGETS_JSON) as fh:
        targets = json.load(fh)
    return verdicts, country, facts, history, launch, oos, rules, targets


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
    tmask = v["cm3_target"].notna() & (v["net_sales_l6m"] > 0)
    tw = v.loc[tmask, "net_sales_l6m"]
    target = (v.loc[tmask, "cm3_target"] * tw).sum() / tw.sum() if tw.sum() else float("nan")
    coverage = scoped["net_sales_ttm"].sum() / sales_ttm * 100 if sales_ttm else 0

    c = st.columns(6)
    c[0].metric("Net sales · TTM", eur(sales_ttm / 1e6, 2) + "m")
    c[1].metric("CM3 · last 6m", eur(cm3_l6m / 1e6, 2) + "m")
    c[2].metric("CM3 % · last 6m", f"{cm3_pct:.1f}%",
                delta=f"{cm3_pct - target:+.1f}pp vs plan", delta_color="normal")
    c[3].metric("SKU × channel", f"{len(v):,}")
    c[4].metric("Action needed", f"{int(v['verdict'].isin(['Fix', 'Exit']).sum()):,}",
                help="Fix + Exit verdicts at channel level")
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
            dot = f"<span style='color:{VERDICT_COLORS[name]}'>&#9632;</span>"
            st.markdown(
                f"{dot} **{name}** — <span style='color:{INK60};font-size:0.85rem'>"
                f"{desc.split('.')[0]}.</span>",
                unsafe_allow_html=True,
            )
        n_new = int(v["incubating"].fillna(False).sum())
        n_nodata = int((v["verdict"] == NO_DATA).sum())
        st.markdown(
            f"<span style='color:{NEW_TAG_COLOR}'>&#9632;</span> **New** tag "
            f"<span style='color:{INK60};font-size:0.85rem'>— {n_new} launches inside "
            "the incubation window, shielded from Fix/Exit.</span><br>"
            f"<span style='color:{VERDICT_COLORS[NO_DATA]}'>&#9632;</span> **No data** "
            f"<span style='color:{INK60};font-size:0.85rem'>— {n_nodata} without a "
            "verdict (see Data gaps).</span>",
            unsafe_allow_html=True,
        )


def cluster_scatter(v: pd.DataFrame, unit_label: str) -> None:
    """Margin × volume quadrant map: X = net sales (volume), Y = CM3% (margin),
    bubble = one SKU coloured by its verdict (the 'category'). Dashed lines at
    the medians split the four strategic quadrants."""
    d = v[v["net_sales_ttm"].notna() & (v["net_sales_ttm"] > 0)
          & v["cm3_pct_l6m"].notna()].copy()
    st.markdown("#### Margin × volume map")
    st.caption(
        "Each bubble is one " + unit_label + ", placed by TTM net sales (volume) "
        "and last-6m CM3 % (margin), sized by sales and coloured by verdict. "
        "Dashed lines are the medians; quadrants read like the categories below."
    )
    if len(d) < 4:
        st.info("Not enough classified rows to draw the map for this filter.")
        return

    xmed = d["net_sales_ttm"].median()
    ymed = d["cm3_pct_l6m"].median()
    xmin, xmax = d["net_sales_ttm"].min() * 0.6, d["net_sales_ttm"].max() * 1.6
    ypad = (d["cm3_pct_l6m"].max() - d["cm3_pct_l6m"].min()) * 0.12 + 1
    ymin, ymax = d["cm3_pct_l6m"].min() - ypad, d["cm3_pct_l6m"].max() + ypad

    fig = go.Figure()
    # quadrant tints (log-x aware: use paper-relative shapes around the medians)
    quad = [
        (xmin, xmed, ymed, ymax, "rgba(0,131,0,0.05)"),      # low vol / high margin
        (xmed, xmax, ymed, ymax, "rgba(42,120,214,0.06)"),   # high vol / high margin
        (xmin, xmed, ymin, ymed, "rgba(158,158,158,0.06)"),  # low vol / low margin
        (xmed, xmax, ymin, ymed, "rgba(235,104,52,0.06)"),   # high vol / low margin
    ]
    for x0, x1, y0, y1, col in quad:
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                      fillcolor=col, line=dict(width=0), layer="below")
    labels = [
        (xmin, ymax, "Grow / boost", "left", "top", "#4a3aa7"),
        (xmax, ymax, "Winners", "right", "top", "#2a78d6"),
        (xmin, ymin, "Low priority", "left", "bottom", INK60),
        (xmax, ymin, "Fix margin", "right", "bottom", "#eb6834"),
    ]
    for x, y, txt, xa, ya, col in labels:
        fig.add_annotation(x=np.log10(x), y=y, text=txt, showarrow=False,
                           xanchor=xa, yanchor=ya, font=dict(color=col, size=12))
    fig.add_vline(x=np.log10(xmed), line=dict(color=INK60, dash="dash", width=1))
    fig.add_hline(y=ymed, line=dict(color=INK60, dash="dash", width=1))

    sizeref = 2.0 * d["net_sales_ttm"].max() / (42.0 ** 2)
    for verdict in VERDICT_ORDER:
        sub = d[d["verdict"] == verdict]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["net_sales_ttm"], y=sub["cm3_pct_l6m"], mode="markers",
            name=verdict,
            marker=dict(size=sub["net_sales_ttm"], sizemode="area", sizeref=sizeref,
                        sizemin=5, color=VERDICT_COLORS[verdict],
                        line=dict(width=1, color="#fbf7f2"), opacity=0.85),
            customdata=np.stack([sub["sku"], sub["product"].astype(str).str.slice(0, 48),
                                 sub["net_sales_ttm"]], axis=-1),
            hovertemplate=("%{customdata[0]} — %{customdata[1]}<br>"
                           "CM3%: %{y:.1f} · TTM €%{customdata[2]:,.0f}<extra>"
                           + verdict + "</extra>"),
        ))
    fig.update_layout(
        **PLOT_LAYOUT, height=520,
        title=dict(text=None),
        xaxis=dict(title="Net sales · TTM (€, log)", type="log", showgrid=False,
                   range=[np.log10(xmin), np.log10(xmax)]),
        yaxis=dict(title="CM3 % · last 6m", gridcolor=HAIR, zeroline=True,
                   zerolinecolor=INK60, range=[ymin, ymax]),
        legend=dict(orientation="h", y=-0.16, title=None),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _chips_html(country_verdicts: str) -> str:
    if not isinstance(country_verdicts, str) or not country_verdicts:
        return ""
    out = []
    for pair in country_verdicts.split("|"):
        if ":" not in pair:
            continue
        c, verd = pair.split(":", 1)
        out.append(f"<span style='background:{VERDICT_COLORS.get(verd, '#9e9e9e')}'>"
                    f"{c} {verd}</span>")
    return "<div class='vt-chips'>" + "".join(out) + "</div>"


def _diverging(series: pd.Series, ref, span: float, pos="0,131,0", neg="227,73,72"):
    """CSS backgrounds: green when value >= ref, red when below; alpha by distance.
    `ref` is a scalar or an aligned Series (e.g. per-row CM3 target)."""
    ref = ref if not hasattr(ref, "reindex") else ref.reindex(series.index)
    out = []
    for i, v in series.items():
        r = ref if not hasattr(ref, "reindex") else ref.get(i)
        if pd.isna(v) or (hasattr(ref, "reindex") and pd.isna(r)):
            out.append(""); continue
        diff = v - (r if not hasattr(ref, "reindex") else r)
        a = min(1.0, abs(diff) / span) * 0.45
        out.append(f"background-color: rgba({pos if diff >= 0 else neg},{a:.2f})")
    return out


def channel_table(v: pd.DataFrame) -> None:
    verds = [x for x in VERDICT_ORDER + [NO_DATA] if x in v["verdict"].unique()]
    counts = v["verdict"].value_counts()
    labels = ["All"] + [f"{x} ({int(counts.get(x, 0))})" for x in verds]
    pick = st.radio("Filter by verdict", labels, horizontal=True, key="ch_verdict_filter")
    if pick != "All":
        chosen = pick.rsplit(" (", 1)[0]
        v = v[v["verdict"] == chosen]

    show = v[[
        "brand", "channel", "sku", "product", "verdict", "incubating", "reasons",
        "net_sales_ttm", "sales_trend_pct", "cm1_pct_l6m", "cm2_pct_l6m",
        "cm3_pct_l6m", "cm3_target", "bsr", "n_markets", "n_markets_action",
        "country_verdicts", "cluster", "cm_source", "manual",
    ]].sort_values("net_sales_ttm", ascending=False)
    # colour code: CM3 vs its plan target (green above / red below), CM1 & CM2 by
    # level, revenue growth by sign.
    sty = show.style
    sty = sty.apply(lambda s: _diverging(s, show["cm3_target"], 8.0), subset=["cm3_pct_l6m"])
    sty = sty.apply(lambda s: _diverging(s, 55.0, 25.0), subset=["cm1_pct_l6m"])
    sty = sty.apply(lambda s: _diverging(s, 20.0, 20.0), subset=["cm2_pct_l6m"])
    sty = sty.apply(lambda s: _diverging(s, 0.0, 40.0), subset=["sales_trend_pct"])
    st.dataframe(
        sty, use_container_width=True, height=480, hide_index=True,
        column_config={
            "incubating": st.column_config.CheckboxColumn("New"),
            "net_sales_ttm": st.column_config.NumberColumn("Net TTM €", format="€%.0f"),
            "sales_trend_pct": st.column_config.NumberColumn("Δ sales 3m", format="%.0f%%"),
            "cm1_pct_l6m": st.column_config.NumberColumn("CM1%", format="%.1f"),
            "cm2_pct_l6m": st.column_config.NumberColumn("CM2%", format="%.1f"),
            "cm3_pct_l6m": st.column_config.NumberColumn("CM3%", format="%.1f"),
            "cm3_target": st.column_config.NumberColumn("CM3% plan", format="%.1f"),
            "bsr": st.column_config.NumberColumn("BSR", format="%d", help="Latest Amazon Best Seller Rank (avg across marketplaces); lower is better"),
            "n_markets": st.column_config.NumberColumn("# mkts", format="%d"),
            "n_markets_action": st.column_config.NumberColumn("# Fix/Exit", format="%d"),
            "country_verdicts": st.column_config.TextColumn("Per-country verdicts", width="large"),
            "reasons": st.column_config.TextColumn("Why (channel)", width="large"),
            "manual": st.column_config.CheckboxColumn("Manual"),
        },
    )
    st.download_button(
        "Download this view (CSV)",
        show.to_csv(index=False).encode(),
        file_name=f"portfolio_review_channel_{v['review_month'].iloc[0]}.csv",
    )


def country_table(cv: pd.DataFrame) -> None:
    show = cv[[
        "brand", "channel", "country", "sku", "product", "verdict", "incubating",
        "reasons", "net_sales_ttm", "sales_trend_pct", "cm1_pct_l6m", "cm2_pct_l6m",
        "cm3_pct_l6m", "cm3_target", "bsr", "launch_month", "age_months", "censored",
        "cluster", "cm_source", "manual",
    ]].sort_values("net_sales_ttm", ascending=False)
    st.dataframe(
        show, use_container_width=True, height=520, hide_index=True,
        column_config={
            "incubating": st.column_config.CheckboxColumn("New"),
            "bsr": st.column_config.NumberColumn("BSR", format="%d", help="Latest Amazon Best Seller Rank; lower is better"),
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
        "Download by-country view (CSV)",
        show.to_csv(index=False).encode(),
        file_name=f"portfolio_review_country_{cv['review_month'].iloc[0]}.csv",
    )


def drilldown(cv: pd.DataFrame, facts: pd.DataFrame, history: pd.DataFrame) -> None:
    # separate Market and SKU pickers (Market first, then SKU within it)
    sel = st.columns([1, 3])
    markets = sorted(cv["country"].dropna().unique())
    market = sel[0].selectbox("Market", markets, index=0)
    sub = cv[cv["country"] == market].sort_values("net_sales_ttm", ascending=False)
    if sub.empty:
        st.info("No SKUs for this market under the current filters.")
        return
    sub = sub.assign(label=lambda d: d["sku"] + " · " + d["product"].astype(str).str.slice(0, 70))
    pick = sel[1].selectbox("SKU", sub["label"], index=0)
    row = sub[sub["label"] == pick].iloc[0]
    sku, country = row["sku"], market

    new_badge = ""
    if bool(row.get("incubating", False)):
        new_badge = (f" <span style='background:{NEW_TAG_COLOR};color:#fff;"
                     "padding:1px 7px;border-radius:10px;font-size:0.72rem'>New</span>")
    st.markdown(
        f"**{row['product']}**  \n"
        f"<span style='color:{VERDICT_COLORS[row['verdict']]}'>&#9632;</span> "
        f"**{row['verdict']}**{new_badge} — {row['reasons']}  ·  launched {row['launch_month']}"
        + (" (censored: first month of history)" if row["censored"] else ""),
        unsafe_allow_html=True,
    )

    f = facts[(facts["sku"] == sku) & (facts["country"] == country)].sort_values("month")
    if f.empty:
        st.info("No monthly facts for this market.")
        return

    # one shared month axis for every chart below, so a given month lines up
    # vertically across net sales / CM / PPC / ROAS / BSR.
    bsr = history[(history["sku"] == sku) & (history["metric"] == "bsr")]
    bsr = bsr.groupby("month", as_index=False)["value"].mean()
    months = sorted(set(f["month"]) | set(bsr["month"]))

    def xc(**extra):
        return dict(showgrid=False, type="category", categoryorder="array",
                    categoryarray=months, **extra)

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
            xaxis=xc(), yaxis=dict(gridcolor=HAIR, zeroline=False),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    with c2:
        # small multiples: CM1/CM2/CM3 each on its own scale (never a shared axis
        # that squashes CM3 under CM1). Shared x, three stacked panels.
        fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.07,
                            subplot_titles=("CM1 %", "CM2 %", "CM3 %"))
        for i, key in enumerate(["cm1_pct", "cm2_pct", "cm3_pct"], start=1):
            fig.add_trace(go.Scatter(
                x=f["month"], y=f[key], mode="lines",
                line=dict(width=2, color=CM_RAMP[key[:3]]),
                hovertemplate="%{x}: %{y:.1f}%<extra></extra>", showlegend=False,
            ), row=i, col=1)
        if pd.notna(row["cm3_target"]):
            fig.add_hline(y=row["cm3_target"], line=dict(color=ORANGE, dash="dot", width=2),
                          annotation_text="plan", annotation_font_color=ORANGE, row=3, col=1)
        fig.update_layout(**PLOT_LAYOUT, height=320,
                          title=dict(text="Contribution margins · monthly (own scale each)",
                                     font=dict(size=14)))
        fig.update_xaxes(showgrid=False, type="category", categoryorder="array",
                         categoryarray=months)
        fig.update_yaxes(gridcolor=HAIR, zeroline=True, zerolinecolor=INK60)
        for ann in fig["layout"]["annotations"][:3]:
            ann["font"] = dict(size=11, color=INK60)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # PPC: ad spend (bars) + true ROAS = PPC sales ÷ PPC spend (ad-attributed
    # sales, Amazon ROAS column). Webshop has spend but no ad-attributed sales yet.
    f = f.assign(ad_spend=pd.to_numeric(f["ad_spend"], errors="coerce"),
                 ppc_sales=pd.to_numeric(f.get("ppc_sales"), errors="coerce"))
    ppc = f[f["ad_spend"].notna() & (f["ad_spend"] > 0)].copy()
    c3, c4 = st.columns(2)
    if not ppc.empty:
        with c3:
            fig = go.Figure(go.Bar(
                x=ppc["month"], y=ppc["ad_spend"],
                marker=dict(color="#eb6834", line=dict(width=1, color="#fbf7f2")),
                hovertemplate="%{x}: €%{y:,.0f} ad spend<extra></extra>",
            ))
            fig.update_layout(**PLOT_LAYOUT, height=280,
                              title=dict(text="PPC spend · monthly", font=dict(size=14)),
                              xaxis=xc(), yaxis=dict(gridcolor=HAIR),
                              showlegend=False)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        with c4:
            roas = ppc[ppc["ppc_sales"].notna() & (ppc["ppc_sales"] > 0)].copy()
            if roas.empty:
                st.caption("PPC ROAS needs ad-attributed sales — available for Amazon "
                           "(from Feb 2026); not connected for the webshop yet.")
            else:
                roas["roas"] = roas["ppc_sales"] / roas["ad_spend"]
                fig = go.Figure(go.Bar(
                    x=roas["month"], y=roas["roas"],
                    marker=dict(color="#008300", line=dict(width=1, color="#fbf7f2")),
                    text=[f"{r:.1f}×" for r in roas["roas"]], textposition="outside",
                    textfont=dict(color=INK, size=10),
                    hovertemplate="%{x}: ROAS %{y:.2f}× (PPC sales ÷ PPC spend)<extra></extra>",
                ))
                fig.add_hline(y=1, line=dict(color=INK60, dash="dot", width=1),
                              annotation_text="break-even", annotation_font_color=INK60)
                fig.update_layout(**PLOT_LAYOUT, height=280,
                                  title=dict(text="ROAS · PPC sales ÷ PPC spend",
                                             font=dict(size=14)),
                                  xaxis=xc(),
                                  yaxis=dict(gridcolor=HAIR, rangemode="tozero"),
                                  showlegend=False)
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    else:
        c3.caption("No PPC spend recorded for this market/period.")

    # Amazon BSR as a colour-coded heatmap over time (lower rank = better = greener)
    if not bsr.empty:
        bmap = {m: v for m, v in zip(bsr["month"], bsr["value"])}
        z = [[bmap.get(m, None) for m in months]]
        txt = [[f"{bmap[m]:,.0f}" if m in bmap else "" for m in months]]
        fig = go.Figure(go.Heatmap(
            z=z, x=months, y=["BSR"], text=txt, texttemplate="%{text}",
            textfont=dict(size=10, color=INK),
            colorscale="Greens_r", reversescale=False,
            hovertemplate="%{x}: BSR %{z:,.0f}<extra></extra>",
            colorbar=dict(title="BSR", tickformat=",.0f"),
            xgap=2, ygap=2,
        ))
        fig.update_layout(**PLOT_LAYOUT, height=170,
                          title=dict(text="Amazon BSR · monthly (darker = better rank)",
                                     font=dict(size=14)),
                          xaxis=xc(), yaxis=dict(showgrid=False))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def oos_view(oos: pd.DataFrame, verdicts: pd.DataFrame) -> None:
    if oos.empty:
        st.info("No OOS data — run `python pipeline/oos.py` after the build.")
        return
    o = oos.merge(verdicts[["brand", "channel", "sku", "verdict"]],
                  on=["brand", "channel", "sku"], how="left")

    st.markdown("#### Out of stock — and where marketing is still running")
    st.caption(
        "Amazon FBA stock (Novadata snapshot, "
        f"{o['snapshot'].dropna().iloc[0] if o['snapshot'].notna().any() else 'latest'}). "
        "‘Out of stock’ = zero sellable units in the pool. Amazon ad spend is "
        "reported from Feb 2026; the webshop has no live stock feed."
    )

    waste = o[o["advertising_while_out_now"]].sort_values("recent_ad_spend", ascending=False)
    low = o[o["advertising_while_low"]].sort_values("recent_ad_spend", ascending=False)
    c = st.columns(4)
    c[0].metric("Out of stock now", f"{int(o['out_now'].sum())}",
                help="Amazon SKUs with zero sellable units in the pool")
    c[1].metric("OOS & still advertised", f"{len(waste)}",
                help="Out of stock but still drew ad spend last month — wasted")
    c[2].metric("Wasted ad € · last month", eur(waste["recent_ad_spend"].sum()),
                help="Last-month ad spend on products that are now out of stock")
    c[3].metric("Low stock & advertised", f"{len(low)}",
                help="Selling but running low — a restock signal, not wasted spend")

    st.markdown("##### :rotating_light: Out of stock but still advertised — stop the spend")
    cols = ["sku", "product", "channel", "verdict", "fba_available", "fba_incoming",
            "sales_velocity", "recent_ad_spend", "recent_units"]
    if waste.empty:
        st.success("No fully out-of-stock product is currently being advertised.")
    else:
        st.dataframe(
            waste[cols], use_container_width=True, height=300, hide_index=True,
            column_config={
                "fba_available": st.column_config.NumberColumn("Avail", format="%.0f"),
                "fba_incoming": st.column_config.NumberColumn("Incoming", format="%.0f"),
                "sales_velocity": st.column_config.NumberColumn("Units/day", format="%.1f"),
                "recent_ad_spend": st.column_config.NumberColumn("Ad € last m", format="€%.0f"),
                "recent_units": st.column_config.NumberColumn("Units last m", format="%.0f"),
                "product": st.column_config.TextColumn("Product", width="medium"),
            },
        )

    st.markdown("##### Selling but running low while advertised — restock, don't cut ads")
    if low.empty:
        st.info("No low-stock products with active ad spend.")
    else:
        st.dataframe(
            low[["sku", "product", "channel", "verdict", "days_of_inventory",
                 "fba_available", "fba_incoming", "sales_velocity", "recent_ad_spend"]],
            use_container_width=True, height=300, hide_index=True,
            column_config={
                "days_of_inventory": st.column_config.NumberColumn("Days stock", format="%.0f"),
                "fba_available": st.column_config.NumberColumn("Avail", format="%.0f"),
                "fba_incoming": st.column_config.NumberColumn("Incoming", format="%.0f"),
                "sales_velocity": st.column_config.NumberColumn("Units/day", format="%.1f"),
                "recent_ad_spend": st.column_config.NumberColumn("Ad € last m", format="€%.0f"),
                "product": st.column_config.TextColumn("Product", width="medium"),
            },
        )
    st.download_button(
        "Download OOS view (CSV)", o.to_csv(index=False).encode(),
        file_name=f"oos_{o['review_month'].iloc[0]}.csv",
    )

    wasted = o[o["wasted_ad_spend_l6m"] > 0].sort_values("wasted_ad_spend_l6m", ascending=False)
    st.markdown("##### Confirmed wasted spend during past stock-outs (last 6 months)")
    st.caption(
        "Demand-gap proxy: months where units collapsed to near-zero against the "
        "product's own baseline while it still booked ad spend. Conservative — a "
        f"floor, not a ceiling. Total €{o['wasted_ad_spend_l6m'].sum():,.0f}."
    )
    if wasted.empty:
        st.info("No demand-gap stock-out months with ad spend in the window.")
    else:
        st.dataframe(
            wasted[["sku", "product", "channel", "verdict", "oos_months_l6m",
                    "wasted_ad_spend_l6m"]],
            use_container_width=True, height=280, hide_index=True,
            column_config={
                "oos_months_l6m": st.column_config.NumberColumn("OOS months", format="%d"),
                "wasted_ad_spend_l6m": st.column_config.NumberColumn("Wasted €", format="€%.0f"),
                "product": st.column_config.TextColumn("Product", width="medium"),
            },
        )

    # ---- slow movers / dead stock (opposite problem: capital tied in stock) ----
    slow = o[o.get("slow_mover", False) == True].sort_values(  # noqa: E712
        "tied_capital", ascending=False, na_position="last")
    st.markdown("---")
    st.markdown("##### :snail: Slow movers & dead stock — capital sitting still")
    st.caption(
        "Amazon SKUs with sellable stock on hand but little or no movement "
        f"(no sales in 30 days = dead; ≥{int(180/30)} months of supply = slow). "
        "‘Capital tied’ values the on-hand units at COGS. Recommendation per row."
    )
    if slow.empty:
        st.success("No slow movers or dead stock flagged in the current snapshot.")
    else:
        s = st.columns(3)
        s[0].metric("Slow movers", f"{len(slow)}",
                    help="Includes dead stock (no sales in 30 days)")
        s[1].metric("Dead stock", f"{int(o['dead_stock'].sum())}",
                    help="Stock on hand with zero sales in the last 30 days")
        s[2].metric("Capital tied up", eur(slow["tied_capital"].sum()),
                    help="On-hand units × COGS across slow movers")
        st.dataframe(
            slow[["sku", "product", "channel", "verdict", "fba_available", "units_30d",
                  "days_of_inventory", "tied_capital", "stock_reco"]],
            use_container_width=True, height=320, hide_index=True,
            column_config={
                "fba_available": st.column_config.NumberColumn("Avail units", format="%.0f"),
                "units_30d": st.column_config.NumberColumn("Units 30d", format="%.0f"),
                "days_of_inventory": st.column_config.NumberColumn("Days stock", format="%.0f"),
                "tied_capital": st.column_config.NumberColumn("Capital tied €", format="€%.0f"),
                "stock_reco": st.column_config.TextColumn("Recommendation", width="large"),
                "product": st.column_config.TextColumn("Product", width="medium"),
            },
        )


def data_gaps(v: pd.DataFrame, launch: pd.DataFrame) -> None:
    st.markdown("#### Where the review is flying blind")
    gaps = []
    nodata = v[v["verdict"] == "No data"]
    gaps.append((
        "No verdict possible",
        f"{len(nodata)} SKU × channels (€{nodata['net_sales_ttm'].sum():,.0f} TTM) "
        "have no margin data or under 3 months of history — includes ALL of "
        "Jasnum (no margin feed) and the wowtamins webshop (store not connected).",
    ))
    est = v[v["cm_source"].astype(str).str.startswith("estimated")]
    gaps.append((
        "Shopify margins are estimates",
        f"{len(est)} webshop rows use COGS + placeholder fees from "
        "config/shopify_assumptions.yaml — replace with real payment/3PL numbers. "
        "Webshop CM3 subtracts actual Google Ads spend (Windsor.ai, from Oct 2025) "
        "only where that spend exists; Meta and other web channels are not connected yet.",
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
        "fees on zero sales). See docs/wowtamins_stockout_2026-07.md — resolve "
        "availability before acting on margins.",
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
    if not VERDICTS_PATH.exists() or not COUNTRY_VERDICTS_PATH.exists():
        st.error("Run the pipeline first: `python pipeline/build_portfolio.py && "
                 "python pipeline/classify.py`")
        st.stop()
    verdicts, country, facts, history, launch, oos, rules, _targets = load_data()
    st.session_state["_rules"] = rules

    header(verdicts)

    with st.sidebar:
        st.markdown("**Filters**")
        brands = sorted(verdicts["brand"].dropna().unique())
        channels = sorted(verdicts["channel"].dropna().unique())
        countries = sorted(country["country"].dropna().unique())
        f_brand = st.multiselect("Brand", brands, default=brands)
        f_channel = st.multiselect("Channel", channels, default=channels)
        f_verdict = st.multiselect("Verdict", VERDICT_FILTER_OPTS, default=VERDICT_FILTER_OPTS)
        f_new = st.checkbox("New (incubating) only", value=False)
        st.markdown("**By-country tab only**")
        f_country = st.multiselect("Market", countries, default=countries)
        st.markdown("---")
        st.caption(
            "Headline unit is SKU × channel; the Market filter scopes the "
            "By-country tab. Rules live in `config/rules.yaml`; manual calls in "
            "`data/overrides/verdict_overrides.csv` (country `*` = channel-wide)."
        )

    # channel-grain view (Review tab) — Brand / Channel / Verdict
    v = verdicts[
        verdicts["brand"].isin(f_brand)
        & verdicts["channel"].isin(f_channel)
        & verdicts["verdict"].isin(f_verdict)
    ]
    # country-grain view (By-country tab + drill-down) — adds Market
    cv = country[
        country["brand"].isin(f_brand)
        & country["channel"].isin(f_channel)
        & country["country"].isin(f_country)
        & country["verdict"].isin(f_verdict)
    ]
    if f_new:
        v = v[v["incubating"].fillna(False)]
        cv = cv[cv["incubating"].fillna(False)]
    if v.empty:
        st.warning("Nothing matches the current filters.")
        st.stop()

    kpi_band(v)
    st.markdown("---")

    tab_review, tab_country, tab_oos, tab_drill, tab_gaps = st.tabs(
        [":compass: Review (channel)", ":globe_with_meridians: By country",
         ":package: Out of stock", ":mag: Drill-down", ":warning: Data gaps"]
    )
    with tab_review:
        verdict_overview(v)
        unit = "SKU × " + (" / ".join(sorted(v["channel"].unique())) or "channel")
        cluster_scatter(v, unit)
        st.markdown("#### Decision table — SKU × channel")
        st.caption("Each row is a channel-level verdict; the *Per-country verdicts* "
                   "column shows how the markets split without the full detail.")
        channel_table(v)
    with tab_country:
        st.markdown("#### Per-country assessment")
        st.caption("Every SKU × marketplace classified on its own AP26 country target. "
                   "Use the Market filter in the sidebar to focus.")
        if cv.empty:
            st.info("No rows for the current Market/brand/verdict filter.")
        else:
            country_table(cv)
    with tab_oos:
        oos_view(oos, verdicts)
    with tab_drill:
        drilldown(cv if not cv.empty else country, facts, history)
    with tab_gaps:
        data_gaps(verdicts, launch)


main()
