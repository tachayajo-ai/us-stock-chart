"""ข้อมูลบริษัท: แปลภาษา (Google Translate), ข่าว, วันประกาศงบ และลิงก์ Earnings call"""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import pandas as pd
import requests
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0"}


# ---------------- แปลภาษา ----------------
def _chunks(text: str, limit: int = 1500) -> list[str]:
    """ตัดข้อความยาวเป็นท่อนตามประโยค (Google Translate รับได้จำกัดต่อครั้ง)"""
    out, cur = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if cur and len(cur) + len(sentence) + 1 > limit:
            out.append(cur)
            cur = sentence
        else:
            cur = f"{cur} {sentence}".strip()
    if cur:
        out.append(cur)
    return out


def translate(text: str, target: str = "th", api_key: str | None = None) -> str:
    """มี api_key -> Google Cloud Translation API (ทางการ)
    ไม่มี -> ช่องทางฟรีของ Google Translate (ไม่ต้องใช้คีย์ แต่ไม่ใช่ช่องทางทางการ)"""
    if not text or not text.strip():
        return ""
    parts = []
    for chunk in _chunks(text):
        if api_key:
            r = requests.post("https://translation.googleapis.com/language/translate/v2",
                              params={"key": api_key},
                              data={"q": chunk, "target": target, "format": "text"}, timeout=20)
            r.raise_for_status()
            parts.append(html.unescape(r.json()["data"]["translations"][0]["translatedText"]))
        else:
            r = requests.get("https://translate.googleapis.com/translate_a/single",
                             params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": chunk},
                             headers=UA, timeout=20)
            r.raise_for_status()
            parts.append("".join(seg[0] for seg in r.json()[0] if seg and seg[0]))
    return " ".join(parts)


def translate_lines(lines: list[str], target: str = "th", api_key: str | None = None) -> list[str]:
    """แปลหลายบรรทัดในครั้งเดียว (เช่น หัวข้อข่าว) ถ้าจำนวนบรรทัดไม่ตรงค่อยแปลทีละบรรทัด"""
    if not lines:
        return []
    joined = translate("\n".join(lines), target, api_key) if not api_key else None
    if joined is not None:
        out = [x.strip() for x in joined.split("\n")]
        if len(out) == len(lines):
            return out
    return [translate(x, target, api_key) for x in lines]


# ---------------- ข่าว ----------------
def _to_time(v):
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        ts = pd.Timestamp(v)
        return (ts.tz_localize("UTC") if ts.tzinfo is None else ts).to_pydatetime()
    except Exception:
        return None


def _google_news(ticker: str, name: str) -> list[dict]:
    q = quote(f'"{ticker}" {name} stock')
    r = requests.get(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
                     headers=UA, timeout=20)
    r.raise_for_status()
    items = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(" - " + src)]
        try:
            when = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            when = None
        items.append({"title": title, "url": it.findtext("link"), "source": src, "time": when, "summary": ""})
    return items


def fetch_news(ticker: str, name: str = "", limit: int = 12) -> list[dict]:
    """ข่าวจาก Yahoo Finance ถ้าไม่มีใช้ Google News แทน"""
    items = []
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception:
        raw = []
    for n in raw:
        c = n.get("content", n) if isinstance(n, dict) else {}
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
               or c.get("link"))
        title = c.get("title")
        if not title or not url:
            continue
        items.append({
            "title": title.strip(), "url": url,
            "source": (c.get("provider") or {}).get("displayName") or c.get("publisher") or "",
            "time": _to_time(c.get("pubDate") or c.get("displayTime") or n.get("providerPublishTime")),
            "summary": (c.get("summary") or "").strip(),
        })
    if not items:
        try:
            items = _google_news(ticker, name)
        except Exception:
            items = []
    seen, uniq = set(), []
    for it in items:
        if it["title"] not in seen:
            seen.add(it["title"])
            uniq.append(it)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    uniq.sort(key=lambda x: x["time"] or epoch, reverse=True)
    return uniq[:limit]


# ---------------- Earnings ----------------
def fetch_earnings(ticker: str) -> dict:
    """วันประกาศงบครั้งถัดไป + ผลงบย้อนหลัง (EPS คาดการณ์ vs จริง)"""
    t = yf.Ticker(ticker)
    out = {"next": None, "eps_est": None, "history": None}
    try:
        cal = t.calendar or {}
        dates = cal.get("Earnings Date") or []
        if dates:
            out["next"] = pd.Timestamp(dates[0]).date()
        out["eps_est"] = cal.get("Earnings Average")
    except Exception:
        pass
    try:
        df = t.get_earnings_dates(limit=12)
        if df is not None and not df.empty:
            df = df.copy()
            df.index = pd.to_datetime(df.index)
            past = df[df["Reported EPS"].notna()].sort_index(ascending=False).head(8)
            out["history"] = past
            if out["next"] is None:
                fut = df[df["Reported EPS"].isna() & (df.index > pd.Timestamp.now(tz=df.index.tz))]
                if not fut.empty:
                    out["next"] = fut.index.min().date()
                    out["eps_est"] = out["eps_est"] or fut.sort_index()["EPS Estimate"].iloc[0]
    except Exception:
        pass
    return out


def earnings_links(ticker: str, info: dict) -> list[tuple[str, str, str]]:
    """(ชื่อ, URL, คำอธิบาย) สำหรับฟัง/อ่าน earnings call"""
    t = ticker.upper()
    name = info.get("shortName") or info.get("longName") or t
    links = []
    ir = info.get("irWebsite")
    if ir:
        links.append(("Investor Relations ของบริษัท", ir, "webcast สด/ย้อนหลัง และเอกสารประกอบการนำเสนอ (แหล่งทางการ)"))
    elif info.get("website"):
        links.append(("เว็บไซต์บริษัท", info["website"], "หาเมนู Investors / Investor Relations เพื่อฟัง webcast"))
    links += [
        ("YouTube — earnings call", "https://www.youtube.com/results?search_query=" + quote(f"{name} {t} earnings call"),
         "หลายบริษัทอัปโหลดเสียงการประชุมไว้ บางคลิปมีซับไตเติล"),
        ("Motley Fool — transcript", "https://www.google.com/search?q=" + quote(f"{t} earnings call transcript site:fool.com"),
         "บทถอดความฉบับเต็ม อ่านฟรี"),
        ("Seeking Alpha — transcripts", f"https://seekingalpha.com/symbol/{t}/earnings/transcripts",
         "รวบรวม transcript ทุกไตรมาส (บางส่วนต้องสมัครสมาชิก)"),
        ("Nasdaq — earnings", f"https://www.nasdaq.com/market-activity/stocks/{t.lower()}/earnings",
         "วันประกาศงบและ EPS คาดการณ์ vs จริง"),
        ("SEC EDGAR — 8-K / 10-Q", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK="
         + quote(t.split("-")[0]) + "&type=8-K&dateb=&owner=include&count=40",
         "ข่าวประกาศผลประกอบการและงบฉบับทางการที่ยื่นต่อ ก.ล.ต. สหรัฐ"),
    ]
    return links
