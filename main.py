import os
import requests
import time
import hmac
import hashlib
import json
from datetime import datetime

API_KEY    = os.environ.get("cf026d2ec839ca9fd7a39e38ba760d54", "")
API_SECRET = os.environ.get("4a5a0f610536a56d551d99c86c858c34", "")
SYMBOL     = "BTCUSDT"
SL_PTS     = 50
RR         = 5
TP_PTS     = SL_PTS * RR
BALANCE    = 10000
RISK_PCT   = 0.01
MAX_LOSSES = 2
BASE_URL   = "https://api.sharkexchange.in"

daily_losses = 0
last_day     = datetime.now().date()

def sign(data):
    body = json.dumps(data, separators=(',', ':'))
    sig  = hmac.new(API_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return body, sig

def get_candles(limit=60):
    try:
        r = requests.get(
            f"{BASE_URL}/v1/market/klines",
            params={"symbol": SYMBOL, "interval": "5m", "limit": limit},
            timeout=10
        )
        return r.json().get("data", [])
    except Exception as e:
        print(f"Candle error: {e}")
        return []

def ema(values, period):
    if len(values) < period:
        return values[-1]
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e

def compute_adx(candles, period=14):
    if len(candles) < period + 2:
        return 0, 0, 0
    trs, pdms, mdms = [], [], []
    for i in range(1, len(candles)):
        h  = float(candles[i]['high'])
        l  = float(candles[i]['low'])
        ph = float(candles[i-1]['high'])
        pl = float(candles[i-1]['low'])
        pc = float(candles[i-1]['close'])
        tr  = max(h - l, abs(h - pc), abs(l - pc))
        pdm = max(h - ph, 0) if (h - ph) > (pl - l) else 0
        mdm = max(pl - l, 0) if (pl - l) > (h - ph) else 0
        trs.append(tr)
        pdms.append(pdm)
        mdms.append(mdm)
    def rma(vals, p):
        r = sum(vals[:p]) / p
        for v in vals[p:]:
            r = (r * (p - 1) + v) / p
        return r
    atr = rma(trs, period)
    pdi = 100 * rma(pdms, period) / (atr + 0.0001)
    mdi = 100 * rma(mdms, period) / (atr + 0.0001)
    dx  = 100 * abs(pdi - mdi) / (pdi + mdi + 0.0001)
    return dx, pdi, mdi

def compute_atr_ratio(candles, period=14):
    if len(candles) < period + 5:
        return 1.0
    trs = []
    for i in range(1, len(candles)):
        h  = float(candles[i]['high'])
        l  = float(candles[i]['low'])
        pc = float(candles[i-1]['close'])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    def rma(vals, p):
        r = sum(vals[:p]) / p
        for v in vals[p:]:
            r = (r * (p - 1) + v) / p
        return r
    atrval = rma(trs, period)
    atravg = sum(trs[-20:]) / 20
    return atrval / (atravg + 0.0001)

def compute_volz(candles, period=20):
    if len(candles) < period:
        return 0
    vols = [float(c['volume']) for c in candles[-period:]]
    mean = sum(vols) / period
    std  = (sum((v - mean) ** 2 for v in vols) / period) ** 0.5
    return (vols[-1] - mean) / (std + 0.0001)

def compute_bodyr(c):
    body = abs(float(c['close']) - float(c['open']))
    wick = float(c['high']) - float(c['low'])
    return body / (wick + 0.0001)

def detect_fvg(candles):
    c0 = candles[-1]
    c1 = candles[-2]
    c2 = candles[-3]
    bear_fvg = (float(c0['high']) < float(c2['low'])
                and float(c1['close']) < float(c1['open']))
    bull_fvg = (float(c0['low']) > float(c2['high'])
                and float(c1['close']) > float(c1['open']))
    return bear_fvg, bull_fvg

def check_filters(candles, is_long):
    adx, pdi, mdi = compute_adx(candles)
    closes        = [float(c['close']) for c in candles]
    e20           = ema(closes, 20)
    e50           = ema(closes, 50)
    atr_ratio     = compute_atr_ratio(candles)
    volz          = compute_volz(candles)
    bodyr         = compute_bodyr(candles[-2])
    ema_ok        = e20 > e50 if is_long else e20 < e50
    now_utc       = datetime.utcnow().hour
    session_ok    = 7 <= now_utc < 21
    c1 = 1 if adx > 28        else 0
    c2 = 1 if atr_ratio > 1.1 else 0
    c3 = 1 if volz > 1.0      else 0
    c4 = 1 if ema_ok           else 0
    c5 = 1 if bodyr > 0.5     else 0
    confluence = c1 + c2 + c3 + c4 + c5
    print(f"ADX:{adx:.1f} AtrR:{atr_ratio:.2f} VolZ:{volz:.2f} Body:{bodyr:.2f} EMA:{'OK' if ema_ok else 'NO'} Sess:{'OK' if session_ok else 'NO'} Conf:{confluence}/5")
    return (adx > 28 and ema_ok and atr_ratio > 1.0
            and volz > 1.0 and bodyr > 0.5
            and confluence >= 4 and session_ok)

def get_qty():
    return max(round((BALANCE * RISK_PCT) / SL_PTS, 4), 0.0001)

def place_order(side, qty, sl_price, tp_price):
    ts   = str(int(time.time() * 1000))
    data = {
        "timestamp": ts, "symbol": SYMBOL,
        "side": side, "type": "MARKET",
        "quantity": str(qty),
        "stopLossPrice": str(sl_price),
        "takeProfitPrice": str(tp_price),
        "marginAsset": "INR", "reduceOnly": False
    }
    body, sig = sign(data)
    headers = {"api-key": API_KEY, "signature": sig, "Content-Type": "application/json"}
    try:
        r = requests.post(f"{BASE_URL}/v1/order/place-order", data=body, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Order error: {e}")
        return {}

print("BTC Rev FVG 5m Algo started")
print(f"SL={SL_PTS} TP={TP_PTS} Risk={RISK_PCT*100}% MaxLoss={MAX_LOSSES}/day")

while True:
    try:
        today = datetime.now().date()
        if today != last_day:
            daily_losses = 0
            last_day = today
            print("New day - loss counter reset")

        if daily_losses >= MAX_LOSSES:
            print(f"Max losses hit - waiting...")
            time.sleep(300)
            continue

        candles = get_candles(60)
        if len(candles) < 55:
            print("Not enough candles - retrying...")
            time.sleep(30)
            continue

        now_str  = datetime.utcnow().strftime('%H:%M:%S UTC')
        price    = float(candles[-1]['close'])
        bear_fvg, bull_fvg = detect_fvg(candles)

        if bear_fvg and check_filters(candles, is_long=True):
            sl  = round(price - SL_PTS, 2)
            tp  = round(price + TP_PTS, 2)
            qty = get_qty()
            print(f"LONG | {now_str} | Entry:{price} SL:{sl} TP:{tp} Qty:{qty}")
            result = place_order("BUY", qty, sl, tp)
            print(f"Result: {result}")
            time.sleep(310)

        elif bull_fvg and check_filters(candles, is_long=False):
            sl  = round(price + SL_PTS, 2)
            tp  = round(price - TP_PTS, 2)
            qty = get_qty()
            print(f"SHORT | {now_str} | Entry:{price} SL:{sl} TP:{tp} Qty:{qty}")
            result = place_order("SELL", qty, sl, tp)
            print(f"Result: {result}")
            time.sleep(310)

        else:
            print(f"No signal | {now_str} | BTC:{price}")
            time.sleep(60)

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(30)
