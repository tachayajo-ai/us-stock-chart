"""คัดกรองหุ้นด้วย Buffett checklist
- วันเสาร์  (.github/workflows/screener.yml):       S&P 500 + Nasdaq-100 → screener.csv (+ buffett_indicator.csv)
- วันอาทิตย์ (.github/workflows/screener_r1000.yml): Russell 1000 เฉพาะตัวที่ไม่อยู่ใน screener.csv
                                                    → screener_r1000.csv + russell1000.json (รายชื่อสมาชิกทั้งหมด)
รันเอง:  python screener.py                      (S&P 500 + Nasdaq-100)
         python screener.py --universe r1000     (Russell 1000 ส่วนที่เหลือ)
         python screener.py --tickers AAPL,MSFT,KO   (ทดสอบไม่กี่ตัว)
"""
import argparse
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from buffett import (build_buffett_indicator, buffett_checks, current_price, dcf_options, default_growth, fetch_fundamentals,
                     implied_growth, is_company, is_financial, price_to_book, share_count,
                     valuation_rows)

OUT = Path(__file__).with_name("screener.csv")
OUT_R1000 = Path(__file__).with_name("screener_r1000.csv")
R1000_MEMBERS = Path(__file__).with_name("russell1000.json")
BI_OUT = Path(__file__).with_name("buffett_indicator.csv")
UA = {"User-Agent": "Mozilla/5.0 (us-stock-chart screener)"}
DISCOUNT, TERMINAL = 0.10, 0.025


def sp500() -> pd.DataFrame:
    """รายชื่อ S&P 500: Wikipedia (ล่าสุด) ถ้าไม่ได้ใช้ dataset บน GitHub แทน"""
    try:
        html = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                            headers=UA, timeout=30).text
        df = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})[0]
        df = df.rename(columns={"Symbol": "ticker", "Security": "name", "GICS Sector": "sector"})
    except Exception as e:  # noqa: BLE001
        print(f"Wikipedia failed ({e}); using GitHub dataset", file=sys.stderr)
        csv = requests.get("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
                           "main/data/constituents.csv", headers=UA, timeout=30).text
        df = pd.read_csv(io.StringIO(csv)).rename(
            columns={"Symbol": "ticker", "Security": "name", "GICS Sector": "sector"})
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False).str.strip()
    return df[["ticker", "name", "sector"]].drop_duplicates("ticker")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["ticker"]).copy()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper().str.replace(".", "-", regex=False)
    df = df[df["ticker"].str.fullmatch(r"[A-Z][A-Z0-9-]{0,6}")]
    for c in ("name", "sector"):
        if c not in df.columns:
            df[c] = ""
    return df[["ticker", "name", "sector"]].drop_duplicates("ticker")


def wiki_table(url: str, min_rows: int) -> pd.DataFrame:
    """หาตารางรายชื่อหุ้นในหน้า Wikipedia: ตารางแรกที่มีคอลัมน์ Ticker/Symbol และจำนวนแถว ≥ min_rows"""
    html = requests.get(url, headers=UA, timeout=30).text
    for t in pd.read_html(io.StringIO(html)):
        t.columns = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
        sym = next((c for c in t.columns if c.strip().lower() in ("ticker", "symbol", "ticker symbol")), None)
        if sym is None or len(t) < min_rows:
            continue
        name = next((c for c in t.columns if c.strip().lower() in ("company", "security", "name")), None)
        sec = next((c for c in t.columns if "sector" in c.lower()), None)
        out = pd.DataFrame({"ticker": t[sym], "name": t[name] if name else "", "sector": t[sec] if sec else ""})
        return _clean(out)
    raise ValueError(f"no constituents table on {url}")


def nasdaq100() -> pd.DataFrame:
    return wiki_table("https://en.wikipedia.org/wiki/Nasdaq-100", 90)


def russell1000() -> pd.DataFrame:
    """รายชื่อ Russell 1000: Wikipedia ถ้าไม่ได้ใช้ holdings ของ iShares IWB"""
    try:
        return wiki_table("https://en.wikipedia.org/wiki/Russell_1000_Index", 900)
    except Exception as e:  # noqa: BLE001
        print(f"Wikipedia Russell 1000 failed ({e}); using iShares IWB holdings", file=sys.stderr)
    text = requests.get("https://www.ishares.com/us/products/239707/ishares-russell-1000-etf/"
                        "1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund",
                        headers=UA, timeout=60).text
    start = next(i for i, line in enumerate(text.splitlines()) if line.startswith("Ticker,"))
    df = pd.read_csv(io.StringIO("\n".join(text.splitlines()[start:])), on_bad_lines="skip")
    if "Asset Class" in df.columns:
        df = df[df["Asset Class"] == "Equity"]
    df = _clean(df.rename(columns={"Ticker": "ticker", "Name": "name", "Sector": "sector"}))
    df["name"] = df["name"].astype(str).str.title()
    if len(df) < 900:
        raise ValueError(f"iShares holdings too short ({len(df)})")
    return df


def main_universe() -> pd.DataFrame:
    """S&P 500 + Nasdaq-100 พร้อมคอลัมน์ indexes (เช่น "S&P 500 · Nasdaq-100")"""
    sp = sp500().assign(indexes="S&P 500")
    try:
        ndx = nasdaq100()
        print(f"Nasdaq-100: {len(ndx)} tickers")
    except Exception as e:  # noqa: BLE001
        print(f"Nasdaq-100 list failed ({e}); S&P 500 only", file=sys.stderr)
        return sp
    both = sp["ticker"].isin(ndx["ticker"])
    sp.loc[both, "indexes"] = "S&P 500 · Nasdaq-100"
    extra = ndx[~ndx["ticker"].isin(sp["ticker"])].assign(indexes="Nasdaq-100")
    return pd.concat([sp, extra], ignore_index=True)


def fetch_with_retry(ticker: str, tries: int = 3) -> dict | None:
    for attempt in range(tries):
        f = fetch_fundamentals(ticker)
        if f["info"] or not f["inc"].empty:
            return f
        time.sleep(10 * (attempt + 1))  # น่าจะโดน rate limit: รอแล้วลองใหม่
    return None


def screen_one(ticker: str, name: str, sector: str, tnx) -> dict | None:
    f = fetch_with_retry(ticker)
    if f is None or not is_company(f):
        return None
    financial = is_financial(f["info"], sector)
    checks, extra = buffett_checks(f, financial=financial)
    info = f["info"]
    price = current_price(info)
    shares = share_count(info, price)
    basis = dcf_options(extra)[0]
    g = default_growth(extra, basis)
    vrows, iv, mos, oe_y = valuation_rows(extra, price, shares, basis, g, DISCOUNT, TERMINAL, tnx)
    rows = checks + vrows
    base = extra.get("oe_base" if basis.startswith("Owner") else "fcf_base")
    imp_g = None if extra.get("financial") else implied_growth(base, price, shares, DISCOUNT, TERMINAL)
    passed = sum(r["_s"] == "pass" for r in rows)
    applicable = sum(r["_s"] != "na" for r in rows)
    m = extra["metrics"]
    return {
        "ticker": ticker, "name": name, "sector": sector,
        "score": passed, "applicable": applicable,
        "score_pct": passed / applicable if applicable else None,
        "price": price, "market_cap": info.get("marketCap"),
        "intrinsic_value": iv, "margin_of_safety": mos,
        "price_to_book": price_to_book(f),
        "oe_yield": oe_y, "roe": m.get("roe"), "gross_margin": m.get("gross_margin"),
        "net_margin": m.get("net_margin"), "debt_years": m.get("debt_years"),
        "capex_to_ni": m.get("capex_to_ni"), "share_change": m.get("share_change"),
        "dcf_basis": basis, "dcf_growth": g, "implied_growth": imp_g, "financial": financial,
        "failed": " | ".join(r["หลักเกณฑ์"] for r in rows if r["_s"] == "fail"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=["main", "r1000"], default="main",
                    help="main = S&P 500 + Nasdaq-100 · r1000 = Russell 1000 ที่ไม่อยู่ใน screener.csv")
    ap.add_argument("--tickers", help="คั่นด้วย comma (ทดสอบ — ไม่ใส่ = ตาม --universe)")
    ap.add_argument("--delay", type=float, default=1.0, help="วินาทีที่พักระหว่างหุ้นแต่ละตัว")
    ap.add_argument("--min-results", type=int, default=None,
                    help="ถ้าได้ผลน้อยกว่านี้จะไม่เขียนทับไฟล์เดิม (กันข้อมูลเสียจาก rate limit)")
    args = ap.parse_args()
    r1000 = args.universe == "r1000"
    out = OUT_R1000 if r1000 else OUT

    if args.tickers:
        universe = pd.DataFrame({"ticker": [t.strip().upper() for t in args.tickers.split(",")],
                                 "name": "", "sector": "", "indexes": ""})
        min_results = 1
    elif r1000:
        members = russell1000()
        print(f"Russell 1000: {len(members)} tickers")
        R1000_MEMBERS.write_text(json.dumps({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                             "tickers": sorted(members["ticker"])}, indent=0))
        done = set(pd.read_csv(OUT)["ticker"]) if OUT.exists() else set()
        universe = members[~members["ticker"].isin(done)].assign(indexes="Russell 1000")
        print(f"Skip {len(members) - len(universe)} already in {OUT.name}")
        min_results = 100
    else:
        universe = main_universe()
        min_results = 300
    if args.min_results is not None:
        min_results = args.min_results
    print(f"Screening {len(universe)} tickers…")

    try:
        tnx = float(yf.Ticker("^TNX").history(period="5d")["Close"].iloc[-1]) / 100
    except Exception:
        tnx = None
    print(f"US 10Y Treasury: {tnx}")

    results, skipped = [], []
    for i, row in enumerate(universe.itertuples(index=False), 1):
        try:
            r = screen_one(row.ticker, row.name, row.sector, tnx)
        except Exception as e:  # noqa: BLE001
            print(f"  {row.ticker}: error {type(e).__name__}: {e}", file=sys.stderr)
            r = None
        if r:
            if not r["name"]:
                r["name"] = row.ticker
            r["indexes"] = row.indexes
            results.append(r)
        else:
            skipped.append(row.ticker)
        if i % 25 == 0:
            print(f"  {i}/{len(universe)} done, {len(results)} ok")
        time.sleep(args.delay)

    print(f"OK {len(results)} · skipped {len(skipped)}: {', '.join(skipped[:30])}")
    if not r1000:
        # Buffett Indicator ของตลาดรวม (ล้มเหลวได้โดยไม่กระทบผลคัดกรองหุ้น)
        try:
            bi = build_buffett_indicator()
            bi.to_csv(BI_OUT, float_format="%.4f")
            print(f"Wrote {BI_OUT.name} ({len(bi)} quarters, latest {bi.index[-1]:%Y-%m-%d}: "
                  f"{bi['ratio'].iloc[-1]:.0f}%)")
        except Exception as e:  # noqa: BLE001
            print(f"Buffett Indicator failed: {e}", file=sys.stderr)

    if len(results) < min_results:
        sys.exit(f"Only {len(results)} results (< {min_results}); keeping previous {out.name}")

    df = pd.DataFrame(results).sort_values(["score_pct", "margin_of_safety"], ascending=False)
    df["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    df.to_csv(out, index=False, float_format="%.4f")
    print(f"Wrote {out} ({len(df)} rows)")


if __name__ == "__main__":
    main()
