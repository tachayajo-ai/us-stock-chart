"""พอร์ตการลงทุน: เก็บรายการซื้อขาย (เข้ารหัสด้วยรหัสผ่าน) และคำนวณต้นทุน กำไรขาดทุน ปันผล

- ต้นทุนแบบถัวเฉลี่ย (average cost) เหมือนรายงานของโบรกเกอร์ส่วนใหญ่
- ข้อมูลเข้ารหัสด้วย Fernet (AES) + PBKDF2 จากรหัสผ่าน — ไฟล์บน GitHub จึงอ่านไม่ออกถ้าไม่มีรหัส
"""
import base64
import json
import os
import uuid
from datetime import date, datetime, timezone

import pandas as pd
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

KDF_ITER = 390_000


# ---------------- เข้ารหัส ----------------
def _key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=KDF_ITER)
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def encrypt(data, password: str) -> dict:
    salt = os.urandom(16)
    token = Fernet(_key(password, salt)).encrypt(json.dumps(data, ensure_ascii=False).encode())
    return {"v": 1, "salt": base64.b64encode(salt).decode(), "data": token.decode()}


def decrypt(blob: dict, password: str):
    """คืนข้อมูลเดิม · ValueError ถ้ารหัสผิดหรือไฟล์เสีย"""
    try:
        salt = base64.b64decode(blob["salt"])
        return json.loads(Fernet(_key(password, salt)).decrypt(blob["data"].encode()))
    except (InvalidToken, KeyError, ValueError, TypeError) as e:
        raise ValueError("รหัสผ่านไม่ถูกต้อง หรือไฟล์ข้อมูลเสียหาย") from e


# ---------------- รายการซื้อขาย ----------------
def new_tx(d: date | str, ticker: str, kind: str, shares: float, price: float, fee: float = 0.0,
           note: str = "") -> dict:
    """kind: "buy" | "sell" """
    return {"id": uuid.uuid4().hex[:10], "date": str(d)[:10], "ticker": ticker.upper().strip(), "kind": kind,
            "shares": float(shares), "price": float(price), "fee": float(fee or 0), "note": note.strip(),
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _sorted(txs: list[dict]) -> list[dict]:
    return sorted(txs, key=lambda t: (t["date"], t.get("created", "")))


def compute_positions(txs: list[dict]) -> tuple[dict, dict]:
    """คืน (positions, realized)
    positions[ticker] = {shares, cost (ต้นทุนรวมของหุ้นที่ยังถือ รวมค่าธรรมเนียม), avg, first_date}
    realized[ticker] = กำไร/ขาดทุนที่ขายไปแล้ว (หักค่าธรรมเนียมขาย)"""
    pos: dict[str, dict] = {}
    realized: dict[str, float] = {}
    for t in _sorted(txs):
        p = pos.setdefault(t["ticker"], {"shares": 0.0, "cost": 0.0, "first_date": t["date"]})
        if t["kind"] == "buy":
            p["cost"] += t["shares"] * t["price"] + t["fee"]
            p["shares"] += t["shares"]
        elif t["kind"] == "sell":
            sell = min(t["shares"], p["shares"])
            avg = p["cost"] / p["shares"] if p["shares"] else 0.0
            realized[t["ticker"]] = realized.get(t["ticker"], 0.0) + sell * (t["price"] - avg) - t["fee"]
            p["cost"] -= avg * sell
            p["shares"] -= sell
            if p["shares"] <= 1e-9:
                p["shares"], p["cost"] = 0.0, 0.0
    for p in pos.values():
        p["avg"] = p["cost"] / p["shares"] if p["shares"] else 0.0
    return pos, realized


def shares_held(txs: list[dict], ticker: str, on: str) -> float:
    """จำนวนหุ้นที่ถือ ณ สิ้นวัน on (YYYY-MM-DD)"""
    n = 0.0
    for t in _sorted(txs):
        if t["ticker"] == ticker and t["date"] <= on:
            n += t["shares"] if t["kind"] == "buy" else -t["shares"]
    return max(n, 0.0)


def validate_sell(txs: list[dict], tx: dict) -> str | None:
    """ข้อความผิดพลาด ถ้าขายเกินจำนวนที่ถือ ณ วันนั้น (หรือทำให้รายการขายภายหลังเกิน)"""
    if tx["kind"] != "sell":
        return None
    trial = _sorted(txs + [tx])
    held = 0.0
    for t in trial:
        if t["ticker"] != tx["ticker"]:
            continue
        held += t["shares"] if t["kind"] == "buy" else -t["shares"]
        if held < -1e-9:
            return f"ขายเกินจำนวนที่ถือ ณ วันที่ {t['date']} (ถืออยู่ {held + t['shares']:g} หุ้น)"
    return None


# ---------------- ปันผล (ประมาณจากข้อมูล Yahoo) ----------------
def dividends_received(txs: list[dict], divs: dict[str, pd.Series], withholding: float = 0.15) -> pd.DataFrame:
    """ปันผลที่ควรได้รับ = ปันผลต่อหุ้น × หุ้นที่ถือ ณ วันก่อน ex-date − ภาษีหัก ณ ที่จ่าย
    นักลงทุนไทยถูกหัก 15% ตามอนุสัญญาภาษีไทย-สหรัฐ"""
    rows = []
    for tk, s in divs.items():
        if s is None or s.empty:
            continue
        for ex, dps in s[s > 0].items():
            day_before = (pd.Timestamp(ex) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            n = shares_held(txs, tk, day_before)
            if n > 0:
                gross = n * float(dps)
                rows.append({"ticker": tk, "ex_date": pd.Timestamp(ex).strftime("%Y-%m-%d"), "per_share": float(dps),
                             "shares": n, "gross": gross, "net": gross * (1 - withholding)})
    return pd.DataFrame(rows, columns=["ticker", "ex_date", "per_share", "shares", "gross", "net"])


# ---------------- มูลค่าพอร์ตย้อนหลัง + เทียบ S&P 500 ----------------
def value_history(txs: list[dict], closes: dict[str, pd.Series], bench: pd.Series | None = None) -> pd.DataFrame:
    """มูลค่าพอร์ตรายวัน, เงินลงทุนสุทธิสะสม และมูลค่าถ้านำเงินก้อนเดียวกันไปซื้อ/ขาย S&P 500 (SPY) ในวันเดียวกัน"""
    if not txs or not closes:
        return pd.DataFrame()
    start = pd.Timestamp(min(t["date"] for t in txs))
    idx = None
    for s in closes.values():
        s_idx = s.index[s.index >= start]
        idx = s_idx if idx is None else idx.union(s_idx)
    if idx is None or len(idx) == 0:
        return pd.DataFrame()
    px = pd.DataFrame({tk: s.reindex(idx).ffill() for tk, s in closes.items()})
    shares = pd.DataFrame(0.0, index=idx, columns=px.columns)
    flow = pd.Series(0.0, index=idx)
    for t in _sorted(txs):
        d = pd.Timestamp(t["date"])
        on = idx[idx >= d]
        if len(on) == 0:
            continue
        sign = 1 if t["kind"] == "buy" else -1
        if t["ticker"] in shares.columns:
            shares.loc[on, t["ticker"]] += sign * t["shares"]
        amount = t["shares"] * t["price"] + (t["fee"] if sign > 0 else -t["fee"])
        flow.loc[on[0]] += sign * amount
    out = pd.DataFrame({"value": (shares.clip(lower=0) * px).sum(axis=1), "invested": flow.cumsum()})
    if bench is not None and not bench.empty:
        b = bench.reindex(idx).ffill().bfill()
        units = (flow / b).cumsum()  # ซื้อ/ขาย SPY ด้วยจำนวนเงินเท่ากันในวันเดียวกัน
        out["benchmark"] = units * b
    return out


# ---------------- นำเข้า / ส่งออก CSV ----------------
CSV_COLS = ["date", "ticker", "kind", "shares", "price", "fee", "note"]


def to_csv(txs: list[dict]) -> str:
    return pd.DataFrame(_sorted(txs)).reindex(columns=CSV_COLS).to_csv(index=False)


def from_csv(text: str) -> list[dict]:
    import io
    df = pd.read_csv(io.StringIO(text))
    missing = {"date", "ticker", "kind", "shares", "price"} - set(df.columns)
    if missing:
        raise ValueError("ไฟล์ CSV ขาดคอลัมน์: " + ", ".join(sorted(missing)))
    out = []
    for r in df.fillna({"fee": 0, "note": ""}).to_dict("records"):
        kind = str(r["kind"]).strip().lower()
        kind = {"ซื้อ": "buy", "ขาย": "sell"}.get(kind, kind)
        if kind not in ("buy", "sell"):
            raise ValueError(f"kind ต้องเป็น buy หรือ sell (พบ '{r['kind']}')")
        out.append(new_tx(pd.Timestamp(r["date"]).date(), str(r["ticker"]), kind, float(r["shares"]),
                          float(r["price"]), float(r.get("fee", 0) or 0), str(r.get("note", "") or "")))
    return out
