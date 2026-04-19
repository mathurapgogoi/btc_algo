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

# Two different base URLs as per official docs
BASE_URL   = "https://api.sharkexchange.in"
PUBLIC_URL = "https://marketdata.sharkexchange.in"

daily_losses = 0
last_day     = datetime.now().date()

def generate_signature(secret, data):
    return hmac.new(secret.encode('utf-8'), data.encode('utf-8'), hashlib.sha256).hexdigest()

# klines is POST on publicBaseUrl — no auth needed
BASE_URL   = "https://api.sharkexchange.in"

def get_candles(limit=60):
    urls_to_try = [
        ("POST", "https://api.sharkexchange.in/v1/market/klines"),
        ("POST", "https://market.sharkexchange.in/v1/market/klines"),
        ("POST", "https://data.sharkexchange.in/v1/market/klines"),
        ("POST", "https://public.sharkexchange.in/v1/market/klines"),
    ]
    body = {"symbol": SYMBOL, "interval": "5m", "limit": limit, "priceType": "LAST_PRICE"}
    for method, url in urls_to_try:
        try:
            r = requests.post(url, json=body, timeout=5)
            print(f"{r.status_code} {url[:50]}: {str(r.json())[:100]}")
            if r.status_code == 200:
                resp = r.json()
                for key in ["data", "result", "klines", "candles", "list"]:
                    if key in resp and isinstance(resp[key], list) and len(resp[key]) > 5:
                        return resp[key]
                if isinstance(resp, list) and len(resp) > 5:
                    return resp
        except Exception as e:
            print(f"Error {url[:40]}: {e}")
    return []
    

def p(c, k):
    if isinstance(c, dict):
        return float(c.get(k, 0))
    idx = {'o':1,'h':2,'l':3,'c':4,'v':5}
    return float(c[idx[k]])

def ema(values, period):
    if len(values) < period: return values[-1]
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]: e = v * k + e * (1 - k)
    return e

def compute_adx(candles, period=14):
    if len(candles) < period + 2: return 0, 0, 0
    trs, pdms, mdms = [], [], []
    for i in range(1, len(candles)):
        h, l, ph, pl, pc = p(candles[i],'h'), p(candles[i],'l'), p(candles[i-1],'h'), p(candles[i-1],'l'), p(candles[i-1],'c')
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
        pdms.append(max(h-ph,0) if (h-ph)>(pl-l) else 0)
        mdms.append(max(pl-l,0) if (pl-l)>(h-ph) else 0)
    def rma(vals, n):
        r = sum(vals[:n])/n
        for v in vals[n:]: r = (r*(n-1)+v)/n
        return r
    atr = rma(trs, period)
    pdi = 100*rma(pdms,period)/(atr+0.0001)
    mdi = 100*rma(mdms,period)/(atr+0.0001)
    return 100*abs(pdi-mdi)/(pdi+mdi+0.0001), pdi, mdi

def compute_atr_ratio(candles, period=14):
    if len(candles) < period+5: return 1.0
    trs = [max(p(candles[i],'h')-p(candles[i],'l'), abs(p(candles[i],'h')-p(candles[i-1],'c')), abs(p(candles[i],'l')-p(candles[i-1],'c'))) for i in range(1,len(candles))]
    def rma(vals, n):
        r = sum(vals[:n])/n
        for v in vals[n:]: r = (r*(n-1)+v)/n
        return r
    return rma(trs, period) / (sum(trs[-20:])/20 + 0.0001)

def compute_volz(candles, period=20):
    if len(candles) < period: return 0
    vols = [p(c,'v') for c in candles[-period:]]
    mean = sum(vols)/period
    std  = (sum((v-mean)**2 for v in vols)/period)**0.5
    return (vols[-1]-mean)/(std+0.0001)

def detect_fvg(candles):
    c0,c1,c2 = candles[-1],candles[-2],candles[-3]
    bear = p(c0,'h') < p(c2,'l') and p(c1,'c') < p(c1,'o')
    bull = p(c0,'l') > p(c2,'h') and p(c1,'c') > p(c1,'o')
    return bear, bull

def check_filters(candles, is_long):
    adx,_,_ = compute_adx(candles)
    closes   = [p(c,'c') for c in candles]
    ema_ok   = ema(closes,20) > ema(closes,50) if is_long else ema(closes,20) < ema(closes,50)
    atr_r    = compute_atr_ratio(candles)
    volz     = compute_volz(candles)
    bodyr    = abs(p(candles[-2],'c')-p(candles[-2],'o'))/(p(candles[-2],'h')-p(candles[-2],'l')+0.0001)
    sess_ok  = 7 <= datetime.utcnow().hour < 21
    conf     = sum([adx>28, atr_r>1.1, volz>1.0, ema_ok, bodyr>0.5])
    print(f"ADX:{adx:.1f} AtrR:{atr_r:.2f} VolZ:{volz:.2f} Body:{bodyr:.2f} EMA:{'OK' if ema_ok else 'NO'} Sess:{'OK' if sess_ok else 'NO'} Conf:{conf}/5")
    return adx>28 and ema_ok and atr_r>1.0 and volz>1.0 and bodyr>0.5 and conf>=4 and sess_ok

def place_order(side, qty, sl, tp):
    ts = str(int(time.time()*1000))
    params = {'timestamp':ts,'placeType':'ORDER_FORM','quantity':qty,'side':side,
              'symbol':SYMBOL,'type':'MARKET','reduceOnly':False,'marginAsset':'INR',
              'deviceType':'WEB','userCategory':'EXTERNAL','stopLossPrice':sl,'takeProfitPrice':tp}
    sig = generate_signature(API_SECRET, json.dumps(params, separators=(',',':')))
    headers = {'api-key':API_KEY,'signature':sig,'Content-Type':'application/json'}
    try:
        r = requests.post(f"{BASE_URL}/v1/order/place-order", json=params, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Order error: {e}"); return {}

print("BTC Rev FVG 5m Algo started")
print(f"SL={SL_PTS} TP={TP_PTS} Risk={RISK_PCT*100}% MaxLoss={MAX_LOSSES}/day")

while True:
    try:
        today = datetime.now().date()
        if today != last_day:
            daily_losses = 0; last_day = today
            print("New day - loss counter reset")
        if daily_losses >= MAX_LOSSES:
            print("Max losses - waiting..."); time.sleep(300); continue

        candles = get_candles(60)
        if len(candles) < 55:
            print(f"Not enough candles ({len(candles)}) - retrying...")
            time.sleep(30); continue

        now_str = datetime.utcnow().strftime('%H:%M:%S UTC')
        price   = p(candles[-1],'c')
        bear, bull = detect_fvg(candles)

        if bear and check_filters(candles, True):
            sl,tp,qty = round(price-SL_PTS,2), round(price+TP_PTS,2), max(round((BALANCE*RISK_PCT)/SL_PTS,4),0.0001)
            print(f"LONG | {now_str} | Entry:{price} SL:{sl} TP:{tp} Qty:{qty}")
            print(f"Result: {place_order('BUY',qty,sl,tp)}")
            time.sleep(310)
        elif bull and check_filters(candles, False):
            sl,tp,qty = round(price+SL_PTS,2), round(price-TP_PTS,2), max(round((BALANCE*RISK_PCT)/SL_PTS,4),0.0001)
            print(f"SHORT | {now_str} | Entry:{price} SL:{sl} TP:{tp} Qty:{qty}")
            print(f"Result: {place_order('SELL',qty,sl,tp)}")
            time.sleep(310)
        else:
            print(f"No signal | {now_str} | BTC:{price}")
            time.sleep(60)

    except Exception as e:
        print(f"Error: {e}"); time.sleep(30)
