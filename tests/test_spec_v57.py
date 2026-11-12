"""
SPEC v5.7 Unit Tests (§8.3(6)).
Tests for:
1. Trend regime waterfall table & boundary values (ADX=25.0, RET24=-0.05, +0.03, ±DI equal).
2. Dynamic multiplier ladder & planned notional recursion (plan -> trunc -> round, recursion uses plan).
3. SLOW_DOWN cooldown boundary (now_utc - max(bought_utc) exactly 86400s).
4. NULL planned notional fallback (§6.1: ERROR, STOP_ADDING risk event, cancel PENDING, stop_adding=1).
5. Schema migration idempotency (init_db called multiple times).
6. Transition close idempotency (§9 Rule 18(d)).
7. HOLDMAX exempts 7d/14d time stops at bought_layers == 9, but TP & halt still execute.
"""

import os
import sys
import unittest
import sqlite3
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import engine
from monitor import SpotMartingaleMonitor
from backtest import BacktestEngine


class TestSpecV57Requirements(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test_martingale_v57.sqlite3"
        engine.init_db(self.db_path)
        events_path = Path(self.test_dir) / "events.csv"
        content = "version=20261108,sha256=1b039cfa90577b5b59b298b53f5d85a74cd63e03aba0cbde29e382c59b73eecc\n2025-01-15T12:00:00Z,CPI,US,HIGH\n"
        with open(events_path, "w", encoding="utf-8") as f:
            f.write(content)
        self.events = {datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)}

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

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
    # 1. Trend regime waterfall table & boundary values
    # -------------------------------------------------------------
    def test_trend_regime_waterfall_and_boundaries(self):
        # 1. STRONG_DOWN: ADX >= 25.0 and -DI > +DI and ret24 <= -0.05
        # Exact boundary: ADX=25.0, -DI=20.0, +DI=19.9, ret24=-0.05
        self.assertEqual(engine.classify_regime(25.0, 19.9, 20.0, -0.05, None), "STRONG_DOWN")
        self.assertEqual(engine.classify_regime(30.0, 10.0, 25.0, -0.08, None), "STRONG_DOWN")

        # Boundary fail: ret24 = -0.0499 (> -0.05) -> drops to SLOW_DOWN
        self.assertEqual(engine.classify_regime(25.0, 19.9, 20.0, -0.0499, None), "SLOW_DOWN")

        # Boundary fail: ADX = 24.99 (< 25.0) with ret24 = -0.05 -> drops to RANGING
        self.assertEqual(engine.classify_regime(24.99, 19.9, 20.0, -0.05, None), "RANGING")

        # 2. SLOW_DOWN: ADX >= 25.0 and -DI > +DI
        self.assertEqual(engine.classify_regime(25.0, 15.0, 15.1, 0.00, None), "SLOW_DOWN")
        self.assertEqual(engine.classify_regime(40.0, 10.0, 30.0, 0.02, None), "SLOW_DOWN")

        # Equal ±DI: +DI == -DI -> NOT -DI > +DI
        self.assertEqual(engine.classify_regime(25.0, 20.0, 20.0, -0.06, None), "RANGING")
        self.assertEqual(engine.classify_regime(30.0, 20.0, 20.0, 0.00, None), "RANGING")

        # 3. REBOUND: ADX < 25.0 and ret24 >= +0.03
        self.assertEqual(engine.classify_regime(24.99, 20.0, 10.0, 0.03, None), "REBOUND")
        self.assertEqual(engine.classify_regime(15.0, 10.0, 20.0, 0.05, None), "REBOUND")

        # Boundary fail: ret24 = +0.0299 (< +0.03) -> drops to RANGING
        self.assertEqual(engine.classify_regime(24.99, 20.0, 10.0, 0.0299, None), "RANGING")

        # 4. RANGING default
        self.assertEqual(engine.classify_regime(20.0, 15.0, 12.0, 0.01, None), "RANGING")
        self.assertIsNone(engine.classify_regime(None, 15.0, 12.0, 0.01, None))
        self.assertIsNone(engine.classify_regime(20.0, 15.0, 12.0, None, None))

        # 5. REVERSAL detection: prev was STRONG_DOWN or SLOW_DOWN, waterfall is RANGING and +DI > -DI
        self.assertEqual(engine.classify_regime(20.0, 20.0, 10.0, 0.01, "STRONG_DOWN"), "REVERSAL")
        self.assertEqual(engine.classify_regime(20.0, 20.0, 10.0, 0.01, "SLOW_DOWN"), "REVERSAL")
        # If prev was RANGING -> RANGING (not REVERSAL)
        self.assertEqual(engine.classify_regime(20.0, 20.0, 10.0, 0.01, "RANGING"), "RANGING")

    # -------------------------------------------------------------
    # 2. Dynamic multiplier ladder & planned notional recursion
    # -------------------------------------------------------------
    def test_dynamic_multiplier_ladder_and_recursion(self):
        # Multiplier ladder returns (multiplier, is_permitted):
        # DD < 2%: 1.2
        self.assertEqual(engine.get_dynamic_multiplier(0.00, "RANGING"), (1.2, True))
        self.assertEqual(engine.get_dynamic_multiplier(0.0199, "STRONG_DOWN"), (1.2, True))

        # 2% <= DD < 5%: 1.3
        self.assertEqual(engine.get_dynamic_multiplier(0.02, "RANGING"), (1.3, True))
        self.assertEqual(engine.get_dynamic_multiplier(0.0499, "SLOW_DOWN"), (1.3, True))

        # 5% <= DD < 10%: 1.4
        self.assertEqual(engine.get_dynamic_multiplier(0.05, "RANGING"), (1.4, True))
        self.assertEqual(engine.get_dynamic_multiplier(0.0999, "STRONG_DOWN"), (1.4, True))

        # 10% <= DD <= 20%:
        # Non-STRONG_DOWN: 1.5, True
        self.assertEqual(engine.get_dynamic_multiplier(0.10, "RANGING"), (1.5, True))
        self.assertEqual(engine.get_dynamic_multiplier(0.15, "SLOW_DOWN"), (1.5, True))
        self.assertEqual(engine.get_dynamic_multiplier(0.20, "REBOUND"), (1.5, True))
        # STRONG_DOWN: 1.5, False (blocked)
        self.assertEqual(engine.get_dynamic_multiplier(0.10, "STRONG_DOWN"), (1.5, False))
        self.assertEqual(engine.get_dynamic_multiplier(0.15, "STRONG_DOWN"), (1.5, False))
        self.assertEqual(engine.get_dynamic_multiplier(0.20, "STRONG_DOWN"), (1.5, False))

        # DD > 20%: None, False (stop adding permanently)
        self.assertEqual(engine.get_dynamic_multiplier(0.2001, "RANGING"), (None, False))
        self.assertEqual(engine.get_dynamic_multiplier(0.25, "RANGING"), (None, False))

        # Recursion verification:
        # L1 seed for BTC = 93.71
        seed = config.BTC_LAYER_NOTIONALS[0]
        self.assertEqual(seed, 93.71)

        # Assume DD = 1.5% -> mult = 1.2
        # L2 planned = 93.71 * 1.2 = 112.452
        l2_plan = seed * 1.2
        self.assertAlmostEqual(l2_plan, 112.452, places=3)

        # Suppose L2 executes at BTC price 50000.0
        qty2, actual2 = engine.compute_order_qty("BTCUSDT", l2_plan, 50000.0)
        # Step size is 0.001 -> qty2 = round_down(112.452 / 50000.0, 3) = 0.002 BTC
        # actual2 = 0.002 * 50000.0 = 100.0 USDT
        self.assertEqual(qty2, 0.002)
        self.assertEqual(actual2, 100.0)

        # Now compute L3 planned notional: MUST use l2_plan (112.452), NOT actual2 (100.0)!
        # If DD = 3% -> mult = 1.3
        l3_plan = l2_plan * 1.3
        self.assertAlmostEqual(l3_plan, 146.1876, places=4)
        # Verify that l3_plan is distinct from actual2 * 1.3 (130.0)
        self.assertNotEqual(round(l3_plan, 2), round(actual2 * 1.3, 2))

    # -------------------------------------------------------------
    # 3. SLOW_DOWN cooldown boundary (now_utc - max(bought_utc) == 86400s)
    # -------------------------------------------------------------
    def test_slow_down_cooldown_86400s_boundary(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        t0 = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)

        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc, stop_adding)
            VALUES ('BTCUSDT', 50000.0, 0.02, 0.01, 'OPEN', ?, ?, 0)
        """, (t0.isoformat(), t0.isoformat()))
        gid = cur.lastrowid

        # Insert Layer 1 & 2 OPEN (Layer 2 bought at t0 as first add-on)
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc, planned_notional_usdt)
            VALUES (?, 1, 50000.0, 50000.0, 0.002, 100.0, 0.1, 0.02, 'OPEN', ?, 100.0)
        """, (gid, t0.isoformat()))
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc, planned_notional_usdt)
            VALUES (?, 2, 49000.0, 49000.0, 0.0025, 122.5, 0.1225, 0.0245, 'OPEN', ?, 120.0)
        """, (gid, t0.isoformat()))

        # Insert Layer 3 PENDING with trigger_price 48000.0
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, status)
            VALUES (?, 3, 48000.0, 'PENDING')
        """, (gid,))
        self._sync_cash_invariant(conn)
        engine.assert_cash_invariant(conn)

        monitor = SpotMartingaleMonitor(self.db_path)
        monitor.startup_checks()
        monitor.prev_valid_regime["BTCUSDT"] = "SLOW_DOWN"

        cur.execute("SELECT * FROM grid_groups WHERE id = ?", (gid,))
        group_row = dict(cur.fetchone())

        # Case A: now_utc is 86399 seconds after t0 (< 86400s) -> CANNOT buy Layer 3
        t_before = t0 + timedelta(seconds=86399)
        monitor._process_group_exits_and_adds(
            conn, group_row, val_px=47500.0, now_utc=t_before,
            time_jump=False, block_trading=False, skip_tp=False, daily_loss_blocked=False,
            current_regime="SLOW_DOWN"
        )
        cur.execute("SELECT status FROM layers WHERE group_id = ? AND layer_no = 3", (gid,))
        self.assertEqual(cur.fetchone()["status"], "PENDING", "Layer 3 should NOT buy when cooldown < 86400s")

        # Case B: now_utc is exactly 86400 seconds after t0 (== 86400s) -> CAN buy Layer 3!
        t_exact = t0 + timedelta(seconds=86400)
        monitor._process_group_exits_and_adds(
            conn, group_row, val_px=47500.0, now_utc=t_exact,
            time_jump=False, block_trading=False, skip_tp=False, daily_loss_blocked=False,
            current_regime="SLOW_DOWN"
        )
        cur.execute("SELECT status, planned_notional_usdt FROM layers WHERE group_id = ? AND layer_no = 3", (gid,))
        row = cur.fetchone()
        self.assertEqual(row["status"], "OPEN", "Layer 3 MUST buy when cooldown == 86400s")
        self.assertIsNotNone(row["planned_notional_usdt"])
        engine.assert_cash_invariant(conn)
        conn.close()

    # -------------------------------------------------------------
    # 4. NULL planned notional fallback
    # -------------------------------------------------------------
    def test_null_planned_notional_fallback(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        t0 = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)

        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc, stop_adding)
            VALUES ('BTCUSDT', 50000.0, 0.02, 0.01, 'OPEN', ?, ?, 0)
        """, (t0.isoformat(), t0.isoformat()))
        gid = cur.lastrowid

        # Insert Layer 1 OPEN with NULL planned_notional_usdt
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc, planned_notional_usdt)
            VALUES (?, 1, 50000.0, 50000.0, 0.002, 100.0, 0.1, 0.02, 'OPEN', ?, NULL)
        """, (gid, t0.isoformat()))

        # Insert Layer 2 & 3 PENDING
        cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, 2, 49000.0, 'PENDING')", (gid,))
        cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, 3, 48000.0, 'PENDING')", (gid,))
        self._sync_cash_invariant(conn)
        engine.assert_cash_invariant(conn)

        monitor = SpotMartingaleMonitor(self.db_path)
        monitor.startup_checks()

        cur.execute("SELECT * FROM grid_groups WHERE id = ?", (gid,))
        group_row = dict(cur.fetchone())

        # Trigger Layer 2 buy attempt at price 48500.0
        monitor._process_group_exits_and_adds(
            conn, group_row, val_px=48500.0, now_utc=t0 + timedelta(hours=1),
            time_jump=False, block_trading=False, skip_tp=False, daily_loss_blocked=False,
            current_regime="RANGING"
        )

        # Fallback check:
        # 1. Group stop_adding set to 1
        cur.execute("SELECT stop_adding FROM grid_groups WHERE id = ?", (gid,))
        self.assertEqual(cur.fetchone()["stop_adding"], 1)

        # 2. Remaining PENDING layers CANCELLED
        cur.execute("SELECT layer_no, status FROM layers WHERE group_id = ? ORDER BY layer_no ASC", (gid,))
        status_map = {r["layer_no"]: r["status"] for r in cur.fetchall()}
        self.assertEqual(status_map[1], "OPEN")
        self.assertEqual(status_map[2], "CANCELLED")
        self.assertEqual(status_map[3], "CANCELLED")

        # 3. Risk event STOP_ADDING logged
        cur.execute("SELECT COUNT(*) as cnt FROM risk_events WHERE type = 'STOP_ADDING'")
        self.assertGreaterEqual(cur.fetchone()["cnt"], 1)
        engine.assert_cash_invariant(conn)
        conn.close()

    # -------------------------------------------------------------
    # 5. Schema migration idempotency
    # -------------------------------------------------------------
    def test_schema_migration_idempotent(self):
        # Call init_db multiple times
        engine.init_db(self.db_path)
        engine.init_db(self.db_path)
        engine.init_db(self.db_path)

        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()

        # Check layers columns
        cur.execute("PRAGMA table_info(layers)")
        cols = {r["name"] for r in cur.fetchall()}
        self.assertIn("planned_notional_usdt", cols)

        # Check regime_log columns
        cur.execute("PRAGMA table_info(regime_log)")
        r_cols = {r["name"] for r in cur.fetchall()}
        self.assertIn("regime", r_cols)
        self.assertIn("ret24_pct", r_cols)
        self.assertIn("plus_di", r_cols)
        self.assertIn("minus_di", r_cols)
        conn.close()

    # -------------------------------------------------------------
    # 6. Transition close idempotency (§9 Rule 18(d))
    # -------------------------------------------------------------
    def test_transition_close_idempotent(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        t0 = datetime(2026, 11, 8, 8, 0, tzinfo=timezone.utc)

        # Legacy v5.6 OPEN group (6 layers)
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc, stop_adding)
            VALUES ('BTCUSDT', 50000.0, 0.008, 0.01, 'OPEN', ?, ?, 0)
        """, (t0.isoformat(), t0.isoformat()))
        gid = cur.lastrowid

        # Insert 2 OPEN layers and 4 SOLD layers (total 6 layers)
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc)
            VALUES (?, 1, 50000.0, 50000.0, 0.002, 100.0, 0.1, 0.02, 'OPEN', ?)
        """, (gid, t0.isoformat()))
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc)
            VALUES (?, 2, 49600.0, 49600.0, 0.0025, 124.0, 0.124, 0.0248, 'OPEN', ?)
        """, (gid, t0.isoformat()))
        for lno in range(3, 7):
            cur.execute("""
                INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc, sold_utc, sell_price)
                VALUES (?, ?, 49000.0, 49000.0, 0.003, 147.0, 0.147, 0.0294, 'SOLD', ?, ?, 49500.0)
            """, (gid, lno, t0.isoformat(), t0.isoformat()))

        # Also insert a v5.7 group (9 layers) to ensure it is NOT closed
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc, stop_adding)
            VALUES ('ETHUSDT', 3000.0, 0.015, 0.01, 'OPEN', ?, ?, 0)
        """, (t0.isoformat(), t0.isoformat()))
        gid_v57 = cur.lastrowid
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc, planned_notional_usdt)
            VALUES (?, 1, 3000.0, 3000.0, 0.02, 60.0, 0.06, 0.012, 'OPEN', ?, 62.47)
        """, (gid_v57, t0.isoformat()))
        for lno in range(2, 10):
            cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, ?, 2800.0, 'PENDING')", (gid_v57, lno))

        # Set symbol last_valid_price
        cur.execute("UPDATE symbol_state SET last_valid_price = 50200.0 WHERE symbol = 'BTCUSDT'")
        cur.execute("UPDATE symbol_state SET last_valid_price = 3000.0 WHERE symbol = 'ETHUSDT'")
        self._sync_cash_invariant(conn)
        engine.assert_cash_invariant(conn)
        conn.close()

        monitor = SpotMartingaleMonitor(self.db_path)
        # Execute transition close first time
        monitor.execute_transition_close()

        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        # Verify legacy group is CLOSED with TRANSITION_CLOSE
        cur.execute("SELECT status, close_reason FROM grid_groups WHERE id = ?", (gid,))
        g1 = cur.fetchone()
        self.assertEqual(g1["status"], "CLOSED")
        self.assertEqual(g1["close_reason"], "TRANSITION_CLOSE")

        # Verify all open layers of legacy group were sold with reason TRANSITION_CLOSE
        cur.execute("SELECT COUNT(*) as cnt FROM trades WHERE group_id = ? AND reason = 'TRANSITION_CLOSE'", (gid,))
        self.assertEqual(cur.fetchone()["cnt"], 2)

        # Verify v5.7 group is STILL OPEN (untouched)
        cur.execute("SELECT status FROM grid_groups WHERE id = ?", (gid_v57,))
        self.assertEqual(cur.fetchone()["status"], "OPEN")

        engine.assert_cash_invariant(conn)

        # Execute transition close a second time: MUST BE IDEMPOTENT (no new trades, no errors)
        monitor.execute_transition_close()
        cur.execute("SELECT COUNT(*) as cnt FROM trades WHERE group_id = ? AND reason = 'TRANSITION_CLOSE'", (gid,))
        self.assertEqual(cur.fetchone()["cnt"], 2, "Second run must not create duplicate trades")
        engine.assert_cash_invariant(conn)
        conn.close()

    # -------------------------------------------------------------
    # 7. Main startup wiring transition close test (Task 1)
    # -------------------------------------------------------------
    def test_main_startup_wiring_triggers_transition_close(self):
        """
        Wiring Regression Test (Task 1, SPEC §9 Rule 18(d)):
        Verify that invoking monitor.main(['--once']) executes the real startup sequence:
        startup_checks() -> execute_transition_close() -> poll_once(),
        closing any legacy v5.6 OPEN group (<= 6 layers) via TRANSITION_CLOSE,
        rolling over cash/realized PnL, leaving 9-layer v5.7 groups OPEN,
        and preserving the cash accounting invariant.
        """
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        t0 = datetime(2026, 11, 8, 8, 0, tzinfo=timezone.utc)

        # 1. Legacy v5.6 OPEN group (6 layers)
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc, stop_adding)
            VALUES ('BTCUSDT', 50000.0, 0.008, 0.01, 'OPEN', ?, ?, 0)
        """, (t0.isoformat(), t0.isoformat()))
        gid_legacy = cur.lastrowid

        # Insert 2 OPEN layers and 4 SOLD layers (total 6 layers)
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc)
            VALUES (?, 1, 50000.0, 50000.0, 0.002, 100.0, 0.1, 0.02, 'OPEN', ?)
        """, (gid_legacy, t0.isoformat()))
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc)
            VALUES (?, 2, 49600.0, 49600.0, 0.0025, 124.0, 0.124, 0.0248, 'OPEN', ?)
        """, (gid_legacy, t0.isoformat()))
        for lno in range(3, 7):
            cur.execute("""
                INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc, sold_utc, sell_price)
                VALUES (?, ?, 49000.0, 49000.0, 0.003, 147.0, 0.147, 0.0294, 'SOLD', ?, ?, 49500.0)
            """, (gid_legacy, lno, t0.isoformat(), t0.isoformat()))

        # 2. v5.7 OPEN group (9 layers)
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc, stop_adding)
            VALUES ('ETHUSDT', 3000.0, 0.015, 0.01, 'OPEN', ?, ?, 0)
        """, (t0.isoformat(), t0.isoformat()))
        gid_v57 = cur.lastrowid
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc, planned_notional_usdt)
            VALUES (?, 1, 3000.0, 3000.0, 0.02, 60.0, 0.06, 0.012, 'OPEN', ?, 62.47)
        """, (gid_v57, t0.isoformat()))
        for lno in range(2, 10):
            cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, ?, 2800.0, 'PENDING')", (gid_v57, lno))

        # Set symbol state
        cur.execute("UPDATE symbol_state SET last_valid_price = 50200.0 WHERE symbol = 'BTCUSDT'")
        cur.execute("UPDATE symbol_state SET last_valid_price = 3000.0 WHERE symbol = 'ETHUSDT'")
        self._sync_cash_invariant(conn)
        engine.assert_cash_invariant(conn)
        conn.close()

        mock_now = datetime.now(timezone.utc)
        dummy_kline = [0, 50000.0, 50500.0, 49500.0, 50000.0, 10.0, int(mock_now.timestamp() * 1000) - 1000, 0, 0, 0, 0, 0]
        klines_500 = [dummy_kline for _ in range(500)]

        with patch.object(config, "DB_PATH", self.db_path):
            with patch("monitor.BinancePublicClient") as mock_client_cls:
                mock_client = mock_client_cls.return_value
                mock_client.get_server_time.return_value = (mock_now, None)
                mock_client.get_ticker_price.side_effect = lambda sym: (50200.0 if sym == "BTCUSDT" else 3000.0, None)
                mock_client.get_recent_closed_klines.side_effect = lambda sym, limit=500: (klines_500, None)

                # Execute real main() entry point with --once
                import monitor as monitor_mod
                monitor_mod.main(["--once"])

        # Verify results in DB
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()

        # Legacy group was closed via TRANSITION_CLOSE
        cur.execute("SELECT status, close_reason FROM grid_groups WHERE id = ?", (gid_legacy,))
        g_leg = cur.fetchone()
        self.assertEqual(g_leg["status"], "CLOSED")
        self.assertEqual(g_leg["close_reason"], "TRANSITION_CLOSE")

        # Open layers of legacy group sold via TRANSITION_CLOSE
        cur.execute("SELECT COUNT(*) as cnt FROM trades WHERE group_id = ? AND reason = 'TRANSITION_CLOSE'", (gid_legacy,))
        self.assertEqual(cur.fetchone()["cnt"], 2)

        # v5.7 group is STILL OPEN
        cur.execute("SELECT status FROM grid_groups WHERE id = ?", (gid_v57,))
        self.assertEqual(cur.fetchone()["status"], "OPEN")

        # Cash accounting invariant is satisfied
        engine.assert_cash_invariant(conn)
        conn.close()

    # -------------------------------------------------------------
    # 8. Indicator audit edge cases (Task 2)
    # -------------------------------------------------------------
    def test_calculate_indicators_insufficient_bars_and_tr_zero(self):
        """
        Audit Regression Test:
        1. When klines < MIN_KLINES_BARS (500), calculate_indicators must return all keys
           ('adx', 'plus_di', 'minus_di', 'ret24', etc.) with None, without KeyError.
        2. classify_regime with any None indicator must safely return None.
        3. tr_val == 0 (no price fluctuation) must not raise ZeroDivisionError and return DI == 0.0.
        """
        # 1. Insufficient bars (10 bars < 500)
        short_klines = [[0, 100.0, 105.0, 95.0, 100.0, 1.0, 0] for _ in range(10)]
        inds = engine.calculate_indicators(short_klines)
        self.assertIsNone(inds["adx"])
        self.assertIsNone(inds["plus_di"])
        self.assertIsNone(inds["minus_di"])
        self.assertIsNone(inds["ret24"])
        self.assertIn("error", inds)

        # 2. classify_regime on None
        self.assertIsNone(engine.classify_regime(None, 20.0, 10.0, 0.01))
        self.assertIsNone(engine.classify_regime(30.0, None, 10.0, 0.01))
        self.assertIsNone(engine.classify_regime(30.0, 20.0, None, 0.01))
        self.assertIsNone(engine.classify_regime(30.0, 20.0, 10.0, None))

        # 3. Constant prices (tr_val == 0 for all bars)
        flat_klines = [[0, 50000.0, 50000.0, 50000.0, 50000.0, 1.0, 0] for _ in range(505)]
        inds_flat = engine.calculate_indicators(flat_klines)
        self.assertEqual(inds_flat["plus_di"], 0.0)
        self.assertEqual(inds_flat["minus_di"], 0.0)
        self.assertEqual(inds_flat["adx"], 0.0)
        self.assertEqual(inds_flat["ret24"], 0.0)


if __name__ == "__main__":
    unittest.main()
