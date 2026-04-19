import os
import requests
import time
import hmac
import hashlib
import json
from datetime import datetime

API_KEY      = os.environ.get("cf026d2ec839ca9fd7a39e38ba760d54", "")
API_SECRET   = os.environ.get("4a5a0f610536a56d551d99c86c858c34", "")
SYMBOL       = "BTCUSDT"
SL_PTS       = 50
RR           = 5
TP_PTS       = SL_PTS * RR
BALANCE      = 10000
RISK_PCT     = 0.01
MAX_LOSSES   = 2
BASE_URL     = "https://api.sharkexchange.in"
PUBLIC_URL   = "https://api.sharkexchange.in"

daily_losses = 0
last_day     = datetime.now().date()

def generate_signature(api_secret, data_to_sign):
    return hmac.new(
        api_secret.encode('utf-8'),
        data_to_sign.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

# ── PUBLIC endpoint — GET with query params, no auth needed ──
def get_candles(limit=60):
    try:
        params = {
            "symbol":    SYMBOL,
            "interval":  "5m",
            "limit":     str(limit),
            "priceType": "LAST_PRICE"
        }
        r = requests.get(
            f"{PUBLIC_URL}/v1/market/klines",
            params=params,
            timeout=10
        )
        resp = r.json()
        print(f"Candle {r.status_code}: {str(resp)[:200]}")
        for key in ["data", "result", "klines", "candles", "list"]:
            if key in resp and isinstance(resp[key], list) and len(resp[key]) > 0:
                return resp[key]
        if isinstance(resp, list) and len(resp) > 0:
            return resp
        return []
    except Exception as e:
        print(f"Candle error: {e}")
        return []

def parse_candle(c):
    # Handle both dict format and list/array format
    if isinstance(c, dict):
        return {
            'o': float(c.get('open',   c.get('o', 0))),
            'h': float(c.get('high',   c.get('h', 0))),
            'l': float(c.get('low',    c.get('l', 0))),
            'c': float(c.get('close',  c.get('c', 0))),
            'v': float(c.get('volume', c.get('v', 0))),
        }
    else:
        # array format [time, open, high, low, close, volume]
        return {'o': float(c[1]), 'h': float(c[2]),
                'l': float(c[3]), 'c': float(c[4]), 'v': float(c[5])}

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
        c  = parse_candle(candles[i])
        pc = parse_candle(candles[i-1])
        tr  = max(c['h'] - c['l'], abs(c['h'] - pc['c']), abs(c['l'] - pc['c']))
        pdm = max(c['h'] - pc['h'], 0) if (c['h'] - pc['h']) > (pc['l'] - c['l']) else 0
        mdm = max(pc['l'] - c['l'], 0) if (pc['l'] - c['l']) > (c['h'] - pc['h']) else 0
        trs.append(tr); pdms.append(pdm); mdms.append(mdm)
    def rma(vals, p):
        r = sum(vals[:p]) / p
        for v in vals[p:]: r = (r * (p - 1) + v) / p
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
        c  = parse_candle(candles[i])
        pc = parse_candle(candles[i-1])
        trs.append(max(c['h'] - c['l'], abs(c['h'] - pc['c']), abs(c['l'] - pc['c'])))
    def rma(vals, p):
        r = sum(vals[:p]) / p
        for v in vals[p:]: r = (r * (p - 1) + v) / p
        return r
    return rma(trs, period) / (sum(trs[-20:]) / 20 + 0.0001)

def compute_volz(candles, period=20):
    if len(candles) < period:
        return 0
    vols = [parse_candle(c)['v'] for c in candles[-period:]]
    mean = sum(vols) / period
    std  = (sum((v - mean) ** 2 for v in vols) / period) ** 0.5
    return (vols[-1] - mean) / (std + 0.0001)

def compute_bodyr(c):
    p = parse_candle(c)
    return abs(p['c'] - p['o']) / (p['h'] - p['l'] + 0.0001)

def detect_fvg(candles):
    c0 = parse_candle(candles[-1])
    c1 = parse_candle(candles[-2])
    c2 = parse_candle(candles[-3])
    bear_fvg = c0['h'] < c2['l'] and c1['c'] < c1['o']
    bull_fvg = c0['l'] > c2['h'] and c1['c'] > c1['o']
    return bear_fvg, bull_fvg

def check_filters(candles, is_long):
    adx, _, _ = compute_adx(candles)
    closes    = [parse_candle(c)['c'] for c in candles]
    e20       = ema(closes, 20)
    e50       = ema(closes, 50)
    atr_ratio = compute_atr_ratio(candles)
    volz      = compute_volz(candles)
    bodyr     = compute_bodyr(candles[-2])
    ema_ok    = e20 > e50 if is_long else e20 < e50
    sess_ok   = 7 <= datetime.utcnow().hour < 21
    conf = sum([adx > 28, atr_ratio > 1.1, volz > 1.0, ema_ok, bodyr > 0.5])
    print(f"ADX:{adx:.1f} AtrR:{atr_ratio:.2f} VolZ:{volz:.2f} Body:{bodyr:.2f} EMA:{'OK' if ema_ok else 'NO'} Sess:{'OK' if sess_ok else 'NO'} Conf:{conf}/5")
    return adx > 28 and ema_ok and atr_ratio > 1.0 and volz > 1.0 and bodyr > 0.5 and conf >= 4 and sess_ok

def get_qty():
    return max(round((BALANCE * RISK_PCT) / SL_PTS, 4), 0.0001)

def place_order(side, qty, sl_price, tp_price):
    timestamp = str(int(time.time() * 1000))
    params = {
        'timestamp':       timestamp,
        'placeType':       'ORDER_FORM',
        'quantity':        qty,
        'side':            side,
        'symbol':          SYMBOL,
        'type':            'MARKET',
        'reduceOnly':      False,
        'marginAsset':     'INR',
        'deviceType':      'WEB',
        'userCategory':    'EXTERNAL',
        'stopLossPrice':   sl_price,
        'takeProfitPrice': tp_price,
    }
    data_to_sign = json.dumps(params, separators=(',', ':'))
    signature    = generate_signature(API_SECRET, data_to_sign)
    headers = {
        'api-key':      API_KEY,
        'signature':    signature,
        'Content-Type': 'application/json'
    }
    try:
        r = requests.post(
            f"{BASE_URL}/v1/order/place-order",
            json=params, headers=headers, timeout=10
        )
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
            print("Max losses hit - waiting...")
            time.sleep(300)
            continue

        candles = get_candles(60)
        if len(candles) < 55:
            print(f"Not enough candles ({len(candles)}) - retrying...")
            time.sleep(30)
            continue

        now_str  = datetime.utcnow().strftime('%H:%M:%S UTC')
        price    = parse_candle(candles[-1])['c']
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
