import os
import time
import requests
import threading
from flask import Flask, request, jsonify
from datetime import datetime, timezone
import pytz

app = Flask(__name__)

# ============================================================
# CONFIG — ใส่ค่าใน Environment Variables บน Render
# ============================================================
LINE_TOKEN        = os.environ.get("LINE_TOKEN", "YOUR_LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID      = os.environ.get("LINE_USER_ID", "YOUR_LINE_USER_ID")  # UID หรือ Group ID
TWELVE_API_KEY    = os.environ.get("TWELVE_API_KEY", "YOUR_TWELVEDATA_API_KEY")
MANUAL_SUPPORT    = os.environ.get("MANUAL_SUPPORT", "")   # แนวรับ Manual เช่น "1.3400,1.3420"
MANUAL_RESIST     = os.environ.get("MANUAL_RESIST", "")    # แนวต้าน Manual เช่น "1.3480,1.3500"
INTERVAL_MINUTES  = int(os.environ.get("INTERVAL_MINUTES", "15"))

BKK = pytz.timezone("Asia/Bangkok")

# สัญลักษณ์ที่ติดตาม
SYMBOLS = {
    "USDJPY": {"name": "💴 USD/JPY", "pip": 0.01},
    "XAUUSD": {"name": "🥇 Gold (XAU/USD)", "pip": 0.1},
    "EURUSD": {"name": "💵 EUR/USD", "pip": 0.0001},
}

# เก็บ state ราคาเพื่อตรวจแนวรับ/แนวต้าน
price_state = {sym: {"last_price": None, "alerted_levels": set()} for sym in SYMBOLS}

# ============================================================
# Twelve Data — ดึงราคาและแท่งเทียน
# ============================================================
def get_price(symbol: str) -> float | None:
    try:
        url = f"https://api.twelvedata.com/price?symbol={symbol}&apikey={TWELVE_API_KEY}"
        r = requests.get(url, timeout=10)
        data = r.json()
        return float(data.get("price", 0)) if "price" in data else None
    except Exception:
        return None

def get_candles(symbol: str, interval: str = "15min", outputsize: int = 50) -> list:
    try:
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={symbol}&interval={interval}"
            f"&outputsize={outputsize}&apikey={TWELVE_API_KEY}"
        )
        r = requests.get(url, timeout=15)
        data = r.json()
        if "values" not in data:
            return []
        return data["values"]
    except Exception:
        return []

# ============================================================
# คำนวณแนวรับ/แนวต้าน
# ============================================================
def calc_auto_levels(candles: list) -> dict:
    """คำนวณจาก High/Low ย้อนหลัง 50 แท่ง"""
    if len(candles) < 5:
        return {"support": [], "resistance": []}

    highs = [float(c["high"]) for c in candles]
    lows  = [float(c["low"])  for c in candles]

    # หา Swing High / Swing Low
    supports    = []
    resistances = []

    for i in range(2, len(lows) - 2):
        # Swing Low
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] \
           and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            supports.append(round(lows[i], 5))
        # Swing High
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] \
           and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            resistances.append(round(highs[i], 5))

    # ดึงเฉพาะ 3 ระดับล่าสุดที่ไม่ซ้ำกัน
    def dedup(levels, n=3):
        seen = []
        for v in sorted(set(levels)):
            if not any(abs(v - s) < 0.0005 for s in seen):
                seen.append(v)
        return seen[-n:]

    return {
        "support":    dedup(supports),
        "resistance": dedup(resistances),
    }

def get_manual_levels() -> dict:
    """ดึงระดับที่ผู้ใช้กำหนดเอง"""
    sup = [float(x) for x in MANUAL_SUPPORT.split(",") if x.strip()] if MANUAL_SUPPORT else []
    res = [float(x) for x in MANUAL_RESIST.split(",")  if x.strip()] if MANUAL_RESIST  else []
    return {"support": sup, "resistance": res}

def merge_levels(auto: dict, manual: dict) -> dict:
    return {
        "support":    sorted(set(auto["support"]    + manual["support"])),
        "resistance": sorted(set(auto["resistance"] + manual["resistance"])),
    }

# ============================================================
# วิเคราะห์ Trend & Signal
# ============================================================
def analyze_signal(candles: list, current_price: float) -> dict:
    if len(candles) < 20:
        return {"trend": "ไม่พอข้อมูล", "signal": "WAIT", "rsi": None}

    closes = [float(c["close"]) for c in candles]

    # EMA 9 / EMA 21
    def ema(data, period):
        k = 2 / (period + 1)
        e = data[0]
        for p in data[1:]:
            e = p * k + e * (1 - k)
        return e

    ema9  = ema(closes[-20:], 9)
    ema21 = ema(closes[-20:], 21)

    # RSI 14
    gains, losses = [], []
    for i in range(1, 15):
        diff = closes[-i] - closes[-i-1]
        (gains if diff > 0 else losses).append(abs(diff))
    avg_gain = sum(gains) / 14 if gains else 0
    avg_loss = sum(losses) / 14 if losses else 0.0001
    rsi = round(100 - (100 / (1 + avg_gain / avg_loss)), 1)

    # Trend
    trend  = "📈 ขาขึ้น" if ema9 > ema21 else "📉 ขาลง"

    # Signal
    if ema9 > ema21 and rsi < 70:
        signal = "🟢 BUY"
    elif ema9 < ema21 and rsi > 30:
        signal = "🔴 SELL"
    else:
        signal = "⏸ WAIT"

    return {"trend": trend, "signal": signal, "rsi": rsi, "ema9": round(ema9, 5), "ema21": round(ema21, 5)}

# ============================================================
# สร้างข้อความแจ้งเตือน
# ============================================================
def build_message(symbol: str, price: float, levels: dict, analysis: dict, trigger: str) -> str:
    now   = datetime.now(BKK).strftime("%d/%m/%Y %H:%M")
    name  = SYMBOLS[symbol]["name"]

    sup_str = ", ".join(str(s) for s in levels["support"][-3:])    or "-"
    res_str = ", ".join(str(r) for r in levels["resistance"][-3:]) or "-"

    rsi_bar = ""
    if analysis["rsi"] is not None:
        rsi = analysis["rsi"]
        rsi_bar = f"{'🔥 Overbought' if rsi > 70 else ('❄️ Oversold' if rsi < 30 else '✅ Normal')}"

    msg = f"""
{'='*30}
{name}
🕐 {now}  |  ⚡ {trigger}
{'='*30}
💰 ราคาปัจจุบัน : {price}

📊 สัญญาณ : {analysis['signal']}
{analysis['trend']}
📐 EMA9={analysis.get('ema9','?')}  EMA21={analysis.get('ema21','?')}
📉 RSI({analysis['rsi']}) {rsi_bar}

🟢 แนวรับ  : {sup_str}
🔴 แนวต้าน : {res_str}
{'='*30}
⚠️ ใช้ประกอบการวิเคราะห์เท่านั้น ไม่ใช่คำแนะนำการลงทุน
""".strip()
    return msg

# ============================================================
# ส่งข้อความ LINE
# ============================================================
def send_line(message: str):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_TOKEN}",
    }
    # ส่งหาผู้ใช้คนเดียว (userId) หรือกลุ่ม
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": message}],
    }
    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers=headers,
            json=payload,
            timeout=10,
        )
        print(f"[LINE] {r.status_code} {r.text[:80]}")
    except Exception as e:
        print(f"[LINE ERROR] {e}")

# ============================================================
# ตรวจว่าราคาใกล้แนวรับ/แนวต้านไหม
# ============================================================
def near_level(price: float, level: float, pip_size: float, pips: int = 10) -> bool:
    return abs(price - level) <= pip_size * pips

# ============================================================
# Loop หลัก — รันทุก 1 นาที ตรวจสอบทุกเงื่อนไข
# ============================================================
def monitor_loop():
    tick = 0
    while True:
        tick += 1
        time.sleep(60)  # ตรวจทุก 1 นาที

        for symbol, info in SYMBOLS.items():
            try:
                price = get_price(symbol)
                if price is None:
                    continue

                candles  = get_candles(symbol)
                auto_lvl = calc_auto_levels(candles)
                man_lvl  = get_manual_levels()
                levels   = merge_levels(auto_lvl, man_lvl)
                analysis = analyze_signal(candles, price)

                trigger = None

                # --- เงื่อนไข 1: ทุก 15 นาที ---
                if tick % INTERVAL_MINUTES == 0:
                    trigger = f"รายงาน {INTERVAL_MINUTES} นาที"

                # --- เงื่อนไข 2: ราคาชนแนวรับ/แนวต้าน ---
                pip = info["pip"]
                for lvl in levels["support"]:
                    key = f"S{lvl}"
                    if near_level(price, lvl, pip) and key not in price_state[symbol]["alerted_levels"]:
                        trigger = f"⚡ ราคาถึงแนวรับ {lvl}"
                        price_state[symbol]["alerted_levels"].add(key)
                    elif not near_level(price, lvl, pip):
                        price_state[symbol]["alerted_levels"].discard(key)

                for lvl in levels["resistance"]:
                    key = f"R{lvl}"
                    if near_level(price, lvl, pip) and key not in price_state[symbol]["alerted_levels"]:
                        trigger = f"⚡ ราคาถึงแนวต้าน {lvl}"
                        price_state[symbol]["alerted_levels"].add(key)
                    elif not near_level(price, lvl, pip):
                        price_state[symbol]["alerted_levels"].discard(key)

                if trigger:
                    msg = build_message(symbol, price, levels, analysis, trigger)
                    send_line(msg)
                    print(f"[SENT] {symbol} @ {price} | {trigger}")

            except Exception as e:
                print(f"[ERROR] {symbol}: {e}")

# ============================================================
# Flask Webhook (รับ LINE Event) + Health Check
# ============================================================
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now(BKK).isoformat()})

@app.route("/webhook", methods=["POST"])
def webhook():
    # รองรับ Webhook verify จาก LINE
    return jsonify({"status": "ok"})

@app.route("/test", methods=["GET"])
def test_send():
    """ทดสอบส่งข้อความ — เรียก /test จากบราวเซอร์"""
    for symbol, info in SYMBOLS.items():
        price    = get_price(symbol)
        candles  = get_candles(symbol)
        auto_lvl = calc_auto_levels(candles)
        man_lvl  = get_manual_levels()
        levels   = merge_levels(auto_lvl, man_lvl)
        analysis = analyze_signal(candles, price or 0)
        msg = build_message(symbol, price or 0, levels, analysis, "🔧 ทดสอบระบบ")
        send_line(msg)
    return jsonify({"status": "sent", "symbols": list(SYMBOLS.keys())})

# ============================================================
# Start
# ============================================================
if __name__ == "__main__":
    # รัน monitor ใน background thread
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    print("✅ Forex Line Bot started")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
