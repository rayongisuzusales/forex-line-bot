# 📊 Forex Line Bot — คู่มือติดตั้ง

ระบบแจ้งเตือนแผนการเทรด Forex/Gold ผ่าน LINE OA
ติดตาม: USDJPY | EURUSD | XAUUSD (Gold)

---

## ✅ สิ่งที่ต้องเตรียม

| รายการ | ลิงก์สมัคร | ค่าใช้จ่าย |
|---|---|---|
| LINE OA + Messaging API | https://developers.line.biz | ฟรี |
| Twelve Data API Key | https://twelvedata.com | ฟรี (800 req/วัน) |
| GitHub Account | https://github.com | ฟรี |
| Render Account | https://render.com | ฟรี |

---

## 🔑 STEP 1 — เตรียม LINE OA Token

1. ไปที่ https://developers.line.biz
2. สร้าง Provider → สร้าง Channel (Messaging API)
3. ไปที่ Basic Settings → คัดลอก **Channel Access Token**
4. หา **User ID** ของคุณ:
   - ไปที่ LINE Official Account Manager
   - เพิ่มเพื่อน OA แล้วส่งข้อความหาตัวเอง
   - ดู Webhook log หรือใช้ https://api.line.me/v2/profile

---

## 🔑 STEP 2 — เตรียม Twelve Data API Key

1. สมัครที่ https://twelvedata.com
2. ไปที่ Dashboard → API Keys → คัดลอก API Key

---

## 🐙 STEP 3 — อัปโหลดขึ้น GitHub

```bash
git init
git add .
git commit -m "Initial forex bot"
git remote add origin https://github.com/USERNAME/forex-line-bot.git
git push -u origin main
```

---

## 🚀 STEP 4 — Deploy บน Render

1. ไปที่ https://render.com → New → Web Service
2. เชื่อมต่อ GitHub repo ที่เพิ่ง push
3. ตั้งค่า:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4`
4. ไปที่ **Environment** → เพิ่ม Environment Variables:

```
LINE_TOKEN          = <Channel Access Token>
LINE_USER_ID        = <User ID หรือ Group ID>
TWELVE_API_KEY      = <Twelve Data API Key>
MANUAL_SUPPORT      = (เว้นว่าง หรือ เช่น 147.50,148.00)
MANUAL_RESIST       = (เว้นว่าง หรือ เช่น 149.50,150.00)
INTERVAL_MINUTES    = 15
```

5. คลิก **Deploy**

---

## 🧪 STEP 5 — ทดสอบ

หลัง Deploy สำเร็จ เรียก URL นี้จากบราวเซอร์:

```
https://your-app-name.onrender.com/test
```

ถ้าได้รับข้อความใน LINE = ✅ ทำงานแล้ว!

---

## 📱 ตัวอย่างข้อความที่จะได้รับ

```
==============================
💴 USD/JPY
🕐 25/04/2025 14:15  |  ⚡ รายงาน 15 นาที
==============================
💰 ราคาปัจจุบัน : 154.320

📊 สัญญาณ : 🟢 BUY
📈 ขาขึ้น
📐 EMA9=154.180  EMA21=153.950
📉 RSI(52.3) ✅ Normal

🟢 แนวรับ  : 153.800, 154.000, 154.100
🔴 แนวต้าน : 154.500, 154.800, 155.000
==============================
⚠️ ใช้ประกอบการวิเคราะห์เท่านั้น ไม่ใช่คำแนะนำการลงทุน
```

---

## ⚙️ ปรับแต่งเพิ่มเติม

### เพิ่มแนวรับ/แนวต้าน Manual
แก้ Environment Variables บน Render:
```
MANUAL_SUPPORT=153.500,154.000
MANUAL_RESIST=155.000,155.500
```

### เปลี่ยน interval
```
INTERVAL_MINUTES=30   # แจ้งทุก 30 นาที
```

---

## ⚠️ หมายเหตุ Render Free Tier

Render Free จะ **sleep หลังไม่มีการใช้งาน 15 นาที**
แก้ด้วยการใช้ UptimeRobot (ฟรี) ping ทุก 10 นาที:
1. สมัคร https://uptimerobot.com
2. สร้าง HTTP Monitor ชี้ไปที่ `https://your-app.onrender.com/`
3. ตั้ง interval = 10 นาที
