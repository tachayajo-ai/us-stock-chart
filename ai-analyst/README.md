# นักวิเคราะห์หุ้น AI (Claude Skill)

Skill `stock-analyst` สำหรับ Claude — วิเคราะห์โมเดลธุรกิจ, MOAT, ผู้บริหาร และตัวชี้วัดเฉพาะอุตสาหกรรม (12 กลุ่ม) เป็นภาษาไทย

## ติดตั้ง
1. ดาวน์โหลด `stock-analyst.zip`
2. claude.ai → Settings → Capabilities → Skills → Upload skill → เลือกไฟล์ zip
   (ต้องเปิด "Code execution and file creation" ไว้)
3. แชทใหม่: พิมพ์ "วิเคราะห์หุ้น AAPL" หรือวางข้อมูลจากแท็บ 🎩 Buffett → 🤖 ส่งข้อมูลให้นักวิเคราะห์ AI

## แก้ไข
แก้ไฟล์ใน `stock-analyst/` แล้ว zip ใหม่และอัปโหลดทับ
- `SKILL.md` — ขั้นตอนและโครงรายงาน
- `references/moat-management-rubric.md` — เกณฑ์ MOAT/ผู้บริหาร
- `references/<อุตสาหกรรม>.md` — ตัวชี้วัด, MOAT, red flags, วิธีประเมินมูลค่า, คำถาม earnings call
