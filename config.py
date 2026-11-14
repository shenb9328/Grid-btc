"""
Configuration for Spot Martingale (Binance Spot · DRY_RUN simulation)
Frozen under SPEC v5.8 (2026-11-12).
Strict equality check with frozen value table on startup; mismatch causes sys.exit(1).
"""

import sys
import logging
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
RESULTS_DIR = BASE_DIR / "results"
DB_PATH = DATA_DIR / "spot_martingale.sqlite3"
EVENTS_CSV_PATH = DATA_DIR / "events.csv"
DASHBOARD_LAST_READ_PATH = DATA_DIR / "spot_dashboard_last_read.txt"

# 1. Execution Mode (Constant, strictly "DRY_RUN", no LIVE execution)
EXECUTION_MODE = "DRY_RUN"

# 2. Capital and Caps (§2, §7.1)
INITIAL_CAPITAL_USDT = 10000.0
BTC_CAP_USDT = 3000.0
ETH_CAP_USDT = 2000.0
TOTAL_DEPLOYED_CAP_USDT = 5000.0

# Layer Parameters (§2)
MAX_LAYERS = 9
MULTIPLIER = 1.3
S = 32.01499791

# Dynamic Multiplier Ladders (§2, §3.2, §7.1)
MULT_DD_LT_2 = 1.0
MULT_DD_2_5 = 1.0
MULT_DD_5_10 = 1.0
MULT_DD_10_20 = 1.0

# Authoritative layer notionals (§2, half-up rounding, 9th layer residual)
BTC_LAYER_NOTIONALS = [93.71, 121.82, 158.36, 205.87, 267.63, 347.92, 452.30, 587.99, 764.40]
ETH_LAYER_NOTIONALS = [62.47, 81.21, 105.58, 137.25, 178.42, 231.95, 301.53, 391.99, 509.60]

# 3. Grid Spacing & Take Profit (§3.2, §3.3)
BTC_SPACING_COEFF = 2.0
BTC_SPACING_MIN = 0.008
BTC_SPACING_MAX = 0.030

ETH_SPACING_COEFF = 2.5
ETH_SPACING_MIN = 0.010
ETH_SPACING_MAX = 0.040

GROUP_TP_PCT = 0.015  # +1.5%

# 4. Market Regime & Indicators (§3.4, §4.1, §4.3)
ADX_THRESHOLD = 30.0  # < 30
POSITION_THRESHOLD = 0.35  # <= 0.35
ATR_MEDIAN_MULTIPLIER = 2.0  # < 2.0
RANGE_WINDOW_BARS = 480
MIN_KLINES_BARS = 500
ATR_FIRST_BAR_INDEX = 14
ADX_FIRST_BAR_INDEX = 28

REGIME_ADX_THRESHOLD = 25.0
STRONG_DOWN_RET24 = -0.05
REBOUND_RET24 = 0.03
REBOUND_TP_COEFF = 1.008
SLOW_DOWN_COOLDOWN_SECS = 86400

EVENT_WINDOW_PRE_HOURS = 24.0  # future 24 hours (inclusive)
EVENT_WINDOW_POST_HOURS = 2.0  # past 2 hours (exclusive)
EVENT_STALE_DAYS = 90

# 5. Risk, Price Feed & Halt (§4.2, §5.1, §5.2, §5.3)
STOP_ADDING_DROP_PCT = 0.80  # price < 0.80 * C
SERVER_TIME_MAX_DRIFT_SEC = 60.0  # <= 60s
SUSPECT_PRICE_JUMP_PCT = 0.10  # > 10%
SUSPECT_PRICE_KLINE_CLOSE_PCT = 0.05  # <= 5%
SUSPECT_PRICE_ACCEPT_PCT = 0.15  # <= 15%
ERROR_STREAK_THRESHOLD = 3
SUSPECT_STREAK_THRESHOLD = 3
RECOVERY_STREAK_REQUIRED = 3

DRAWDOWN_HALT_PCT = 0.15  # >= 15%
HALT_COOLDOWN_HOURS = 24  # 24h
DAILY_LOSS_LIMIT_USDT = -300.0  # < -300 USDT
TIME_STOP_7D_DAYS = 7
TIME_STOP_14D_DAYS = 14
HOLDMAX_EXEMPT_TIME_STOP = True

# 6. Costs & Minimum Orders (§1.3)
FEE_RATE = 0.0010  # 0.10%
SLIPPAGE_RATE = 0.0002  # 0.02%
MIN_QTY_BTC = 0.001
MIN_QTY_ETH = 0.01
MIN_NOTIONAL_USDT = 5.0  # >= 5.00

# 7. Polling & Timeouts (§1.1, §4.2, §7)
POLL_INTERVAL_SEC = 60.0
REQUEST_TIMEOUT_SEC = 10.0  # > 10s
SQLITE_LOCK_TIMEOUT_SEC = 5.0  # > 5s
HALTING_ZERO_PROGRESS_ROUNDS_STOP = 3
TIME_JUMP_THRESHOLD_SEC = 60.0

# 8. Stress Test & Acceptance (§8.2, §8.3)
STRESS_TEST_STEPS = 720
STRESS_TEST_SPLIT_STEPS = 360
STRESS_TEST_RECOVERY_HOURS = 24
MAX_ACCEPTABLE_DRAWDOWN_PCT = 0.1600  # <= 16.00%
MIN_PROFIT_FACTOR = 1.20
MIN_WIN_MONTH_RATIO = 0.60

# Public API endpoints (Public only, NO API KEY)
BINANCE_PUBLIC_BASE = "https://data-api.binance.vision"
BINANCE_PUBLIC_FALLBACK = "https://api.binance.com"

# Frozen Table Dictionary for Strict Equality Verification (§7, §7.1)
FROZEN_VALUES = {
    "EXECUTION_MODE": "DRY_RUN",
    "INITIAL_CAPITAL_USDT": 10000.0,
    "BTC_CAP_USDT": 3000.0,
    "ETH_CAP_USDT": 2000.0,
    "TOTAL_DEPLOYED_CAP_USDT": 5000.0,
    "MAX_LAYERS": 9,
    "MULTIPLIER": 1.3,
    "S": 32.01499791,
    "MULT_DD_LT_2": 1.0,
    "MULT_DD_2_5": 1.0,
    "MULT_DD_5_10": 1.0,
    "MULT_DD_10_20": 1.0,
    "BTC_LAYER_NOTIONALS": [93.71, 121.82, 158.36, 205.87, 267.63, 347.92, 452.30, 587.99, 764.40],
    "ETH_LAYER_NOTIONALS": [62.47, 81.21, 105.58, 137.25, 178.42, 231.95, 301.53, 391.99, 509.60],
    "BTC_SPACING_COEFF": 2.0,
    "BTC_SPACING_MIN": 0.008,
    "BTC_SPACING_MAX": 0.030,
    "ETH_SPACING_COEFF": 2.5,
    "ETH_SPACING_MIN": 0.010,
    "ETH_SPACING_MAX": 0.040,
    "GROUP_TP_PCT": 0.015,
    "ADX_THRESHOLD": 30.0,
    "POSITION_THRESHOLD": 0.35,
    "ATR_MEDIAN_MULTIPLIER": 2.0,
    "RANGE_WINDOW_BARS": 480,
    "MIN_KLINES_BARS": 500,
    "ATR_FIRST_BAR_INDEX": 14,
    "ADX_FIRST_BAR_INDEX": 28,
    "REGIME_ADX_THRESHOLD": 25.0,
    "STRONG_DOWN_RET24": -0.05,
    "REBOUND_RET24": 0.03,
    "REBOUND_TP_COEFF": 1.008,
    "SLOW_DOWN_COOLDOWN_SECS": 86400,
    "EVENT_WINDOW_PRE_HOURS": 24.0,
    "EVENT_WINDOW_POST_HOURS": 2.0,
    "EVENT_STALE_DAYS": 90,
    "STOP_ADDING_DROP_PCT": 0.80,
    "SERVER_TIME_MAX_DRIFT_SEC": 60.0,
    "SUSPECT_PRICE_JUMP_PCT": 0.10,
    "SUSPECT_PRICE_KLINE_CLOSE_PCT": 0.05,
    "SUSPECT_PRICE_ACCEPT_PCT": 0.15,
    "ERROR_STREAK_THRESHOLD": 3,
    "SUSPECT_STREAK_THRESHOLD": 3,
    "RECOVERY_STREAK_REQUIRED": 3,
    "DRAWDOWN_HALT_PCT": 0.15,
    "HALT_COOLDOWN_HOURS": 24,
    "DAILY_LOSS_LIMIT_USDT": -300.0,
    "TIME_STOP_7D_DAYS": 7,
    "TIME_STOP_14D_DAYS": 14,
    "HOLDMAX_EXEMPT_TIME_STOP": True,
    "FEE_RATE": 0.0010,
    "SLIPPAGE_RATE": 0.0002,
    "MIN_QTY_BTC": 0.001,
    "MIN_QTY_ETH": 0.01,
    "MIN_NOTIONAL_USDT": 5.0,
    "POLL_INTERVAL_SEC": 60.0,
    "REQUEST_TIMEOUT_SEC": 10.0,
    "SQLITE_LOCK_TIMEOUT_SEC": 5.0,
    "HALTING_ZERO_PROGRESS_ROUNDS_STOP": 3,
    "TIME_JUMP_THRESHOLD_SEC": 60.0,
    "STRESS_TEST_STEPS": 720,
    "STRESS_TEST_SPLIT_STEPS": 360,
    "STRESS_TEST_RECOVERY_HOURS": 24,
    "MAX_ACCEPTABLE_DRAWDOWN_PCT": 0.1600,
    "MIN_PROFIT_FACTOR": 1.20,
    "MIN_WIN_MONTH_RATIO": 0.60,
    "BINANCE_PUBLIC_BASE": "https://data-api.binance.vision",
    "BINANCE_PUBLIC_FALLBACK": "https://api.binance.com",
}

def _compute_half_up_layers(cap: float) -> list:
    cap_d = Decimal(str(cap))
    mult_d = Decimal(str(MULTIPLIER))
    s_d = Decimal(str(S))
    layers = []
    for n in range(1, MAX_LAYERS):
        val = (cap_d * (mult_d ** (n - 1)) / s_d).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        layers.append(float(val))
    rem = cap_d - sum(Decimal(str(x)) for x in layers)
    layers.append(float(rem.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)))
    return layers

def verify_frozen_config() -> bool:
    """
    Verify that current config attributes strictly equal the SPEC v5.1 frozen values table.
    Exits with error if any mismatch is found.
    """
    current_module = sys.modules[__name__]
    mismatches = []
    for key, frozen_val in FROZEN_VALUES.items():
        if not hasattr(current_module, key):
            mismatches.append(f"Missing config attribute: {key}")
            continue
        actual_val = getattr(current_module, key)
        if type(actual_val) is not type(frozen_val) or actual_val != frozen_val:
            mismatches.append(
                f"Config mismatch for {key}: actual={actual_val!r} (type {type(actual_val).__name__}) != "
                f"frozen={frozen_val!r} (type {type(frozen_val).__name__})"
            )
        elif isinstance(frozen_val, list):
            if len(actual_val) != MAX_LAYERS:
                mismatches.append(f"Config list {key} length {len(actual_val)} != MAX_LAYERS {MAX_LAYERS}")
            for idx, (a_item, f_item) in enumerate(zip(actual_val, frozen_val)):
                if type(a_item) is not type(f_item) or a_item != f_item:
                    mismatches.append(f"Config list {key}[{idx}] mismatch: {a_item!r} != {f_item!r}")

    # Verify authoritative layer arrays against half-up formula and exact sums (§2)
    if not mismatches:
        expected_btc = _compute_half_up_layers(BTC_CAP_USDT)
        expected_eth = _compute_half_up_layers(ETH_CAP_USDT)
        if BTC_LAYER_NOTIONALS != expected_btc:
            mismatches.append(f"BTC_LAYER_NOTIONALS {BTC_LAYER_NOTIONALS} != half-up formula {expected_btc}")
        if ETH_LAYER_NOTIONALS != expected_eth:
            mismatches.append(f"ETH_LAYER_NOTIONALS {ETH_LAYER_NOTIONALS} != half-up formula {expected_eth}")

        btc_sum = round(sum(BTC_LAYER_NOTIONALS), 2)
        eth_sum = round(sum(ETH_LAYER_NOTIONALS), 2)
        if btc_sum != BTC_CAP_USDT:
            mismatches.append(f"BTC_LAYER_NOTIONALS sum {btc_sum} != BTC_CAP_USDT {BTC_CAP_USDT}")
        if eth_sum != ETH_CAP_USDT:
            mismatches.append(f"ETH_LAYER_NOTIONALS sum {eth_sum} != ETH_CAP_USDT {ETH_CAP_USDT}")

    if mismatches:
        for err in mismatches:
            logging.error("CONFIG VERIFICATION FAILED: %s", err)
        return False
    return True

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not verify_frozen_config():
        sys.exit(1)
    print("All config values match SPEC v5.1 frozen value table strictly.")
