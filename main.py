import os
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
