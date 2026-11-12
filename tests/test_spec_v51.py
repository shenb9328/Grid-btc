"""
Comprehensive Unit Test Suite for Spot Martingale (SPEC v5.1).
Covering all 12 items specified in §8.3(6).
"""

import unittest
import sqlite3
import os
import shutil
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import config
import engine
from monitor import SpotMartingaleMonitor

class TestSpecV51Requirements(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test_martingale.sqlite3"
        engine.init_db(self.db_path)
        self.events_version, self.events_sha, self.events = engine.load_events_calendar(config.EVENTS_CSV_PATH)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------
    # 1. 层金额求和 = 上限（BTC 3,000.00、ETH 2,000.00，误差 0.00）
    # -------------------------------------------------------------
    def test_item_01_layer_notionals_sum(self):
        btc_sum = round(sum(config.BTC_LAYER_NOTIONALS), 2)
        eth_sum = round(sum(config.ETH_LAYER_NOTIONALS), 2)
        self.assertEqual(btc_sum, 3000.00, f"BTC layer sum {btc_sum} != 3000.00")
        self.assertEqual(eth_sum, 2000.00, f"ETH layer sum {eth_sum} != 2000.00")
        self.assertEqual(len(config.BTC_LAYER_NOTIONALS), 6)
        self.assertEqual(len(config.ETH_LAYER_NOTIONALS), 6)

    # -------------------------------------------------------------
    # 2. 层数上限 6 不可突破
    # -------------------------------------------------------------
    def test_item_02_max_layers_capped_at_six(self):
        self.assertEqual(config.MAX_LAYERS, 6)
        # Attempting layer 7 should raise or not exist
        with self.assertRaises(IndexError):
            _ = config.BTC_LAYER_NOTIONALS[6]
        with self.assertRaises(IndexError):
            _ = config.ETH_LAYER_NOTIONALS[6]

        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        # Open group
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc)
            VALUES ('BTCUSDT', 50000.0, 0.015, 0.01, 'OPEN', '2026-11-06T00:00:00Z')
        """)
        gid = cur.lastrowid
        for lno in range(1, 7):
            cur.execute("""
                INSERT INTO layers (group_id, layer_no, trigger_price, status)
                VALUES (?, ?, ?, 'PENDING')
            """, (gid, lno, 50000.0 * (1 - (lno - 1) * 0.015)))
        conn.commit()

        # Count layers for this group
        cur.execute("SELECT COUNT(*) as cnt FROM layers WHERE group_id = ?", (gid,))
        self.assertEqual(cur.fetchone()["cnt"], 6)
        conn.close()

    # -------------------------------------------------------------
    # 3. 回撤 15% 触发 halt 且恢复后不立即再触发（含 halt 期间每轮不重触发、恢复轮跳过 §5.1 检查）
    # -------------------------------------------------------------
    def test_item_03_drawdown_halt_and_recovery(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        # Set peak equity to 10,000, initial cash to 5,000, and 1 open layer bought at 50,000 with 0.1 BTC (value 5,000)
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc)
            VALUES ('BTCUSDT', 50000.0, 0.015, 0.01, 'OPEN', '2026-11-06T00:00:00Z', '2026-11-06T00:00:00Z')
        """)
        gid = cur.lastrowid
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc)
            VALUES (?, 1, 50000.0, 50000.0, 0.1, 5000.0, 5.0, 1.0, 'OPEN', '2026-11-06T00:00:00Z')
        """, (gid,))
        cur.execute("UPDATE account SET cash_usdt = 4994.0, initial_usdt = 10000.0, peak_equity_usdt = 10000.0 WHERE id = 1")
        conn.commit()

        monitor = SpotMartingaleMonitor(self.db_path)
        monitor.startup_checks()

        # Mock price plunge to 30,000 (BTC position value drops from 5000 to 3000, equity = 4994 + 3000 = 7994 -> drawdown 20.06% >= 15%)
        cur_sim_time = [datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)]
        monitor.client.get_ticker_price = lambda sym: (30000.0 if sym == "BTCUSDT" else 2000.0, None)
        monitor.client.get_server_time = lambda: (cur_sim_time[0], None)
        monitor.client.get_recent_closed_klines = lambda sym, limit: ([[0, 0, 0, 0, 30000.0, 0, 1]] * limit, None)

        now1 = cur_sim_time[0]
        mono1 = 100.0
        monitor.poll_once(now1, mono1)

        # Verify halt state
        cur.execute("SELECT halt_active, halt_state, halted_until_utc, peak_equity_usdt FROM account WHERE id = 1")
        acc1 = cur.fetchone()
        self.assertEqual(acc1["halt_active"], 1)
        self.assertEqual(acc1["halt_state"], "HALTED")
        self.assertIsNotNone(acc1["halted_until_utc"])

        # Run another poll while still in cooldown period (+60 seconds) -> must NOT retrigger or change halt state
        now2 = now1 + timedelta(seconds=60)
        mono2 = mono1 + 60.0
        cur_sim_time[0] = now2
        monitor.poll_once(now2, mono2)
        cur.execute("SELECT halt_active, halt_state FROM account WHERE id = 1")
        acc2 = cur.fetchone()
        self.assertEqual(acc2["halt_active"], 1)
        self.assertEqual(acc2["halt_state"], "HALTED")

        # Now test expiration: advance past 24 hours (e.g. +25 hours) without time jump
        now3 = now1 + timedelta(hours=25)
        mono3 = mono1 + 25 * 3600.0
        cur_sim_time[0] = now3
        monitor.poll_once(now3, mono3)

        cur.execute("SELECT halt_active, halt_state, peak_equity_usdt, last_equity_usdt FROM account WHERE id = 1")
        acc3 = cur.fetchone()
        self.assertEqual(acc3["halt_active"], 0)
        self.assertEqual(acc3["halt_state"], "NONE")
        # Peak equity must have been reset to current equity (recovery round skips §5.1 check)
        self.assertAlmostEqual(acc3["peak_equity_usdt"], acc3["last_equity_usdt"], places=2)
        conn.close()

    # -------------------------------------------------------------
    # 4. halt 清仓失败保持 HALTING 并续清（含独立事务提交状态、逐层进度、连续 3 轮零进展停交易）
    # -------------------------------------------------------------
    def test_item_04_halting_failure_resumption_and_zero_progress(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        # Create 2 open layers
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc)
            VALUES ('BTCUSDT', 50000.0, 0.015, 0.01, 'OPEN', '2026-11-06T00:00:00Z')
        """)
        gid = cur.lastrowid
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status)
            VALUES (?, 1, 50000.0, 50000.0, 0.01, 500.0, 0.5, 0.1, 'OPEN')
        """, (gid,))
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status)
            VALUES (?, 2, 49250.0, 49250.0, 0.01, 492.5, 0.5, 0.1, 'OPEN')
        """, (gid,))
        # Set account in HALTING state with zero progress = 0 and halt_triggered_utc = 00:30:00Z
        trig_utc = datetime(2026, 11, 6, 0, 30, tzinfo=timezone.utc)
        cur.execute("""
            UPDATE account
            SET cash_usdt = 9006.3, halt_state = 'HALTING', halt_active = 0, halt_triggered_utc = ?,
                halt_progress_total_layers = 2,
                halt_progress_sold_layers = 0, halt_zero_progress_rounds = 0
            WHERE id = 1
        """, (trig_utc.isoformat(),))
        cur.execute("UPDATE symbol_state SET last_valid_price = 50000.0 WHERE symbol = 'BTCUSDT'")
        cur.execute("UPDATE symbol_state SET last_valid_price = 2000.0 WHERE symbol = 'ETHUSDT'")
        conn.commit()

        monitor = SpotMartingaleMonitor(self.db_path)
        monitor.startup_checks()
        now = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)
        cur_sim_time = [now]
        monitor.client.get_ticker_price = lambda sym: (50000.0 if sym == "BTCUSDT" else 2000.0, None)
        monitor.client.get_server_time = lambda: (cur_sim_time[0], None)
        monitor.client.get_recent_closed_klines = lambda sym, limit: ([[0, 0, 0, 0, 50000.0, 0, 1]] * limit, None)

        # Mock liquidation failure by temporarily hooking _liquidate_layer to raise exception
        orig_liquidate = monitor._liquidate_layer
        fail_count = [0]
        def mock_liquidate(conn, lay, sell_px, now_utc, reason):
            fail_count[0] += 1
            raise sqlite3.OperationalError("Simulated disk error or write failure")

        monitor._liquidate_layer = mock_liquidate

        # Poll round 1: failure -> halt_zero_progress_rounds becomes 1, halt_state remains HALTING
        cur_sim_time[0] = now
        monitor.poll_once(now, 100.0)

        cur.execute("SELECT halt_state, halt_zero_progress_rounds, halt_progress_sold_layers FROM account WHERE id = 1")
        r1 = cur.fetchone()
        self.assertEqual(r1["halt_state"], "HALTING")
        self.assertEqual(r1["halt_zero_progress_rounds"], 1)
        self.assertEqual(r1["halt_progress_sold_layers"], 0)

        # Poll round 2: failure -> halt_zero_progress_rounds becomes 2
        cur_sim_time[0] = now + timedelta(minutes=1)
        monitor.poll_once(now + timedelta(minutes=1), 160.0)
        cur.execute("SELECT halt_zero_progress_rounds FROM account WHERE id = 1")
        self.assertEqual(cur.fetchone()["halt_zero_progress_rounds"], 2)

        # Poll round 3: failure -> halt_zero_progress_rounds becomes 3 (stops trading)
        cur_sim_time[0] = now + timedelta(minutes=2)
        monitor.poll_once(now + timedelta(minutes=2), 220.0)
        cur.execute("SELECT halt_zero_progress_rounds FROM account WHERE id = 1")
        self.assertEqual(cur.fetchone()["halt_zero_progress_rounds"], 3)

        # Now restore original liquidation method and poll round 4: liquidation succeeds, zero progress clears to 0
        monitor._liquidate_layer = orig_liquidate
        cur_sim_time[0] = now + timedelta(minutes=3)
        monitor.poll_once(now + timedelta(minutes=3), 280.0)

        cur.execute("""
            SELECT halt_state, halt_active, halt_zero_progress_rounds, halt_progress_sold_layers,
                   halted_until_utc, halt_trigger_equity_usdt, last_equity_usdt
            FROM account WHERE id = 1
        """)
        r4 = cur.fetchone()
        self.assertEqual(r4["halt_state"], "HALTED")
        self.assertEqual(r4["halt_active"], 1)
        self.assertEqual(r4["halt_zero_progress_rounds"], 0)
        self.assertEqual(r4["halt_progress_sold_layers"], 2)
        # P1-2 check: halted_until_utc must be halt_triggered_utc + 24h (not completion time + 24h)
        self.assertEqual(r4["halted_until_utc"], (trig_utc + timedelta(hours=24)).isoformat())
        self.assertIsNotNone(r4["halt_trigger_equity_usdt"])
        self.assertAlmostEqual(r4["halt_trigger_equity_usdt"], r4["last_equity_usdt"], places=2)
        conn.close()

    # -------------------------------------------------------------
    # 5. 事件过滤在事件前 24h 内与事件后 2h 内禁止开组
    # -------------------------------------------------------------
    def test_item_05_economic_events_filter(self):
        # Pick an event from loaded events
        test_ev = self.events[0]
        ev_dt = test_ev[0]

        # Case 1: 25 hours before event -> allowed
        dt_pre_25h = ev_dt - timedelta(hours=25)
        ev_24h, ev_past_2h, is_stale, _ = engine.check_event_filter(self.events, dt_pre_25h, is_simulation=True)
        self.assertFalse(ev_24h, "Should not be in 24h pre-window at T-25h")

        # Case 2: 23 hours before event -> blocked (ev_24h = True)
        dt_pre_23h = ev_dt - timedelta(hours=23)
        ev_24h, ev_past_2h, is_stale, _ = engine.check_event_filter(self.events, dt_pre_23h, is_simulation=True)
        self.assertTrue(ev_24h, "Must be blocked at T-23h (future 24h)")

        # Case 3: Exactly 1 hour after event -> blocked (ev_past_2h = True)
        dt_post_1h = ev_dt + timedelta(hours=1)
        ev_24h, ev_past_2h, is_stale, _ = engine.check_event_filter(self.events, dt_post_1h, is_simulation=True)
        self.assertTrue(ev_past_2h, "Must be blocked at T+1h (past 2h)")

        # Case 4: 2.5 hours after event -> allowed (both False)
        dt_post_2_5h = ev_dt + timedelta(hours=2, minutes=30)
        ev_24h, ev_past_2h, is_stale, _ = engine.check_event_filter(self.events, dt_post_2_5h, is_simulation=True)
        self.assertFalse(ev_24h)
        self.assertFalse(ev_past_2h)

    # -------------------------------------------------------------
    # 6. 重启恢复不重复建组、不重复执行 7 天减半
    # -------------------------------------------------------------
    def test_item_06_restart_idempotency_and_half_time_stop(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        t0 = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc, half_time_stopped, stop_adding)
            VALUES ('BTCUSDT', 50000.0, 0.015, 0.01, 'OPEN', ?, ?, 1, 1)
        """, (t0.isoformat(), t0.isoformat()))
        gid = cur.lastrowid
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status, bought_utc)
            VALUES (?, 1, 50000.0, 50000.0, 0.004, 200.0, 0.2, 0.04, 'OPEN', ?)
        """, (gid, t0.isoformat()))
        conn.commit()

        # First monitor instance
        m1 = SpotMartingaleMonitor(self.db_path)
        m1.startup_checks()

        # Simulate 8 days later (more than 7 days)
        now = t0 + timedelta(days=8)
        m1.client.get_ticker_price = lambda sym: (50000.0, None)
        m1.client.get_server_time = lambda: (now, None)
        m1.client.get_recent_closed_klines = lambda sym, limit: ([[0, 0, 0, 0, 50000.0, 0, 1]] * limit, None)

        m1.poll_once(now, 100.0)

        # Check trades count: half_time_stopped was already 1, so no TIME_STOP_7D trade should be generated!
        cur.execute("SELECT COUNT(*) as cnt FROM trades WHERE reason = 'TIME_STOP_7D'")
        self.assertEqual(cur.fetchone()["cnt"], 0, "Must not re-execute 7-day half stop if half_time_stopped=1")

        # Second monitor instance (simulate process restart)
        m2 = SpotMartingaleMonitor(self.db_path)
        m2.startup_checks()
        m2.client.get_ticker_price = lambda sym: (50000.0, None)
        m2.client.get_server_time = lambda: (now + timedelta(minutes=1), None)
        m2.client.get_recent_closed_klines = lambda sym, limit: ([[0, 0, 0, 0, 50000.0, 0, 1]] * limit, None)

        m2.poll_once(now + timedelta(minutes=1), 160.0)
        # Check OPEN groups count: exactly 1 group, no duplicate group created
        cur.execute("SELECT COUNT(*) as cnt FROM grid_groups WHERE symbol = 'BTCUSDT' AND status = 'OPEN'")
        self.assertEqual(cur.fetchone()["cnt"], 1, "Must not create duplicate group on restart")
        conn.close()

    # -------------------------------------------------------------
    # 7. 成本模型每笔单边 = 0.12%
    # -------------------------------------------------------------
    def test_item_07_cost_model_one_side(self):
        self.assertEqual(config.FEE_RATE, 0.0010)
        self.assertEqual(config.SLIPPAGE_RATE, 0.0002)
        total_one_side = config.FEE_RATE + config.SLIPPAGE_RATE
        self.assertAlmostEqual(total_one_side, 0.0012, places=6)

        notional = 1000.0
        fee = round(notional * config.FEE_RATE, 4)
        slippage = round(notional * config.SLIPPAGE_RATE, 4)
        self.assertEqual(fee, 1.00)
        self.assertEqual(slippage, 0.20)
        self.assertEqual(fee + slippage, 1.20)

    # -------------------------------------------------------------
    # 8. realized_pnl 满足 §1.4 现金不变量
    # -------------------------------------------------------------
    def test_item_08_cash_invariant(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()

        # Initial: cash = 10000, initial = 10000, realized = 0
        engine.assert_cash_invariant(conn)

        # Buy Layer 1: notional = 200, fee = 0.2, slippage = 0.04
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc)
            VALUES ('BTCUSDT', 50000.0, 0.015, 0.01, 'OPEN', '2026-11-06T00:00:00Z')
        """)
        gid = cur.lastrowid
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status)
            VALUES (?, 1, 50000.0, 50000.0, 0.004, 200.0, 0.2, 0.04, 'OPEN')
        """, (gid,))
        cur.execute("UPDATE account SET cash_usdt = 10000.0 - 200.24 WHERE id = 1")
        conn.commit()
        engine.assert_cash_invariant(conn)

        # Sell Layer 1 at profit 51000: gross = 204.0, fee = 0.204, slip = 0.0408, net_in = 203.7552
        # Realized = 4.0 - 0.24 - 0.2448 = 3.5152
        cur.execute("UPDATE layers SET status = 'SOLD', sell_price = 51000.0 WHERE group_id = ? AND layer_no = 1", (gid,))
        cur.execute("""
            UPDATE account
            SET cash_usdt = cash_usdt + 203.7552,
                realized_pnl_usdt = realized_pnl_usdt + 3.5152
            WHERE id = 1
        """)
        conn.commit()
        engine.assert_cash_invariant(conn)
        conn.close()

    # -------------------------------------------------------------
    # 9. 同轮跌破多层依次买齐
    # -------------------------------------------------------------
    def test_item_09_multi_layer_plunge_buy_all(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        # Create group with Layer 1 OPEN at 50,000, spacing 1.5%
        # L2 trig = 49250, L3 trig = 48500, L4 trig = 47750
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc)
            VALUES ('BTCUSDT', 50000.0, 0.015, 0.01, 'OPEN', '2026-11-06T00:00:00Z', '2026-11-06T00:00:00Z')
        """)
        gid = cur.lastrowid
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status)
            VALUES (?, 1, 50000.0, 50000.0, 0.004, 200.0, 0.2, 0.04, 'OPEN')
        """, (gid,))
        for lno in range(2, 7):
            trig = 50000.0 * (1.0 - (lno - 1) * 0.015)
            cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, ?, ?, 'PENDING')", (gid, lno, trig))
        cur.execute("UPDATE account SET cash_usdt = 10000.0 - 200.24 WHERE id = 1")
        conn.commit()

        monitor = SpotMartingaleMonitor(self.db_path)
        monitor.startup_checks()

        # Price plunges in one round to 47,000 (below L2 49250, L3 48500, L4 47750, but above 0.85*C = 42500)
        monitor.client.get_ticker_price = lambda sym: (47000.0 if sym == "BTCUSDT" else 2000.0, None)
        monitor.client.get_server_time = lambda: (datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc), None)
        monitor.client.get_recent_closed_klines = lambda sym, limit: ([[0, 0, 0, 0, 47000.0, 0, 1]] * limit, None)

        now = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)
        monitor.poll_once(now, 100.0)

        # Verify that L2, L3, L4 were all bought in this round!
        cur.execute("SELECT layer_no, status FROM layers WHERE group_id = ? ORDER BY layer_no ASC", (gid,))
        layers = {r["layer_no"]: r["status"] for r in cur.fetchall()}
        self.assertEqual(layers[1], "OPEN")
        self.assertEqual(layers[2], "OPEN", "Layer 2 should be bought")
        self.assertEqual(layers[3], "OPEN", "Layer 3 should be bought")
        self.assertEqual(layers[4], "OPEN", "Layer 4 should be bought")
        self.assertEqual(layers[5], "PENDING", "Layer 5 trig 47000 was not strictly breached (47000 < 47000 is False)")
        conn.close()

    # -------------------------------------------------------------
    # 10. OPEN 层为 0 立即关组
    # -------------------------------------------------------------
    def test_item_10_zero_open_layers_immediately_closes_group(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc)
            VALUES ('BTCUSDT', 50000.0, 0.015, 0.01, 'OPEN', '2026-11-06T00:00:00Z', '2026-11-06T00:00:00Z')
        """)
        gid = cur.lastrowid
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status)
            VALUES (?, 1, 50000.0, 50000.0, 0.004, 200.0, 0.2, 0.04, 'OPEN')
        """, (gid,))
        cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, 2, 49250.0, 'PENDING')", (gid,))
        cur.execute("UPDATE account SET cash_usdt = 10000.0 - 200.24 WHERE id = 1")
        conn.commit()

        monitor = SpotMartingaleMonitor(self.db_path)
        monitor.startup_checks()

        # Price rises to 51,000 (>= TP_1 = 50000 * 1.015 = 50750) -> triggers TP for Layer 1
        monitor.client.get_ticker_price = lambda sym: (51000.0 if sym == "BTCUSDT" else 2000.0, None)
        monitor.client.get_server_time = lambda: (datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc), None)
        monitor.client.get_recent_closed_klines = lambda sym, limit: ([[0, 0, 0, 0, 51000.0, 0, 1]] * limit, None)

        now = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)
        monitor.poll_once(now, 100.0)

        # Verify group is CLOSED and remaining PENDING layer is CANCELLED in same transaction
        cur.execute("SELECT status, close_reason FROM grid_groups WHERE id = ?", (gid,))
        grp = cur.fetchone()
        self.assertEqual(grp["status"], "CLOSED")
        self.assertIn(grp["close_reason"], ("ALL_SOLD", "TAKE_PROFIT"))

        cur.execute("SELECT status FROM layers WHERE group_id = ? AND layer_no = 2", (gid,))
        self.assertEqual(cur.fetchone()["status"], "CANCELLED")
        conn.close()

    # -------------------------------------------------------------
    # 11. 上限拒买后不无限重试
    # -------------------------------------------------------------
    def test_item_11_cap_rejection_no_infinite_retry(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        # Open group for BTC with Layer 1
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc)
            VALUES ('BTCUSDT', 50000.0, 0.015, 0.01, 'OPEN', '2026-11-06T00:00:00Z', '2026-11-06T00:00:00Z')
        """)
        gid = cur.lastrowid
        # Set Layer 1 to already have deployed 2900 USDT (close to 3000 cap)
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status)
            VALUES (?, 1, 50000.0, 50000.0, 0.058, 2900.0, 2.9, 0.58, 'OPEN')
        """, (gid,))
        # Layer 2 notional is 305.74 -> 2900 + 305.74 > 3000.0 BTC cap!
        cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, 2, 49250.0, 'PENDING')", (gid,))
        cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, 3, 48500.0, 'PENDING')", (gid,))
        conn.commit()

        monitor = SpotMartingaleMonitor(self.db_path)
        monitor.startup_checks()

        # Price triggers Layer 2 buy
        monitor.client.get_ticker_price = lambda sym: (49000.0 if sym == "BTCUSDT" else 2000.0, None)
        monitor.client.get_server_time = lambda: (datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc), None)
        monitor.client.get_recent_closed_klines = lambda sym, limit: ([[0, 0, 0, 0, 49000.0, 0, 1]] * limit, None)

        now = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)
        monitor.poll_once(now, 100.0)

        # Verify Layer 2 is CANCELLED, group stop_adding = 1, Layer 3 is CANCELLED
        cur.execute("SELECT stop_adding FROM grid_groups WHERE id = ?", (gid,))
        self.assertEqual(cur.fetchone()["stop_adding"], 1)

        cur.execute("SELECT layer_no, status FROM layers WHERE group_id = ? ORDER BY layer_no ASC", (gid,))
        status_map = {r["layer_no"]: r["status"] for r in cur.fetchall()}
        self.assertEqual(status_map[2], "CANCELLED")
        self.assertEqual(status_map[3], "CANCELLED")

        # In subsequent poll, it must NOT retry
        monitor.poll_once(now + timedelta(minutes=1), 160.0)
        cur.execute("SELECT status FROM layers WHERE group_id = ? AND layer_no = 2", (gid,))
        self.assertEqual(cur.fetchone()["status"], "CANCELLED")
        cur.execute("SELECT COUNT(*) as cnt FROM risk_events WHERE type = 'LAYER_CAP'")
        self.assertGreaterEqual(cur.fetchone()["cnt"], 1)
        conn.close()

    # -------------------------------------------------------------
    # 12. 停加仓后其余 PENDING 全置 CANCELLED 且不再触发；
    #     可疑价 >10% 当轮禁止开组/加仓、跳过止盈判断，halt 判断仍执行、改用上一轮有效价
    # -------------------------------------------------------------
    def test_item_12_stop_adding_and_suspect_price_rules(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()
        # Setup group with C = 50,000. 0.85 * C = 42,500.
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc)
            VALUES ('BTCUSDT', 50000.0, 0.015, 0.01, 'OPEN', '2026-11-06T00:00:00Z', '2026-11-06T00:00:00Z')
        """)
        gid = cur.lastrowid
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status)
            VALUES (?, 1, 50000.0, 50000.0, 0.004, 200.0, 0.2, 0.04, 'OPEN')
        """, (gid,))
        cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, 2, 49250.0, 'PENDING')", (gid,))
        cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, 3, 48500.0, 'PENDING')", (gid,))
        # Set last valid price = 45,000 (so 42,000 is a normal 6.67% drop <= 10%, immediately active)
        cur.execute("UPDATE symbol_state SET last_valid_price = 45000.0 WHERE symbol = 'BTCUSDT'")
        conn.commit()

        monitor = SpotMartingaleMonitor(self.db_path)
        monitor.startup_checks()

        # Part A: Price drops below 0.85 * C (e.g. 42,000) -> stop_adding=1 and all PENDING cancelled
        monitor.client.get_ticker_price = lambda sym: (42000.0 if sym == "BTCUSDT" else 2000.0, None)
        monitor.client.get_server_time = lambda: (datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc), None)
        monitor.client.get_recent_closed_klines = lambda sym, limit: ([[0, 0, 0, 0, 42000.0, 0, 1]] * limit, None)

        now = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)
        monitor.poll_once(now, 100.0)

        cur.execute("SELECT stop_adding FROM grid_groups WHERE id = ?", (gid,))
        self.assertEqual(cur.fetchone()["stop_adding"], 1)
        cur.execute("SELECT status FROM layers WHERE group_id = ? AND layer_no IN (2, 3)", (gid,))
        for r in cur.fetchall():
            self.assertEqual(r["status"], "CANCELLED")

        # Part B: Suspect price jump > 10%
        # Set valid price to 50,000. Send price = 60,000 (+20% jump, suspect!)
        cur.execute("UPDATE symbol_state SET last_valid_price = 50000.0, last_suspect_price = NULL WHERE symbol = 'BTCUSDT'")
        conn.commit()

        # Kline close at 50,000 (so condition b does not match 60,000; condition a does not match since P1 is None)
        monitor.client.get_ticker_price = lambda sym: (60000.0 if sym == "BTCUSDT" else 2000.0, None)
        monitor.client.get_recent_closed_klines = lambda sym, limit: ([[0, 0, 0, 0, 50000.0, 0, 1]] * limit, None)

        monitor.poll_once(now + timedelta(minutes=1), 160.0)

        cur.execute("SELECT last_valid_price, last_suspect_price, suspect_price_streak FROM symbol_state WHERE symbol = 'BTCUSDT'")
        sym_st = cur.fetchone()
        self.assertEqual(sym_st["last_valid_price"], 50000.0, "Last valid price must NOT be overwritten by unaccepted suspect price")
        self.assertEqual(sym_st["last_suspect_price"], 60000.0, "Suspect price P2 must be stored in last_suspect_price")
        self.assertEqual(sym_st["suspect_price_streak"], 1)

        # Verify that TP was NOT triggered at 60,000 (even though 60,000 > TP_1 = 50750)
        cur.execute("SELECT status FROM layers WHERE group_id = ? AND layer_no = 1", (gid,))
        self.assertEqual(cur.fetchone()["status"], "OPEN", "TP must be skipped on suspect price round!")
        cur.execute("SELECT type FROM risk_events")
        ev_types = {r["type"] for r in cur.fetchall()}
        self.assertIn("STOP_ADDING", ev_types)
        self.assertIn("SUSPECT_PRICE", ev_types)
        conn.close()

    # -------------------------------------------------------------
    # 13. Round 2 Fix Verification Tests (P1-2, P1-3, P1-4, P2-1, P2-2, P2-3)
    # -------------------------------------------------------------
    def test_item_13_post_trade_equity_and_risk_events_and_constraints(self):
        conn = engine.get_db_connection(self.db_path)
        cur = conn.cursor()

        # (A) P2-1: Partial unique index prevents duplicate OPEN grid_groups per symbol
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc)
            VALUES ('BTCUSDT', 50000.0, 0.015, 0.01, 'OPEN', '2026-11-06T00:00:00Z')
        """)
        gid1 = cur.lastrowid
        with self.assertRaises(sqlite3.IntegrityError):
            cur.execute("""
                INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc)
                VALUES ('BTCUSDT', 51000.0, 0.015, 0.01, 'OPEN', '2026-11-06T00:01:00Z')
            """)
        # Closing gid1 allows a new OPEN group and multiple CLOSED groups
        cur.execute("UPDATE grid_groups SET status = 'CLOSED' WHERE id = ?", (gid1,))
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc)
            VALUES ('BTCUSDT', 51000.0, 0.015, 0.01, 'CLOSED', '2026-11-06T00:02:00Z')
        """)
        conn.commit()

        # (B) P2-2: Element-level frozen config check
        orig_btc_layers = list(config.BTC_LAYER_NOTIONALS)
        try:
            config.BTC_LAYER_NOTIONALS = [200.00, 300.00, 449.70, 629.58, 809.46, 611.26]  # still sums to 3000.00
            self.assertFalse(config.verify_frozen_config())
        finally:
            config.BTC_LAYER_NOTIONALS = orig_btc_layers
        self.assertTrue(config.verify_frozen_config())

        # (C) P1-3 & P2-3: Post-trade equity in monitor and risk_events logging
        cur.execute("""
            INSERT INTO grid_groups (symbol, center_price, spacing_pct, atr_pct, status, opened_utc, first_buy_utc)
            VALUES ('BTCUSDT', 50000.0, 0.015, 0.01, 'OPEN', '2026-11-06T00:00:00Z', '2026-11-06T00:00:00Z')
        """)
        gid2 = cur.lastrowid
        cur.execute("""
            INSERT INTO layers (group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status)
            VALUES (?, 1, 50000.0, 50000.0, 0.004, 200.0, 0.2, 0.04, 'OPEN')
        """, (gid2,))
        cur.execute("INSERT INTO layers (group_id, layer_no, trigger_price, status) VALUES (?, 2, 49250.0, 'PENDING')", (gid2,))
        # Cash after buying L1 = 9999.76 - 200 = 9799.76
        cur.execute("UPDATE account SET cash_usdt = 9799.76, peak_equity_usdt = 9999.76, last_equity_usdt = 9999.76 WHERE id = 1")
        cur.execute("UPDATE symbol_state SET last_valid_price = 50000.0 WHERE symbol = 'BTCUSDT'")
        conn.commit()

        monitor = SpotMartingaleMonitor(self.db_path)
        monitor.startup_checks()

        # Price rises to 51,000 -> Layer 1 takes profit:
        # Pre-trade equity at 51,000 would be 9799.76 + 0.004 * 51000 = 10003.76
        # Post-trade sell at 51,000 deducts sell fee (0.204) + sell slippage (0.0408) = 0.2448 -> 10003.5152 (rounded 10003.52)
        now = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)
        monitor.client.get_ticker_price = lambda sym: (51000.0 if sym == "BTCUSDT" else 2000.0, None)
        monitor.client.get_server_time = lambda: (now, None)
        monitor.client.get_recent_closed_klines = lambda sym, limit: ([[0, 0, 0, 0, 51000.0, 0, 1]] * limit, None)
        monitor.poll_once(now, 100.0)

        cur.execute("SELECT cash_usdt, last_equity_usdt, peak_equity_usdt FROM account WHERE id = 1")
        acc = cur.fetchone()
        self.assertAlmostEqual(acc["last_equity_usdt"], round(acc["cash_usdt"], 2), places=2)
        self.assertAlmostEqual(acc["peak_equity_usdt"], 10003.52, places=2)
        self.assertLess(acc["peak_equity_usdt"], 10003.76, "Peak equity must use post-trade equity, not pre-trade equity")

        cur.execute("SELECT equity FROM daily_summary WHERE date_utc = '2026-11-06'")
        ds = cur.fetchone()
        self.assertAlmostEqual(ds["equity"], 10003.52, places=2)

        # (D) P1-4: BacktestEngine §4.2 price anomaly state machine
        from backtest import BacktestEngine
        bt = BacktestEngine([])
        eval_dt = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)
        # First bar initializes last_valid_price = 50000 (explicitly passing latest_kline_close)
        val_px, suspect, err_st = bt.evaluate_symbol_price("BTCUSDT", 50000.0, eval_dt, 50000.0)
        self.assertEqual(val_px, 50000.0)
        self.assertFalse(suspect)
        self.assertEqual(err_st, 0)

        # Jump +20% to 60000 with prev_close = 50000 -> unaccepted suspect price P1
        val_px2, suspect2, _ = bt.evaluate_symbol_price("BTCUSDT", 60000.0, eval_dt, 50000.0)
        self.assertEqual(val_px2, 50000.0, "Valuation price must stay at previous valid price 50000")
        self.assertTrue(suspect2)
        self.assertEqual(bt.symbol_state["BTCUSDT"]["suspect_price_streak"], 1)
        self.assertEqual(bt.symbol_state["BTCUSDT"]["last_suspect_price"], 60000.0)

        # Second consecutive bar at 60500 (within 2% of P1=60000 -> Condition (a) satisfied!)
        val_px3, suspect3, _ = bt.evaluate_symbol_price("BTCUSDT", 60500.0, eval_dt, 60000.0)
        self.assertEqual(val_px3, 60500.0, "Condition (a) must accept P2 and update last_valid_price")
        self.assertFalse(suspect3)
        self.assertEqual(bt.symbol_state["BTCUSDT"]["suspect_price_streak"], 0)
        self.assertIsNone(bt.symbol_state["BTCUSDT"]["last_suspect_price"])

        conn.close()


if __name__ == "__main__":
    unittest.main()
