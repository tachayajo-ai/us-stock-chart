"""US Stock & ETF Chart — Streamlit + yfinance
รัน:  streamlit run app.py
"""
import base64
import io
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

tab_chart, tab_overview, tab_compare, tab_buffett = st.tabs(
    ["📈 กราฟ", "📋 ภาพรวม", "⚖️ เปรียบเทียบ", "🎩 Buffett"])

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
    t = yf.Ticker(ticker)
    try:
        info = t.info or {}
    except Exception:
        info = {}

    def clean(df):
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.T.sort_index()  # แถว = ปี (เก่า -> ใหม่)
        df.index = pd.to_datetime(df.index).year
        return df[~df.index.duplicated(keep="last")]

    out = {"info": info}
    for key, attr in (("inc", "income_stmt"), ("bal", "balance_sheet"), ("cf", "cashflow")):
        try:
            out[key] = clean(getattr(t, attr))
        except Exception:
            out[key] = pd.DataFrame()
    return out


def _col(df: pd.DataFrame, *names):
    for n in names:
        if n in df.columns:
            s = pd.to_numeric(df[n], errors="coerce").dropna()
            if len(s):
                return s
    return None


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fred_series(series_id: str) -> pd.Series:
    r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}",
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna().set_index("date")["value"]


def dcf_value(fcf0: float, growth: float, discount: float, terminal: float, years: int = 10) -> float:
    """มูลค่าปัจจุบันของ FCF 10 ปี + Terminal value (Gordon growth)"""
    pv, f = 0.0, fcf0
    for y in range(1, years + 1):
        f *= 1 + growth
        pv += f / (1 + discount) ** y
    tv = f * (1 + terminal) / (discount - terminal)
    return pv + tv / (1 + discount) ** years


def fmt_money(v: float) -> str:
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            return f"${v / div:,.1f}{suf}"
    return f"${v:,.0f}"


def owner_earnings(f: dict) -> pd.DataFrame | None:
    """Owner Earnings (Buffett, จดหมายผู้ถือหุ้น 1986)
    = กำไรสุทธิ + ค่าเสื่อม/ค่าตัดจำหน่าย ± การเปลี่ยนแปลง working capital − Maintenance capex
    Maintenance capex ประมาณแบบ Greenwald: capex ทั้งหมด − growth capex
    โดย growth capex = (Net PPE ÷ รายได้ เฉลี่ย) × รายได้ที่เพิ่มขึ้นในปีนั้น"""
    inc, bal, cf = f["inc"], f["bal"], f["cf"]
    ni = _col(inc, "Net Income", "Net Income Common Stockholders")
    da = _col(cf, "Depreciation And Amortization", "Depreciation Amortization Depletion",
              "Depreciation")
    capex = _col(cf, "Capital Expenditure")
    if ni is None or da is None or capex is None:
        return None
    wc = _col(cf, "Change In Working Capital")
    rev = _col(inc, "Total Revenue", "Operating Revenue")
    ppe = _col(bal, "Net PPE")
    df = pd.concat({"ni": ni, "da": da, "capex": capex.abs()}, axis=1, join="inner").dropna()
    if df.empty:
        return None
    df["wc"] = wc.reindex(df.index).fillna(0) if wc is not None else 0.0
    df["growth_capex"] = 0.0
    if rev is not None and ppe is not None:
        ppe_to_sales = (ppe / rev).dropna().mean()
        d_rev = rev.diff().clip(lower=0).reindex(df.index).fillna(0)
        if pd.notna(ppe_to_sales):
            df["growth_capex"] = (ppe_to_sales * d_rev).clip(upper=df["capex"])
    df["maint_capex"] = df["capex"] - df["growth_capex"]
    df["oe"] = df["ni"] + df["da"] + df["wc"] - df["maint_capex"]
    return df


def buffett_checks(f: dict) -> tuple[list[dict], dict]:
    inc, bal, cf = f["inc"], f["bal"], f["cf"]
    ni = _col(inc, "Net Income", "Net Income Common Stockholders")
    rev = _col(inc, "Total Revenue", "Operating Revenue")
    gp = _col(inc, "Gross Profit")
    eps = _col(inc, "Diluted EPS", "Basic EPS")
    eq = _col(bal, "Stockholders Equity", "Common Stock Equity")
    ltd = _col(bal, "Long Term Debt", "Long Term Debt And Capital Lease Obligation")
    fcf = _col(cf, "Free Cash Flow")
    capex = _col(cf, "Capital Expenditure")
    shares = _col(bal, "Ordinary Shares Number", "Share Issued")
    rows = []

    def add(name, rule, value, status, why):
        rows.append({"หลักเกณฑ์": name, "เกณฑ์ผ่าน": rule, "ค่าจริง": value,
                     "ผล": {"pass": "✅ ผ่าน", "fail": "❌ ไม่ผ่าน", "na": "➖ N/A"}[status],
                     "ทำไมสำคัญ": why, "_s": status})

    # 1 ROE
    if ni is not None and eq is not None:
        both = pd.concat([ni, eq], axis=1, join="inner").dropna()
        if len(both) and (both.iloc[:, 1] > 0).all():
            roe = (both.iloc[:, 0] / both.iloc[:, 1]).mean()
            add("ROE เฉลี่ย", "≥ 15%", f"{roe:.1%}", "pass" if roe >= 0.15 else "fail",
                "ใช้เงินผู้ถือหุ้นสร้างกำไรได้ดี")
        else:
            add("ROE เฉลี่ย", "≥ 15%", "ส่วนผู้ถือหุ้นติดลบ", "na", "ROE ใช้ไม่ได้เมื่อ equity ติดลบ (มักเกิดจากซื้อหุ้นคืนมาก)")
    else:
        add("ROE เฉลี่ย", "≥ 15%", "ไม่มีข้อมูล", "na", "ใช้เงินผู้ถือหุ้นสร้างกำไรได้ดี")

    # 2 Debt payoff years
    if ni is not None:
        debt = float(ltd.iloc[-1]) if ltd is not None else 0.0
        last_ni = float(ni.iloc[-1])
        if last_ni > 0:
            yrs = debt / last_ni
            add("หนี้ระยะยาว / กำไรสุทธิ", "≤ 4 ปี",
                f"{yrs:.1f} ปี" if ltd is not None else "ไม่พบหนี้ระยะยาวในงบ", "pass" if yrs <= 4 else "fail",
                "ใช้กำไรปิดหนี้ระยะยาวได้ภายใน 3-4 ปี")
        else:
            add("หนี้ระยะยาว / กำไรสุทธิ", "≤ 4 ปี", "ขาดทุน", "fail", "ใช้กำไรปิดหนี้ระยะยาวได้ภายใน 3-4 ปี")
    else:
        add("หนี้ระยะยาว / กำไรสุทธิ", "≤ 4 ปี", "ไม่มีข้อมูล", "na", "ใช้กำไรปิดหนี้ระยะยาวได้ภายใน 3-4 ปี")

    # 3 Gross margin
    if gp is not None and rev is not None:
        gm = (gp / rev).dropna().mean()
        add("Gross margin เฉลี่ย", "≥ 40%", f"{gm:.1%}", "pass" if gm >= 0.40 else "fail",
            "สัญญาณของความได้เปรียบทางการแข่งขัน (moat) ตั้งราคาได้")
    else:
        add("Gross margin เฉลี่ย", "≥ 40%", "ไม่มีข้อมูล (เช่น ธนาคาร)", "na",
            "สัญญาณของความได้เปรียบทางการแข่งขัน (moat) ตั้งราคาได้")

    # 4 Net margin
    if ni is not None and rev is not None:
        nm = (ni / rev).dropna().mean()
        add("Net margin เฉลี่ย", "≥ 20%", f"{nm:.1%}", "pass" if nm >= 0.20 else "fail",
            "เหลือเป็นกำไรจริงมาก ต้นทุนแฝงต่ำ")
    else:
        add("Net margin เฉลี่ย", "≥ 20%", "ไม่มีข้อมูล", "na", "เหลือเป็นกำไรจริงมาก ต้นทุนแฝงต่ำ")

    # 5 Consistent earnings
    if eps is not None and ni is not None and len(eps) >= 2:
        ok = (ni > 0).all() and eps.iloc[-1] > eps.iloc[0]
        add("กำไรสม่ำเสมอและโตขึ้น", "ไม่ขาดทุน + EPS ล่าสุด > ปีแรก",
            f"EPS {eps.iloc[0]:.2f} → {eps.iloc[-1]:.2f} ({eps.index[0]}–{eps.index[-1]})",
            "pass" if ok else "fail", "ธุรกิจคาดการณ์ได้ ไม่ผันผวน")
    else:
        add("กำไรสม่ำเสมอและโตขึ้น", "ไม่ขาดทุน + EPS ล่าสุด > ปีแรก", "ไม่มีข้อมูล", "na",
            "ธุรกิจคาดการณ์ได้ ไม่ผันผวน")

    # 6 FCF positive
    if fcf is not None:
        add("Free cash flow เป็นบวกทุกปี", "FCF > 0 ทุกปี",
            ", ".join(f"{y}: {fmt_money(v)}" for y, v in fcf.items()),
            "pass" if (fcf > 0).all() else "fail", "กำไรเป็นเงินสดจริง ไม่ใช่แค่ตัวเลขบัญชี")
    else:
        add("Free cash flow เป็นบวกทุกปี", "FCF > 0 ทุกปี", "ไม่มีข้อมูล", "na",
            "กำไรเป็นเงินสดจริง ไม่ใช่แค่ตัวเลขบัญชี")

    # 7 Capex / NI
    if capex is not None and ni is not None:
        both = pd.concat([capex.abs(), ni], axis=1, join="inner").dropna()
        both = both[both.iloc[:, 1] > 0]
        if len(both):
            ratio = (both.iloc[:, 0] / both.iloc[:, 1]).mean()
            add("Capex / กำไรสุทธิ", "≤ 50%", f"{ratio:.0%}", "pass" if ratio <= 0.5 else "fail",
                "ไม่ต้องลงทุนซ้ำมากเพื่อรักษาธุรกิจ")
        else:
            add("Capex / กำไรสุทธิ", "≤ 50%", "ขาดทุน", "fail", "ไม่ต้องลงทุนซ้ำมากเพื่อรักษาธุรกิจ")
    else:
        add("Capex / กำไรสุทธิ", "≤ 50%", "ไม่มีข้อมูล", "na", "ไม่ต้องลงทุนซ้ำมากเพื่อรักษาธุรกิจ")

    # 8 Share count
    if shares is not None and len(shares) >= 2:
        chg = shares.iloc[-1] / shares.iloc[0] - 1
        add("จำนวนหุ้นลดลง (ซื้อหุ้นคืน)", "หุ้นไม่เพิ่มขึ้น", f"{chg:+.1%} ({shares.index[0]}–{shares.index[-1]})",
            "pass" if chg <= 0 else "fail", "ผู้ถือหุ้นเดิมไม่ถูก dilute")
    else:
        add("จำนวนหุ้นลดลง (ซื้อหุ้นคืน)", "หุ้นไม่เพิ่มขึ้น", "ไม่มีข้อมูล", "na", "ผู้ถือหุ้นเดิมไม่ถูก dilute")

    # ค่าเริ่มต้น DCF
    extra = {"fcf": fcf}
    if fcf is not None and len(fcf):
        extra["fcf_base"] = float(fcf.tail(3).mean())
        pos = fcf[fcf > 0]
        if len(pos) >= 2 and pos.index[-1] > pos.index[0]:
            n = pos.index[-1] - pos.index[0]
            extra["fcf_cagr"] = (pos.iloc[-1] / pos.iloc[0]) ** (1 / n) - 1
    extra["ni"], extra["rev"] = ni, rev
    oe = owner_earnings(f)
    extra["oe_table"] = oe
    if oe is not None:
        o = oe["oe"]
        extra["oe"] = o
        extra["oe_base"] = float(o.tail(3).mean())
        pos = o[o > 0]
        if len(pos) >= 2 and pos.index[-1] > pos.index[0]:
            n = pos.index[-1] - pos.index[0]
            extra["oe_cagr"] = (pos.iloc[-1] / pos.iloc[0]) ** (1 / n) - 1
        rows.append({"หลักเกณฑ์": "Owner earnings เป็นบวกทุกปี", "เกณฑ์ผ่าน": "OE > 0 ทุกปี",
                     "ค่าจริง": ", ".join(f"{y}: {fmt_money(v)}" for y, v in o.items()),
                     "ผล": "✅ ผ่าน" if (o > 0).all() else "❌ ไม่ผ่าน",
                     "ทำไมสำคัญ": "เงินที่เจ้าของดึงออกได้จริงโดยธุรกิจไม่เสียความสามารถในการแข่งขัน",
                     "_s": "pass" if (o > 0).all() else "fail"})
    return rows, extra


with tab_buffett:
    st.markdown("### 🎩 Buffett checklist รายตัว")
    b_ticker = st.selectbox("เลือกหุ้น", wl, index=wl.index(st.session_state.selected), key="buffett_ticker")
    with st.spinner("กำลังโหลดงบการเงิน…"):
        f = get_fundamentals(b_ticker)
    info = f["info"]
    qtype = str(info.get("quoteType", "")).upper()

    if qtype in ("ETF", "MUTUALFUND", "INDEX") or f["inc"].empty:
        st.info(f"{b_ticker} เป็น {qtype or 'กองทุน/ดัชนี'} หรือไม่มีงบการเงิน — Buffett checklist ใช้ได้เฉพาะหุ้นรายบริษัท "
                "ดู Buffett Indicator ของตลาดรวมด้านล่างแทน")
    else:
        rows, extra = buffett_checks(f)
        price = info.get("currentPrice") or info.get("regularMarketPrice") or get_quote(b_ticker).get("close")
        shares_out = info.get("sharesOutstanding")

        summary = st.container()
        with st.expander("⚙️ สมมติฐานมูลค่าที่แท้จริง (DCF) — ปรับได้", expanded=False):
            options = []
            if extra.get("oe_base") is not None:
                options.append("Owner Earnings (Buffett)")
            if extra.get("fcf_base") is not None:
                options.append("Free Cash Flow")
            if not options:
                options = ["Free Cash Flow"]
            if "Owner Earnings (Buffett)" in options and extra["oe_base"] <= 0 and "Free Cash Flow" in options:
                options.reverse()  # OE ติดลบ ใช้ FCF เป็นค่าเริ่มต้น
            basis = st.radio("ฐานกระแสเงินสด", options, horizontal=True, key=f"basis_{b_ticker}")
            is_oe = basis.startswith("Owner")
            base = extra.get("oe_base" if is_oe else "fcf_base")
            default_g = float(min(max(extra.get("oe_cagr" if is_oe else "fcf_cagr", 0.05), 0.0), 0.15))
            c1, c2, c3 = st.columns(3)
            g = c1.slider("กระแสเงินสดโตปีละ (10 ปีแรก)", 0.0, 0.25,
                          round(round(default_g / 0.005) * 0.005, 3), 0.005, format="%.3f",
                          key=f"g_{b_ticker}_{basis}")
            dr = c2.slider("Discount rate (ผลตอบแทนที่ต้องการ)", 0.06, 0.15, 0.10, 0.005, format="%.3f",
                           key=f"dr_{b_ticker}")
            tg = c3.slider("Terminal growth", 0.0, 0.04, 0.025, 0.005, format="%.3f", key=f"tg_{b_ticker}")
            st.caption(
                f"ฐาน {basis} = ค่าเฉลี่ย 3 ปีล่าสุด {fmt_money(base) if base else '—'} · "
                "ค่าเริ่มต้นของการเติบโต = CAGR ในอดีต (จำกัด 0–15%) · "
                "สูตร: PV ของกระแสเงินสด 10 ปี + Terminal value ÷ จำนวนหุ้น · 0.10 = 10%")

        iv = None
        if base and base > 0 and shares_out and dr > tg:
            iv = dcf_value(base, g, dr, tg) / shares_out
        if iv and price:
            mos = 1 - price / iv
            rows.append({"หลักเกณฑ์": "ราคาต่ำกว่ามูลค่าที่แท้จริง", "เกณฑ์ผ่าน": "Margin of safety ≥ 25%",
                         "ค่าจริง": f"ราคา ${price:,.2f} vs มูลค่า ${iv:,.2f} ({mos:+.0%}) · ฐาน {basis}",
                         "ผล": "✅ ผ่าน" if mos >= 0.25 else "❌ ไม่ผ่าน",
                         "ทำไมสำคัญ": "ซื้อของดีในราคาที่มีส่วนเผื่อความผิดพลาด", "_s": "pass" if mos >= 0.25 else "fail"})
        else:
            mos = None
            rows.append({"หลักเกณฑ์": "ราคาต่ำกว่ามูลค่าที่แท้จริง", "เกณฑ์ผ่าน": "Margin of safety ≥ 25%",
                         "ค่าจริง": f"คำนวณไม่ได้ ({basis} ติดลบ/ไม่มีข้อมูล)", "ผล": "➖ N/A",
                         "ทำไมสำคัญ": "ซื้อของดีในราคาที่มีส่วนเผื่อความผิดพลาด", "_s": "na"})

        # Owner earnings yield เทียบพันธบัตรรัฐบาลสหรัฐ 10 ปี
        oe_y = None
        if extra.get("oe_base") is not None and shares_out and price:
            oe_y = extra["oe_base"] / (shares_out * price)
            try:
                tnx = float(get_history("^TNX", "5d", "1d", False)["Close"].iloc[-1]) / 100
            except Exception:
                tnx = None
            if tnx:
                ok = oe_y >= tnx
                rows.append({"หลักเกณฑ์": "Owner earnings yield vs พันธบัตร 10 ปี",
                             "เกณฑ์ผ่าน": "OE yield ≥ ผลตอบแทนพันธบัตร",
                             "ค่าจริง": f"{oe_y:.1%} vs Treasury 10Y {tnx:.2%}",
                             "ผล": "✅ ผ่าน" if ok else "❌ ไม่ผ่าน",
                             "ทำไมสำคัญ": "ถ้าได้น้อยกว่าพันธบัตรที่ไม่มีความเสี่ยง ก็ไม่คุ้มที่จะรับความเสี่ยงหุ้น",
                             "_s": "pass" if ok else "fail"})

        passed = sum(r["_s"] == "pass" for r in rows)
        applicable = sum(r["_s"] != "na" for r in rows)
        with summary:
            st.caption(f"{info.get('shortName', b_ticker)} · {info.get('sector', '')} · "
                       f"งบการเงินรายปี {f['inc'].index.min()}–{f['inc'].index.max()} (Yahoo Finance)")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("คะแนน Buffett", f"{passed}/{applicable}",
                      help="นับเฉพาะข้อที่ประเมินได้ (ไม่รวม N/A)")
            m2.metric("ราคาปัจจุบัน", f"${price:,.2f}" if price else "—")
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
                st.info("งบไม่มีข้อมูลค่าเสื่อมราคาหรือ capex พอจะคำนวณ Owner Earnings")
            else:
                show = pd.DataFrame({
                    "กำไรสุทธิ": oet["ni"], "+ ค่าเสื่อม/ค่าตัดจำหน่าย": oet["da"],
                    "± Working capital": oet["wc"], "Capex ทั้งหมด": -oet["capex"],
                    "(Growth capex)": oet["growth_capex"], "− Maintenance capex": -oet["maint_capex"],
                    "= Owner earnings": oet["oe"],
                }).T
                show.columns = [str(c) for c in show.columns]
                st.dataframe(show.map(lambda v: fmt_money(v) if pd.notna(v) else "—"), width="stretch")
                if oe_y is not None:
                    st.caption(f"Owner earnings yield (เฉลี่ย 3 ปี ÷ มูลค่าตลาด) = {oe_y:.1%} · "
                               f"เทียบเท่า P/OE {1 / oe_y:.1f} เท่า" if oe_y > 0 else
                               f"Owner earnings yield = {oe_y:.1%}")
                st.caption("สูตร Buffett (1986): Owner earnings = กำไรสุทธิ + ค่าเสื่อม ± working capital − "
                           "Maintenance capex · Maintenance capex ประมาณแบบ Greenwald = capex ทั้งหมด − "
                           "(Net PPE ÷ รายได้) × รายได้ที่เพิ่มขึ้น · ถ้าไม่มีข้อมูล PPE จะถือว่า capex ทั้งหมดเป็น maintenance")
        st.caption("ข้อที่ระบบประเมินไม่ได้แต่ Buffett ให้ความสำคัญ: เข้าใจธุรกิจไหม · มี moat ยั่งยืนไหม · "
                   "ผู้บริหารซื่อตรงและจัดสรรเงินเก่งไหม — ต้องประเมินเอง · ไม่ใช่คำแนะนำการลงทุน")

    # ---------- Market Buffett Indicator ----------
    st.divider()
    st.markdown("### 🇺🇸 Buffett Indicator (มูลค่าตลาดหุ้นรวม ÷ GDP)")
    try:
        with st.spinner("กำลังโหลดข้อมูล FRED…"):
            mcap = fred_series("NCBEILQ027S") / 1000  # ล้าน → พันล้าน USD
            gdp = fred_series("GDP")                   # พันล้าน USD (annualized)
        bi = (pd.concat([mcap, gdp], axis=1, join="inner").dropna()
              .pipe(lambda d: d.iloc[:, 0] / d.iloc[:, 1] * 100))
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
            "ที่มา: FRED — Corporate equities ของบริษัทนอกภาคการเงิน (NCBEILQ027S) ÷ GDP · ข้อมูลรายไตรมาส "
            "ประกาศช้า ~3 เดือน ค่าปัจจุบันจึงประมาณจากการเปลี่ยนแปลงราคา VTI (ทั้งตลาด) หลังสิ้นไตรมาส · "
            "เกณฑ์ 75/90/115/135% มาจาก Buffett ปี 2001 แต่ค่านี้มีแนวโน้มสูงขึ้นตามเวลา "
            "(บริษัทมีรายได้ต่างประเทศมากขึ้น ดอกเบี้ยต่ำลง) จึงควรดูประกอบกับค่าเฉลี่ยและ SD")
    except Exception as e:  # noqa: BLE001
        st.warning(f"โหลดข้อมูล Buffett Indicator ไม่สำเร็จ ลองกด Refresh อีกครั้ง ({type(e).__name__})")
