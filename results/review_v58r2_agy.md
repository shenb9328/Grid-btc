# BTC/ETH 现货马丁策略 SPEC v5.8 草稿（第 2 轮）定点复审报告

- **评审对象**：[spec_v58_draft_r2.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md)（共 569 行）
- **对比草稿**：[spec_v58_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md)（第 1 轮草稿，共 568 行）
- **第 1 轮盲审基准**：[results/review_v58_agy.md](file:///home/shenb/myQ/projects/spot-martingale/results/review_v58_agy.md)（第 1 轮单路 FAIL：`0 P0 / 4 P1 / 5 P2`）
- **已冻结基线**：[spec.md](file:///home/shenb/myQ/projects/spot-martingale/spec.md)（已冻结 v5.7，共 552 行）
- **前置校验（只读核验）**：
  - `md5sum /home/shenb/myQ/projects/spot-martingale/spec.md` = `6969b432170928138cb787d73531a71b`（一致）
  - `md5sum /home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md` = `13217f7484acfb4161832e71dead33c6`（一致）
  - `md5sum /home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md` = `a2407c6ab7c95fa5e21a031d7e3a5dc5`（一致）
  - 评审全程严格只读，未修改任何文件、数据库或运行中进程。

---

## 一、总体复审结论

- **判定结论：`PASS`（可进入冻结评审）**
- **残余问题分级统计：`0 P0` / `0 P1` / `0 P2`**
- **核心结论**：
  1. 第 1 轮盲审报告（[review_v58_agy.md](file:///home/shenb/myQ/projects/spot-martingale/results/review_v58_agy.md)）提出的 **4 个 P1（P1-1 ~ P1-4）与 5 个 P2（P2-1 ~ P2-5）已全部修好并闭环**。
  2. 经 `diff -U 0` 逐行核对，[spec_v58_draft_r2.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md) 相对 [spec_v58_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft.md) 的改动**严格限定在声称的 12 处**，未改动任何实验回测/压测数字，未引入任何跨章节新矛盾。
  3. 线上模拟盘数据库只读快照复核确认：截至本次复审时刻（`2026-11-12T08:08Z`，北京时间 `16:08`），现存 `OPEN` 组 Group 3（`BTCUSDT`）与 Group 4（`ETHUSDT`）仍均仅买 L1（`planned_notional_usdt` 分别为 `93.71` 与 `62.47`），L2~L9 全为 `PENDING`，第 24 条的实盘快照仍然成立（冻结重启前按草稿要求重查一次即可）。

---

## 二、7 项复审范围逐项核查结论与依据

### 1. R2-1（对应 P1-1 / P2-2）：开组条件双路径、指标字典扩展、同轮 K 线复用与 `allow_new_group` 口径
- **结论：已修好**
- **逐项核查依据**：
  1. **`monitor.py` 两处开组条件与 `backtest.py` 开组条件行号核对**：
     - [spec_v58_draft_r2.md:531](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L531) 明确点名：
       - `monitor.py` 第一处：[monitor.py:623-648](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L623-L648)（步骤 `(b2)` 计算 `cond1..cond4`、写入 `regime_log.allow_new_group` 及触发 `REGIME_BLOCK` 风险事件）；
       - `monitor.py` 第二处：[monitor.py:1084-1096](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1084-L1096)（`_consider_open_new_group` 内计算 `cond1..cond4` 决定是否实际开组）；
       - `backtest.py` 开组条件：[backtest.py:799-805](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L799-L805)（`_consider_open_group` 内计算 `cond1..cond3`）。
     - 逐行对照实际代码，上述三处行号与代码逻辑 100% 精确吻合，且草稿写明“均须同步接入 `pos_ok`……只改一处即判实现不合格”。
  2. **`engine.calculate_indicators` 与 `backtest.precompute_series_indicators` 函数存在性与签名兼容性**：
     - [engine.py:204](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L204) 定义了 `def calculate_indicators(klines_1h: List[List[Any]]) -> Dict[str, Any]:`（第 222–236 行为 `< 500` 根早退字典，第 334–346 行为正常返回字典）；
     - [backtest.py:937](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L937) 定义了 `def precompute_series_indicators(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:`（第 1015–1029 行为 `< 500` 根字典，第 1041–1053 行为正常字典）。
     - 两函数均返回 `Dict[str, Any]`（或其列表），在字典中统一新增 `'position'` 与 `'pos_ok'`（当 `n < 500` 或 `hh_480 <= ll_480` 时 `pos_ok = False`）完全兼容现有调用方签名，不会破坏任何既有键解包。
  3. **复用同轮 `symbol_klines[sym]`、删除 L1069 二次拉取的代码事实核对**：
     - 核查 [monitor.py:348-349](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L348-L349)：在 `_execute_poll_cycle` 的步骤 4 中，已执行 `klines, k_err = self.client.get_recent_closed_klines(sym, config.MIN_KLINES_BARS)` 并存入同轮局部变量 `symbol_klines[sym] = klines`，且在步骤 `(b2)`（[monitor.py:606-607](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L606-L607)）已用 `symbol_klines.get(sym)` 计算指标。
     - 核查 [monitor.py:669](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L669) 与 [monitor.py:1063-1076](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1063-L1076)：在步骤 `(d)` 调用 `self._consider_open_new_group(conn, sym, val_px, now_utc)` 时，第 1069 行确实再次调用了 `klines, k_err = self.client.get_recent_closed_klines(symbol, config.MIN_KLINES_BARS)` 发起二次网络请求。
     - 因此草稿要求“复用同轮已拉取的 `symbol_klines[sym]`，不得同轮二次拉取 K 线（`monitor.py:348` 已拉取，`L1069` 的二次拉取须删除，消除跨整点漂移）”与代码事实完全一致且可直接执行。
  4. **§6.1 `regime_log` 与 §6.3 `/api/spot/regime` 的 `allow_new_group` 口径一致性**：
     - [spec_v58_draft_r2.md:392](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L392)（§6.1 `regime_log`）补入：“`allow_new_group` 为 §4.1 全部开组条件（含条件 7 位置过滤）的合取结果（v5.8）：任一条件不满足时记 0 并记 `REGIME_BLOCK`；monitor 两处开组条件须同步接入位置过滤（见 §8.3(6)）。”
     - [spec_v58_draft_r2.md:434](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L434)（§6.3 `/api/spot/regime`）补入：“`allow_new_group` 含 §4.1 条件 7（位置过滤），与 `regime_log` 同口径（v5.8）。”
     - 两处与 §4.1（[spec_v58_draft_r2.md:281-289](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L281-L289)）完全一致。

---

### 2. R2-2（对应 P1-2 / P2-5）：过渡方案混合状态披露、`prev_planned × mult` 递推锁定机制、边界选项与实盘库状态核查
- **结论：已修好**
- **逐项核查依据**：
  1. **V5.8-4 与 §9 第 24 条披露完整性**：
     - [spec_v58_draft_r2.md:163](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L163)（V5.8-4）与 [spec_v58_draft_r2.md:562](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L562)（§9 第 24 条）均明确披露：
       - 现存组开组时未经过 `Position <= 0.35` 过滤；
       - “不平仓直接按平铺额递推”仅在切换时刻该组仍停留在 L1 时成立；
       - 若切换前已触发 L2+（`planned_notional_usdt` 已带 v5.7 的 `1.2x~1.5x` 膨胀值），切换后 `mult = 1.0` 会把切换前最后一层的膨胀名义额锁定并传到后续各层，形成既非 v5.7 也非 v5.8 的混合仓位。
  2. **`monitor.py:952-964` 递推代码核对**：
     - 核查 [monitor.py:952-964](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L952-L964)：
       ```python
       cur.execute("SELECT planned_notional_usdt FROM layers WHERE group_id = ? AND layer_no = ?", (gid, lno - 1))
       prev_row = cur.fetchone()
       ...
       prev_planned = prev_row["planned_notional_usdt"]
       planned_notional = round(prev_planned * mult, 2)
       ```
     - 代码确系从 `layer_no = lno - 1` 读取已持久化的 `planned_notional_usdt` 乘以当轮 `mult`。当 `mult = 1.0` 时，`round(prev_planned * 1.0, 2) == prev_planned`，确实会将切换前最高已买入层的 `planned_notional_usdt` 原样锁定并传给后续所有层。草稿对锁定机制的描述完全准确。
  3. **边界选项 `(a)(b)(c)` 的完备性、可裁决性与可实现性**：
     - [spec_v58_draft_r2.md:562](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L562) 给出三个互斥且覆盖全部处置路径的选项：
       - `(a)` 不平仓、切换时未买层递归基数重置为首层种子额（并已明确注明“**须补实现条款**”，因为当前代码读的是 `lno - 1` 的 `planned_notional_usdt`，若选 `(a)` 需改代码或迁移已买层的递归基数说明）；
       - `(b)` 不平仓、但切换前已触发 L2+ 的组执行 `TRANSITION_CLOSE`、其余组（仅买 L1 的组，天然按首层种子额平铺递推）继续跑；
       - `(c)` 全部平仓重启（v5.7 选项 `(d)` 式，并注明“**须补写过渡条款含平仓原因枚举与幂等**”）。
     - 选项划分清晰、边界完备，可直接供 `moka` 在冻结评审时拍板。
  4. **线上实盘数据库只读核查（`mode=ro`）**：
     - 复审时刻（`2026-11-12T08:08Z`，即北京时间 `16:08`）以 `file:/home/shenb/myQ/projects/spot-martingale/data/spot_martingale.sqlite3?mode=ro` 只读查询 `grid_groups` 与 `layers` 表：
       - Group 1（BTC）、Group 2（ETH）：`status='CLOSED'`，`close_reason='TRANSITION_CLOSE'`（`2026-11-12T05:37:39Z/40Z`）；
       - **Group 3（`BTCUSDT`）**：`status='OPEN'`，`center_price=73550.01`，`opened_utc='2026-11-12T05:41:48.614833+00:00'`；`layer_no=1` 为 `'OPEN'`（`planned_notional_usdt=93.71`，`notional_usdt=73.55`，`qty=0.001`），`layer_no=2..9` 全部为 `'PENDING'`（L2 `trigger_price=72593.41`，`planned_notional_usdt=None`）；
       - **Group 4（`ETHUSDT`）**：`status='OPEN'`，`center_price=2045.83`，`opened_utc='2026-11-12T05:41:48.614833+00:00'`；`layer_no=1` 为 `'OPEN'`（`planned_notional_usdt=62.47`，`notional_usdt=61.3749`，`qty=0.03`），`layer_no=2..9` 全部为 `'PENDING'`（L2 `trigger_price=2001.42`，`planned_notional_usdt=None`）。
     - **核查结论**：第 24 条所载“2026-11-12 16:00 只读核查线上库：组 3（BTC）、组 4（ETH）均仅买 L1（93.71 / 62.47），L2~L9 均为 PENDING，混合状态尚未发生；冻结重启前须重查”在复审时刻**仍然完全成立**。

---

### 3. R2-3（对应 P1-3 / P2-4）：单测同步要求、递归算术数字裁决与残留注释清理清单
- **结论：已修好（且起草人对单测递归算术数字的更正完全正确）**
- **逐项核查依据**：
  1. **`tests/test_spec_v57.py:108-159` 逐行核对与数字裁决**：
     - 核查 [tests/test_spec_v57.py:108-159](file:///home/shenb/myQ/projects/spot-martingale/tests/test_spec_v57.py#L108-L159)（`test_dynamic_multiplier_ladder_and_recursion`）：
       - 第 111–130 行：硬编码断言 `engine.get_dynamic_multiplier(...)` 返回 `(1.2, True)`（L111-112）、`(1.3, True)`（L115-116）、`(1.4, True)`（L119-120）、`(1.5, True)`（L124-126）、`(1.5, False)`（L128-130）；
       - 第 138–144 行：`seed = config.BTC_LAYER_NOTIONALS[0]`（`93.71`），`l2_plan = seed * 1.2`，`self.assertAlmostEqual(l2_plan, 112.452, places=3)`；
       - 第 155–156 行：`l3_plan = l2_plan * 1.3`，`self.assertAlmostEqual(l3_plan, 146.1876, places=4)`。
     - **【重要裁决】**：第 1 轮盲审报告（`review_v58_agy.md` 第 77 行）所写的“校验了 BTC `909.61` 与 ETH `606.38` 的递归序列”确系第 1 轮报告的笔误，[tests/test_spec_v57.py](file:///home/shenb/myQ/projects/spot-martingale/tests/test_spec_v57.py) 全文根本不存在 `909.61` 与 `606.38` 这两个数；该文件第 141–156 行实际断言的递归算术数字正是 **`112.452`**（L144）与 **`146.1876`**（L156）。因此，起草人在 [spec_v58_draft_r2.md:531](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L531) 将其修正为“硬编码旧倍数断言（`(1.2, True)…(1.5, True)/(1.5, False)` 与递归算术 `112.452/146.1876`）”**完全正确**！
  2. **残留注释与 docstring 清理清单逐行核对**：
     - [config.py:3](file:///home/shenb/myQ/projects/spot-martingale/config.py#L3)：`Frozen under SPEC v5.7 (2026-11-12).`（精确匹配）
     - [config.py:57](file:///home/shenb/myQ/projects/spot-martingale/config.py#L57)：`ADX_THRESHOLD = 25.0  # < 25`（精确匹配）
     - [engine.py:2](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L2)：`Engine module for Spot Martingale (SPEC v5.7).`（精确匹配）
     - [engine.py:502-505](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L502-L505)：`get_dynamic_multiplier` docstring 中 `- DD < 2%: 1.2` / `- 2% <= DD < 5%: 1.3` / `- 5% <= DD < 10%: 1.4` / `- 10% <= DD <= 20%: 1.5 (permitted only when regime != STRONG_DOWN)`（精确匹配）
     - [monitor.py:4](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L4)：`Frozen under SPEC v5.7.`（精确匹配）
     - [monitor.py:137](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L137)：`logger.info("Performing startup checks under SPEC v5.7...")`（精确匹配）
     - [monitor.py:1085](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1085)：`# 1. ADX < 25`（精确匹配）
     - [backtest.py:2](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L2)：`Offline Backtest and Stress Testing Module (SPEC v5.7).`（精确匹配）
     - 全仓扫描无其他将 v5.8 被改参数写死为 v5.7 值的注释（其余出现 `v5.7` 的注释如 `engine.py:283`、`monitor.py:188/895`、`backtest.py:689` 均为标记某既有特性在 v5.7 引入的历史出处，在 v5.8 中依然保持原规则不变，无需改动）。清单无错列、无漏列。

---

### 4. R2-4（对应 P1-4 / P2-5）：30 天前瞻验证窗口标准、$t_0$ 基准、去月胜率与 §8.4 衔接
- **结论：已修好**
- **逐项核查依据**：
  1. **$t_0$ 基准与继承持仓计入方式**：
     - [spec_v58_draft_r2.md:164](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L164)（V5.8-5）与 [spec_v58_draft_r2.md:563](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L563)（§9 第 25 条）明确定义：`(a) 窗口 ΔEquity > 0（基准 = v5.8 启动时刻 t0 的账户总权益，继承持仓在窗口内的平仓与浮动均计入该基准的增量）`。数学定义唯一、无歧义（即 $\Delta\text{Equity} = \text{Equity}(t_{\text{end}}) - \text{Equity}(t_0) > 0$，由于 $\text{Equity}(t_0)$ 已包含切换时刻继承持仓按 $t_0$ 估值价计算的市值与未实现盈亏，窗口内的后续浮动与平仓变动自然且无重不漏地反映在 $\text{Equity}(t) - \text{Equity}(t_0)$ 中）。
  2. **PF 口径与窗口局部峰值回撤定义**：
     - `(b) 窗口内已平仓交易 PF ≥ 1.20（§8.3(3) 口径）`：显式引用 §8.3(3)（按窗口内每笔 `SELL` 成交的含双边成本已实现盈亏计算），口径完全一致。
     - `(c) 窗口内峰值回撤 ≤ 16%（t0 起算的局部峰值）`：明确以 $t_0$ 起算的窗口内局部最高净值 $\max_{\tau \in [t_0, t]} \text{Equity}(\tau)$ 为峰值计算回撤，不受 $t_0$ 之前的历史峰值干扰。
  3. **最低成交笔数下限与“不确定”处置**：
     - `(d) 窗口内 ≥ 10 笔 SELL 成交，不足则结论为“不确定”`，且在 §9 第 25 条（[spec_v58_draft_r2.md:563](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L563)）补齐了“未通过/不确定时的处置（回 §2、延长窗口或继续观察），由 moka 在冻结时定”。
  4. **全文搜索确认“盈利月 ≥ 60%”不再用作前瞻窗口标准、且 §8.3(4) 回测标准保留**：
     - 经全文 `grep -n "盈利月\|月胜率"` 核实：
       - V5.8-5（第 164 行）与 §9 第 25 条（第 563 行）均已改为“**不适用自然月胜率（30 天仅跨 1~2 个自然月，月胜率只能取 0%/50%/100%，统计失真）**”；
       - §8.3(4)（[spec_v58_draft_r2.md:529](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L529)）的回测验收标准“4. 盈利月占比 ≥ 60%”完整保留，未被误删。
  5. **§8.4 例外句衔接核查**：
     - [spec_v58_draft_r2.md:535](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L535) 在原句后补入：“例外（v5.8）：30 天前瞻验证的窗口与标准由本文件 V5.8-5 与 §9 第 25 条定义，不另起 SPEC；前瞻验证通过后，长时间跑仍须 moka 批准。”消除了与原句“不定义模拟盘验收标准……另起 SPEC”的文本冲突，且未引入新矛盾。

---

### 5. R2-5（对应 P2-1 / P2-3）：§6.1 两处 `TRANSITION_CLOSE` 枚举与 §5.3 充要条件路径限定
- **结论：已修好**
- **逐项核查依据**：
  1. **§6.1 `grid_groups.close_reason` 与 `trades.reason` 枚举核对**：
     - [spec_v58_draft_r2.md:387](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L387)：`close_reason[TAKE_PROFIT/ALL_SOLD/TIME_STOP_7D/TIME_STOP_14D/DRAWDOWN_HALT/TRANSITION_CLOSE]`；
     - [spec_v58_draft_r2.md:389](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L389)：`reason 枚举：GROUP_OPEN / LAYER_BUY / LAYER_TP / GROUP_TP / ALL_SOLD / TIME_STOP_7D / TIME_STOP_14D / DRAWDOWN_HALT / TRANSITION_CLOSE`。
     - 与 §9 第 18(d) 条（[spec_v58_draft_r2.md:556](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L556)）、代码 [engine.py:109](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L109)、[monitor.py:223-227](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L223-L227) 以及实盘数据库 Group 1/2 的实际持久化值 `'TRANSITION_CLOSE'` 完全一致。
  2. **§5.3 充要条件句的路径限定核对**：
     - [spec_v58_draft_r2.md:365](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L365) 已修改为：“故在未触发 7 天减半且未单步跳空跌破 0.80C 的逐层触发路径下，触发价条件即买满 9 层的充要条件（计划名义算术下）。”
     - 与同段前文列举的“以下三种情形任一发生，该组即不可能再买入、不可能达到满层：① 7 天减半执行……② 上限用满……③ 价格跌破 C×0.80”及“边界效应”严密自洽。

---

### 6. 修订范围核查（`spec_v58_draft.md` vs `spec_v58_draft_r2.md` 全文 Diff）
- **结论：完全合规（仅改声称的 12 处，零夹带改动，零实验数字改动）**
- **逐处 Diff 清单核实**：

| 序号 | 声称改动位置 | `spec_v58_draft.md` 行号 | `spec_v58_draft_r2.md` 行号 | 核查结果 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `V5.8-4` 过渡方案补披露 | L163 | L163 | 一致 |
| 2 | `V5.8-5` 前瞻验证改标准 + 新增第 2 轮变更日志行 | L164 | L164–165 | 一致（新增 L165 使后续行号整体 +1） |
| 3 | §5.3 充要条件句补路径限定 | L364 | L365 | 一致 |
| 4 | §6.1 `grid_groups.close_reason` 补 `TRANSITION_CLOSE` | L386 | L387 | 一致 |
| 5 | §6.1 `trades.reason` 补 `TRANSITION_CLOSE` | L388 | L389 | 一致 |
| 6 | §6.1 `regime_log` 注明 `allow_new_group` 含条件 7 | L391 | L392 | 一致 |
| 7 | §6.3 `/api/spot/regime` 注明 `allow_new_group` 含条件 7 | L433 | L434 | 一致 |
| 8 | §8.3(6) v5.8 实现说明扩展 | L530 | L531 | 一致 |
| 9 | §8.4 补 v5.8 前瞻验证例外句 | L534 | L535 | 一致 |
| 10 | §9 第 8 条更新为“第 2 轮”并注明第 1 轮归档 | L545 | L546 | 一致 |
| 11 | §9 第 24 条补披露、实盘快照与 `(a)(b)(c)` 选项 | L561 | L562 | 一致 |
| 12 | §9 第 25 条更新 30 天前瞻验证标准与处置选项 | L562 | L563 | 一致 |

- **变更日志行准确性**：[spec_v58_draft_r2.md:165](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L165) 对 `R2-1 ~ R2-5` 的概括与上述实际修改一一对应，无夸大或遗漏。
- **实验数字未改动确认**：L159（变更日志证据）、L514–524（§8.2 压力测试）、L526–530（§8.3 验收标准 1–5）在 `diff` 中均为 0 改动，与 [results/v58_exp_e6b_bt.log](file:///home/shenb/myQ/projects/spot-martingale/results/v58_exp_e6b_bt.log) 完全一致。

---

### 7. 新矛盾扫描与第 1 轮 `0 P0` 结论复核
- **结论：无新矛盾，第 1 轮 `0 P0` 结论继续成立**
- **核查详情**：
  1. **常量命名一致性**：§7.1 冻结值表（[spec_v58_draft_r2.md:469](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L469)）与 §8.3(6) 实现说明（[spec_v58_draft_r2.md:531](file:///home/shenb/myQ/projects/spot-martingale/spec_v58_draft_r2.md#L531)）均统一使用 `POSITION_THRESHOLD = 0.35`，前后完全一致。
  2. **指标字典新增字段与既有测试/代码的兼容性**：`engine.calculate_indicators` 与 `backtest.precompute_series_indicators` 新增 `'position'` 与 `'pos_ok'` 键，与 §3.4 Position 定义、§4.1 条件 7（`Position <= 0.35` 且 `HH_480 <= LL_480` 时 fail-closed）、§6.1 `regime_log.allow_new_group`、§6.3 `/api/spot/regime` 完全闭环。
  3. **过渡方案与前瞻验证的耦合自洽性**：V5.8-4 / §9 第 24 条（默认不平仓，并提供 `(a)(b)(c)` 三个边界选项）与 V5.8-5 / §9 第 25 条（以 v5.8 启动时刻 $t_0$ 的账户总权益为基准，继承持仓在窗口内的平仓与浮动均计入该基准的增量）在“不平仓继承持仓”和“平仓重启”两种裁决路径下均自洽成立。
  4. **第 1 轮 `0 P0` 结论复核**：九层平铺名义算术（BTC `749.68 / 843.39`、ETH `499.76 / 562.23`）、§5.3 触发价充要条件 $d < 2.5\%$、§8.3(5) 平铺路径 `-50%` 极限亏损（BTC 约 `409 USDT / 4.1%`、ETH 约 `270 USDT / 2.7%`）及 `v58_exp_e6b_bt.log` 全部实验证据数字均未变动，第 1 轮 `0 P0` 结论依然成立。
  5. **冻结归档时的例行提醒（非问题，0 P2）**：为严格控制第 2 轮 diff 范围在 12 处，草稿顶部的标题行（L1、L3“第 1 轮”）及 §6.2 目录树（L409）未做改动；待 `moka` 裁决冻结、将本文件落盘为正式 `spec.md` 时，按惯例同步更新顶部状态头与 §6.2 归档文件列表即可，且在冻结重启 `monitor` 前按 §9 第 24 条重查一次线上库 Group 3/4 是否仍停留在 L1。
