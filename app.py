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
if "buffett" in sys.modules:
    importlib.reload(sys.modules["buffett"])
from buffett import (BASIS_OE, buffett_checks, build_buffett_indicator, current_price, dcf_options, default_growth,
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

tab_chart, tab_overview, tab_compare, tab_buffett, tab_screener = st.tabs(
    ["📈 กราฟ", "📋 ภาพรวม", "⚖️ เปรียบเทียบ", "🎩 Buffett", "🔍 Screener"])

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


# ================= Buffett =================
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_fundamentals(ticker: str) -> dict:
    return fetch_fundamentals(ticker)


BI_FILE = Path(__file__).with_name("buffett_indicator.csv")


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_buffett_indicator(mtime: float) -> tuple[pd.Series, str]:
    """ใช้ไฟล์ที่ GitHub Actions บันทึกไว้ทุกสัปดาห์ (FRED มักบล็อก/ช้าเมื่อเรียกจาก Streamlit Cloud)
    ถ้ายังไม่มีไฟล์ค่อยดึงสดจาก FRED"""
    if BI_FILE.exists():
        df = pd.read_csv(BI_FILE, parse_dates=["date"]).set_index("date")
        return df["ratio"], "ไฟล์ที่บันทึกทุกสัปดาห์"
    return build_buffett_indicator(timeout=15, tries=1)["ratio"], "FRED (ดึงสด)"


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

        table = pd.DataFrame(rows).drop(columns="_s").set_index("หลักเกณฑ์")
        st.dataframe(table, width="stretch")

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
    st.markdown("### 🇺🇸 Buffett Indicator (มูลค่าตลาดหุ้นรวม ÷ GDP)")
    try:
        with st.spinner("กำลังโหลดข้อมูล Buffett Indicator…"):
            bi, bi_src = load_buffett_indicator(BI_FILE.stat().st_mtime if BI_FILE.exists() else 0.0)
        last_date = bi.index[-1]
        q_end = last_date + pd.offsets.QuarterEnd(0)
        vti = get_history("VTI", "2y", "1d", False)["Close"]
        vti.index = vti.index.tz_localize(None)
        at_qend = vti[vti.index <= q_end]
        est = float(bi.iloc[-1] * vti.iloc[-1] / at_qend.iloc[-1]) if len(at_qend) else float(bi.iloc[-1])
        pct = (bi < est).mean() * 100
        mean, sd = bi.mean(), bi.std()

        if est < 75:
            zone = "🟢 ถูกมาก (Significantly undervalued)"
        elif est < 90:
            zone = "🟢 ค่อนข้างถูก (Modestly undervalued)"
        elif est < 115:
            zone = "🟡 ราคาเหมาะสม (Fair value)"
        elif est < 135:
            zone = "🟠 ค่อนข้างแพง (Modestly overvalued)"
        else:
            zone = "🔴 แพงมาก (Significantly overvalued)"

        k1, k2, k3 = st.columns(3)
        k1.metric("ประมาณการปัจจุบัน", f"{est:.0f}%",
                  f"{est - bi.iloc[-1]:+.0f} จุด จากไตรมาสล่าสุด", delta_color="off")
        k2.metric(f"ข้อมูลทางการล่าสุด ({q_end:%b %Y})", f"{bi.iloc[-1]:.0f}%")
        k3.metric("สูงกว่าประวัติศาสตร์", f"{pct:.0f}% ของทุกไตรมาส",
                  help=f"เทียบกับข้อมูลตั้งแต่ {bi.index[0]:%Y}")
        st.markdown(f"**เกณฑ์ดั้งเดิมของ Buffett: {zone}**")

        figb = go.Figure()
        figb.add_trace(go.Scatter(x=bi.index, y=bi.values, mode="lines", name="Buffett Indicator",
                                  line=dict(color="#3b82f6", width=2)))
        figb.add_trace(go.Scatter(x=[pd.Timestamp.today()], y=[est], mode="markers", name="ประมาณการวันนี้",
                                  marker=dict(color="#ef4444", size=10)))
        for y, label, dash in ((mean, "ค่าเฉลี่ย", "solid"), (mean + sd, "+1 SD", "dash"),
                               (mean + 2 * sd, "+2 SD", "dot"), (mean - sd, "-1 SD", "dash")):
            figb.add_hline(y=y, line_dash=dash, line_color="#94a3b8", opacity=0.7,
                           annotation_text=f"{label} {y:.0f}%", annotation_position="top left")
        figb.update_layout(height=420, yaxis_title="% ของ GDP", hovermode="x unified",
                           margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.05))
        st.plotly_chart(figb, width="stretch")
        st.caption(
            f"ข้อมูลจาก{bi_src} · ที่มา: FRED — Corporate equities ของบริษัทนอกภาคการเงิน (NCBEILQ027S) ÷ GDP · ข้อมูลรายไตรมาส "
            "ประกาศช้า ~3 เดือน ค่าปัจจุบันจึงประมาณจากการเปลี่ยนแปลงราคา VTI (ทั้งตลาด) หลังสิ้นไตรมาส · "
            "เกณฑ์ 75/90/115/135% มาจาก Buffett ปี 2001 แต่ค่านี้มีแนวโน้มสูงขึ้นตามเวลา "
            "(บริษัทมีรายได้ต่างประเทศมากขึ้น ดอกเบี้ยต่ำลง) จึงควรดูประกอบกับค่าเฉลี่ยและ SD")
    except Exception as e:  # noqa: BLE001
        st.warning(f"โหลดข้อมูล Buffett Indicator ไม่สำเร็จ ({type(e).__name__}) — ไปที่ GitHub → Actions → "
                   "Run workflow เพื่อบันทึกข้อมูลลง repo แล้วรอ 15 นาที")


# ================= Screener (S&P 500) =================
SCREENER_FILE = Path(__file__).with_name("screener.csv")


@st.cache_data(ttl=600, show_spinner=False)
def load_screener(mtime: float) -> pd.DataFrame:  # mtime: โหลดใหม่เมื่อไฟล์เปลี่ยน
    return pd.read_csv(SCREENER_FILE)


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

        c1, c2, c3 = st.columns([1.2, 1.2, 2])
        min_score = c1.slider("คะแนนขั้นต่ำ (% ข้อที่ผ่าน)", 0, 100, 70, 5, key="sc_min")
        min_mos = c2.slider("Margin of safety ขั้นต่ำ", -100, 80, 0, 5, format="%d%%", key="sc_mos",
                            help="0% = ราคาไม่เกินมูลค่าที่แท้จริง · 25% = ตามเกณฑ์ Buffett · ซ้ายสุด = ไม่กรอง")
        sectors = sorted(sc["sector"].dropna().unique())
        pick_sec = c3.multiselect("Sector", sectors, key="sc_sector", placeholder="ทุก sector")
        query = st.text_input("ค้นหาชื่อหรือ symbol", key="sc_q", placeholder="เช่น Apple, KO")

        view = sc[sc["score_pct"] * 100 >= min_score]
        if min_mos > -100:  # ซ้ายสุด = ไม่กรอง MOS (รวมตัวที่คำนวณ DCF ไม่ได้)
            fin = (view["financial"].astype(str).str.lower() == "true") if "financial" in view.columns \
                else (view["sector"] == "Financials")
            view = view[(view["margin_of_safety"].fillna(-9) * 100 >= min_mos) | fin]  # การเงินไม่มี MOS ไม่ตัดออก
        if pick_sec:
            view = view[view["sector"].isin(pick_sec)]
        if query:
            q = query.strip().lower()
            view = view[view["ticker"].str.lower().str.contains(q, regex=False)
                        | view["name"].str.lower().str.contains(q, regex=False)]

        st.markdown(f"**พบ {len(view)} บริษัท**")
        pct_cols = ["score_pct", "margin_of_safety", "oe_yield", "roe", "gross_margin", "net_margin"]
        show = view.copy()
        show[pct_cols] = show[pct_cols] * 100
        show["score_txt"] = show["score"].astype(int).astype(str) + "/" + show["applicable"].astype(int).astype(str)
        if "price_to_book" not in show.columns:
            show["price_to_book"] = None
        show = show[["ticker", "name", "sector", "score_txt", "score_pct", "price", "intrinsic_value",
                     "margin_of_safety", "price_to_book", "oe_yield", "roe", "gross_margin", "net_margin", "debt_years", "failed"]]
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
