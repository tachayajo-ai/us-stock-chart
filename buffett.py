"""Buffett checklist / Owner Earnings / DCF — ใช้ร่วมกันระหว่าง app.py และ screener.py"""
import time

import pandas as pd
import requests
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
    return to_trading_currency(out)


NON_MONEY = ("Shares", "Share Issued", "Tax Rate")  # คอลัมน์ที่ไม่ใช่จำนวนเงิน (ไม่ต้องแปลงสกุล)


def to_trading_currency(f: dict) -> dict:
    """หุ้นต่างชาติ (ADR เช่น PDD, TSM) งบเป็นสกุลเงินบ้านเกิด (CNY, TWD) แต่ราคาเป็น USD
    → แปลงตัวเลขเงินในงบเป็นสกุลเดียวกับราคา ไม่งั้น DCF/MOS เพี้ยนหลายเท่า
    ถ้าหาอัตราแลกเปลี่ยนไม่ได้ ให้ล้างงบทิ้ง (ไม่ประเมิน) ดีกว่าแสดงตัวเลขผิด"""
    info = f["info"]
    fin, cur = info.get("financialCurrency"), info.get("currency")
    if not fin or not cur or fin == cur:
        return f
    rate = None
    try:
        rate = float(yf.Ticker(f"{fin}{cur}=X").history(period="5d")["Close"].dropna().iloc[-1])
    except Exception:
        pass
    if not rate or rate <= 0:
        info["_fx_note"] = f"งบเป็น {fin} แต่ราคาเป็น {cur} และหาอัตราแลกเปลี่ยนไม่ได้ — ไม่ประเมิน"
        for k in ("inc", "bal", "cf"):
            f[k] = pd.DataFrame()
        return f
    for k in ("inc", "bal", "cf"):
        df = f[k]
        if df.empty:
            continue
        money = [c for c in df.columns if not any(s in str(c) for s in NON_MONEY)]
        df[money] = df[money].apply(pd.to_numeric, errors="coerce") * rate
    info["_fx_note"] = f"งบการเงินเป็น {fin} แปลงเป็น {cur} ที่ 1 {fin} = {rate:.4g} {cur}"
    return f


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
    สูตรเดิม: กำไรสุทธิ + ค่าเสื่อม + รายการที่ไม่ใช่เงินสด ± working capital − Maintenance capex
    ซึ่ง "กำไรสุทธิ + ค่าเสื่อม + ไม่ใช่เงินสด ± working capital" ก็คือ กระแสเงินสดจากการดำเนินงาน (OCF)
    จึงใช้: OE = OCF − ค่าตอบแทนเป็นหุ้น (SBC, Buffett ถือว่าเป็นต้นทุนจริง) − Maintenance capex
    (ไม่ใช้บรรทัด "Change In Working Capital" ของ Yahoo ตรงๆ เพราะบางบริษัทไม่ตรงกับงบจริง เช่น Visa)
    Maintenance capex ประมาณแบบ Greenwald: capex ทั้งหมด − (Net PPE ÷ รายได้) × รายได้ที่เพิ่มขึ้น"""
    inc, bal, cf = f["inc"], f["bal"], f["cf"]
    ni = _col(inc, "Net Income", "Net Income Common Stockholders")
    ocf = _col(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
    capex = _col(cf, "Capital Expenditure")
    if ocf is None or capex is None:
        return None
    sbc = _col(cf, "Stock Based Compensation")
    rev = _col(inc, "Total Revenue", "Operating Revenue")
    ppe = _col(bal, "Net PPE")
    df = pd.concat({"ocf": ocf, "capex": capex.abs()}, axis=1, join="inner").dropna()
    if df.empty:
        return None
    df["ni"] = ni.reindex(df.index) if ni is not None else float("nan")
    df["sbc"] = sbc.reindex(df.index).fillna(0) if sbc is not None else 0.0
    df["growth_capex"] = 0.0
    if rev is not None and ppe is not None:
        ppe_to_sales = (ppe / rev).dropna().mean()
        d_rev = rev.diff().clip(lower=0).reindex(df.index).fillna(0)
        if pd.notna(ppe_to_sales):
            df["growth_capex"] = (ppe_to_sales * d_rev).clip(upper=df["capex"])
    df["maint_capex"] = df["capex"] - df["growth_capex"]
    df["oe"] = df["ocf"] - df["sbc"] - df["maint_capex"]
    return df


FIN_NOTE = "ไม่ใช้กับธุรกิจการเงิน"
FIN_WHY = ("ธนาคาร/ประกัน/บริษัทการเงิน: กระแสเงินสดรวมเงินฝาก เบี้ยประกัน และเงินลงทุนของลูกค้า "
           "จึงไม่ใช่เงินที่เจ้าของดึงออกได้จริง")


# อุตสาหกรรม (Yahoo) ที่กระแสเงินสดปนเงินฝาก/เบี้ยประกัน/เงินลูกค้า -> DCF ใช้ไม่ได้
FIN_INDUSTRY_PREFIXES = ("banks", "insurance -", "capital markets", "asset management",
                         "mortgage finance", "credit services", "financial conglomerates")
# อยู่ใน Credit Services แต่เป็นเครือข่ายชำระเงิน ไม่ได้ปล่อยกู้ -> ใช้ DCF ได้
PAYMENT_NETWORKS = {"V", "MA", "PYPL"}


def is_financial(info: dict, sector: str | None = None) -> bool:
    """ใช้ industry ของ Yahoo ก่อน (แยกธนาคาร/ประกัน ออกจาก Visa, Moody's, FactSet ได้)
    ถ้าไม่มีข้อมูล industry จึงใช้ sector (Yahoo 'Financial Services' / GICS 'Financials')"""
    industry = str(info.get("industry", "")).strip().lower()
    if industry:
        if str(info.get("symbol", "")).upper() in PAYMENT_NETWORKS:
            return False
        return industry.startswith(FIN_INDUSTRY_PREFIXES)
    names = {str(info.get("sector", "")).strip().lower(), str(sector or "").strip().lower()}
    return bool(names & {"financial services", "financials"})


def buffett_checks(f: dict, financial: bool | None = None) -> tuple[list[dict], dict]:
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

    # ธุรกิจการเงิน: FCF / Capex / Owner earnings / DCF ไม่มีความหมาย -> N/A ไม่นับคะแนน
    if financial is None:
        financial = is_financial(f["info"])
    extra["financial"] = financial
    if financial:
        for r in rows:
            if r["หลักเกณฑ์"] in ("Free cash flow เป็นบวกทุกปี", "Capex / กำไรสุทธิ", "Owner earnings เป็นบวกทุกปี"):
                r.update({"ค่าจริง": f"{FIN_NOTE} ({r['ค่าจริง']})", "ผล": "➖ N/A", "ทำไมสำคัญ": FIN_WHY, "_s": "na"})
        m.pop("capex_to_ni", None)
    return rows, extra


# ความหมายของผลแต่ละข้อ: (ถ้าผ่าน 👍, ถ้าไม่ผ่าน 👎) — แสดงใต้การ์ดใน Buffett checklist
MEANING = {
    "ROE เฉลี่ย": (
        "ใช้เงินของผู้ถือหุ้นสร้างกำไรได้ดี มักเป็นสัญญาณของความได้เปรียบทางการแข่งขัน",
        "ใช้เงินผู้ถือหุ้นสร้างกำไรได้น้อย ธุรกิจอาจแข่งขันสูง หรือใช้ทุนมากแต่ได้กำไรน้อย"),
    "หนี้ระยะยาว / กำไรสุทธิ": (
        "หนี้น้อยเมื่อเทียบกับกำไร ทนช่วงเศรษฐกิจแย่หรือดอกเบี้ยขาขึ้นได้",
        "หนี้มากเมื่อเทียบกับกำไร (หรือขาดทุน) เสี่ยงเมื่อดอกเบี้ยขึ้นหรือรายได้ลด อาจต้องเพิ่มทุนหรือขายทรัพย์สิน"),
    "Gross margin เฉลี่ย": (
        "ตั้งราคาสูงกว่าต้นทุนได้มาก สัญญาณของแบรนด์หรือ moat",
        "กำไรขั้นต้นบาง สินค้าคล้ายคู่แข่งจึงต้องแข่งด้วยราคา (ยกเว้นธุรกิจที่ขายปริมาณมาก เช่น Costco)"),
    "Net margin เฉลี่ย": (
        "ขายได้ 100 บาทเหลือเป็นกำไรจริงมาก ต้นทุนแฝงต่ำ",
        "เหลือเป็นกำไรสุทธิน้อย ค่าใช้จ่าย ดอกเบี้ย หรือภาษีกินกำไร หรือเป็นธุรกิจ margin ต่ำโดยธรรมชาติ"),
    "กำไรสม่ำเสมอและโตขึ้น": (
        "มีกำไรทุกปีและกำไรต่อหุ้นโตขึ้น ธุรกิจคาดการณ์ได้",
        "เคยขาดทุนหรือกำไรต่อหุ้นไม่โต คาดการณ์อนาคตยาก มูลค่า DCF จึงเชื่อถือได้น้อย"),
    "Free cash flow เป็นบวกทุกปี": (
        "กำไรออกมาเป็นเงินสดจริงทุกปี",
        "มีปีที่เงินสดไหลออกมากกว่าเข้า กำไรในบัญชีอาจไม่ใช่เงินสด หรือกำลังลงทุนหนัก"),
    "Capex / กำไรสุทธิ": (
        "ไม่ต้องใช้เงินลงทุนมากเพื่อรักษาธุรกิจ เหลือเงินจ่ายปันผลหรือซื้อหุ้นคืน",
        "ต้องลงทุนซ้ำมาก (โรงงาน เครื่องจักร) เหลือเงินให้ผู้ถือหุ้นน้อย หรือบริษัทขาดทุน"),
    "จำนวนหุ้นไม่เพิ่ม (ไม่ dilute)": (
        "จำนวนหุ้นไม่เพิ่ม ส่วนแบ่งกำไรของผู้ถือหุ้นเดิมไม่ถูกลด (ถ้าลดลง = บริษัทซื้อหุ้นคืน)",
        "ออกหุ้นเพิ่ม (เพิ่มทุนหรือให้พนักงาน) ส่วนแบ่งกำไรต่อหุ้นของผู้ถือหุ้นเดิมลดลง"),
    "Owner earnings เป็นบวกทุกปี": (
        "เจ้าของดึงเงินออกได้จริงทุกปีโดยไม่กระทบความสามารถในการแข่งขัน",
        "มีปีที่ Owner earnings ติดลบ ธุรกิจใช้เงินมากกว่าที่สร้าง (ลงทุนหนักหรือจ่ายหุ้นให้พนักงานมาก)"),
    "ราคาต่ำกว่ามูลค่าที่แท้จริง": (
        "ราคาต่ำกว่ามูลค่าที่ประเมินพอ มีส่วนเผื่อถ้าประเมินผิด",
        "ราคาสูงกว่าหรือใกล้มูลค่าที่ประเมิน ไม่มีส่วนเผื่อความผิดพลาด ตลาดคาดหวังการเติบโตสูงไว้แล้ว"),
    "Owner earnings yield vs พันธบัตร 10 ปี": (
        "ผลตอบแทนจากกำไรของธุรกิจสูงกว่าพันธบัตรรัฐบาลที่ไม่มีความเสี่ยง",
        "ผลตอบแทนจากกำไรต่ำกว่าพันธบัตรรัฐบาล ราคาแพงจนถือพันธบัตรยังได้มากกว่าโดยไม่ต้องเสี่ยง"),
}


def explain(rows: list[dict]) -> list[dict]:
    """เติม "ความหมาย" ตามผล (ผ่าน/ไม่ผ่าน) ให้แต่ละแถว"""
    for r in rows:
        pair = MEANING.get(r["หลักเกณฑ์"])
        if pair and r.get("_s") in ("pass", "fail"):
            r["ความหมาย"] = ("👍 " + pair[0]) if r["_s"] == "pass" else ("👎 " + pair[1])
    return rows


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


def _cagr(s):
    if s is None or len(s) < 2 or s.iloc[0] <= 0 or s.iloc[-1] <= 0 or s.index[-1] <= s.index[0]:
        return None
    return (s.iloc[-1] / s.iloc[0]) ** (1 / (s.index[-1] - s.index[0])) - 1


MAX_GROWTH = 0.10


def default_growth(extra: dict, basis: str) -> float:
    """อัตราเติบโตเริ่มต้นแบบระวัง:
    - กระแสเงินสดเคยติดลบ/เป็นศูนย์ในช่วงที่ดู -> 0% (ธุรกิจคาดการณ์ไม่ได้)
    - ไม่งั้นใช้ค่าต่ำกว่าระหว่าง CAGR ของกระแสเงินสด (เฉลี่ยเคลื่อนที่ 2 ปี ลดผลของปีที่ผิดปกติ)
      กับ CAGR ของรายได้ (กันกรณีกำไรฟื้นจากฐานต่ำ เช่นหลัง COVID)
    - จำกัดไว้ 0-10%"""
    cash = extra.get("oe") if basis == BASIS_OE else extra.get("fcf")
    if cash is None or len(cash) < 2:
        return 0.05
    if (cash <= 0).any():
        return 0.0
    smooth = cash.rolling(2).mean().dropna() if len(cash) >= 3 else cash
    g = _cagr(smooth)
    if g is None:
        g = 0.05
    rev_g = _cagr(extra.get("rev"))
    if rev_g is not None:
        g = min(g, rev_g)
    return float(min(max(g, 0.0), MAX_GROWTH))


def valuation_rows(extra: dict, price, shares_out, basis: str, g: float, dr: float, tg: float, tnx):
    """แถว Margin of safety + Owner earnings yield · คืน (rows, iv, mos, oe_y)"""
    rows = []
    if extra.get("financial"):
        rows.append({"หลักเกณฑ์": "ราคาต่ำกว่ามูลค่าที่แท้จริง", "เกณฑ์ผ่าน": "Margin of safety ≥ 25%",
                     "ค่าจริง": f"{FIN_NOTE} — ใช้ P/B, ROE และคุณภาพสินเชื่อ/การรับประกันแทน",
                     "ผล": "➖ N/A", "ทำไมสำคัญ": FIN_WHY, "_s": "na"})
        return rows, None, None, None
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



def implied_growth(base, price, shares, dr: float = 0.10, tg: float = 0.025):
    """อัตราเติบโตต่อปี (10 ปี) ที่ทำให้มูลค่า DCF ต่อหุ้น = ราคาตลาดพอดี
    บอกว่าราคาปัจจุบัน "คาดหวัง" ให้บริษัทโตเท่าไร · None ถ้าคำนวณไม่ได้"""
    if not base or base <= 0 or not price or not shares or dr <= tg:
        return None
    target = price * shares
    lo, hi = -0.30, 0.80
    if dcf_value(base, lo, dr, tg) > target:
        return lo  # ต่อให้หดตัวหนักก็ยังคุ้ม -> ต่ำกว่า -30%
    if dcf_value(base, hi, dr, tg) < target:
        return hi  # ต้องโตเกิน 80%/ปี
    for _ in range(60):
        mid = (lo + hi) / 2
        if dcf_value(base, mid, dr, tg) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

def is_company(f: dict) -> bool:
    qtype = str(f["info"].get("quoteType", "")).upper()
    return qtype not in ("ETF", "MUTUALFUND", "INDEX") and not f["inc"].empty


def current_price(info: dict):
    return info.get("currentPrice") or info.get("regularMarketPrice")


def share_count(info: dict, price):
    """จำนวนหุ้นเทียบเท่าทั้งบริษัท = มูลค่าตลาดรวม ÷ ราคา
    แก้ปัญหาหุ้นหลาย class (BRK-B, GOOGL) ที่ Yahoo ให้ sharesOutstanding เฉพาะ class เดียว"""
    mc = info.get("marketCap")
    if mc and price:
        return mc / price
    return info.get("impliedSharesOutstanding") or info.get("sharesOutstanding")


def price_to_book(f: dict):
    """P/B = มูลค่าตลาดรวม ÷ ส่วนของผู้ถือหุ้น (งบปีล่าสุด)
    ไม่ใช้ priceToBook ของ Yahoo ตรงๆ เพราะหุ้นหลาย class คำนวณผิด (เช่น BRK-B ได้ 0.001)"""
    info = f["info"]
    eq = _col(f["bal"], "Stockholders Equity", "Common Stock Equity")
    mc = info.get("marketCap")
    if eq is not None and mc:
        return mc / float(eq.iloc[-1]) if eq.iloc[-1] > 0 else None
    pb = info.get("priceToBook")
    return pb if pb and pb >= 0.05 else None


# ---------------- Buffett Indicator (ตลาดรวม) ----------------
# FRED บล็อกการเรียกจากเซิร์ฟเวอร์คลาวด์ (GitHub Actions / Streamlit Cloud) จึงใช้ DBnomics
# ซึ่งเป็นตัวกลางข้อมูลทางการ (Federal Reserve, BEA) ฟรี ไม่ต้องใช้ API key
DBN = "https://api.db.nomics.world/v22/series/"
MCAP_SERIES = "FED/Z1/FL103164105.Q"    # Fed Z.1: มูลค่าตลาดหุ้นของบริษัทนอกภาคการเงิน (ล้าน USD)
GDP_SERIES = "BEA/NIPA-T10105/A191RC-Q"  # BEA: GDP ราคาปัจจุบัน ปรับเป็นรายปี (ล้าน USD)


def fetch_dbnomics(code: str, timeout: int = 60, tries: int = 3) -> pd.Series:
    """คืน Series รายไตรมาส (index = วันแรกของไตรมาส)"""
    last = None
    for attempt in range(tries):
        try:
            r = requests.get(DBN + code, params={"observations": 1},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
            r.raise_for_status()
            doc = r.json()["series"]["docs"][0]
            s = pd.Series(pd.to_numeric(doc["value"], errors="coerce"),
                          index=pd.PeriodIndex([pd.Period(p, freq="Q") for p in doc["period"]]))
            s.index = s.index.start_time
            return s.dropna()
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < tries - 1:
                time.sleep(5 * (attempt + 1))
    raise last


def build_buffett_indicator(timeout: int = 60, tries: int = 3) -> pd.DataFrame:
    """มูลค่าตลาดหุ้นบริษัทนอกภาคการเงิน ÷ GDP × 100 (รายไตรมาส ตั้งแต่ 1947)"""
    mcap = fetch_dbnomics(MCAP_SERIES, timeout, tries) / 1000  # ล้าน -> พันล้าน USD
    gdp = fetch_dbnomics(GDP_SERIES, timeout, tries) / 1000
    df = pd.concat({"mcap_bn": mcap, "gdp_bn": gdp}, axis=1, join="inner").dropna()
    df["ratio"] = df["mcap_bn"] / df["gdp_bn"] * 100
    df.index.name = "date"
    return df


def key_stats(f: dict) -> list[tuple[str, list[tuple]]]:
    """ตัวเลขการเงินสำคัญสำหรับแท็บ Data · คืน [(หัวข้อ, [(ชื่อ, ค่า, สี, หมายเหตุ), ...])]
    ค่าที่ไม่มีข้อมูลเป็น None (ui.key_stats จะซ่อนให้)"""
    info = f["info"]
    price = current_price(info)
    mc = info.get("marketCap")

    def num(v):
        try:
            v = float(v)
            return v if v == v and abs(v) != float("inf") else None
        except (TypeError, ValueError):
            return None

    def x(v, unit=" เท่า", d=1):
        v = num(v)
        return f"{v:,.{d}f}{unit}" if v is not None else None

    def pct(v, sign=False):
        v = num(v)
        return (f"{v:+.1%}" if sign else f"{v:.1%}") if v is not None else None

    def tone(v, good_if_pos=True):
        v = num(v)
        if v is None or v == 0:
            return ""
        return "up" if (v > 0) == good_if_pos else "down"

    def money(v):
        v = num(v)
        return fmt_money(v) if v is not None else None

    def last(df, *names):
        s = _col(df, *names)
        if s is None:
            return None, None
        s = s.dropna()
        return (num(s.iloc[-1]), s.index[-1]) if len(s) else (None, None)

    qtype = str(info.get("quoteType", "")).upper()
    if qtype in ("ETF", "MUTUALFUND"):
        er = num(info.get("netExpenseRatio"))  # Yahoo ให้เป็น % แล้ว (0.09 = 0.09%)
        er = er / 100 if er is not None else num(info.get("annualReportExpenseRatio"))
        return [
            ("กองทุน", [
                ("ค่าธรรมเนียมต่อปี (Expense ratio)", f"{er:.2%}" if er is not None else None, "", None),
                ("อัตราปันผล (Yield)", pct(info.get("yield") or info.get("trailingAnnualDividendYield")), "", None),
                ("ผลตอบแทนเฉลี่ย 3 ปี", pct(info.get("threeYearAverageReturn"), True),
                 tone(info.get("threeYearAverageReturn")), "ต่อปี"),
                ("ผลตอบแทนเฉลี่ย 5 ปี", pct(info.get("fiveYearAverageReturn"), True),
                 tone(info.get("fiveYearAverageReturn")), "ต่อปี"),
                ("P/E ของหุ้นในกอง", x(info.get("trailingPE")), "", None),
            ]),
            ("ราคา", _price_items(info, price, num, money, pct, tone)),
        ]

    inc, cf = f.get("inc", pd.DataFrame()), f.get("cf", pd.DataFrame())
    fcf, fcf_y = last(cf, "Free Cash Flow")
    rev = _col(inc, "Total Revenue")
    rev_cagr, rev_note = None, None
    if rev is not None:
        rev = rev.dropna()
        if len(rev) >= 2 and rev.iloc[0] > 0 and rev.iloc[-1] > 0:
            n = len(rev) - 1
            rev_cagr = (rev.iloc[-1] / rev.iloc[0]) ** (1 / n) - 1
            rev_note = f"{rev.index[0]}–{rev.index[-1]}"
    ebit, _ = last(inc, "EBIT", "Operating Income")
    intexp, _ = last(inc, "Interest Expense", "Interest Expense Non Operating")
    cover = ebit / abs(intexp) if ebit is not None and intexp else None
    debt, cash = num(info.get("totalDebt")), num(info.get("totalCash"))
    de = num(info.get("debtToEquity"))
    div_rate = num(info.get("dividendRate"))
    dy = div_rate / price if div_rate and price else num(info.get("trailingAnnualDividendYield"))
    ex_div = info.get("exDividendDate")
    try:
        ex_div = f"{pd.Timestamp(ex_div, unit='s'):%d/%m/%Y}" if ex_div else None
    except Exception:
        ex_div = None
    pb = price_to_book(f)

    return [
        ("มูลค่า (ถูกหรือแพง)", [
            ("P/E ล่วงหน้า (Forward)", x(info.get("forwardPE")), "", "จากกำไรที่นักวิเคราะห์คาดปีหน้า"),
            ("PEG", x(info.get("trailingPegRatio") or info.get("pegRatio"), "", 2), "", "P/E ÷ การเติบโต · < 1 ถือว่าถูก"),
            ("P/S (ราคา ÷ ยอดขาย)", x(info.get("priceToSalesTrailing12Months")), "", None),
            ("P/B (ราคา ÷ มูลค่าทางบัญชี)", x(pb, " เท่า", 2), "", None),
            ("EV/EBITDA", x(info.get("enterpriseToEbitda")), "", "รวมหนี้ด้วย · ใช้เทียบในอุตสาหกรรมเดียวกัน"),
            ("P/FCF (ราคา ÷ กระแสเงินสดอิสระ)", x(mc / fcf) if mc and fcf and fcf > 0 else None, "",
             f"FCF ปี {fcf_y}" if fcf_y else None),
            ("FCF yield", pct(fcf / mc) if mc and fcf else None, tone(fcf), "ยิ่งสูงยิ่งถูก"),
        ]),
        ("ความสามารถทำกำไร", [
            ("Gross margin", pct(info.get("grossMargins")), "", "กำไรขั้นต้น ÷ ยอดขาย"),
            ("Operating margin", pct(info.get("operatingMargins")), tone(info.get("operatingMargins")),
             "กำไรจากธุรกิจหลัก ÷ ยอดขาย"),
            ("Net margin", pct(info.get("profitMargins")), tone(info.get("profitMargins")), "กำไรสุทธิ ÷ ยอดขาย"),
            ("ROE", pct(info.get("returnOnEquity")), tone(info.get("returnOnEquity")), "กำไร ÷ ส่วนผู้ถือหุ้น"),
            ("ROA", pct(info.get("returnOnAssets")), tone(info.get("returnOnAssets")), "กำไร ÷ สินทรัพย์"),
            ("EPS 12 เดือนล่าสุด", f"${num(info.get('trailingEps')):,.2f}" if num(info.get("trailingEps")) is not None
             else None, tone(info.get("trailingEps")), None),
            ("EPS ที่คาดปีหน้า", f"${num(info.get('forwardEps')):,.2f}" if num(info.get("forwardEps")) is not None
             else None, "", None),
        ]),
        ("การเติบโต", [
            ("รายได้ (ไตรมาสล่าสุด เทียบปีก่อน)", pct(info.get("revenueGrowth"), True), tone(info.get("revenueGrowth")),
             None),
            ("กำไร (ไตรมาสล่าสุด เทียบปีก่อน)", pct(info.get("earningsGrowth"), True),
             tone(info.get("earningsGrowth")), None),
            ("รายได้โตเฉลี่ยต่อปี (CAGR)", pct(rev_cagr, True), tone(rev_cagr), rev_note),
            ("รายได้ 12 เดือนล่าสุด", money(info.get("totalRevenue")), "", None),
        ]),
        ("ฐานะการเงิน", [
            ("เงินสด", money(cash), "", None),
            ("หนี้สินที่มีดอกเบี้ย", money(debt), "", None),
            ("หนี้สุทธิ (หนี้ − เงินสด)", money(debt - cash) if debt is not None and cash is not None else None,
             tone(debt - cash, False) if debt is not None and cash is not None else "", "ติดลบ = เงินสดมากกว่าหนี้"),
            ("D/E (หนี้ ÷ ทุน)", x(de / 100, " เท่า", 2) if de is not None else None, "", None),
            ("Current ratio", x(info.get("currentRatio"), " เท่า", 2), "",
             "สินทรัพย์หมุนเวียน ÷ หนี้ระยะสั้น · > 1 ดี"),
            ("ความสามารถจ่ายดอกเบี้ย", x(cover) if cover is not None else None,
             "up" if cover and cover >= 5 else "down" if cover is not None and cover < 2 else "",
             "EBIT ÷ ดอกเบี้ยจ่าย · ≥ 5 เท่าปลอดภัย"),
            ("Free cash flow", money(fcf), tone(fcf), f"ปี {fcf_y}" if fcf_y else None),
        ]),
        ("ปันผล", [
            ("อัตราปันผล", pct(dy) if dy else None, "", f"${div_rate:,.2f} ต่อหุ้นต่อปี" if div_rate else None),
            ("Payout ratio", pct(info.get("payoutRatio")) if num(info.get("payoutRatio")) else None, "",
             "สัดส่วนกำไรที่จ่ายเป็นปันผล"),
            ("วันขึ้น XD ล่าสุด", ex_div, "", None),
        ]),
        ("ราคาและนักวิเคราะห์", _price_items(info, price, num, money, pct, tone) + [
            ("ราคาเป้าหมายเฉลี่ย", f"${num(info.get('targetMeanPrice')):,.2f}" if num(info.get("targetMeanPrice"))
             else None, tone(num(info.get("targetMeanPrice")) - price)
             if num(info.get("targetMeanPrice")) and price else "",
             (f"{num(info.get('targetMeanPrice')) / price - 1:+.0%} จากราคาปัจจุบัน · "
              if num(info.get("targetMeanPrice")) and price else "")
             + f"{info.get('numberOfAnalystOpinions') or '?'} นักวิเคราะห์"),
            ("คำแนะนำนักวิเคราะห์", _REC.get(str(info.get("recommendationKey", "")).lower()), "",
             f"คะแนน {num(info.get('recommendationMean')):.1f} (1 = ซื้อมาก, 5 = ขาย)"
             if num(info.get("recommendationMean")) else None),
            ("ผู้บริหารถือหุ้น", pct(info.get("heldPercentInsiders")), "", None),
            ("สถาบันถือหุ้น", pct(info.get("heldPercentInstitutions")), "", None),
            ("Short interest", pct(info.get("shortPercentOfFloat")), "", "% ของหุ้นหมุนเวียนที่ถูกขายชอร์ต"),
        ]),
    ]


_REC = {"strong_buy": "ซื้อมาก", "buy": "ซื้อ", "hold": "ถือ", "underperform": "ต่ำกว่าตลาด", "sell": "ขาย",
        "strong_sell": "ขายมาก"}


def _price_items(info, price, num, money, pct, tone) -> list[tuple]:
    hi, lo = num(info.get("fiftyTwoWeekHigh")), num(info.get("fiftyTwoWeekLow"))
    ma50, ma200 = num(info.get("fiftyDayAverage")), num(info.get("twoHundredDayAverage"))
    return [
        ("ช่วงราคา 52 สัปดาห์", f"${lo:,.2f} – ${hi:,.2f}" if hi and lo else None, "", None),
        ("ห่างจากจุดสูงสุด 52 สัปดาห์", pct(price / hi - 1, True) if hi and price else None,
         tone(price / hi - 1) if hi and price else "", None),
        ("เทียบเส้นค่าเฉลี่ย 50 วัน", pct(price / ma50 - 1, True) if ma50 and price else None,
         tone(price / ma50 - 1) if ma50 and price else "", f"MA50 = ${ma50:,.2f}" if ma50 else None),
        ("เทียบเส้นค่าเฉลี่ย 200 วัน", pct(price / ma200 - 1, True) if ma200 and price else None,
         tone(price / ma200 - 1) if ma200 and price else "", f"MA200 = ${ma200:,.2f}" if ma200 else None),
    ]
