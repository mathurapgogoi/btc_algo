import os
import requests
import time
import hmac
import hashlib
import json
from datetime import datetime, timezone

# ─── CONFIG ────────────────────────────────────────────────
API_KEY    = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")

SYMBOL     = "BTCUSDT"
BALANCE    = 5.40        # ₹500 = ~$5.40 USD
RISK_PCT   = 0.02        # 2% risk per trade
MIN_QTY    = 0.001       # Shark Exchange minimum BTC order
LEVERAGE   = 20          # Set 20x in Shark Exchange UI too

SL_PTS     = 50          # $50 stop loss points
RR         = 3           # Risk:Reward 1:3
TP_PTS     = SL_PTS * RR # $150 take profit points

MAX_DAILY_LOSS = 2       # Max losing trades per day
CANDLE_LIMIT   = 210     # Candles to fetch
INTERVAL       = "5m"    # Timeframe

BASE_URL   = "https://api.sharkexchange.com"  # update if different

# ─── GLOBALS ───────────────────────────────────────────────
daily_losses   = 0
last_reset_day = datetime.now(timezone.utc).date()

# ─── HELPERS ───────────────────────────────────────────────
def get_timestamp():
    return str(int(time.time() * 1000))

def sign(params: dict):
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def get_headers():
    return {
        "X-API-KEY": API_KEY,
        "Content-Type": "application/json"
    }

# ─── MARKET DATA ───────────────────────────────────────────
def get_candles():
    try:
        url = f"{BASE_URL}/api/v1/klines"
        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "limit": CANDLE_LIMIT
        }
        r = requests.get(url, params=params, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Candle fetch error: {e}")
        return []

def get_btc_price(candles):
    if candles:
        return float(candles[-1]['close'])
    return 0.0

# ─── FVG DETECTION ─────────────────────────────────────────
def detect_fvg(candles):
    """
    Bullish FVG : candle[i-2].high < candle[i].low   → gap up
    Bearish FVG : candle[i-2].low  > candle[i].high  → gap down
    Returns: ('LONG'/'SHORT', fvg_mid_price) or (None, None)
    """
    if len(candles) < 3:
        return None, None

    for i in range(len(candles) - 1, 1, -1):
        try:
            c0 = candles[i - 2]
            c2 = candles[i]

            h0 = float(c0['high'])
            l0 = float(c0['low'])
            h2 = float(c2['high'])
            l2 = float(c2['low'])

            # Bullish FVG
            if h0 < l2:
                fvg_mid = (h0 + l2) / 2
                return "LONG", round(fvg_mid, 2)

            # Bearish FVG
            if l0 > h2:
                fvg_mid = (l0 + h2) / 2
                return "SHORT", round(fvg_mid, 2)

        except Exception:
            continue

    return None, None

# ─── POSITION SIZING ───────────────────────────────────────
def calc_qty():
    raw_qty = BALANCE * RISK_PCT / SL_PTS
    return max(MIN_QTY, round(raw_qty, 6))

# ─── ORDER PLACEMENT ───────────────────────────────────────
def place_order(side, entry, sl, tp, qty):
    try:
        ts = get_timestamp()
        payload = {
            "symbol":    SYMBOL,
            "side":      side,           # "BUY" or "SELL"
            "type":      "LIMIT",
            "price":     str(entry),
            "quantity":  str(qty),
            "stopLoss":  str(sl),
            "takeProfit": str(tp),
            "timestamp": ts
        }
        payload["signature"] = sign(payload)

        r = requests.post(
            f"{BASE_URL}/api/v1/order",
            headers=get_headers(),
            json=payload,
            timeout=10
        )
        return r.json()

    except Exception as e:
        print(f"Order error: {e}")
        return None

# ─── DAILY LOSS RESET ──────────────────────────────────────
def check_reset():
    global daily_losses, last_reset_day
    today = datetime.now(timezone.utc).date()
    if today != last_reset_day:
        daily_losses   = 0
        last_reset_day = today

# ─── MAIN LOOP ─────────────────────────────────────────────
def main():
    global daily_losses

    print(f"BTC Rev FVG 5m Algo started")
    print(f"SL={SL_PTS} TP={TP_PTS} Risk={RISK_PCT*100}% MaxLoss={MAX_DAILY_LOSS}/day")

    candle_count = 0

    while True:
        try:
            check_reset()

            if daily_losses >= MAX_DAILY_LOSS:
                print(f"Max daily loss hit. Sleeping till next day...")
                time.sleep(60)
                continue

            candles = get_candles()
            candle_count += 1
            price   = get_btc_price(candles)
            now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S")

            if candles:
                print(f"Candle {candle_count}: [{candles[-1]}]")

            signal, fvg_price = detect_fvg(candles)

            if signal is None:
                print(f"No signal | {now_utc} UTC | BTC:{price}")

            else:
                qty = calc_qty()

                if signal == "LONG":
                    entry = fvg_price
                    sl    = round(entry - SL_PTS, 2)
                    tp    = round(entry + TP_PTS, 2)
                    side  = "BUY"
                else:
                    entry = fvg_price
                    sl    = round(entry + SL_PTS, 2)
                    tp    = round(entry - TP_PTS, 2)
                    side  = "SELL"

                print(f"{signal} | {now_utc} UTC | BTC:{price} | Entry:{entry} SL:{sl} TP:{tp} Qty:{qty}")

                result = place_order(side, entry, sl, tp, qty)
                print(f"Order result: {result}")

                if result and result.get("status") == "FILLED":
                    print(f"✅ Order filled!")
                elif result and "error" in str(result).lower():
                    print(f"❌ Order failed: {result}")
                    daily_losses += 1

            time.sleep(60)

        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
