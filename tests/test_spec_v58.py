"""
Unit and regression test suite for SPEC v5.8 requirements:
1. Position filter: Position <= 0.35 (inclusive), degenerate window HH <= LL fail-closed.
2. Flat multiplier: 1.0 for all tiers, 9-layer planned notional constant at seed.
3. ADX threshold separation: ADX < 30 for opening new group vs REGIME_ADX_THRESHOLD = 25.0.
4. Transition close regression: legacy OPEN groups closed and v58_transition_done set to 1;
   subsequent start leaves post-transition OPEN groups untouched.
"""

import sys
import os
import unittest
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from pathlib import Path

# Ensure root directory in path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config
import engine
from monitor import SpotMartingaleMonitor
import monitor as monitor_mod
import backtest


class TestSpecV58Requirements(unittest.TestCase):
    def setUp(self):
        self.test_dir = ROOT_DIR / "data" / "test_scratch_v58"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.test_dir / "test_spec_v58.sqlite3"
        if self.db_path.exists():
            self.db_path.unlink()
        engine.init_db(self.db_path)

    def tearDown(self):
        for f in self.test_dir.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass
        try:
            self.test_dir.rmdir()
        except Exception:
            pass

    def _sync_cash_invariant(self, conn: sqlite3.Connection):
        """Set cash_usdt = initial + realized - open_basis - open_costs to satisfy cash invariant."""
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(notional_usdt), 0.0) as open_basis,
                   COALESCE(SUM(buy_fee_usdt + buy_slippage_usdt), 0.0) as open_costs
            FROM layers
            WHERE status = 'OPEN'
        """)
        r = cur.fetchone()
        ob = r["open_basis"]
        oc = r["open_costs"]
        cur.execute("SELECT initial_usdt, COALESCE(realized_pnl_usdt, 0.0) as realized FROM account WHERE id = 1")
        acc = cur.fetchone()
        exp_cash = acc["initial_usdt"] + acc["realized"] - ob - oc
        cur.execute("UPDATE account SET cash_usdt = ? WHERE id = 1", (exp_cash,))
        conn.commit()

    # -------------------------------------------------------------
    # 1. Position filter and degenerate window tests (§3.4, §4.1 Condition 7)
    # -------------------------------------------------------------
    def test_position_filter_boundaries_and_degenerate_window(self):
        """Test Position <= 0.35 boundary (inclusive) and degenerate window HH <= LL."""
        # Synthesize 500 closed bars where the last 480 bars have LL=100.0, HH=200.0
        # HH - LL = 100.0.
        # Boundary: close = 135.0 -> Position = (135.0 - 100.0) / 100.0 = 0.35 (pos_ok = True)
        mock_now = datetime.now(timezone.utc)
        base_klines = []
        for i in range(500):
            # Bar 0..19: filler
            # Bar 20..499: recent 480 bars
            c = 150.0
            h = 160.0
            l = 140.0
            if i == 20: # earliest in 480-window
                h = 200.0 # sets HH_480 = 200.0
            elif i == 21:
                l = 100.0 # sets LL_480 = 100.0
            base_klines.append([i * 3600, c, h, l, c, 10.0, (i + 1) * 3600 * 1000])

        # Case A: close = 135.0 (Position = 0.35 exactly -> pos_ok must be True)
        klines_35 = [list(b) for b in base_klines]
        klines_35[-1][4] = 135.0
        inds_35 = engine.calculate_indicators(klines_35)
        self.assertAlmostEqual(inds_35["position"], 0.35, places=4)
        self.assertTrue(inds_35["pos_ok"], "Position == 0.35 must satisfy pos_ok (inclusive)")

        # Case B: close = 135.01 (Position = 0.3501 > 0.35 -> pos_ok must be False)
        klines_above = [list(b) for b in base_klines]
        klines_above[-1][4] = 135.01
        inds_above = engine.calculate_indicators(klines_above)
        self.assertGreater(inds_above["position"], 0.35)
        self.assertFalse(inds_above["pos_ok"], "Position > 0.35 must fail pos_ok")

        # Case C: close = 134.99 (Position = 0.3499 < 0.35 -> pos_ok must be True)
        klines_below = [list(b) for b in base_klines]
        klines_below[-1][4] = 134.99
        inds_below = engine.calculate_indicators(klines_below)
        self.assertLess(inds_below["position"], 0.35)
        self.assertTrue(inds_below["pos_ok"])

        # Case D: close == LL_480 = 100.0 (Position = 0.0 -> pos_ok True, but in_range False)
        klines_new_low = [list(b) for b in base_klines]
        klines_new_low[-1][4] = 100.0
        inds_new_low = engine.calculate_indicators(klines_new_low)
        self.assertAlmostEqual(inds_new_low["position"], 0.0, places=4)
        self.assertTrue(inds_new_low["pos_ok"])
        self.assertFalse(inds_new_low["in_range"], "Close == LL fails condition 2 (strict inner point)")

        # Case E: Degenerate window (HH <= LL, e.g. all 500 bars flat)
        flat_klines = [[i * 3600, 100.0, 100.0, 100.0, 100.0, 1.0, (i + 1) * 3600 * 1000] for i in range(500)]
        inds_flat = engine.calculate_indicators(flat_klines)
        self.assertIsNone(inds_flat["position"])
        self.assertFalse(inds_flat["pos_ok"], "Degenerate window HH <= LL must fail closed (pos_ok=False)")

        # Case F: Insufficient bars (n < 500)
        short_klines = flat_klines[:400]
        inds_short = engine.calculate_indicators(short_klines)
        self.assertIsNone(inds_short["position"])
        self.assertFalse(inds_short["pos_ok"], "Insufficient bars must return pos_ok=False")

        # Verify backtest.precompute_series_indicators has identical output
        bars_35 = [{'open': b[1], 'high': b[2], 'low': b[3], 'close': b[4], 'volume': b[5], 'time': datetime.fromtimestamp(b[0], tz=timezone.utc)} for b in klines_35]
        bt_inds = backtest.precompute_series_indicators(bars_35)
        self.assertAlmostEqual(bt_inds[-1]["position"], 0.35, places=4)
        self.assertTrue(bt_inds[-1]["pos_ok"])

        bars_flat = [{'open': 100.0, 'high': 100.0, 'low': 100.0, 'close': 100.0, 'volume': 1.0, 'time': datetime.fromtimestamp(i * 3600, tz=timezone.utc)} for i in range(500)]
        bt_inds_flat = backtest.precompute_series_indicators(bars_flat)
        self.assertIsNone(bt_inds_flat[-1]["position"])
        self.assertFalse(bt_inds_flat[-1]["pos_ok"])

    # -------------------------------------------------------------
    # 2. Flat multiplier (1.0) and constant seed recursion (§2, §3.2)
    # -------------------------------------------------------------
    def test_flat_multiplier_and_nine_layer_seed_recursion(self):
        """Verify flat 1.0 multiplier across all DD tiers and constant seed planned notionals."""
        # Check config constants and frozen table
        self.assertEqual(config.MULT_DD_LT_2, 1.0)
        self.assertEqual(config.MULT_DD_2_5, 1.0)
        self.assertEqual(config.MULT_DD_5_10, 1.0)
        self.assertEqual(config.MULT_DD_10_20, 1.0)
        self.assertEqual(config.POSITION_THRESHOLD, 0.35)

        # Dynamic multiplier outputs 1.0 across tiers
        self.assertEqual(engine.get_dynamic_multiplier(0.00, "RANGING"), (1.0, True))
        self.assertEqual(engine.get_dynamic_multiplier(0.015, "SLOW_DOWN"), (1.0, True))
        self.assertEqual(engine.get_dynamic_multiplier(0.03, "RANGING"), (1.0, True))
        self.assertEqual(engine.get_dynamic_multiplier(0.07, "SLOW_DOWN"), (1.0, True))
        self.assertEqual(engine.get_dynamic_multiplier(0.12, "RANGING"), (1.0, True))
        self.assertEqual(engine.get_dynamic_multiplier(0.15, "SLOW_DOWN"), (1.0, True))
        self.assertEqual(engine.get_dynamic_multiplier(0.20, "REBOUND"), (1.0, True))

        # Blocked in STRONG_DOWN for 10% <= DD <= 20%
        self.assertEqual(engine.get_dynamic_multiplier(0.10, "STRONG_DOWN"), (1.0, False))
        self.assertEqual(engine.get_dynamic_multiplier(0.15, "STRONG_DOWN"), (1.0, False))
        self.assertEqual(engine.get_dynamic_multiplier(0.20, "STRONG_DOWN"), (1.0, False))

        # Stop adding for DD > 20%
        self.assertEqual(engine.get_dynamic_multiplier(0.2001, "RANGING"), (None, False))

        # Recursion verification: BTC seed = 93.71, ETH seed = 62.47
        btc_seed = config.BTC_LAYER_NOTIONALS[0]
        eth_seed = config.ETH_LAYER_NOTIONALS[0]
        self.assertEqual(btc_seed, 93.71)
        self.assertEqual(eth_seed, 62.47)

        # 9 layers planned notionals remain strictly equal to seed
        btc_plans = [btc_seed]
        for _ in range(2, 10):
            btc_plans.append(round(btc_plans[-1] * 1.0, 2))
        self.assertEqual(btc_plans, [93.71] * 9)
        self.assertAlmostEqual(sum(btc_plans), 843.39, places=2)
        self.assertLess(sum(btc_plans), config.BTC_CAP_USDT)

        eth_plans = [eth_seed]
        for _ in range(2, 10):
            eth_plans.append(round(eth_plans[-1] * 1.0, 2))
        self.assertEqual(eth_plans, [62.47] * 9)
        self.assertAlmostEqual(sum(eth_plans), 562.23, places=2)
        self.assertLess(sum(eth_plans), config.ETH_CAP_USDT)

    # -------------------------------------------------------------
    # 3. ADX threshold separation (§4.1 Condition 1 vs §4.3)
    # -------------------------------------------------------------
    def test_adx_30_open_gate_and_regime_25_separation(self):
        """Verify ADX < 30 opening gate separates from REGIME_ADX_THRESHOLD = 25.0."""
        self.assertEqual(config.ADX_THRESHOLD, 30.0)
        self.assertEqual(config.REGIME_ADX_THRESHOLD, 25.0)

        # Case 1: ADX = 27.0 (< 30.0) -> satisfies opening gate
        adx_val = 27.0
        cond1 = (adx_val < config.ADX_THRESHOLD)
        self.assertTrue(cond1, "ADX 27 < 30 must satisfy opening condition 1")

        # But with -DI > +DI, classify_regime categorizes it as SLOW_DOWN (since ADX >= 25.0)
        regime = engine.classify_regime(adx_val, plus_di=10.0, minus_di=20.0, ret24=-0.01)
        self.assertEqual(regime, "SLOW_DOWN", "ADX 27 >= 25 with -DI > +DI classifies as SLOW_DOWN")

        # Case 2: ADX = 27.0 with RET24 = +0.04 -> falls into RANGING, NOT REBOUND
        # because REBOUND requires ADX < 25.0 (§4.3)
        regime_rebound_check = engine.classify_regime(adx_val, plus_di=20.0, minus_di=10.0, ret24=0.04)
        self.assertEqual(regime_rebound_check, "RANGING", "ADX 27 >= 25 with RET24 >= 3% must be RANGING, not REBOUND")

        # Case 3: ADX = 30.0 -> fails opening gate (strictly < 30)
        self.assertFalse(30.0 < config.ADX_THRESHOLD)

        # Case 4: ADX = 29.99 -> satisfies opening gate
        self.assertTrue(29.99 < config.ADX_THRESHOLD)

    # -------------------------------------------------------------
    # 4. Transition close regression test (§6.1, V5.8-4, §9 Rule 24(c))
    # -------------------------------------------------------------
    def test_v58_transition_close_regression_full_path(self):
        """
        Transition Close Regression Test:
        - When v5.8 starts with v58_transition_done == 0, legacy OPEN groups are closed
          with TRANSITION_CLOSE, their open layers sold, and v58_transition_done set to 1.
        - On subsequent startup, newly opened groups under v5.8 remain untouched.
        """
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        t0 = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)

        # Check account migration and initial flag
        cur.execute("SELECT v58_transition_done FROM account WHERE id = 1")
        self.assertEqual(cur.fetchone()["v58_transition_done"], 0)

        # Create a legacy OPEN group (e.g. BTCUSDT with 2 OPEN layers, 7 PENDING layers)
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc, stop_adding)
            VALUES ('BTCUSDT', 50000.0, 0.02, 0.01, 'OPEN', ?, ?, 0)
        """, (t0.isoformat(), t0.isoformat()))
        gid_legacy = cur.lastrowid

        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc, planned_notional_usdt)
            VALUES (?, 1, 50000.0, 50000.0, 0.002, 100.0, 0.10, 0.02, 'OPEN', ?, 93.71)
        """, (gid_legacy, t0.isoformat()))
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc, planned_notional_usdt)
            VALUES (?, 2, 49000.0, 49000.0, 0.002, 98.0, 0.098, 0.0196, 'OPEN', ?, 93.71)
        """, (gid_legacy, t0.isoformat()))
        for lno in range(3, 10):
            cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, ?, 45000.0, 'PENDING')", (gid_legacy, lno))

        cur.execute("UPDATE symbol_state SET last_valid_price = 50200.0 WHERE symbol = 'BTCUSDT'")
        self._sync_cash_invariant(conn)
        engine.assert_cash_invariant(conn)
        conn.close()

        # Step 1: Run transition close
        monitor = SpotMartingaleMonitor(self.db_path)
        monitor.execute_transition_close()

        # Verify DB state after transition
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT status, close_reason FROM grid_groups WHERE id = ?", (gid_legacy,))
        g_leg = cur.fetchone()
        self.assertEqual(g_leg["status"], "CLOSED")
        self.assertEqual(g_leg["close_reason"], "TRANSITION_CLOSE")

        # Verify open layers were sold
        cur.execute("SELECT COUNT(*) as cnt FROM trades WHERE group_id = ? AND reason = 'TRANSITION_CLOSE'", (gid_legacy,))
        self.assertEqual(cur.fetchone()["cnt"], 2)

        # Verify pending layers cancelled
        cur.execute("SELECT COUNT(*) as cnt FROM layers WHERE group_id = ? AND status = 'CANCELLED'", (gid_legacy,))
        self.assertEqual(cur.fetchone()["cnt"], 7)

        # Verify v58_transition_done == 1
        cur.execute("SELECT v58_transition_done FROM account WHERE id = 1")
        self.assertEqual(cur.fetchone()["v58_transition_done"], 1)
        engine.assert_cash_invariant(conn)

        # Step 2: Now open a new group under v5.8 rules
        t1 = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc, stop_adding)
            VALUES ('ETHUSDT', 3000.0, 0.025, 0.01, 'OPEN', ?, ?, 0)
        """, (t1.isoformat(), t1.isoformat()))
        gid_new = cur.lastrowid
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc, planned_notional_usdt)
            VALUES (?, 1, 3000.0, 3000.0, 0.02, 60.0, 0.06, 0.012, 'OPEN', ?, 62.47)
        """, (gid_new, t1.isoformat()))
        for lno in range(2, 10):
            cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, ?, 2800.0, 'PENDING')", (gid_new, lno))

        cur.execute("UPDATE symbol_state SET last_valid_price = 3000.0 WHERE symbol = 'ETHUSDT'")
        self._sync_cash_invariant(conn)
        engine.assert_cash_invariant(conn)
        conn.close()

        # Step 3: Second start / execution of transition close
        # v58_transition_done is 1, so execute_transition_close must do NOTHING
        monitor.execute_transition_close()

        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT status FROM grid_groups WHERE id = ?", (gid_new,))
        self.assertEqual(cur.fetchone()["status"], "OPEN", "Newly opened post-transition group must remain untouched")

        # No new transition close trades
        cur.execute("SELECT COUNT(*) as cnt FROM trades WHERE reason = 'TRANSITION_CLOSE'")
        self.assertEqual(cur.fetchone()["cnt"], 2)
        engine.assert_cash_invariant(conn)
        conn.close()


if __name__ == "__main__":
    unittest.main()
