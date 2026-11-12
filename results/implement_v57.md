已在副本环境启动 `python3 backtest.py --all` 全量回测任务，正在执行测试集与压力测试，完成后将自动唤醒并汇总结果。
正在进行第二次回测运行以验证可复现性（SPEC §8.3(7)），完成后自动继续。
# SPEC v5.7 第 ⑥ 步单份实现报告 (acc3 / gemini-3.8-flash-medium)

---

## 一、基本信息与 SPEC 冻结核验

- **执行角色**：第 ⑥ 步实现（acc3 / gemini-3.8-flash-medium）
- **依据规范**：`/home/shenb/myQ/projects/spot-martingale/spec.md`（冻结版 v5.7）
- **MD5 校验**：`6969b432170928138cb787d73531a71b`（552 行，完全一致，未做任何修改）
- **工作目录**：
  - 隔离开发与验证副本：`/home/shenb/myQ/projects/spot-martingale-sens3/implement_v57/`
  - 目标线上代码库：`/home/shenb/myQ/projects/spot-martingale/`
- **线上环境状态**：
  - Live Monitor 进程 `PID 1881932`（正在内存中运行 v5.6 代码）保持存活，未 kill、未重启。
  - 线上数据库 `data/spot_martingale.sqlite3*` 与 `events.csv`、看板及 Solana monitor 均未受任何触碰。

---

## 二、代码改动清单（带精确文件与行号）

全部改动严格遵循 SPEC §8.3(6) 实现清单，未私自引入任何规避逻辑或参数变动：

### 1. `config.py`
- **L31**: `MAX_LAYERS = 9`（由 6 改为 9）。
- **L33**: `S = 32.01499791`（由 12.75603 改为 32.01499791，均匀 1.3× 路径 9 层累计系数）。
- **L36-37**: `BTC_LAYER_NOTIONALS` 与 `ETH_LAYER_NOTIONALS` 扩为 9 项静态列表：
  - BTC: `[93.71, 121.82, 158.36, 205.87, 267.63, 347.92, 452.30, 587.99, 764.40]`（求和 3,000.00 USDT）。
  - ETH: `[62.47, 81.21, 105.58, 137.25, 178.42, 231.95, 301.53, 391.99, 509.60]`（求和 2,000.00 USDT）。
- **L63**: `STOP_ADDING_DROP_PCT = 0.80`（停加仓价格阈值由 0.85 改为 0.80，即 `< 0.80 * C`）。
- **L68-75**: 新增动态倍数与趋势状态常量：
  - `MULT_DD_LT_2 = 1.2`
  - `MULT_DD_2_5 = 1.3`
  - `MULT_DD_5_10 = 1.4`
  - `MULT_DD_10_20 = 1.5`
  - `REGIME_ADX_THRESHOLD = 25.0`
  - `STRONG_DOWN_RET24 = -0.05`
  - `REBOUND_RET24 = 0.03`
  - `REBOUND_TP_COEFF = 1.008`
  - `SLOW_DOWN_COOLDOWN_SECS = 86400`
- **L123-145**: `FROZEN_VALUES` 字典同步补入上述所有常量，类型与数值完全对应。
- **L178-190 & L216-228**: 半进位公式 `_compute_half_up_layers` 与配置校验逻辑更新为 9 层口径。

### 2. `engine.py`
- **L50**: `layers` 表建表 DDL 中新增 `planned_notional_usdt REAL` 可空列。
- **L82-88**: `regime_log` 表建表 DDL 中新增 `regime TEXT, ret24_pct REAL, plus_di REAL, minus_di REAL` 列。
- **L108-124**: `init_db()` 中增加幂等 `ALTER TABLE` 迁移逻辑，安全处理既有数据库。
- **L305-345**: `calculate_indicators()` 在 Wilder 平滑循环中暴露每根的 `plus_di` 与 `minus_di`，并计算 `ret24`（上一收盘价相对 24 根前收盘价 `closes[-1]/closes[-25] - 1.0`）。
- **L464-494**: 新增 `classify_regime(adx, plus_di, minus_di, ret24, prev_regime)`：实现 STRONG_DOWN、SLOW_DOWN、REBOUND、RANGING 四态瀑布与 REVERSAL 过渡标签。
- **L496-518**: 新增 `get_dynamic_multiplier(dd, regime)`：按 DD 分档返回 `(multiplier, is_permitted)`；10%~20% 档校验非 STRONG_DOWN；DD > 20% 返回 `(None, False)`。
- **L520-535**: 废弃并删除 `get_layer_planned_notional`。
- **L538**: `compute_trigger_price` docstring 更新为 `n=2..9`。

### 3. `monitor.py`
- **L1-10**: 文件顶部 docstring 升级至 SPEC v5.7。
- **L155-168**: `startup_checks()` 启动阶段从最新一条非空 `regime_log` 恢复 `prev_valid_regime`。
- **L184-235**: 新增 `execute_transition_close()`（SPEC §9 第 18 条裁决 (d) 遗留组市价平仓逻辑，独立方法，不自动在普通启动中强执）。
- **L445-475**: `_execute_poll_cycle()` 严格按 §5.0(b2) 顺序，在退出前拉取 K 线判定双币种趋势状态并写入 `regime_log`。
- **L780-995**: `_process_group_exits_and_adds()`：
  - 满层定义更新为 `bought_layers == config.MAX_LAYERS`（9 层豁免 7d/14d 时间止损）。
  - REBOUND 状态下整组止盈读取 `config.REBOUND_TP_COEFF`（1.008）。
  - 7 天减半改读 OPEN 层实际成交名义 `l["notional_usdt"]`。
  - 停加仓判断线更新为 `c * config.STOP_ADDING_DROP_PCT`（`0.80 * C`）。
  - 加仓前检查 STRONG_DOWN（跳过并保留 PENDING）；检查 SLOW_DOWN 滚动 24 小时冷却（`now_utc - MAX(bought_utc) < 86400s`，仅检查 `layer_no >= 2` 的已买入层）。
  - 动态倍数乘上一层 `planned_notional_usdt` 递归计算当层计划名义。
  - 遇到 `planned_notional_usdt IS NULL` 时严格执行 §6.1 兜底：记 ERROR 日志、记一条 `STOP_ADDING` 类型 `risk_events`、置 `stop_adding=1` 并取消组内其余 PENDING。
  - 三步执行计划名义 → 截断 → 最小步长取整；UPDATE 时持久化写入 `planned_notional_usdt`。
  - SLOW_DOWN 状态下同轮最多买入 1 层。
- **L1010-1070**: `_consider_open_new_group()`：
  - 首层种子计划名义读取静态配置首项 `config.BTC/ETH_LAYER_NOTIONALS[0]`。
  - L1 插入时写入 `planned_notional_usdt`，L2..L9 PENDING 层保持 NULL。

### 4. `backtest.py`
- **L278-315**: `precompute_series_indicators()` 暴露 `plus_di`、`minus_di` 与 `ret24` 序列。
- **L390-410**: `BacktestEngine` 状态机跟踪 `self.regime` 与 `self.prev_regime`。
- **L505-525**: 每轮先按 (b2) 更新趋势状态。
- **L585-645**: `_process_group_exits()`：满层 9 层豁免时间止损；REBOUND 使用 1.008 系数；7 天减半按内存层实际成交 `notional` 扣减。
- **L680-770**: `_process_group_adds()`：0.80 停加仓线；STRONG_DOWN 暂停；SLOW_DOWN 24 小时冷却；动态倍数梯子；基于 `prev_lay["planned_notional"]` 递归；NULL 兜底；额度截断与步长取整。
- **L800-845**: `_consider_open_group()`：L1 种子计划名义写入层字典 `planned_notional` 字段。
- **L1240-1375**: 压力测试 1~3 均匀路径夹具扩建为 9 层，写入 `planned_notional`，固定间距读取配置除以 100。

### 5. `tests/test_spec_v51.py`
- **L36-37**: 校验静态名义列表长度 `len == 9`。
- **L43, 46-48**: `MAX_LAYERS == 9`，索引 9（第 10 层）抛出 `IndexError`。
- **L58, 67**: 建组夹具 `range(1, 10)`，层数验证 `cnt == 9`。
- **L370-375**: `test_item_09` 夹具 L1 补充 `planned_notional_usdt = 200.0`，PENDING 夹具改为 9 层。
- **L453-458**: `test_item_11` 夹具部署额度设为 2998.0，剩余额度 2.0 < 5.0 USDT 触发截断拒买与 `LAYER_CAP` 风控事件。
- **L584**: 9 项篡改列表校验启动冻结配置检验失败。

### 6. `tests/test_spec_v56_holdmax.py`
- **L4, 8, 65, 80-89, 95**: 满层定义与夹具改为 9 层（8 OPEN + 1 SOLD）。
- **L153-158, 181**: 9 层全 OPEN 整组止盈平仓验证。
- **L186-211, 238**: 3 OPEN + 6 PENDING 未满层 7d/14d 触发验证。
- **L262-326**: 回测引擎 Holdmax 9 层全满与未满对照验证。

### 7. `tests/test_spec_v57.py`（新增单测套件）
- **L46-104**: 趋势状态优先级瀑布与边界测试（ADX=25.0、RET24=-0.05、+0.03、±DI 相等、REVERSAL 判定）。
- **L106-160**: 动态倍数分档阶梯校验、计划名义递归与实际成交名义隔离验证。
- **L162-220**: SLOW_DOWN 加仓冷却 86400 秒精确边界测试（86399s 禁买，86400s 准买）。
- **L222-268**: NULL 计划名义安全兜底机制（写 ERROR 日志、记 STOP_ADDING 事件、停加仓、撤销 PENDING）。
- **L270-295**: 数据库架构迁移幂等性测试（多次重复 `init_db` 不报错、字段完整）。
- **L297-380**: 过渡平仓（TRANSITION_CLOSE）幂等性测试（旧 6 层组清仓、新 9 层组不受影响、二次执行无多余操作）。

---

## 三、单元测试结果

在隔离副本环境中运行 Python 标准测试发现套件：
```bash
python3 -m unittest discover tests
```
**运行结果**：
```
Ran 31 tests in 1.728s

OK
```
全部 31 个单元测试（覆盖 SPEC v5.1、v5.5、v5.6 及 v5.7 专项）**100% 全部通过**。

---

## 四、全量回测结果（实测数据）

运行指令：
```bash
python3 backtest.py --all
```

### 1. 训练集（2021-01-01 至 2024-12-31 UTC）
- **网格总小时数**：35,064 小时
- **异常读价与缺口**：
  - BTCUSDT: 价格错误 14 轮（缺失 14，无效 OHLC 0），>3 小时缺口 1 次，可疑价 1 轮（条件 a 接受 1，条件 b 接受 0）。
  - ETHUSDT: 价格错误 14 轮（缺失 14，无效 OHLC 0），>3 小时缺口 1 次，可疑价 1 轮（条件 a 接受 0，条件 b 接受 0）。
- **收益表现**：
  - 总盈亏：**-694.49 USDT**（期末权益：9,305.51 USDT）
  - 最大净值回撤：**15.44%**（收盘采样 15.44%，强平落地后 15.44%）
  - 利润因子 (Profit Factor)：**0.84**
  - 盈利月占比：**60.42%**（29 / 48 个有效月）
  - 成交笔数：总 3,723 笔（卖出 1,860 笔）

### 2. 测试集（2025-01-01 至 2026-10-31 UTC）
- **网格总小时数**：16,056 小时
- **异常读价与缺口**：
  - BTCUSDT: 价格错误 0 轮，>3 小时缺口 0 次，可疑价 0 轮。
  - ETHUSDT: 价格错误 0 轮，>3 小时缺口 0 次，可疑价 1 轮（条件 a 接受 1，条件 b 接受 0）。
- **收益表现**：
  - 总盈亏：**-471.96 USDT**（期末权益：9,528.04 USDT）
  - 最大净值回撤：**11.82%**（收盘采样 11.82%，强平落地后 0.00%）
  - 利润因子 (Profit Factor)：**1.10**
  - 盈利月占比：**63.64%**（14 / 22 个有效月）
  - 成交笔数：总 976 笔（卖出 481 笔）

### 3. 压力测试（确定性持仓测试 ST1–ST3 + 真实切片 ST4）
- **固定间距设定**：BTC $d = 0.0200$（配置系数 2.0），ETH $d = 0.0250$（配置系数 2.5）

- **ST1：合成路径 +50%（720 步）**
  - 总盈亏：**+3,577.96 USDT**（期末权益：13,577.96 USDT）
  - 最大回撤：**0.00%**，无触发 halt，期末权益显著大于初始本金。

- **ST2：合成路径 -50%（720 步）**
  - **(c) 双币组合**：
    - Halt 触发小时：**第 509 小时**（与 SPEC §8.2 第 508 行解析预期“约第 509 小时”精确吻合）。
    - 落地总盈亏：**-1,504.42 USDT**（期末权益：8,495.58 USDT）。
    - 最大回撤：**15.04%**（$\le 16.00\%$）。
    - 恢复验证：触发后 24 小时执行恢复，峰值权益重置为恢复权益，且无新开仓，`recovery_verified = True`。
  - **(a) BTC 单独**：
    - 亏损：**-1,310.02 USDT**（与 SPEC §8.2 第 508 行“BTC 亏 1,310.02”解析值绝对吻合）。
    - 最大回撤：**13.10%**（未达 15% halt 线，未触发 halt，$\le 16.00\%$）。
  - **(b) ETH 单独**：
    - 亏损：**-834.66 USDT**（与 SPEC §8.2 第 508 行“ETH 亏 834.66”解析值绝对吻合）。
    - 最大回撤：**8.35%**（未达 15% halt 线，未触发 halt，$\le 16.00\%$）。

- **ST3：合成先跌 -30% 后涨 +20%（360 + 360 步）**
  - 总盈亏：**-198.98 USDT**（期末权益：9,801.02 USDT）
  - 最大回撤：**10.00%**，未触发 halt。

- **ST4：真实切片（2022-06-01 至 2022-06-30 UTC，720 步）**
  - 总盈亏：**-144.33 USDT**（期末权益：9,855.67 USDT）
  - 最大回撤：**2.62%**，未触发 halt。
  - 利润因子：0.39，有效交易月 1 个（亏损）。

---

## 五、SPEC §8.3 验收标准逐项对照

| 编号 | 验收项 | SPEC 规定门槛 | 实测值 | 判定结论 | 说明 |
|---|---|---|---|---|---|
| **§8.3(1)** | 测试集总盈亏 | 扣成本后总盈亏 $> 0$ | **-471.96 USDT** | <font color="red">**FAIL**</font> | v5.7 规定不予自动豁免（§9 第 17 条）；实测未转正。 |
| **§8.3(2)** | 测试集最大净值回撤 | $\le 16.00\%$（双口径均须满足） | 收盘采样 **11.82%**，强平落地 **0.00%** | <font color="green">**PASS**</font> | 远低于 16.00% 安全红线。 |
| **§8.3(3)** | 利润因子 (PF) | $\ge 1.20$（按 Sell 已实现盈亏算） | **1.10** | <font color="red">**FAIL**</font> | 实测 1.10，未达 1.20 门槛。 |
| **§8.3(4)** | 盈利月占比 | $\ge 60.00\%$（分子>0，分母有平仓） | **63.64%**（14/22 月） | <font color="green">**PASS**</font> | 满足 $\ge 60\%$ 要求。 |
| **§8.3(5)** | 压力测试 2（-50%） | (c) 双币必须触发 halt 且最大回撤 $\le 16.00\%$、恢复重置峰值；(a)(b) 单币回撤 $\le 16.00\%$ | 双币第 509 小时触发 halt，落地回撤 **15.04%**，恢复校验成功；BTC 单币回撤 **13.10%**；ETH 单币回撤 **8.35%** | <font color="green">**PASS**</font> | 解析数值与触发小时（509h）与 SPEC 完全一致。 |
| **§8.3(6)** | 单元测试全过 | 31 项单元测试全部 PASS | 31 个测试运行通过 | <font color="green">**PASS**</font> | 覆盖 9 层、动态倍数、计划名义递归、86400s 边界、迁移与过渡平仓等。 |
| **§8.3(7)** | 结果可复现性 | 两次运行总盈亏容差 $\pm 0.01$ USDT | 连续两次回测全指标完全一致（误差 0.00） | <font color="green">**PASS**</font> | 完全确定性复现。 |

**验收总体结论**：第 2、4、5、6、7 项通过；**第 1 项（总盈亏 -471.96）与第 3 项（利润因子 1.10）未达标（FAIL）**。

---

## 六、逻辑缺口与未解决问题披露（只上报、不私自规避）

1. **测试集表现未满足通过条件**：
   - 测试集扣成本后总盈亏为 `-471.96 USDT`，PF 为 `1.10`。
   - SPEC §9 第 17 条明确指出：“v5.6 的豁免不延及本版本；v5.7 须测试集扣成本后总盈亏 > 0 才算通过。冻结时 moka 未予豁免”。本报告按规定真实上报，不做参数 Hack 或后门规避。
2. **SLOW_DOWN 冷却判定对于首个加仓层的语义界定**：
   - SPEC §4.3 规定：`MAX(bought_utc)` 取 layers 表中 `layer_no >= 2` 的最大值，无加仓买入时视为冷却期已过；L1 开组买入不计入。
   - 这意味着组开出后（已有 L1），跌破 L2 时的加仓是该组第 1 次加仓（无 `layer_no >= 2`），故 L2 不受 24 小时冷却限制，会立即买入；冷却限制仅在 L2 买入后对后续 L3..L9 加仓生效。代码严格按此语义实现。
3. **REVERSAL 状态语义怪点**：
   - 瀑布第 3 级 REBOUND 要求 `ADX < 25`。若行情属于上升趋势（$-DI \le +DI$）但 $ADX \ge 25$，即使 RET24 达到强反弹幅度，也不会落入 REBOUND，而会落入 RANGING。这是 SPEC 已知的语义怪点，按 SPEC 规定保持原样。

---

## 七、同步与合规声明

1. **代码同步**：已将副本验证通过的全部代码与测试文件（`config.py`、`engine.py`、`monitor.py`、`backtest.py`、`tests/test_spec_v51.py`、`tests/test_spec_v56_holdmax.py`、`tests/test_spec_v57.py`）完整同步回项目目录 `/home/shenb/myQ/projects/spot-martingale/`。
2. **运行中 Monitor 保护**：确认进程 PID `1881932` 毫发无损，一直在后台正常轮询。
3. **数据库与配置文件红线**：未触碰 `data/` 目录下的 SQLite 数据库与 `events.csv`，未修改任何 `spec*.md` 规范文件，未引入 API Key，未推送远程 Git 分支。
