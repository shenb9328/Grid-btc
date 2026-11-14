#!/usr/bin/env python3
"""
Spot Martingale Monitor (Binance Spot · Simulation DRY_RUN)
Frozen under SPEC v5.8.
Polls Binance public market data every 60s, executes simulation grid matching,
and persists state to SQLite database.
"""

import os
import sys
import time
import json
import sqlite3
import argparse
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import config
import engine

# Process naming
try:
    import setproctitle
    setproctitle.setproctitle("spot-martingale-monitor")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOGS_DIR / f"spot_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger("spot-martingale-monitor")

class BinancePublicClient:
    def __init__(self, timeout=config.REQUEST_TIMEOUT_SEC):
        self.timeout = timeout
        self.base_url = config.BINANCE_PUBLIC_BASE
        self.fallback_url = config.BINANCE_PUBLIC_FALLBACK

    def _fetch_json(self, path: str) -> Tuple[Optional[Any], Optional[Dict[str, str]], Optional[str]]:
        for base in [self.base_url, self.fallback_url]:
            url = f"{base}{path}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    elapsed = time.time() - t0
                    if elapsed > self.timeout:
                        return None, None, f"Timeout: request took {elapsed:.2f}s > {self.timeout}s"
                    if resp.status != 200:
                        return None, None, f"HTTP status {resp.status}"
                    headers = dict(resp.headers)
                    raw_data = resp.read().decode('utf-8')
                    data = json.loads(raw_data)
                    return data, headers, None
            except urllib.error.HTTPError as e:
                # If 451 or 403 on primary, try fallback
                continue
            except Exception as e:
                elapsed = time.time() - t0
                if elapsed > self.timeout:
                    return None, None, f"Timeout error: {e}"
                continue
        return None, None, "All endpoints failed or timed out"

    def get_server_time(self) -> Tuple[Optional[datetime], Optional[str]]:
        # 1. Try GET /api/v3/time
        data, headers, err = self._fetch_json("/api/v3/time")
        if data and "serverTime" in data:
            st_ms = data["serverTime"]
            dt = datetime.fromtimestamp(st_ms / 1000.0, tz=timezone.utc)
            return dt, None

        # 2. Try ticker response Date header
        data_t, headers_t, err_t = self._fetch_json("/api/v3/ticker/price?symbol=BTCUSDT")
        if headers_t and "Date" in headers_t:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(headers_t["Date"]).astimezone(timezone.utc)
                return dt, None
            except Exception as e:
                pass

        return None, "Unable to obtain server time from /time or HTTP Date header"

    def get_ticker_price(self, symbol: str) -> Tuple[Optional[float], Optional[str]]:
        data, headers, err = self._fetch_json(f"/api/v3/ticker/price?symbol={symbol}")
        if err:
            return None, err
        if not data or "price" not in data:
            return None, f"Malformed ticker payload: {data}"
        try:
            price = float(data["price"])
            if price <= 0:
                return None, f"Non-positive price: {price}"
            return price, None
        except Exception as e:
            return None, f"Price parse error: {e}"

    def get_recent_closed_klines(self, symbol: str, limit: int = config.MIN_KLINES_BARS) -> Tuple[Optional[List[List[Any]]], Optional[str]]:
        # Fetch limit + 1 bars so we can discard the current unclosed bar
        fetch_limit = min(limit + 5, 1000)
        data, headers, err = self._fetch_json(f"/api/v3/klines?symbol={symbol}&interval=1h&limit={fetch_limit}")
        if err:
            return None, err
        if not data or len(data) < limit:
            return None, f"Insufficient klines received: {len(data) if data else 0} < {limit}"

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        # Discard any bar whose close_time >= now_ms (unclosed bar)
        closed_bars = [b for b in data if b[6] < now_ms]
        if len(closed_bars) < limit:
            # If slightly short, return what closed bars we have
            return closed_bars, None
        # Return the most recent `limit` closed bars
        return closed_bars[-limit:], None


class SpotMartingaleMonitor:
    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path is not None else Path(config.DB_PATH)
        self.client = BinancePublicClient()
        self.events_version = None
        self.events_sha = None
        self.events: List[Tuple[datetime, str, str, str]] = []
        self.prev_valid_regime: Dict[str, Optional[str]] = {"BTCUSDT": None, "ETHUSDT": None}

    def startup_checks(self):
        logger.info("Performing startup checks under SPEC v5.8...")
        if not config.verify_frozen_config():
            logger.error("Startup frozen configuration verification failed. Exiting immediately.")
            sys.exit(1)

        # Database verification and schema migrations (§6.1)
        if not self.db_path.exists():
            logger.info("Database file not found. Initializing new database at %s", self.db_path)
            engine.init_db(self.db_path)
        else:
            try:
                # Run idempotent schema migrations on existing database (§6.1)
                engine.init_db(self.db_path)
                conn = engine.get_db_connection(self.db_path)
                with conn:
                    cur = conn.cursor()
                    cur.execute("SELECT id FROM account WHERE id = 1")
                    if not cur.fetchone():
                        logger.error("Database account table corrupted: row 1 missing.")
                        sys.exit(1)
                    # Restore previous valid regime from latest non-null regime_log entry (§6.1)
                    for sym in ["BTCUSDT", "ETHUSDT"]:
                        cur.execute("""
                            SELECT regime FROM regime_log
                            WHERE symbol = ? AND regime IS NOT NULL
                            ORDER BY rowid DESC LIMIT 1
                        """, (sym,))
                        r = cur.fetchone()
                        if r and r["regime"]:
                            self.prev_valid_regime[sym] = r["regime"]
                conn.close()
            except Exception as e:
                logger.error("Database open or integrity check failed: %s. Prohibiting trading and exiting.", e)
                sys.exit(1)

        # Events verification
        try:
            ver, sha, evs = engine.load_events_calendar(config.EVENTS_CSV_PATH)
            self.events_version = ver
            self.events_sha = sha
            self.events = evs
            logger.info("Events calendar loaded: version=%s, sha256=%s, total_events=%d", ver, sha, len(evs))
        except Exception as e:
            logger.error("events.csv startup check failed: %s. Exiting.", e)
            sys.exit(1)

        logger.info("Startup checks passed successfully.")

    def _check_and_execute_transition_close(self):
        """
        Transition closing (SPEC V5.8-4, §9 Rule 24(c)):
        When v5.8 starts for the first time, check account.v58_transition_done.
        If 0, sell all OPEN layers in all OPEN groups at market price (reason=TRANSITION_CLOSE),
        cancel PENDING layers, mark groups as CLOSED (close_reason=TRANSITION_CLOSE),
        and in the same final transaction set v58_transition_done = 1.
        Cash and realized PnL roll over; account is not reset.
        Idempotent: when v58_transition_done == 1, no-op.
        """
        if not self.db_path.exists():
            return
        conn = engine.get_db_connection(self.db_path)
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT v58_transition_done FROM account WHERE id = 1")
            row = cur.fetchone()
            if not row or row["v58_transition_done"] == 1:
                return

            cur.execute("SELECT * FROM grid_groups WHERE status = 'OPEN'")
            open_groups = [dict(r) for r in cur.fetchall()]
            now_utc = datetime.now(timezone.utc)
            for grp in open_groups:
                gid = grp["id"]
                sym = grp["symbol"]
                logger.info("Legacy OPEN group %d (%s) detected. Executing v5.8 transition close.", gid, sym)
                px, _ = self.client.get_ticker_price(sym)
                if px is None or px <= 0:
                    cur.execute("SELECT last_valid_price FROM symbol_state WHERE symbol = ?", (sym,))
                    s_row = cur.fetchone()
                    px = s_row["last_valid_price"] if s_row else None

                cur.execute("SELECT * FROM layers WHERE group_id = ? AND status = 'OPEN' ORDER BY layer_no ASC", (gid,))
                open_layers = [dict(r) for r in cur.fetchall()]

                for lay in open_layers:
                    sell_px = px if (px is not None and px > 0) else lay["buy_price"]
                    self._sell_layer(conn, lay, sym, sell_px, now_utc, "TRANSITION_CLOSE", layer_status="SOLD")

                cur.execute("UPDATE layers SET status = 'CANCELLED' WHERE group_id = ? AND status = 'PENDING'", (gid,))
                cur.execute(
                    "UPDATE grid_groups SET status = 'CLOSED', close_reason = 'TRANSITION_CLOSE', closed_utc = ? WHERE id = ?",
                    (now_utc.isoformat(), gid)
                )
                engine.assert_cash_invariant(conn)
                logger.info("Legacy group %d (%s) closed via TRANSITION_CLOSE.", gid, sym)

            cur.execute("UPDATE account SET v58_transition_done = 1 WHERE id = 1")
        conn.close()

    def execute_transition_close(self):
        """Public alias for transition close (SPEC V5.8-4, §9 Rule 24(c))."""
        return self._check_and_execute_transition_close()

    def poll_once(self, now_utc: datetime, now_monotonic: float):
        """Execute one complete polling cycle strictly following SPEC v5.1 §5.0."""
        # 1. Re-verify events.csv every round (§7)
        try:
            ver, sha, evs = engine.load_events_calendar(config.EVENTS_CSV_PATH)
            self.events_version = ver
            self.events_sha = sha
            self.events = evs
            events_ok = True
        except Exception as e:
            logger.error("data/events.csv validation failed: %s. Blocking new groups.", e)
            events_ok = False

        conn = engine.get_db_connection(self.db_path)
        try:
            self._execute_poll_cycle(conn, now_utc, now_monotonic, events_ok)
        finally:
            conn.close()

    def _log_risk_event(self, conn: sqlite3.Connection, utc_str: str, event_type: str, detail: str):
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO risk_events (utc, type, detail) VALUES (?, ?, ?)",
            (utc_str, event_type, detail)
        )

    def _compute_current_equity(
        self, conn: sqlite3.Connection, valuation_prices: Dict[str, Optional[float]]
    ) -> Tuple[float, float]:
        """Compute (current_equity, unrealized_pnl) from DB state using fixed valuation_prices."""
        cur = conn.cursor()
        cur.execute("SELECT cash_usdt FROM account WHERE id = 1")
        acc_row = cur.fetchone()
        cash = acc_row["cash_usdt"] if acc_row else 0.0

        cur.execute("""
            SELECT l.qty, l.buy_price, g.symbol
            FROM layers l
            JOIN grid_groups g ON l.group_id = g.id
            WHERE l.status = 'OPEN'
        """)
        open_market_val = 0.0
        unrealized = 0.0
        for lay in cur.fetchall():
            sym = lay["symbol"]
            px = valuation_prices.get(sym)
            if px is None:
                continue
            open_market_val += lay["qty"] * px
            unrealized += lay["qty"] * (px - lay["buy_price"])

        return cash + open_market_val, unrealized

    def _execute_poll_cycle(self, conn: sqlite3.Connection, now_utc: datetime, now_monotonic: float, events_ok: bool):
        cur = conn.cursor()
        if not events_ok:
            self._log_risk_event(conn, now_utc.isoformat(), "REGIME_BLOCK", "events.csv validation failed")
            conn.commit()
        cur.execute("SELECT * FROM account WHERE id = 1")
        acc = dict(cur.fetchone())

        # 2. Time Jump Guard (§5.0, v4-1, v4-7)
        time_jump = False
        last_poll_utc_str = acc.get("last_poll_utc")
        last_poll_mono = acc.get("last_poll_monotonic_s")
        if last_poll_utc_str and last_poll_mono is not None:
            last_poll_dt = datetime.fromisoformat(last_poll_utc_str.replace("Z", "+00:00")).astimezone(timezone.utc)
            wall_delta = (now_utc - last_poll_dt).total_seconds()
            mono_delta = now_monotonic - last_poll_mono
            jump_diff = abs(wall_delta - mono_delta)
            if jump_diff > config.TIME_JUMP_THRESHOLD_SEC:
                time_jump = True
                logger.warning(
                    "System time jump detected: |(now - last) - delta_mono| = |%.2f - %.2f| = %.2f > 60s. "
                    "Skipping expiration judgments this round.",
                    wall_delta, mono_delta, jump_diff
                )

        # 3. Server Time Check (§4.2)
        server_dt, st_err = self.client.get_server_time()
        server_time_valid = False
        if server_dt:
            drift = abs((server_dt - now_utc).total_seconds())
            if drift <= config.SERVER_TIME_MAX_DRIFT_SEC:
                server_time_valid = True
            else:
                logger.warning("Server time drift too large: %.2fs > 60s", drift)
        else:
            logger.warning("Failed to obtain server time: %s", st_err)

        # 4. Symbol Prices & Suspicious Price State Machine (§4.2, v4-3, v5-3, v5-4, v5.1-1)
        symbols = ["BTCUSDT", "ETHUSDT"]
        sym_states = {}
        valuation_prices = {}
        symbol_klines = {}
        block_trading = {}  # Flag indicating whether opening/adding is prohibited for symbol this round
        skip_tp = {}        # Flag indicating whether TP judgment is skipped for symbol this round

        for sym in symbols:
            cur.execute("SELECT * FROM symbol_state WHERE symbol = ?", (sym,))
            s_row = dict(cur.fetchone())
            sym_states[sym] = s_row
            block_trading[sym] = False
            skip_tp[sym] = False

            # Fetch price and klines
            raw_price, p_err = None, None
            if server_time_valid:
                raw_price, p_err = self.client.get_ticker_price(sym)

            klines, k_err = self.client.get_recent_closed_klines(sym, config.MIN_KLINES_BARS)
            symbol_klines[sym] = klines
            latest_kline_close = float(klines[-1][4]) if klines and len(klines) > 0 else None

            # Handle price validity & state transitions
            last_valid = s_row["last_valid_price"]
            last_suspect = s_row["last_suspect_price"]
            err_streak = s_row["price_error_streak"]
            sus_streak = s_row["suspect_price_streak"]
            rec_streak = s_row["recovery_streak"]
            in_recovery = (err_streak >= config.ERROR_STREAK_THRESHOLD or sus_streak >= config.SUSPECT_STREAK_THRESHOLD)

            if raw_price is None or not server_time_valid:
                # Invalid price reading
                err_streak += 1
                if in_recovery:
                    rec_streak = 0
                if err_streak == config.ERROR_STREAK_THRESHOLD:
                    logger.error("Price error streak reached 3 for %s!", sym)
                elif err_streak > config.ERROR_STREAK_THRESHOLD:
                    logger.warning("Price error streak is %d for %s", err_streak, sym)
                else:
                    logger.warning("Price read failed for %s: %s", sym, p_err)

                self._log_risk_event(
                    conn, now_utc.isoformat(), "PRICE_STALE",
                    f"{sym} invalid price or server time (err_streak={err_streak}): {p_err or st_err}"
                )
                valuation_prices[sym] = last_valid
                block_trading[sym] = True
                skip_tp[sym] = True
            else:
                # Valid price HTTP response
                if last_valid is None:
                    # Initial reading
                    last_valid = raw_price
                    last_suspect = None
                    err_streak = 0
                    sus_streak = 0
                    rec_streak = 0
                    valuation_prices[sym] = raw_price
                else:
                    jump = abs(raw_price - last_valid) / last_valid
                    if jump <= config.SUSPECT_PRICE_JUMP_PCT:
                        # Normal valid price
                        if not in_recovery:
                            err_streak = 0
                            sus_streak = 0
                            last_suspect = None
                        else:
                            rec_streak += 1
                            if rec_streak >= config.RECOVERY_STREAK_REQUIRED:
                                err_streak = 0
                                sus_streak = 0
                                rec_streak = 0
                                last_suspect = None

                        last_valid = raw_price
                        valuation_prices[sym] = raw_price
                        if err_streak >= config.ERROR_STREAK_THRESHOLD or sus_streak >= config.SUSPECT_STREAK_THRESHOLD:
                            block_trading[sym] = True
                            skip_tp[sym] = True
                    else:
                        # Jump > 10%: Suspicious price P2!
                        P2 = raw_price
                        P1 = last_suspect
                        logger.warning("Suspicious price jump %.2f%% for %s (P2=%.2f, last_valid=%.2f)", jump * 100, sym, P2, last_valid)
                        self._log_risk_event(
                            conn, now_utc.isoformat(), "SUSPECT_PRICE",
                            f"{sym} price jump {jump * 100:.2f}% > 10% (P2={P2:.2f}, last_valid={last_valid:.2f})"
                        )

                        # Check Condition (a)
                        accepted_a = False
                        if P1 is not None:
                            same_dir = ((P2 - last_valid) * (P1 - last_valid)) > 0
                            diff_p1 = abs(P2 - P1) / P1
                            if same_dir and diff_p1 <= config.SUSPECT_PRICE_ACCEPT_PCT:
                                accepted_a = True

                        # Check Condition (b)
                        accepted_b = False
                        if not accepted_a and latest_kline_close is not None:
                            k_diff = abs(P2 - latest_kline_close) / latest_kline_close
                            if k_diff <= config.SUSPECT_PRICE_KLINE_CLOSE_PCT:
                                accepted_b = True

                        if accepted_a:
                            logger.info("Suspicious price P2=%.2f accepted via Condition (a) for %s", P2, sym)
                            last_valid = P2
                            last_suspect = None
                            sus_streak = 0
                            rec_streak = 0
                            valuation_prices[sym] = P2
                            if err_streak >= config.ERROR_STREAK_THRESHOLD:
                                block_trading[sym] = True
                                skip_tp[sym] = True
                            # Condition (a) participates normally in this round's valuation & decisions
                        elif accepted_b:
                            logger.info("Suspicious price P2=%.2f accepted via Condition (b) (kline close) for %s", P2, sym)
                            # Accepted for NEXT round; this round uses OLD last_valid
                            old_valid = last_valid
                            last_valid = P2
                            last_suspect = None
                            sus_streak = 0
                            rec_streak = 0
                            valuation_prices[sym] = old_valid
                            block_trading[sym] = True
                            skip_tp[sym] = True
                        else:
                            # Not accepted
                            last_suspect = P2
                            sus_streak += 1
                            if in_recovery:
                                rec_streak = 0
                            if sus_streak == config.SUSPECT_STREAK_THRESHOLD:
                                logger.error("Suspect price streak reached 3 for %s!", sym)
                            valuation_prices[sym] = last_valid
                            block_trading[sym] = True
                            skip_tp[sym] = True

            # Update symbol state in DB
            cur.execute("""
                UPDATE symbol_state
                SET last_valid_price = ?,
                    last_valid_price_utc = ?,
                    last_suspect_price = ?,
                    price_error_streak = ?,
                    suspect_price_streak = ?,
                    recovery_streak = ?
                WHERE symbol = ?
            """, (last_valid, now_utc.isoformat(), last_suspect, err_streak, sus_streak, rec_streak, sym))

        conn.commit()

        # 5. Portfolio Valuation & Single Valuation Price (§1.4, v4-10, v5-2)
        current_equity, _ = self._compute_current_equity(conn, valuation_prices)
        stored_peak_equity = acc["peak_equity_usdt"]

        # 6. Daily Equity Tracking (§5.2)
        day_start_equity = acc["day_start_equity_usdt"]
        day_start_date = acc.get("day_start_date_utc")
        today_date_str = now_utc.strftime("%Y-%m-%d")
        if day_start_date != today_date_str:
            last_eq_utc = acc.get("last_equity_utc")
            if last_eq_utc and last_eq_utc < f"{today_date_str}T00:00:00Z":
                day_start_equity = acc.get("last_equity_usdt") or current_equity
            else:
                day_start_equity = current_equity
                logger.warning("No pre-midnight equity recorded. day_start_equity approximated at %.2f", day_start_equity)
            day_start_date = today_date_str
            cur.execute("UPDATE account SET day_start_equity_usdt = ?, day_start_date_utc = ? WHERE id = 1",
                        (day_start_equity, day_start_date))
            conn.commit()

        daily_loss = current_equity - day_start_equity
        daily_loss_blocked = (daily_loss < config.DAILY_LOSS_LIMIT_USDT)
        if daily_loss_blocked:
            self._log_risk_event(
                conn, now_utc.isoformat(), "DAILY_LOSS_3",
                f"Daily loss {daily_loss:.2f} USDT < {config.DAILY_LOSS_LIMIT_USDT:.2f} USDT"
            )
            conn.commit()

        # 7. §5.0 Sequence of Decisions
        halt_state = acc.get("halt_state", "NONE")
        halt_active = acc.get("halt_active", 0)
        halted_until_str = acc.get("halted_until_utc")

        # --- (0) HALTING Guard (§5.0(0)) ---
        if halt_state == "HALTING":
            logger.info("HALTING state active: Resuming liquidation of open layers...")
            self._execute_halting_sweep(conn, valuation_prices, now_utc)
            self._end_round_bookkeeping(conn, valuation_prices, now_utc, now_monotonic)
            return

        # --- (a) Halt Expiration Recovery (§5.0(a)) ---
        skip_drawdown_check_this_round = False
        if halt_active == 1 and halted_until_str:
            halted_until_dt = datetime.fromisoformat(halted_until_str.replace("Z", "+00:00")).astimezone(timezone.utc)
            if now_utc >= halted_until_dt:
                # Defensive check (§5.0(a) / V5.4-1(c)): halt_active=1 and any symbol last_valid_price is None -> ERROR, do not reset peak, do not clear halt
                cur.execute("SELECT symbol, last_valid_price FROM symbol_state")
                sym_states = {row["symbol"]: row["last_valid_price"] for row in cur.fetchall()}
                if any(v is None for v in sym_states.values()):
                    logger.error("Halt expiration recovery blocked: defensive check failed (halt_active=1 and symbol last_valid_price is None: %s)", sym_states)
                    skip_drawdown_check_this_round = True
                elif not time_jump:
                    logger.info("Halt cooldown period expired. Recovering normal state, resetting peak equity to %.2f", current_equity)
                    with conn:
                        cur.execute("""
                            UPDATE account
                            SET halt_active = 0,
                                halt_state = 'NONE',
                                halted_until_utc = NULL,
                                peak_equity_usdt = ?
                            WHERE id = 1
                        """, (current_equity,))
                    stored_peak_equity = current_equity
                    skip_drawdown_check_this_round = True
                    halt_active = 0
                    halt_state = 'NONE'
                else:
                    logger.warning("Time jump guard active: delaying halt expiration recovery to next round.")
                    skip_drawdown_check_this_round = True

        # --- (b) Drawdown Halt Check (§5.1) ---
        if halt_active == 0 and halt_state == "NONE" and not skip_drawdown_check_this_round:
            peak_for_dd = max(stored_peak_equity, current_equity)
            drawdown = (peak_for_dd - current_equity) / peak_for_dd if peak_for_dd > 0 else 0.0
            if drawdown >= config.DRAWDOWN_HALT_PCT:
                logger.error("ACCOUNT DRAWDOWN HALT TRIGGERED: drawdown=%.2f%% >= 15.00%%!", drawdown * 100)
                with conn:
                    self._log_risk_event(
                        conn, now_utc.isoformat(), "DRAWDOWN_15",
                        f"Account drawdown {drawdown * 100:.2f}% >= 15.00% (peak={peak_for_dd:.2f}, eq={current_equity:.2f})"
                    )
                cur.execute("SELECT COUNT(*) as cnt FROM layers WHERE status = 'OPEN'")
                open_cnt = cur.fetchone()["cnt"]

                if open_cnt == 0:
                    trig_str = now_utc.isoformat()
                    until_dt = now_utc + timedelta(hours=config.HALT_COOLDOWN_HOURS)
                    post_eq, _ = self._compute_current_equity(conn, valuation_prices)
                    with conn:
                        cur.execute("""
                            UPDATE account
                            SET halt_state = 'HALTED',
                                halt_active = 1,
                                halt_triggered_utc = ?,
                                halted_until_utc = ?,
                                halt_trigger_equity_usdt = ?,
                                halt_progress_total_layers = 0,
                                halt_progress_sold_layers = 0,
                                halt_zero_progress_rounds = 0
                            WHERE id = 1
                        """, (trig_str, until_dt.isoformat(), post_eq))
                else:
                    with conn:
                        cur.execute("""
                            UPDATE account
                            SET halt_state = 'HALTING',
                                halt_triggered_utc = ?,
                                halt_progress_total_layers = ?,
                                halt_progress_sold_layers = 0,
                                halt_zero_progress_rounds = 0
                            WHERE id = 1
                        """, (now_utc.isoformat(), open_cnt))

                    # Immediately perform first liquidation sweep
                    self._execute_halting_sweep(conn, valuation_prices, now_utc)

                self._end_round_bookkeeping(conn, valuation_prices, now_utc, now_monotonic)
                return

        # --- (b2) Market Trend Regime Determination & Logging (§4.3, §5.0(b2)) ---
        current_regimes = {}
        for sym in symbols:
            klines = symbol_klines.get(sym)
            inds = engine.calculate_indicators(klines) if klines else {}
            adx = inds.get("adx")
            plus_di = inds.get("plus_di")
            minus_di = inds.get("minus_di")
            ret24 = inds.get("ret24")

            regime = None
            if adx is not None and plus_di is not None and minus_di is not None and ret24 is not None:
                regime = engine.classify_regime(adx, plus_di, minus_di, ret24, self.prev_valid_regime.get(sym))
                self.prev_valid_regime[sym] = regime
            else:
                # Indicator unavailable: keep prev_valid_regime unchanged, prohibit opening and adding (§4.3)
                block_trading[sym] = True

            current_regimes[sym] = regime

            # Check allow_new_group (§4.1)
            cond1 = (adx is not None and adx < config.ADX_THRESHOLD)
            cond2 = inds.get("in_range", False)
            atr = inds.get("atr")
            atr_med = inds.get("atr_median_480")
            cond3 = (atr is not None and atr_med is not None and atr < config.ATR_MEDIAN_MULTIPLIER * atr_med)
            ev_24h, ev_past_2h, is_stale, ev_err = engine.check_event_filter(self.events, now_utc)
            cond4 = (not ev_24h and not ev_past_2h and not is_stale)
            cond7 = bool(inds.get("pos_ok", False))
            allow_new = int(cond1 and cond2 and cond3 and cond4 and cond7 and not block_trading[sym] and not daily_loss_blocked)

            cur.execute("""
                INSERT INTO regime_log (
                    utc, symbol, adx, atr_pct, in_range,
                    event_in_24h, event_in_past_2h, allow_new_group,
                    regime, ret24_pct, plus_di, minus_di
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now_utc.isoformat(), sym, adx, inds.get("atr_pct"), int(cond2),
                int(ev_24h), int(ev_past_2h), allow_new,
                regime, ret24, plus_di, minus_di
            ))
            if not allow_new and not (cond1 and cond2 and cond3 and cond4 and cond7):
                self._log_risk_event(
                    conn, now_utc.isoformat(), "REGIME_BLOCK",
                    f"{sym} blocked: adx_ok={cond1}, range_ok={cond2}, atr_ok={cond3}, event_ok={cond4}, pos_ok={cond7}"
                )
        conn.commit()

        # --- (c) In-group Exits & (d) Buys ---
        if halt_active == 0 and halt_state == "NONE":
            # For each symbol, process open group exits and buys
            for sym in symbols:
                cur.execute("SELECT * FROM grid_groups WHERE symbol = ? AND status = 'OPEN'", (sym,))
                group_row = cur.fetchone()
                val_px = valuation_prices.get(sym)

                if group_row:
                    group = dict(group_row)
                    self._process_group_exits_and_adds(
                        conn, group, val_px, now_utc, time_jump,
                        block_trading[sym], skip_tp[sym], daily_loss_blocked,
                        current_regime=current_regimes.get(sym)
                    )
                else:
                    # No open group: consider opening group if allowed
                    if not block_trading[sym] and not daily_loss_blocked and events_ok:
                        self._consider_open_new_group(conn, sym, val_px, now_utc, klines=symbol_klines.get(sym))

        self._end_round_bookkeeping(conn, valuation_prices, now_utc, now_monotonic)

    def _execute_halting_sweep(self, conn: sqlite3.Connection, valuation_prices: Dict[str, Optional[float]], now_utc: datetime):
        cur = conn.cursor()
        cur.execute("SELECT halt_zero_progress_rounds, halt_triggered_utc FROM account WHERE id = 1")
        acc_row = cur.fetchone()
        halt_zero_progress_rounds = acc_row["halt_zero_progress_rounds"] if acc_row else 0

        cur.execute("""
            SELECT l.*, g.symbol
            FROM layers l
            JOIN grid_groups g ON l.group_id = g.id
            WHERE l.status = 'OPEN'
            ORDER BY g.symbol ASC, l.layer_no ASC
        """)
        open_layers = [dict(r) for r in cur.fetchall()]
        sold_count = 0
        for lay in open_layers:
            sym = lay["symbol"]
            val_px = valuation_prices.get(sym)
            if val_px is None:
                logger.error(
                    "HALTING sweep skip: layer %d (%s) has no valid valuation price (valuation_prices[%s] is None)",
                    lay["id"], sym, sym
                )
                continue
            try:
                with conn:
                    self._liquidate_layer(conn, lay, val_px, now_utc, "DRAWDOWN_HALT")
                    cur.execute("UPDATE account SET halt_progress_sold_layers = halt_progress_sold_layers + 1 WHERE id = 1")
                    sold_count += 1
                    cur.execute("SELECT COUNT(*) as cnt FROM layers WHERE group_id = ? AND status = 'OPEN'", (lay["group_id"],))
                    if cur.fetchone()["cnt"] == 0:
                        cur.execute("""
                            UPDATE grid_groups
                            SET status = 'CLOSED', close_reason = 'DRAWDOWN_HALT', closed_utc = ?
                            WHERE id = ?
                        """, (now_utc.isoformat(), lay["group_id"]))
                        cur.execute("UPDATE layers SET status = 'CANCELLED' WHERE group_id = ? AND status = 'PENDING'", (lay["group_id"],))
                engine.assert_cash_invariant(conn)
            except Exception as e:
                logger.error("Sweep liquidation failed for layer %d: %s", lay["id"], e)

        if sold_count == 0 and len(open_layers) > 0:
            halt_zero_progress_rounds += 1
            if halt_zero_progress_rounds == config.HALTING_ZERO_PROGRESS_ROUNDS_STOP:
                logger.error("HALTING continuous zero progress rounds reached 3! Stopping new trades.")
        elif sold_count > 0:
            halt_zero_progress_rounds = 0

        cur.execute("SELECT COUNT(*) as cnt FROM layers WHERE status = 'OPEN'")
        if cur.fetchone()["cnt"] == 0:
            cur.execute("SELECT halt_triggered_utc FROM account WHERE id = 1")
            trig_str = cur.fetchone()["halt_triggered_utc"]
            if trig_str:
                trig_dt = datetime.fromisoformat(trig_str.replace("Z", "+00:00")).astimezone(timezone.utc)
            else:
                trig_dt = now_utc
                trig_str = now_utc.isoformat()
            until_dt = trig_dt + timedelta(hours=config.HALT_COOLDOWN_HOURS)
            post_eq, _ = self._compute_current_equity(conn, valuation_prices)
            with conn:
                cur.execute("""
                    UPDATE account
                    SET halt_state = 'HALTED',
                        halt_active = 1,
                        halt_triggered_utc = ?,
                        halted_until_utc = ?,
                        halt_trigger_equity_usdt = ?,
                        halt_zero_progress_rounds = 0
                    WHERE id = 1
                """, (trig_str, until_dt.isoformat(), post_eq))
            logger.info(
                "All layers liquidated. State transitioned to HALTED until %s (halt_trigger_equity=%.2f)",
                until_dt.isoformat(), post_eq
            )
        else:
            with conn:
                cur.execute("UPDATE account SET halt_zero_progress_rounds = ? WHERE id = 1", (halt_zero_progress_rounds,))

    def _liquidate_layer(self, conn: sqlite3.Connection, layer: Dict[str, Any], sell_px: float, now_utc: datetime, reason: str):
        cur = conn.cursor()
        qty = layer["qty"]
        gross = round(qty * sell_px, 4)
        fee = round(gross * config.FEE_RATE, 4)
        slippage = round(gross * config.SLIPPAGE_RATE, 4)
        net_cash_add = gross - fee - slippage

        realized = round((sell_px - layer["buy_price"]) * qty - layer["buy_fee_usdt"] - layer["buy_slippage_usdt"] - fee - slippage, 4)

        cur.execute("""
            UPDATE layers
            SET status = 'HALT_CLOSED', sold_utc = ?, sell_price = ?
            WHERE id = ?
        """, (now_utc.isoformat(), sell_px, layer["id"]))

        cur.execute("""
            INSERT INTO trades (
                utc, symbol, side, price, qty, notional_usdt,
                fee_usdt, slippage_usdt, group_id, layer_no, reason
            ) VALUES (?, ?, 'SELL', ?, ?, ?, ?, ?, ?, ?, ?)
        """, (now_utc.isoformat(), layer.get("symbol", "BTCUSDT"), sell_px, qty, gross, fee, slippage, layer["group_id"], layer["layer_no"], reason))

        cur.execute("""
            UPDATE account
            SET cash_usdt = cash_usdt + ?,
                realized_pnl_usdt = realized_pnl_usdt + ?
            WHERE id = 1
        """, (net_cash_add, realized))

    def _process_group_exits_and_adds(
        self, conn: sqlite3.Connection, group: Dict[str, Any], val_px: Optional[float],
        now_utc: datetime, time_jump: bool, block_trading: bool, skip_tp: bool, daily_loss_blocked: bool,
        current_regime: Optional[str] = None
    ):
        cur = conn.cursor()
        gid = group["id"]
        sym = group["symbol"]
        if val_px is None:
            return

        cur.execute("SELECT * FROM layers WHERE group_id = ? ORDER BY layer_no ASC", (gid,))
        all_layers = [dict(r) for r in cur.fetchall()]
        open_layers = [l for l in all_layers if l["status"] == "OPEN"]
        first_buy_str = group.get("first_buy_utc")
        first_buy_dt = datetime.fromisoformat(first_buy_str.replace("Z", "+00:00")).astimezone(timezone.utc) if first_buy_str else now_utc

        # HOLDMAX guard (§5.3): bought_layers = layers with status in OPEN, SOLD, TIME_STOPPED
        # When bought_layers == 9, skip 7d half and 14d liquidation, keep holding without setting half_time_stopped or stop_adding
        cur.execute("""
            SELECT COUNT(*) as cnt FROM layers
            WHERE group_id = ? AND status IN ('OPEN', 'SOLD', 'TIME_STOPPED')
        """, (gid,))
        bought_layers = cur.fetchone()["cnt"]
        fully_filled = getattr(config, "HOLDMAX_EXEMPT_TIME_STOP", True) and (bought_layers == config.MAX_LAYERS)

        # Check exit conditions in order of §5.0(c) priority
        # Priority 2: 14 Days Time Stop
        if (not fully_filled) and (not time_jump) and (now_utc - first_buy_dt >= timedelta(days=config.TIME_STOP_14D_DAYS)):
            logger.info("Group %d reached 14-day time stop. Liquidating all open layers.", gid)
            with conn:
                for lay in open_layers:
                    self._sell_layer(conn, lay, sym, val_px, now_utc, "TIME_STOP_14D", layer_status="TIME_STOPPED")
                cur.execute("UPDATE grid_groups SET status = 'CLOSED', close_reason = 'TIME_STOP_14D', closed_utc = ? WHERE id = ?",
                            (now_utc.isoformat(), gid))
                cur.execute("UPDATE layers SET status = 'CANCELLED' WHERE group_id = ? AND status = 'PENDING'", (gid,))
            engine.assert_cash_invariant(conn)
            return

        # Priority 3: Group Take Profit (+1.5%, or +0.8% in REBOUND)
        if not skip_tp and open_layers:
            total_qty = sum(l["qty"] for l in open_layers)
            total_cost = sum(l["qty"] * l["buy_price"] for l in open_layers)
            avg_cost = total_cost / total_qty if total_qty > 0 else 0.0
            tp_coeff = config.REBOUND_TP_COEFF if current_regime == "REBOUND" else (1.0 + config.GROUP_TP_PCT)
            if val_px >= avg_cost * tp_coeff:
                logger.info("Group %d achieved group TP (val_px=%.2f >= avg=%.2f * %.4f, regime=%s). Closing group.", gid, val_px, avg_cost, tp_coeff, current_regime)
                with conn:
                    for lay in open_layers:
                        self._sell_layer(conn, lay, sym, val_px, now_utc, "GROUP_TP", layer_status="SOLD")
                    cur.execute("UPDATE grid_groups SET status = 'CLOSED', close_reason = 'TAKE_PROFIT', closed_utc = ? WHERE id = ?",
                                (now_utc.isoformat(), gid))
                    cur.execute("UPDATE layers SET status = 'CANCELLED' WHERE group_id = ? AND status = 'PENDING'", (gid,))
                engine.assert_cash_invariant(conn)
                return

        # Priority 4: 7 Days Time Stop (Half, execute once)
        if (not fully_filled) and (not time_jump) and group.get("half_time_stopped", 0) == 0 and (now_utc - first_buy_dt >= timedelta(days=config.TIME_STOP_7D_DAYS)):
            logger.info("Group %d reached 7-day time stop. Halving positions.", gid)
            # Sell from highest layer_no down to lowest layer_no until cumulative actual notional >= 50% of current open layers actual notional (§5.3)
            open_sorted_desc = sorted(open_layers, key=lambda l: l["layer_no"], reverse=True)
            total_open_actual = sum(l["notional_usdt"] for l in open_layers)
            target_cut = total_open_actual * 0.50
            cum_cut = 0.0
            layers_to_sell = []
            if len(open_layers) == 1:
                layers_to_sell = open_layers
            else:
                for lay in open_sorted_desc:
                    layers_to_sell.append(lay)
                    cum_cut += lay["notional_usdt"]
                    if cum_cut >= target_cut:
                        break

            with conn:
                for lay in layers_to_sell:
                    self._sell_layer(conn, lay, sym, val_px, now_utc, "TIME_STOP_7D", layer_status="TIME_STOPPED")
                cur.execute("UPDATE grid_groups SET half_time_stopped = 1, stop_adding = 1 WHERE id = ?", (gid,))
                cur.execute("UPDATE layers SET status = 'CANCELLED' WHERE group_id = ? AND status = 'PENDING'", (gid,))
                self._log_risk_event(conn, now_utc.isoformat(), "STOP_ADDING", f"{sym} group {gid} 7-day time stop set stop_adding=1")
                # Check if all OPEN layers were sold
                cur.execute("SELECT COUNT(*) as cnt FROM layers WHERE group_id = ? AND status = 'OPEN'", (gid,))
                if cur.fetchone()["cnt"] == 0:
                    cur.execute("UPDATE grid_groups SET status = 'CLOSED', close_reason = 'TIME_STOP_7D', closed_utc = ? WHERE id = ?",
                                (now_utc.isoformat(), gid))
            engine.assert_cash_invariant(conn)
            return

        # Priority 5: Single Layer TP
        if not skip_tp and open_layers:
            c = group["center_price"]
            d = group["spacing_pct"]
            qualified_layers = []
            for lay in open_layers:
                tp_px = engine.compute_tp_price(c, d, lay["layer_no"])
                if val_px >= tp_px:
                    qualified_layers.append(lay)

            if qualified_layers:
                with conn:
                    for lay in qualified_layers:
                        self._sell_layer(conn, lay, sym, val_px, now_utc, "LAYER_TP", layer_status="SOLD")
                    cur.execute("SELECT COUNT(*) as cnt FROM layers WHERE group_id = ? AND status = 'OPEN'", (gid,))
                    if cur.fetchone()["cnt"] == 0:
                        cur.execute("UPDATE grid_groups SET status = 'CLOSED', close_reason = 'ALL_SOLD', closed_utc = ? WHERE id = ?",
                                    (now_utc.isoformat(), gid))
                        cur.execute("UPDATE layers SET status = 'CANCELLED' WHERE group_id = ? AND status = 'PENDING'", (gid,))
                engine.assert_cash_invariant(conn)
                return

        # --- Check Buying / Adding Layers ---
        if block_trading or daily_loss_blocked:
            return

        # Check Stop Adding condition: last price < 0.80 * C (§4.2, v5.7)
        c = group["center_price"]
        if val_px < c * config.STOP_ADDING_DROP_PCT:
            if group.get("stop_adding", 0) == 0:
                logger.info("Price %.2f < 0.80 * C (%.2f) for group %d. Setting stop_adding=1 and cancelling PENDING layers.", val_px, c * config.STOP_ADDING_DROP_PCT, gid)
                with conn:
                    cur.execute("UPDATE grid_groups SET stop_adding = 1 WHERE id = ?", (gid,))
                    cur.execute("UPDATE layers SET status = 'CANCELLED' WHERE group_id = ? AND status = 'PENDING'", (gid,))
                    self._log_risk_event(
                        conn, now_utc.isoformat(), "STOP_ADDING",
                        f"{sym} group {gid} price {val_px:.2f} < 0.80*C ({c * config.STOP_ADDING_DROP_PCT:.2f})"
                    )
                return

        if group.get("stop_adding", 0) == 1:
            return

        # Market regime checks on adding (§4.3)
        if current_regime == "STRONG_DOWN":
            logger.info("Group %d adding skipped: STRONG_DOWN active (PENDING retained)", gid)
            return

        if current_regime == "SLOW_DOWN":
            cur.execute("""
                SELECT MAX(bought_utc) as last_buy FROM layers
                WHERE group_id = ? AND layer_no >= 2 AND status IN ('OPEN', 'SOLD', 'TIME_STOPPED')
            """, (gid,))
            r = cur.fetchone()
            if r and r["last_buy"]:
                last_buy_dt = datetime.fromisoformat(r["last_buy"].replace("Z", "+00:00")).astimezone(timezone.utc)
                if (now_utc - last_buy_dt).total_seconds() < config.SLOW_DOWN_COOLDOWN_SECS:
                    logger.info("Group %d adding skipped: SLOW_DOWN cooldown active (< 86400s)", gid)
                    return

        # Group drawdown DD and dynamic multiplier ladder (§2, §3.2)
        dd = (c - val_px) / c
        mult, permitted = engine.get_dynamic_multiplier(dd, current_regime)
        if not permitted or mult is None:
            return

        # Check pending layers sequentially to support multi-layer plunge in a single round (§3.2)
        cur.execute("SELECT * FROM layers WHERE group_id = ? AND status = 'PENDING' ORDER BY layer_no ASC", (gid,))
        pending_layers = [dict(r) for r in cur.fetchall()]

        for play in pending_layers:
            lno = play["layer_no"]
            # Layer n can buy iff layers 1..n-1 were ever bought (status in OPEN, SOLD, TIME_STOPPED)
            cur.execute("""
                SELECT COUNT(*) as cnt FROM layers
                WHERE group_id = ? AND layer_no < ? AND status IN ('OPEN', 'SOLD', 'TIME_STOPPED')
            """, (gid, lno))
            if cur.fetchone()["cnt"] != (lno - 1):
                break

            trig_px = play["trigger_price"]
            if val_px < trig_px:
                # Recursive planned notional from layer n-1 planned_notional_usdt (§2, §6.1, §9 Rule 19)
                cur.execute("SELECT planned_notional_usdt FROM layers WHERE group_id = ? AND layer_no = ?", (gid, lno - 1))
                prev_row = cur.fetchone()
                if prev_row is None or prev_row["planned_notional_usdt"] is None:
                    # NULL fallback per §6.1, §8.3(6):
                    logger.error("Group %d layer %d: prev planned_notional_usdt is NULL! Triggering NULL fallback.", gid, lno)
                    with conn:
                        cur.execute("UPDATE grid_groups SET stop_adding = 1 WHERE id = ?", (gid,))
                        cur.execute("UPDATE layers SET status = 'CANCELLED' WHERE group_id = ? AND status = 'PENDING'", (gid,))
                        self._log_risk_event(conn, now_utc.isoformat(), "STOP_ADDING", f"{sym} group {gid} layer {lno} NULL planned notional fallback")
                    break

                prev_planned = prev_row["planned_notional_usdt"]
                planned_notional = round(prev_planned * mult, 2)

                # Check caps and truncate (§2)
                cur.execute("SELECT SUM(qty * buy_price) as deployed FROM layers WHERE status = 'OPEN'")
                tot_deployed = cur.fetchone()["deployed"] or 0.0

                cur.execute("""
                    SELECT SUM(l.qty * l.buy_price) as sym_deployed
                    FROM layers l JOIN grid_groups g ON l.group_id = g.id
                    WHERE l.status = 'OPEN' AND g.symbol = ?
                """, (sym,))
                sym_deployed = cur.fetchone()["sym_deployed"] or 0.0

                sym_cap = config.BTC_CAP_USDT if sym == "BTCUSDT" else config.ETH_CAP_USDT
                tot_rem = max(0.0, config.TOTAL_DEPLOYED_CAP_USDT - tot_deployed)
                sym_rem = max(0.0, sym_cap - sym_deployed)
                trunc_planned = min(planned_notional, tot_rem, sym_rem)

                qty, actual_notional = engine.compute_order_qty(sym, trunc_planned, val_px)
                if trunc_planned < config.MIN_NOTIONAL_USDT or actual_notional < config.MIN_NOTIONAL_USDT or qty <= 0:
                    logger.error("Cap exceeded on layer %d buy! trunc=%.2f, actual=%.2f. Rejecting buy and cancelling remaining PENDING layers.",
                                 lno, trunc_planned, actual_notional)
                    with conn:
                        cur.execute("UPDATE layers SET status = 'CANCELLED' WHERE id = ?", (play["id"],))
                        cur.execute("UPDATE grid_groups SET stop_adding = 1 WHERE id = ?", (gid,))
                        cur.execute("UPDATE layers SET status = 'CANCELLED' WHERE group_id = ? AND status = 'PENDING'", (gid,))
                        self._log_risk_event(
                            conn, now_utc.isoformat(), "LAYER_CAP",
                            f"{sym} group {gid} layer {lno} buy rejected by cap (tot={tot_deployed:.2f}+{actual_notional:.2f}, sym={sym_deployed:.2f}+{actual_notional:.2f})"
                        )
                        self._log_risk_event(
                            conn, now_utc.isoformat(), "STOP_ADDING",
                            f"{sym} group {gid} stop_adding=1 due to layer {lno} cap rejection"
                        )
                    break

                # Execute buy in transaction
                fee = round(actual_notional * config.FEE_RATE, 4)
                slippage = round(actual_notional * config.SLIPPAGE_RATE, 4)
                total_cash_out = actual_notional + fee + slippage

                with conn:
                    cur.execute("""
                        UPDATE layers
                        SET status = 'OPEN', buy_price = ?, qty = ?, notional_usdt = ?,
                            planned_notional_usdt = ?, buy_fee_usdt = ?, buy_slippage_usdt = ?, bought_utc = ?
                        WHERE id = ?
                    """, (val_px, qty, actual_notional, planned_notional, fee, slippage, now_utc.isoformat(), play["id"]))

                    cur.execute("""
                        INSERT INTO trades (
                            utc, symbol, side, price, qty, notional_usdt,
                            fee_usdt, slippage_usdt, group_id, layer_no, reason
                        ) VALUES (?, ?, 'BUY', ?, ?, ?, ?, ?, ?, ?, 'LAYER_BUY')
                    """, (now_utc.isoformat(), sym, val_px, qty, actual_notional, fee, slippage, gid, lno))

                    cur.execute("UPDATE account SET cash_usdt = cash_usdt - ? WHERE id = 1", (total_cash_out,))
                engine.assert_cash_invariant(conn)
                logger.info("Bought layer %d for group %d: qty=%.4f @ %.2f (notional=%.2f, planned=%.2f)", lno, gid, qty, val_px, actual_notional, planned_notional)

                # In SLOW_DOWN regime, max 1 layer can be added per round (§3.2, §4.3)
                if current_regime == "SLOW_DOWN":
                    break
            else:
                break

    def _sell_layer(
        self, conn: sqlite3.Connection, layer: Dict[str, Any], symbol: str,
        sell_px: float, now_utc: datetime, reason: str, layer_status: str
    ):
        cur = conn.cursor()
        qty = layer["qty"]
        gross = round(qty * sell_px, 4)
        fee = round(gross * config.FEE_RATE, 4)
        slippage = round(gross * config.SLIPPAGE_RATE, 4)
        net_cash_in = gross - fee - slippage

        realized = round((sell_px - layer["buy_price"]) * qty - layer["buy_fee_usdt"] - layer["buy_slippage_usdt"] - fee - slippage, 4)

        cur.execute("""
            UPDATE layers
            SET status = ?, sold_utc = ?, sell_price = ?
            WHERE id = ?
        """, (layer_status, now_utc.isoformat(), sell_px, layer["id"]))

        cur.execute("""
            INSERT INTO trades (
                utc, symbol, side, price, qty, notional_usdt,
                fee_usdt, slippage_usdt, group_id, layer_no, reason
            ) VALUES (?, ?, 'SELL', ?, ?, ?, ?, ?, ?, ?, ?)
        """, (now_utc.isoformat(), symbol, sell_px, qty, gross, fee, slippage, layer["group_id"], layer["layer_no"], reason))

        cur.execute("""
            UPDATE account
            SET cash_usdt = cash_usdt + ?,
                realized_pnl_usdt = realized_pnl_usdt + ?
            WHERE id = 1
        """, (net_cash_in, realized))

    def _consider_open_new_group(self, conn: sqlite3.Connection, symbol: str, val_px: Optional[float], now_utc: datetime, klines: Optional[List[List[Any]]] = None):
        cur = conn.cursor()
        if val_px is None:
            return

        # Reuse klines fetched in the current round (§8.3(6), SPEC v5.8)
        if not klines or len(klines) < config.MIN_KLINES_BARS:
            logger.warning("Cannot open new group for %s: insufficient klines", symbol)
            with conn:
                self._log_risk_event(conn, now_utc.isoformat(), "REGIME_BLOCK", f"{symbol} insufficient klines")
            return

        inds = engine.calculate_indicators(klines)
        adx = inds["adx"]
        atr = inds["atr"]
        atr_med = inds["atr_median_480"]
        hh = inds["hh_480"]
        ll = inds["ll_480"]
        prev_c = inds["prev_close"]
        pos_ok = inds.get("pos_ok", False)

        # Check conditions §4.1:
        # 1. ADX < 30
        cond1 = (adx is not None and adx < config.ADX_THRESHOLD)
        # 2. LL_480 < prev_close < HH_480
        cond2 = (hh is not None and ll is not None and prev_c is not None and ll < prev_c < hh)
        # 3. ATR < 2.0 * ATR_median_480
        cond3 = (atr is not None and atr_med is not None and atr < config.ATR_MEDIAN_MULTIPLIER * atr_med)
        # 4. Economic events filter
        ev_24h, ev_past_2h, is_stale, ev_err = engine.check_event_filter(self.events, now_utc)
        cond4 = (not ev_24h and not ev_past_2h and not is_stale)
        # 7. Position filter (Position <= 0.35, SPEC v5.8)
        cond7 = bool(pos_ok)

        if not (cond1 and cond2 and cond3 and cond4 and cond7):
            return

        # Check capital caps for layer 1 (§2, §3.1)
        planned_l1 = config.BTC_LAYER_NOTIONALS[0] if symbol == "BTCUSDT" else config.ETH_LAYER_NOTIONALS[0]
        qty, actual_l1 = engine.compute_order_qty(symbol, planned_l1, val_px)
        if actual_l1 < config.MIN_NOTIONAL_USDT:
            logger.warning("Layer 1 notional %.2f < 5.00 USDT. Do not open group.", actual_l1)
            return

        cur.execute("SELECT SUM(qty * buy_price) as tot_dep FROM layers WHERE status = 'OPEN'")
        tot_dep = cur.fetchone()["tot_dep"] or 0.0
        cur.execute("""
            SELECT SUM(l.qty * l.buy_price) as sym_dep
            FROM layers l JOIN grid_groups g ON l.group_id = g.id
            WHERE l.status = 'OPEN' AND g.symbol = ?
        """, (symbol,))
        sym_dep = cur.fetchone()["sym_dep"] or 0.0

        sym_cap = config.BTC_CAP_USDT if symbol == "BTCUSDT" else config.ETH_CAP_USDT
        if (tot_dep + actual_l1 > config.TOTAL_DEPLOYED_CAP_USDT) or (sym_dep + actual_l1 > sym_cap):
            logger.warning("Opening group for %s would exceed caps. Skipping.", symbol)
            with conn:
                self._log_risk_event(
                    conn, now_utc.isoformat(), "LAYER_CAP",
                    f"{symbol} group open rejected by cap (tot={tot_dep:.2f}+{actual_l1:.2f}, sym={sym_dep:.2f}+{actual_l1:.2f})"
                )
            return

        # Open Group and Buy Layer 1 in atomic transaction (§3.1)
        d = engine.compute_grid_spacing(symbol, atr, val_px)
        fee = round(actual_l1 * config.FEE_RATE, 4)
        slippage = round(actual_l1 * config.SLIPPAGE_RATE, 4)
        cash_out = actual_l1 + fee + slippage

        with conn:
            cur.execute("""
                INSERT INTO grid_groups (
                    symbol, center_price, spacing_pct, atr_pct, status,
                    opened_utc, first_buy_utc, stop_adding, half_time_stopped
                ) VALUES (?, ?, ?, ?, 'OPEN', ?, ?, 0, 0)
            """, (symbol, val_px, d, inds["atr_pct"], now_utc.isoformat(), now_utc.isoformat()))
            gid = cur.lastrowid

            # Insert all 9 layers: Layer 1 is OPEN, Layers 2..9 are PENDING (§8.3(6))
            # Layer 1
            cur.execute("""
                INSERT INTO layers (
                    group_id, layer_no, trigger_price, buy_price, qty, notional_usdt,
                    planned_notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """, (gid, val_px, val_px, qty, actual_l1, planned_l1, fee, slippage, now_utc.isoformat()))

            # Layers 2..9 (PENDING rows do not write planned_notional_usdt, remains NULL)
            for lno in range(2, config.MAX_LAYERS + 1):
                trig = engine.compute_trigger_price(val_px, d, lno)
                cur.execute("""
                    INSERT INTO layers (
                        group_id, layer_no, trigger_price, status
                    ) VALUES (?, ?, ?, 'PENDING')
                """, (gid, lno, trig))

            # Trade record
            cur.execute("""
                INSERT INTO trades (
                    utc, symbol, side, price, qty, notional_usdt,
                    fee_usdt, slippage_usdt, group_id, layer_no, reason
                ) VALUES (?, ?, 'BUY', ?, ?, ?, ?, ?, ?, 1, 'GROUP_OPEN')
            """, (now_utc.isoformat(), symbol, val_px, qty, actual_l1, fee, slippage, gid))

            # Deduct cash
            cur.execute("UPDATE account SET cash_usdt = cash_usdt - ? WHERE id = 1", (cash_out,))

        engine.assert_cash_invariant(conn)
        logger.info("OPENED NEW GROUP for %s: gid=%d, C=%.2f, d=%.4f, layer 1 bought (qty=%.4f, notional=%.2f)",
                    symbol, gid, val_px, d, qty, actual_l1)

    def _end_round_bookkeeping(
        self, conn: sqlite3.Connection, valuation_prices: Dict[str, Optional[float]],
        now_utc: datetime, now_monotonic: float
    ):
        """
        Recompute current_equity and unrealized_pnl AFTER all round decisions/trades complete (§6.1),
        using this round's fixed valuation_prices, then persist last_equity, peak_equity, and daily_summary.
        """
        cur = conn.cursor()
        post_equity, unrealized = self._compute_current_equity(conn, valuation_prices)

        cur.execute("SELECT peak_equity_usdt, realized_pnl_usdt FROM account WHERE id = 1")
        acc = cur.fetchone()
        stored_peak = acc["peak_equity_usdt"] if acc else post_equity
        realized = acc["realized_pnl_usdt"] if acc else 0.0

        peak_equity = max(stored_peak, post_equity)
        drawdown_pct = (peak_equity - post_equity) / peak_equity if peak_equity > 0 else 0.0

        cur.execute("""
            UPDATE account
            SET peak_equity_usdt = ?,
                last_equity_usdt = ?,
                last_equity_utc = ?,
                last_poll_utc = ?,
                last_poll_monotonic_s = ?,
                updated_utc = ?
            WHERE id = 1
        """, (peak_equity, post_equity, now_utc.isoformat(), now_utc.isoformat(), now_monotonic, now_utc.isoformat()))

        cur.execute("SELECT COUNT(*) as cnt FROM trades")
        t_cnt = cur.fetchone()["cnt"]

        today_str = now_utc.strftime("%Y-%m-%d")
        cur.execute("""
            INSERT INTO daily_summary (
                date_utc, realized_pnl, unrealized_pnl, equity, drawdown_pct, trades_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date_utc) DO UPDATE SET
                realized_pnl = excluded.realized_pnl,
                unrealized_pnl = excluded.unrealized_pnl,
                equity = excluded.equity,
                drawdown_pct = excluded.drawdown_pct,
                trades_count = excluded.trades_count
        """, (today_str, realized, unrealized, post_equity, drawdown_pct, t_cnt))

        conn.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Spot Martingale Monitor (Binance Spot · DRY_RUN)")
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit")
    parser.add_argument("--rounds", type=int, default=None, help="Run N poll cycles and exit")
    parser.add_argument("--db", type=str, default=None, help="Database path override")
    args = parser.parse_args(argv)

    monitor = SpotMartingaleMonitor(db_path=args.db)
    monitor.startup_checks()
    monitor.execute_transition_close()

    logger.info("Spot Martingale Monitor initialized in DRY_RUN mode.")

    rounds_run = 0
    max_rounds = 1 if args.once else args.rounds

    try:
        while True:
            now_utc = datetime.now(timezone.utc)
            now_monotonic = time.monotonic()
            logger.info("--- Starting polling cycle at %s ---", now_utc.isoformat())

            monitor.poll_once(now_utc, now_monotonic)
            rounds_run += 1

            if max_rounds is not None and rounds_run >= max_rounds:
                logger.info("Completed %d round(s). Exiting as requested.", rounds_run)
                break

            time.sleep(config.POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        logger.info("Monitor received KeyboardInterrupt. Exiting cleanly.")

if __name__ == "__main__":
    main()
