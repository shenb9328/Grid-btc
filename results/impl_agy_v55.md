# BTC/ETH 现货马丁策略（冻结 SPEC v5.5）第 ⑥ 步实现报告（impl_agy_v55）

- **冻结基线**：`spec.md`（438 行，md5 `faa9b30a3f7e2fd651a7acc97cc1e023`，moka 2026-11-06 17:03 批准冻结，**未做任何改动**）
- **实现范围**：SPEC v5.5 §4.2 离线回测状态机闭环（V5.4-1、V5.4-2、V5.4-3、V5.5-1～V5.5-5、P-1 权威�# BTC/ETH 现货马丁策略（冻结 SPEC v5.5）第 ⑥ 步实现报告（impl_agy_v55）

- **冻结基线**：[`spec.md`](file:///home/shenb/myQ/projects/spot-martingale/spec.md)（438 行，md5 `faa9b30a3f7e2fd651a7acc97cc1e023`，moka 2026-11-06 17:03 批准冻结，**未做任何改动**）
- **实现范围**：SPEC v5.5 §4.2 离线回测状态机闭环（V5.4-1、V5.4-2、V5.4-3、V5.5-1～V5.5-5、P-1 权威解释、`merged_review_v54.md` §三 4 项实现要求）
- **完整运行日志**：[`results/impl_agy_v55.log`](file:///home/shenb/myQ/projects/spot-martingale/results/impl_agy_v55.log)（41 KB，非空，完整记录两次单元测试与两次全量回测的实际输出）
- **报告归档**：[`results/impl_agy_v55.md`](file:///home/shenb/myQ/projects/spot-martingale/results/impl_agy_v55.md)

---

## 一、改动文件清单及 SHA256

| 文件 | 变更说明 | SHA256 |
|---|---|---|
| [`spec.md`](file:///home/shenb/myQ/projects/spot-martingale/spec.md) | **绝不动（未修改）** | `df355314290428e487e4eef617b07b4a9b471e53d1706d81f773b2a4c4666258` (md5: `faa9b30a3f7e2fd651a7acc97cc1e023`) |
| [`config.py`](file:///home/shenb/myQ/projects/spot-martingale/config.py) | 策略与系统配置（**绝不调参**，启动严格等于校验保持通过） | `40b2e27962d7cecb01ef3b2f206f45369c6926940d9f2104a0b31e55a2789390` |
| [`engine.py`](file:///home/shenb/myQ/projects/spot-martingale/engine.py) | 订单计算、网格间距、事件日历校验与底层约束（保持原样） | `454eeaff153236c972ed81c32120b1ff0cdc71105bfe8ebf1fd4e4ebebbd264f` |
| [`monitor.py`](file:///home/shenb/myQ/projects/spot-martingale/monitor.py) | 显式落实 §5.0(a) halt 期满恢复入口的防御性检查（4(d) 项） | `ad1214b91d86237fdf1449e6ce3bcf8d9e4cd8a53423bd6063a92c8cf7f8c0ed` |
| [`backtest.py`](file:///home/shenb/myQ/projects/spot-martingale/backtest.py) | 落实 V5.4-1、V5.4-2、V5.4-3、V5.5-1～V5.5-5、P-1、取消 `latest_kline_close` 默认值、整点网格推进、缺 bar/无效 OHLC 细分与连续缺口计数、§5.0(a) 防御检查 | `eae9c7714a10e4cfeb5ef48f143554be18e4bdc4e87521b0e5c531f8fe676dba` |
| [`tests/test_spec_v51.py`](file:///home/shenb/myQ/projects/spot-martingale/tests/test_spec_v51.py) | 回归测试适配：显式向 `evaluate_symbol_price` 传入 `latest_kline_close` | `60d888f65cc97a2716a88a74113cb545d16cc3d15aa369f376623424fe26d683` |
| [`tests/test_spec_v55.py`](file:///home/shenb/myQ/projects/spot-martingale/tests/test_spec_v55.py) | 新增 SPEC v5.5 专项单元测试（6 项：参数契约、预热24h上限、P-1权威解释、缺bar与时间止损、防御检查、条件b统计） | `0b072f2e4dc821546e96545b4c84f9601c0ed8b32e5f6807e00f1b0ddb3e5f87` |
| [`results/impl_agy_v55.log`](file:///home/shenb/myQ/projects/spot-martingale/results/impl_agy_v55.log) | 两次单测与两次全量回测实际运行日志 | `d3c41c167735d42f915cb04f5ba5d373ffb776a8c162c93cbdff96de9ca09bce` |

*(注：线上看板 `web_dashboard.py` 未动，线上进程 PID 1223916 看板与 PID 1466554 solana 监控未做任何重启)*

---

## 二、运行命令与测试结果

### 1. 运行命令（测试、回测各连续执行两次）
```bash
python3 config.py
python3 -m unittest discover -v tests   # 单元测试 Run 1
python3 -m unittest discover -v tests   # 单元测试 Run 2
python3 backtest.py --all               # 全量回测 Run 1
python3 backtest.py --all               # 全量回测 Run 2
```

### 2. 单元测试结果（两次运行完全一致）
- **测试总数**：19 项全部通过（`test_spec_v51.py` 13 项 + `test_spec_v55.py` 6 项）
- **运行耗时**：0.837s（Run 1）、0.811s（Run 2）
- **测试状态**：`Ran 19 tests — OK`
- **关键测试覆盖**：
  1. `test_item_01_evaluate_symbol_price_no_default_param`：`evaluate_symbol_price` 移除 `latest_kline_close` 默认值，省略传参必然抛 `TypeError`；
  2. `test_item_02_preheat_initialization_and_24h_limit`：预热 bar 时效 <= 24h 成功初始化 `last_valid_price` 并将各 streak 归零；时效 > 24h 记 WARNING 并回退首根有效 bar 初始化分支；
  3. `test_item_03_p1_authoritative_interpretation`：首根有效 bar 到来之前的轮次显式传 `latest_kline_close=None` 并计价格错误；首根有效 bar 所在的轮次走回退初始化分支、将其 close 写入 `last_valid_price`、不计价格错误；
  4. `test_item_04_missing_bar_handling_and_time_stops`：缺 bar 轮次用上一有效价估值，跳过止盈，时间止损（14d）照常执行；无价币种持仓市值恒为 0，现金不变量严格守恒；
  5. `test_item_05_defensive_check_halt_recovery_backtest_and_monitor`：在 `backtest.py` 与 `monitor.py` 实盘中，当 `halt_active==1` 且任一币种 `last_valid_price is None` 时，防御性拦截恢复（记 ERROR、不重置峰值、不清除 halt 状态）；
  6. `test_item_06_condition_b_evaluation_and_counters`：验证条件 (b) 评估轮数与接受轮数统计。

---

## 三、回测实际运行结果（来自修后代码实跑，两次运行 100% 逐行完全一致）

### 1. 分区数据质量与价格异常状态机统计（按 symbol × 预期整点网格小时轮）

| 回测分区 | 网格总小时 | 币种 | 价格错误轮数 (缺bar / close无效) | 连续缺口 > 3小时 | 可疑价轮数 | 条件 (a) 接受轮数 | 条件 (b) 评估轮数 (分母) | 条件 (b) 接受轮数 |
|---|---|---|---|---|---|---|---|---|
| **训练集** (2021-01-01 ~ 2024-12-31 UTC) | 35,064 | BTCUSDT | **14** (14 / 0) | 1 (4h gap) | 1 | 1 | 1 | 0 |
| | 35,064 | ETHUSDT | **14** (14 / 0) | 1 (4h gap) | 1 | 0 | 1 | 0 |
| **测试集** (2025-01-01 ~ 2026-10-31 UTC) | 16,056 | BTCUSDT | **0** (0 / 0) | 0 | 0 | 0 | 0 | 0 |
| | 16,056 | ETHUSDT | **0** (0 / 0) | 0 | 1 | 1 | 1 | 0 |
| **压力测试 4** (2022-06-01 ~ 2022-06-30 UTC) | 720 | BTCUSDT | **0** (0 / 0) | 0 | 0 | 0 | 0 | 0 |
| | 720 | ETHUSDT | **0** (0 / 0) | 0 | 0 | 0 | 0 | 0 |

- **对账校验**：
  - 训练集：`缺 bar 轮数 (14) + close 无效轮数 (0) == 价格错误总轮数 (14)`，完全对账；
  - 训练集在 2021-08-13 存在一处连续 4 小时缺失，严格触发 1 次 `K-line gap over 3 hours` ERROR 日志；
  - 条件 (b) 评估轮数严格等于进入跳变分支且未被条件 (a) 接受的轮次数；回测中条件 (b) 独立接受轮数**均为 0**，完全证实了 SPEC V5.4-1(d) 证明的等价性。

### 2. 策略各窗口表现指标

| 窗口 / 测试项 | 总盈亏 (USDT) | 期末权益 (USDT) | 收盘采样最大回撤 (%) | 平仓落地最大回撤 (%) | 利润因子 (PF) | 盈利月占比 (%) | 总交易笔数 (卖出笔数) |
|---|---|---|---|---|---|---|---|
| **训练集** (2021-2024) | -2056.94 | 7943.06 | 15.45% | 15.45% | 0.81 | 45.83% (22/48) | 4110 (2054) |
| **测试集** (2025-2026) | **-658.80** | **9341.20** | **12.70%** | **0.00%** | **0.82** | **57.14% (12/21)** | 1578 (789) |
| **压力测试 1** (+50%) | +2937.67 | 12937.67 | 0.00% | 0.00% | -- | -- | 0 (0) |
| **压力测试 2** (-50% 双币) | -1506.19 | 8493.81 | 15.06% | 15.06% | -- | -- | 12 (12) |
| **压力测试 2** (-50% BTC) | -1426.11 | 8573.89 | 14.26% | 0.00% | -- | -- | 0 (0) |
| **压力测试 2** (-50% ETH) | -932.00 | 9068.00 | 9.32% | 0.00% | -- | -- | 0 (0) |
| **压力测试 3** (-30% -> +20%) | -557.54 | 9442.46 | 12.99% | 0.00% | -- | -- | 0 (0) |
| **压力测试 4** (2022-06 切片) | -325.09 | 9674.91 | 6.25% | 0.00% | 0.39 | 0.00% (0/1) | 62 (28) |

- **压力测试 2 细节**：
  - 双币组合于第 429 小时触发 15% 回撤 halt 并全平（落地权益 8,493.81 USDT，落地最大回撤 15.06% <= 16.00%）；
  - 触发后 24 小时（第 453 小时）期满恢复，峰值重置为 8,493.81 USDT，`recovery_verified = True`。

---

## 四、SPEC §8.3 七项验收标准逐项判定

| 验收项 | SPEC 标准要求 | 实际运行数字 | 判定 |
|---|---|---|---|
| **1. 测试集总盈亏** | 扣成本后总盈亏 > 0 | `total_pnl = -658.80 USDT` | **FAIL** |
| **2. 测试集最大净值回撤** | 收盘采样与平仓落地均 ≤ 16.00% | 收盘采样 `12.70%`，平仓落地 `0.00%`（未触发 halt） | **PASS** |
| **3. 利润因子 (PF)** | 按每笔 SELL 算 PF ≥ 1.20 | `profit_factor = 0.82` | **FAIL** |
| **4. 盈利月占比** | 分母为成交月数，胜率 ≥ 60.00% | `57.14%`（12 盈利月 / 21 活跃月） | **FAIL** |
| **5. 压力测试 2 (合成 -50%)** | 双币组合 halt 必须触发且落地回撤 ≤ 16.00%、恢复后峰值重置；单币回撤 ≤ 16.00% | 双币: 429h 触发 halt，落地回撤 `15.06% <= 16.00%`，恢复后峰值已重置；BTC 单币 `14.26%`；ETH 单币 `9.32%` | **PASS** |
| **6. 单元测试** | 规定的全部功能与防御断言全过 | 19 项单元测试全部通过（19/19 OK） | **PASS** |
| **7. 结果可复现性** | 同一数据+配置，两次运行结果完全一致（容差 ±0.01 USDT） | 两次全量回测 100 行输出逐字符完全一致，容差 0.00 USDT | **PASS** |

---

## 五、未解决的问题与说明

1. **策略经济性未达标（非代码缺陷）**：
   测试集在严格执行冻结 SPEC 参数下总收益为 `-658.80 USDT`、PF 为 `0.82`、胜率 `57.14%`，未能通过 §8.3 第 1、3、4 项。该数字与评审预期的 `-658.80` 完全一致。按照红线纪律，实现阶段绝不私自调参，如实上报该经济性结果。
2. **线上隔离**：
   未修改 `web_dashboard.py`，未重启任何线上运行中的服务进程。
