# US Stock Chart

📖 **[คู่มือการใช้งานฉบับเต็ม (MANUAL.md)](MANUAL.md)** — เปิดในแอพได้ที่แท็บ 📖 คู่มือ

แอพดูกราฟหุ้น/ETF อเมริกา (ข้อมูลจาก Yahoo Finance ผ่าน `yfinance`)

## ฟีเจอร์
- **Watchlist** เพิ่ม/ลบ symbol ได้ (บันทึกไว้ใน `watchlist.json` เปิดใหม่ยังอยู่)
- **กราฟ Pre-market / After-hours**: ช่วง 1D / 5D / 1M แสดงแท่ง 04:00–20:00 ET โดยแรเงาสีเทาช่วงนอกเวลาทำการ
- **ย้อนหลังยาว**: 6M, YTD, 1Y, 5Y, 10Y, Max (ตั้งแต่ IPO)
- Candlestick / Line, Log scale, MA50/MA200, Volume
- แท็บ **ภาพรวม**: ราคาปิด, % วันนี้, ราคา Pre/After-hours ของทุกตัวใน watchlist
- แท็บ **เปรียบเทียบ**: Total return (รวมปันผล) หลายตัวบนกราฟเดียว
- แท็บ **บริษัท**: ข้อมูลบริษัทแปลไทย ข่าว และลิงก์ earnings call
- แท็บ **แจ้งเตือน**: เตือนราคา/MOS เข้ามือถือผ่าน ntfy (GitHub Actions ตรวจทุก 30 นาที)
- แท็บ **พอร์ต**: บันทึกซื้อขาย ต้นทุนเฉลี่ย กำไรขาดทุน ปันผล เทียบ S&P 500 (ข้อมูลเข้ารหัสด้วยรหัสผ่าน)
- แท็บ **Screener**: คัดกรองหุ้น S&P 500 ด้วย Buffett checklist (GitHub Actions รัน `screener.py` ทุกวันเสาร์ บันทึกผลเป็น `screener.csv`)
- แท็บ **Buffett**: Buffett checklist รายตัว (ROE, หนี้, margin, FCF, capex, ซื้อหุ้นคืน, DCF + Margin of safety) และ Buffett Indicator ของตลาดรวม (Market cap ÷ GDP จาก FRED)

## วิธีติดตั้ง (ครั้งแรก)
1. ติดตั้ง Python 3.10+ จาก https://www.python.org/downloads/
   (Windows: ติ๊ก **Add Python to PATH** ตอนติดตั้ง)
2. ดับเบิลคลิก `START-Windows.bat` หรือ `START-Mac.command`
3. เบราว์เซอร์จะเปิด http://localhost:8501 อัตโนมัติ

หรือรันเองผ่าน terminal:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## ดูบนมือถือ
- อยู่ Wi-Fi เดียวกับคอม: เปิด URL "Network URL" ที่ terminal แสดง (เช่น `http://192.168.1.10:8501`)
- ดูจากที่ไหนก็ได้: deploy ฟรีที่ https://share.streamlit.io
  ถ้าใส่ Secrets `GITHUB_TOKEN` และ `GITHUB_REPO` แอพจะบันทึก watchlist ลง `watchlist.json` ใน GitHub repo (ถาวร)

## Symbol ที่ควรรู้
- หุ้นที่มีจุด ใช้ขีดแทน: `BRK-B`, `BF-B`
- Index: `^GSPC` (S&P 500), `^NDX` (Nasdaq 100), `^VIX`

## ข้อจำกัด
- ข้อมูลฟรีจาก Yahoo, delay ~15 นาทีช่วงตลาดเปิด ไม่ใช่ข้อมูลสำหรับเทรดแบบ real-time
- กราฟรายนาทีย้อนหลังได้ไม่เกินประมาณ 30–60 วัน (ข้อจำกัดของ Yahoo) ช่วงยาวจึงใช้แท่งรายวัน/สัปดาห์/เดือน
