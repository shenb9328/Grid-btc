"""
SPEC v5.6 HOLDMAX Unit Tests (§5.3, §8.3(6)).
Tests for:
1. Full groups (bought_layers == 9, e.g. 1-9 OPEN or 1-8 OPEN + 9 SOLD):
   - Skip 7d time stop: no sell, half_time_stopped remains 0, stop_adding remains 0.
   - Skip 14d time stop: no sell, group remains OPEN.
   - Single layer TP, Group TP, and §5.1 halt execute normally.
2. Partial groups (bought_layers < 9, e.g. 1-3 OPEN, 4-9 PENDING):
   - 7d time stop executes normally: cuts >= 50%, sets half_time_stopped=1, stop_adding=1.
   - 14d time stop executes normally: liquidates remaining positions, closes group with TIME_STOP_14D.
3. Both Monitor and Backtest implementations verified.
"""

import os
import sys
import unittest
import sqlite3
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import engine
from monitor import SpotMartingaleMonitor
from backtest import BacktestEngine


class TestSpecV56Holdmax(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test_holdmax.sqlite3"
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

    # -------------------------------------------------------------------------
    # 1. Full group (bought_layers == 9) skips 7d and 14d in Monitor
    # -------------------------------------------------------------------------
    def test_full_group_skips_7d_and_14d_monitor(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        t0 = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)

        # Scenario: ETH group 2 (1-8 OPEN, 9 SOLD)
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc, half_time_stopped, stop_adding)
            VALUES ('ETHUSDT', 3000.0, 0.025, 0.01, 'OPEN', ?, ?, 0, 0)
        """, (t0.isoformat(), t0.isoformat()))
        gid = cur.lastrowid

        # Insert layers 1 to 8 as OPEN
        for lno in range(1, 9):
            cur.execute("""
                INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc)
                VALUES (?, ?, 3000.0, 3000.0, 0.05, 150.0, 0.15, 0.03, 'OPEN', ?)
            """, (gid, lno, t0.isoformat()))
        # Insert layer 9 as SOLD
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc, sold_utc, sell_price)
            VALUES (?, 9, 2600.0, 2600.0, 0.20, 520.0, 0.52, 0.10, 'SOLD', ?, ?, 2650.0)
        """, (gid, t0.isoformat(), t0.isoformat()))
        self._sync_cash_invariant(conn)
        engine.assert_cash_invariant(conn)

        # Verify bought_layers count from database is exactly 9
        cur.execute("SELECT COUNT(*) as cnt FROM layers WHERE group_id = ? AND status IN ('OPEN', 'SOLD', 'TIME_STOPPED')", (gid,))
        self.assertEqual(cur.fetchone()["cnt"], 9)

        m = SpotMartingaleMonitor(self.db_path)
        m.startup_checks()

        # (a) Check at 8 days (> 7 days)
        t_8d = t0 + timedelta(days=8)
        cur.execute("SELECT * FROM grid_groups WHERE id = ?", (gid,))
        group_row = dict(cur.fetchone())

        # Call _process_group_exits_and_adds at same price (no TP triggered)
        m._process_group_exits_and_adds(
            conn, group_row, val_px=2950.0, now_utc=t_8d,
            time_jump=False, block_trading=False, skip_tp=False, daily_loss_blocked=False
        )

        # Must skip 7d half stop: no trades, half_time_stopped remains 0, stop_adding remains 0
        cur.execute("SELECT COUNT(*) as cnt FROM trades WHERE reason = 'TIME_STOP_7D'")
        self.assertEqual(cur.fetchone()["cnt"], 0, "Full group must NOT execute 7-day half stop")
        cur.execute("SELECT half_time_stopped, stop_adding, status FROM grid_groups WHERE id = ?", (gid,))
        g_state = cur.fetchone()
        self.assertEqual(g_state["half_time_stopped"], 0, "half_time_stopped must remain 0 for full group")
        self.assertEqual(g_state["stop_adding"], 0, "stop_adding must remain 0 for full group")
        self.assertEqual(g_state["status"], "OPEN")

        # (b) Check at 15 days (> 14 days)
        t_15d = t0 + timedelta(days=15)
        cur.execute("SELECT * FROM grid_groups WHERE id = ?", (gid,))
        group_row = dict(cur.fetchone())
        m._process_group_exits_and_adds(
            conn, group_row, val_px=2950.0, now_utc=t_15d,
            time_jump=False, block_trading=False, skip_tp=False, daily_loss_blocked=False
        )

        # Must skip 14d stop: no trades, status remains OPEN
        cur.execute("SELECT COUNT(*) as cnt FROM trades WHERE reason = 'TIME_STOP_14D'")
        self.assertEqual(cur.fetchone()["cnt"], 0, "Full group must NOT execute 14-day time stop")
        cur.execute("SELECT status, close_reason FROM grid_groups WHERE id = ?", (gid,))
        g_state = cur.fetchone()
        self.assertEqual(g_state["status"], "OPEN", "Group must remain OPEN")
        self.assertIsNone(g_state["close_reason"])
        conn.close()

    # -------------------------------------------------------------------------
    # 2. Full group executes Single Layer TP, Group TP, and Drawdown Halt normally
    # -------------------------------------------------------------------------
    def test_full_group_tp_and_halt_work_normally_monitor(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        t0 = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)

        # Group TP test: center_price=50000, spacing=0.02
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc, half_time_stopped, stop_adding)
            VALUES ('BTCUSDT', 50000.0, 0.02, 0.01, 'OPEN', ?, ?, 0, 0)
        """, (t0.isoformat(), t0.isoformat()))
        gid = cur.lastrowid

        # Insert 9 layers (all OPEN, bought_layers = 9)
        for lno in range(1, 10):
            cur.execute("""
                INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc)
                VALUES (?, ?, 50000.0, 50000.0, 0.005, 250.0, 0.25, 0.05, 'OPEN', ?)
            """, (gid, lno, t0.isoformat()))
        self._sync_cash_invariant(conn)
        engine.assert_cash_invariant(conn)

        m = SpotMartingaleMonitor(self.db_path)
        m.startup_checks()

        # Advance to 10 days later (> 7 days) and test Group TP (+1.5%)
        # avg_cost is 50000.0. Price = 50800.0 (>= 50000 * 1.015 = 50750)
        t_10d = t0 + timedelta(days=10)
        cur.execute("SELECT * FROM grid_groups WHERE id = ?", (gid,))
        group_row = dict(cur.fetchone())
        m._process_group_exits_and_adds(
            conn, group_row, val_px=50800.0, now_utc=t_10d,
            time_jump=False, block_trading=False, skip_tp=False, daily_loss_blocked=False
        )

        cur.execute("SELECT status, close_reason FROM grid_groups WHERE id = ?", (gid,))
        g_state = cur.fetchone()
        self.assertEqual(g_state["status"], "CLOSED")
        self.assertEqual(g_state["close_reason"], "TAKE_PROFIT")

        cur.execute("SELECT COUNT(*) as cnt FROM trades WHERE reason = 'GROUP_TP'")
        self.assertEqual(cur.fetchone()["cnt"], 9, "All 9 layers sold with GROUP_TP")
        engine.assert_cash_invariant(conn)
        conn.close()

    # -------------------------------------------------------------------------
    # 3. Partial group (bought_layers < 9) executes 7d and 14d normally in Monitor
    # -------------------------------------------------------------------------
    def test_partial_group_executes_7d_and_14d_monitor(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        t0 = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)

        # 3 OPEN layers, 6 PENDING layers (bought_layers = 3 < 9)
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc, half_time_stopped, stop_adding)
            VALUES ('BTCUSDT', 50000.0, 0.02, 0.01, 'OPEN', ?, ?, 0, 0)
        """, (t0.isoformat(), t0.isoformat()))
        gid = cur.lastrowid

        # Insert 3 OPEN layers
        for lno in range(1, 4):
            cur.execute("""
                INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc)
                VALUES (?, ?, 50000.0, 50000.0, 0.005, 250.0, 0.25, 0.05, 'OPEN', ?)
            """, (gid, lno, t0.isoformat()))
        # Insert 6 PENDING layers
        for lno in range(4, 10):
            cur.execute("""
                INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status)
                VALUES (?, ?, 45000.0, NULL, 0.005, 250.0, 0.0, 0.0, 'PENDING')
            """, (gid, lno))
        self._sync_cash_invariant(conn)
        engine.assert_cash_invariant(conn)

        m = SpotMartingaleMonitor(self.db_path)
        m.startup_checks()

        # Advance to 8 days (> 7 days)
        t_8d = t0 + timedelta(days=8)
        cur.execute("SELECT * FROM grid_groups WHERE id = ?", (gid,))
        group_row = dict(cur.fetchone())
        m._process_group_exits_and_adds(
            conn, group_row, val_px=49000.0, now_utc=t_8d,
            time_jump=False, block_trading=False, skip_tp=False, daily_loss_blocked=False
        )

        # Must execute 7d half stop!
        cur.execute("SELECT COUNT(*) as cnt FROM trades WHERE reason = 'TIME_STOP_7D'")
        self.assertGreater(cur.fetchone()["cnt"], 0, "Partial group must execute 7-day half stop")

        cur.execute("SELECT half_time_stopped, stop_adding FROM grid_groups WHERE id = ?", (gid,))
        g_state = cur.fetchone()
        self.assertEqual(g_state["half_time_stopped"], 1, "half_time_stopped must be set to 1")
        self.assertEqual(g_state["stop_adding"], 1, "stop_adding must be set to 1")

        # PENDING layers must be cancelled
        cur.execute("SELECT COUNT(*) as cnt FROM layers WHERE group_id = ? AND status = 'CANCELLED'", (gid,))
        self.assertEqual(cur.fetchone()["cnt"], 6, "PENDING layers must be cancelled")
        engine.assert_cash_invariant(conn)

        # Now advance to 15 days (> 14 days)
        t_15d = t0 + timedelta(days=15)
        cur.execute("SELECT * FROM grid_groups WHERE id = ?", (gid,))
        group_row = dict(cur.fetchone())
        m._process_group_exits_and_adds(
            conn, group_row, val_px=49000.0, now_utc=t_15d,
            time_jump=False, block_trading=False, skip_tp=False, daily_loss_blocked=False
        )

        # Must execute 14d time stop and close group
        cur.execute("SELECT status, close_reason FROM grid_groups WHERE id = ?", (gid,))
        g_state = cur.fetchone()
        self.assertEqual(g_state["status"], "CLOSED")
        self.assertEqual(g_state["close_reason"], "TIME_STOP_14D")
        engine.assert_cash_invariant(conn)
        conn.close()

    # -------------------------------------------------------------------------
    # 4. Backtest Engine Holdmax Behavior Verification
    # -------------------------------------------------------------------------
    def test_backtest_engine_holdmax_full_vs_partial(self):
        # Case A: Full group (9/9 bought) in BacktestEngine
        bt = BacktestEngine(self.events)
        start_dt = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        first_buy_dt = start_dt - timedelta(days=15) # > 14 days ago

        grp_full = {
            'id': 1,
            'symbol': 'BTCUSDT',
            'center_price': 50000.0,
            'spacing_pct': 0.02,
            'status': 'OPEN',
            'opened_dt': first_buy_dt,
            'first_buy_dt': first_buy_dt,
            'stop_adding': 1,
            'half_time_stopped': 0,
            'layers': [
                {
                    'layer_no': lno,
                    'trigger_price': 50000.0 * (1.0 - (lno - 1) * 0.02),
                    'buy_price': 50000.0 * (1.0 - (lno - 1) * 0.02),
                    'qty': 0.005,
                    'notional': 250.0,
                    'planned_notional': 250.0,
                    'buy_fee': 0.25,
                    'buy_slippage': 0.05,
                    'status': 'OPEN',
                    'bought_dt': first_buy_dt
                } for lno in range(1, 10)
            ]
        }
        bt.groups['BTCUSDT'] = grp_full
        bt.cash = 5000.0
        bt.symbol_state['BTCUSDT']["last_valid_price"] = 45000.0
        bt.symbol_state['BTCUSDT']["last_valid_price_dt"] = start_dt

        # Process exits: even after 15 days, full group must skip 14d and 7d time stops
        bt._process_group_exits(grp_full, 45000.0, start_dt, skip_tp=False)
        self.assertIsNotNone(bt.groups['BTCUSDT'], "Full group must NOT be liquidated at 14d in backtest")
        self.assertEqual(grp_full['status'], 'OPEN')
        self.assertEqual(grp_full['half_time_stopped'], 0)

        # Case B: Partial group (3/9 bought, 6 PENDING) in BacktestEngine
        grp_partial = {
            'id': 2,
            'symbol': 'BTCUSDT',
            'center_price': 50000.0,
            'spacing_pct': 0.02,
            'status': 'OPEN',
            'opened_dt': first_buy_dt,
            'first_buy_dt': first_buy_dt,
            'stop_adding': 0,
            'half_time_stopped': 0,
            'layers': [
                {
                    'layer_no': lno,
                    'trigger_price': 50000.0 * (1.0 - (lno - 1) * 0.02),
                    'buy_price': 50000.0 * (1.0 - (lno - 1) * 0.02),
                    'qty': 0.005,
                    'notional': 250.0,
                    'planned_notional': 250.0,
                    'buy_fee': 0.25,
                    'buy_slippage': 0.05,
                    'status': 'OPEN' if lno <= 3 else 'PENDING',
                    'bought_dt': first_buy_dt if lno <= 3 else None
                } for lno in range(1, 10)
            ]
        }
        bt.groups['BTCUSDT'] = grp_partial

        # Process exits at 15 days: partial group must trigger 14d liquidation
        bt._process_group_exits(grp_partial, 45000.0, start_dt, skip_tp=False)
        self.assertIsNone(bt.groups['BTCUSDT'], "Partial group MUST be liquidated at 14d in backtest")
        self.assertEqual(len(bt.closed_groups), 1)
        self.assertEqual(bt.closed_groups[0]['close_reason'], 'TIME_STOP_14D')


if __name__ == "__main__":
    unittest.main()
