"""
Engine module for Spot Martingale (SPEC v5.8).
Contains database schema, models, technical indicators, accounting invariants,
suspicious price state machine, and risk management logic.
"""

import math
import sqlite3
import hashlib
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple, Any

import config

logger = logging.getLogger("spot_martingale.engine")

# ---------------------------------------------------------
# Database Schema & Connection
# ---------------------------------------------------------

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash_usdt REAL NOT NULL,
    initial_usdt REAL NOT NULL DEFAULT 10000.0,
    peak_equity_usdt REAL NOT NULL,
    halted_until_utc TEXT,
    halt_trigger_equity_usdt REAL,
    halt_triggered_utc TEXT,
    halt_active INTEGER NOT NULL DEFAULT 0,
    halt_state TEXT NOT NULL DEFAULT 'NONE',
    halt_progress_sold_layers INTEGER NOT NULL DEFAULT 0,
    halt_progress_total_layers INTEGER NOT NULL DEFAULT 0,
    halt_zero_progress_rounds INTEGER NOT NULL DEFAULT 0,
    day_start_equity_usdt REAL NOT NULL,
    day_start_date_utc TEXT,
    last_equity_usdt REAL,
    last_equity_utc TEXT,
    last_poll_utc TEXT,
    last_poll_monotonic_s REAL,
    realized_pnl_usdt REAL NOT NULL DEFAULT 0.0,
    updated_utc TEXT NOT NULL,
    v58_transition_done INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS symbol_state (
    symbol TEXT PRIMARY KEY,
    last_valid_price REAL,
    last_valid_price_utc TEXT,
    last_suspect_price REAL,
    price_error_streak INTEGER NOT NULL DEFAULT 0,
    suspect_price_streak INTEGER NOT NULL DEFAULT 0,
    recovery_streak INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS grid_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    center_price REAL NOT NULL,
    spacing_pct REAL NOT NULL,
    atr_pct REAL NOT NULL,
    status TEXT NOT NULL, -- OPEN / CLOSED
    opened_utc TEXT NOT NULL,
    first_buy_utc TEXT,
    closed_utc TEXT,
    close_reason TEXT, -- TAKE_PROFIT / ALL_SOLD / TIME_STOP_7D / TIME_STOP_14D / DRAWDOWN_HALT
    stop_adding INTEGER NOT NULL DEFAULT 0,
    half_time_stopped INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_grid_groups_single_open
ON grid_groups(symbol) WHERE status = 'OPEN';

CREATE TABLE IF NOT EXISTS layers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    layer_no INTEGER NOT NULL,
    trigger_price REAL NOT NULL,
    buy_price REAL,
    qty REAL,
    notional_usdt REAL,
    planned_notional_usdt REAL,
    buy_fee_usdt REAL,
    buy_slippage_usdt REAL,
    status TEXT NOT NULL, -- PENDING / OPEN / SOLD / TIME_STOPPED / CANCELLED / HALT_CLOSED
    bought_utc TEXT,
    sold_utc TEXT,
    sell_price REAL,
    UNIQUE(group_id, layer_no),
    FOREIGN KEY(group_id) REFERENCES grid_groups(id)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL, -- BUY / SELL
    price REAL NOT NULL,
    qty REAL NOT NULL,
    notional_usdt REAL NOT NULL,
    fee_usdt REAL NOT NULL,
    slippage_usdt REAL NOT NULL,
    group_id INTEGER NOT NULL,
    layer_no INTEGER NOT NULL,
    reason TEXT NOT NULL -- GROUP_OPEN / LAYER_BUY / LAYER_TP / GROUP_TP / ALL_SOLD / TIME_STOP_7D / TIME_STOP_14D / DRAWDOWN_HALT / TRANSITION_CLOSE
);

CREATE TABLE IF NOT EXISTS daily_summary (
    date_utc TEXT PRIMARY KEY,
    realized_pnl REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    equity REAL NOT NULL,
    drawdown_pct REAL NOT NULL,
    trades_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    utc TEXT NOT NULL,
    type TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS regime_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    adx REAL,
    atr_pct REAL,
    in_range INTEGER,
    event_in_24h INTEGER,
    event_in_past_2h INTEGER,
    allow_new_group INTEGER,
    regime TEXT,
    ret24_pct REAL,
    plus_di REAL,
    minus_di REAL
);
"""

def get_db_connection(db_path=config.DB_PATH, timeout=config.SQLITE_LOCK_TIMEOUT_SEC) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path=config.DB_PATH):
    """Initialize tables and default single-row state if not already initialized, with idempotent migrations."""
    conn = get_db_connection(db_path)
    with conn:
        conn.executescript(SCHEMA_SQL)
        cur = conn.cursor()

        # Idempotent migrations for existing tables (§6.1)
        # 1. layers.planned_notional_usdt
        cur.execute("PRAGMA table_info(layers)")
        layer_cols = [r["name"] for r in cur.fetchall()]
        if "planned_notional_usdt" not in layer_cols:
            cur.execute("ALTER TABLE layers ADD COLUMN planned_notional_usdt REAL")

        # 2. regime_log columns: regime, ret24_pct, plus_di, minus_di
        cur.execute("PRAGMA table_info(regime_log)")
        regime_cols = [r["name"] for r in cur.fetchall()]
        for col, col_type in [("regime", "TEXT"), ("ret24_pct", "REAL"), ("plus_di", "REAL"), ("minus_di", "REAL")]:
            if col not in regime_cols:
                cur.execute(f"ALTER TABLE regime_log ADD COLUMN {col} {col_type}")

        # 3. account.v58_transition_done (§6.1, v5.8)
        cur.execute("PRAGMA table_info(account)")
        account_cols = [r["name"] for r in cur.fetchall()]
        if "v58_transition_done" not in account_cols:
            cur.execute("ALTER TABLE account ADD COLUMN v58_transition_done INTEGER NOT NULL DEFAULT 0")

        now_str = datetime.now(timezone.utc).isoformat()
        cur.execute("SELECT id FROM account WHERE id = 1")
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO account (
                    id, cash_usdt, initial_usdt, peak_equity_usdt,
                    day_start_equity_usdt, halt_active, halt_state,
                    halt_progress_sold_layers, halt_progress_total_layers,
                    halt_zero_progress_rounds, realized_pnl_usdt, updated_utc,
                    v58_transition_done
                ) VALUES (1, 10000.0, 10000.0, 10000.0, 10000.0, 0, 'NONE', 0, 0, 0, 0.0, ?, 0)
            """, (now_str,))
        
        for sym in ["BTCUSDT", "ETHUSDT"]:
            cur.execute("SELECT symbol FROM symbol_state WHERE symbol = ?", (sym,))
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO symbol_state (
                        symbol, last_valid_price, last_valid_price_utc,
                        last_suspect_price, price_error_streak,
                        suspect_price_streak, recovery_streak
                    ) VALUES (?, NULL, NULL, NULL, 0, 0, 0)
                """, (sym,))
    conn.close()

# ---------------------------------------------------------
# Indicators: TR, ATR, Wilder's ADX, HH/LL, Median ATR
# ---------------------------------------------------------

def compute_tr(high: float, low: float, prev_close: Optional[float]) -> float:
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))

def calculate_indicators(klines_1h: List[List[Any]]) -> Dict[str, Any]:
    """
    Calculate ATR(14) SMA, ADX(14) Wilder's, HH_480, LL_480, ATR_median_480.
    klines_1h: list of closed bars, each bar is [open_time, open, high, low, close, volume, ...]
    Must have at least 500 closed bars.
    Returns dict:
      {
         'adx': float,
         'atr': float,
         'atr_pct': float,
         'hh_480': float,
         'll_480': float,
         'atr_median_480': float,
         'prev_close': float,
         'in_range': bool
      }
    """
    n = len(klines_1h)
    if n < config.MIN_KLINES_BARS:
        return {
            'adx': None,
            'plus_di': None,
            'minus_di': None,
            'ret24': None,
            'atr': None,
            'atr_pct': None,
            'hh_480': None,
            'll_480': None,
            'atr_median_480': None,
            'prev_close': float(klines_1h[-1][4]) if n > 0 else None,
            'in_range': False,
            'position': None,
            'pos_ok': False,
            'error': f'Insufficient bars: {n} < {config.MIN_KLINES_BARS}'
        }

    highs = [float(b[2]) for b in klines_1h]
    lows = [float(b[3]) for b in klines_1h]
    closes = [float(b[4]) for b in klines_1h]

    # 1. TR
    trs = []
    for i in range(n):
        prev_c = closes[i - 1] if i > 0 else None
        trs.append(compute_tr(highs[i], lows[i], prev_c))

    # 2. ATR(14) SMA
    # First ATR value is at index 13 (bar 14)
    atrs = [None] * n
    window = 14
    for i in range(window - 1, n):
        atrs[i] = sum(trs[i - window + 1:i + 1]) / window

    # 3. ADX(14) Wilder's
    # +DM, -DM
    plus_dms = [0.0] * n
    minus_dms = [0.0] * n
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        if up_move > down_move and up_move > 0:
            plus_dms[i] = up_move
        if down_move > up_move and down_move > 0:
            minus_dms[i] = down_move

    # Wilder smoothing (alpha = 1/14)
    # First smoothed value = sum of first 14 values (indices 1 to 14, i.e. bar 1 to 14)
    smooth_tr = [None] * n
    smooth_plus_dm = [None] * n
    smooth_minus_dm = [None] * n

    # Initial sum of 14 for Wilder (indices 1..14)
    smooth_tr[14] = sum(trs[1:15])
    smooth_plus_dm[14] = sum(plus_dms[1:15])
    smooth_minus_dm[14] = sum(minus_dms[1:15])

    for i in range(15, n):
        smooth_tr[i] = smooth_tr[i - 1] - (smooth_tr[i - 1] / 14.0) + trs[i]
        smooth_plus_dm[i] = smooth_plus_dm[i - 1] - (smooth_plus_dm[i - 1] / 14.0) + plus_dms[i]
        smooth_minus_dm[i] = smooth_minus_dm[i - 1] - (smooth_minus_dm[i - 1] / 14.0) + minus_dms[i]

    # DX and Wilder's +DI/-DI Exposure (§3.4, v5.7)
    plus_di_arr = [None] * n
    minus_di_arr = [None] * n
    dx = [None] * n
    for i in range(14, n):
        tr_val = smooth_tr[i]
        if tr_val == 0:
            plus_di_arr[i] = 0.0
            minus_di_arr[i] = 0.0
            dx[i] = 0.0
            continue
        plus_di = 100.0 * (smooth_plus_dm[i] / tr_val)
        minus_di = 100.0 * (smooth_minus_dm[i] / tr_val)
        plus_di_arr[i] = plus_di
        minus_di_arr[i] = minus_di
        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx[i] = 0.0
        else:
            dx[i] = 100.0 * abs(plus_di - minus_di) / di_sum

    # ADX: Wilder smoothed DX
    # First ADX at bar 28 (index 27 in 0-based indexing) = mean of first 14 DX (indices 14..27)
    adx_arr = [None] * n
    if n >= 28:
        adx_arr[27] = sum(dx[14:28]) / 14.0
        for i in range(28, n):
            adx_arr[i] = ((adx_arr[i - 1] * 13.0) + dx[i]) / 14.0

    # 4. Recent 480 closed bars (indices n-480 to n-1)
    recent_480_highs = highs[n - config.RANGE_WINDOW_BARS:n]
    recent_480_lows = lows[n - config.RANGE_WINDOW_BARS:n]
    recent_480_atrs = atrs[n - config.RANGE_WINDOW_BARS:n]

    hh_480 = max(recent_480_highs)
    ll_480 = min(recent_480_lows)

    # Median of 480 ATR values
    sorted_atrs = sorted(recent_480_atrs)
    mid = len(sorted_atrs) // 2
    atr_median_480 = (sorted_atrs[mid - 1] + sorted_atrs[mid]) / 2.0

    current_close = closes[-1]
    current_atr = atrs[-1]
    current_adx = adx_arr[-1]

    # RET24 (§3.4): close[-1] / close[-25] - 1 (requires at least 25 closed bars)
    ret24 = (closes[-1] / closes[-25] - 1.0) if (n >= 25 and closes[-25] != 0) else None

    in_range = (ll_480 < current_close < hh_480)

    # Position & pos_ok (§3.4, §4.1 Condition 7, SPEC v5.8)
    if hh_480 <= ll_480:
        position = None
        pos_ok = False
    else:
        position = (current_close - ll_480) / (hh_480 - ll_480)
        pos_ok = (position <= config.POSITION_THRESHOLD)

    return {
        'adx': current_adx,
        'plus_di': plus_di_arr[-1] if n >= 28 else None,
        'minus_di': minus_di_arr[-1] if n >= 28 else None,
        'ret24': ret24,
        'atr': current_atr,
        'atr_pct': current_atr / current_close if current_close else 0.0,
        'hh_480': hh_480,
        'll_480': ll_480,
        'atr_median_480': atr_median_480,
        'prev_close': current_close,
        'in_range': in_range,
        'position': position,
        'pos_ok': pos_ok
    }

# ---------------------------------------------------------
# Economic Events Calendar Loader & Stale Check (§1.1, §7)
# ---------------------------------------------------------

def load_events_calendar(path=config.EVENTS_CSV_PATH) -> Tuple[str, str, List[Tuple[datetime, str, str, str]]]:
    """
    Load data/events.csv, verify normalized SHA256 against version header.
    Returns (version, sha256, list_of_events)
    where each event is (utc_dt, event_name, country, impact).
    Raises ValueError / RuntimeError on any verification failure.
    """
    with open(path, 'rb') as f:
        raw = f.read()

    # Strip UTF-8 BOM if present
    if raw.startswith(b'\xef\xbb\xbf'):
        raw = raw[3:]

    newline_pos = raw.find(b'\n')
    if newline_pos == -1:
        raise ValueError("events.csv missing newline in header")

    first_line = raw[:newline_pos].decode('utf-8').strip()
    match = re.match(r'^#\s*version:\s*(\w+),\s*sha256:\s*([a-fA-F0-9]{64})$', first_line)
    if not match:
        raise ValueError(f"Invalid version line in events.csv: {first_line}")

    version, expected_sha = match.group(1), match.group(2).lower()
    body_bytes = raw[newline_pos + 1:]
    # Normalize CRLF -> LF
    norm_body = body_bytes.replace(b'\r\n', b'\n')
    actual_sha = hashlib.sha256(norm_body).hexdigest()

    if actual_sha != expected_sha:
        raise ValueError(f"events.csv SHA256 mismatch: actual {actual_sha} != expected {expected_sha}")

    lines = norm_body.decode('utf-8').splitlines()
    if not lines or lines[0].strip() != "utc_time,event,country,impact":
        raise ValueError(f"Invalid column header in events.csv: {lines[0] if lines else ''}")

    events = []
    prev_dt = None
    seen = set()

    for line_idx, line in enumerate(lines[1:], start=2):
        line = line.strip()
        if not line:
            continue
        parts = line.split(',')
        if len(parts) != 4:
            raise ValueError(f"Line {line_idx} does not have 4 columns: {line}")
        utc_str, event_name, country, impact = parts
        if not (utc_str and event_name and country and impact):
            raise ValueError(f"Line {line_idx} has empty field: {line}")
        if impact not in ('HIGH', 'MEDIUM', 'LOW'):
            raise ValueError(f"Line {line_idx} invalid impact {impact}")

        # Parse ISO8601 UTC timestamp
        clean_utc = utc_str.replace('Z', '+00:00')
        dt = datetime.fromisoformat(clean_utc).astimezone(timezone.utc)

        if prev_dt and dt < prev_dt:
            raise ValueError(f"Line {line_idx} not sorted ascending: {dt} < {prev_dt}")
        prev_dt = dt

        key = (dt.isoformat(), event_name)
        if key in seen:
            raise ValueError(f"Line {line_idx} duplicate event: {key}")
        seen.add(key)

        events.append((dt, event_name, country, impact))

    return version, actual_sha, events

def check_event_filter(
    events: List[Tuple[datetime, str, str, str]],
    current_dt_utc: datetime,
    is_simulation: bool = False
) -> Tuple[bool, bool, bool, Optional[str]]:
    """
    Check economic events around current_dt_utc (§4.1.4, §7):
    Window: (now - 2h, now + 24h] - past 2h exclusive, future 24h inclusive.
    Stale check: last event in events must be >= current_dt_utc + 90 days.
    Returns:
      (event_in_24h, event_in_past_2h, is_stale, error_msg)
    """
    if not events:
        return True, True, True, "No events loaded"

    # Stale check: last event must be >= current_dt + 90 days
    last_event_dt = events[-1][0]
    stale_threshold = current_dt_utc + timedelta(days=config.EVENT_STALE_DAYS)
    is_stale = (last_event_dt < stale_threshold)
    if is_stale:
        msg = f"events.csv stale: last event {last_event_dt.isoformat()} < threshold {stale_threshold.isoformat()}"
        return True, True, True, msg

    past_2h = current_dt_utc - timedelta(hours=config.EVENT_WINDOW_POST_HOURS)
    future_24h = current_dt_utc + timedelta(hours=config.EVENT_WINDOW_PRE_HOURS)

    event_in_24h = False
    event_in_past_2h = False

    for dt, ev, c, impact in events:
        if impact != 'HIGH':
            continue
        # past 2h: (now - 2h, now] exclusive of now-2h, inclusive of now
        if past_2h < dt <= current_dt_utc:
            event_in_past_2h = True
        # future 24h: (now, now + 24h] inclusive of future_24h
        if current_dt_utc < dt <= future_24h:
            event_in_24h = True

    return event_in_24h, event_in_past_2h, False, None

# ---------------------------------------------------------
# Sizing, Dynamic Multiplier & Accounting Invariants (§1.3, §1.4, §2, §4.3)
# ---------------------------------------------------------

def classify_regime(
    adx: Optional[float],
    plus_di: Optional[float],
    minus_di: Optional[float],
    ret24: Optional[float],
    prev_regime: Optional[str] = None
) -> Optional[str]:
    """
    Classify market trend regime (§4.3) using 4-state priority waterfall + REVERSAL logging:
    1. STRONG_DOWN: ADX >= 25 and -DI > +DI and RET24 <= -5% (-0.05)
    2. SLOW_DOWN: ADX >= 25 and -DI > +DI (RET24 > -5% guaranteed by not 1)
    3. REBOUND: ADX < 25 and RET24 >= +3% (+0.03)
    4. RANGING: all other cases (default)
    REVERSAL: transition label if prev_regime in (STRONG_DOWN, SLOW_DOWN)
              and waterfall is RANGING and +DI > -DI.
    Returns None if any required indicator is None.
    """
    if adx is None or plus_di is None or minus_di is None or ret24 is None:
        return None

    if adx >= config.REGIME_ADX_THRESHOLD and minus_di > plus_di and ret24 <= config.STRONG_DOWN_RET24:
        return "STRONG_DOWN"
    if adx >= config.REGIME_ADX_THRESHOLD and minus_di > plus_di:
        return "SLOW_DOWN"
    if adx < config.REGIME_ADX_THRESHOLD and ret24 >= config.REBOUND_RET24:
        return "REBOUND"

    # Waterfall default is RANGING; check for REVERSAL transition tag (§4.3)
    if prev_regime in ("STRONG_DOWN", "SLOW_DOWN") and plus_di > minus_di:
        return "REVERSAL"
    return "RANGING"

def get_dynamic_multiplier(dd: float, regime: Optional[str] = None) -> Tuple[Optional[float], bool]:
    """
    Determine dynamic multiplier and check whether adding is permitted (§2, §3.2, §7.1):
    - DD < 2%: 1.0 (v5.8)
    - 2% <= DD < 5%: 1.0 (v5.8)
    - 5% <= DD < 10%: 1.0 (v5.8)
    - 10% <= DD <= 20%: 1.0 (permitted only when regime != STRONG_DOWN)
    - DD > 20%: None, stop adding
    Returns (multiplier, is_permitted).
    """
    if dd < 0.02:
        return config.MULT_DD_LT_2, True
    elif dd < 0.05:
        return config.MULT_DD_2_5, True
    elif dd < 0.10:
        return config.MULT_DD_5_10, True
    elif dd <= 0.20:
        if regime == "STRONG_DOWN":
            return config.MULT_DD_10_20, False
        return config.MULT_DD_10_20, True
    else:
        return None, False

def compute_order_qty(symbol: str, planned_notional: float, price: float) -> Tuple[float, float]:
    """
    Compute order quantity rounded down to step size, and actual notional.
    BTC: step size 0.001 BTC
    ETH: step size 0.01 ETH
    Returns: (qty, actual_notional)
    """
    raw_qty = planned_notional / price
    if symbol == "BTCUSDT":
        step = config.MIN_QTY_BTC
        qty = math.floor(raw_qty / step) * step
        qty = round(qty, 6)
    elif symbol == "ETHUSDT":
        step = config.MIN_QTY_ETH
        qty = math.floor(raw_qty / step) * step
        qty = round(qty, 4)
    else:
        raise ValueError(f"Unknown symbol {symbol}")

    actual_notional = round(qty * price, 4)
    return qty, actual_notional

def assert_cash_invariant(conn: sqlite3.Connection):
    """
    Assert accounting invariant:
    cash_usdt = initial_usdt + realized_pnl_usdt - sum(OPEN basis) - sum(OPEN buy_fee + buy_slippage)
    """
    cur = conn.cursor()
    cur.execute("SELECT cash_usdt, initial_usdt, realized_pnl_usdt FROM account WHERE id = 1")
    acc = cur.fetchone()
    if not acc:
        return
    cash = acc["cash_usdt"]
    initial = acc["initial_usdt"]
    realized = acc["realized_pnl_usdt"]

    cur.execute("""
        SELECT SUM(qty * buy_price) as open_basis,
               SUM(buy_fee_usdt + buy_slippage_usdt) as open_costs
        FROM layers
        WHERE status = 'OPEN'
    """)
    row = cur.fetchone()
    open_basis = row["open_basis"] or 0.0
    open_costs = row["open_costs"] or 0.0

    expected_cash = initial + realized - open_basis - open_costs
    # Allow 0.005 USDT floating point tolerance
    diff = abs(cash - expected_cash)
    if diff > 0.01:
        raise AssertionError(
            f"Cash invariant violated! cash={cash:.4f}, expected={expected_cash:.4f}, "
            f"diff={diff:.4f} (initial={initial}, realized={realized}, open_basis={open_basis}, open_costs={open_costs})"
        )

# ---------------------------------------------------------
# Spacing Calculation (§3.2)
# ---------------------------------------------------------

def compute_grid_spacing(symbol: str, atr: float, center_price: float) -> float:
    """
    Compute spacing d for grid group:
    BTC: 1.5 * ATR / C, clamped [0.8%, 3.0%]
    ETH: 2.0 * ATR / C, clamped [1.0%, 4.0%]
    """
    if symbol == "BTCUSDT":
        raw_d = config.BTC_SPACING_COEFF * atr / center_price
        return min(max(raw_d, config.BTC_SPACING_MIN), config.BTC_SPACING_MAX)
    elif symbol == "ETHUSDT":
        raw_d = config.ETH_SPACING_COEFF * atr / center_price
        return min(max(raw_d, config.ETH_SPACING_MIN), config.ETH_SPACING_MAX)
    else:
        raise ValueError(f"Unknown symbol {symbol}")

def compute_trigger_price(center_price: float, spacing_d: float, layer_no: int) -> float:
    """Trigger price for layer n (n=2..9) = C * (1 - (n - 1) * d). Layer 1 = C."""
    if layer_no == 1:
        return center_price
    return center_price * (1.0 - (layer_no - 1) * spacing_d)

def compute_tp_price(center_price: float, spacing_d: float, layer_no: int) -> float:
    """
    TP price for layer n:
    Layer 1: C * (1 + d)
    Layer n (n >= 2): C * (1 - (n - 2) * d) (previous layer trigger price)
    """
    if layer_no == 1:
        return center_price * (1.0 + spacing_d)
    return center_price * (1.0 - (layer_no - 2) * spacing_d)
