import os
import time
import requests
import threading
from flask import Flask, request, jsonify
from datetime import datetime
import pytz

app = Flask(__name__)

LINE_TOKEN        = os.environ.get("LINE_TOKEN", "YOUR_LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID      = os.environ.get("LINE_USER_ID", "YOUR_LINE_USER_ID")
TWELVE_API_KEY    = os.environ.get("TWELVE_API_KEY", "YOUR_TWELVEDATA_API_KEY")
MANUAL_SUPPORT    = os.environ.get("MANUAL_SUPPORT", "")
MANUAL_RESIST     = os.environ.get("MANUAL_RESIST", "")
INTERVAL_MINUTES  = int(os.environ.get("INTERVAL_MINUTES", "15"))

BKK = pytz.timezone("Asia/Bangkok")

SL_PIPS = {
    "USDJPY": 15,
    "XAUUSD": 150,
    "EURUSD": 15,
}

SYMBOLS = {
    "USDJPY": {"name": "💴 USD/JPY",        "pip": 0.01},
    "XAUUSD": {"name": "🥇 Gold (XAU/USD)", "pip": 0.1},
    "EURUSD": {"name": "💵 EUR/USD",         "pip": 0.0001},
}

price_state = {sym: {"last_price": None, "alerted_levels": set()} for sym in SYMBOLS}

def get_price(symbol):
    try:
        url = f"https://api.twelvedata.com/price?symbol={symbol}&apikey={TWELVE_API_KEY}"
        r = requests.get(url, timeout=10)
        data = r.json()
        return float(data["price"]) if "price" in data else None
    except Exception:
        return None

def get_candles(symbol, interval="15min", outputsize=50):
    try:
        url = (f"https://api.twelvedata.com/time_series"
               f"?symbol={symbol}&interval={interval}"
               f"&outputsize={outputsize}&apikey={TWELVE_API_KEY}")
        r = requests.get(url, timeout=15)
        data = r.json()
        return data.get("values", [])
    except Exception:
        return []

def calc_auto_levels(candles):
    if len(candles) < 5:
        return {"support": [], "resistance": []}
    highs = [float(c["high"]) for c in candles]
    lows  = [float(c["low"])  for c in candles]
    supports, resistances = [], []
    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            supports.append(round(lows[i], 5))
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            resistances.append(round(highs[i], 5))
    def dedup(levels, n=3):
        seen = []
        for v in sorted(set(levels)):
            if not any(abs(v - s) < 0.0005 for s in seen):
                seen.append(v)
        return seen[-n:]
    return {"support": dedup(supports), "resistance": dedup(resistances)}

def get_manual_levels():
    sup = [float(x) for x in MANUAL_SUPPORT.split(",") if x.strip()] if MANUAL_SUPPORT else []
    res = [float(x) for x in MANUAL_RESIST.split(",")  if x.strip()] if MANUAL_RESIST  else []
    return {"support": sup, "resistance": res}

def merge_levels(auto, manual):
    return {
        "support":    sorted(set(auto["support"]    + manual["support"])),
        "resistance": sorted(set(auto["resistance"] + manual["resistance"])),
    }

def analyze_signal(candles, current_price):
    if len(candles) < 20:
        return {"trend": "ไม่พอข้อมูล", "signal": "WAIT", "rsi": None, "ema9": None, "ema21": None}
    closes = [float(c["close"]) for c in candles]
    def ema(data, period):
        k = 2 / (period + 1)
        e = data[0]
        for p in data[1:]:
            e = p * k + e * (1 - k)
        return e
    ema9  = ema(closes[-20:], 9)
    ema21 = ema(closes[-20:], 21)
    gains, losses = [], []
    for i in range(1, 15):
        diff = closes[-i] - closes[-i-1]
        (gains if diff > 0 else losses).append(abs(diff))
    avg_gain = sum(gains) / 14 if gains else 0
    avg_loss = sum(losses) / 14 if losses else 0.0001
    rsi = round(100 - (100 / (1 + avg_gain / avg_loss)), 1)
    trend  = "📈 ขาขึ้น" if ema9 > ema21 else "📉 ขาลง"
    if ema9 > ema21 and rsi < 70:
        signal = "🟢 BUY"
    elif ema9 < ema21 and rsi > 30:
        signal = "🔴 SELL"
    else:
        signal = "⏸ WAIT"
    return {"trend": trend, "signal": signal, "rsi": rsi, "ema9": round(ema9, 5), "ema21": round(ema21, 5)}

def calc_trade_plan(symbol, price, levels):
    pip     = SYMBOLS[symbol]["pip"]
    sl_pips = SL_PIPS.get(symbol, 15)
    sl_size = pip * sl_pips
    tp_size = sl_size * 2
    plan = {}
    if levels["support"]:
        nearest_sup = min(levels["support"], key=lambda x: abs(price - x))
        buy_entry = round(nearest_sup + pip * 2, 5)
        plan["buy"] = {
            "level": nearest_sup,
            "entry": buy_entry,
            "sl":    round(nearest_sup - sl_size, 5),
            "tp1":   round(buy_entry + tp_size, 5),
            "tp2":   round(buy_entry + tp_size * 1.5, 5),
            "sl_pips": sl_pips,
        }
    if levels["resistance"]:
        nearest_res = min(levels["resistance"], key=lambda x: abs(price - x))
        sell_entry = round(nearest_res - pip * 2, 5)
        plan["sell"] = {
            "level": nearest_res,
            "entry": sell_entry,
            "sl":    round(nearest_res + sl_size, 5),
            "tp1":   round(sell_entry - tp_size, 5),
            "tp2":   round(sell_entry - tp_size * 1.5, 5),
            "sl_pips": sl_pips,
        }
    return plan

def build_message(symbol, price, levels, analysis, trigger):
    now  = datetime.now(BKK).strftime("%d/%m/%Y %H:%M")
    name = SYMBOLS[symbol]["name"]
    sup_str = ", ".join(str(s) for s in levels["support"][-3:])    or "-"
    res_str = ", ".join(str(r) for r in levels["resistance"][-3:]) or "-"
    rsi_label = ""
    if analysis["rsi"] is not None:
        rsi = analysis["rsi"]
        rsi_label = "🔥 Overbought" if rsi > 70 else ("❄️ Oversold" if rsi < 30 else "✅ Normal")
    plan = calc_trade_plan(symbol, price, levels)

    buy_block = ""
    if "buy" in plan:
        b = plan["buy"]
        buy_block = (
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🟢 แผน BUY (เมื่อราคาถึงแนวรับ)\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 แนวรับ         : {b['level']}\n"
            f"🎯 เข้าซื้อที่      : {b['entry']}\n"
            f"🛑 Stop Loss    : {b['sl']}  (-{b['sl_pips']} pip)\n"
            f"✅ Take Profit 1 : {b['tp1']}  (RR 1:2)\n"
            f"✅ Take Profit 2 : {b['tp2']}  (RR 1:3)\n"
        )

    sell_block = ""
    if "sell" in plan:
        s = plan["sell"]
        sell_block = (
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔴 แผน SELL (เมื่อราคาถึงแนวต้าน)\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 แนวต้าน        : {s['level']}\n"
            f"🎯 เข้าขายที่       : {s['entry']}\n"
            f"🛑 Stop Loss    : {s['sl']}  (+{s['sl_pips']} pip)\n"
            f"✅ Take Profit 1 : {s['tp1']}  (RR 1:2)\n"
            f"✅ Take Profit 2 : {s['tp2']}  (RR 1:3)\n"
        )

    msg = (
        f"==============================\n"
        f"{name}\n"
        f"🕐 {now}  |  ⚡ {trigger}\n"
        f"==============================\n"
        f"💰 ราคาปัจจุบัน : {price}\n\n"
        f"📊 สัญญาณ : {analysis['signal']}\n"
        f"{analysis['trend']}\n"
        f"📐 EMA9={analysis.get('ema9','?')}  EMA21={analysis.get('ema21','?')}\n"
        f"📉 RSI({analysis['rsi']}) {rsi_label}\n\n"
        f"🟢 แนวรับ  : {sup_str}\n"
        f"🔴 แนวต้าน : {res_str}"
        f"{buy_block}"
        f"{sell_block}"
        f"\n==============================\n"
        f"⚠️ ใช้ประกอบการวิเคราะห์เท่านั้น\nไม่ใช่คำแนะนำการลงทุน"
    )
    return msg

def send_line(message):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try:
        r = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=10)
        print(f"[LINE] {r.status_code} {r.text[:80]}")
    except Exception as e:
        print(f"[LINE ERROR] {e}")

def near_level(price, level, pip_size, pips=10):
    return abs(price - level) <= pip_size * pips

def monitor_loop():
    tick = 0
    while True:
        tick += 1
        time.sleep(60)
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
                trigger  = None
                if tick % INTERVAL_MINUTES == 0:
                    trigger = f"รายงาน {INTERVAL_MINUTES} นาที"
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

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now(BKK).isoformat()})

@app.route("/webhook", methods=["POST"])
def webhook():
    return jsonify({"status": "ok"})

@app.route("/test", methods=["GET"])
def test_send():
    for symbol in SYMBOLS:
        price    = get_price(symbol)
        candles  = get_candles(symbol)
        auto_lvl = calc_auto_levels(candles)
        man_lvl  = get_manual_levels()
        levels   = merge_levels(auto_lvl, man_lvl)
        analysis = analyze_signal(candles, price or 0)
        msg = build_message(symbol, price or 0, levels, analysis, "🔧 ทดสอบระบบ")
        send_line(msg)
    return jsonify({"status": "sent", "symbols": list(SYMBOLS.keys())})

if __name__ == "__main__":
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    print("✅ Forex Line Bot started")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
