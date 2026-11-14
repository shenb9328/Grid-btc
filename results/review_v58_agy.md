# SPEC v5.8 草稿（第 1 轮）独立盲审报告

- **评审对象**：[spec_v58_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md)（共 568 行）
- **基线对照**：[spec.md](file:///home/shenb/myQ/projects/spot-martingale/spec.md)（v5.7 冻结版，共 552 行）
- **前置校验**：
  - `md5sum /home/shenb/myQ/projects/spot-martingale/spec.md` = `6969b432170928138cb787d73531a71b`（与指令要求完全一致）
  - 全程严格只读，未修改任何 SPEC、代码、测试、数据库或后台进程。

---

## 一、总体判定结论

**判定结果：`FAIL`（不建议直接进入冻结评审，需小幅修订后复审）**

- **统计**：**`0 P0` / `4 P1` / `5 P2`**
- **核心结论**：
  1. **算术与回测证据无硬伤（`0 P0`）**：草稿中九层平铺名义算术（BTC `749.68` / `843.39`、ETH `499.76` / `562.23`、双币 `1,405.62`）、§5.3 触发价边界条件 $d < 2.5\%$、§8.3(5) 平铺路径 `-50%` 极限浮亏（BTC 约亏 `409 USDT / 4.1%`，ETH 约亏 `270 USDT / 2.7%`），以及 `v58_exp_e6b_bt.log` 的全部回测/压力测试数字，经独立重算与逐项核对**全部精确吻合**；同时对证据污染（3 轮 15 变体看测试集）、微利（`+52.34`）、欠配效应（`E2c` 全额等额测试集 `-963.77`）及不豁免 §8.3(1) 的诚实披露非常充分。
  2. **存在 4 项 `P1` 级实现一致性、过渡状态与验收定义缺陷**，若不修正直接按 §8.3(6) 落地或交由 `moka` 裁决，会导致：
     - **`P1-1`**：[monitor.py](file:///home/shenb/myQ/projects/spot-martingale/monitor.py) 在同一轮中分两处独立评估 §4.1 开组条件（[monitor.py:623-648](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L623-L648) 写 `regime_log.allow_new_group` 与 `REGIME_BLOCK`，以及 [monitor.py:1084-1096](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1084-L1096) 实际执行开组），且单轮重复拉取两次 K 线（[monitor.py:348](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L348) 与 [monitor.py:1069](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1069)）。§8.3(6) 仅笼统写“开组条件处加位置过滤”，未点明 [monitor.py:623-648](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L623-L648)，极易漏改导致 `regime_log.allow_new_group` 及 `/api/spot/regime` 与实际开组门禁脱节。
     - **`P1-2`**：V5.8-4 与 §9 第 24 条声称“已有 9 层组状态兼容：计划名义列已持久化，后续加仓按平铺倍数递归”，但漏掉了 [monitor.py:952-964](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L952-L964) 是用 `prev_planned * mult` 递推的事实——**仅当切换瞬间现存 v5.7 组仍停留在 L1 时**后续各层才会保持首层种子额；一旦在切换前触发了 L2+（按 v5.7 的 `1.2x~1.5x` 写入了膨胀的 `planned_notional_usdt`），切换到 v5.8 后 `1.0x` 会将该膨胀名义额永久锁定并传递到后续所有层，且现存组（Group 3/4）是在未经过 `Position <= 0.35` 过滤下开出的。
     - **`P1-3`**：§8.3(6) 遗漏了对既有单测 [tests/test_spec_v57.py:108-159](file:///home/shenb/myQ/projects/spot-martingale/tests/test_spec_v57.py#L108-L159) 的同步修改要求，该单测硬编码断言了 `(1.2, True)`、`(1.3, True)`、`(1.4, True)`、`(1.5, True)`，把 `config.py` 倍数改为 `1.0` 后既有单测套件会直接报错失败。
     - **`P1-4`**：V5.8-5 与 §9 第 25 条提议“30 天窗口按 §8.3 同口径评估（总盈亏 > 0、PF >= 1.2、回撤 <= 16%、**月胜率 >= 60%**）”，但 30 天窗口仅跨 1～2 个自然月（跨 2 个月时只要其中 1 个残月为负，月胜率即 `50% < 60%` 必然 FAIL），未定义继承 v5.7 持仓时的 30 天盈亏/回撤基准点，且与 §8.4 原文（“不定义模拟盘验收标准…另起 SPEC”）存在文本冲突。

---

## 二、分级问题清单（P0 / P1 / P2）

### P0 级（致命：数学算错、逻辑自相矛盾、回测数字对不上、虚假声明）

- **无（0 项）**。

---

### P1 级（重要：跨章节漏改、边界条件未定义、代码与 SPEC 映射缺口、风险未披露）

#### [P1-1] §8.3(6) 未指明 `monitor.py` 存在两处独立评估 §4.1 开组条件的代码路径及单轮二次拉取 K 线问题，极易导致 `regime_log.allow_new_group` 与实际开组门禁不一致
- **涉及位置**：
  - [spec_v58_draft.md:530](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L530)（§8.3(6) v5.8 实现说明）
  - [spec_v58_draft.md:391](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L391)（§6.1 `regime_log` 与 `risk_events`）
  - [spec_v58_draft.md:433](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L433)（§6.3 `/api/spot/regime`）
  - [monitor.py:348](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L348)、[monitor.py:607-648](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L607-L648)、[monitor.py:1069-1096](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1069-L1096)
  - [engine.py:204-346](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L204-L346)、[backtest.py:799-805](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L799-L805)、[backtest.py:937-1054](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L937-L1054)
- **问题详情**：
  1. 在 [monitor.py](file:///home/shenb/myQ/projects/spot-martingale/monitor.py) 中，§4.1 的开组条件被复制在**两处**分别计算：
     - **第一处（步骤 `(b2)`，[monitor.py:623-648](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L623-L648)）**：计算 `cond1`（`adx < ADX_THRESHOLD`）、`cond2`（`in_range`）、`cond3`（`atr_ratio <= 1.5`）、`cond4`（无 `OPEN` 组且未处于停机恢复禁开期），用于写入 `regime_log.allow_new_group`（同时供看板 `/api/spot/regime` 读取）并在阻断时写入 `risk_events` 的 `REGIME_BLOCK` 事件。
     - **第二处（步骤 `(d)`，[monitor.py:1084-1096](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1084-L1096)）**：在 `_consider_open_new_group` 内再次计算 `cond1..cond4` 决定是否真正开组。
  2. [spec_v58_draft.md:530](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L530) 仅写“**`monitor.py` 与 `backtest.py` 的开组条件处加位置过滤**”。在实验阶段（参看 `results/v58_exp_summary.md:11`），E1b 只改了回测开组判断；若开发阶段只改 `_consider_open_new_group`（[monitor.py:1084-1096](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1084-L1096)）而漏改 [monitor.py:623-648](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L623-L648)，则当 `Position > 0.35` 拦截开组时，`regime_log.allow_new_group` 和 `/api/spot/regime` 仍会错误显示 `allow_new_group = 1`，且 `risk_events` 不会记录 `REGIME_BLOCK`。
  3. 此外，[monitor.py:348](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L348) 在主轮询开头已拉取了 500 根已收盘 1h K 线（`symbol_klines[sym]`）并在 [monitor.py:607](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L607) 计算了指标，但 `_consider_open_new_group`（[monitor.py:1069](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1069)）又重新向 Binance 发起第二次 K 线请求并重算指标。若两次请求之间恰好跨整点，步骤 `(b2)` 与步骤 `(d)` 看到的 `close[-1]`、`HH_480`、`LL_480` 会不一致。
- **修复建议**：
  - 在 §8.3(6) 明确要求：
    1. 在 [engine.py:204-346](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L204-L346)（`calculate_indicators`）与 [backtest.py:937-1054](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L937-L1054)（`precompute_series_indicators`）返回的指标字典中统一新增 `'position'` 与 `'pos_ok'`（当 `n < 500` 或 `hh_480 <= ll_480` 时 `pos_ok = False`）。
    2. 明确点名 [monitor.py:623-648](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L623-L648)（`regime_log.allow_new_group` 及 `REGIME_BLOCK` 日志详情）与 [monitor.py:1084-1096](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1084-L1096)（`_consider_open_new_group`）以及 [backtest.py:799-805](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L799-L805) 均须同步接入 `pos_ok`（建议 `_consider_open_new_group` 直接复用同轮已拉取的 `symbol_klines[sym]`，消除同轮二次拉取 K 线的跨整点漂移）。

#### [P1-2] V5.8-4 与 §9 第 24 条关于“现存 v5.7 9 层组不平仓、状态兼容、后续加仓按平铺倍数递归”的表述存在未披露的混合状态漏洞
- **涉及位置**：
  - [spec_v58_draft.md:163](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L163)（V5.8-4 模拟盘过渡默认说明）
  - [spec_v58_draft.md:561](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L561)（§9 第 24 条裁决项）
  - [monitor.py:952-964](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L952-L964)（实盘动态倍数递推逻辑）
  - 模拟盘数据库 `data/spot_martingale.sqlite3`（当前 `OPEN` 组：Group 3 `BTCUSDT`、Group 4 `ETHUSDT`）
- **问题详情**：
  1. 核查当前模拟盘 SQLite 数据库可见：旧 v5.6 的 Group 1、2 已于 `2026-11-12T05:37:39Z/40Z` 执行 `TRANSITION_CLOSE`；当前处于 `OPEN` 状态的是 v5.7 下新开的 Group 3（BTC，`id=3`，`opened_utc='2026-11-12T05:41:48Z'`，L1 已买 `planned_notional_usdt=93.71`，L2~L9 为 `PENDING`）和 Group 4（ETH，`id=4`，`opened_utc='2026-11-12T05:41:48Z'`，L1 已买 `planned_notional_usdt=62.47`，L2~L9 为 `PENDING`）。
  2. [monitor.py:952-964](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L952-L964) 计算第 $n$ 层计划名义额的代码是：读取第 $n-1$ 层已持久化的 `planned_notional_usdt`（即 `prev_planned`），再令 `planned_notional = round(prev_planned * mult, 2)`。
  3. **关键边界**：
     - **仅当切换到 v5.8 时现存组仍停留在仅买完第 1 层（`max_bought_layer == 1`）**，后续 L2~L9 按 `prev_planned * 1.0` 递推才会保持首层种子额（`93.71` / `62.47`）。
     - **若在 v5.8 冻结重启之前，现存 v5.7 组已经触发了第 2 层或更高层加仓**（例如按 v5.7 的 `1.2x~1.5x` 写入了 L2 `112.45` 或 `121.82`、L3 `146.19` 等），则切换到 v5.8（`mult = 1.0`）后，后续所有剩余层 `round(prev_planned * 1.0, 2)` **会将切换前最后一层膨胀后的名义额永久复制下去**（而非回到平铺种子额 `93.71` / `62.47`），形成既非 v5.7 也非 v5.8 的畸形混合仓位！
     - 此外，现存 Group 3、Group 4 在 `05:41:48Z` 开组时**并未经过 v5.8 新增的 `Position <= 0.35` 位置过滤检验**。
- **修复建议**：
  - 在 V5.8-4（第 163 行）与 §9 第 24 条（第 561 行）中如实披露上述两项事实：
    1. 现存 v5.7 组（Group 3/4）开组时未经过 `Position <= 0.35` 过滤；
    2. “不平仓直接兼容平铺额”的前提是**切换时刻该组仅买完 L1（未发生 L2+ 动态倍数加仓）**；若切换时刻已买过 L2+，要么在代码切换时重置未买层的基准为 `seed_notional_usdt`（L1 的 `planned_notional_usdt`），要么由 `moka` 裁决执行 `TRANSITION_CLOSE`。

#### [P1-3] §8.3(6) 遗漏对既有单元测试 `tests/test_spec_v57.py` 的同步更新说明，直接修改 `config.py` 会导致现有测试套件报错失败
- **涉及位置**：
  - [spec_v58_draft.md:530](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L530)（§8.3(6) v5.8 实现说明）
  - [tests/test_spec_v57.py:108-159](file:///home/shenb/myQ/projects/spot-martingale/tests/test_spec_v57.py#L108-L159)（`test_dynamic_multiplier_ladder_and_recursion`）
- **问题详情**：
  - [tests/test_spec_v57.py:111-130](file:///home/shenb/myQ/projects/spot-martingale/tests/test_spec_v57.py#L111-L130) 直接调用 `engine.get_dynamic_multiplier(...)` 并硬编码断言返回值为 `(1.2, True)`、`(1.3, True)`、`(1.4, True)`、`(1.5, True)`、`(1.5, False)`；同函数第 141–156 行还按 `[1.2, 1.3, 1.4, 1.5, ...]` 校验了 BTC `909.61` 与 ETH `606.38` 的递归序列。
  - 当按 §8.3(6) 将 [config.py:124-127](file:///home/shenb/myQ/projects/spot-martingale/config.py#L124-L127) 的四个 `MULT_DD_*` 改为 `1.0` 后，`tests/test_spec_v57.py` 中的 `test_dynamic_multiplier_ladder_and_recursion` 会立即 **FAIL**。
  - 但 [spec_v58_draft.md:530](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L530) 写的是“单测补：位置过滤…、平铺倍数…、ADX 30…；其余 v5.7 清单已实现，不重复改”，未指出必须同步更新或重构 `test_spec_v57.py:108-159` 中依赖 `config.MULT_DD_*` 默认值的断言。
- **修复建议**：
  - 在 §8.3(6) 中明确说明：更新 [tests/test_spec_v57.py:108-159](file:///home/shenb/myQ/projects/spot-martingale/tests/test_spec_v57.py#L108-L159)（或将其拆分为显式 mock 旧阶梯倍数的算术测试 + 新增 `test_spec_v58.py` 校验默认 `1.0` 平铺倍数），确保全量 `pytest` 通过。

#### [P1-4] V5.8-5 与 §9 第 25 条“30 天前向验证窗口按 §8.3 同口径评估”在“月胜率 >= 60%”与跨期持仓口径上存在统计与定义缺陷，且与 §8.4 原文冲突
- **涉及位置**：
  - [spec_v58_draft.md:164](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L164)（V5.8-5 前向验证提议）
  - [spec_v58_draft.md:528](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L528)（§8.3(4) 月胜率定义）
  - [spec_v58_draft.md:533-534](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L533-L534)（§8.4 模拟盘观察说明）
  - [spec_v58_draft.md:562](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L562)（§9 第 25 条裁决项）
- **问题详情**：
  1. **30 天窗口套用“自然月胜率 >= 60%”在数学上失真**：§8.3(4) 定义月胜率分母为“有 ≥1 笔已平仓成交的自然月数”。30 天窗口最多只覆盖 **1 个或 2 个自然月**（且首尾自然月往往只有几天样本）：
     - 若分母为 1 个自然月，月胜率只能是 `0%` 或 `100%`（等价于已实现盈亏 > 0，指标冗余）；
     - 若跨 2 个自然月且两月均有平仓，月胜率只能取 `0%`、`50%`、`100%`——只要其中某一个仅有几天的残月已实现盈亏为 `-0.01 USDT`，哪怕 30 天总盈亏显著为正，月胜率也是 `1/2 = 50% < 60%`，触发一票否决 FAIL！
  2. **若 V5.8-4 采取“不平仓过渡”，30 天窗口的起算基准未定义**：切换时刻已存在的 Group 3/4 在窗口内平仓或浮亏变动时，30 天“总盈亏 > 0”与“最大回撤 <= 16%”是以切换时刻的 `total_equity(t_0)` 为基准（`equity(t_end) - equity(t_0) > 0`、窗口内局部 peak-to-trough DD），还是沿用 DB 历史 `INITIAL_CAPITAL = 10000`？
  3. **与 §8.4 原文冲突**：[spec_v58_draft.md:533-534](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L533-L534) 仍保留旧文“v3 不定义模拟盘验收标准。回测通过后，由 Muse 另起 SPEC 补充模拟盘的运行时长、观察指标与上线标准”，而 V5.8-5 与 §9 第 25 条已在本文档中设定了 30 天 DRY_RUN 前向验证期及验收标准。
- **修复建议**：
  - 在 V5.8-5 与 §9 第 25 条中明确：
    1. 30 天窗口不适用“自然月胜率 >= 60%”（或改为按 4 个完整 7 天周统计周胜率，或仅考核 30 天窗口内总净值增量 `> 0`、窗口内平仓交易 `PF >= 1.2`、窗口内最大回撤 `<= 16%` 及最低成交笔数下限）；
    2. 明确 30 天窗口的起算净值基准（以 v5.8 启动时刻 $t_0$ 的账户净值为基准计算窗口期 ΔEquity 与窗口期最大回撤）；
    3. 在 §8.4 补充一句指向 V5.8-5 与 §9 第 25 条的交叉引用，消除与“不定义模拟盘验收标准”的文本冲突。

---

### P2 级（次要：措辞严谨性、枚举遗漏、旧注释同步提示）

#### [P2-1] §6.1 数据库表结构枚举仍遗漏 `TRANSITION_CLOSE`
- **涉及位置**：[spec_v58_draft.md:386](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L386)、[spec_v58_draft.md:388](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L388)、[spec_v58_draft.md:555](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L555)
- **问题详情**：§9 第 18(d) 条（第 555 行）及代码 [engine.py:109](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L109)、[monitor.py:223-227](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L223-L227) 均使用 `TRANSITION_CLOSE` 作为 `grid_groups.close_reason` 与 `trades.reason` 的合法值（当前实盘 DB 中 Group 1、2 的 `close_reason` 即为 `'TRANSITION_CLOSE'`），但 §6.1 第 386 行 `grid_groups(... close_reason[TAKE_PROFIT/ALL_SOLD/TIME_STOP_7D/TIME_STOP_14D/DRAWDOWN_HALT])` 与第 388 行 `trades(... reason[...])` 的枚举列表仍未补入 `TRANSITION_CLOSE`。
- **修复建议**：在 §6.1 第 386 行与第 388 行的枚举中补入 `TRANSITION_CLOSE`。

#### [P2-2] §6.1 `regime_log` / `risk_events` 与 §6.3 `/api/spot/regime` 未明确 `allow_new_group` 包含条件 7（位置过滤）
- **涉及位置**：[spec_v58_draft.md:391](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L391)、[spec_v58_draft.md:433](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L433)
- **问题详情**：§4.1 新增了第 7 条开组条件（`Position <= 0.35`），§6.1 `regime_log(allow_new_group)`、`risk_events(REGIME_BLOCK)` 与 §6.3 `/api/spot/regime` 应明确 `allow_new_group` 为 §4.1 全部开组条件（含条件 1/2/3/4/5/7）的合取结果。
- **修复建议**：在 §8.3(6) 或 §6.1 简要注明 `allow_new_group` 及 `REGIME_BLOCK` 涵盖新增的位置过滤条件。

#### [P2-3] §5.3 “故触发价条件即买满 9 层的充要条件（计划名义算术下）”措辞略过满，宜前置说明路径前提
- **涉及位置**：[spec_v58_draft.md:364](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L364)
- **问题详情**：第 364 行写“九层累计……恒远低于硬上限……不会因上限截断丢层。故触发价条件即买满 9 层的充要条件（计划名义算术下）”，紧接着下一句才限定“在逐层恰好触发的最小下跌路径下”。严格来说，若价格在买齐前已满 7 天触发 `TIME_STOP_7D`（`stop_adding = 1`），或在 `STRONG_DOWN` / `SLOW_DOWN` 阻断期间单根 K 线直接跳空跌破 `0.80C`（[monitor.py:897](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L897) / [backtest.py:690](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L690) 先执行 `< 0.80C` 取消剩余 `PENDING` 层），即便 $d < 2.5\%$ 也不会买满 9 层。
- **修复建议**：将该句微调为“故在未触发 7 天减半且未单步跳空跌破 `0.80C` 的逐层触发路径下，触发价条件 $d < 2.5\%$ 即买满 9 层的充要条件（计划名义算术下）”。

#### [P2-4] §8.3(6) 未提示清理代码中残留的 v5.7 硬编码注释与 docstring
- **涉及位置**：[spec_v58_draft.md:530](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L530)
- **问题详情**：当前代码多处注释与 docstring 硬编码了 v5.7 参数，若不随 v5.8 同步更新会造成代码阅读误导：
  - [config.py:3](file:///home/shenb/myQ/projects/spot-martingale/config.py#L3)（`Frozen under SPEC v5.7`）、[config.py:57](file:///home/shenb/myQ/projects/spot-martingale/config.py#L57)（`ADX_THRESHOLD = 25.0  # < 25`）
  - [engine.py:2](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L2)、[engine.py:502-505](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L502-L505)（`get_dynamic_multiplier` docstring 仍写 `DD < 2%: 1.2 ... 1.5`）
  - [monitor.py:4](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L4)、[monitor.py:137](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L137)、[monitor.py:1085](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1085)（`# 1. ADX < 25`）
  - [backtest.py:2](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L2)
- **修复建议**：在 §8.3(6) 顺带提醒同步更新上述 docstring 与行内注释。

#### [P2-5] §9 第 21–25 条裁决项格式与前置已裁决项的衔接微瑕
- **涉及位置**：[spec_v58_draft.md:558-562](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L558-L562)
- **问题详情**：第 1–20 条已全部标为“已裁决”（其中 §4.3 第 320 行已正确把第 20 条从 v5.7 的“冻结时由 moka 裁决”更新为“已由 moka 裁决接受”），第 21–25 条标为“待裁决”，状态区分清晰；但第 24 条（过渡策略）与第 25 条（30 天前向验证）缺少 `[P1-2]` 与 `[P1-4]` 所述的关键边界选项，补充后更便于 `moka` 一次性拍板。

---

## 三、11 项评审范围逐项核查明细

### 1. 改动跨章节一致性（V5.8-1 位置过滤、V5.8-2 平铺 1.0 倍数、V5.8-3 开组 ADX < 30）

经对 [spec.md](file:///home/shenb/myQ/projects/spot-martingale/spec.md) 与 [spec_v58_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md) 全文逐行 diff 核查：

| 章节 | 核查内容 | 核查结果 |
| :--- | :--- | :--- |
| **§1.1** ([L191](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L191)) | 500 根 1h K 线用途补入“及位置 `Position`（§3.4）” | 一致 |
| **§2** ([L230](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L230), [L235](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L235)) | 九层计划名义全 `93.71`（累计 `843.39 < 3,000.00`）与全 `62.47`（累计 `562.23 < 2,000.00`）；平铺倍数表全 `1.0` | 一致 |
| **§3.2 / §3.3 / §3.4** ([L253](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L253), [L276](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L276)) | §3.2 区分开组 `< 30` 与趋势门 `>= 25 / < 25`；§3.4 完整定义 `Position` 公式与 `HH_480 <= LL_480` 退化处理 | 一致 |
| **§4.1** ([L281](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L281), [L287](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L287)) | 条件 1 改为 `ADX(14) < 30` 并注明与 `REGIME_ADX_THRESHOLD = 25` 独立；新增条件 7 `Position <= 0.35` | 一致 |
| **§4.2 / §4.3** ([L314](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L314), [L319-320](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L319-L320)) | 4 档倍数全部更新为 `1.0`；说明递归退化为常数序列且保留框架与 `10%~20%` `STRONG_DOWN` 禁加仓检查 | 一致 |
| **§5.0 / §5.3** ([L338](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L338), [L361](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L361), [L364](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L364)) | §5.0 注明平铺后每层计划名义恒等于首层种子额；§5.3 重写平铺下的触发价充要条件与累计金额 | 一致（细微措辞见 `[P2-3]`） |
| **§6.1 / §6.3** ([L386-391](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L386-L391), [L433](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L433)) | 数据库表与接口定义 | 见 `[P1-1]`、`[P2-1]`、`[P2-2]` |
| **§7.1** ([L462-485](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L462-L485)) | 新增 `POSITION_MAX = 0.35`，`MULT_DD_* = 1.0`，`ADX_THRESHOLD = 30.0`，保留 `REGIME_ADX_THRESHOLD = 25.0` | 一致 |
| **§8.2 / §8.3 / §8.4** ([L513](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L513), [L525-534](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L525-L534)) | 压测表更新 ST4 为 `-96.52`（`1.54%`）、§8.3(1)/(5)/(6)/(7) 同步更新 | 见 `[P1-1]`、`[P1-3]`、`[P1-4]` |
| **§9** ([L545](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L545), [L554](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L554), [L558-562](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L558-L562)) | 第 7、16 条加历史演进注记；新增第 21–25 条待裁决项 | 见 `[P1-2]`、`[P1-4]`、`[P2-5]` |

---

### 2. 独立算术重算与证明

#### (a) 九层平铺计划名义（BTC / ETH 前 8 层、9 层累计与双币上限校验）
- **首层种子名义额（§2 既有公式）**：
  - 9 层 1.35 倍等比权重和：$\sum_{k=0}^{8} 1.35^k = 32.01499791$
  - BTC 首层种子：$\text{round}(3000.00 / 32.01499791, 2) = \mathbf{93.71\text{ USDT}}$
  - ETH 首层种子：$\text{round}(2000.00 / 32.01499791, 2) = \mathbf{62.47\text{ USDT}}$
- **平铺倍数（`mult = 1.0`）递推**：
  - 对任意层 $n \in \{2..9\}$，$\text{planned}_n = \text{round}(\text{planned}_{n-1} \times 1.0, 2) = \text{planned}_1$。
  - **BTC**：
    - L1..L8 累计：$93.71 \times 8 = \mathbf{749.68\text{ USDT}}$（与草稿 §5.3 第 364 行 `749.68` 完全一致）
    - L1..L9 累计：$93.71 \times 9 = \mathbf{843.39\text{ USDT}} < 3,000.00\text{ USDT}$（余量 `2,156.61 USDT`，与草稿 §2 第 230 行、§5.3 第 364 行完全一致）
  - **ETH**：
    - L1..L8 累计：$62.47 \times 8 = \mathbf{499.76\text{ USDT}}$（与草稿 §5.3 第 364 行 `499.76` 完全一致）
    - L1..L9 累计：$62.47 \times 9 = \mathbf{562.23\text{ USDT}} < 2,000.00\text{ USDT}$（余量 `1,437.77 USDT`，与草稿 §2 第 230 行、§5.3 第 364 行完全一致）
  - **双币同时满 9 层累计**：
    - 计划名义合计：$843.39 + 562.23 = \mathbf{1,405.62\text{ USDT}}$
    - 实际下单经 `floor_to_step` 向下取整后，$\text{actual\_notional} \le \text{planned\_notional}$，故双币实际已投合计恒 $\le 1,405.62\text{ USDT} < \text{TOTAL\_DEPLOYED\_CAP\_USDT} (5,000.00\text{ USDT})$，占总预算上限仅 `28.11%`，占单币上限同样仅 `28.11%`。

#### (b) §5.3 触发价条件 $d < 2.5\%$ 的必要性与充分性证明
- **定义**：第 $n$ 层触发价为 $P_n = C \times (1 - (n-1)d)$，故第 9 层触发价为 $P_9 = C \times (1 - 8d)$。
- **必要性（Necessity）**：
  - 加仓触发条件为严格不等式 `last < P_n`（[monitor.py:926](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L926)、[backtest.py:725](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L725)），而 §4.2 停止加仓条件为严格不等式 `last < 0.80 * C`（[monitor.py:897](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L897)、[backtest.py:690](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L690)），且代码中 `< 0.80 * C` 停止加仓检查**先于**逐层加仓检查执行。
  - 若 $d \ge 2.5\%$，则 $8d \ge 0.20 \implies P_9 = C(1 - 8d) \le 0.80C$。此时任何满足第 9 层触发条件 `last < P_9` 的价格必然同时满足 `last < 0.80C`，从而在同轮优先触发 §4.2 停止加仓并把第 9 层置为 `CANCELLED`。因此 $d < 2.5\%$（即 $P_9 > 0.80C$）是能买进第 9 层的**严格必要条件**。
- **充分性（Sufficiency）**：
  - 当 $d < 2.5\%$ 时，$P_9 = C(1 - 8d) > 0.80C$，存在非空价格区间 $[0.80C, P_9)$，当价格逐层落入该区间时，`last < P_9` 成立且 `last < 0.80C` 不成立。
  - 同时，平铺倍数 `1.0` 下前 9 层累计计划名义（BTC `843.39`、ETH `562.23`）恒远小于单币上限（`3,000` / `2,000`）与总上限（`5,000`），剩余额度截断（`trunc_planned`）恒等于 `planned`，永不触发上限丢层。
  - **关于步长向下取整是否改变买满结论的严格验证**：在平铺倍数下，$\text{planned}_n \equiv \text{planned}_1$，而成交价 $P_n < C$ 随层数递增而降低，因此第 $n$ 层的理论数量 $\text{planned}_n / P_n > \text{planned}_1 / C$ **随层数递增而严格增大**！只要第 1 层在开组价 $C$ 处满足最小步长与 `5 USDT` 门槛（BTC $C \le 93,710$、ETH $C \le 6,247$ 时 $\text{qty}_1 \ge 1\text{ step}$ 且在 $P_n \ge 0.80C$ 处实际名义额 $\ge 0.80 \times P_1 \cdot \text{step} > 5\text{ USDT}$），后续第 2～9 层在 $P_n \in [0.80C, C)$ 处必然也满足 $\text{qty}_n \ge \text{qty}_1 \ge 1\text{ step}$ 且实际名义额 $\ge 5\text{ USDT}$。因此草稿“取整不改变买满结论”在算术上成立（唯需注意 `[P2-3]` 所述的非跳空/未满 7 天路径前提）。

#### (c) §8.3(5) 平铺路径 `-50%` 极限亏损独立重算
- **设定**：在中心价 $C$ 开组，价格沿最小步幅 $d_{\min}$（BTC $d = 0.8\%$，ETH $d = 1.0\%$，此为触发价最高、买在最高位、跌至 $0.5C$ 时浮亏最大的最坏步幅）逐层恰好触发买满 9 层，每层名义额为 $N_0$，中途不卖出，一直持有至价格跌到 $0.5C$：
  - 第 $n$ 层（$n=1..9$）买入价为 $P_n = C(1 - (n-1)d)$，在 $0.5C$ 时的剩余市值为 $V_n = N_0 \cdot \frac{0.5C}{P_n} = N_0 \cdot \frac{0.5}{1 - (n-1)d}$。
  - 9 层买入手续费与滑点（单边 `0.10% + 0.02% = 0.12%`）：$\text{Cost}_{\text{buy}} = 9 N_0 \times 0.0012$。
- **逐项重算结果**：
  - **BTC（$N_0 = 93.71\text{ USDT}$，$d = 0.8\%$）**：
    - 9 层总投入名义额：$9 \times 93.71 = 843.39\text{ USDT}$
    - 跌至 $0.5C$ 时 9 层剩余市值：$\sum_{n=1}^{9} 93.71 \times \frac{0.5}{1 - 0.008(n-1)} = 435.8339\text{ USDT}$
    - 账面浮亏：$843.39 - 435.8339 = 407.5561\text{ USDT}$
    - 买入手续费+滑点：$843.39 \times 0.0012 = 1.0121\text{ USDT}$
    - **含买成本总亏损**：$407.5561 + 1.0121 = \mathbf{408.57\text{ USDT}} \approx \mathbf{409\text{ USDT}}$，占初始本金 `10,000 USDT` 的 **`4.09%`（约 `4.1%`）**。（注：若取 $d = 2.0\%$，含买成本总亏损为 `384.59 USDT`，亏损更小）。**与草稿第 529 行“约亏 409（约 4.1%）”完全吻合。**
  - **ETH（$N_0 = 62.47\text{ USDT}$，$d = 1.0\%$）**：
    - 9 层总投入名义额：$9 \times 62.47 = 562.23\text{ USDT}$
    - 跌至 $0.5C$ 时 9 层剩余市值：$\sum_{n=1}^{9} 62.47 \times \frac{0.5}{1 - 0.01(n-1)} = 293.0402\text{ USDT}$
    - 账面浮亏：$562.23 - 293.0402 = 269.1898\text{ USDT}$
    - 买入手续费+滑点：$562.23 \times 0.0012 = 0.6747\text{ USDT}$
    - **含买成本总亏损**：$269.1898 + 0.6747 = \mathbf{269.86\text{ USDT}} \approx \mathbf{270\text{ USDT}}$，占初始本金 `10,000 USDT` 的 **`2.70%`（约 `2.7%`）**。（注：若取 $d = 2.0\%$，含买成本总亏损为 `256.38 USDT`）。**与草稿第 529 行“约亏 270（约 2.7%）”完全吻合。**

---

### 3. 位置过滤定义与边界（§3.4、§4.1 条件 7、§7.1、§8.3(6)）

1. **公式与符号边界**：`Position = (close[-1] - LL_480) / (HH_480 - LL_480) <= 0.35`（含等号 `0.35`），当 `HH_480 <= LL_480` 时不除零、直接判开组条件不成立（Fail-closed），数学定义无歧义。
2. **与条件 2（`LL_480 < close[-1] < HH_480`）的组合关系**：
   - 当 `close[-1] == LL_480` 时，虽然 `Position = 0 <= 0.35` 为真，但条件 2 的严格左不等号 `LL_480 < close[-1]` 为假，交集仍为不触发。草稿在 §4.1 条件 7（第 287 行）已显式写明这一组合行为：`实际有效开组区间为 LL_480 < close[-1] <= LL_480 + 0.35 * (HH_480 - LL_480)`。
3. **实盘与回测的 480 根已收盘 K 线切片等价性核查**：
   - 实盘 [monitor.py:119](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L119) `klines_1h` 通过 `b[6] < now_ms` 过滤掉未收盘 K 线，保留最近 500 根已收盘 K 线；[engine.py:313-325](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L313-L325) 取 `highs[n - 480:n]`、`lows[n - 480:n]` 计算 `hh_480`、`ll_480`，并取 `closes[-1]` 作为 `prev_close`。
   - 回测 [backtest.py:1031-1051](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L1031-L1051) 在第 `i` 根已收盘 K 线处取 `highs[i - 479:i + 1]`（共 480 根，含 `i`）与 `closes[i]` 作为 `prev_close`。
   - **结论**：只要实盘不在同轮内二次重新拉取 K 线（见 `[P1-1]`），实盘与回测的 `prev_close`、`hh_480`、`ll_480` 切片窗口完全等价。

---

### 4. 平铺倍数自洽性与 §5.3 边界重写（§2、§4.2、§5.0、§5.3、§7.1）

- **递归公式与三步截断自洽性**：
  - 在 `MULT_DD_LT_2 = MULT_DD_2_5 = MULT_DD_5_10 = MULT_DD_10_20 = 1.0` 下，[engine.py:500-520](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L500-L520) 的 `get_dynamic_multiplier` 在 `DD <= 20%` 时恒返回 `mult = 1.0`；在 `10% <= DD <= 20%` 且 `STRONG_DOWN` 时返回 `(1.0, False)`；在 `DD > 20%` 时返回 `(None, False)`。
  - 三步截断（`planned -> trunc_planned -> compute_order_qty`）与 `planned_notional_usdt` 列持久化逻辑无需改动即可自然退化为常数序列。
- **§5.3 充要条件完备性**：算术与步长取整均已验证成立（见第 2(b) 节），唯路径前提表述建议按 `[P2-3]` 微调。

---

### 5. ADX 双阈值（`ADX_THRESHOLD = 30.0` vs `REGIME_ADX_THRESHOLD = 25.0`）

经核查全仓代码对两个常量的引用位置：
- `config.ADX_THRESHOLD` **仅**用于：[monitor.py:624](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L624)、[monitor.py:1086](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1086)、[backtest.py:799](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L799)（均为 §4.1 开组条件 1）。
- `config.REGIME_ADX_THRESHOLD` **仅**用于：[engine.py:487-491](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L487-L491)（`classify_regime` 瀑布流分类）。
- **没有任何代码隐式假定两个常量相等**。
- **当 `ADX ∈ [25, 30)` 时的全部语义组合**：
  1. 若 `-DI > +DI` 且 `RET24 <= -5%`：判为 `STRONG_DOWN`。允许满足条件 2~7 时新开组买第 1 层；开组后只要仍处于 `STRONG_DOWN`，第 2~9 层暂停加仓（§4.3 #1）。
  2. 若 `-DI > +DI` 且 `RET24 > -5%`：判为 `SLOW_DOWN`。允许新开组买第 1 层；由于 `SLOW_DOWN` 24 小时冷却时间 `MAX(bought_utc)` 仅统计 `layer_no >= 2`（§4.3 #2），第 2 层仍可正常触发买入一次，第 3 层起受 24h 冷却限制。
  3. 若 `-DI <= +DI`（即使 `RET24 >= +3%`）：因不满足 `ADX < 25`（§4.3 #3 `REBOUND` 要求 `ADX(14) < 25`），落入默认分支 #4 `RANGING`（允许新开组，正常加仓，止盈目标维持 `1.5%` 而非 `REBOUND` 的 `2.0%`）。
- 草稿在 V5.8-3（第 162 行）、§4.1（第 281 行）、§7.1（第 484 行）及 §9 第 23 条（第 560 行）已完整、准确地披露了上述双阈值语义，§4.3 瀑布流四分支保持互斥且完备。

---

### 6. 过渡策略（V5.8-4 模拟盘过渡默认说明与 §9 第 24 条）

- **`_check_and_execute_transition_close` 触发条件核查**：
  - [monitor.py:204-209](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L204-L209) 判断条件为 `if total_cnt <= 6 or max_l <= 6:`。
  - 当前实盘数据库中的 OPEN 组（Group 3 `BTCUSDT`、Group 4 `ETHUSDT`）均为 v5.7 组，`layers` 表中各有 9 行（`total_cnt = 9, max_l = 9`），因此在不改代码的情况下重启 `monitor.py` **确实不会触发** `_check_and_execute_transition_close`。
- **隐患**：如 `[P1-2]` 所述，若切换前 Group 3 或 Group 4 已经触发了 L2+ 加仓，其持久化的 `planned_notional_usdt` 会带有 v5.7 的 `1.2x~1.5x` 膨胀倍数，切换后 `mult = 1.0` 会将该膨胀值一路传递至 L9，且现存组未经过 `Position <= 0.35` 过滤。此风险必须在 V5.8-4 与 §9 第 24 条中向 `moka` 披露。

---

### 7. 证据诚实度与 `v58_exp_e6b_bt.log` 逐项核对

将草稿顶部研究披露（[spec_v58_draft.md:159](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L159)）与 §8.2（[spec_v58_draft.md:513](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L513)）的全部数字，同 [results/v58_exp_e6b_bt.log](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_e6b_bt.log) 及 [results/v58_exp_summary.md](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_summary.md) 逐项比对：

| 指标项 | `spec_v58_draft.md` 记载值 | `v58_exp_e6b_bt.log` 实测值（行号） | 核对结果 |
| :--- | :--- | :--- | :--- |
| Events CSV 版本 / SHA256 | `20261108` / `1b039cfa...73eecc` | `20261108` / `1b039cfa...73eecc` ([L3](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_e6b_bt.log#L3)) | 完全一致 |
| 训练集总盈亏 / PF / 最大回撤 | `+172.49 USDT` / `1.35` / `2.96%` | `172.49 USDT` / `1.35` / `2.96%` ([L24-L26](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_e6b_bt.log#L24-L26)) | 完全一致 |
| 训练集月胜率 / 总交易（卖出） | `86.96% (40/46)` / `1,211`（卖 `604`） | `86.96% (40/46)` / `1211 (Sell: 604)` ([L27-L28](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_e6b_bt.log#L27-L28)) | 完全一致 |
| 测试集总盈亏 / PF / 最大回撤 | `+52.34 USDT` / `1.34` / `2.14%` | `52.34 USDT` / `1.34` / `2.14%` ([L47-L49](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_e6b_bt.log#L47-L49)) | 完全一致 |
| 测试集月胜率 / 总交易（卖出） | `76.19% (16/21)` / `497`（卖 `246`） | `76.19% (16/21)` / `497 (Sell: 246)` ([L50-L51](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_e6b_bt.log#L50-L51)) | 完全一致 |
| ST1（闪崩 V 反） | `+3,577.96 USDT` | `3577.96` ([L57](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_e6b_bt.log#L57)) | 完全一致 |
| ST2 双币 / 单跑 BTC / 单跑 ETH | `-1,504.42 (15.04%, h509)` / `-1,310.02 (13.10%)` / `-834.66 (8.35%)` | `-1504.42, DD=15.04%, h509` / `-1310.02, DD=13.1%` / `-834.66, DD=8.35%` ([L60-L62](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_e6b_bt.log#L60-L62)) | 完全一致 |
| ST3（横盘 30 天后 -20%） | `-198.98 USDT`，回撤 `10.00%` | `-198.98, MaxDD: 10.0%` ([L65](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_e6b_bt.log#L65)) | 完全一致 |
| ST4（2022 熊市主跌段） | `-96.52 USDT`，回撤 `1.54%`，PF `0.39`，月胜率 `0.00% (0/1)`，交易 `84` | `-96.52 USDT`，`1.54%`，`0.39`，`0.00% (0/1)`，`84` ([L85-L89](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_e6b_bt.log#L85-L89)) | 完全一致 |
| §8.3 验收 1–5 项判定 | 全部 `PASS` | 1~5 全部 `PASS` ([L95-L99](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_e6b_bt.log#L95-L99)) | 完全一致 |

- **局限披露与不豁免立场核查**：草稿第 159 行完整披露了微利（22 个月仅 `+0.5%`）、证据污染（3 轮 15 变体看测试集挑选）、欠配效应（单组全买仅占预算 28%，全额等额版 `E2c` 测试集 `-963.77` 证伪“平铺形状本身创造优势”）、PF/月胜率失真（`E5a` PF `2.79`、月胜率 `100%` 但总盈亏 `-16.18`）以及叠加验证过程，且在第 3、159、525、554 行一致声明**不豁免 §8.3(1)**，没有任何夸大或宣称“证明策略长期盈利”的表述。

---

### 8. §8.3(6) 实现说明与 §8.3(7) 复现容差可验证性

- **§8.3(6) 缺口**：见 `[P1-1]`（漏列 `monitor.py:623-648` 及 `engine.py`/`backtest.py` 指标字典扩展）、`[P1-3]`（漏列更新 `tests/test_spec_v57.py:108-159`）与 `[P2-4]`（残留 v5.7 注释）。
- **§8.3(7) 复现容差**：[spec_v58_draft.md:531](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md#L531) 规定正式实现运行 `backtest.py` 在固定离线数据集与 `events.csv`（`version=20261108`）上，训练/测试总盈亏须与 E6b 基准（`+172.49` / `+52.34`）在未四舍五入总盈亏上相差 `<= 0.01 USDT`，展示两位小数完全一致，且 ST1–ST4 全部对齐。标准清晰、可执行、可精确验证。

---

### 9. §9 待裁决项（第 21–25 条）完备性

- 第 21、22、23、24、25 条均明确标为“待裁决”，第 1–20 条均为“已裁决”（无漏标或误标）。
- 需补充的裁决信息：第 24 条需补充 `[P1-2]` 的现存组 L2+ 混合状态说明；第 25 条需修正 `[P1-4]` 的 30 天窗口月胜率定义及净值基准。

---

### 10. 前向验证提议（V5.8-5 与 §9 第 25 条）与 §8.3 / §8.4 的衔接

- 存在 `[P1-4]` 所述的三项问题（30 天窗口仅有 1~2 个自然月导致 `月胜率 >= 60%` 统计失真、未定义继承持仓时的 30 天净值/回撤起算基准、与 §8.4 “不定义模拟盘验收标准”原文未做交叉衔接）。

---

### 11. 其他逻辑漏洞或实现歧义

- 除上述 `[P1-1] ~ [P1-4]` 及 `[P2-1] ~ [P2-5]` 外，未发现其他算术、状态机或风控门禁漏洞。

---

## 四、进入冻结评审前的修改建议清单（供起草人快速闭环）

1. **修复 `[P1-1]` & `[P2-2]`（§8.3(6)、§6.1）**：在 §8.3(6) 明确要求在 `engine.calculate_indicators` 与 `backtest.precompute_series_indicators` 中输出 `position` / `pos_ok`，并在 `monitor.py` 的**两处**开组条件判断（[monitor.py:623-648](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L623-L648) `regime_log.allow_new_group` / `REGIME_BLOCK` 与 [monitor.py:1084-1096](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1084-L1096) `_consider_open_new_group`）及 [backtest.py:799-805](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L799-L805) 同步接入 `pos_ok`；同时让 `_consider_open_new_group` 复用同轮已拉取的 `symbol_klines[sym]`。
2. **修复 `[P1-2]`（V5.8-4、§9 第 24 条）**：注明现存 v5.7 组（Group 3/4）未经过 `Position <= 0.35` 过滤，且“不平仓直接按平铺额递推”仅在切换时刻该组仍停留在 L1 时成立；若切换前已触发 L2+，须重置未买层基准为首层种子额或执行 `TRANSITION_CLOSE`。
3. **修复 `[P1-3]` & `[P2-4]`（§8.3(6)）**：在 §8.3(6) 补入同步更新 `tests/test_spec_v57.py:108-159` 中硬编码旧倍数断言的要求，并提示清理代码 docstring/注释中的 v5.7 残留数值。
4. **修复 `[P1-4]`（V5.8-5、§8.4、§9 第 25 条）**：将 30 天前向验证标准中不适用于 30 天短窗口的“自然月胜率 >= 60%”移除或替换为周胜率/笔数门槛，明确以 v5.8 启动时刻 $t_0$ 的净值为 30 天盈亏与回撤基准，并在 §8.4 增加指向 V5.8-5 / §9 第 25 条的衔接说明。
5. **修复 `[P2-1]` & `[P2-3]`（§6.1、§5.3）**：在 §6.1 的 `close_reason` / `trades.reason` 枚举中补入 `TRANSITION_CLOSE`；在 §5.3 第 364 行“充要条件”句前补入“未触发 7 天减半且未单步跳空跌破 `0.80C` 的逐层触发路径下”限定语。
