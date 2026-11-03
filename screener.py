"""คัดกรองหุ้น S&P 500 ด้วย Buffett checklist แล้วบันทึกเป็น screener.csv
รันอัตโนมัติทุกสัปดาห์ผ่าน GitHub Actions (.github/workflows/screener.yml)
รันเอง:  python screener.py            (ทั้ง S&P 500 ใช้เวลาประมาณ 20-40 นาที)
         python screener.py --tickers AAPL,MSFT,KO   (ทดสอบไม่กี่ตัว)
"""
import argparse
import io
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from buffett import (buffett_checks, current_price, dcf_options, default_growth, fetch_fundamentals,
                     is_company, is_financial, valuation_rows)

OUT = Path(__file__).with_name("screener.csv")
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
    checks, extra = buffett_checks(f, financial=is_financial(f["info"], sector))
    info = f["info"]
    price, shares = current_price(info), info.get("sharesOutstanding")
    basis = dcf_options(extra)[0]
    g = default_growth(extra, basis)
    vrows, iv, mos, oe_y = valuation_rows(extra, price, shares, basis, g, DISCOUNT, TERMINAL, tnx)
    rows = checks + vrows
    passed = sum(r["_s"] == "pass" for r in rows)
    applicable = sum(r["_s"] != "na" for r in rows)
    m = extra["metrics"]
    return {
        "ticker": ticker, "name": name, "sector": sector,
        "score": passed, "applicable": applicable,
        "score_pct": passed / applicable if applicable else None,
        "price": price, "intrinsic_value": iv, "margin_of_safety": mos,
        "price_to_book": info.get("priceToBook"),
        "oe_yield": oe_y, "roe": m.get("roe"), "gross_margin": m.get("gross_margin"),
        "net_margin": m.get("net_margin"), "debt_years": m.get("debt_years"),
        "capex_to_ni": m.get("capex_to_ni"), "share_change": m.get("share_change"),
        "dcf_basis": basis, "dcf_growth": g,
        "failed": " | ".join(r["หลักเกณฑ์"] for r in rows if r["_s"] == "fail"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", help="คั่นด้วย comma (ไม่ใส่ = ทั้ง S&P 500)")
    ap.add_argument("--delay", type=float, default=1.0, help="วินาทีที่พักระหว่างหุ้นแต่ละตัว")
    ap.add_argument("--min-results", type=int, default=300,
                    help="ถ้าได้ผลน้อยกว่านี้จะไม่เขียนทับไฟล์เดิม (กันข้อมูลเสียจาก rate limit)")
    args = ap.parse_args()

    if args.tickers:
        universe = pd.DataFrame({"ticker": [t.strip().upper() for t in args.tickers.split(",")],
                                 "name": "", "sector": ""})
        args.min_results = min(args.min_results, 1)
    else:
        universe = sp500()
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
            results.append(r)
        else:
            skipped.append(row.ticker)
        if i % 25 == 0:
            print(f"  {i}/{len(universe)} done, {len(results)} ok")
        time.sleep(args.delay)

    print(f"OK {len(results)} · skipped {len(skipped)}: {', '.join(skipped[:30])}")
    if len(results) < args.min_results:
        sys.exit(f"Only {len(results)} results (< {args.min_results}); keeping previous {OUT.name}")

    df = pd.DataFrame(results).sort_values(["score_pct", "margin_of_safety"], ascending=False)
    df["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    df.to_csv(OUT, index=False, float_format="%.4f")
    print(f"Wrote {OUT} ({len(df)} rows)")


if __name__ == "__main__":
    main()
