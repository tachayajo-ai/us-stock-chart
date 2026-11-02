"""US Stock & ETF Chart — Streamlit + yfinance
รัน:  streamlit run app.py
"""
import base64
import json
from datetime import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st
import yfinance as yf

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


def _gh_request(method: str, body: dict | None = None):
    token, repo = _github_cfg()
    return requests.request(
        method,
        f"https://api.github.com/repos/{repo}/contents/watchlist.json",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json=body, timeout=15,
    )


def _clean(data) -> list[str] | None:
    if isinstance(data, list):
        return [str(t).upper() for t in data]
    return None


def load_watchlist() -> list[str]:
    if _github_cfg():
        try:
            r = _gh_request("GET")
            if r.status_code == 200:
                st.session_state.wl_sha = r.json()["sha"]
                data = _clean(json.loads(base64.b64decode(r.json()["content"])))
                if data is not None:
                    return data
        except Exception:
            pass
        return DEFAULT_WATCHLIST.copy()
    try:
        data = _clean(json.loads(WATCHLIST_FILE.read_text()))
        if data is not None:
            return data
    except Exception:
        pass
    return DEFAULT_WATCHLIST.copy()


def save_watchlist(tickers: list[str]) -> None:
    text = json.dumps(tickers, indent=2)
    if _github_cfg():
        body = {"message": "Update watchlist",
                "content": base64.b64encode(text.encode()).decode()}
        if st.session_state.get("wl_sha"):
            body["sha"] = st.session_state.wl_sha
        r = _gh_request("PUT", body)
        if r.status_code == 409 or r.status_code == 422:  # sha เก่า: ดึงใหม่แล้วลองอีกครั้ง
            g = _gh_request("GET")
            if g.status_code == 200:
                body["sha"] = g.json()["sha"]
                r = _gh_request("PUT", body)
        if r.status_code in (200, 201):
            st.session_state.wl_sha = r.json()["content"]["sha"]
        else:
            st.toast(f"บันทึก watchlist ไม่สำเร็จ (GitHub {r.status_code})", icon="⚠️")
        return
    WATCHLIST_FILE.write_text(text)


if "watchlist" not in st.session_state:
    st.session_state.watchlist = load_watchlist()


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
        if c2.button("✕", key=f"del_{t}", help=f"ลบ {t}"):
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

tab_chart, tab_overview, tab_compare = st.tabs(["📈 กราฟ", "📋 ภาพรวม", "⚖️ เปรียบเทียบ"])

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
