"""หน้าตาแอพ: CSS แนวแอพโบรกเกอร์ (โหมดมืด) และชิ้นส่วน HTML (ส่วนหัวหุ้น, การ์ดผลตอบแทน)"""
import html

UP, DOWN, ACCENT = "#22C55E", "#F05252", "#7C6CFF"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@400;500;600;700&display=swap');
:root {
  --bg:#0B0E14; --card:#141924; --card2:#1B2130; --border:#252D3D; --text:#E7EAF0; --muted:#8C95A8;
  --accent:#7C6CFF; --up:#22C55E; --down:#F05252; --radius:16px;
}
html, body, .stApp, p, li, label, td, th, input, textarea, button, h1, h2, h3, h4, h5, h6,
[data-testid="stMarkdownContainer"], [data-testid="stMetricValue"], [data-testid="stMetricLabel"] {
  font-family: 'IBM Plex Sans Thai', 'Sarabun', 'Noto Sans Thai', system-ui, -apple-system, sans-serif !important;
}
.block-container { padding-top: 2.2rem; max-width: 1280px; }
h3 { font-weight: 700 !important; letter-spacing: -0.01em; }

/* ---- การ์ดตัวเลข ---- */
[data-testid="stMetric"] {
  background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 14px 16px; height: 100%;
}
[data-testid="stMetricLabel"] p { color: var(--muted) !important; font-size: .82rem !important; }
[data-testid="stMetricValue"] { font-weight: 700 !important; font-variant-numeric: tabular-nums; font-size: 1.55rem !important; }
[data-testid="stMetricDelta"] { font-weight: 600; }

/* ---- แท็บ ---- */
.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid var(--border); overflow-x: auto; }
.stTabs [data-baseweb="tab"] { padding: 8px 12px; border-radius: 10px 10px 0 0; }
.stTabs [aria-selected="true"] { background: var(--card); }
.stTabs [data-baseweb="tab-highlight"] { background: var(--accent); height: 3px; border-radius: 3px; }

/* ---- ปุ่มเลือก (radio) เป็นแคปซูล ---- */
[data-testid="stRadioGroup"] { gap: 6px !important; flex-wrap: wrap; }
label[data-testid="stRadioOption"] {
  background: var(--card); border: 1px solid var(--border); border-radius: 999px;
  padding: 4px 14px !important; margin: 0 !important; cursor: pointer;
}
label[data-testid="stRadioOption"] > div > div:first-child { display: none !important; }
label[data-testid="stRadioOption"]:hover { border-color: var(--accent); }
label[data-testid="stRadioOption"][data-selected="true"] { background: var(--accent); border-color: var(--accent); }
label[data-testid="stRadioOption"][data-selected="true"] p { color: #fff !important; font-weight: 600; }

/* ---- กล่องพับ ตาราง ปุ่ม ---- */
[data-testid="stExpander"] details { border: 1px solid var(--border) !important; border-radius: var(--radius) !important; background: var(--card); }
[data-testid="stExpander"] summary { font-weight: 600; }
[data-testid="stDataFrame"] { border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
.stButton > button, .stFormSubmitButton > button { border-radius: 12px; }
[data-testid="stPlotlyChart"] { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 6px; }
[data-testid="stAlert"] { border-radius: 12px; }

/* ---- Watchlist (แท็บ ⭐) ---- */
[data-testid="stSidebar"] { background: #0F131B; border-right: 1px solid var(--border); }
.st-key-wl_list .stButton > button { justify-content: flex-start; text-align: left; padding: 6px 12px; }
.st-key-wl_list .stButton > button p { font-variant-numeric: tabular-nums; font-size: .9rem; white-space: nowrap; }
.st-key-wl_list [data-testid="stColumn"]:last-child .stButton > button {
  justify-content: center; padding: 6px 0; min-width: 0; color: var(--muted); }

/* ---- ส่วนหัวหุ้น ---- */
.hero { display:flex; gap:16px; align-items:center; background: linear-gradient(135deg, #1A1F33 0%, #141924 60%);
  border:1px solid var(--border); border-radius: 20px; padding: 18px 20px; margin-bottom: 14px; flex-wrap: wrap; }
.hero .logo { width:52px; height:52px; border-radius:14px; background:#fff; display:flex; align-items:center;
  justify-content:center; overflow:hidden; flex-shrink:0; font-weight:700; color:#111; font-size:1.1rem; }
.hero .logo img { width:36px; height:36px; }
.hero .id { flex: 1 1 200px; min-width: 0; }
.hero .tk { font-size:1.5rem; font-weight:700; line-height:1.1; }
.hero .nm { color: var(--muted); font-size:.9rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.hero .px { text-align:right; }
.hero .price { font-size:2.1rem; font-weight:700; font-variant-numeric: tabular-nums; line-height:1.1; }
.hero .sub { color: var(--muted); font-size:.8rem; margin-top:4px; }
.pill { display:inline-block; padding:3px 10px; border-radius:999px; font-weight:600; font-size:.9rem;
  font-variant-numeric: tabular-nums; margin-top:4px; }
.pill.up { background: rgba(34,197,94,.14); color: var(--up); }
.pill.down { background: rgba(240,82,82,.14); color: var(--down); }
.pill.flat { background: var(--card2); color: var(--muted); }
.ext { margin-left:8px; color: var(--muted); font-size:.85rem; }
.ext b.up { color: var(--up); } .ext b.down { color: var(--down); }
@media (max-width: 640px) { .hero .px { text-align:left; width:100%; } .hero .price { font-size:1.8rem; } }

/* ---- การ์ดผลตอบแทนแต่ละช่วง ---- */
.chips { display:flex; gap:8px; overflow-x:auto; padding: 2px 2px 8px; margin-bottom: 4px; }
.chip { flex: 0 0 auto; min-width: 92px; background: var(--card); border:1px solid var(--border);
  border-radius: 14px; padding: 8px 12px; }
.chip .p { color: var(--muted); font-size:.75rem; font-weight:600; letter-spacing:.04em; }
.chip .v { font-size: 1.05rem; font-weight:700; font-variant-numeric: tabular-nums; }
.chip .d { font-size:.78rem; color: var(--muted); font-variant-numeric: tabular-nums; }
.chip.up .v { color: var(--up); } .chip.down .v { color: var(--down); }
.chip.sel { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); background: #1A1D33; }
</style>
"""


def _cls(x: float | None) -> str:
    if x is None:
        return "flat"
    return "up" if x > 0 else "down" if x < 0 else "flat"


def hero(ticker: str, name: str, price: float | None, chg: float | None, pct: float | None,
         logo_url: str | None = None, sub: str = "", ext: tuple | None = None) -> str:
    """ส่วนหัวหุ้น: โลโก้ ชื่อ ราคา ป้ายขึ้น/ลง และราคา pre/after-hours (ext = (label, price, pct))"""
    e = html.escape
    initials = e(ticker[:2])
    logo = (f'<img src="{e(logo_url)}" alt="" onerror="this.remove();this.parentNode.textContent=\'{initials}\'">'
            if logo_url else initials)
    arrow = "▲" if (pct or 0) > 0 else "▼" if (pct or 0) < 0 else "•"
    pill = (f'<span class="pill {_cls(pct)}">{arrow} {chg:+,.2f} ({pct:+.2f}%)</span>'
            if chg is not None and pct is not None else "")
    ext_html = ""
    if ext:
        lbl, xp, xpct = ext
        ext_html = (f'<span class="ext">{e(lbl)} <b>${xp:,.2f}</b> '
                    f'<b class="{_cls(xpct)}">{xpct:+.2f}%</b></span>') if xpct is not None else ""
    price_html = f"${price:,.2f}" if price is not None else "—"
    return (f'<div class="hero"><div class="logo">{logo}</div>'
            f'<div class="id"><div class="tk">{e(ticker)}</div><div class="nm">{e(name)}</div></div>'
            f'<div class="px"><div class="price">{price_html}</div><div>{pill}{ext_html}</div>'
            f'<div class="sub">{e(sub)}</div></div></div>')


def chips(perf: dict, order: list[str], selected: str | None = None) -> str:
    """การ์ดผลตอบแทนแต่ละช่วง (เลื่อนซ้าย-ขวาได้บนมือถือ)"""
    out = ['<div class="chips">']
    for k in order:
        p = perf[k]
        out.append(f'<div class="chip {_cls(p["pct"])}{" sel" if k == selected else ""}">'
                   f'<div class="p">{html.escape(k)}</div><div class="v">{p["pct"]:+.2f}%</div>'
                   f'<div class="d">{p["chg"]:+,.2f} $</div></div>')
    out.append("</div>")
    return "".join(out)


def logo_url(website: str | None) -> str | None:
    """ไอคอนเว็บไซต์บริษัท (บริการ favicon ของ Google)"""
    if not website:
        return None
    from urllib.parse import urlparse
    host = urlparse(website if "://" in website else "https://" + website).netloc
    return f"https://www.google.com/s2/favicons?domain={host}&sz=128" if host else None
