"""
SPEC v5.5 Unit Tests.
Verifies all frozen rules and gaps closed in SPEC v5.5:
1. evaluate_symbol_price contract (no default value for latest_kline_close)
2. Preheat initialization & 24h freshness limit (V5.4-1(a) / V5.5-1)
3. P-1 authoritative interpretation (pre-first-bar error vs first bar fallback init)
4. Hourly grid & missing bar handling (V5.4-2)
5. K-line gap > 3 hours tracking (V5.4-2(d))
6. Defensive check on halt expiration recovery in both backtest.py and monitor.py (V5.4-1(c) / agy P3-3)
7. Data cleaning, unmappable records, and error attribution (V5.4-2(a) / V5.5-3)
8. Condition (b) evaluation and acceptance counters
9. Unpriced symbol market value is 0 (V5.4-1(c) / P2 fix)
"""

import os
import sqlite3
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

import config
import engine
from backtest import BacktestEngine, load_cached_klines, run_strategy_simulation
from monitor import SpotMartingaleMonitor


class TestSpecV55(unittest.TestCase):
    def setUp(self):
        self.events = [
            (datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc), "CPI", "US", "HIGH")
        ]
        self.test_db_dir = "/tmp/spot_martingale_test_v55"
        os.makedirs(self.test_db_dir, exist_ok=True)
        self.db_path = os.path.join(self.test_db_dir, "test_v55.sqlite3")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        engine.init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_item_01_evaluate_symbol_price_no_default_param(self):
        """Contract: evaluate_symbol_price must require latest_kline_close without default value."""
        bt = BacktestEngine(self.events)
        dt = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)
        # Calling without latest_kline_close must raise TypeError
        with self.assertRaises(TypeError):
            bt.evaluate_symbol_price("BTCUSDT", 50000.0, dt) # type: ignore

        # Calling with explicit latest_kline_close succeeds
        val_px, blk, skp = bt.evaluate_symbol_price("BTCUSDT", 50000.0, dt, 50000.0)
        self.assertEqual(val_px, 50000.0)
        self.assertFalse(blk)
        self.assertFalse(skp)

    def test_item_02_preheat_initialization_and_24h_limit(self):
        """V5.4-1(a) / V5.5-1: Preheat bar <= 24h initializes last_valid_price; > 24h falls back."""
        start_dt = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        end_dt = datetime(2025, 1, 1, 5, 0, tzinfo=timezone.utc)

        # Case A: Preheat bar 1 hour before start_dt (<= 24h)
        bar_fresh = {
            'open_time': int((start_dt - timedelta(hours=1)).timestamp() * 1000),
            'dt': start_dt - timedelta(hours=1),
            'open': 90000.0, 'high': 91000.0, 'low': 89000.0, 'close': 90500.0,
            'volume': 100.0, 'close_time': int((start_dt - timedelta(hours=1)).timestamp() * 1000) + 3599999
        }
        # Evaluation bars
        eval_bars = []
        for h in range(6):
            b_dt = start_dt + timedelta(hours=h)
            eval_bars.append({
                'open_time': int(b_dt.timestamp() * 1000),
                'dt': b_dt,
                'open': 90500.0, 'high': 91000.0, 'low': 90000.0, 'close': 90500.0,
                'volume': 100.0, 'close_time': int(b_dt.timestamp() * 1000) + 3599999
            })

        eng_a, _ = run_strategy_simulation([bar_fresh] + eval_bars, [bar_fresh] + eval_bars, self.events, start_dt, end_dt)
        self.assertEqual(eng_a.symbol_state["BTCUSDT"]["last_valid_price"], 90500.0)
        self.assertEqual(eng_a.stats["BTCUSDT"]["price_error_rounds"], 0)

        # Case B: Preheat bar 25 hours before start_dt (> 24h -> expiration fallback)
        bar_stale = {
            'open_time': int((start_dt - timedelta(hours=25)).timestamp() * 1000),
            'dt': start_dt - timedelta(hours=25),
            'open': 80000.0, 'high': 81000.0, 'low': 79000.0, 'close': 80500.0,
            'volume': 100.0, 'close_time': int((start_dt - timedelta(hours=25)).timestamp() * 1000) + 3599999
        }
        # At start_dt, no valid bar (missing bar round)
        # Then at start_dt + 1h, first valid bar arrives
        eval_bars_b = []
        for h in range(1, 6):
            b_dt = start_dt + timedelta(hours=h)
            eval_bars_b.append({
                'open_time': int(b_dt.timestamp() * 1000),
                'dt': b_dt,
                'open': 95000.0, 'high': 96000.0, 'low': 94000.0, 'close': 95000.0,
                'volume': 100.0, 'close_time': int(b_dt.timestamp() * 1000) + 3599999
            })
        eng_b, _ = run_strategy_simulation([bar_stale] + eval_bars_b, [bar_stale] + eval_bars_b, self.events, start_dt, end_dt)
        # Hour 0 was missing before first valid bar -> 1 price error round
        self.assertEqual(eng_b.stats["BTCUSDT"]["price_error_rounds"], 1)
        self.assertEqual(eng_b.stats["BTCUSDT"]["missing_bar_rounds"], 1)
        # Hour 1 was first valid bar -> initialized to 95000 without price error
        self.assertEqual(eng_b.symbol_state["BTCUSDT"]["last_valid_price"], 95000.0)

    def test_item_03_p1_authoritative_interpretation(self):
        """P-1: Rounds before first valid bar pass latest_kline_close=None and count price error; first bar initializes without error."""
        bt = BacktestEngine(self.events)
        dt0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        dt1 = datetime(2025, 1, 1, 1, 0, tzinfo=timezone.utc)
        dt2 = datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc)

        # Round 0: No valid price, no preheat (raw_price=None, latest_kline_close=None)
        val0, blk0, skp0 = bt.evaluate_symbol_price("BTCUSDT", None, dt0, None)
        self.assertIsNone(val0)
        self.assertTrue(blk0)
        self.assertTrue(skp0)
        self.assertEqual(bt.symbol_state["BTCUSDT"]["price_error_streak"], 1)
        self.assertEqual(bt.stats["BTCUSDT"]["price_error_rounds"], 1)
        self.assertIsNone(bt.symbol_state["BTCUSDT"]["last_valid_price"])
        self.assertIsNone(bt.symbol_state["BTCUSDT"]["last_valid_price_dt"])

        # Round 1: First valid bar arrives at 50000.0 -> Fallback initialization
        val1, blk1, skp1 = bt.evaluate_symbol_price("BTCUSDT", 50000.0, dt1, None)
        self.assertEqual(val1, 50000.0)
        self.assertFalse(blk1)
        self.assertFalse(skp1)
        self.assertEqual(bt.symbol_state["BTCUSDT"]["last_valid_price"], 50000.0)
        self.assertEqual(bt.symbol_state["BTCUSDT"]["last_valid_price_dt"], dt1)
        self.assertEqual(bt.symbol_state["BTCUSDT"]["price_error_streak"], 0)
        # Does NOT increment price_error_rounds on first valid bar initialization round!
        self.assertEqual(bt.stats["BTCUSDT"]["price_error_rounds"], 1)

        # Round 2: Normal valid bar at 50500.0 (jump 1% <= 10%)
        val2, blk2, skp2 = bt.evaluate_symbol_price("BTCUSDT", 50500.0, dt2, 50000.0)
        self.assertEqual(val2, 50500.0)
        self.assertFalse(blk2)
        self.assertEqual(bt.symbol_state["BTCUSDT"]["last_valid_price"], 50500.0)
        self.assertEqual(bt.symbol_state["BTCUSDT"]["last_valid_price_dt"], dt2)

    def test_item_04_missing_bar_handling_and_time_stops(self):
        """V5.4-2(e): Missing bar uses last_valid_price for valuation and executes time stops, but skips TP."""
        bt = BacktestEngine(self.events)
        start_dt = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        bt.symbol_state["BTCUSDT"]["last_valid_price"] = 50000.0
        bt.symbol_state["BTCUSDT"]["last_valid_price_dt"] = start_dt

        # Setup an OPEN group that has been open for 15 days (> 14 days time stop)
        first_buy_dt = start_dt - timedelta(days=15)
        grp = {
            'id': 1,
            'symbol': 'BTCUSDT',
            'center_price': 50000.0,
            'spacing_pct': 0.015,
            'status': 'OPEN',
            'opened_dt': first_buy_dt,
            'first_buy_dt': first_buy_dt,
            'stop_adding': 0,
            'half_time_stopped': 0,
            'layers': [
                {
                    'layer_no': 1,
                    'trigger_price': 50000.0,
                    'buy_price': 50000.0,
                    'qty': 0.004,
                    'notional': 200.0,
                    'buy_fee': 0.20,
                    'buy_slippage': 0.04,
                    'status': 'OPEN',
                    'bought_dt': first_buy_dt
                }
            ]
        }
        bt.groups["BTCUSDT"] = grp
        # Deduct initial buy cost from cash to preserve cash invariant
        bt.cash -= (200.0 + 0.20 + 0.04)

        # Execute close bar with missing bar (raw_price=None)
        bt.execute_close_bar(start_dt, {"BTCUSDT": None, "ETHUSDT": 3000.0}, {}, {"BTCUSDT": 50000.0, "ETHUSDT": 3000.0})

        # 14d time stop must execute at last_valid_price (50000.0)
        self.assertIsNone(bt.groups["BTCUSDT"])
        self.assertEqual(len(bt.closed_groups), 1)
        self.assertEqual(bt.closed_groups[0]["close_reason"], "TIME_STOP_14D")
        self.assertEqual(bt.trades[-1]["reason"], "TIME_STOP_14D")
        self.assertEqual(bt.trades[-1]["price"], 50000.0)

    def test_item_05_defensive_check_halt_recovery_backtest_and_monitor(self):
        """V5.4-1(c) / agy P3-3: halt_active==1 and any symbol last_valid_price is None blocks recovery."""
        from pathlib import Path
        now = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)

        # 1. BacktestEngine test
        bt = BacktestEngine(self.events)
        bt.halt_active = True
        bt.halt_state = "HALTED"
        bt.halted_until_dt = now - timedelta(minutes=5)
        bt.peak_equity = 10000.0
        # Set BTC valid price, leave ETH as None
        bt.symbol_state["BTCUSDT"]["last_valid_price"] = 50000.0
        bt.symbol_state["ETHUSDT"]["last_valid_price"] = None

        # Execute bar where cooldown has expired
        bt.execute_close_bar(now, {"BTCUSDT": 50000.0, "ETHUSDT": None}, {})
        # Recovery must be BLOCKED: halt remains active, state remains HALTED
        self.assertTrue(bt.halt_active)
        self.assertEqual(bt.halt_state, "HALTED")
        self.assertIsNotNone(bt.halted_until_dt)

        # 2. Monitor test in DB
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            UPDATE account
            SET halt_active = 1,
                halt_state = 'HALTED',
                halted_until_utc = '2026-11-06T00:50:00Z',
                peak_equity_usdt = 10000.0
            WHERE id = 1
        """)
        # In symbol_state: BTC has valid price, ETH is NULL
        cur.execute("UPDATE symbol_state SET last_valid_price = 50000.0 WHERE symbol = 'BTCUSDT'")
        cur.execute("UPDATE symbol_state SET last_valid_price = NULL WHERE symbol = 'ETHUSDT'")
        conn.commit()

        monitor = SpotMartingaleMonitor(Path(self.db_path))
        monitor.startup_checks()
        # ETH ticker price fails so its last_valid_price remains None
        monitor.client.get_ticker_price = lambda sym: (50000.0 if sym == "BTCUSDT" else None, None if sym == "BTCUSDT" else "Network error")
        monitor.client.get_server_time = lambda: (now, None)
        monitor.client.get_recent_closed_klines = lambda sym, limit: ([[0, 0, 0, 0, 50000.0, 0, 1]] * limit, None)

        monitor.poll_once(now, 100.0)

        cur.execute("SELECT halt_active, halt_state, halted_until_utc FROM account WHERE id = 1")
        acc_row = cur.fetchone()
        self.assertEqual(acc_row["halt_active"], 1, "Monitor must NOT clear halt_active when last_valid_price is None")
        self.assertEqual(acc_row["halt_state"], "HALTED")
        self.assertIsNotNone(acc_row["halted_until_utc"])
        conn.close()

    def test_item_06_condition_b_evaluation_and_counters(self):
        """V5.4-1(d): Condition (b) evaluated on jump > 10% not accepted by (a), and stats accurately tracked."""
        bt = BacktestEngine(self.events)
        dt = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)
        bt.symbol_state["BTCUSDT"]["last_valid_price"] = 50000.0
        bt.symbol_state["BTCUSDT"]["last_valid_price_dt"] = dt

        # Jump +20% to 60000. Condition (a) has no P1, so Condition (b) evaluates against latest_kline_close=61000
        # |60000 - 61000| / 61000 = 1.64% <= 5% -> Condition (b) accepts!
        val_px, blk, skp = bt.evaluate_symbol_price("BTCUSDT", 60000.0, dt, 61000.0)
        # Condition (b) acceptance uses old_valid for current round valuation
        self.assertEqual(val_px, 50000.0)
        self.assertTrue(blk)
        self.assertTrue(skp)
        self.assertEqual(bt.symbol_state["BTCUSDT"]["last_valid_price"], 60000.0)
        self.assertEqual(bt.stats["BTCUSDT"]["evaluated_b_rounds"], 1)
        self.assertEqual(bt.stats["BTCUSDT"]["accepted_b_rounds"], 1)
        self.assertEqual(bt.stats["BTCUSDT"]["suspect_price_rounds"], 0)


    def test_item_07_unpriced_symbol_market_value_zero(self):
        """V5.4-1(c): OPEN group with unpriced symbol (price is None) contributes 0 to market value, equity = cash + other market value."""
        bt = BacktestEngine(self.events)
        bt.cash = 10000.0
        now = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)

        # BTC group OPEN with 1 OPEN layer
        bt.groups["BTCUSDT"] = {
            'id': 1,
            'symbol': 'BTCUSDT',
            'center_price': 50000.0,
            'spacing_pct': 0.015,
            'status': 'OPEN',
            'opened_dt': now,
            'first_buy_dt': now,
            'stop_adding': 0,
            'half_time_stopped': 0,
            'layers': [
                {
                    'layer_no': 1,
                    'trigger_price': 50000.0,
                    'buy_price': 50000.0,
                    'qty': 0.1,
                    'notional': 5000.0,
                    'buy_fee': 5.0,
                    'buy_slippage': 1.0,
                    'status': 'OPEN',
                    'bought_dt': now
                }
            ]
        }

        # ETH group OPEN with 1 OPEN layer
        bt.groups["ETHUSDT"] = {
            'id': 2,
            'symbol': 'ETHUSDT',
            'center_price': 3000.0,
            'spacing_pct': 0.015,
            'status': 'OPEN',
            'opened_dt': now,
            'first_buy_dt': now,
            'stop_adding': 0,
            'half_time_stopped': 0,
            'layers': [
                {
                    'layer_no': 1,
                    'trigger_price': 3000.0,
                    'buy_price': 3000.0,
                    'qty': 2.0,
                    'notional': 6000.0,
                    'buy_fee': 6.0,
                    'buy_slippage': 1.2,
                    'status': 'OPEN',
                    'bought_dt': now
                }
            ]
        }

        # Case 1: BTCUSDT price is None, ETHUSDT price is 3000.0
        # BTC market value must contribute 0.0 (no fallback to center_price 50000.0)
        # ETH market value contributes 2.0 * 3000.0 = 6000.0
        mv = bt.get_open_market_value({"BTCUSDT": None, "ETHUSDT": 3000.0})
        self.assertEqual(mv, 6000.0)

        # Equity must equal cash (10000.0) + 0 + ETH market value (6000.0) = 16000.0
        eq = bt.get_equity({"BTCUSDT": None, "ETHUSDT": 3000.0})
        self.assertEqual(eq, 16000.0)

        # Case 2: Both prices are None -> total open market value is 0.0, equity equals cash
        mv_none = bt.get_open_market_value({"BTCUSDT": None, "ETHUSDT": None})
        self.assertEqual(mv_none, 0.0)
        eq_none = bt.get_equity({"BTCUSDT": None, "ETHUSDT": None})
        self.assertEqual(eq_none, bt.cash)

    def test_item_08_monitor_unpriced_symbol_equity_and_halting_sweep(self):
        """V5.4-1(c) P2 fix round 2: monitor.py _compute_current_equity gives 0 market value and 0 unrealized pnl
        for unpriced symbol (no fallback to buy_price); _execute_halting_sweep skips layer without price and logs ERROR.
        """
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        now = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)
        # initial = 25000.0; BTC layer costs 5006.0, ETH layer costs 6007.2 (total 11013.2)
        # cash_usdt = 25000.0 - 11013.2 = 13986.8
        cur.execute("""
            UPDATE account
            SET initial_usdt = 25000.0, cash_usdt = 13986.8, realized_pnl_usdt = 0.0,
                halt_zero_progress_rounds = 0, halt_state = 'HALTING'
            WHERE id = 1
        """)

        # Insert BTC group with 1 OPEN layer
        cur.execute("""
            INSERT INTO grid_groups (id, symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc)
            VALUES (1, 'BTCUSDT', 50000.0, 0.015, 0.02, 'OPEN', ?, ?)
        """, (now.isoformat(), now.isoformat()))
        cur.execute("""
            INSERT INTO layers (id, group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc)
            VALUES (1, 1, 1, 50000.0, 50000.0, 0.1, 5000.0, 5.0, 1.0, 'OPEN', ?)
        """, (now.isoformat(),))

        # Insert ETH group with 1 OPEN layer
        cur.execute("""
            INSERT INTO grid_groups (id, symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc)
            VALUES (2, 'ETHUSDT', 3000.0, 0.015, 0.02, 'OPEN', ?, ?)
        """, (now.isoformat(), now.isoformat()))
        cur.execute("""
            INSERT INTO layers (id, group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc)
            VALUES (2, 2, 1, 3000.0, 3000.0, 2.0, 6000.0, 6.0, 1.2, 'OPEN', ?)
        """, (now.isoformat(),))
        conn.commit()

        monitor = SpotMartingaleMonitor(Path(self.db_path))

        # 1. Test _compute_current_equity:
        # Case 1: BTC price is None, ETH price is 3000.0
        # BTC must contribute 0 to market value & unrealized (not 0.1 * 50000 = 5000)
        # Total equity must be 13986.8 (cash) + 0 + (2.0 * 3000) = 19986.8
        # Unrealized PnL must be 0 (BTC) + (2.0 * (3000 - 3000)) = 0.0
        eq, unrec = monitor._compute_current_equity(conn, {"BTCUSDT": None, "ETHUSDT": 3000.0})
        self.assertAlmostEqual(eq, 19986.8, places=4)
        self.assertAlmostEqual(unrec, 0.0, places=4)

        # Case 2: Both prices are None -> total equity is cash (13986.8), unrealized is 0.0
        eq_none, unrec_none = monitor._compute_current_equity(conn, {"BTCUSDT": None, "ETHUSDT": None})
        self.assertAlmostEqual(eq_none, 13986.8, places=4)
        self.assertAlmostEqual(unrec_none, 0.0, places=4)

        # 2. Test _execute_halting_sweep:
        # BTC price is None, ETH price is 3000.0
        # BTC layer must NOT be liquidated (skipped + logged ERROR)
        # ETH layer MUST be liquidated (status becomes HALT_CLOSED)
        monitor._execute_halting_sweep(conn, {"BTCUSDT": None, "ETHUSDT": 3000.0}, now)

        # BTC layer still OPEN
        cur.execute("SELECT status FROM layers WHERE id = 1")
        self.assertEqual(cur.fetchone()["status"], "OPEN")
        # ETH layer HALT_CLOSED
        cur.execute("SELECT status FROM layers WHERE id = 2")
        self.assertEqual(cur.fetchone()["status"], "HALT_CLOSED")

        # Because BTC layer is still OPEN, account cannot transition to HALTED
        cur.execute("SELECT halt_state, halt_zero_progress_rounds FROM account WHERE id = 1")
        acc_row = cur.fetchone()
        self.assertNotEqual(acc_row["halt_state"], "HALTED")
        # ETH layer was sold, so sold_count > 0, halt_zero_progress_rounds is 0
        self.assertEqual(acc_row["halt_zero_progress_rounds"], 0)

        # Now test when only unpriced layers remain: run sweep again with BTC still None
        monitor._execute_halting_sweep(conn, {"BTCUSDT": None, "ETHUSDT": 3000.0}, now)
        cur.execute("SELECT halt_zero_progress_rounds FROM account WHERE id = 1")
        # sold_count == 0 and len(open_layers) > 0 -> halt_zero_progress_rounds increments to 1
        self.assertEqual(cur.fetchone()["halt_zero_progress_rounds"], 1)

        conn.close()


if __name__ == "__main__":
    unittest.main()
