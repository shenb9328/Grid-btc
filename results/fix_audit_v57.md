I have executed the transition wiring and audit fixes in the isolated workspace (`/home/shenb/myQ/projects/spot-martingale-sens3/fix_v57_wiring/`), passed all 33 unit tests, and launched the first run of `python3 backtest.py --all`. I am waiting for the backtest task to finish.
The full backtest suite (`python3 backtest.py --all`) is currently executing the test set and stress tests in the background. I will review and verify all metrics against the frozen SPEC v5.7 values once it completes.
The second run of `python3 backtest.py --all` is running in the background to verify reproducibility (SPEC §8.3(7)). I will check the output once it completes.
Waiting for task-196 (second backtest run) to complete.
Launched 3 real poll cycles on the smoke copy of the online database against Binance public market data. Waiting for task-210 to complete.
Task-210 is progressing through its 3 polling rounds (1 round per 60s). Waiting for it to finish.
# SPEC v5.7 线上实现补接线加审计修复轮完整报告

**执行环境**：第 ⑥ 步修复轮 (acc3 / gemini-3.8-flash-medium)  
**依据规范**：`/home/shenb/myQ/projects/spot-martingale/spec.md`（已冻结版 v5.7）  
**冻结规范 MD5**：`6969b432170928138cb787d73531a71b`（核验无误）  
**线上 Monitor 状态**：PID 2144377 持续稳定运行，未 kill、未重启，线上库 `data/spot_martingale.sqlite3` 零写入、零篡改。  

---

## 一、前置两项校验结果

| 序号 | 校验项 | 要求 | 实测结果 | 状态 |
|---|---|---|---|---|
| 1 | `spec.md` 完整性 | MD5 为 `6969b432170928138cb787d73531a71b` | `6969b432170928138cb787d73531a71b` | **PASS** |
| 2 | `monitor.py` 游标修复保留 | `_consider_open_new_group` 体内第一条语句为 `cur = conn.cursor()` | 确认存在于第 1064 行（当前 live 代码） | **PASS** |

---

## 二、任务一：过渡平仓接线设计与回归证明（SPEC §9 第 18 条裁决 (d)）

### 1. 接线设计
- **现状分析**：原 `monitor.py` 中已实现 `_check_and_execute_transition_close` 与公共别名 `execute_transition_close`，但原入口 `main()` 仅调用了 `monitor.startup_checks()`，正常启动路径永远不会触发过渡平仓。
- **接线位置与实现**：
  - 位置严格置于 `monitor.startup_checks()` 之后、第一个轮询周期 `while True:` 之前。
  - 此处保证了数据库 schema 迁移已全部执行完毕（`layers.planned_notional_usdt` 与 `regime_log` 字段均已就绪），且不侵入 `startup_checks()` 内部（避免外部单测直接调用 `startup_checks()` 时产生非预期的过渡平仓副作用）。
  - 在 `SpotMartingaleMonitor.__init__` 中将 `self.db_path` 参数化赋值修正为 `Path(db_path) if db_path is not None else Path(config.DB_PATH)`，并补全 `from pathlib import Path` 导入，消除签名默认值求值时机问题并支持动态路径配置。
  - 在 `main(argv=None)` 中补充 `--db` 命令行参数支持，便于以非默认库启动。
  - 过渡平仓方法本身的判定逻辑与幂等语义（只认 `OPEN` 且层数 $\le 6$ 的遗留组，9 层新组不受影响；数据库不存在时 early return）完全保持不变。

### 2. 接线回归证明
在 `tests/test_spec_v57.py` 中新增接线端到端测试 `test_main_startup_wiring_triggers_transition_close`：
- **夹具设置**：
  - 临时库中构造 1 个遗留 v5.6 的 6 层 OPEN 组（2 层 OPEN + 4 层 SOLD，总层数 6）。
  - 同时构造 1 个 v5.7 的 9 层 OPEN 组（1 层 OPEN + 8 层 PENDING，总层数 9）。
  - 注入 `symbol_state` 最新有效价，同步并断言现金会计恒等式。
- **真实启动序列驱动**：
  - Patch 配置 `config.DB_PATH` 指向该临时库。
  - 对 `BinancePublicClient` 注入模拟公共数据，真实调用 `monitor.main(["--once"])` 执行完整启动序列（`startup_checks()` $\to$ `execute_transition_close()` $\to$ 首轮 `poll_once()` $\to$ 退出）。
- **断言验证**：
  - 遗留 6 层组状态被置为 `CLOSED`，`close_reason` 为 `TRANSITION_CLOSE`。
  - `trades` 表中精准记录 2 笔 `TRANSITION_CLOSE` 卖出交易，现金与已实现盈亏正确结转。
  - v5.7 的 9 层组状态依然保持 `OPEN`，未被误平仓。
  - 整个启动与轮询过程全程满足现金会计恒等式（`engine.assert_cash_invariant`）。
  - 单测运行完全通过（见后文单测章节）。

---

## 三、任务二：审计发现逐项清单与缺陷修复

针对实盘路径与单测未覆盖路径进行了全量 AST 变量作用域扫描、属性定义审计、边界分析以及 `monitor.py` 与 `backtest.py` 逻辑同义性比对：

| 编号 | 涉及文件与行号 | 审计项 / 发现 | 严重度 | 处理方式 | 说明与修复细节 |
|---|---|---|---|---|---|
| **A-1** | `monitor.py:1228` | 过渡平仓未接进正常启动路径 | **高 (P1)** | **已修复** | 在 `monitor.py` 的 `main()` 中补充 `monitor.execute_transition_close()` 调用，置于 `startup_checks()` 之后、第一个轮询周期之前。 |
| **A-2** | `monitor.py:18-20, 128` | `SpotMartingaleMonitor` 内部依赖 `Path` 但未显式导入 `pathlib.Path` | **中 (P2)** | **已修复** | 导入 `from pathlib import Path`，并在 `__init__(self, db_path=None)` 中做 `Path(db_path)` 规范化转换，避免 `str` 路径传入时 `.exists()` 抛 `AttributeError` 或 `NameError`。 |
| **A-3** | `engine.py:222-234` | K 线不足 500 根早退时，`calculate_indicators` 返回字典缺失 `plus_di`、`minus_di`、`ret24` 键 | **低 (P3)** | **已修复** | 早退字典补齐 `'plus_di': None, 'minus_di': None, 'ret24': None`，防止下游任何直接按键索引的代码抛出 `KeyError`。 |
| **A-4** | `engine.py:330` | `ret24` 计算在除以 `closes[-25]` 时缺少非零防御 | **低 (P3)** | **已修复** | 增加 `closes[-25] != 0` 条件守卫，防止极端异常数据引发 `ZeroDivisionError`。 |
| **A-5** | `engine.py:286` & `backtest.py:994` | `tr_val == 0` 时 Wilder +DI/-DI 除零风险 | 提示 | **已确认无缺陷** | 两处实现均已在循环头部检查 `if tr_val == 0: plus_di_arr[i] = 0.0; minus_di_arr[i] = 0.0; dx[i] = 0.0; continue`，安全合规。 |
| **A-6** | `engine.py:108-124` | 既有数据库启动迁移 `init_db()` 幂等性 | 提示 | **已确认无缺陷** | `layers.planned_notional_usdt` 与 `regime_log` 4 列均通过 `PRAGMA table_info` 预先检查列名，已迁移库重复执行不报错、不抛异常、不丢列。 |
| **A-7** | `monitor.py:894-960` vs `backtest.py:685-752` | 动态倍数递归基数、额度截断、步长取整、NULL 计划名义兜底同义性 | 提示 | **已确认无缺陷** | 两处均严格使用上一层计划名义 `planned_notional` 递归，截断与取整只用于当层成交；遇到 NULL 均撤销剩余 PENDING 并置 `stop_adding=1`。 |
| **A-8** | `monitor.py:804` vs `backtest.py:596` | Holdmax 满层豁免 7d/14d 时间止损同义性 | 提示 | **已确认无缺陷** | 均采用 `bought_layers == 9`（`OPEN`、`SOLD`、`TIME_STOPPED` 状态层求和），未满层组严格按实际成交名义执行 7 天减半或 14 天清仓。 |
| **A-9** | `monitor.py:916-927` vs `backtest.py:704-710` | SLOW_DOWN 24 小时冷却边界同义性 | 提示 | **已确认无缺陷** | 判定式均严格为 `now - MAX(bought_utc) < 86400s` 禁买，$\ge 86400s$（含等于）准买，且仅检查 `layer_no >= 2` 的已买入层。 |

---

## 四、改动代码清单（带精确文件与行号）

全部修改仅限于副本中调试并通过验证后，严格同步回 live 目录的 3 个文件：

### 1. `/home/shenb/myQ/projects/spot-martingale/monitor.py`
- **L19**: 增加 `from pathlib import Path` 导入。
- **L128-129**: `SpotMartingaleMonitor.__init__` 参数默认值改为 `db_path=None`，并在体内安全转换 `self.db_path = Path(db_path) if db_path is not None else Path(config.DB_PATH)`。
- **L1064**: 人工修复的 `cur = conn.cursor()` 原样保留（确认在 `_consider_open_new_group` 第一行）。
- **L1221-1230**: `main(argv=None)` 增加 `--db` 参数解析支持；在 `monitor.startup_checks()` 之后、进入 `while True:` 循环之前，正式接入 `monitor.execute_transition_close()`。

### 2. `/home/shenb/myQ/projects/spot-martingale/engine.py`
- **L225-227**: 在 `calculate_indicators` 针对 `n < config.MIN_KLINES_BARS` 的早退字典中补充 `'plus_di': None, 'minus_di': None, 'ret24': None`。
- **L330**: 在 `ret24` 计算条件中增加 `closes[-25] != 0` 非零防御。

### 3. `/home/shenb/myQ/projects/spot-martingale/tests/test_spec_v57.py`
- **L21**: 引入 `from unittest.mock import patch, MagicMock`。
- **L385-467**: 新增 `test_main_startup_wiring_triggers_transition_close`：全真启动序列接线回归测试。
- **L469-502**: 新增 `test_calculate_indicators_insufficient_bars_and_tr_zero`：K 线不足 500 根全键返回、`classify_regime` 容错及 `tr_val == 0` 零波动回归测试。

---

## 五、单元测试结果

在隔离环境与 live 目录分别运行：
```bash
python3 -m unittest discover tests
```
**运行结果**：
```text
Ran 33 tests in 1.910s

OK
```
包含原 31 个测试项及本次新增的 2 项专项回归测试，**33 项全部 PASS（100% 通过）**。

---

## 六、全量回测结果与已核验预期值逐项对照

在副本环境中以相同代码连续运行两次 `python3 backtest.py --all`：

| 测试分区 / 评价指标 | 冻结核验基准值 (v5.7) | 第 1 次运行实测值 | 第 2 次运行实测值 | 偏差说明 | 判定 |
|---|---|---|---|---|---|
| **训练集 (2021-2024)** | | | | | |
| 总盈亏 (USDT) | **-694.49** | **-694.49** | **-694.49** | 误差 0.00 | **MATCH** |
| 利润因子 (PF) | **0.84** | **0.84** | **0.84** | 误差 0.00 | **MATCH** |
| 最大回撤 (%) | **15.44%** | **15.44%** | **15.44%** | 误差 0.00 | **MATCH** |
| 盈利月占比 (%) | **60.42% (29/48)** | **60.42% (29/48)** | **60.42% (29/48)** | 误差 0.00 | **MATCH** |
| **测试集 (2025-2026)** | | | | | |
| 总盈亏 (USDT) | **-471.96** | **-471.96** | **-471.96** | 误差 0.00 | **MATCH** |
| 利润因子 (PF) | **1.10** | **1.10** | **1.10** | 误差 0.00 | **MATCH** |
| 最大回撤 (%) | **11.82%** | **11.82%** | **11.82%** | 误差 0.00 | **MATCH** |
| 盈利月占比 (%) | **63.64% (14/22)** | **63.64% (14/22)** | **63.64% (14/22)** | 误差 0.00 | **MATCH** |
| **压力测试 1 (+50%)** | | | | | |
| ST1 总盈亏 (USDT) | **+3,577.96** | **+3,577.96** | **+3,577.96** | 误差 0.00 | **MATCH** |
| **压力测试 2 (-50%)** | | | | | |
| ST2 双币总盈亏 (USDT) | **-1,504.42** | **-1,504.42** | **-1,504.42** | 误差 0.00 | **MATCH** |
| ST2 双币 Halt 触发小时 | **第 509 小时** | **第 509 小时** | **第 509 小时** | 完全一致 | **MATCH** |
| ST2 双币落地最大回撤 (%) | **15.04%** | **15.04%** | **15.04%** | 误差 0.00 | **MATCH** |
| ST2 恢复校验 (Recovery) | **True** | **True** | **True** | 完全一致 | **MATCH** |
| ST2 BTC 单独总盈亏 (USDT)| **-1,310.02** | **-1,310.02** | **-1,310.02** | 误差 0.00 | **MATCH** |
| ST2 ETH 单独总盈亏 (USDT)| **-834.66** | **-834.66** | **-834.66** | 误差 0.00 | **MATCH** |
| **压力测试 3 (-30% -> +20%)**| | | | | |
| ST3 总盈亏 (USDT) | **-198.98** | **-198.98** | **-198.98** | 误差 0.00 | **MATCH** |
| **压力测试 4 (真实切片 2022-06)**| | | | | |
| ST4 总盈亏 (USDT) | **-144.33** | **-144.33** | **-144.33** | 误差 0.00 | **MATCH** |
| ST4 利润因子 (PF) | **0.39** | **0.39** | **0.39** | 误差 0.00 | **MATCH** |

**结论**：接线与审计修复不改变任何回测经济性指标，两次运行全部数字与已核验基准绝对一致。

---

## 七、真实行情线上副本冒烟测试结果

- **测试设计**：使用 Python 内置 `sqlite3` API 以 `mode=ro` 只读连接线上数据库，利用 `backup()` 方法无锁克隆一份完整线上数据库副本至 `smoke_spot_martingale.sqlite3`，绝不动线上库。
- **命令与运行**：
  ```bash
  python3 monitor.py --rounds 3 --db data/smoke_spot_martingale.sqlite3
  ```
- **实测过程与日志**：
  - **Round 1 (06:04:39 UTC)**：启动检查通过，成功拉取 Binance 现货实时 1h K 线（500 根），BTC ADX=16.60、RET24=-0.46%、+DI=14.39、-DI=24.66，判定为 `RANGING`；ETH 判定为 `RANGING`；账本重算成功。
  - **Round 2 (06:05:42 UTC)**：正常轮询，指标与趋势判定正常更新，无网络异常。
  - **Round 3 (06:06:44 UTC)**：正常轮询，完成 3 轮既定轮询后正常退出（Exiting as requested）。
- **运行结论**：全程 **0 异常、0 ERROR**，日志与数据库写入完全符合预期。测试完毕后已清理临时冒烟库。

---

## 八、未解决问题与逻辑缺口上报

以下为审计中识别出的 SPEC 规范层面固有逻辑特征与约束，按要求保持原样、仅作上报，不私自改动：
1. **测试集与 PF 门槛未达标**：测试集总盈亏为 -471.96 USDT（要求 $>0$）、PF 为 1.10（要求 $\ge 1.20$），因 SPEC §9 第 17 条裁决本版本不予自动豁免，此两项仍处于 FAIL 状态，须待产品/评审方决议。
2. **STRONG_DOWN 解除后的集中补买效应**：若价格在 STRONG_DOWN 期间连续跌破多层，状态解除后的首轮将按 §3.2 集中买齐所有跌破层，可能以相同较高倍数集中在低位重仓。这是 SPEC §4.3 已披露的已知行为。
3. **RANGING 状态命名与趋势的语义脱节**：当 ADX $\ge 25$ 且属于上升趋势（-DI $\le$ +DI）时，因不满足瀑布前三项而默认落入 `RANGING`。此为 SPEC §4.3 明示的已知语义怪点，不影响交易逻辑与止盈系数。
4. **实时轮询下 REVERSAL 标签的单轮失效**：实盘 60 秒轮询下，换 K 线后第 1 轮标 `REVERSAL`，第 2 轮起因上一轮状态记录为 `REVERSAL`（不在 `STRONG_DOWN` 或 `SLOW_DOWN` 中），会重新变为 `RANGING`。SPEC §4.3 指明该标签仅用于日志，不影响交易动作。
