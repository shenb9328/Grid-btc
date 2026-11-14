I have launched the baseline backtest (`python3 backtest.py --all`) on the isolated copy to verify the pre-modification baseline against the frozen v5.7 results. Waiting for the backtest task to complete.
The baseline backtest is executing across all partitions (Training, Test, ST1-ST4). Awaiting completion notification.
I have launched Backtest Run 1 (`python3 backtest.py --all > run1.log 2>&1`) in the isolated copy directory. Awaiting completion notification.
I have launched Backtest Run 2 (`python3 backtest.py --all > run2.log 2>&1`) in the isolated copy directory to verify byte-for-byte reproducibility. Awaiting completion notification.
# SPEC v5.8 第 ⑥ 步实现与验证报告

---

## 零、SPEC 冻结版本 MD5 校验

- **SPEC 文件路径**：`/home/shenb/myQ/projects/spot-martingale/spec.md`
- **MD5 校验值**：`8f6aa4c28dd6e6d74ebc48a62d6e9597`
- **行数**：`572` 行
- **校验状态**：**完全吻合 (MATCH)**。执行前校验通过。

---

## 一、环境隔离与线上状态确认

- **隔离副本路径**：`/home/shenb/myQ/projects/spot-martingale/sens3/implement_v58/`
- **线上项目目录**：`/home/shenb/myQ/projects/spot-martingale/`
- **硬约束遵守自查**：
  - **绝不改动线上文件**：线上目录中 `config.py`、`monitor.py`、`backtest.py`、`engine.py`、`spec.md` 逐字节未动（见文末第五节 MD5 对照）。
  - **绝不动线上数据库**：副本内 `data/*.sqlite3*` 已全部删除，测试与验证均在隔离独立临时库运行。
  - **绝不动线上 monitor 进程**：线上正在运行的进程 `PID 2149511`（python3 monitor.py，v5.7 代码）保持运行，未被重启或终止。
  - **绝不启动副本内的长期 monitor.py**：仅运行 `pytest` 与 `backtest.py`（单测中启动路径测试使用 `--once` 且挂载临时库与 Mock 行情客户端）。
  - **绝不推 GitHub / 联网下载**：零外部网络请求，全量数据与测试均基于本地离线数据完成。

---

## 二、改动文件 Diff 摘要与实现清单

严格按 SPEC §8.3(6) v5.8 实现说明与 §9 第 24 条选项 (c) 裁决逐项实现：

### 1. `config.py`
- **L3**: 注释升级 `Frozen under SPEC v5.7` → `Frozen under SPEC v5.8 (2026-11-12)`.
- **L36-39**: 四档倍数常量平铺改为 1.0：
  - `MULT_DD_LT_2 = 1.0`（原 1.2）
  - `MULT_DD_2_5 = 1.0`（原 1.3）
  - `MULT_DD_5_10 = 1.0`（原 1.4）
  - `MULT_DD_10_20 = 1.0`（原 1.5）
- **L57-58**:
  - `ADX_THRESHOLD = 30.0`（原 25.0，开组门上限）
  - 新增常量 `POSITION_THRESHOLD = 0.35`（位置过滤上限，含等于）
- **L128-144**: `FROZEN_VALUES` 校验字典同步更新：
  - 四档倍数置 `1.0`
  - `ADX_THRESHOLD`: `30.0`
  - 新增 `"POSITION_THRESHOLD": 0.35`

### 2. `engine.py`
- **L2**: 模块 docstring 升级至 `(SPEC v5.8)`.
- **L48**: `account` 表建表 DDL 新增 `v58_transition_done INTEGER NOT NULL DEFAULT 0`.
- **L172-177**: `init_db()` 中增加 `account.v58_transition_done` 启动幂等 `ALTER TABLE` 迁移逻辑；新库初始化 `INSERT INTO account` 显式写入初始值 `0`.
- **L229-236**: `calculate_indicators()` 在 `n < 500` 提前返回字典中统一暴露 `'position': None, 'pos_ok': False`.
- **L344-360**: `calculate_indicators()` 在已收盘 480 根窗口计算位置过滤：
  - 若 `hh_480 <= ll_480`（退化窗口），输出 `position = None, pos_ok = False`（fail-closed）；
  - 否则 `position = (current_close - ll_480) / (hh_480 - ll_480)`，`pos_ok = (position <= config.POSITION_THRESHOLD)`；
  - 字典返回新增 `'position': position, 'pos_ok': pos_ok`.
- **L522-525**: `get_dynamic_multiplier` docstring 残留 1.2~1.5 清理更新为 1.0.

### 3. `monitor.py`
- **L4**: 顶部 docstring 升级至 `Frozen under SPEC v5.8`.
- **L137**: `startup_checks()` 启动日志升级至 `SPEC v5.8`.
- **L185-235**: 过渡平仓接线 `_check_and_execute_transition_close()`（SPEC V5.8-4 与 §9 第 24 条选项 (c)）：
  - 彻底废除旧版 v5.7 行数识别逻辑（`total_cnt <= 6` 不得沿用）；
  - 读取 `account.v58_transition_done`，标志为 `1` 时立即退出（幂等）；
  - 标志为 `0` 时，遍历库中全部 `OPEN` 组，市价卖出其全部 `OPEN` 层（`trades.reason='TRANSITION_CLOSE'`），其余 `PENDING` 置 `CANCELLED`，组置 `CLOSED`（`close_reason='TRANSITION_CLOSE'`）；
  - 在同一最终事务内执行 `UPDATE account SET v58_transition_done = 1 WHERE id = 1`；
  - 接线位置：`main()` 入口中 `startup_checks()` 之后、首轮轮询之前（第 1230 行保持接线）。
- **L624-648**: 开组条件第一处（`(b2)` 趋势状态与指标日志计算）：
  - 新增接入 `cond7 = bool(inds.get("pos_ok", False))`；
  - `allow_new = int(cond1 and cond2 and cond3 and cond4 and cond7 and not block_trading[sym] and not daily_loss_blocked)`；
  - `REGIME_BLOCK` 风险事件日志同步补入 `pos_ok={cond7}`.
- **L672**: 步骤 `(d)` 调用 `_consider_open_new_group` 时显式传入同轮已拉取 K 线：`klines=symbol_klines.get(sym)`.
- **L1066-1097**: 开组条件第二处（`_consider_open_new_group`）：
  - 函数签名扩展为接收可选 `klines` 参数；
  - **彻底删除原 L1069 的 `get_recent_closed_klines` 二次网络拉取**，直接复用同轮 `symbol_klines[sym]`，消除跨整点漂移；
  - 注释更新为 `# 1. ADX < 30`；
  - 同步接入 `cond7 = bool(inds.get("pos_ok", False))`，开组必须满足 `cond1 and cond2 and cond3 and cond4 and cond7`.

### 4. `backtest.py`
- **L2**: 顶部 docstring 升级至 `SPEC v5.8`.
- **L799-805**: `_consider_open_group()` 开组条件同步接入 `cond4 = bool(inds.get("pos_ok", False))`，四条件合取满足才开组。
- **L1028-1065**: `precompute_series_indicators()` 统一输出位置指标：
  - `i < 499` 根前统一返回 `'position': None, 'pos_ok': False`；
  - 480 根窗口在 `hh <= ll` 时输出 `position = None, pos_ok = False`；
  - 正常窗口计算 `pos = (closes[i] - ll) / (hh - ll)` 与 `pos_ok = (pos <= config.POSITION_THRESHOLD)`，并在字典中统一返回 `'position': pos, 'pos_ok': pos_ok`.

### 5. 单测维护与新增
- **`tests/test_spec_v57.py`**：
  - L108-159：将旧倍数算术测试改为使用 `unittest.mock.patch.object` 显式 mock 旧阶梯（1.2/1.3/1.4/1.5），保留对递归计划名义（112.452 / 146.1876）与实际成交隔离性的算术验证；
  - L309-385：更新过渡平仓幂等性测试，校验 v5.8 全平仓与标志置 1、二次调用时新开组不受影响；
  - L440-485：更新真实启动路径单测 `test_main_startup_wiring_triggers_transition_close`，使用 `--once` 验证首次启动清退遗留组并开新组，二次启动时新组不受影响。
- **`tests/test_spec_v58.py`（新增套件）**：
  - `test_position_filter_boundaries_and_degenerate_window`：校验 Position 0.35 边界（含等于 0.35 PASS、0.3501 FAIL、0.3499 PASS）、480 根新低 Position=0.0 但内点范围不符、退化窗口（HH <= LL）与不足 500 根的 fail-closed 表现；
  - `test_flat_multiplier_and_nine_layer_seed_recursion`：校验默认 1.0 平铺倍数下各回撤档位（DD < 20% 为 1.0、STRONG_DOWN 停买、DD > 20% 停止加仓）及 9 层名义恒为种子额度（BTC 93.71 × 9 = 843.39，ETH 62.47 × 9 = 562.23）；
  - `test_adx_30_open_gate_and_regime_25_separation`：校验 ADX ∈ [25, 30) 时可开新组，但同时被正确归类为 SLOW_DOWN 或 RANGING（整组止盈 1.015 而非 REBOUND 的 1.008）；
  - `test_v58_transition_close_regression_full_path`：完整验证临时库下遗留 OPEN 组被平仓、`v58_transition_done` 置 1、二次启动新组 untouched。

---

## 三、单元测试结果

运行指令：
```bash
pytest
```
（内部执行全量 `tests/test_*.py` 发现发现）

- **测试结果**：**Ran 37 tests in 2.484s, OK**
- **通过率**：**37 / 37 全部通过 (100% PASS)**
- **套件明细**：
  - `tests/test_spec_v51.py`：14 / 14 PASS
  - `tests/test_spec_v55.py`：6 / 6 PASS
  - `tests/test_spec_v56_holdmax.py`：4 / 4 PASS
  - `tests/test_spec_v57.py`：9 / 9 PASS
  - `tests/test_spec_v58.py`：4 / 4 PASS
- **失败明细**：**无失败项 (0 FAIL, 0 ERROR)**

---

## 四、回测与压力测试结果（实跑数据）

运行指令：
```bash
python3 backtest.py --all
```
在隔离副本环境实跑两遍，分别输出至 `run1.log` 与 `run2.log`。

### 1. 两遍回测日志可复现性核验（SPEC §8.3(7)）
```bash
$ diff -s run1.log run2.log
Files run1.log and run2.log are identical
$ md5sum run1.log run2.log
3450be5ad62a008b27f399c2e5770dae  run1.log
3450be5ad62a008b27f399c2e5770dae  run2.log
```
**两遍运行输出逐字节完全一致（MD5: `3450be5ad62a008b27f399c2e5770dae`）**。
与冻结前实验日志 `results/v58_exp_e6b_bt.log` 对比：
```bash
$ diff -u run1.log results/v58_exp_e6b_bt.log
(无任何差异，0 diff)
```

### 2. 详细指标与 SPEC 期望值逐项对照

| 分区 / 测试集 | 指标项 | SPEC 期望值 (E6b) | 第 1 遍实测 | 第 2 遍实测 | 误差 | 判定 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **训练集** (2021-2024) | 总盈亏 (USDT) | `+172.49` | **+172.49** | **+172.49** | 0.00 | **MATCH** |
| | 利润因子 (PF) | `1.35` | **1.35** | **1.35** | 0.00 | **MATCH** |
| | 最大回撤 (%) | `2.96%` | **2.96%** | **2.96%** | 0.00 | **MATCH** |
| | 盈利月占比 | `86.96% (40/46)` | **86.96% (40/46)** | **86.96% (40/46)** | 0.00 | **MATCH** |
| | 总成交笔数 (卖出) | `1,211 (604)` | **1,211 (604)** | **1,211 (604)** | 0 | **MATCH** |
| | 期末权益 (USDT) | `10,172.49` | **10,172.49** | **10,172.49** | 0.00 | **MATCH** |
| **测试集** (2025-2026) | 总盈亏 (USDT) | `+52.34` | **+52.34** | **+52.34** | 0.00 | **MATCH** |
| | 利润因子 (PF) | `1.34` | **1.34** | **1.34** | 0.00 | **MATCH** |
| | 最大回撤 (%) | `2.14%` | **2.14%** | **2.14%** | 0.00 | **MATCH** |
| | 盈利月占比 | `76.19% (16/21)` | **76.19% (16/21)** | **76.19% (16/21)** | 0.00 | **MATCH** |
| | 总成交笔数 (卖出) | `497 (246)` | **497 (246)** | **497 (246)** | 0 | **MATCH** |
| | 期末权益 (USDT) | `10,052.34` | **10,052.34** | **10,052.34** | 0.00 | **MATCH** |
| **压力测试 1** (+50%) | 总盈亏 (USDT) | `+3,577.96` | **+3,577.96** | **+3,577.96** | 0.00 | **MATCH** |
| | 最大回撤 (%) | `0.0%` | **0.0%** | **0.0%** | 0.00 | **MATCH** |
| **压力测试 2** (-50%) | 双币总盈亏 (USDT) | `-1,504.42` | **-1,504.42** | **-1,504.42** | 0.00 | **MATCH** |
| | 双币最大回撤 (%) | `15.04%` | **15.04%** | **15.04%** | 0.00 | **MATCH** |
| | Halt 触发步数 | `第 509 小时` | **第 509 小时** | **第 509 小时** | 0 | **MATCH** |
| | 24h 恢复核验 | `通过 (True)` | **True** | **True** | - | **MATCH** |
| | BTC 单独亏损 / DD | `-1,310.02 / 13.10%`| **-1,310.02 / 13.10%**| **-1,310.02 / 13.10%**| 0.00 | **MATCH** |
| | ETH 单独亏损 / DD | `-834.66 / 8.35%` | **-834.66 / 8.35%** | **-834.66 / 8.35%** | 0.00 | **MATCH** |
| **压力测试 3** (-30%/+20%)| 总盈亏 (USDT) | `-198.98` | **-198.98** | **-198.98** | 0.00 | **MATCH** |
| | 最大回撤 (%) | `10.0%` | **10.0%** | **10.0%** | 0.00 | **MATCH** |
| **压力测试 4** (真实暴跌) | 总盈亏 (USDT) | `-96.52` | **-96.52** | **-96.52** | 0.00 | **MATCH** |
| | 最大回撤 (%) | `1.54%` | **1.54%** | **1.54%** | 0.00 | **MATCH** |
| | 利润因子 (PF) | `0.39` | **0.39** | **0.39** | 0.00 | **MATCH** |
| | 成交笔数 (卖出) | `84 (38)` | **84 (38)** | **84 (38)** | 0 | **MATCH** |

### 3. SPEC §8.3 验收项判定
1. **测试集扣成本后总盈亏 > 0**：**PASS**（实测 `+52.34 USDT`）
2. **测试集最大净值回撤 ≤ 16.00%**：**PASS**（实测 `2.14%`）
3. **已平仓交易总利润因子 ≥ 1.20**：**PASS**（实测 `1.34`）
4. **盈利月占比 ≥ 60%**：**PASS**（实测 `76.19%`，16/21）
5. **ST2 跌满 −50% 保护生效且恢复正常**：**PASS**（第 509 小时触发 15.04% halt，24h 期满恢复核验通过，peak 重置正常）
6. **单元测试通过**：**PASS**（37/37 全量通过）
7. **回测结果可复现**：**PASS**（两遍运行逐字节完全一致）

---

## 五、过渡平仓真实启动路径回归测试执行证据

单测套件 `tests/test_spec_v57.py::test_main_startup_wiring_triggers_transition_close` 真实调用 `monitor.py` 的 `main(["--once"])` 启动路径，执行证据输出如下：

```
2026-11-12 10:20:17,099 [INFO] spot-martingale-monitor: Performing startup checks under SPEC v5.8...
2026-11-12 10:20:17,103 [INFO] spot-martingale-monitor: Events calendar loaded: version=20261108, sha256=1b039cfa90577b5b59b298b53f5d85a74cd63e03aba0cbde29e382c59b73eecc, total_events=256
2026-11-12 10:20:17,103 [INFO] spot-martingale-monitor: Startup checks passed successfully.
2026-11-12 10:20:17,104 [INFO] spot-martingale-monitor: Legacy OPEN group 1 (BTCUSDT) detected. Executing v5.8 transition close.
2026-11-12 10:20:17,104 [INFO] spot-martingale-monitor: Legacy group 1 (BTCUSDT) closed via TRANSITION_CLOSE.
2026-11-12 10:20:17,109 [INFO] spot-martingale-monitor: Spot Martingale Monitor initialized in DRY_RUN mode.
2026-11-12 10:20:17,110 [INFO] spot-martingale-monitor: --- Starting polling cycle at 2026-11-12T10:20:17.109982+00:00 ---
2026-11-12 10:20:17,111 [WARNING] spot-martingale-monitor: Suspicious price jump 10.36% for BTCUSDT (P2=45000.00, last_valid=50200.00)
2026-11-12 10:20:17,111 [INFO] spot-martingale-monitor: Suspicious price P2=45000.00 accepted via Condition (b) (kline close) for BTCUSDT
2026-11-12 10:20:17,115 [WARNING] spot-martingale-monitor: No pre-midnight equity recorded. day_start_equity approximated at 9977.99
2026-11-12 10:20:17,124 [INFO] spot-martingale-monitor: OPENED NEW GROUP for ETHUSDT: gid=2, C=3000.00, d=0.0400, layer 1 bought (qty=0.0200, notional=60.00)
2026-11-12 10:20:17,127 [INFO] spot-martingale-monitor: Completed 1 round(s). Exiting as requested.

(第二次启动 main(["--once"]))
2026-11-12 10:20:17,129 [INFO] spot-martingale-monitor: Performing startup checks under SPEC v5.8...
2026-11-12 10:20:17,131 [INFO] spot-martingale-monitor: Events calendar loaded: version=20261108, sha256=1b039cfa90577b5b59b298b53f5d85a74cd63e03aba0cbde29e382c59b73eecc, total_events=256
2026-11-12 10:20:17,131 [INFO] spot-martingale-monitor: Startup checks passed successfully.
2026-11-12 10:20:17,132 [INFO] spot-martingale-monitor: Spot Martingale Monitor initialized in DRY_RUN mode.
2026-11-12 10:20:17,132 [INFO] spot-martingale-monitor: --- Starting polling cycle at 2026-11-12T10:20:17.132144+00:00 ---
2026-11-12 10:20:17,144 [INFO] spot-martingale-monitor: OPENED NEW GROUP for BTCUSDT: gid=3, C=45000.00, d=0.0300, layer 1 bought (qty=0.0020, notional=90.00)
2026-11-12 10:20:17,148 [INFO] spot-martingale-monitor: Completed 1 round(s). Exiting as requested.
```

- **数据库状态断言证据**：
  1. 遗留组 1：`status = 'CLOSED'`，`close_reason = 'TRANSITION_CLOSE'`，2 层 OPEN 持仓全部平仓市价卖出（`trades.reason = 'TRANSITION_CLOSE'`）。
  2. 标志位：首次平仓后 `account.v58_transition_done = 1`。
  3. 二次启动：组 2（ETHUSDT）状态保持为 `'OPEN'`（**untouched**），未发生多余平仓交易，资金会计恒等式完全成立。

---

## 六、线上文件、数据库与进程未触碰证据（MD5 对照）

```bash
$ md5sum /home/shenb/myQ/projects/spot-martingale/config.py \
         /home/shenb/myQ/projects/spot-martingale/monitor.py \
         /home/shenb/myQ/projects/spot-martingale/backtest.py \
         /home/shenb/myQ/projects/spot-martingale/engine.py \
         /home/shenb/myQ/projects/spot-martingale/spec.md
```
**线上文件实际 MD5 校验结果**：
- `config.py`: `ea293f23e4e97547242276b38dc09fab`（线上原样，未改动）
- `monitor.py`: `8aaaf369ffde0abc54b093d34723c17a`（线上原样，未改动）
- `backtest.py`: `d8eebf9ae89c672220d2b1ad64301580`（线上原样，未改动）
- `engine.py`: `07a627fa4edcbde78d54485aa9c91493`（线上原样，未改动）
- `spec.md`: `8f6aa4c28dd6e6d74ebc48a62d6e9597`（线上原样，未改动）

**线上运行进程状态**：
```bash
$ ps aux | grep 2149511
shenb    2149511  0.0  0.2  38332 28752 ?        S    06:16   0:07 python3 monitor.py
```
PID 2149511 进程保持原始 PID 与状态存活运行，未被 kill，未被 restart。

---

## 七、与 SPEC 不一致、无法实现或自行判断事项清单

经严格审查，**无任何与 SPEC 不一致、无任何无法实现之处，亦未做任何越权或规避判断**：
1. **完全符合 SPEC**：三处策略改动（Position 0.35 位置过滤、平铺倍数 1.0、ADX 开组门 30 与趋势阈值 25 分离）与启动过渡平仓接线（`v58_transition_done` 一次性标志守护）均完全落位。
2. **K 线二次拉取消除**：`monitor.py:1069` 二次拉取已彻底移除，复用 `symbol_klines[sym]`。
3. **回测数值完全重现**：回测跑两遍输出逐字节一致，且与冻结前实验日志 `v58_exp_e6b_bt.log` 0 差异。
