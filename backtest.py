"""
Offline Backtest and Stress Testing Module (SPEC v5.7).
Implements close-only 1h matching, training/test set partition, and 4 stress test suites.
No external network calls; uses local CSV data.
"""

import sys
import os
import math
import logging
import argparse
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple, Optional, Any

import config
import engine

logger = logging.getLogger("spot-martingale-backtest")


def load_cached_klines(csv_path: str, return_details: bool = False) -> Any:
    """
    Load and clean cached 1h klines CSV according to SPEC v5.5 §8.1 / V5.4-2(a) / V5.5-3.
    Filter valid rows first:
      - open_time is UTC hourly timestamp (open_time % 3600000 == 0)
      - open, high, low, close are finite positive numbers (> 0)
    Then deduplicate by open_time ascending: keep the first valid row (file order).
    Non-hourly / unmappable records are recorded as data quality WARNINGs and NOT counted as price errors.
    Returns bars, or (bars, raw_grid_hours, all_invalid_grid_hours, data_quality_stats) if return_details=True.
    """
    valid_bars_dict: Dict[int, Dict[str, Any]] = {}
    raw_grid_hours = set()
    raw_grid_hours_all_invalid = set()
    hours_with_valid = set()
    hours_with_invalid = set()
    unmappable_records = 0
    duplicate_valid_records = 0

    with open(csv_path, 'r', encoding='utf-8') as f:
        header = f.readline().strip().split(',')
        for line_no, line in enumerate(f, start=2):
            line = line.strip()
            if not line:
                continue
            parts = line.split(',')
            try:
                open_ts = int(parts[0])
            except (ValueError, IndexError):
                unmappable_records += 1
                logger.warning("[%s:%d] Unmappable record: cannot parse open_time %r", csv_path, line_no, parts[0] if parts else "")
                continue

            if open_ts % 3600000 != 0:
                unmappable_records += 1
                logger.warning("[%s:%d] Non-hourly open_time %d cannot map to hourly grid", csv_path, line_no, open_ts)
                continue

            raw_grid_hours.add(open_ts)

            try:
                op = float(parts[1])
                hi = float(parts[2])
                lo = float(parts[3])
                cl = float(parts[4])
                vol = float(parts[5])
                cl_ts = int(parts[6])
                is_valid = (
                    math.isfinite(op) and op > 0 and
                    math.isfinite(hi) and hi > 0 and
                    math.isfinite(lo) and lo > 0 and
                    math.isfinite(cl) and cl > 0
                )
            except (ValueError, IndexError):
                is_valid = False

            if is_valid:
                hours_with_valid.add(open_ts)
                if open_ts not in valid_bars_dict:
                    dt = datetime.fromtimestamp(open_ts / 1000.0, tz=timezone.utc)
                    valid_bars_dict[open_ts] = {
                        'open_time': open_ts,
                        'dt': dt,
                        'open': op,
                        'high': hi,
                        'low': lo,
                        'close': cl,
                        'volume': vol,
                        'close_time': cl_ts
                    }
                else:
                    duplicate_valid_records += 1
                    logger.warning("[%s:%d] Duplicate valid bar timestamp %d ignored (keeping first)", csv_path, line_no, open_ts)
            else:
                hours_with_invalid.add(open_ts)
                logger.warning("[%s:%d] Invalid OHLC bar for timestamp %d", csv_path, line_no, open_ts)

    for h in hours_with_invalid:
        if h not in hours_with_valid:
            raw_grid_hours_all_invalid.add(h)

    sorted_bars = [valid_bars_dict[k] for k in sorted(valid_bars_dict.keys())]

    data_quality_stats = {
        'unmappable_records': unmappable_records,
        'duplicate_valid_records': duplicate_valid_records,
        'all_invalid_hours_count': len(raw_grid_hours_all_invalid),
    }

    if return_details:
        return sorted_bars, raw_grid_hours, raw_grid_hours_all_invalid, data_quality_stats
    return sorted_bars


class BacktestEngine:
    def __init__(self, events: List[Tuple[datetime, str, str, str]]):
        self.events = events
        self.reset()

    def reset(self, initial_cash: float = config.INITIAL_CAPITAL_USDT):
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.realized_pnl = 0.0
        self.peak_equity = initial_cash
        self.halt_active = False
        self.halt_state = "NONE" # NONE, HALTING, HALTED
        self.halt_triggered_dt = None
        self.halted_until_dt = None
        self.halt_progress_total = 0
        self.halt_progress_sold = 0
        self.halt_zero_progress_rounds = 0
        self.halt_trigger_equity = None

        self.day_start_equity = initial_cash
        self.current_day_str = None
        self.last_equity = initial_cash
        self.last_equity_dt = None

        self.groups: Dict[str, Optional[Dict[str, Any]]] = {"BTCUSDT": None, "ETHUSDT": None}
        self.symbol_state: Dict[str, Dict[str, Any]] = {
            sym: {
                "last_valid_price": None,
                "last_valid_price_dt": None,
                "last_suspect_price": None,
                "price_error_streak": 0,
                "suspect_price_streak": 0,
                "recovery_streak": 0,
            }
            for sym in ["BTCUSDT", "ETHUSDT"]
        }
        self.stats: Dict[str, Dict[str, int]] = {
            sym: {
                "price_error_rounds": 0,
                "missing_bar_rounds": 0,
                "invalid_ohlc_rounds": 0,
                "suspect_price_rounds": 0,
                "accepted_a_rounds": 0,
                "evaluated_b_rounds": 0,
                "accepted_b_rounds": 0,
                "gaps_over_3_hours": 0,
            }
            for sym in ["BTCUSDT", "ETHUSDT"]
        }
        self.trades: List[Dict[str, Any]] = []
        self.equity_curve: List[Tuple[datetime, float, float]] = [] # dt, equity, peak_equity
        self.pre_trade_max_dd: float = 0.0
        self.post_halt_max_dd: float = 0.0
        self.closed_groups: List[Dict[str, Any]] = []
        self.regime: Dict[str, Optional[str]] = {"BTCUSDT": None, "ETHUSDT": None}
        self.prev_regime: Dict[str, Optional[str]] = {"BTCUSDT": None, "ETHUSDT": None}

    def evaluate_symbol_price(
        self,
        sym: str,
        raw_price: Optional[float],
        current_dt: datetime,
        latest_kline_close: Optional[float],
        price_valid: bool = True
    ) -> Tuple[Optional[float], bool, bool]:
        """
        Execute SPEC v5.5 §4.2 price anomaly state machine for `sym`.
        Returns (valuation_price, block_trading, skip_tp).

        Contract (V5.4-1(d) / Codex P1 / merged_review_v54.md §三 1):
        latest_kline_close has NO default value and must be explicitly provided on each round.
        Only in rounds before the first valid bar in the fallback path is None passed (P-1).
        """
        s_row = self.symbol_state[sym]
        last_valid = s_row["last_valid_price"]
        last_suspect = s_row["last_suspect_price"]
        err_streak = s_row["price_error_streak"]
        sus_streak = s_row["suspect_price_streak"]
        rec_streak = s_row["recovery_streak"]
        in_recovery = (
            err_streak >= config.ERROR_STREAK_THRESHOLD
            or sus_streak >= config.SUSPECT_STREAK_THRESHOLD
        )

        block_trading = False
        skip_tp = False

        if raw_price is None or raw_price <= 0 or not price_valid:
            # Price error branch (§4.2 / V5.4-1(b) / V5.4-2(e))
            err_streak += 1
            if in_recovery:
                rec_streak = 0
            val_px = last_valid
            block_trading = True
            skip_tp = True
            self.stats[sym]["price_error_rounds"] += 1
            # Note: s_row["last_valid_price_dt"] is NOT updated on error round (P3-2)
        else:
            if last_valid is None:
                # V5.4-1(a) fallback branch: first valid bar initializes last_valid_price
                # (P-1 authoritative interpretation: do not count price error, do not enter jump > 10% check)
                last_valid = raw_price
                last_suspect = None
                err_streak = 0
                sus_streak = 0
                rec_streak = 0
                val_px = raw_price
                s_row["last_valid_price_dt"] = current_dt
            else:
                jump = abs(raw_price - last_valid) / last_valid
                if jump <= config.SUSPECT_PRICE_JUMP_PCT:
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
                    s_row["last_valid_price_dt"] = current_dt
                    val_px = raw_price
                    if (
                        err_streak >= config.ERROR_STREAK_THRESHOLD
                        or sus_streak >= config.SUSPECT_STREAK_THRESHOLD
                    ):
                        block_trading = True
                        skip_tp = True
                else:
                    P2 = raw_price
                    P1 = last_suspect

                    accepted_a = False
                    if P1 is not None:
                        same_dir = ((P2 - last_valid) * (P1 - last_valid)) > 0
                        diff_p1 = abs(P2 - P1) / P1
                        if same_dir and diff_p1 <= config.SUSPECT_PRICE_ACCEPT_PCT:
                            accepted_a = True
                            self.stats[sym]["accepted_a_rounds"] += 1

                    accepted_b = False
                    if not accepted_a:
                        self.stats[sym]["evaluated_b_rounds"] += 1
                        if latest_kline_close is not None and latest_kline_close > 0:
                            k_diff = abs(P2 - latest_kline_close) / latest_kline_close
                            if k_diff <= config.SUSPECT_PRICE_KLINE_CLOSE_PCT:
                                accepted_b = True
                                self.stats[sym]["accepted_b_rounds"] += 1

                    if accepted_a:
                        last_valid = P2
                        s_row["last_valid_price_dt"] = current_dt
                        last_suspect = None
                        sus_streak = 0
                        rec_streak = 0
                        val_px = P2
                        if err_streak >= config.ERROR_STREAK_THRESHOLD:
                            block_trading = True
                            skip_tp = True
                    elif accepted_b:
                        old_valid = last_valid
                        last_valid = P2
                        s_row["last_valid_price_dt"] = current_dt
                        last_suspect = None
                        sus_streak = 0
                        rec_streak = 0
                        val_px = old_valid
                        block_trading = True
                        skip_tp = True
                    else:
                        last_suspect = P2
                        sus_streak += 1
                        self.stats[sym]["suspect_price_rounds"] += 1
                        if in_recovery:
                            rec_streak = 0
                        val_px = last_valid
                        block_trading = True
                        skip_tp = True
                        # Note: s_row["last_valid_price_dt"] is NOT updated on unaccepted suspect price (P3-2)

        s_row["last_valid_price"] = last_valid
        s_row["last_suspect_price"] = last_suspect
        s_row["price_error_streak"] = err_streak
        s_row["suspect_price_streak"] = sus_streak
        s_row["recovery_streak"] = rec_streak
        return val_px, block_trading, skip_tp

    def get_deployed_basis(self, symbol: Optional[str] = None) -> float:
        basis = 0.0
        for sym, grp in self.groups.items():
            if symbol and sym != symbol:
                continue
            if grp and grp["status"] == "OPEN":
                for lay in grp["layers"]:
                    if lay["status"] == "OPEN":
                        basis += lay["qty"] * lay["buy_price"]
        return basis

    def get_open_market_value(self, current_prices: Dict[str, Optional[float]]) -> float:
        val = 0.0
        for sym, grp in self.groups.items():
            if grp and grp["status"] == "OPEN":
                px = current_prices.get(sym)
                if px is None:
                    continue
                for lay in grp["layers"]:
                    if lay["status"] == "OPEN":
                        val += lay["qty"] * px
        return val

    def get_equity(self, current_prices: Dict[str, Optional[float]]) -> float:
        return self.cash + self.get_open_market_value(current_prices)

    def assert_cash_invariant(self):
        open_basis = 0.0
        open_costs = 0.0
        for grp in self.groups.values():
            if grp and grp["status"] == "OPEN":
                for lay in grp["layers"]:
                    if lay["status"] == "OPEN":
                        open_basis += lay["qty"] * lay["buy_price"]
                        open_costs += lay["buy_fee"] + lay["buy_slippage"]
        expected_cash = self.initial_cash + self.realized_pnl - open_basis - open_costs
        diff = abs(self.cash - expected_cash)
        if diff > 0.01:
            raise AssertionError(f"Cash invariant violated! cash={self.cash:.4f}, expected={expected_cash:.4f}, diff={diff:.4f}")

    def execute_close_bar(
        self,
        current_dt: datetime,
        current_prices: Dict[str, Optional[float]],
        indicator_data: Dict[str, Dict[str, Any]],
        latest_kline_closes: Optional[Dict[str, Optional[float]]] = None,
        stress_test_mode: bool = False
    ):
        """
        Execute one close-only bar at close price according to §8.1:
        1. §5.0(0) HALTING resume liquidation
        2. Halt expiration recovery (recovery bar skips §5.1 check; defensive check applied)
        3. §5.1 Drawdown halt
        4. Exits (if not stress test): 14d -> group TP (+1.5%) -> 7d half -> layer TP
        5. Buys (if not stress test): open group / add layers
        """
        # Day boundary tracking
        today_str = current_dt.strftime("%Y-%m-%d")
        if self.current_day_str != today_str:
            self.day_start_equity = self.last_equity
            self.current_day_str = today_str

        # §4.2 Price anomaly state machine & single valuation price per bar
        valuation_prices: Dict[str, Optional[float]] = {}
        block_trading: Dict[str, bool] = {"BTCUSDT": False, "ETHUSDT": False}
        skip_tp: Dict[str, bool] = {"BTCUSDT": False, "ETHUSDT": False}

        if not stress_test_mode:
            for sym in ["BTCUSDT", "ETHUSDT"]:
                raw_px = current_prices.get(sym)
                k_close = latest_kline_closes.get(sym) if latest_kline_closes else None
                val_px, blk, skp = self.evaluate_symbol_price(sym, raw_px, current_dt, k_close)
                valuation_prices[sym] = val_px
                block_trading[sym] = blk
                skip_tp[sym] = skp
        else:
            for sym in ["BTCUSDT", "ETHUSDT"]:
                px = current_prices.get(sym)
                valuation_prices[sym] = px
                if px is not None:
                    self.symbol_state[sym]["last_valid_price"] = px
                    self.symbol_state[sym]["last_valid_price_dt"] = current_dt

        current_eq = self.get_equity(valuation_prices)
        peak_for_dd = max(self.peak_equity, current_eq) if not stress_test_mode else self.peak_equity
        pre_dd = (peak_for_dd - current_eq) / peak_for_dd if peak_for_dd > 0 else 0.0
        if pre_dd > self.pre_trade_max_dd:
            self.pre_trade_max_dd = pre_dd

        # 1. HALTING liquidation (§5.0(0))
        if self.halt_state == "HALTING":
            self._execute_halting_liquidation(current_dt, valuation_prices)
            current_eq = self.get_equity(valuation_prices)
            if not stress_test_mode:
                self.peak_equity = max(self.peak_equity, current_eq)
            self._record_equity(current_dt, current_eq)
            return

        # 2. Halt Expiration Recovery (§5.0(a))
        skip_drawdown_check = False
        if self.halt_active and self.halted_until_dt:
            if current_dt >= self.halted_until_dt:
                # Defensive check (§5.0(a) / V5.4-1(c) / merged_review_v54.md §三 4):
                # halt_active == 1 and any symbol last_valid_price is None -> ERROR, do not reset peak, do not clear halt
                if any(self.symbol_state[s]["last_valid_price"] is None for s in ["BTCUSDT", "ETHUSDT"]):
                    logger.error("Halt expiration recovery blocked: defensive check failed (halt_active=1 and symbol last_valid_price is None: %s)",
                                 {s: self.symbol_state[s]["last_valid_price"] for s in ["BTCUSDT", "ETHUSDT"]})
                    skip_drawdown_check = True
                else:
                    self.peak_equity = current_eq
                    self.halt_active = False
                    self.halt_state = "NONE"
                    self.halted_until_dt = None
                    skip_drawdown_check = True

        # 3. Drawdown Halt Check (§5.1)
        if not self.halt_active and self.halt_state == "NONE" and not skip_drawdown_check:
            peak_for_dd = max(self.peak_equity, current_eq) if not stress_test_mode else self.peak_equity
            drawdown = (peak_for_dd - current_eq) / peak_for_dd if peak_for_dd > 0 else 0.0
            if drawdown >= config.DRAWDOWN_HALT_PCT:
                # Trigger halt
                all_open_layers = self._get_all_open_layers()
                if not all_open_layers:
                    self.halt_state = "HALTED"
                    self.halt_active = True
                    self.halt_triggered_dt = current_dt
                    self.halted_until_dt = current_dt + timedelta(hours=config.HALT_COOLDOWN_HOURS)
                    self.halt_trigger_equity = current_eq
                    self.halt_zero_progress_rounds = 0
                else:
                    self.halt_state = "HALTING"
                    self.halt_triggered_dt = current_dt
                    self.halt_progress_total = len(all_open_layers)
                    self.halt_progress_sold = 0
                    self.halt_zero_progress_rounds = 0
                    self._execute_halting_liquidation(current_dt, valuation_prices)

                current_eq = self.get_equity(valuation_prices)
                post_halt_dd = (peak_for_dd - current_eq) / peak_for_dd if peak_for_dd > 0 else 0.0
                if post_halt_dd > self.post_halt_max_dd:
                    self.post_halt_max_dd = post_halt_dd
                if not stress_test_mode:
                    self.peak_equity = max(self.peak_equity, current_eq)
                self._record_equity(current_dt, current_eq)
                return

        # (b2) Market Trend Regime Determination (§4.3, §5.0(b2))
        if not stress_test_mode:
            for sym in ["BTCUSDT", "ETHUSDT"]:
                inds = indicator_data.get(sym, {})
                adx = inds.get("adx")
                p_di = inds.get("plus_di")
                m_di = inds.get("minus_di")
                r24 = inds.get("ret24")
                if adx is not None and p_di is not None and m_di is not None and r24 is not None:
                    self.regime[sym] = engine.classify_regime(adx, p_di, m_di, r24, self.prev_regime.get(sym))
                    self.prev_regime[sym] = self.regime[sym]
                else:
                    self.regime[sym] = None
                    block_trading[sym] = True

        # 4. In-group Exits (Non-stress mode only)
        if not stress_test_mode and not self.halt_active and self.halt_state == "NONE":
            for sym in ["BTCUSDT", "ETHUSDT"]:
                grp = self.groups[sym]
                val_px = valuation_prices.get(sym)
                if grp and grp["status"] == "OPEN" and val_px is not None:
                    self._process_group_exits(grp, val_px, current_dt, skip_tp=skip_tp[sym])

        # 5. Buying (Non-stress mode only)
        if not stress_test_mode and not self.halt_active and self.halt_state == "NONE":
            # Check daily loss limit
            daily_loss = current_eq - self.day_start_equity
            daily_loss_blocked = (daily_loss < config.DAILY_LOSS_LIMIT_USDT)

            if not daily_loss_blocked:
                # Check events filter
                ev_24h, ev_past_2h, is_stale, ev_err = engine.check_event_filter(self.events, current_dt, is_simulation=True)
                events_ok = (not ev_24h and not ev_past_2h and not is_stale)

                for sym in ["BTCUSDT", "ETHUSDT"]:
                    grp = self.groups[sym]
                    val_px = valuation_prices.get(sym)
                    if val_px is None or block_trading[sym]:
                        continue
                    inds = indicator_data.get(sym, {})

                    if grp and grp["status"] == "OPEN":
                        self._process_group_adds(grp, val_px, current_dt)
                    elif grp is None and events_ok:
                        self._consider_open_group(sym, val_px, current_dt, inds)

        current_eq = self.get_equity(valuation_prices)
        if not stress_test_mode:
            self.peak_equity = max(self.peak_equity, current_eq)
        self._record_equity(current_dt, current_eq)

    def _record_equity(self, dt: datetime, eq: float):
        self.last_equity = eq
        self.last_equity_dt = dt
        self.equity_curve.append((dt, eq, self.peak_equity))
        self.assert_cash_invariant()

    def _get_all_open_layers(self) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        """Return list of (symbol, group, layer) sorted by symbol asc, layer_no asc."""
        res = []
        for sym in ["BTCUSDT", "ETHUSDT"]:
            grp = self.groups[sym]
            if grp and grp["status"] == "OPEN":
                for lay in grp["layers"]:
                    if lay["status"] == "OPEN":
                        res.append((sym, grp, lay))
        res.sort(key=lambda x: (x[0], x[2]["layer_no"]))
        return res

    def _execute_halting_liquidation(self, current_dt: datetime, current_prices: Dict[str, Optional[float]]):
        open_items = self._get_all_open_layers()
        sold_count = 0
        for sym, grp, lay in open_items:
            px = current_prices.get(sym)
            if px is None:
                px = lay["buy_price"]
            self._sell_layer(grp, lay, sym, px, current_dt, "DRAWDOWN_HALT", "HALT_CLOSED")
            self.halt_progress_sold += 1
            sold_count += 1
            # Check if group has no more OPEN layers
            if not any(l["status"] == "OPEN" for l in grp["layers"]):
                grp["status"] = "CLOSED"
                grp["close_reason"] = "DRAWDOWN_HALT"
                grp["closed_dt"] = current_dt
                for play in grp["layers"]:
                    if play["status"] == "PENDING":
                        play["status"] = "CANCELLED"
                self.closed_groups.append(grp)
                self.groups[sym] = None

        if sold_count == 0 and len(open_items) > 0:
            self.halt_zero_progress_rounds += 1
        elif sold_count > 0:
            self.halt_zero_progress_rounds = 0

        # Check if all OPEN layers are sold
        if not self._get_all_open_layers():
            self.halt_state = "HALTED"
            self.halt_active = True
            self.halted_until_dt = (self.halt_triggered_dt or current_dt) + timedelta(hours=config.HALT_COOLDOWN_HOURS)
            self.halt_zero_progress_rounds = 0
            self.halt_trigger_equity = self.get_equity(current_prices)

    def _sell_layer(
        self, grp: Dict[str, Any], layer: Dict[str, Any], symbol: str,
        sell_px: float, dt: datetime, reason: str, layer_status: str
    ):
        qty = layer["qty"]
        gross = round(qty * sell_px, 4)
        fee = round(gross * config.FEE_RATE, 4)
        slippage = round(gross * config.SLIPPAGE_RATE, 4)
        net_in = gross - fee - slippage

        realized = round((sell_px - layer["buy_price"]) * qty - layer["buy_fee"] - layer["buy_slippage"] - fee - slippage, 4)

        layer["status"] = layer_status
        layer["sold_dt"] = dt
        layer["sell_price"] = sell_px

        self.cash += net_in
        self.realized_pnl += realized

        self.trades.append({
            'dt': dt,
            'symbol': symbol,
            'side': 'SELL',
            'price': sell_px,
            'qty': qty,
            'notional': gross,
            'fee': fee,
            'slippage': slippage,
            'group_id': grp["id"],
            'layer_no': layer["layer_no"],
            'reason': reason,
            'realized_pnl': realized
        })

    def _process_group_exits(self, grp: Dict[str, Any], px: float, current_dt: datetime, skip_tp: bool = False):
        sym = grp["symbol"]
        first_buy_dt = grp["first_buy_dt"]
        open_layers = [l for l in grp["layers"] if l["status"] == "OPEN"]
        if not open_layers:
            return

        # HOLDMAX variant (§5.3): fully-filled (9/9 bought) groups skip time stops and hold for recovery.
        _bought = sum(1 for l in grp["layers"] if l["status"] in ("OPEN", "SOLD", "TIME_STOPPED"))
        fully_filled = getattr(config, "HOLDMAX_EXEMPT_TIME_STOP", True) and (_bought >= config.MAX_LAYERS)

        # Priority 2: 14 Days Time Stop (§5.0(c) / §5.3)
        if (not fully_filled) and current_dt - first_buy_dt >= timedelta(days=config.TIME_STOP_14D_DAYS):
            for lay in open_layers:
                self._sell_layer(grp, lay, sym, px, current_dt, "TIME_STOP_14D", "TIME_STOPPED")
            grp["status"] = "CLOSED"
            grp["close_reason"] = "TIME_STOP_14D"
            grp["closed_dt"] = current_dt
            for play in grp["layers"]:
                if play["status"] == "PENDING":
                    play["status"] = "CANCELLED"
            self.closed_groups.append(grp)
            self.groups[sym] = None
            return

        # Priority 3: Group TP (+1.5%, or +0.8% in REBOUND)
        if not skip_tp:
            total_qty = sum(l["qty"] for l in open_layers)
            total_cost = sum(l["qty"] * l["buy_price"] for l in open_layers)
            avg_cost = total_cost / total_qty if total_qty > 0 else 0.0
            tp_coeff = config.REBOUND_TP_COEFF if self.regime.get(sym) == "REBOUND" else (1.0 + config.GROUP_TP_PCT)
            if px >= avg_cost * tp_coeff:
                for lay in open_layers:
                    self._sell_layer(grp, lay, sym, px, current_dt, "GROUP_TP", "SOLD")
                grp["status"] = "CLOSED"
                grp["close_reason"] = "TAKE_PROFIT"
                grp["closed_dt"] = current_dt
                for play in grp["layers"]:
                    if play["status"] == "PENDING":
                        play["status"] = "CANCELLED"
                self.closed_groups.append(grp)
                self.groups[sym] = None
                return

        # Priority 4: 7 Days Time Stop (Half) (§5.0(c) / §5.3)
        if (not fully_filled) and grp.get("half_time_stopped", 0) == 0 and (current_dt - first_buy_dt >= timedelta(days=config.TIME_STOP_7D_DAYS)):
            open_sorted_desc = sorted(open_layers, key=lambda l: l["layer_no"], reverse=True)
            total_open_actual = sum(l["notional"] for l in open_layers)
            target_cut = total_open_actual * 0.50
            cum_cut = 0.0
            layers_to_sell = []
            if len(open_layers) == 1:
                layers_to_sell = open_layers
            else:
                for lay in open_sorted_desc:
                    layers_to_sell.append(lay)
                    cum_cut += lay["notional"]
                    if cum_cut >= target_cut:
                        break

            for lay in layers_to_sell:
                self._sell_layer(grp, lay, sym, px, current_dt, "TIME_STOP_7D", "TIME_STOPPED")
            grp["half_time_stopped"] = 1
            grp["stop_adding"] = 1
            for play in grp["layers"]:
                if play["status"] == "PENDING":
                    play["status"] = "CANCELLED"
            if not any(l["status"] == "OPEN" for l in grp["layers"]):
                grp["status"] = "CLOSED"
                grp["close_reason"] = "TIME_STOP_7D"
                grp["closed_dt"] = current_dt
                self.closed_groups.append(grp)
                self.groups[sym] = None
            return

        # Priority 5: Single Layer TP
        if not skip_tp:
            c = grp["center_price"]
            d = grp["spacing_pct"]
            qualified_layers = []
            for lay in open_layers:
                tp_px = engine.compute_tp_price(c, d, lay["layer_no"])
                if px >= tp_px:
                    qualified_layers.append(lay)

            if qualified_layers:
                for lay in qualified_layers:
                    self._sell_layer(grp, lay, sym, px, current_dt, "LAYER_TP", "SOLD")
                if not any(l["status"] == "OPEN" for l in grp["layers"]):
                    grp["status"] = "CLOSED"
                    grp["close_reason"] = "ALL_SOLD"
                    grp["closed_dt"] = current_dt
                    for play in grp["layers"]:
                        if play["status"] == "PENDING":
                            play["status"] = "CANCELLED"
                    self.closed_groups.append(grp)
                    self.groups[sym] = None

    def _process_group_adds(self, grp: Dict[str, Any], px: float, current_dt: datetime):
        sym = grp["symbol"]
        c = grp["center_price"]

        # Stop adding condition: px < 0.80 * C (§4.2, v5.7)
        if px < c * config.STOP_ADDING_DROP_PCT:
            grp["stop_adding"] = 1
            for play in grp["layers"]:
                if play["status"] == "PENDING":
                    play["status"] = "CANCELLED"
            return

        if grp.get("stop_adding", 0) == 1:
            return

        current_regime = self.regime.get(sym)
        if current_regime == "STRONG_DOWN":
            return

        if current_regime == "SLOW_DOWN":
            bought_dts = [l["bought_dt"] for l in grp["layers"] if l["layer_no"] >= 2 and l.get("bought_dt") is not None]
            if bought_dts:
                last_dt = max(bought_dts)
                if (current_dt - last_dt).total_seconds() < config.SLOW_DOWN_COOLDOWN_SECS:
                    return

        dd = (c - px) / c
        mult, permitted = engine.get_dynamic_multiplier(dd, current_regime)
        if not permitted or mult is None:
            return

        pending_layers = [l for l in grp["layers"] if l["status"] == "PENDING"]
        for play in pending_layers:
            lno = play["layer_no"]
            bought_prior = sum(1 for l in grp["layers"] if l["layer_no"] < lno and l["status"] in ("OPEN", "SOLD", "TIME_STOPPED"))
            if bought_prior != (lno - 1):
                break

            trig = play["trigger_price"]
            if px < trig:
                prev_lay = next((l for l in grp["layers"] if l["layer_no"] == lno - 1), None)
                if prev_lay is None or prev_lay.get("planned_notional") is None:
                    logger.error("Group %s layer %d prev planned_notional is None (NULL fallback)", grp.get("id"), lno)
                    grp["stop_adding"] = 1
                    for rem_lay in grp["layers"]:
                        if rem_lay["status"] == "PENDING":
                            rem_lay["status"] = "CANCELLED"
                    break

                prev_planned = prev_lay["planned_notional"]
                planned_notional = round(prev_planned * mult, 2)

                tot_deployed = self.get_deployed_basis()
                sym_deployed = self.get_deployed_basis(sym)
                sym_cap = config.BTC_CAP_USDT if sym == "BTCUSDT" else config.ETH_CAP_USDT
                tot_rem = max(0.0, config.TOTAL_DEPLOYED_CAP_USDT - tot_deployed)
                sym_rem = max(0.0, sym_cap - sym_deployed)
                trunc_planned = min(planned_notional, tot_rem, sym_rem)

                qty, actual_notional = engine.compute_order_qty(sym, trunc_planned, px)
                if trunc_planned < config.MIN_NOTIONAL_USDT or actual_notional < config.MIN_NOTIONAL_USDT or qty <= 0:
                    play["status"] = "CANCELLED"
                    grp["stop_adding"] = 1
                    for rem_lay in grp["layers"]:
                        if rem_lay["status"] == "PENDING":
                            rem_lay["status"] = "CANCELLED"
                    break

                fee = round(actual_notional * config.FEE_RATE, 4)
                slippage = round(actual_notional * config.SLIPPAGE_RATE, 4)
                total_cost = actual_notional + fee + slippage

                play["status"] = "OPEN"
                play["buy_price"] = px
                play["qty"] = qty
                play["notional"] = actual_notional
                play["planned_notional"] = planned_notional
                play["buy_fee"] = fee
                play["buy_slippage"] = slippage
                play["bought_dt"] = current_dt

                self.cash -= total_cost
                self.trades.append({
                    'dt': current_dt,
                    'symbol': sym,
                    'side': 'BUY',
                    'price': px,
                    'qty': qty,
                    'notional': actual_notional,
                    'fee': fee,
                    'slippage': slippage,
                    'group_id': grp["id"],
                    'layer_no': lno,
                    'reason': 'LAYER_BUY'
                })

                if current_regime == "SLOW_DOWN":
                    break
            else:
                break

    def _consider_open_group(self, symbol: str, px: float, current_dt: datetime, inds: Dict[str, Any]):
        adx = inds.get("adx")
        atr = inds.get("atr")
        atr_med = inds.get("atr_median_480")
        hh = inds.get("hh_480")
        ll = inds.get("ll_480")
        prev_c = inds.get("prev_close")
        valid_count = inds.get("valid_bars_count", config.MIN_KLINES_BARS)

        # SPEC §3.4 / §7.1 / merged_review_v54.md §三 3: at least 500 valid closed bars required
        if valid_count < config.MIN_KLINES_BARS:
            return

        cond1 = (adx is not None and adx < config.ADX_THRESHOLD)
        cond2 = (hh is not None and ll is not None and prev_c is not None and ll < prev_c < hh)
        cond3 = (atr is not None and atr_med is not None and atr < config.ATR_MEDIAN_MULTIPLIER * atr_med)

        if not (cond1 and cond2 and cond3):
            return

        planned_l1 = config.BTC_LAYER_NOTIONALS[0] if symbol == "BTCUSDT" else config.ETH_LAYER_NOTIONALS[0]
        qty, actual_l1 = engine.compute_order_qty(symbol, planned_l1, px)
        if actual_l1 < config.MIN_NOTIONAL_USDT:
            return

        tot_dep = self.get_deployed_basis()
        sym_dep = self.get_deployed_basis(symbol)
        sym_cap = config.BTC_CAP_USDT if symbol == "BTCUSDT" else config.ETH_CAP_USDT

        if (tot_dep + actual_l1 > config.TOTAL_DEPLOYED_CAP_USDT) or (sym_dep + actual_l1 > sym_cap):
            return

        d = engine.compute_grid_spacing(symbol, atr, px)
        fee = round(actual_l1 * config.FEE_RATE, 4)
        slippage = round(actual_l1 * config.SLIPPAGE_RATE, 4)
        cash_out = actual_l1 + fee + slippage

        gid = len(self.closed_groups) + sum(1 for g in self.groups.values() if g is not None) + 1
        new_layers = []
        new_layers.append({
            'layer_no': 1,
            'trigger_price': px,
            'buy_price': px,
            'qty': qty,
            'notional': actual_l1,
            'planned_notional': planned_l1,
            'buy_fee': fee,
            'buy_slippage': slippage,
            'status': 'OPEN',
            'bought_dt': current_dt
        })
        for lno in range(2, config.MAX_LAYERS + 1):
            trig = engine.compute_trigger_price(px, d, lno)
            new_layers.append({
                'layer_no': lno,
                'trigger_price': trig,
                'status': 'PENDING'
            })

        grp = {
            'id': gid,
            'symbol': symbol,
            'center_price': px,
            'spacing_pct': d,
            'status': 'OPEN',
            'opened_dt': current_dt,
            'first_buy_dt': current_dt,
            'stop_adding': 0,
            'half_time_stopped': 0,
            'layers': new_layers
        }
        self.groups[symbol] = grp
        self.cash -= cash_out

        self.trades.append({
            'dt': current_dt,
            'symbol': symbol,
            'side': 'BUY',
            'price': px,
            'qty': qty,
            'notional': actual_l1,
            'fee': fee,
            'slippage': slippage,
            'group_id': gid,
            'layer_no': 1,
            'reason': 'GROUP_OPEN'
        })

    def get_metrics(self) -> Dict[str, Any]:
        """Compute performance metrics according to §8.3."""
        if not self.equity_curve:
            return {'total_pnl': 0.0, 'max_drawdown': 0.0, 'profit_factor': 0.0, 'win_month_ratio': 0.0, 'stats': self.stats}

        end_eq = self.equity_curve[-1][1]
        total_pnl = end_eq - self.initial_cash

        # Drawdown computed against tracked peak_equity at each step
        max_dd = 0.0
        for dt, eq, stored_peak in self.equity_curve:
            dd = (stored_peak - eq) / stored_peak if stored_peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        # Profit factor: SELL trades only (§8.3.3)
        pos_pnl = 0.0
        neg_pnl = 0.0
        sell_trades = [t for t in self.trades if t['side'] == 'SELL']
        for t in sell_trades:
            rp = t.get('realized_pnl', 0.0)
            if rp > 0:
                pos_pnl += rp
            elif rp < 0:
                neg_pnl += abs(rp)

        if not sell_trades:
            profit_factor = 0.0
        elif neg_pnl == 0.0 and pos_pnl > 0:
            profit_factor = float('inf')
        elif neg_pnl == 0.0 and pos_pnl == 0.0:
            profit_factor = 0.0
        else:
            profit_factor = pos_pnl / neg_pnl

        # Win month ratio (§8.3.4)
        monthly_pnl: Dict[str, float] = {}
        for t in sell_trades:
            m_key = t['dt'].strftime("%Y-%m")
            monthly_pnl[m_key] = monthly_pnl.get(m_key, 0.0) + t.get('realized_pnl', 0.0)

        total_months = len(monthly_pnl)
        win_months = sum(1 for p in monthly_pnl.values() if p > 0.0)
        win_month_ratio = (win_months / total_months) if total_months > 0 else 0.0

        return {
            'total_pnl': round(total_pnl, 2),
            'max_drawdown_pct': round(max_dd * 100, 2),
            'close_sample_max_dd_pct': round(self.pre_trade_max_dd * 100, 2),
            'post_halt_max_dd_pct': round(self.post_halt_max_dd * 100, 2),
            'max_drawdown_raw': max_dd,
            'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else float('inf'),
            'win_month_ratio_pct': round(win_month_ratio * 100, 2),
            'win_month_ratio_raw': win_month_ratio,
            'total_trades': len(self.trades),
            'sell_trades': len(sell_trades),
            'active_months': total_months,
            'win_months': win_months,
            'end_equity': round(end_eq, 2),
            'stats': self.stats
        }


def precompute_series_indicators(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Precompute indicators for the valid bar series in a single O(N) pass.
    Valid bar count threshold (< 500) enforced: indicators return None when bars < 500.
    """
    n = len(bars)
    if n == 0:
        return []

    highs = [b['high'] for b in bars]
    lows = [b['low'] for b in bars]
    closes = [b['close'] for b in bars]

    # 1. TR
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    # 2. ATR(14) SMA
    atrs = [None] * n
    window = 14
    if n >= window:
        cur_sum = sum(trs[:window])
        atrs[window - 1] = cur_sum / window
        for i in range(window, n):
            cur_sum += trs[i] - trs[i - window]
            atrs[i] = cur_sum / window

    # 3. Wilder's ADX(14)
    plus_dms = [0.0] * n
    minus_dms = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        if up > dn and up > 0:
            plus_dms[i] = up
        if dn > up and dn > 0:
            minus_dms[i] = dn

    adx_arr = [None] * n
    plus_di_arr = [None] * n
    minus_di_arr = [None] * n
    if n >= 28:
        smooth_tr = [None] * n
        smooth_pdm = [None] * n
        smooth_mdm = [None] * n
        smooth_tr[14] = sum(trs[1:15])
        smooth_pdm[14] = sum(plus_dms[1:15])
        smooth_mdm[14] = sum(minus_dms[1:15])
        for i in range(15, n):
            smooth_tr[i] = smooth_tr[i - 1] - (smooth_tr[i - 1] / 14.0) + trs[i]
            smooth_pdm[i] = smooth_pdm[i - 1] - (smooth_pdm[i - 1] / 14.0) + plus_dms[i]
            smooth_mdm[i] = smooth_mdm[i - 1] - (smooth_mdm[i - 1] / 14.0) + minus_dms[i]

        dx = [None] * n
        for i in range(14, n):
            tr_val = smooth_tr[i]
            if tr_val == 0:
                plus_di_arr[i] = 0.0
                minus_di_arr[i] = 0.0
                dx[i] = 0.0
                continue
            p_di = 100.0 * (smooth_pdm[i] / tr_val)
            m_di = 100.0 * (smooth_mdm[i] / tr_val)
            plus_di_arr[i] = p_di
            minus_di_arr[i] = m_di
            s = p_di + m_di
            dx[i] = 100.0 * abs(p_di - m_di) / s if s != 0 else 0.0

        adx_arr[27] = sum(dx[14:28]) / 14.0
        for i in range(28, n):
            adx_arr[i] = ((adx_arr[i - 1] * 13.0) + dx[i]) / 14.0

    # 4. 480-bar window HH_480, LL_480, ATR_median_480
    res = [None] * n
    W = config.RANGE_WINDOW_BARS # 480
    for i in range(n):
        ret24 = (closes[i] / closes[i - 24] - 1.0) if i >= 24 else None
        if i < config.MIN_KLINES_BARS - 1: # Requires at least 500 valid closed bars (§3.4, §7.1)
            res[i] = {
                'adx': None,
                'plus_di': None,
                'minus_di': None,
                'ret24': None,
                'atr': None,
                'atr_pct': None,
                'hh_480': None,
                'll_480': None,
                'atr_median_480': None,
                'prev_close': closes[i],
                'valid_bars_count': i + 1
            }
            continue

        w_highs = highs[i - W + 1:i + 1]
        w_lows = lows[i - W + 1:i + 1]
        w_atrs = atrs[i - W + 1:i + 1]

        hh = max(w_highs)
        ll = min(w_lows)
        sorted_atrs = sorted(w_atrs)
        mid = len(sorted_atrs) // 2
        atr_med = (sorted_atrs[mid - 1] + sorted_atrs[mid]) / 2.0

        res[i] = {
            'adx': adx_arr[i],
            'plus_di': plus_di_arr[i],
            'minus_di': minus_di_arr[i],
            'ret24': ret24,
            'atr': atrs[i],
            'atr_pct': atrs[i] / closes[i] if closes[i] else 0.0,
            'hh_480': hh,
            'll_480': ll,
            'atr_median_480': atr_med,
            'prev_close': closes[i],
            'valid_bars_count': i + 1
        }
    return res


def run_strategy_simulation(
    btc_bars: List[Dict[str, Any]],
    eth_bars: List[Dict[str, Any]],
    events: List[Tuple[datetime, str, str, str]],
    start_dt: datetime,
    end_dt: datetime,
    raw_grid_hours_btc: Optional[set] = None,
    raw_grid_hours_eth: Optional[set] = None,
    all_invalid_hours_btc: Optional[set] = None,
    all_invalid_hours_eth: Optional[set] = None,
) -> Tuple[BacktestEngine, Dict[str, Any]]:
    """
    Run full strategy simulation over specified date range on the hourly grid (§8.1 / V5.4-2(b)).
    Implements:
    - V5.4-1(a) / V5.5-1: Preheat initialization with 24-hour freshness limit; fallback on expiration.
    - V5.4-2(b): Hourly grid [start_dt, end_dt] with step 3600s.
    - V5.4-2(e): Price error vs suspect price categorization; missing bar vs invalid OHLC breakdown.
    - V5.4-2(d): K-line gap > 3 hours counted and logged as ERROR.
    - V5.4-1(d) / Codex P1 / P-1: Explicit latest_kline_close passed on every round.
    """
    # Precompute indicators for both valid bar series
    btc_inds = precompute_series_indicators(btc_bars)
    eth_inds = precompute_series_indicators(eth_bars)

    btc_map = {btc_bars[i]['open_time']: (btc_bars[i], btc_inds[i]) for i in range(len(btc_bars))}
    eth_map = {eth_bars[i]['open_time']: (eth_bars[i], eth_inds[i]) for i in range(len(eth_bars))}

    engine_bt = BacktestEngine(events)

    # Preheat initialization (V5.4-1(a) / V5.5-1 / V5.5-4)
    last_closed_valid_close: Dict[str, Optional[float]] = {}
    for sym, bars in [("BTCUSDT", btc_bars), ("ETHUSDT", eth_bars)]:
        preheat_bars = [b for b in bars if b['dt'] < start_dt]
        if preheat_bars:
            last_preheat = preheat_bars[-1]
            diff_hours = (start_dt - last_preheat['dt']).total_seconds() / 3600.0
            if diff_hours <= 24.0:
                engine_bt.symbol_state[sym]["last_valid_price"] = last_preheat['close']
                engine_bt.symbol_state[sym]["last_valid_price_dt"] = last_preheat['dt']
                engine_bt.symbol_state[sym]["last_suspect_price"] = None
                engine_bt.symbol_state[sym]["price_error_streak"] = 0
                engine_bt.symbol_state[sym]["suspect_price_streak"] = 0
                engine_bt.symbol_state[sym]["recovery_streak"] = 0
                last_closed_valid_close[sym] = last_preheat['close']
            else:
                logger.warning("Preheat bar for %s is older than 24 hours (%.1fh > 24h). Falling back to first valid bar.", sym, diff_hours)
                engine_bt.symbol_state[sym]["last_valid_price"] = None
                last_closed_valid_close[sym] = None
        else:
            engine_bt.symbol_state[sym]["last_valid_price"] = None
            last_closed_valid_close[sym] = None

    # Step through hourly grid [start_dt, end_dt] (V5.4-2(b))
    cur_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)
    consecutive_missing = {"BTCUSDT": 0, "ETHUSDT": 0}

    while cur_ts <= end_ts:
        cur_dt = datetime.fromtimestamp(cur_ts / 1000.0, tz=timezone.utc)
        cur_prices: Dict[str, Optional[float]] = {}
        indicator_data: Dict[str, Dict[str, Any]] = {}
        latest_kline_closes: Dict[str, Optional[float]] = {}

        for sym, v_map, all_inv in [
            ("BTCUSDT", btc_map, all_invalid_hours_btc or set()),
            ("ETHUSDT", eth_map, all_invalid_hours_eth or set())
        ]:
            item = v_map.get(cur_ts)
            if item is not None:
                bar, ind = item
                cur_prices[sym] = bar['close']
                indicator_data[sym] = ind
                if consecutive_missing[sym] > 3:
                    engine_bt.stats[sym]["gaps_over_3_hours"] += 1
                    logger.error("K-line gap over 3 hours for %s: %d consecutive missing hours ended at %s",
                                 sym, consecutive_missing[sym], cur_dt.isoformat())
                consecutive_missing[sym] = 0
            else:
                cur_prices[sym] = None
                indicator_data[sym] = {}
                consecutive_missing[sym] += 1
                if cur_ts in all_inv:
                    engine_bt.stats[sym]["invalid_ohlc_rounds"] += 1
                else:
                    engine_bt.stats[sym]["missing_bar_rounds"] += 1

            # Contract for latest_kline_close (V5.4-1(d) / P-1 / V5.5-4):
            # In rounds before first valid bar, pass None explicitly (P-1);
            # Otherwise pass most recently closed valid bar close.
            if engine_bt.symbol_state[sym]["last_valid_price"] is None and cur_prices[sym] is None:
                latest_kline_closes[sym] = None
            else:
                latest_kline_closes[sym] = last_closed_valid_close.get(sym)

        engine_bt.execute_close_bar(cur_dt, cur_prices, indicator_data, latest_kline_closes, stress_test_mode=False)

        # Update last closed valid bar close on rounds where bar was present
        for sym in ["BTCUSDT", "ETHUSDT"]:
            if cur_prices[sym] is not None:
                last_closed_valid_close[sym] = cur_prices[sym]

        cur_ts += 3600000

    # Check trailing gaps at end of window (V5.4-2(d))
    for sym in ["BTCUSDT", "ETHUSDT"]:
        if consecutive_missing[sym] > 3:
            engine_bt.stats[sym]["gaps_over_3_hours"] += 1
            logger.error("Truncated K-line gap over 3 hours for %s: %d consecutive missing hours at end of window %s",
                         sym, consecutive_missing[sym], end_dt.isoformat())

    metrics = engine_bt.get_metrics()
    return engine_bt, metrics


# ---------------------------------------------------------
# Stress Tests 1 to 4 (§8.2)
# ---------------------------------------------------------

def run_stress_test_1(events):
    """Stress Test 1: Synthetic +50% (720 steps)."""
    return _run_synthetic_holding_test(events, total_change=0.50, steps=720, name="Synthetic +50%")

def run_stress_test_2(events):
    """
    Stress Test 2: Synthetic -50% (720 steps).
    Includes sub-cases:
    (a) BTC alone (cash=6996.40)
    (b) ETH alone (cash=7997.60)
    (c) BTC+ETH dual (cash=4994.00), halt triggers ~hour 428, recovery at +24h.
    """
    res_c = _run_synthetic_holding_test(events, total_change=-0.50, steps=720, name="Synthetic -50% (Dual)", mode='c')
    res_a = _run_synthetic_holding_test(events, total_change=-0.50, steps=720, name="Synthetic -50% (BTC Alone)", mode='a')
    res_b = _run_synthetic_holding_test(events, total_change=-0.50, steps=720, name="Synthetic -50% (ETH Alone)", mode='b')
    return {'dual': res_c, 'btc_alone': res_a, 'eth_alone': res_b}

def run_stress_test_3(events):
    """Stress Test 3: Down -30% (360 steps) then Up +20% (360 steps)."""
    return _run_synthetic_split_test(events, change1=-0.30, steps1=360, change2=0.20, steps2=360)

def run_stress_test_4(btc_bars, eth_bars, events, raw_btc=None, raw_eth=None, inv_btc=None, inv_eth=None):
    """Stress Test 4: Real slice 2022-06-01 to 2022-06-30 normal strategy."""
    start_dt = datetime(2022, 6, 1, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2022, 6, 30, 23, 0, tzinfo=timezone.utc)
    bt_eng, metrics = run_strategy_simulation(
        btc_bars, eth_bars, events, start_dt, end_dt,
        raw_grid_hours_btc=raw_btc, raw_grid_hours_eth=raw_eth,
        all_invalid_hours_btc=inv_btc, all_invalid_hours_eth=inv_eth
    )
    return {'engine': bt_eng, 'metrics': metrics}

def _run_synthetic_holding_test(
    events, total_change: float, steps: int = 720, name: str = "", mode: str = 'c'
) -> Dict[str, Any]:
    """
    Deterministic holding stress test (§8.2).
    Start state:
    Hour 0: All 9 layers bought at trigger prices.
    BTC d=2.0%: L1 C, L2 0.98C, L3 0.96C, L4 0.94C, L5 0.92C, L6 0.90C, L7 0.88C, L8 0.86C, L9 0.84C
    ETH d=2.5%: L1 C, L2 0.975C, L3 0.95C, L4 0.925C, L5 0.90C, L6 0.875C, L7 0.85C, L8 0.825C, L9 0.80C
    """
    start_dt = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    eng = BacktestEngine(events)

    if mode == 'c':
        eng.cash = 4994.00
    elif mode == 'a':
        eng.cash = 6996.40
    elif mode == 'b':
        eng.cash = 7997.60

    eng.initial_cash = 10000.00
    eng.peak_equity = 10000.00 # Initial peak equity baseline before buy

    btc_c = 50000.0
    eth_c = 3000.0
    btc_d = config.BTC_SPACING_COEFF / 100.0
    eth_d = config.ETH_SPACING_COEFF / 100.0
    symbols_to_init = []
    if mode in ('c', 'a'):
        symbols_to_init.append(('BTCUSDT', btc_c, btc_d, config.BTC_LAYER_NOTIONALS, config.MIN_QTY_BTC))
    if mode in ('c', 'b'):
        symbols_to_init.append(('ETHUSDT', eth_c, eth_d, config.ETH_LAYER_NOTIONALS, config.MIN_QTY_ETH))

    for sym, center_px, d, notionals, step in symbols_to_init:
        layers = []
        for lno in range(1, config.MAX_LAYERS + 1):
            trig = center_px * (1.0 - (lno - 1) * d)
            plan_notional = notionals[lno - 1]
            qty = plan_notional / trig
            actual_notional = plan_notional
            fee = round(actual_notional * config.FEE_RATE, 4)
            slip = round(actual_notional * config.SLIPPAGE_RATE, 4)

            layers.append({
                'layer_no': lno,
                'trigger_price': trig,
                'buy_price': trig,
                'qty': qty,
                'notional': actual_notional,
                'planned_notional': plan_notional,
                'buy_fee': fee,
                'buy_slippage': slip,
                'status': 'OPEN',
                'bought_dt': start_dt
            })
        grp = {
            'id': 1 if sym == 'BTCUSDT' else 2,
            'symbol': sym,
            'center_price': center_px,
            'spacing_pct': d,
            'status': 'OPEN',
            'opened_dt': start_dt,
            'first_buy_dt': start_dt,
            'stop_adding': 1, # Already 9 layers
            'half_time_stopped': 0,
            'layers': layers
        }
        eng.groups[sym] = grp
        eng.symbol_state[sym]["last_valid_price"] = center_px
        eng.symbol_state[sym]["last_valid_price_dt"] = start_dt

    # If single symbol mode, initialize the other symbol's price to center_px as well
    if mode == 'a':
        eng.symbol_state['ETHUSDT']["last_valid_price"] = eth_c
        eng.symbol_state['ETHUSDT']["last_valid_price_dt"] = start_dt
    elif mode == 'b':
        eng.symbol_state['BTCUSDT']["last_valid_price"] = btc_c
        eng.symbol_state['BTCUSDT']["last_valid_price_dt"] = start_dt

    r = (1.0 + total_change) ** (1.0 / steps)
    halt_hour = None
    halt_price_ratio = None
    recovery_hour = None
    recovery_verified = False

    for h in range(1, steps + 1):
        dt = start_dt + timedelta(hours=h)
        cur_ratio = r ** h
        cur_pxs = {
            'BTCUSDT': btc_c * cur_ratio,
            'ETHUSDT': eth_c * cur_ratio
        }

        prev_halt_state = eng.halt_state
        eng.execute_close_bar(dt, cur_pxs, {}, stress_test_mode=True)

        if prev_halt_state == "NONE" and eng.halt_state in ("HALTING", "HALTED"):
            halt_hour = h
            halt_price_ratio = cur_ratio

        # For mode 'c', verify recovery 24 hours after halt (§8.2)
        if mode == 'c' and halt_hour is not None and h == (halt_hour + 24):
            recovery_hour = h
            if not eng.halt_active and eng.halt_state == "NONE":
                recovery_verified = True

    metrics = eng.get_metrics()
    return {
        'name': name,
        'metrics': metrics,
        'halt_hour': halt_hour,
        'halt_price_ratio': halt_price_ratio,
        'recovery_hour': recovery_hour,
        'recovery_verified': recovery_verified,
        'engine': eng
    }

def _run_synthetic_split_test(
    events, change1: float = -0.30, steps1: int = 360, change2: float = 0.20, steps2: int = 360
) -> Dict[str, Any]:
    """Stress Test 3: Down -30% (360 steps) then Up +20% (360 steps)."""
    start_dt = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    eng = BacktestEngine(events)
    eng.cash = 4994.00
    eng.initial_cash = 10000.00
    eng.peak_equity = 10000.00

    btc_c = 50000.0
    eth_c = 3000.0
    btc_d = config.BTC_SPACING_COEFF / 100.0
    eth_d = config.ETH_SPACING_COEFF / 100.0
    for sym, center_px, d, notionals, step in [
        ('BTCUSDT', btc_c, btc_d, config.BTC_LAYER_NOTIONALS, config.MIN_QTY_BTC),
        ('ETHUSDT', eth_c, eth_d, config.ETH_LAYER_NOTIONALS, config.MIN_QTY_ETH)
    ]:
        layers = []
        for lno in range(1, config.MAX_LAYERS + 1):
            trig = center_px * (1.0 - (lno - 1) * d)
            plan_notional = notionals[lno - 1]
            qty = plan_notional / trig
            actual_notional = plan_notional
            fee = round(actual_notional * config.FEE_RATE, 4)
            slip = round(actual_notional * config.SLIPPAGE_RATE, 4)

            layers.append({
                'layer_no': lno,
                'trigger_price': trig,
                'buy_price': trig,
                'qty': qty,
                'notional': actual_notional,
                'planned_notional': plan_notional,
                'buy_fee': fee,
                'buy_slippage': slip,
                'status': 'OPEN',
                'bought_dt': start_dt
            })
        grp = {
            'id': 1 if sym == 'BTCUSDT' else 2,
            'symbol': sym,
            'center_price': center_px,
            'spacing_pct': d,
            'status': 'OPEN',
            'opened_dt': start_dt,
            'first_buy_dt': start_dt,
            'stop_adding': 1,  # Already 9 layers
            'half_time_stopped': 0,
            'layers': layers
        }
        eng.groups[sym] = grp
        eng.symbol_state[sym]["last_valid_price"] = center_px
        eng.symbol_state[sym]["last_valid_price_dt"] = start_dt

    r1 = (1.0 + change1) ** (1.0 / steps1)
    r2 = (1.0 + change2) ** (1.0 / steps2)

    total_steps = steps1 + steps2
    for h in range(1, total_steps + 1):
        dt = start_dt + timedelta(hours=h)
        if h <= steps1:
            ratio = r1 ** h
        else:
            ratio = (r1 ** steps1) * (r2 ** (h - steps1))

        cur_pxs = {
            'BTCUSDT': btc_c * ratio,
            'ETHUSDT': eth_c * ratio
        }
        eng.execute_close_bar(dt, cur_pxs, {}, stress_test_mode=True)

    metrics = eng.get_metrics()
    return {'name': "Down -30% then Up +20%", 'metrics': metrics, 'engine': eng}


def print_partition_stats(name: str, total_hours: int, stats: Dict[str, Dict[str, int]], metrics: Dict[str, Any]):
    print(f"\n================================================================================")
    print(f"PARTITION: {name}")
    print(f"Total Hours in Grid: {total_hours}")
    for sym in ["BTCUSDT", "ETHUSDT"]:
        s = stats[sym]
        pe = s["price_error_rounds"]
        mb = s["missing_bar_rounds"]
        inv = s["invalid_ohlc_rounds"]
        sp = s["suspect_price_rounds"]
        aa = s["accepted_a_rounds"]
        eb = s["evaluated_b_rounds"]
        ab = s["accepted_b_rounds"]
        g3 = s["gaps_over_3_hours"]
        print(f"  {sym}:")
        print(f"    Price Error Rounds: {pe} (missing_bar={mb}, invalid_ohlc={inv}) [Check: {mb}+{inv}=={pe}]")
        print(f"    Gaps > 3 hours Count: {g3}")
        print(f"    Suspect Price Rounds: {sp}")
        print(f"    Condition (a) Accepted Rounds: {aa}")
        print(f"    Condition (b) Evaluated Rounds: {eb}, Accepted Rounds: {ab}")
    print(f"  Performance Metrics:")
    print(f"    Total PnL: {metrics['total_pnl']:.2f} USDT (End Equity: {metrics['end_equity']:.2f} USDT)")
    print(f"    Max Drawdown: {metrics['max_drawdown_pct']:.2f}% (close sample: {metrics['close_sample_max_dd_pct']:.2f}%, post-halt: {metrics['post_halt_max_dd_pct']:.2f}%)")
    print(f"    Profit Factor: {metrics['profit_factor']}")
    print(f"    Win Month Ratio: {metrics['win_month_ratio_pct']:.2f}% ({metrics['win_months']}/{metrics['active_months']})")
    print(f"    Total Trades: {metrics['total_trades']} (Sell trades: {metrics['sell_trades']})")
    print(f"================================================================================")


def main():
    parser = argparse.ArgumentParser(description="Spot Martingale Backtest Engine (SPEC v5.5)")
    parser.add_argument("--all", action="store_true", help="Run full suite (train + test + stress tests)")
    args = parser.parse_args()

    # Verify config
    if not config.verify_frozen_config():
        sys.exit(1)

    # Load events
    ver, sha, events = engine.load_events_calendar(config.EVENTS_CSV_PATH)
    print(f"Loaded events.csv: version={ver}, sha256={sha}")

    # Load klines with data cleaning details (V5.4-2(a))
    print("Loading and cleaning cached 1h klines...")
    btc_bars, raw_btc_hours, all_inv_btc, dq_btc = load_cached_klines("data/BTCUSDT_1h.csv", return_details=True)
    eth_bars, raw_eth_hours, all_inv_eth, dq_eth = load_cached_klines("data/ETHUSDT_1h.csv", return_details=True)
    print(f"Loaded BTC: {len(btc_bars)} valid bars (unmappable={dq_btc['unmappable_records']}, dups={dq_btc['duplicate_valid_records']}, all_invalid={dq_btc['all_invalid_hours_count']})")
    print(f"Loaded ETH: {len(eth_bars)} valid bars (unmappable={dq_eth['unmappable_records']}, dups={dq_eth['duplicate_valid_records']}, all_invalid={dq_eth['all_invalid_hours_count']})")

    # 1. Training Set Backtest (2021-01-01 00:00 to 2024-12-31 23:00 UTC)
    train_start = datetime(2021, 1, 1, 0, 0, tzinfo=timezone.utc)
    train_end = datetime(2024, 12, 31, 23, 0, tzinfo=timezone.utc)
    train_hours = int((train_end - train_start).total_seconds() / 3600.0) + 1
    train_eng, train_metrics = run_strategy_simulation(
        btc_bars, eth_bars, events, train_start, train_end,
        raw_grid_hours_btc=raw_btc_hours, raw_grid_hours_eth=raw_eth_hours,
        all_invalid_hours_btc=all_inv_btc, all_invalid_hours_eth=all_inv_eth
    )
    print_partition_stats("Training Set (2021-01-01 to 2024-12-31 UTC)", train_hours, train_eng.stats, train_metrics)

    # 2. Test Set Backtest (2025-01-01 00:00 to 2026-10-31 23:00 UTC)
    test_start = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    test_end = datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)
    test_hours = int((test_end - test_start).total_seconds() / 3600.0) + 1
    test_eng, test_metrics = run_strategy_simulation(
        btc_bars, eth_bars, events, test_start, test_end,
        raw_grid_hours_btc=raw_btc_hours, raw_grid_hours_eth=raw_eth_hours,
        all_invalid_hours_btc=all_inv_btc, all_invalid_hours_eth=all_inv_eth
    )
    print_partition_stats("Test Set (2025-01-01 to 2026-10-31 UTC)", test_hours, test_eng.stats, test_metrics)

    # 3. Stress Tests (§8.2)
    btc_fixed_d = config.BTC_SPACING_COEFF / 100.0
    eth_fixed_d = config.ETH_SPACING_COEFF / 100.0
    print(f"\nStress Tests 1-3 Fixed Spacings: BTC d={btc_fixed_d:.4f} (BTC_SPACING_COEFF={config.BTC_SPACING_COEFF}), ETH d={eth_fixed_d:.4f} (ETH_SPACING_COEFF={config.ETH_SPACING_COEFF})")

    print("\n--- Running Stress Test 1 (+50%) ---")
    st1 = run_stress_test_1(events)
    print("ST1 Results:", st1['metrics'])

    print("\n--- Running Stress Test 2 (-50%) ---")
    st2 = run_stress_test_2(events)
    print("ST2 Dual Results:", st2['dual']['metrics'], f"Halt hour: {st2['dual']['halt_hour']}, recovery verified: {st2['dual']['recovery_verified']}")
    print("ST2 BTC alone Results:", st2['btc_alone']['metrics'])
    print("ST2 ETH alone Results:", st2['eth_alone']['metrics'])

    print("\n--- Running Stress Test 3 (Down 30% Up 20%) ---")
    st3 = run_stress_test_3(events)
    print("ST3 Results:", st3['metrics'])

    print("\n--- Running Stress Test 4 (Real Slice 2022-06-01 to 2022-06-30 UTC) ---")
    st4 = run_stress_test_4(
        btc_bars, eth_bars, events,
        raw_btc=raw_btc_hours, raw_eth=raw_eth_hours,
        inv_btc=all_inv_btc, inv_eth=all_inv_eth
    )
    st4_hours = 720
    print_partition_stats("Stress Test 4 (Real Slice 2022-06 UTC)", st4_hours, st4['engine'].stats, st4['metrics'])

    # 4. Acceptance Criteria Evaluation (§8.3)
    print("\n" + "=" * 80)
    print("SPEC §8.3 ACCEPTANCE CRITERIA EVALUATION")
    print("=" * 80)

    # Item 1: Test set total PnL > 0
    t_pnl = test_metrics['total_pnl']
    pass_1 = (t_pnl > 0.0)
    print(f"1. Test Set Total PnL > 0: {'PASS' if pass_1 else 'FAIL'} (total_pnl = {t_pnl:.2f} USDT)")

    # Item 2: Test set max drawdown <= 16.00%
    cs_dd = test_metrics['close_sample_max_dd_pct']
    ph_dd = test_metrics['post_halt_max_dd_pct']
    pass_2 = (cs_dd <= 16.00 and ph_dd <= 16.00)
    print(f"2. Test Set Max Drawdown <= 16.00%: {'PASS' if pass_2 else 'FAIL'} (close_sample={cs_dd:.2f}%, post_halt={ph_dd:.2f}%)")

    # Item 3: Profit factor >= 1.20
    pf = test_metrics['profit_factor']
    pass_3 = (pf >= config.MIN_PROFIT_FACTOR if pf != float('inf') else True)
    print(f"3. Profit Factor >= 1.20: {'PASS' if pass_3 else 'FAIL'} (profit_factor = {pf})")

    # Item 4: Win month ratio >= 60%
    wm = test_metrics['win_month_ratio_pct']
    pass_4 = (wm >= 60.0)
    print(f"4. Win Month Ratio >= 60%: {'PASS' if pass_4 else 'FAIL'} (win_month_ratio = {wm:.2f}%, {test_metrics['win_months']}/{test_metrics['active_months']})")

    # Item 5: Stress Test 2 halt & recovery
    st2_dual = st2['dual']
    st2_btc = st2['btc_alone']
    st2_eth = st2['eth_alone']
    pass_5_c = (st2_dual['halt_hour'] is not None and st2_dual['metrics']['post_halt_max_dd_pct'] <= 16.00 and st2_dual['recovery_verified'])
    pass_5_a = (st2_btc['metrics']['max_drawdown_pct'] <= 16.00)
    pass_5_b = (st2_eth['metrics']['max_drawdown_pct'] <= 16.00)
    pass_5 = (pass_5_c and pass_5_a and pass_5_b)
    print(f"5. Stress Test 2 Halt & Recovery: {'PASS' if pass_5 else 'FAIL'} (Dual: halt_hour={st2_dual['halt_hour']}, dd={st2_dual['metrics']['post_halt_max_dd_pct']}%, recovery={st2_dual['recovery_verified']}; BTC alone dd={st2_btc['metrics']['max_drawdown_pct']}%; ETH alone dd={st2_eth['metrics']['max_drawdown_pct']}%)")

    # Item 6: Unit tests
    print(f"6. Unit Tests: (Run via 'python3 -m unittest discover tests' to verify all 13+ tests pass)")

    # Item 7: Reproducibility
    print(f"7. Reproducibility: (Run twice with identical config & data; verify metrics match exactly)")
    print("=" * 80)


if __name__ == "__main__":
    main()
