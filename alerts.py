"""แจ้งเตือนราคา: เก็บการตั้งเตือนใน alerts.json (แอพเขียน) และสถานะใน alerts_state.json (GitHub Actions เขียน)

ตรวจ:  python alerts.py            (GitHub Actions เรียกทุก 30 นาทีช่วงตลาดเปิด)
ทดสอบ: python alerts.py --test     (ส่งแจ้งเตือนทดสอบ 1 ข้อความ)
ต้องตั้ง environment variable NTFY_TOPIC (GitHub → Settings → Secrets → Actions)
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).parent
ALERTS_FILE = HERE / "alerts.json"
STATE_FILE = HERE / "alerts_state.json"
NTFY_URL = "https://ntfy.sh/"
KINDS = {"below": "ราคาลงถึง", "above": "ราคาขึ้นถึง", "mos": "Margin of safety ถึง"}


# ---------------- ตัวช่วยที่แอพใช้ร่วม ----------------
def new_alert(ticker: str, kind: str, target: float, iv: float | None = None, mos: float | None = None,
              note: str = "") -> dict:
    return {"id": uuid.uuid4().hex[:10], "ticker": ticker.upper(), "kind": kind, "target": round(float(target), 2),
            "iv": round(float(iv), 2) if iv else None, "mos": mos, "note": note.strip(),
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%d")}


def describe(a: dict) -> str:
    if a["kind"] == "mos":
        return (f"MOS ≥ {a['mos']:.0%} → ราคา ≤ ${a['target']:,.2f} "
                f"(มูลค่า DCF ${a['iv']:,.2f} ณ {a['created']})")
    sign = "≤" if a["kind"] == "below" else "≥"
    return f"{KINDS[a['kind']]} {sign} ${a['target']:,.2f}"


def is_hit(a: dict, price: float) -> bool:
    return price >= a["target"] if a["kind"] == "above" else price <= a["target"]


def distance(a: dict, price: float) -> float:
    """ราคาต้องเปลี่ยนอีกกี่ % ถึงจะถึงเป้า (ลบ = ต้องลง, บวก = ต้องขึ้น, 0 = ถึงแล้ว)"""
    if not price or is_hit(a, price):
        return 0.0
    return a["target"] / price - 1


# ---------------- ราคาและการส่งแจ้งเตือน ----------------
def latest_price(ticker: str) -> float | None:
    """ราคาล่าสุดรวมช่วง Pre-market / After-hours (delay ~15 นาที)"""
    import yfinance as yf
    try:
        h = yf.Ticker(ticker).history(period="2d", interval="5m", prepost=True)
        if not h.empty:
            return float(h["Close"].dropna().iloc[-1])
    except Exception:
        pass
    try:
        h = yf.Ticker(ticker).history(period="5d", interval="1d")
        return float(h["Close"].dropna().iloc[-1]) if not h.empty else None
    except Exception:
        return None


def send_ntfy(topic: str, title: str, message: str, click: str | None = None, priority: int = 4,
              tags: list[str] | None = None) -> None:
    """ส่ง push notification ผ่าน ntfy.sh (ใช้ JSON เพื่อรองรับภาษาไทยในหัวข้อ)"""
    body = {"topic": topic, "title": title, "message": message, "priority": priority,
            "tags": tags or ["chart_with_downwards_trend"]}
    if click:
        body["click"] = click
    r = requests.post(NTFY_URL, json=body, timeout=20)
    r.raise_for_status()


def run_check(alerts: list[dict], state: dict, topic: str, price_fn=latest_price, send_fn=send_ntfy,
              now: datetime | None = None) -> tuple[dict, list[str]]:
    """ตรวจทุกการตั้งเตือนที่ยังไม่แจ้ง · คืน (state ใหม่, รายการที่ส่งแล้ว)"""
    now = now or datetime.now(timezone.utc)
    state = dict(state)
    sent = []
    prices: dict[str, float | None] = {}
    for a in alerts:
        if a["id"] in state:
            continue  # แจ้งไปแล้ว (กด "ตั้งใหม่" ในแอพเพื่อเปิดอีกครั้ง)
        t = a["ticker"]
        if t not in prices:
            prices[t] = price_fn(t)
        price = prices[t]
        if price is None or not is_hit(a, price):
            continue
        arrow = "📈" if a["kind"] == "above" else "📉"
        title = f"{arrow} {t} ${price:,.2f} — ถึงจุดที่ตั้งไว้"
        msg = describe(a)
        if a["kind"] == "mos" and a.get("iv"):
            msg += f"\nMOS ตอนนี้ {1 - price / a['iv']:+.0%}"
        if a.get("note"):
            msg += f"\nบันทึก: {a['note']}"
        msg += "\nตรวจงบและข่าวล่าสุดก่อนตัดสินใจ (มูลค่า DCF อาจเปลี่ยนแล้ว)"
        send_fn(topic, title, msg, click=f"https://finance.yahoo.com/quote/{t}/",
                tags=["chart_with_upwards_trend" if a["kind"] == "above" else "chart_with_downwards_trend"])
        state[a["id"]] = {"triggered_at": now.strftime("%Y-%m-%d %H:%M UTC"), "price": round(price, 2)}
        sent.append(f"{t}: {msg.splitlines()[0]} @ {price:.2f}")
    return state, sent


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="ส่งแจ้งเตือนทดสอบ")
    args = ap.parse_args()
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        sys.exit("NTFY_TOPIC is not set (GitHub → Settings → Secrets and variables → Actions)")
    import hashlib
    print(f"Topic fingerprint: {hashlib.sha256(topic.encode()).hexdigest()[:12]} (length {len(topic)})")
    if args.test:
        send_ntfy(topic, "✅ ทดสอบแจ้งเตือนหุ้น", "ระบบแจ้งเตือนราคาของ US Stock Chart ทำงานแล้ว",
                  tags=["white_check_mark"], priority=3)
        print("Test notification sent")
        return
    alerts = _load(ALERTS_FILE, [])
    state = _load(STATE_FILE, {})
    new_state, sent = run_check(alerts, state, topic)
    active_ids = {a["id"] for a in alerts}
    new_state = {k: v for k, v in new_state.items() if k in active_ids}  # ล้างสถานะของการเตือนที่ถูกลบ
    print(f"Checked {len(alerts)} alerts · sent {len(sent)}")
    for s in sent:
        print("  ", s)
    if new_state != state:
        STATE_FILE.write_text(json.dumps(new_state, indent=2, ensure_ascii=False))
        print("State updated")


if __name__ == "__main__":
    main()
