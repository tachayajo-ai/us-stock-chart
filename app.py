"""US Stock & ETF Chart — Streamlit + yfinance
รัน:  streamlit run app.py
"""
import base64
import importlib
import json
import sys
from datetime import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st
import yfinance as yf

# Streamlit Cloud อัปเดต app.py แต่ไม่โหลด buffett.py ใหม่ -> บังคับโหลดใหม่ กัน ImportError หลังอัปเดตไฟล์
for _mod in ("buffett", "guides", "company", "alerts", "portfolio"):
    if _mod in sys.modules:
        importlib.reload(sys.modules[_mod])
import guides  # noqa: E402
from company import earnings_links, fetch_earnings, fetch_news, translate, translate_lines  # noqa: E402
import alerts as alerts_mod  # noqa: E402
import portfolio as pf  # noqa: E402
from buffett import (BASIS_OE, buffett_checks, build_buffett_indicator, current_price, dcf_options, default_growth,
                     implied_growth,
                     fetch_fundamentals, fmt_money, is_company, price_to_book, share_count,
                     valuation_rows)

WATCHLIST_FILE = Path(__file__).with_name("watchlist.json")
DEFAULT_WATCHLIST = ["SPY", "QQQ", "VOO", "AAPL", "MSFT", "NVDA", "TSLA", "SCHD", "BRK-B"]
ET = "America/New_York"

# timeframe -> (period, interval, include pre/after-market)
TIMEFRAMES = {
    "1D": ("1d", "2m", True),
    "5D": ("5d", "15m", True),
    "1M": ("1mo", "60m", True),
    "6M": ("6mo", "1d", False),
    "YTD": ("ytd", "1d", False),
    "1Y": ("1y", "1d", False),
    "5Y": ("5y", "1d", False),
    "10Y": ("10y", "1wk", False),
    "Max": ("max", "1mo", False),
}

st.set_page_config(page_title="US Stock Chart", page_icon="📈", layout="wide")


# ---------------- watchlist ----------------
# บนคลาวด์: ถ้าตั้ง Secrets GITHUB_TOKEN + GITHUB_REPO ไว้ จะเก็บ watchlist.json ลง GitHub repo (ถาวร)
# บนคอม: เก็บเป็นไฟล์ watchlist.json ในโฟลเดอร์เดียวกับแอพ
def _github_cfg():
    try:
        token, repo = st.secrets["GITHUB_TOKEN"], st.secrets["GITHUB_REPO"]
        return token, repo
    except Exception:
        return None


def _gh_request(method: str, body: dict | None = None, path: str = "watchlist.json"):
    token, repo = _github_cfg()
    return requests.request(
        method,
        f"https://api.github.com/repos/{repo}/contents/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json=body, timeout=15,
    )


def read_repo_json(path: str):
    """อ่านไฟล์ JSON จาก GitHub repo (บนคลาวด์) หรือจากโฟลเดอร์แอพ (บนคอม) · None ถ้าไม่มี"""
    if _github_cfg():
        try:
            r = _gh_request("GET", path=path)
            if r.status_code == 200:
                st.session_state.setdefault("gh_sha", {})[path] = r.json()["sha"]
                return json.loads(base64.b64decode(r.json()["content"]))
        except Exception:
            pass
        return None
    try:
        return json.loads(Path(__file__).with_name(path).read_text())
    except Exception:
        return None


def write_repo_json(path: str, data, message: str) -> bool:
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if not _github_cfg():
        Path(__file__).with_name(path).write_text(text)
        return True
    shas = st.session_state.setdefault("gh_sha", {})
    body = {"message": message, "content": base64.b64encode(text.encode()).decode()}
    if shas.get(path):
        body["sha"] = shas[path]
    r = _gh_request("PUT", body, path)
    if r.status_code in (409, 422):  # sha เก่า: ดึงใหม่แล้วลองอีกครั้ง
        g = _gh_request("GET", path=path)
        if g.status_code == 200:
            body["sha"] = g.json()["sha"]
            r = _gh_request("PUT", body, path)
    if r.status_code in (200, 201):
        shas[path] = r.json()["content"]["sha"]
        return True
    st.toast(f"บันทึก {path} ไม่สำเร็จ (GitHub {r.status_code})", icon="⚠️")
    return False


def _clean(data) -> list[str] | None:
    if isinstance(data, list):
        return [str(t).upper() for t in data]
    return None


def load_watchlist() -> list[str]:
    data = _clean(read_repo_json("watchlist.json"))
    return data if data is not None else DEFAULT_WATCHLIST.copy()


def save_watchlist(tickers: list[str]) -> None:
    write_repo_json("watchlist.json", tickers, "Update watchlist")


if "watchlist" not in st.session_state:
    st.session_state.watchlist = load_watchlist()
if "alerts" not in st.session_state:
    _al = read_repo_json("alerts.json")
    st.session_state.alerts = [x for x in (_al if isinstance(_al, list) else [])
                               if isinstance(x, dict) and {"id", "ticker", "kind", "target"} <= x.keys()]


def save_alerts() -> bool:
    return write_repo_json("alerts.json", st.session_state.alerts, "Update price alerts")


@st.cache_data(ttl=60, show_spinner=False)
def load_alert_state(_nonce: int = 0) -> dict:
    data = read_repo_json("alerts_state.json")
    return data if isinstance(data, dict) else {}


def last_price(ticker: str) -> float | None:
    try:
        h = get_history(ticker, "2d", "5m", True)
        return float(h["Close"].dropna().iloc[-1]) if not h.empty else None
    except Exception:
        return None


# ---------------- data ----------------
@st.cache_data(ttl=60, show_spinner=False)
def get_history(ticker: str, period: str, interval: str, prepost: bool) -> pd.DataFrame:
    df = yf.Ticker(ticker).history(
        period=period, interval=interval, prepost=prepost, auto_adjust=False
    )
    if df.empty:
        return df
    if df.index.tz is not None:
        df.index = df.index.tz_convert(ET)
    return df


@st.cache_data(ttl=60, show_spinner=False)
def get_quote(ticker: str) -> dict:
    """ราคาปิดปกติ + ราคา pre/after-market ล่าสุด (จากแท่ง 1m prepost)"""
    q = {"ticker": ticker}
    try:
        daily = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
        if len(daily) >= 1:
            q["close"] = float(daily["Close"].iloc[-1])
            q["close_date"] = daily.index[-1].date()
        if len(daily) >= 2:
            prev = float(daily["Close"].iloc[-2])
            q["chg_pct"] = (q["close"] / prev - 1) * 100
        intra = yf.Ticker(ticker).history(period="1d", interval="1m", prepost=True)
        if not intra.empty:
            intra.index = intra.index.tz_convert(ET)
            last_ts = intra.index[-1]
            t = last_ts.time()
            if t >= time(16, 0) or t < time(9, 30):
                q["ext_price"] = float(intra["Close"].iloc[-1])
                q["ext_time"] = last_ts
                q["ext_label"] = "After-hours" if t >= time(16, 0) else "Pre-market"
                if "close" in q:
                    q["ext_chg_pct"] = (q["ext_price"] / q["close"] - 1) * 100
    except Exception as e:  # noqa: BLE001
        q["error"] = str(e)
    return q


@st.cache_data(ttl=3600, show_spinner=False)
def get_name(ticker: str) -> str:
    try:
        info = yf.Ticker(ticker).info
        return info.get("shortName") or info.get("longName") or ticker
    except Exception:
        return ticker


def ticker_exists(ticker: str) -> bool:
    try:
        return not yf.Ticker(ticker).history(period="5d").empty
    except Exception:
        return False


# ---------------- sidebar: watchlist ----------------
with st.sidebar:
    st.header("Watchlist")
    with st.form("add", clear_on_submit=True):
        new = st.text_input("เพิ่มหุ้น / ETF", placeholder="เช่น TSLA, SCHD, BRK-B")
        if st.form_submit_button("➕ เพิ่ม", width="stretch"):
            added, bad = [], []
            for t in [x.strip().upper() for x in new.replace(" ", ",").split(",") if x.strip()]:
                if t in st.session_state.watchlist:
                    continue
                (added if ticker_exists(t) else bad).append(t)
            if added:
                st.session_state.watchlist += added
                save_watchlist(st.session_state.watchlist)
                st.success("เพิ่มแล้ว: " + ", ".join(added))
            if bad:
                st.error("ไม่พบ symbol: " + ", ".join(bad))

    for t in list(st.session_state.watchlist):
        c1, c2 = st.columns([4, 1])
        if c1.button(t, key=f"sel_{t}", width="stretch"):
            st.session_state.selected = t
        if c2.button("✕", key=f"del_{t}", help=f"ลบ {t}" if len(st.session_state.watchlist) > 1
                     else "ต้องมีอย่างน้อย 1 ตัว", disabled=len(st.session_state.watchlist) <= 1):
            st.session_state.watchlist.remove(t)
            save_watchlist(st.session_state.watchlist)
            if st.session_state.get("selected") == t:
                st.session_state.pop("selected")
            st.rerun()

    if st.button("🔄 Refresh ข้อมูล", width="stretch"):
        st.cache_data.clear()
        st.rerun()

wl = st.session_state.watchlist
if not wl:
    st.info("Watchlist ว่าง — เพิ่ม symbol ที่ sidebar ด้านซ้าย")
    st.stop()
if st.session_state.get("selected") not in wl:
    st.session_state.selected = wl[0]

(tab_chart, tab_overview, tab_compare, tab_company, tab_buffett, tab_screener, tab_alerts, tab_port,
 tab_manual) = st.tabs(["📈 กราฟ", "📋 ภาพรวม", "⚖️ เปรียบเทียบ", "🏢 บริษัท", "🎩 Buffett", "🔍 Screener",
                        "🔔 แจ้งเตือน", "💼 พอร์ต", "📖 คู่มือ"])

# ---------------- tab: chart ----------------
with tab_chart:
    ticker = st.session_state.selected
    q = get_quote(ticker)

    st.subheader(f"{ticker} · {get_name(ticker)}")
    m1, m2, m3 = st.columns(3)
    if "close" in q:
        m1.metric(f"ปิดตลาด ({q['close_date']})", f"${q['close']:,.2f}",
                  f"{q['chg_pct']:+.2f}%" if "chg_pct" in q else None)
    if "ext_price" in q:
        m2.metric(f"{q['ext_label']} ({q['ext_time']:%H:%M} ET)", f"${q['ext_price']:,.2f}",
                  f"{q['ext_chg_pct']:+.2f}%" if "ext_chg_pct" in q else None)
    else:
        m2.metric("Pre / After-hours", "—")
    m3.caption("ข้อมูลจาก Yahoo Finance (delay ~15 นาทีช่วงตลาดเปิด) · เวลาเป็น ET")

    c1, c2, c3, c4 = st.columns([5, 1.4, 1.2, 1.4])
    tf = c1.radio("ช่วงเวลา", list(TIMEFRAMES), index=0, horizontal=True)
    style = c2.radio("แบบกราฟ", ["Candle", "Line"], horizontal=True)
    log = c3.toggle("Log scale", value=tf in ("10Y", "Max"))
    show_ma = c4.toggle("MA 50/200", value=tf in ("1Y", "5Y"))

    period, interval, prepost = TIMEFRAMES[tf]
    with st.spinner("กำลังโหลดข้อมูล…"):
        df = get_history(ticker, period, interval, prepost)

    if df.empty:
        st.warning("ไม่มีข้อมูลสำหรับช่วงเวลานี้")
    else:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            row_heights=[0.78, 0.22], vertical_spacing=0.03)
        if style == "Candle":
            fig.add_trace(go.Candlestick(
                x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
                name=ticker, increasing_line_color="#16a34a", decreasing_line_color="#dc2626"),
                row=1, col=1)
        else:
            fig.add_trace(go.Scatter(x=df.index, y=df["Close"], mode="lines", name=ticker,
                                     line=dict(width=1.6, color="#2563eb")), row=1, col=1)

        if show_ma and not prepost:
            for n, color in ((50, "#f59e0b"), (200, "#8b5cf6")):
                if len(df) > n:
                    fig.add_trace(go.Scatter(x=df.index, y=df["Close"].rolling(n).mean(),
                                             mode="lines", name=f"MA{n}",
                                             line=dict(width=1.2, color=color)), row=1, col=1)

        vol_colors = ["#16a34a" if c >= o else "#dc2626" for o, c in zip(df["Open"], df["Close"])]
        fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=vol_colors,
                             name="Volume", opacity=0.6), row=2, col=1)

        # แรเงาช่วง pre-market / after-hours บนกราฟ intraday
        if prepost:
            for day in sorted(set(df.index.date)):
                d = pd.Timestamp(day, tz=ET)
                for start, end in ((time(4, 0), time(9, 30)), (time(16, 0), time(20, 0))):
                    fig.add_vrect(x0=d.replace(hour=start.hour, minute=start.minute),
                                  x1=d.replace(hour=end.hour, minute=end.minute),
                                  fillcolor="#94a3b8", opacity=0.15, line_width=0,
                                  row="all", col=1)
            breaks = [dict(bounds=["sat", "mon"]), dict(bounds=[20, 4], pattern="hour")]
        elif interval == "1d":
            breaks = [dict(bounds=["sat", "mon"])]
        else:
            breaks = []

        fig.update_xaxes(rangebreaks=breaks, rangeslider_visible=False)
        fig.update_yaxes(type="log" if log else "linear", row=1, col=1, title="USD")
        fig.update_layout(height=620, margin=dict(l=10, r=10, t=10, b=10),
                          hovermode="x unified", legend=dict(orientation="h", y=1.02),
                          dragmode="pan")
        st.plotly_chart(fig, width="stretch",
                        config={"scrollZoom": True, "displaylogo": False})

        first, last = df["Close"].iloc[0], df["Close"].iloc[-1]
        st.caption(
            f"{tf}: {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d %H:%M} · "
            f"เปลี่ยนแปลง {(last / first - 1) * 100:+.2f}% · "
            f"High {df['High'].max():,.2f} · Low {df['Low'].min():,.2f}"
            + (" · แถบสีเทา = Pre-market / After-hours" if prepost else "")
        )
    with st.expander("📘 วิธีอ่านกราฟ"):
        st.markdown(guides.CHART)

# ---------------- tab: overview ----------------
with tab_overview:
    rows = []
    with st.spinner("กำลังโหลดราคา…"):
        for t in wl:
            q = get_quote(t)
            rows.append({
                "Symbol": t,
                "ปิดล่าสุด": q.get("close"),
                "% วันนี้": q.get("chg_pct"),
                "Pre/After": q.get("ext_price"),
                "% Pre/After": q.get("ext_chg_pct"),
                "เวลา (ET)": q["ext_time"].strftime("%H:%M") if "ext_time" in q else "",
            })
    table = pd.DataFrame(rows).set_index("Symbol")

    def color(v):
        if pd.isna(v):
            return ""
        return "color:#16a34a" if v > 0 else "color:#dc2626" if v < 0 else ""

    st.dataframe(
        table.style.format({"ปิดล่าสุด": "${:,.2f}", "Pre/After": "${:,.2f}",
                            "% วันนี้": "{:+.2f}%", "% Pre/After": "{:+.2f}%"}, na_rep="—")
        .map(color, subset=["% วันนี้", "% Pre/After"]),
        width="stretch",
    )

# ---------------- tab: compare ----------------
with tab_compare:
    picks = st.multiselect("เลือก symbol", wl, default=wl[: min(3, len(wl))])
    cperiod = st.radio("ช่วงเวลา", ["1y", "3y", "5y", "10y", "max"], index=2,
                       horizontal=True, key="cmp_period")
    if picks:
        fig = go.Figure()
        series = {}
        for t in picks:
            d = get_history(t, cperiod, "1d" if cperiod in ("1y", "3y") else "1wk", False)
            if d.empty:
                continue
            s = (d["Adj Close"] if "Adj Close" in d else d["Close"]).dropna()
            s.index = s.index.tz_localize(None).normalize()
            series[t] = s
        if series:
            start = max(s.index[0] for s in series.values())  # เริ่มพร้อมกันทุกตัว
            palette = ["#3b82f6", "#f97316", "#22c55e", "#ef4444", "#a855f7",
                       "#eab308", "#06b6d4", "#ec4899", "#84cc16", "#94a3b8"]
            for i, (t, s) in enumerate(series.items()):
                s = s[s.index >= start]
                fig.add_trace(go.Scatter(x=s.index, y=(s / s.iloc[0] - 1) * 100,
                                         mode="lines", name=t,
                                         line=dict(width=2, color=palette[i % len(palette)])))
        fig.update_layout(height=520, yaxis_title="% Total return (รวมปันผล)",
                          hovermode="x unified", margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, width="stretch")
        if series:
            st.caption(f"เริ่มนับพร้อมกันที่ {start:%Y-%m-%d} (วันแรกที่ทุกตัวมีข้อมูล)")


# ================= Buffett =================
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_fundamentals(ticker: str) -> dict:
    return fetch_fundamentals(ticker)


BI_FILE = Path(__file__).with_name("buffett_indicator.csv")


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_buffett_indicator(mtime: float) -> tuple[pd.DataFrame, str]:
    """ใช้ไฟล์ที่ GitHub Actions บันทึกไว้ทุกสัปดาห์ ถ้ายังไม่มีไฟล์ค่อยดึงสดจาก DBnomics"""
    if BI_FILE.exists():
        df = pd.read_csv(BI_FILE, parse_dates=["date"]).set_index("date")
        return df, "ไฟล์ที่บันทึกทุกสัปดาห์"
    return build_buffett_indicator(timeout=15, tries=1), "DBnomics (ดึงสด)"



SCREENER_FILE = Path(__file__).with_name("screener.csv")


@st.cache_data(ttl=600, show_spinner=False)
def load_screener(mtime: float) -> pd.DataFrame:  # mtime: โหลดใหม่เมื่อไฟล์เปลี่ยน
    return pd.read_csv(SCREENER_FILE)


def buffett_zone(v: float) -> str:
    if v < 75:
        return "🟢 ถูกมาก (Significantly undervalued)"
    if v < 90:
        return "🟢 ค่อนข้างถูก (Modestly undervalued)"
    if v < 115:
        return "🟡 ราคาเหมาะสม (Fair value)"
    if v < 135:
        return "🟠 ค่อนข้างแพง (Modestly overvalued)"
    return "🔴 แพงมาก (Significantly overvalued)"


def show_indicator(series: pd.Series, est: float, zones: bool, unit: str, color: str,
                   official: tuple[str, str], caption: str) -> None:
    pct = (series < est).mean() * 100
    mean, sd = series.mean(), series.std()
    k1, k2, k3 = st.columns(3)
    k1.metric("ประมาณการวันนี้", f"{est:.0f}%", f"{(est - mean) / sd:+.1f} SD จากค่าเฉลี่ย", delta_color="off")
    k2.metric(official[0], official[1])
    k3.metric("สูงกว่าในอดีต", f"{pct:.0f}% ของทุก{unit}", help=f"เทียบกับข้อมูลตั้งแต่ {series.index[0]:%Y}")
    if zones:
        st.markdown(f"**เกณฑ์ดั้งเดิมของ Buffett: {buffett_zone(est)}**")
    else:
        z = (est - mean) / sd
        lvl = ("🔴 สูงกว่าค่าเฉลี่ยมาก (> +2 SD)" if z > 2 else "🟠 สูงกว่าค่าเฉลี่ย (+1 ถึง +2 SD)" if z > 1
               else "🟡 ใกล้ค่าเฉลี่ย (±1 SD)" if z >= -1 else "🟢 ต่ำกว่าค่าเฉลี่ย (< −1 SD)")
        st.markdown(f"**เทียบกับประวัติของตัวเอง: {lvl}**")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines", name="Buffett Indicator",
                             line=dict(color=color, width=2)))
    fig.add_trace(go.Scatter(x=[pd.Timestamp.today()], y=[est], mode="markers", name="ประมาณการวันนี้",
                             marker=dict(color="#ef4444", size=10)))
    for y, label, dash in ((mean, "ค่าเฉลี่ย", "solid"), (mean + sd, "+1 SD", "dash"),
                           (mean + 2 * sd, "+2 SD", "dot"), (mean - sd, "-1 SD", "dash")):
        fig.add_hline(y=y, line_dash=dash, line_color="#94a3b8", opacity=0.7,
                      annotation_text=f"{label} {y:.0f}%", annotation_position="top left")
    fig.update_layout(height=420, yaxis_title="% ของ GDP", hovermode="x unified",
                      margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.05))
    st.plotly_chart(fig, width="stretch")
    st.caption(caption)


def sp500_indicator(bi_df: pd.DataFrame) -> dict | None:
    """S&P 500 market cap ÷ GDP
    - ค่าจริง ณ วันที่ Screener อัปเดต = ผลรวม marketCap ของทุกบริษัทใน S&P 500
    - ค่าย้อนหลัง/วันนี้ = ค่าจริงนั้น × (ดัชนี ^GSPC ณ เวลานั้น ÷ ดัชนี ณ วันที่อัปเดต)"""
    if not SCREENER_FILE.exists():
        return None
    sc = load_screener(SCREENER_FILE.stat().st_mtime)
    if "market_cap" not in sc.columns or sc["market_cap"].notna().sum() < 400:
        return None
    base = sc["name"].astype(str).str.replace(r"\s*\(Class [A-Z]\)", "", regex=True).str.strip()
    uniq = sc.assign(_base=base).dropna(subset=["market_cap"]).drop_duplicates("_base")
    anchor_mcap = float(uniq["market_cap"].sum()) / 1e9  # พันล้าน USD
    anchor_date = pd.Timestamp(sc["updated"].iloc[0])
    daily = get_history("^GSPC", "1y", "1d", False)["Close"]
    daily.index = daily.index.tz_localize(None)
    at_anchor = daily[daily.index <= anchor_date + pd.Timedelta(days=1)]
    if at_anchor.empty:
        return None
    idx_anchor, idx_now = float(at_anchor.iloc[-1]), float(daily.iloc[-1])
    monthly = get_history("^GSPC", "max", "1mo", False)["Close"]
    monthly.index = monthly.index.tz_localize(None)
    gdp = bi_df["gdp_bn"]
    monthly = monthly[monthly.index >= gdp.index[0]]
    gdp_m = gdp.reindex(gdp.index.union(monthly.index)).ffill().reindex(monthly.index)
    series = anchor_mcap * monthly / idx_anchor / gdp_m * 100
    return {"series": series.dropna(), "est": anchor_mcap * idx_now / idx_anchor / float(gdp.iloc[-1]) * 100,
            "anchor_date": f"{anchor_date:%Y-%m-%d}", "anchor_mcap_tn": anchor_mcap / 1000,
            "gdp_tn": float(gdp.iloc[-1]) / 1000, "n": len(uniq)}


def analyst_package(ticker, f, rows, extra, price, iv, mos, passed, applicable,
                    basis, g, dr, tg, tnx, imp) -> str:
    """ข้อความสรุปตัวเลขจากแอพ สำหรับวางให้ Claude วิเคราะห์ต่อ"""
    info = f["info"]
    mc = info.get("marketCap")
    lines = [
        f"วิเคราะห์หุ้น {ticker} ด้วย skill stock-analyst โดยใช้ข้อมูลจากแอพด้านล่างประกอบ "
        "และค้นข้อมูลล่าสุดเพิ่มเติม (10-K, earnings call, proxy statement, ข่าว)",
        "",
        f"=== ข้อมูลจากแอพ US Stock Chart ({pd.Timestamp.now():%Y-%m-%d}) ===",
        f"บริษัท: {info.get('longName') or info.get('shortName') or ticker} ({ticker})",
        f"Sector / Industry: {info.get('sector', '-')} / {info.get('industry', '-')}",
        f"ราคา: ${price:,.2f}" + (f" · มูลค่าตลาด: {fmt_money(mc)}" if mc else "") if price else "ราคา: -",
        f"ผลตอบแทนพันธบัตรรัฐบาลสหรัฐ 10 ปี: {tnx:.2%}" if tnx else "",
        "",
        f"[Buffett checklist] ผ่าน {passed}/{applicable} ข้อที่ประเมินได้",
    ]
    for r in rows:
        lines.append(f"- {r['หลักเกณฑ์']} (เกณฑ์ {r['เกณฑ์ผ่าน']}): {r['ค่าจริง']} → {r['ผล']}")
    yrs = sorted(set().union(*[set(s.index) for s in (extra.get("rev"), extra.get("ni"), extra.get("fcf"),
                                                       extra.get("oe")) if s is not None]))
    if yrs:
        lines += ["", "[งบการเงินรายปี] ปี | รายได้ | กำไรสุทธิ | Free cash flow | Owner earnings"]
        for y in yrs:
            def v(key):
                s = extra.get(key)
                return fmt_money(s[y]) if s is not None and y in s.index and pd.notna(s[y]) else "-"
            lines.append(f"{y} | {v('rev')} | {v('ni')} | {v('fcf')} | {v('oe')}")
    lines.append("")
    if extra.get("financial"):
        pb, roe_i = price_to_book(f), info.get("returnOnEquity")
        lines.append("[มูลค่า] กลุ่มการเงิน — แอพไม่คำนวณ DCF"
                     + (f" · P/B {pb:.2f} เท่า" if pb else "") + (f" · ROE ล่าสุด {roe_i:.1%}" if roe_i else ""))
    else:
        lines.append(f"[มูลค่า DCF จากแอพ] ฐาน {basis} · เติบโต {g:.1%}/ปี 10 ปี · discount {dr:.1%} · terminal {tg:.1%}")
        lines.append(f"มูลค่าที่แท้จริง: {'$' + format(iv, ',.2f') if iv else '-'} · Margin of safety: "
                     f"{format(mos, '+.0%') if mos is not None else '-'}")
        if imp is not None:
            lines.append(f"ราคาปัจจุบันคาดการณ์ให้กระแสเงินสดโต {imp:+.1%}/ปี นาน 10 ปี")
    lines += ["", "หมายเหตุ: ตัวเลขจาก Yahoo Finance งบ 4 ปีล่าสุด อาจคลาดเคลื่อน ให้ตรวจกับ 10-K"]
    return "\n".join(x for x in lines if x is not None)


with tab_buffett:
    st.markdown("### 🎩 Buffett checklist รายตัว")
    b_ticker = st.selectbox("เลือกหุ้น", wl, index=wl.index(st.session_state.selected), key="buffett_ticker")
    with st.spinner("กำลังโหลดงบการเงิน…"):
        f = get_fundamentals(b_ticker)
    info = f["info"]
    qtype = str(info.get("quoteType", "")).upper()

    if not is_company(f):
        st.info(f"{b_ticker} เป็น {qtype or 'กองทุน/ดัชนี'} หรือไม่มีงบการเงิน — Buffett checklist ใช้ได้เฉพาะหุ้นรายบริษัท "
                "ดู Buffett Indicator ของตลาดรวมด้านล่างแทน")
    else:
        rows, extra = buffett_checks(f)
        price = current_price(info) or get_quote(b_ticker).get("close")
        shares_out = share_count(info, price)

        summary = st.container()
        with st.expander("⚙️ สมมติฐานมูลค่าที่แท้จริง (DCF) — ปรับได้", expanded=False):
            if extra.get("financial"):
                st.info("หุ้นกลุ่มการเงิน (ธนาคาร ประกัน บริษัทจัดการกองทุน) ไม่คำนวณ DCF จากกระแสเงินสด "
                        "เพราะเงินสดของลูกค้าปนอยู่ในงบ ตัวเลขจะสูงเกินจริงมาก · ควรดู P/B และ ROE แทน")
            options = dcf_options(extra)
            basis = st.radio("ฐานกระแสเงินสด", options, horizontal=True, key=f"basis_{b_ticker}")
            base = extra.get("oe_base" if basis == BASIS_OE else "fcf_base")
            default_g = default_growth(extra, basis)
            c1, c2, c3 = st.columns(3)
            g = c1.slider("กระแสเงินสดโตปีละ (10 ปีแรก)", 0.0, 0.25,
                          round(round(default_g / 0.005) * 0.005, 3), 0.005, format="%.3f",
                          key=f"g_{b_ticker}_{basis}")
            dr = c2.slider("Discount rate (ผลตอบแทนที่ต้องการ)", 0.06, 0.15, 0.10, 0.005, format="%.3f",
                           key=f"dr_{b_ticker}")
            tg = c3.slider("Terminal growth", 0.0, 0.04, 0.025, 0.005, format="%.3f", key=f"tg_{b_ticker}")
            st.caption(
                f"ฐาน {basis} = ค่าเฉลี่ย 3 ปีล่าสุด {fmt_money(base) if base else '—'} · "
                "ค่าเริ่มต้นของการเติบโต = ค่าต่ำกว่าระหว่าง CAGR กระแสเงินสดกับ CAGR รายได้ จำกัด 0–10% (ถ้าเคยติดลบ = 0%) · "
                "สูตร: PV ของกระแสเงินสด 10 ปี + Terminal value ÷ จำนวนหุ้น · 0.10 = 10%")

        try:
            tnx = float(get_history("^TNX", "5d", "1d", False)["Close"].iloc[-1]) / 100
        except Exception:
            tnx = None
        vrows, iv, mos, oe_y = valuation_rows(extra, price, shares_out, basis, g, dr, tg, tnx)
        rows += vrows

        passed = sum(r["_s"] == "pass" for r in rows)
        applicable = sum(r["_s"] != "na" for r in rows)
        with summary:
            st.caption(f"{info.get('shortName', b_ticker)} · {info.get('sector', '')} · "
                       f"งบการเงินรายปี {f['inc'].index.min()}–{f['inc'].index.max()} (Yahoo Finance)")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("คะแนน Buffett", f"{passed}/{applicable}",
                      help="นับเฉพาะข้อที่ประเมินได้ (ไม่รวม N/A)")
            m2.metric("ราคาปัจจุบัน", f"${price:,.2f}" if price else "—")
            if extra.get("financial"):
                pb, roe_i = price_to_book(f), info.get("returnOnEquity")
                m3.metric("P/B (ราคา ÷ มูลค่าทางบัญชี)", f"{pb:.2f} เท่า" if pb else "—",
                          help="กลุ่มการเงินใช้ P/B แทน DCF · ยิ่งต่ำยิ่งถูก เทียบกับ ROE ประกอบ")
                m4.metric("ROE ล่าสุด", f"{roe_i:.1%}" if roe_i is not None else "—")
            else:
                m3.metric("มูลค่าที่แท้จริง (DCF)", f"${iv:,.2f}" if iv else "—")
                m4.metric("Margin of safety", f"{mos:+.0%}" if mos is not None else "—")
            score = passed / applicable if applicable else 0
            verdict = ("🟢 เข้าเกณฑ์ธุรกิจแบบ Buffett" if score >= 0.75 else
                       "🟡 ผ่านบางส่วน — ดูข้อที่ไม่ผ่านประกอบ" if score >= 0.5 else
                       "🔴 ไม่ค่อยเข้าเกณฑ์ Buffett")
            st.markdown(f"**{verdict}**")
            if not extra.get("financial"):
                base_now = extra.get("oe_base" if basis == BASIS_OE else "fcf_base")
                imp = implied_growth(base_now, price, shares_out, dr, tg)
                if imp is not None:
                    note = ("สูงกว่าสมมติฐาน DCF → ราคาคาดหวังการเติบโตสูง" if imp > g + 0.005 else
                            "ต่ำกว่าสมมติฐาน DCF → ราคาคาดหวังไม่สูง" if imp < g - 0.005 else "ใกล้เคียงสมมติฐาน DCF")
                    st.caption(f"📐 **ราคาปัจจุบันคาดการณ์ให้ {basis.split(' (')[0]} โต "
                               f"{'≥ ' if imp >= 0.8 else '≤ ' if imp <= -0.3 else ''}{imp:+.1%} ต่อปี นาน 10 ปี** "
                               f"(ที่ discount rate {dr:.1%}) · สมมติฐาน DCF ตอนนี้ {g:.1%} — {note}")

        table = pd.DataFrame(rows).drop(columns="_s").set_index("หลักเกณฑ์")
        st.dataframe(table, width="stretch")
        with st.expander("📘 วิธีแปลผล Buffett checklist"):
            st.markdown(guides.CHECKLIST)
            if extra.get("financial"):
                st.markdown(guides.FINANCIAL)
        with st.expander("🤖 ส่งข้อมูลให้นักวิเคราะห์ AI (Claude)"):
            st.caption("กดไอคอนคัดลอกที่มุมขวาบนของกล่องด้านล่าง → เปิด Claude (แชทใหม่หรือใน Project) → วาง "
                       "· Claude จะใช้ skill stock-analyst วิเคราะห์โดยใช้ตัวเลขชุดนี้ร่วมกับข้อมูลที่ค้นเพิ่มเอง")
            st.code(analyst_package(b_ticker, f, rows, extra, price, iv, mos, passed, applicable,
                                    basis, g, dr, tg, tnx,
                                    implied_growth(extra.get("oe_base" if basis == BASIS_OE else "fcf_base"),
                                                   price, shares_out, dr, tg)),
                    language=None, wrap_lines=True)
            st.markdown("[เปิด Claude ↗](https://claude.ai/new)")
        if iv and not extra.get("financial"):
            q_target = iv * 0.75
            if st.button(f"🔔 แจ้งเตือนเมื่อ MOS ถึง 25% (ราคา ≤ ${q_target:,.2f}) ด้วยสมมติฐาน DCF ตอนนี้",
                         key=f"quick_alert_{b_ticker}"):
                st.session_state.alerts.append(alerts_mod.new_alert(b_ticker, "mos", q_target, iv=iv, mos=0.25))
                if save_alerts():
                    st.toast(f"ตั้งเตือน {b_ticker} แล้ว — ดูที่แท็บ 🔔 แจ้งเตือน", icon="🔔")

        # กราฟงบการเงินรายปี
        figf = go.Figure()
        for label, s, color in (("รายได้", extra["rev"], "#94a3b8"), ("กำไรสุทธิ", extra["ni"], "#3b82f6"),
                                ("Free cash flow", extra["fcf"], "#22c55e"),
                                ("Owner earnings", extra.get("oe"), "#f97316")):
            if s is not None:
                figf.add_trace(go.Bar(x=s.index.astype(str), y=s.values, name=label, marker_color=color))
        figf.update_layout(barmode="group", height=320, margin=dict(l=10, r=10, t=30, b=10),
                           title="งบการเงินรายปี (USD)", legend=dict(orientation="h", y=1.12))
        st.plotly_chart(figf, width="stretch")
        oet = extra.get("oe_table")
        with st.expander("🔎 Owner Earnings รายปี — ที่มาของตัวเลข"):
            if oet is None:
                st.info("งบไม่มีข้อมูลกระแสเงินสดจากการดำเนินงานหรือ capex พอจะคำนวณ Owner Earnings")
            else:
                show = pd.DataFrame({
                    "(กำไรสุทธิ — ไว้เทียบ)": oet["ni"],
                    "กระแสเงินสดจากการดำเนินงาน (OCF)": oet["ocf"],
                    "− ค่าตอบแทนเป็นหุ้น (SBC)": -oet["sbc"],
                    "(Capex ทั้งหมด)": -oet["capex"],
                    "(Growth capex)": oet["growth_capex"], "− Maintenance capex": -oet["maint_capex"],
                    "= Owner earnings": oet["oe"],
                }).T
                show.columns = [str(c) for c in show.columns]
                st.dataframe(show.map(lambda v: fmt_money(v) if pd.notna(v) else "—"), width="stretch")
                if oe_y is not None:
                    st.caption(f"Owner earnings yield (เฉลี่ย 3 ปี ÷ มูลค่าตลาด) = {oe_y:.1%} · "
                               f"เทียบเท่า P/OE {1 / oe_y:.1f} เท่า" if oe_y > 0 else
                               f"Owner earnings yield = {oe_y:.1%}")
                st.caption("สูตร Buffett (1986): กำไรสุทธิ + ค่าเสื่อม + รายการไม่ใช่เงินสด ± working capital − "
                           "Maintenance capex ซึ่งส่วนแรกรวมกันก็คือ OCF · จึงคิดเป็น OE = OCF − SBC − Maintenance capex "
                           "(หัก SBC เพราะ Buffett ถือว่าเป็นค่าใช้จ่ายจริง) · Maintenance capex ประมาณแบบ Greenwald = "
                           "capex ทั้งหมด − (Net PPE ÷ รายได้) × รายได้ที่เพิ่มขึ้น")
        st.caption("ข้อที่ระบบประเมินไม่ได้แต่ Buffett ให้ความสำคัญ: เข้าใจธุรกิจไหม · มี moat ยั่งยืนไหม · "
                   "ผู้บริหารซื่อตรงและจัดสรรเงินเก่งไหม — ต้องประเมินเอง · ไม่ใช่คำแนะนำการลงทุน")

    # ---------- Market Buffett Indicator ----------
    st.divider()
    st.markdown("### 🇺🇸 Buffett Indicator (มูลค่าตลาดหุ้น ÷ GDP)")
    bi_view = st.radio("ดูของ", ["ตลาดหุ้นสหรัฐทั้งตลาด", "S&P 500"], horizontal=True, key="bi_view",
                       help="ทั้งตลาด = มูลค่าหุ้นบริษัทนอกภาคการเงินทั้งหมด (Fed Z.1) · "
                            "S&P 500 = มูลค่าตลาดรวมของ 500 บริษัทใหญ่ (รวมธนาคาร/ประกัน)")
    with st.expander("📘 วิธีแปลผล Buffett Indicator"):
        st.markdown(guides.INDICATOR)
    try:
        with st.spinner("กำลังโหลดข้อมูล Buffett Indicator…"):
            bi_df, bi_src = load_buffett_indicator(BI_FILE.stat().st_mtime if BI_FILE.exists() else 0.0)
        if bi_view == "S&P 500":
            sp = sp500_indicator(bi_df)
            if sp is None:
                st.info("ต้องใช้ข้อมูลมูลค่าตลาดจาก Screener รอบใหม่ — รอรอบอัตโนมัติวันเสาร์ "
                        "หรือกด Run workflow ใน GitHub Actions")
            else:
                show_indicator(
                    sp["series"], sp["est"], zones=False, unit="เดือน", color="#a855f7",
                    official=(f"มูลค่าตลาด S&P 500 ({sp['anchor_date']})", f"${sp['anchor_mcap_tn']:,.1f}T"),
                    caption=(f"มูลค่าตลาดรวม {sp['n']} บริษัทใน S&P 500 จาก Screener วันที่ {sp['anchor_date']} "
                             f"(หุ้นหลาย class นับครั้งเดียว) ÷ GDP ล่าสุด ${sp['gdp_tn']:,.1f}T · "
                             "ค่าย้อนหลังประมาณจากระดับดัชนี S&P 500 (^GSPC) รายเดือน เทียบกับวันที่มีมูลค่าจริง "
                             "จึงคลาดเคลื่อนได้ช่วงที่ห่างจากปัจจุบันมาก · ไม่มีเกณฑ์ 75/90/115/135% "
                             "เพราะเกณฑ์นั้นทำไว้สำหรับตลาดรวม ให้เทียบกับค่าเฉลี่ยและ SD ของตัวเองแทน"))
        else:
            bi = bi_df["ratio"]
            q_end = bi.index[-1] + pd.offsets.QuarterEnd(0)
            vti = get_history("VTI", "2y", "1d", False)["Close"]
            vti.index = vti.index.tz_localize(None)
            at_qend = vti[vti.index <= q_end]
            est = float(bi.iloc[-1] * vti.iloc[-1] / at_qend.iloc[-1]) if len(at_qend) else float(bi.iloc[-1])
            show_indicator(
                bi, est, zones=True, unit="ไตรมาส", color="#3b82f6",
                official=(f"ข้อมูลทางการล่าสุด ({q_end:%b %Y})", f"{bi.iloc[-1]:.0f}%"),
                caption=(f"ข้อมูลจาก{bi_src} · ที่มา: Federal Reserve Z.1 มูลค่าหุ้นบริษัทนอกภาคการเงิน (FL103164105) "
                         "÷ GDP ของ BEA ผ่าน DBnomics · ข้อมูลรายไตรมาส ประกาศช้า ~3 เดือน "
                         "ค่าปัจจุบันจึงประมาณจากการเปลี่ยนแปลงราคา VTI (ทั้งตลาด) หลังสิ้นไตรมาส · "
                         "เกณฑ์ 75/90/115/135% มาจาก Buffett ปี 2001 แต่ค่านี้มีแนวโน้มสูงขึ้นตามเวลา "
                         "(บริษัทมีรายได้ต่างประเทศมากขึ้น ดอกเบี้ยต่ำลง) จึงควรดูประกอบกับค่าเฉลี่ยและ SD"))
    except Exception as e:  # noqa: BLE001
        st.warning(f"โหลดข้อมูล Buffett Indicator ไม่สำเร็จ ({type(e).__name__}) — ไปที่ GitHub → Actions → "
                   "Run workflow เพื่อบันทึกข้อมูลลง repo แล้วรอ 15 นาที")


# ================= Screener (S&P 500) =================
with tab_screener:
    st.markdown("### 🔍 Buffett Screener — S&P 500")
    if not SCREENER_FILE.exists():
        st.info("ยังไม่มีผลคัดกรอง — ไปที่ GitHub repo → แท็บ **Actions** → **Buffett screener (S&P 500)** "
                "→ **Run workflow** รอประมาณ 30-40 นาที แล้วเปิดหน้านี้ใหม่")
    else:
        sc = load_screener(SCREENER_FILE.stat().st_mtime)
        st.caption(f"อัปเดตล่าสุด {sc['updated'].iloc[0]} · {len(sc)} บริษัท · คำนวณใหม่อัตโนมัติทุกวันเสาร์ · "
                   "DCF ใช้ค่าเริ่มต้น (discount 10%, terminal 2.5%, growth = ต่ำกว่าระหว่าง CAGR กระแสเงินสด/รายได้ จำกัด 0–10%) · "
                   "ธนาคาร/ประกัน/โบรกเกอร์ ไม่คำนวณ DCF/MOS ให้ดู P/B แทน")
        with st.expander("📘 วิธีใช้และแปลผล Screener"):
            st.markdown(guides.SCREENER)
            st.markdown(guides.FINANCIAL)

        MOS_OPTS = {"มี Margin of safety (> 0%)": 0, "MOS ≥ 25% (เกณฑ์ Buffett)": 25, "MOS ≥ 50%": 50,
                    "กำหนดเอง": None, "ไม่กรอง MOS": -999}
        c1, c2, c3 = st.columns([1.2, 1.4, 2])
        min_score = c1.slider("คะแนนขั้นต่ำ (% ข้อที่ผ่าน)", 0, 100, 70, 5, key="sc_min")
        mos_opt = c2.selectbox("Margin of safety", list(MOS_OPTS), index=0, key="sc_mos_opt",
                               help="MOS = (มูลค่าที่แท้จริง − ราคา) ÷ มูลค่าที่แท้จริง · "
                                    "Buffett ต้องการอย่างน้อย 25% เผื่อความผิดพลาดของการประเมิน")
        min_mos = MOS_OPTS[mos_opt]
        if min_mos is None:
            min_mos = c2.slider("MOS ขั้นต่ำ", -100, 90, 10, 5, format="%d%%", key="sc_mos")
        sectors = sorted(sc["sector"].dropna().unique())
        pick_sec = c3.multiselect("Sector", sectors, key="sc_sector", placeholder="ทุก sector")
        c4, c5, c6 = st.columns([1.2, 1.4, 2])
        sort_by = c4.selectbox("เรียงตาม", ["MOS มากสุด", "คะแนนสูงสุด", "OE yield สูงสุด", "P/B ต่ำสุด"],
                               key="sc_sort")
        incl_fin = c5.checkbox("รวมธนาคาร/ประกัน (ไม่มี MOS)", value=False, key="sc_fin",
                               help="หุ้นกลุ่มการเงินไม่คำนวณ DCF จึงไม่มี MOS · เลือกเพื่อแสดงโดยดู P/B แทน")
        query = c6.text_input("ค้นหาชื่อหรือ symbol", key="sc_q", placeholder="เช่น Apple, KO")

        fin = (sc["financial"].astype(str).str.lower() == "true") if "financial" in sc.columns \
            else (sc["sector"] == "Financials")
        view = sc[sc["score_pct"] * 100 >= min_score]
        if min_mos > -999:
            mos_ok = view["margin_of_safety"] * 100 > min_mos if min_mos == 0 \
                else view["margin_of_safety"] * 100 >= min_mos
            view = view[mos_ok.fillna(False) | (fin.reindex(view.index) & incl_fin)]
        elif not incl_fin:
            view = view[~fin.reindex(view.index)]
        if pick_sec:
            view = view[view["sector"].isin(pick_sec)]
        if query:
            q = query.strip().lower()
            view = view[view["ticker"].str.lower().str.contains(q, regex=False)
                        | view["name"].str.lower().str.contains(q, regex=False)]
        sort_map = {"MOS มากสุด": (["margin_of_safety", "score_pct"], [False, False]),
                    "คะแนนสูงสุด": (["score_pct", "margin_of_safety"], [False, False]),
                    "OE yield สูงสุด": (["oe_yield"], [False]),
                    "P/B ต่ำสุด": (["price_to_book"], [True])}
        cols, asc = sort_map[sort_by]
        if all(c in view.columns for c in cols):
            view = view.sort_values(cols, ascending=asc, na_position="last")

        n_mos = int((view["margin_of_safety"] > 0).sum())
        med = view["margin_of_safety"].median()
        s1, s2, s3 = st.columns(3)
        s1.metric("พบ", f"{len(view)} บริษัท")
        s2.metric("มี MOS > 0%", f"{n_mos} บริษัท")
        s3.metric("MOS กลาง (median)", f"{med:+.0%}" if pd.notna(med) else "—")
        pct_cols = ["score_pct", "margin_of_safety", "oe_yield", "roe", "gross_margin", "net_margin"]
        show = view.copy()
        show[pct_cols] = show[pct_cols] * 100
        show["score_txt"] = show["score"].astype(int).astype(str) + "/" + show["applicable"].astype(int).astype(str)
        for col in ("price_to_book", "implied_growth"):
            if col not in show.columns:
                show[col] = None
        show["implied_growth"] = pd.to_numeric(show["implied_growth"], errors="coerce") * 100
        show = show[["ticker", "name", "sector", "score_txt", "score_pct", "price", "intrinsic_value",
                     "margin_of_safety", "implied_growth", "price_to_book", "oe_yield", "roe", "gross_margin", "net_margin", "debt_years", "failed"]]
        st.dataframe(
            show, hide_index=True, width="stretch", height=480,
            column_config={
                "ticker": st.column_config.TextColumn("Symbol"),
                "name": st.column_config.TextColumn("บริษัท"),
                "sector": st.column_config.TextColumn("Sector"),
                "score_txt": st.column_config.TextColumn("คะแนน"),
                "score_pct": st.column_config.ProgressColumn("ผ่าน %", min_value=0, max_value=100, format="%.0f%%"),
                "price": st.column_config.NumberColumn("ราคา", format="$%.2f"),
                "intrinsic_value": st.column_config.NumberColumn("มูลค่า DCF", format="$%.2f"),
                "margin_of_safety": st.column_config.NumberColumn("MOS", format="%.0f%%"),
                "implied_growth": st.column_config.NumberColumn(
                    "โตที่ราคาคาดไว้", format="%.1f%%",
                    help="อัตราเติบโต/ปี 10 ปี ที่ทำให้มูลค่า DCF = ราคาพอดี · ยิ่งต่ำ ราคายิ่งคาดหวังน้อย"),
                "price_to_book": st.column_config.NumberColumn("P/B", format="%.2f"),
                "oe_yield": st.column_config.NumberColumn("OE yield", format="%.1f%%"),
                "roe": st.column_config.NumberColumn("ROE", format="%.0f%%"),
                "gross_margin": st.column_config.NumberColumn("Gross M.", format="%.0f%%"),
                "net_margin": st.column_config.NumberColumn("Net M.", format="%.0f%%"),
                "debt_years": st.column_config.NumberColumn("หนี้ (ปี)", format="%.1f"),
                "failed": st.column_config.TextColumn("ข้อที่ไม่ผ่าน", width="large"),
            },
        )

        add_pick = st.multiselect("เลือกหุ้นเพื่อเพิ่มเข้า Watchlist แล้วดูรายละเอียดในแท็บ 🎩 Buffett",
                                  [t for t in view["ticker"] if t not in st.session_state.watchlist],
                                  key="sc_add")
        if st.button("➕ เพิ่มเข้า Watchlist", disabled=not add_pick, key="sc_add_btn"):
            st.session_state.watchlist += add_pick
            save_watchlist(st.session_state.watchlist)
            st.toast("เพิ่มเข้า Watchlist แล้ว: " + ", ".join(add_pick), icon="✅")
            st.rerun()
        st.caption("ใช้คัดกรองเบื้องต้นเท่านั้น ตัวเลขมาจาก Yahoo Finance อาจคลาดเคลื่อน "
                   "ควรเปิดดูรายละเอียดในแท็บ 🎩 Buffett ก่อนตัดสินใจ · ไม่ใช่คำแนะนำการลงทุน")


# ================= บริษัท: profile / ข่าว / earnings =================
BKK = "Asia/Bangkok"


def _translate_key():
    try:
        return st.secrets["GOOGLE_TRANSLATE_API_KEY"]
    except Exception:
        return None


@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False)
def translate_cached(text: str) -> str:
    return translate(text, "th", _translate_key())


@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False)
def translate_lines_cached(lines: tuple[str, ...]) -> list[str]:
    return translate_lines(list(lines), "th", _translate_key())


@st.cache_data(ttl=1800, show_spinner=False)
def get_news(ticker: str, name: str) -> list[dict]:
    return fetch_news(ticker, name)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_earnings(ticker: str) -> dict:
    return fetch_earnings(ticker)


def esc_dollar(s: str) -> str:
    """$ สองตัวในข้อความ Markdown ถูกตีความเป็นสูตรคณิตศาสตร์ → escape ให้แสดงเป็นเครื่องหมายดอลลาร์"""
    return str(s).replace("\\$", "$").replace("$", "\\$")  # กัน escape ซ้ำกับ md_safe


def md_safe(s: str) -> str:
    """กัน $ ถูกตีความเป็นสูตร และ [ ] ทำลิงก์เพี้ยน"""
    return str(s).replace("$", "\\$").replace("[", "(").replace("]", ")")


with tab_company:
    c_ticker = st.selectbox("เลือกหุ้น / ETF", wl, index=wl.index(st.session_state.selected), key="company_ticker")
    with st.spinner("กำลังโหลดข้อมูลบริษัท…"):
        cinfo = get_fundamentals(c_ticker)["info"]
    cname = cinfo.get("longName") or cinfo.get("shortName") or c_ticker
    is_fund = str(cinfo.get("quoteType", "")).upper() in ("ETF", "MUTUALFUND")
    st.markdown(f"### {md_safe(cname)} ({c_ticker})")
    emp = cinfo.get("fullTimeEmployees")
    facts = [cinfo.get("sector"), cinfo.get("industry"), cinfo.get("category"), cinfo.get("country"),
             f"พนักงาน {emp:,} คน" if emp else None]
    st.caption(" · ".join(x for x in facts if x))
    k1, k2, k3 = st.columns(3)
    mc = cinfo.get("marketCap") or cinfo.get("totalAssets")
    k1.metric("ขนาดกองทุน" if is_fund else "มูลค่าตลาด", fmt_money(mc) if mc else "—")
    pe = cinfo.get("trailingPE")
    k2.metric("P/E (12 เดือนล่าสุด)", f"{pe:.1f} เท่า" if pe else "—")
    beta = cinfo.get("beta") or cinfo.get("beta3Year")
    k3.metric("Beta", f"{beta:.2f}" if beta else "—", help="ความผันผวนเทียบตลาด: 1 = เท่าตลาด, >1 = ผันผวนกว่า")
    if cinfo.get("website"):
        st.markdown(f"🌐 [{cinfo['website']}]({cinfo['website']})")

    # ---- Profile ----
    st.markdown("#### 📄 ข้อมูลบริษัท")
    summary = (cinfo.get("longBusinessSummary") or "").strip()
    if not summary:
        st.info("Yahoo Finance ไม่มีคำอธิบายของหุ้นตัวนี้")
    else:
        if st.toggle("แปลเป็นภาษาไทย", value=True, key="company_th"):
            try:
                with st.spinner("กำลังแปล…"):
                    st.markdown(md_safe(translate_cached(summary)))
                with st.expander("ต้นฉบับภาษาอังกฤษ"):
                    st.markdown(md_safe(summary))
                st.caption("แปลอัตโนมัติด้วย Google Translate — ชื่อเฉพาะและศัพท์การเงินอาจแปลไม่ตรง")
            except Exception as e:  # noqa: BLE001
                st.warning(f"แปลไม่สำเร็จ ({type(e).__name__}) — แสดงต้นฉบับแทน")
                st.markdown(md_safe(summary))
        else:
            st.markdown(md_safe(summary))

    # ---- News ----
    st.markdown("#### 📰 ข่าวล่าสุด")
    with st.spinner("กำลังโหลดข่าว…"):
        news = get_news(c_ticker, cinfo.get("shortName") or c_ticker)
    if not news:
        st.info("ไม่พบข่าวของหุ้นตัวนี้")
    else:
        th_titles = None
        if st.toggle("แปลหัวข้อข่าวเป็นไทย", value=True, key="news_th"):
            try:
                with st.spinner("กำลังแปลหัวข้อข่าว…"):
                    th_titles = translate_lines_cached(tuple(n["title"] for n in news))
            except Exception as e:  # noqa: BLE001
                st.caption(f"แปลหัวข้อข่าวไม่สำเร็จ ({type(e).__name__}) — แสดงต้นฉบับ")
        for i, n in enumerate(news):
            when = pd.Timestamp(n["time"]).tz_convert(BKK).strftime("%d/%m/%Y %H:%M น.") if n["time"] else ""
            title = th_titles[i] if th_titles and th_titles[i] else n["title"]
            st.markdown(f"**[{md_safe(title)}]({n['url']})**  \n"
                        + (f"{md_safe(n['title'])}  \n" if th_titles else "")
                        + f"<small>{md_safe(n['source'])} · {when}</small>", unsafe_allow_html=True)
        st.caption("กดหัวข้อเพื่ออ่านข่าวต้นฉบับ · แหล่งข่าว: Yahoo Finance (สำรอง: Google News) · เวลาเป็นเวลาไทย")

    # ---- Earnings ----
    st.markdown("#### 🎙️ Earnings call และผลประกาศงบ")
    if is_fund:
        st.info("ETF/กองทุนไม่มีการประกาศงบหรือ earnings call — ดูข้อมูลบริษัทที่กองทุนถือแทน")
    else:
        with st.spinner("กำลังโหลดข้อมูลงบ…"):
            ern = get_earnings(c_ticker)
        e1, e2 = st.columns(2)
        nxt = ern.get("next")
        if nxt:
            days = (pd.Timestamp(nxt) - pd.Timestamp.now().normalize()).days
            e1.metric("ประกาศงบครั้งถัดไป (เวลาสหรัฐ)", f"{pd.Timestamp(nxt):%d/%m/%Y}",
                      f"อีก {days} วัน" if days >= 0 else "ผ่านมาแล้ว", delta_color="off")
        else:
            e1.metric("ประกาศงบครั้งถัดไป", "—")
        est_eps = ern.get("eps_est")
        e2.metric("EPS ที่นักวิเคราะห์คาด", f"${est_eps:.2f}" if est_eps is not None and pd.notna(est_eps) else "—")
        hist = ern.get("history")
        if hist is not None and not hist.empty:
            h = pd.DataFrame({
                "วันประกาศ": [f"{d:%d/%m/%Y}" for d in hist.index],
                "EPS คาด": hist["EPS Estimate"].values,
                "EPS จริง": hist["Reported EPS"].values,
                "Surprise %": hist["Surprise(%)"].values if "Surprise(%)" in hist.columns else None,
            })
            h["ผล"] = ["✅ ดีกว่าคาด" if s > 0 else "❌ แย่กว่าคาด" if s < 0 else "➖ ตามคาด" if pd.notna(s) else ""
                      for s in pd.to_numeric(h["Surprise %"], errors="coerce")]
            st.dataframe(h, hide_index=True, width="stretch", column_config={
                "EPS คาด": st.column_config.NumberColumn(format="$%.2f"),
                "EPS จริง": st.column_config.NumberColumn(format="$%.2f"),
                "Surprise %": st.column_config.NumberColumn(format="%+.1f%%")})
        st.markdown("**ฟัง / อ่าน earnings call**")
        for label, url, desc in earnings_links(c_ticker, cinfo):
            st.markdown(f"- **[{label}]({url})** — {desc}")
        with st.expander("📘 วิธีอ่านผลงบและฟัง earnings call"):
            st.markdown(guides.EARNINGS)


# ================= แจ้งเตือนราคา =================
with tab_alerts:
    st.markdown("### 🔔 แจ้งเตือนราคา")
    st.caption("GitHub ตรวจราคาให้ทุก 30 นาที (จันทร์–ศุกร์ ช่วง Pre-market ถึง After-hours) แล้วส่งแจ้งเตือนเข้ามือถือ"
               "ผ่านแอพ ntfy · แต่ละรายการแจ้งครั้งเดียว กด \"ตั้งใหม่\" เพื่อให้เตือนอีกครั้ง")
    al = st.session_state.alerts
    with st.expander("➕ ตั้งเตือนใหม่", expanded=not al):
        a_t = st.selectbox("หุ้น", wl, index=wl.index(st.session_state.selected), key="al_ticker")
        a_kind = st.radio("เตือนเมื่อ", ["Margin of safety ถึง", "ราคาลงถึง", "ราคาขึ้นถึง"], horizontal=True,
                          key="al_kind")
        p_now = last_price(a_t)
        st.caption(f"ราคาล่าสุด {a_t}: ${p_now:,.2f}" if p_now else "ไม่พบราคาล่าสุด")
        a_target, a_iv, a_mos, can_save = None, None, None, True
        if a_kind == "Margin of safety ถึง":
            fa = get_fundamentals(a_t)
            if not is_company(fa):
                st.warning("ETF/กองทุนไม่มีมูลค่า DCF — ใช้แบบ \"ราคาลงถึง\" แทน")
                can_save = False
            else:
                _rows, ex = buffett_checks(fa)
                if ex.get("financial"):
                    st.warning("หุ้นกลุ่มการเงินไม่คำนวณ DCF — ใช้แบบ \"ราคาลงถึง\" โดยดู P/B ประกอบ")
                    can_save = False
                else:
                    pr = current_price(fa["info"]) or p_now
                    bas = dcf_options(ex)[0]
                    _vr, iv0, _m, _oe = valuation_rows(ex, pr, share_count(fa["info"], pr), bas,
                                                       default_growth(ex, bas), 0.10, 0.025, None)
                    a_iv = st.number_input("มูลค่าที่แท้จริงที่ใช้ ($ ต่อหุ้น)", min_value=0.0,
                                           value=float(round(iv0, 2)) if iv0 else float(round(p_now or 0, 2)),
                                           step=1.0, key=f"al_iv_{a_t}",
                                           help="ค่าเริ่มต้น = DCF ด้วยสมมติฐานตั้งต้นของแอพ · ถ้าปรับสมมติฐานในแท็บ 🎩 Buffett "
                                                "แล้วได้ค่าอื่น ใส่ค่านั้นแทนได้")
                    a_mos = st.slider("Margin of safety ที่ต้องการ", 0, 60, 25, 5, format="%d%%", key="al_mos") / 100
                    a_target = a_iv * (1 - a_mos)
                    if not iv0:
                        st.caption("คำนวณ DCF อัตโนมัติไม่ได้ (กระแสเงินสดติดลบ/ไม่มีข้อมูล) — ใส่มูลค่าเอง")
        else:
            base_p = float(round(p_now or 100.0, 2))
            a_target = st.number_input("ราคาเป้าหมาย ($)", min_value=0.0,
                                       value=round(base_p * (0.9 if a_kind == "ราคาลงถึง" else 1.1), 2),
                                       step=1.0, key=f"al_px_{a_t}_{a_kind}")
        if st.session_state.pop("al_clear_note", False):
            st.session_state.al_note = ""
        a_note = st.text_input("บันทึกช่วยจำ (ไม่บังคับ)", key="al_note", placeholder="เช่น ทยอยซื้อไม้แรก 30%")
        if a_target and p_now:
            kind_code = {"Margin of safety ถึง": "mos", "ราคาลงถึง": "below", "ราคาขึ้นถึง": "above"}[a_kind]
            preview = {"kind": kind_code, "target": a_target}
            d = alerts_mod.distance(preview, p_now)
            st.info(f"จะแจ้งเมื่อราคา {'≥' if kind_code == 'above' else '≤'} **${a_target:,.2f}** · "
                    + ("ถึงแล้วตอนนี้ (จะแจ้งรอบตรวจถัดไป)" if d == 0 else f"ห่างจากราคาตอนนี้ {d:+.1%}"))
        if st.button("💾 บันทึกการเตือน", disabled=not (can_save and a_target), key="al_save"):
            kind_code = {"Margin of safety ถึง": "mos", "ราคาลงถึง": "below", "ราคาขึ้นถึง": "above"}[a_kind]
            st.session_state.alerts.append(alerts_mod.new_alert(a_t, kind_code, a_target, iv=a_iv, mos=a_mos,
                                                                note=a_note))
            if save_alerts():
                st.session_state.al_clear_note = True
                st.toast(f"ตั้งเตือน {a_t} แล้ว", icon="🔔")
                st.rerun()

    state = load_alert_state()
    if not al:
        st.info("ยังไม่มีการตั้งเตือน — กด ➕ ตั้งเตือนใหม่ หรือกดปุ่ม 🔔 ในแท็บ 🎩 Buffett")
    else:
        st.markdown(f"**รายการเตือน ({len(al)})**")
        for i, item in enumerate(list(al)):
            p = last_price(item["ticker"])
            done = state.get(item["id"])
            if done:
                status = f"✅ แจ้งแล้ว {done['triggered_at']} ที่ ${done['price']:,.2f}"
            elif p and alerts_mod.is_hit(item, p):
                status = "🎯 ถึงเป้าแล้ว — จะแจ้งในรอบตรวจถัดไป"
            elif p:
                status = f"⏳ รอ — ต้อง{'ขึ้น' if item['kind'] == 'above' else 'ลง'}อีก {abs(alerts_mod.distance(item, p)):.1%}"
            else:
                status = "⏳ รอ"
            c1, c2, c3 = st.columns([5, 1.2, 1])
            c1.markdown(esc_dollar(f"**{item['ticker']}** · {alerts_mod.describe(item)}  \n"
                        f"ราคาล่าสุด {'$' + format(p, ',.2f') if p else '—'} · {status}"
                        + (f"  \n📝 {md_safe(item['note'])}" if item.get("note") else "")))
            if done and c2.button("🔁 ตั้งใหม่", key=f"al_rearm_{item['id']}", help="เปิดให้เตือนอีกครั้ง"):
                al[i] = dict(item, id=alerts_mod.new_alert(item["ticker"], "below", 1)["id"])
                if save_alerts():
                    st.rerun()
            if c3.button("🗑️ ลบ", key=f"al_del_{item['id']}"):
                al.pop(i)
                if save_alerts():
                    st.rerun()
        if st.button("🔄 อัปเดตสถานะ", key="al_refresh"):
            load_alert_state.clear()
            st.rerun()

    with st.expander("📘 วิธีตั้งค่าแอพ ntfy และใช้การแจ้งเตือนให้ได้ผล"):
        st.markdown(guides.ALERTS)


# ================= พอร์ต =================
PORT_FILE = "portfolio.enc.json"


@st.cache_data(ttl=900, show_spinner=False)
def history_since(ticker: str, start: str) -> pd.DataFrame:
    """ราคาปิดรายวัน + ปันผล ตั้งแต่วันที่ start"""
    df = yf.Ticker(ticker).history(start=start, auto_adjust=False)
    if not df.empty and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def close_on(ticker: str, d) -> float | None:
    try:
        h = history_since(ticker, (pd.Timestamp(d) - pd.Timedelta(days=10)).strftime("%Y-%m-%d"))
    except Exception:
        return None
    if h.empty:
        return None
    h = h[h.index <= pd.Timestamp(d) + pd.Timedelta(hours=23)]
    return float(h["Close"].iloc[-1]) if not h.empty else None


TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def date_picker(container, key: str, default=None):
    """เลือกวันที่ด้วย วัน/เดือน/ปี (ใช้แทน st.date_input ที่โหลดไม่ขึ้นบน Streamlit Cloud บางเวอร์ชัน)"""
    import calendar
    today = pd.Timestamp.now().date()
    base = pd.Timestamp(default).date() if default is not None else today
    container.markdown("วันที่")
    d1, d2, d3 = container.columns([1, 1.2, 1.3])
    years = list(range(today.year, today.year - 31, -1))
    y = d3.selectbox("ปี", years, index=years.index(base.year) if base.year in years else 0, key=f"{key}_y",
                     label_visibility="collapsed",
                     format_func=lambda v: f"{v} ({v + 543})")
    months = list(range(1, 13)) if y < today.year else list(range(1, today.month + 1))
    m_default = months.index(base.month) if base.month in months else len(months) - 1
    if st.session_state.get(f"{key}_m") not in months:
        st.session_state.pop(f"{key}_m", None)
    m = d2.selectbox("เดือน", months, index=m_default, key=f"{key}_m", label_visibility="collapsed",
                     format_func=lambda v: TH_MONTHS[v - 1])
    last = calendar.monthrange(y, m)[1]
    if (y, m) == (today.year, today.month):
        last = today.day
    days = list(range(1, last + 1))
    if st.session_state.get(f"{key}_d") not in days:
        st.session_state.pop(f"{key}_d", None)
    d = d1.selectbox("วัน", days, index=min(base.day, last) - 1, key=f"{key}_d", label_visibility="collapsed")
    return pd.Timestamp(y, m, d).date()


def save_portfolio() -> bool:
    return write_repo_json(PORT_FILE, pf.encrypt(st.session_state.port_data, st.session_state.port_pw),
                           "Update portfolio (encrypted)")


with tab_port:
    st.markdown("### 💼 พอร์ตของฉัน")
    if "port_data" not in st.session_state:
        blob = read_repo_json(PORT_FILE)
        if not (isinstance(blob, dict) and {"salt", "data"} <= blob.keys()):
            blob = None
        st.caption("🔒 ข้อมูลพอร์ตเข้ารหัสด้วยรหัสผ่านของคุณ — ไฟล์บน GitHub อ่านไม่ออก และคนที่มีลิงก์แอพเปิดแท็บนี้ไม่ได้")
        if blob is None:
            st.info("ยังไม่มีพอร์ต — ตั้งรหัสผ่านเพื่อสร้างพอร์ตใหม่")
            p1 = st.text_input("ตั้งรหัสผ่าน (อย่างน้อย 6 ตัว)", type="password", key="port_new_pw")
            p2 = st.text_input("ยืนยันรหัสผ่าน", type="password", key="port_new_pw2")
            st.warning("⚠️ จดรหัสผ่านไว้ให้ดี — ถ้าลืม จะเปิดข้อมูลพอร์ตไม่ได้อีก (ไม่มีใครกู้ให้ได้ รวมถึงผู้พัฒนา)")
            if st.button("สร้างพอร์ต", key="port_create", disabled=not p1):
                if len(p1) < 6:
                    st.error("รหัสผ่านสั้นเกินไป")
                elif p1 != p2:
                    st.error("รหัสผ่านสองช่องไม่ตรงกัน")
                else:
                    st.session_state.port_data = {"portfolios": [pf.new_portfolio("พอร์ตหลัก")]}
                    st.session_state.port_pw = p1
                    if save_portfolio():
                        st.rerun()
        else:
            pw = st.text_input("รหัสผ่านพอร์ต", type="password", key="port_pw_in")
            if st.button("🔓 เปิดพอร์ต", key="port_unlock", disabled=not pw):
                try:
                    st.session_state.port_data = pf.normalize(pf.decrypt(blob, pw))
                    st.session_state.port_pw = pw
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
    else:
        ports = st.session_state.port_data["portfolios"]
        ALL_PORTS = "📊 รวมทุกพอร์ต"
        names = [p["name"] for p in ports] + ([ALL_PORTS] if len(ports) > 1 else [])
        if "port_sel_next" in st.session_state:  # ตั้งค่าที่ต้องการสลับไว้จากรอบก่อน (หลังสร้าง/เปลี่ยนชื่อ/ลบ)
            st.session_state.port_sel = st.session_state.pop("port_sel_next")
        if st.session_state.get("port_sel") not in names:
            st.session_state.port_sel = names[0]
        sel_name = st.selectbox("พอร์ต", names, key="port_sel")
        combined = sel_name == ALL_PORTS
        cur = None if combined else next(p for p in ports if p["name"] == sel_name)
        txs = [t for p in ports for t in p["txs"]] if combined else cur["txs"]
        if combined:
            st.caption(f"รวม {len(ports)} พอร์ต · เพิ่ม/ลบรายการได้เฉพาะในแต่ละพอร์ต")

        with st.expander("⚙️ จัดการพอร์ต (สร้างใหม่ / เปลี่ยนชื่อ / ลบ)"):
            n1, n2 = st.columns([3, 1])
            new_name = n1.text_input("ชื่อพอร์ตใหม่", key="port_new_name", placeholder="เช่น พอร์ตปันผล, พอร์ตเกษียณ")
            if n2.button("➕ สร้าง", key="port_add", disabled=not new_name.strip()):
                if new_name.strip() in [p["name"] for p in ports] or new_name.strip() == ALL_PORTS:
                    st.error("มีพอร์ตชื่อนี้แล้ว")
                else:
                    ports.append(pf.new_portfolio(new_name))
                    if save_portfolio():
                        st.session_state.port_sel_next = new_name.strip()
                        st.rerun()
            if cur is not None:
                r1, r2 = st.columns([3, 1])
                ren = r1.text_input(f"เปลี่ยนชื่อ \"{cur['name']}\" เป็น", key=f"port_ren_{cur['id']}")
                if r2.button("✏️ เปลี่ยนชื่อ", key="port_ren_btn", disabled=not ren.strip()):
                    if ren.strip() in [p["name"] for p in ports] or ren.strip() == ALL_PORTS:
                        st.error("มีพอร์ตชื่อนี้แล้ว")
                    else:
                        cur["name"] = ren.strip()
                        if save_portfolio():
                            st.session_state.port_sel_next = ren.strip()
                            st.rerun()
                if len(ports) > 1:
                    d1, d2 = st.columns([3, 1])
                    confirm = d1.text_input(f"พิมพ์ชื่อ \"{cur['name']}\" เพื่อยืนยันการลบ "
                                            f"(รายการ {len(cur['txs'])} รายการจะหายด้วย)", key=f"port_del_cf_{cur['id']}")
                    if d2.button("🗑️ ลบพอร์ต", key="port_del_btn", disabled=confirm.strip() != cur["name"]):
                        ports.remove(cur)
                        if save_portfolio():
                            st.session_state.port_sel_next = ports[0]["name"]
                            st.rerun()
                else:
                    st.caption("ลบพอร์ตได้เมื่อมีมากกว่า 1 พอร์ต")
        top_l, top_r = st.columns([4, 1])
        top_l.caption("🔒 เปิดพอร์ตอยู่ — ปิดแท็บเบราว์เซอร์หรือกดล็อกเมื่อใช้เสร็จ")
        if top_r.button("🔒 ล็อก", key="port_lock"):
            for k in ("port_data", "port_pw", "port_sel"):
                st.session_state.pop(k, None)
            st.rerun()

        pos, realized = pf.compute_positions(txs)
        held = {k: v for k, v in pos.items() if v["shares"] > 0}
        tickers = sorted(pos)
        hist, prices, prev = {}, {}, {}
        if tickers:
            start = min(t["date"] for t in txs)
            with st.spinner("กำลังโหลดราคา…"):
                for tk in tickers:
                    try:
                        hist[tk] = history_since(tk, start)
                    except Exception:
                        hist[tk] = pd.DataFrame()
                    prices[tk] = last_price(tk) or (float(hist[tk]["Close"].iloc[-1]) if not hist[tk].empty else None)
                    if not hist[tk].empty and len(hist[tk]) >= 2:
                        prev[tk] = float(hist[tk]["Close"].iloc[-2])
        wht = st.toggle("หักภาษีปันผล 15% (นักลงทุนไทย)", value=True, key="port_wht",
                        help="สหรัฐหักภาษีปันผล 15% สำหรับผู้มีถิ่นที่อยู่ในไทย (อนุสัญญาภาษีซ้อน)")
        divs = pf.dividends_received(txs, {tk: h["Dividends"] for tk, h in hist.items()
                                           if not h.empty and "Dividends" in h.columns},
                                     0.15 if wht else 0.0)
        div_by = divs.groupby("ticker")["net"].sum() if not divs.empty else pd.Series(dtype=float)

        value = sum(v["shares"] * (prices.get(k) or 0) for k, v in held.items())
        cost = sum(v["cost"] for v in held.values())
        unreal = value - cost
        real_total = sum(realized.values())
        div_total = float(div_by.sum()) if len(div_by) else 0.0
        day_chg = sum(v["shares"] * ((prices.get(k) or 0) - prev.get(k, prices.get(k) or 0)) for k, v in held.items())
        thb = last_price("THB=X")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("มูลค่าพอร์ต", f"${value:,.2f}", f"{day_chg:+,.2f} วันนี้", delta_color="normal",
                  help=f"≈ ฿{value * thb:,.0f} ที่อัตรา {thb:.2f} บาท/ดอลลาร์" if thb else None)
        m2.metric("กำไร/ขาดทุน (ยังไม่ขาย)", f"${unreal:+,.2f}", f"{unreal / cost:+.1%}" if cost else None)
        m3.metric("กำไรที่ขายแล้ว", f"${real_total:+,.2f}")
        m4.metric("ปันผลรับ (ประมาณ)", f"${div_total:,.2f}")
        if thb and value:
            st.caption(f"มูลค่าพอร์ต ≈ **฿{value * thb:,.0f}** · ผลตอบแทนรวม (ยังไม่ขาย + ขายแล้ว + ปันผล) "
                       f"**${unreal + real_total + div_total:+,.2f}** · อัตราแลกเปลี่ยน {thb:.2f} บาท/ดอลลาร์ "
                       "(ไม่รวมกำไร/ขาดทุนจากค่าเงิน)")

        if held:
            sc_map = load_screener(SCREENER_FILE.stat().st_mtime).set_index("ticker") if SCREENER_FILE.exists() else None
            rows_h = []
            for k, v in sorted(held.items(), key=lambda kv: -kv[1]["shares"] * (prices.get(kv[0]) or 0)):
                p = prices.get(k)
                val = v["shares"] * (p or 0)
                r = {"Symbol": k, "หุ้น": v["shares"], "ต้นทุนเฉลี่ย": v["avg"], "ราคาล่าสุด": p, "มูลค่า": val,
                     "สัดส่วน %": val / value * 100 if value else None,
                     "กำไร/ขาดทุน $": val - v["cost"], "กำไร/ขาดทุน %": (val / v["cost"] - 1) * 100 if v["cost"] else None,
                     "วันนี้ %": (p / prev[k] - 1) * 100 if p and prev.get(k) else None,
                     "ปันผลรับ $": float(div_by.get(k, 0.0)), "MOS %": None, "คะแนน Buffett": ""}
                if sc_map is not None and k in sc_map.index:
                    srow = sc_map.loc[k]
                    if pd.notna(srow.get("margin_of_safety")):
                        r["MOS %"] = srow["margin_of_safety"] * 100
                    r["คะแนน Buffett"] = f"{int(srow['score'])}/{int(srow['applicable'])}"
                rows_h.append(r)
            hold_df = pd.DataFrame(rows_h)
            for col in ("ราคาล่าสุด", "สัดส่วน %", "กำไร/ขาดทุน %", "วันนี้ %", "MOS %"):
                hold_df[col] = pd.to_numeric(hold_df[col], errors="coerce")
            st.dataframe(hold_df, hide_index=True, width="stretch", column_config={
                "หุ้น": st.column_config.NumberColumn(format="%g"),
                "ต้นทุนเฉลี่ย": st.column_config.NumberColumn(format="$%.2f"),
                "ราคาล่าสุด": st.column_config.NumberColumn(format="$%.2f"),
                "มูลค่า": st.column_config.NumberColumn(format="$%,.2f"),
                "สัดส่วน %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f%%"),
                "กำไร/ขาดทุน $": st.column_config.NumberColumn(format="$%+,.2f"),
                "กำไร/ขาดทุน %": st.column_config.NumberColumn(format="%+.1f%%"),
                "วันนี้ %": st.column_config.NumberColumn(format="%+.2f%%"),
                "ปันผลรับ $": st.column_config.NumberColumn(format="$%.2f"),
                "MOS %": st.column_config.NumberColumn(format="%+.0f%%",
                                                       help="จาก Screener ล่าสุด (เฉพาะหุ้นใน S&P 500)")})

            g1, g2 = st.columns(2)
            alloc = pd.DataFrame([{"Symbol": r["Symbol"], "มูลค่า": r["มูลค่า"]} for r in rows_h])
            fig_a = go.Figure(go.Pie(labels=alloc["Symbol"], values=alloc["มูลค่า"], hole=0.5, textinfo="label+percent"))
            fig_a.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), title="สัดส่วนตามหุ้น", showlegend=False)
            g1.plotly_chart(fig_a, width="stretch")
            sect = {}
            for r in rows_h:
                s_name = None
                if sc_map is not None and r["Symbol"] in sc_map.index:
                    s_name = sc_map.loc[r["Symbol"]].get("sector")  # GICS จาก Screener
                if not isinstance(s_name, str) or not s_name:
                    try:
                        inf = get_fundamentals(r["Symbol"])["info"]
                        s_name = inf.get("sector") or (
                            "ETF/กองทุน" if str(inf.get("quoteType", "")).upper() in ("ETF", "MUTUALFUND") else "อื่นๆ")
                    except Exception:
                        s_name = "อื่นๆ"
                sect[s_name] = sect.get(s_name, 0) + r["มูลค่า"]
            fig_s = go.Figure(go.Pie(labels=list(sect), values=list(sect.values()), hole=0.5, textinfo="label+percent"))
            fig_s.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), title="สัดส่วนตาม Sector", showlegend=False)
            g2.plotly_chart(fig_s, width="stretch")

            try:
                spy = history_since("SPY", min(t["date"] for t in txs))["Close"]
            except Exception:
                spy = None
            vh = pf.value_history(txs, {tk: h["Close"] for tk, h in hist.items() if not h.empty}, spy)
            if vh.empty or len(vh) < 2:
                st.info("📈 กราฟเทียบกับ S&P 500 จะแสดงเมื่อมีราคาหลังวันที่ซื้ออย่างน้อย 2 วันทำการ "
                        "— ถ้าเพิ่งซื้อ รอวันทำการถัดไป · ถ้าลงวันที่ผิดเป็นวันนี้ ให้แก้วันที่ในประวัติรายการ (✏️)")
            else:
                if "benchmark" in vh and vh["benchmark"].iloc[-1]:
                    pv, bv = float(vh["value"].iloc[-1]), float(vh["benchmark"].iloc[-1])
                    diff = pv - bv
                    st.markdown(esc_dollar(f"**เทียบกับ S&P 500:** พอร์ต ${pv:,.2f} vs ถ้าซื้อ SPY แทน ${bv:,.2f} → "
                                + (f"🟢 **ชนะตลาด ${diff:,.2f}** ({pv / bv - 1:+.1%})" if diff >= 0
                                   else f"🔴 **แพ้ตลาด ${-diff:,.2f}** ({pv / bv - 1:+.1%})")
                                + f" · ตั้งแต่ {vh.index[0]:%d/%m/%Y} (เริ่มเทียบจากราคาปิดวันที่ซื้อ)"))
                st.markdown("**มูลค่าพอร์ตเทียบเงินลงทุนและ S&P 500**")
                fig_v = go.Figure()
                fig_v.add_trace(go.Scatter(x=vh.index, y=vh["value"], name="มูลค่าพอร์ต", line=dict(color="#3b82f6", width=2)))
                fig_v.add_trace(go.Scatter(x=vh.index, y=vh["invested"], name="เงินลงทุนสุทธิ",
                                           line=dict(color="#94a3b8", dash="dash")))
                if "benchmark" in vh:
                    fig_v.add_trace(go.Scatter(x=vh.index, y=vh["benchmark"], name="ถ้าซื้อ S&P 500 (SPY) แทน",
                                               line=dict(color="#f97316", width=1.5)))
                fig_v.update_layout(height=380, hovermode="x unified", margin=dict(l=10, r=10, t=10, b=10),
                                    yaxis_title="USD", legend=dict(orientation="h", y=-0.15))
                fig_v.update_xaxes(tickformat="%d/%m/%y", hoverformat="%d/%m/%Y",
                                   dtick=86400000 if (vh.index[-1] - vh.index[0]).days <= 14 else None)
                st.plotly_chart(fig_v, width="stretch")
                st.caption("เส้นส้ม = ถ้าซื้อ/ขาย SPY แทนในวันเดียวกัน ด้วยมูลค่าเท่ากัน (คิดจากราคาปิดของวันนั้น ทั้งสองเส้นจึงเริ่มจุดเดียวกัน) "
                           "· เส้นประ = เงินที่จ่ายจริง ถ้าซื้อระหว่างวันแพงกว่าราคาปิด เส้นประจะอยู่สูงกว่าตั้งแต่วันแรก · ไม่รวมปันผล")
        elif txs:
            st.info("ขายหุ้นออกหมดแล้ว — ดูกำไรที่ขายแล้วด้านบน")
        else:
            st.info("ยังไม่มีรายการ — เพิ่มรายการซื้อด้านล่าง หรือนำเข้าจากไฟล์ CSV")

        # ---- เพิ่มรายการ ----
        with st.expander("➕ บันทึกการซื้อ/ขาย" + ("" if combined else f" — {cur['name']}"),
                         expanded=not txs and not combined):
          if combined:
            st.info("เลือกพอร์ตที่ต้องการด้านบนก่อนบันทึกรายการ")
          else:
            c1, c2, c3 = st.columns(3)
            t_kind = c1.radio("ประเภท", ["ซื้อ", "ขาย"], horizontal=True, key="tx_kind")
            t_date = date_picker(c2, "tx")
            opts = sorted(set(wl) | set(pos))
            t_tk = c3.selectbox("หุ้น", opts + ["อื่นๆ (พิมพ์เอง)"], key="tx_tk")
            if t_tk == "อื่นๆ (พิมพ์เอง)":
                t_tk = st.text_input("Symbol", key="tx_tk_custom", placeholder="เช่น COST").strip().upper()
            ref_px = close_on(t_tk, t_date) if t_tk else None
            c4, c5, c6 = st.columns(3)
            t_sh = c4.number_input("จำนวนหุ้น", min_value=0.0, value=0.0, step=1.0, format="%g", key="tx_sh",
                                   help="ใส่ทศนิยมได้ ถ้าซื้อเศษหุ้น (fractional)")
            t_px = c5.number_input("ราคาต่อหุ้น ($)", min_value=0.0, value=float(round(ref_px or 0, 2)), step=0.01,
                                   format="%.2f", key=f"tx_px_{t_tk}_{t_date}",
                                   help="ค่าเริ่มต้น = ราคาปิดของวันนั้น · แก้เป็นราคาที่ซื้อขายจริง")
            t_fee = c6.number_input("ค่าธรรมเนียม ($)", min_value=0.0, value=0.0, step=0.01, format="%.2f", key="tx_fee")
            t_note = st.text_input("บันทึก (ไม่บังคับ)", key="tx_note", placeholder="เช่น ซื้อตามแจ้งเตือน MOS 25%")
            if t_tk and t_sh and t_px:
                st.caption(f"รวม {'จ่าย' if t_kind == 'ซื้อ' else 'ได้รับ'} ≈ "
                           f"${t_sh * t_px + (t_fee if t_kind == 'ซื้อ' else -t_fee):,.2f}")
            if st.button("💾 บันทึกรายการ", key="tx_save", disabled=not (t_tk and t_sh > 0 and t_px > 0)):
                tx = pf.new_tx(t_date, t_tk, "buy" if t_kind == "ซื้อ" else "sell", t_sh, t_px, t_fee, t_note)
                err = pf.validate_sell(txs, tx)
                if err:
                    st.error(err)
                else:
                    txs.append(tx)
                    if save_portfolio():
                        st.toast(f"บันทึก{t_kind} {t_tk} แล้ว", icon="💼")
                        st.rerun()

        # ---- รายการทั้งหมด ----
        if txs:
            with st.expander(f"📜 ประวัติรายการ ({len(txs)})"):
                owner = {t["id"]: p["name"] for p in ports for t in p["txs"]}
                for t in sorted(txs, key=lambda x: (x["date"], x.get("created", "")), reverse=True):
                    c1, c2, c3 = st.columns([6, 1, 1])
                    c1.markdown(esc_dollar(f"{t['date']} · **{'🟢 ซื้อ' if t['kind'] == 'buy' else '🔴 ขาย'} {t['ticker']}** "
                                + (f"[{md_safe(owner.get(t['id'], ''))}] " if combined else "")
                                + f"{t['shares']:g} หุ้น @ ${t['price']:,.2f}"
                                + (f" · ค่าธรรมเนียม ${t['fee']:,.2f}" if t["fee"] else "")
                                + (f"  \n📝 {md_safe(t['note'])}" if t.get("note") else "")))
                    if combined:
                        continue
                    if c2.button("✏️", key=f"tx_edit_{t['id']}", help="แก้ไขรายการนี้"):
                        st.session_state.tx_editing = None if st.session_state.get("tx_editing") == t["id"] else t["id"]
                        st.rerun()
                    if c3.button("🗑️", key=f"tx_del_{t['id']}", help="ลบรายการนี้"):
                        remaining = [x for x in txs if x["id"] != t["id"]]
                        err = pf.validate_all(remaining)
                        if err:
                            st.error("ลบไม่ได้: " + err)
                        else:
                            cur["txs"] = remaining
                            if save_portfolio():
                                st.rerun()
                    if st.session_state.get("tx_editing") == t["id"]:
                        with st.container(border=True):
                            st.markdown(f"**แก้ไขรายการ {t['ticker']}**")
                            e1, e2 = st.columns(2)
                            e_kind = e1.radio("ประเภท", ["ซื้อ", "ขาย"], index=0 if t["kind"] == "buy" else 1,
                                              horizontal=True, key=f"ed_kind_{t['id']}")
                            e_date = date_picker(e2, f"ed_{t['id']}", default=t["date"])
                            e3, e4, e5 = st.columns(3)
                            e_sh = e3.number_input("จำนวนหุ้น", min_value=0.0, value=float(t["shares"]), step=1.0,
                                                   format="%g", key=f"ed_sh_{t['id']}")
                            e_px = e4.number_input("ราคาต่อหุ้น ($)", min_value=0.0, value=float(t["price"]), step=0.01,
                                                   format="%.2f", key=f"ed_px_{t['id']}")
                            e_fee = e5.number_input("ค่าธรรมเนียม ($)", min_value=0.0, value=float(t["fee"]), step=0.01,
                                                    format="%.2f", key=f"ed_fee_{t['id']}")
                            e_note = st.text_input("บันทึก", value=t.get("note", ""), key=f"ed_note_{t['id']}")
                            s1, s2 = st.columns(2)
                            if s1.button("💾 บันทึกการแก้ไข", key=f"ed_save_{t['id']}", disabled=not (e_sh > 0 and e_px > 0)):
                                edited = dict(t, kind="buy" if e_kind == "ซื้อ" else "sell", date=str(e_date),
                                              shares=float(e_sh), price=float(e_px), fee=float(e_fee), note=e_note.strip())
                                new_txs = [edited if x["id"] == t["id"] else x for x in txs]
                                err = pf.validate_all(new_txs)
                                if err:
                                    st.error(err)
                                else:
                                    cur["txs"] = new_txs
                                    st.session_state.tx_editing = None
                                    if save_portfolio():
                                        st.toast("แก้ไขรายการแล้ว", icon="✏️")
                                        st.rerun()
                            if s2.button("ยกเลิก", key=f"ed_cancel_{t['id']}"):
                                st.session_state.tx_editing = None
                                st.rerun()
            if div_total and not divs.empty:
                with st.expander(f"💵 ปันผลที่ควรได้รับ ({len(divs)} ครั้ง)"):
                    st.dataframe(divs.sort_values("ex_date", ascending=False), hide_index=True, width="stretch",
                                 column_config={"ex_date": "วัน XD", "per_share": st.column_config.NumberColumn("ต่อหุ้น", format="$%.4f"),
                                                "shares": st.column_config.NumberColumn("หุ้น", format="%g"),
                                                "gross": st.column_config.NumberColumn("ก่อนภาษี", format="$%.2f"),
                                                "net": st.column_config.NumberColumn("สุทธิ", format="$%.2f")})
                    st.caption("คำนวณจากประวัติปันผลของ Yahoo × จำนวนหุ้นที่ถือก่อนวัน XD — ตรวจกับรายงานของโบรกเกอร์")

        # ---- สำรอง / นำเข้า ----
        with st.expander("💾 สำรองข้อมูล / นำเข้า CSV"):
            st.caption("แนะนำให้คัดลอกข้อมูลสำรองเก็บไว้ (เช่นใน Notes) เป็นระยะ — ถ้าลืมรหัสผ่านจะได้มีสำเนา")
            for p in (ports if combined else [cur]):
                st.markdown(f"**{md_safe(p['name'])}** ({len(p['txs'])} รายการ)")
                st.code(pf.to_csv(p["txs"]), language=None)
            up = None if combined else st.file_uploader(
                f"นำเข้าไฟล์ CSV เข้าพอร์ต \"{cur['name']}\" (คอลัมน์: date, ticker, kind, shares, price, fee, note)",
                type=["csv"], key="port_csv")
            if up is not None and st.button("นำเข้า (เพิ่มต่อจากรายการเดิม)", key="port_import"):
                try:
                    new = pf.from_csv(up.getvalue().decode("utf-8-sig"))
                    cur["txs"] = txs + new
                    if save_portfolio():
                        st.toast(f"นำเข้า {len(new)} รายการแล้ว", icon="✅")
                        st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"นำเข้าไม่สำเร็จ: {e}")

        with st.expander("📘 วิธีอ่านพอร์ตและดูแลพอร์ตแบบ Buffett"):
            st.markdown(guides.PORTFOLIO)


# ================= คู่มือ =================
with tab_manual:
    manual = Path(__file__).with_name("MANUAL.md")
    st.markdown(esc_dollar(manual.read_text(encoding="utf-8")) if manual.exists() else "ไม่พบไฟล์คู่มือ (MANUAL.md)")

