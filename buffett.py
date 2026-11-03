"""Buffett checklist / Owner Earnings / DCF — ใช้ร่วมกันระหว่าง app.py และ screener.py"""
import pandas as pd
import yfinance as yf

def fetch_fundamentals(ticker: str) -> dict:
    """ดึง info + งบการเงินรายปีจาก Yahoo Finance"""
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
    m = {}  # ค่าตัวเลขสำหรับ screener

    def add(name, rule, value, status, why):
        rows.append({"หลักเกณฑ์": name, "เกณฑ์ผ่าน": rule, "ค่าจริง": value,
                     "ผล": {"pass": "✅ ผ่าน", "fail": "❌ ไม่ผ่าน", "na": "➖ N/A"}[status],
                     "ทำไมสำคัญ": why, "_s": status})

    # 1 ROE
    if ni is not None and eq is not None:
        both = pd.concat([ni, eq], axis=1, join="inner").dropna()
        if len(both) and (both.iloc[:, 1] > 0).all():
            roe = (both.iloc[:, 0] / both.iloc[:, 1]).mean()
            m["roe"] = roe
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
            m["debt_years"] = yrs
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
        m["gross_margin"] = gm
        add("Gross margin เฉลี่ย", "≥ 40%", f"{gm:.1%}", "pass" if gm >= 0.40 else "fail",
            "สัญญาณของความได้เปรียบทางการแข่งขัน (moat) ตั้งราคาได้")
    else:
        add("Gross margin เฉลี่ย", "≥ 40%", "ไม่มีข้อมูล (เช่น ธนาคาร)", "na",
            "สัญญาณของความได้เปรียบทางการแข่งขัน (moat) ตั้งราคาได้")

    # 4 Net margin
    if ni is not None and rev is not None:
        nm = (ni / rev).dropna().mean()
        m["net_margin"] = nm
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
            m["capex_to_ni"] = ratio
            add("Capex / กำไรสุทธิ", "≤ 50%", f"{ratio:.0%}", "pass" if ratio <= 0.5 else "fail",
                "ไม่ต้องลงทุนซ้ำมากเพื่อรักษาธุรกิจ")
        else:
            add("Capex / กำไรสุทธิ", "≤ 50%", "ขาดทุน", "fail", "ไม่ต้องลงทุนซ้ำมากเพื่อรักษาธุรกิจ")
    else:
        add("Capex / กำไรสุทธิ", "≤ 50%", "ไม่มีข้อมูล", "na", "ไม่ต้องลงทุนซ้ำมากเพื่อรักษาธุรกิจ")

    # 8 Share count (ยอมให้เพิ่มได้ไม่เกิน 1% ตลอดช่วง เช่นจากหุ้นที่ให้พนักงานเล็กน้อย)
    if shares is not None and len(shares) >= 2:
        chg = shares.iloc[-1] / shares.iloc[0] - 1
        m["share_change"] = chg
        add("จำนวนหุ้นไม่เพิ่ม (ไม่ dilute)", "เพิ่มไม่เกิน +1% ทั้งช่วง",
            f"{chg:+.2%} ({shares.index[0]}: {shares.iloc[0] / 1e6:,.1f}M → "
            f"{shares.index[-1]}: {shares.iloc[-1] / 1e6:,.1f}M หุ้น)",
            "pass" if chg <= 0.01 else "fail", "ผู้ถือหุ้นเดิมไม่ถูก dilute · ถ้าลดลงแปลว่าบริษัทซื้อหุ้นคืน")
    else:
        add("จำนวนหุ้นไม่เพิ่ม (ไม่ dilute)", "เพิ่มไม่เกิน +1% ทั้งช่วง", "ไม่มีข้อมูล", "na",
            "ผู้ถือหุ้นเดิมไม่ถูก dilute")

    # ค่าเริ่มต้น DCF
    extra = {"fcf": fcf, "metrics": m}
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


BASIS_OE, BASIS_FCF = "Owner Earnings (Buffett)", "Free Cash Flow"


def dcf_options(extra: dict) -> list[str]:
    """ตัวเลือกฐาน DCF ตัวแรกคือค่าเริ่มต้น (OE ถ้าเป็นบวก ไม่งั้น FCF)"""
    options = []
    if extra.get("oe_base") is not None:
        options.append(BASIS_OE)
    if extra.get("fcf_base") is not None:
        options.append(BASIS_FCF)
    if not options:
        return [BASIS_FCF]
    if options[0] == BASIS_OE and extra["oe_base"] <= 0 and BASIS_FCF in options:
        options.reverse()
    return options


def default_growth(extra: dict, basis: str) -> float:
    key = "oe_cagr" if basis == BASIS_OE else "fcf_cagr"
    return float(min(max(extra.get(key, 0.05), 0.0), 0.15))


def valuation_rows(extra: dict, price, shares_out, basis: str, g: float, dr: float, tg: float, tnx):
    """แถว Margin of safety + Owner earnings yield · คืน (rows, iv, mos, oe_y)"""
    rows = []
    base = extra.get("oe_base" if basis == BASIS_OE else "fcf_base")
    iv = None
    if base and base > 0 and shares_out and dr > tg:
        iv = dcf_value(base, g, dr, tg) / shares_out
    why = "ซื้อของดีในราคาที่มีส่วนเผื่อความผิดพลาด"
    if iv and price:
        mos = 1 - price / iv
        ok = mos >= 0.25
        rows.append({"หลักเกณฑ์": "ราคาต่ำกว่ามูลค่าที่แท้จริง", "เกณฑ์ผ่าน": "Margin of safety ≥ 25%",
                     "ค่าจริง": f"ราคา ${price:,.2f} vs มูลค่า ${iv:,.2f} ({mos:+.0%}) · ฐาน {basis}",
                     "ผล": "✅ ผ่าน" if ok else "❌ ไม่ผ่าน", "ทำไมสำคัญ": why, "_s": "pass" if ok else "fail"})
    else:
        mos = None
        rows.append({"หลักเกณฑ์": "ราคาต่ำกว่ามูลค่าที่แท้จริง", "เกณฑ์ผ่าน": "Margin of safety ≥ 25%",
                     "ค่าจริง": f"คำนวณไม่ได้ ({basis} ติดลบ/ไม่มีข้อมูล)", "ผล": "➖ N/A",
                     "ทำไมสำคัญ": why, "_s": "na"})
    oe_y = None
    if extra.get("oe_base") is not None and shares_out and price:
        oe_y = extra["oe_base"] / (shares_out * price)
        if tnx:
            ok = oe_y >= tnx
            rows.append({"หลักเกณฑ์": "Owner earnings yield vs พันธบัตร 10 ปี",
                         "เกณฑ์ผ่าน": "OE yield ≥ ผลตอบแทนพันธบัตร",
                         "ค่าจริง": f"{oe_y:.1%} vs Treasury 10Y {tnx:.2%}",
                         "ผล": "✅ ผ่าน" if ok else "❌ ไม่ผ่าน",
                         "ทำไมสำคัญ": "ถ้าได้น้อยกว่าพันธบัตรที่ไม่มีความเสี่ยง ก็ไม่คุ้มที่จะรับความเสี่ยงหุ้น",
                         "_s": "pass" if ok else "fail"})
    return rows, iv, mos, oe_y


def is_company(f: dict) -> bool:
    qtype = str(f["info"].get("quoteType", "")).upper()
    return qtype not in ("ETF", "MUTUALFUND", "INDEX") and not f["inc"].empty


def current_price(info: dict):
    return info.get("currentPrice") or info.get("regularMarketPrice")
