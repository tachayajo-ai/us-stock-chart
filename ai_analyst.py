"""ส่งข้อมูลจากแท็บ Buffett ให้ Claude วิเคราะห์ด้วย skill stock-analyst ผ่าน Claude API

ต้องตั้ง Streamlit secret ANTHROPIC_API_KEY (คีย์จาก console.anthropic.com — คิดเงินแยกจากแพ็กเกจ claude.ai)
"""
from pathlib import Path

MODEL = "claude-opus-5-5"
SKILL_DIR = Path(__file__).with_name("ai-analyst") / "stock-analyst"
MAX_CONTINUES = 4  # pause_turn จาก web search ต่อได้กี่รอบ

# USD ต่อ 1 ล้าน token (Claude Opus 5.5) · web search $10 ต่อ 1,000 ครั้ง
PRICE_IN, PRICE_OUT, PRICE_CACHE_W, PRICE_CACHE_R, PRICE_SEARCH = 4.0, 20.0, 5.0, 0.40, 0.01


def system_prompt() -> str:
    """SKILL.md + ไฟล์อ้างอิงทั้งหมด (ข้อความคงที่ → ใช้ prompt caching ได้)"""
    parts = [(SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"),
             "\n\n# ไฟล์อ้างอิง (references/) — แนบมาครบทุกไฟล์แล้ว ให้ใช้ไฟล์ที่ตรงกับอุตสาหกรรม\n"]
    for p in sorted((SKILL_DIR / "references").glob("*.md")):
        parts.append(f"\n\n<reference file=\"references/{p.name}\">\n{p.read_text(encoding='utf-8')}\n</reference>")
    parts.append("\n\nคุณมีเครื่องมือ web_search ให้ค้นข้อมูลล่าสุด (10-K, earnings call, proxy, ข่าว) "
                 "ตอบเป็น Markdown ภาษาไทยตามโครงรายงานข้างต้น (แสดงผลในแอพ Streamlit)")
    return "".join(parts)


def _cost(usage) -> float:
    def g(name):
        return getattr(usage, name, 0) or 0
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
    return (g("input_tokens") * PRICE_IN + g("output_tokens") * PRICE_OUT
            + g("cache_creation_input_tokens") * PRICE_CACHE_W
            + g("cache_read_input_tokens") * PRICE_CACHE_R) / 1e6 + searches * PRICE_SEARCH


def analyze(api_key: str, package: str, on_text=None) -> dict:
    """เรียก Claude (streaming) · on_text(ข้อความสะสม) ถูกเรียกระหว่างสตรีม · คืน dict รายงาน"""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, timeout=900.0)
    system = [{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 15}]
    messages = [{"role": "user", "content": package}]
    cost, searches, live = 0.0, 0, ""
    final = None
    for _ in range(MAX_CONTINUES + 1):
        with client.beta.messages.stream(
            model=MODEL, max_tokens=64000, system=system, tools=tools, messages=messages,
            thinking={"type": "adaptive"}, output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        ) as stream:
            for chunk in stream.text_stream:
                live += chunk
                if on_text:
                    on_text(live)
            final = stream.get_final_message()
        cost += _cost(final.usage)
        searches += getattr(getattr(final.usage, "server_tool_use", None), "web_search_requests", 0) or 0
        if final.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": final.content})  # ให้โมเดลทำงานต่อจากเดิม
        live += "\n\n"
    if final.stop_reason == "refusal":
        raise RuntimeError("Claude ปฏิเสธคำขอนี้ (refusal) — ลองกดใหม่อีกครั้ง")
    # รวมข้อความจากทุกรอบ (ข้อความสุดท้ายจาก final message แม่นกว่าที่สตรีม)
    texts = []
    for m in messages[1:] + [{"content": final.content}]:
        texts += [b.text for b in m["content"] if getattr(b, "type", None) == "text"]
    report = "".join(texts).strip() or live.strip()
    if final.stop_reason == "max_tokens":
        report += "\n\n_(รายงานถูกตัดเพราะยาวเกินขีดจำกัด)_"
    return {"text": report, "model": getattr(final, "model", MODEL), "cost_usd": round(cost, 3),
            "searches": searches}
