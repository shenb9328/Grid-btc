# BTC/ETH 现货马丁策略 SPEC v5.6 草稿盲审报告

- **评审对象**：[`spec_v56_draft.md`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md)（452 行，md5 `200de8e723dc91beb5f4dc2de9c11f5a`）
- **对照基准**：[`spec.md`](file:///home/shenb/myQ/projects/spot-martingale/spec.md)（已冻结 v5.5，438 行，评审前后实测 md5 均为 `faa9b30a3f7e2fd651a7acc97cc1e023`，未发生任何改动）
- **核对材料**：
  - [`results/merged_review_v55.md`](file:///home/shenb/myQ/projects/spot-martingale/results/merged_review_v55.md)（35 行）
  - [`spot-martingale-sens3/combo2/bt.log`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/bt.log)（100 行，sha256 `7ff6555153d2107c584534c838097f83b07a8416ca1e3f3625e6e431f51416f8`）
  - [`spot-martingale-sens3/backtest_holdmax.py`](file:///home/shenb/myQ/projects/spot-martingale-sens3/backtest_holdmax.py)（1,471 行）
  - [`spot-martingale-sens3/RESULT.md`](file:///home/shenb/myQ/projects/spot-martingale-sens3/RESULT.md)（359 行）
  - [`spot-martingale-sens2/SWEEP.md`](file:///home/shenb/myQ/projects/spot-martingale-sens2/SWEEP.md)（338 行）
- **盲审结论**：**FAIL（不宜直接冻结，修完 3 个 P1 小补丁后可进入冻结评审）**
  - **分级汇总**：**P0: 0 项｜P1: 3 项｜P2: 3 项**
  - **核心原因**：
    1. **[P1-1]** 草案改写第 3 行状态头时，意外删掉了 [`spec.md`](file:///home/shenb/myQ/projects/spot-martingale/spec.md#L3) 中写入的 [`merged_review_v55.md`](file:///home/shenb/myQ/projects/spot-martingale/results/merged_review_v55.md#L18-L23) §二 `P-1` 权威解释，且未合入第 89 行 `V5.5-2（O-2）`，导致草案退回到 v5.5 盲审被判 FAIL 的字面歧义状态；
    2. **[P1-2]** 第 8.2 节（L97、L412、L418）已将确定性持仓压力测试间距改为 BTC `2.0%`、ETH `2.5%` 并重算单币 `-50%` 亏损为 `BTC 1,394.39 / ETH 909.99`，但第 8.3(5) 节（L428）遗漏未改，仍保留旧间距下的 `BTC 1,422.5 / ETH 929.6`，造成规范内部前后数字矛盾；
    3. **[P1-3]** [`backtest_holdmax.py`](file:///home/shenb/myQ/projects/spot-martingale-sens3/backtest_holdmax.py#L1171-L1173)（及 `:1271-1272`）在压力测试 1–3 中硬编码了旧间距 `0.015 / 0.020`，导致 [`combo2/bt.log`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/bt.log#L57-L63) 的压力测试 1–3 实际仍按 v5.5 旧间距运行（`Halt hour: 429`、`BTC alone -1426.11`、`ETH alone -932.0`），与草案 V5.6-2 / §8.2 的新间距口径不一致；此外，从验收门禁本身看，草案已诚实披露测试集总盈亏为 `-173.45 USDT`（§8.3 第 1 项 FAIL），不满足 §0.3 / §8.3“全部满足才算通过”的硬门禁，若作为例外候选冻结须由 moka 显式豁免或批准。

---

## 一、分级问题清单（P0 / P1 / P2）

### P0（致命架构或账务崩溃）：无（0 项）

---

### P1（阻塞直接冻结的文本矛盾、遗漏或回测口径偏差）：3 项

#### [P1-1] 第 3 行状态头改写时误删 v5.5 冻结版 `P-1` 权威解释，导致 `V5.5-2（O-2）` 退回未闭合歧义状态
- **位置与依据**：
  - 已冻结基准 [`spec.md:3`](file:///home/shenb/myQ/projects/spot-martingale/spec.md#L3)：
    > `# 状态：已冻结。v5.5（moka 2026-11-06 17:03 批准冻结；两份盲审一 PASS 一 FAIL，见 results/merged_review_v55.md；O-2 的解释以 merged_review_v55.md §二 P-1 为准——首根有效 bar 到来之前的轮次才按 V5.4-1(b) 计价格错误，首根 bar 所在轮按 V5.4-1(a) 回退分支初始化、不计价格错误）。`
  - 上一轮盲审结论 [`results/merged_review_v55.md:9-23`](file:///home/shenb/myQ/projects/spot-martingale/results/merged_review_v55.md#L9-L23)：裁决 Codex 的 P1 成立——`V5.5-2（O-2）` 原文“在首根有效 bar 初始化完成前，每轮……只按 V5.4-1(b) 计一次价格错误”未排除首根有效 bar 所在的初始化轮次，字面存在歧义。v5.5 冻结时通过把 `P-1` 写进 [`spec.md:3`](file:///home/shenb/myQ/projects/spot-martingale/spec.md#L3) 完成了规范固化。
  - 本草案 [`spec_v56_draft.md:3`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L3) 将第 3 行整体替换为 v5.6 草案说明，却未保留该句 `P-1` 权威解释，也未将其合入 [`spec_v56_draft.md:89`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L89)（`V5.5-2（O-2）`），导致 `spec_v56_draft.md` 全文丢失了 `P-1` 修正。
- **修复建议**：在 [`spec_v56_draft.md:89`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L89) 直接按 [`merged_review_v55.md:23`](file:///home/shenb/myQ/projects/spot-martingale/results/merged_review_v55.md#L23) 补齐：“在首根有效 bar 到来之前的每轮调用 `evaluate_symbol_price` 时均可显式传 `latest_kline_close=None`，该轮不进入跳变分支，只按 V5.4-1(b) 计一次价格错误；首根有效 bar 所在的轮次按 V5.4-1(a) 回退分支初始化（将其 close 写入 `last_valid_price`），不计价格错误。”

#### [P1-2] 第 8.3(5) 节单币 −50% 最大亏损数遗漏未改（仍为 `1,422.5 / 929.6`），与第 8.2 节（`1,394.39 / 909.99`）自相矛盾
- **位置与依据**：
  - [`spec_v56_draft.md:97`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L97)（`V5.6-2`）与 [`spec_v56_draft.md:412-418`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L412-L418)（§8.2）已将压力测试 1–3 的固定间距从 `BTC 1.5% / ETH 2.0%` 改为 `BTC 2.0% / ETH 2.5%`，并将持仓到 `0.5C` 不卖的单币仓位亏损更新为 `BTC 亏 1,394.39、ETH 亏 909.99`。
  - 但 [`spec_v56_draft.md:428`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L428)（§8.3 第 5 项）仍写：
    > `**(a)(b) 单币情形：不要求 halt 触发**（单币满仓跌 50% 最多亏 BTC 1,422.5 / ETH 929.6，数学上触发不了 15% halt），**不用验证恢复**；要求最大回撤 ≤ 16.00%，且无新开仓。`
  - `1,422.5` 与 `929.6` 是 v5.5 旧间距（`1.5% / 2.0%`）下的数字，与同文 §8.2 的新间距数字直接冲突。
- **修复建议**：将 [`spec_v56_draft.md:428`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L428) 中的 `BTC 1,422.5 / ETH 929.6` 更新为 `BTC 1,394.39 / ETH 909.99`（或注明含买入手续费与滑点后的净值亏损 `BTC 1,397.99 / ETH 912.39`，对应最大回撤 `13.98% / 9.12%`）。

#### [P1-3] `combo2` 回测脚本在压力测试 1–3 中仍硬编码旧间距 `0.015 / 0.020`，导致 `combo2/bt.log` 中 ST1–ST3 的实跑结果并非 V5.6-2 新间距结果
- **位置与依据**：
  - [`backtest_holdmax.py:1171-1173`](file:///home/shenb/myQ/projects/spot-martingale-sens3/backtest_holdmax.py#L1171-L1173)（`_run_synthetic_holding_test`）与 [`backtest_holdmax.py:1271-1272`](file:///home/shenb/myQ/projects/spot-martingale-sens3/backtest_holdmax.py#L1271-L1272)（`_run_synthetic_split_test`）硬编码了 `('BTCUSDT', btc_c, 0.015, ...)` 与 `('ETHUSDT', eth_c, 0.020, ...)`，未随 `config.py` 或 V5.6-2 改为 `0.020 / 0.025`。
  - 因此 [`combo2/bt.log:55-63`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/bt.log#L55-L63) 与 [`:97`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/bt.log#L97) 输出的仍是 v5.5 旧间距结果：
    - `ST1`: `total_pnl = 2937.67`
    - `ST2 Dual`: `total_pnl = -1506.19, post_halt_max_dd_pct = 15.06%, Halt hour: 429, recovery verified: True`
    - `ST2 BTC alone`: `total_pnl = -1426.11, max_drawdown_pct = 14.26%`
    - `ST2 ETH alone`: `total_pnl = -932.0, max_drawdown_pct = 9.32%`
    - `ST3`: `total_pnl = -557.54, max_drawdown_pct = 12.99%`
  - 我们在 `sens3` 临时评审副本中将上述硬编码改为 `0.020 / 0.025` 后实测，V5.6-2 口径下的真实结果为：
    - `ST1`: `total_pnl = 3080.85, end_equity = 13080.85`
    - `ST2 Dual`: `total_pnl = -1504.31, max_drawdown_pct = 15.04%, post_halt_max_dd_pct = 15.04%, end_equity = 8495.69, Halt hour: 447, halt_ratio: 0.650295, recovery: True`
    - `ST2 BTC alone`: `total_pnl = -1397.99 (仓位浮亏 1394.39 + 买入成本 3.60), max_drawdown_pct = 13.98%, end_equity = 8602.01`
    - `ST2 ETH alone`: `total_pnl = -912.39 (仓位浮亏 909.99 + 买入成本 2.40), max_drawdown_pct = 9.12%, end_equity = 9087.61`
    - `ST3`: `total_pnl = -477.36, max_drawdown_pct = 12.32%, end_equity = 9522.64`
  - 虽然新间距下 §8.3 第 5 项依然 PASS（`15.04% <= 16.00%`，`Halt hour = 447` 与草稿 §8.2 完全吻合），但草稿第 95 行直接引用 `combo2` 作为证据时未说明 `combo2/bt.log` 中的 ST1–ST3 尚未按 V5.6-2 重跑。
- **修复建议**：在 [`spec_v56_draft.md:95`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L95) 注明 `combo2/bt.log` 中 ST1–ST3 仍为旧间距 `0.015 / 0.020`（新间距 `0.020 / 0.025` 下实测 ST2 Dual `Halt hour = 447, post-halt dd = 15.04%`，同样 PASS），并在代码实现阶段同步修改 `backtest.py` 中的硬编码间距。

---

### P2（非阻塞但建议在定稿或实现时一并清理的细节）：3 项

#### [P2-1] 第 99 行（`V5.6-4`）与第 445 行（`§9 第 9 条`）对被排除方案“解释 B”的定义不一致
- **位置与依据**：
  - [`spec_v56_draft.md:99`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L99)：`解释 B（彻底取消时间止损）未测试、不在本草稿内。`（指对所有组彻底取消 7d/14d 时间止损）。
  - [`spec_v56_draft.md:445`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L445)：`解释 B——“到了最后一层就不再卖”（连止盈也不执行，只等 halt 或无限持有）——未回测、不在本草稿内`（指满层组连单层/整组止盈也不卖）。
  - 两处对本草稿所采用的**解释 A**（满层组只豁免 7d/14d 时间止损，单层/整组止盈与 halt 照常执行）定义完全一致、毫无歧义；但对向 moka 征询的备选“解释 B”表述不一致。
- **修复建议**：统一第 99 行与第 445 行对解释 B 的表述（或拆分为解释 B1“满层后连止盈也不卖”、解释 B2“全部组彻底取消时间止损”）。

#### [P2-2] 第 7.1 节冻结值表新增的“满层时间止损豁免”行未给出 `config.py` 严格等于校验所需的常量名与精确值，且 §8.3(6) 单测列表未补入 `holdmax` 必测项
- **位置与依据**：
  - [`spec_v56_draft.md:127`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L127) 与 [`:358`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L358) 要求启动时对 `config.py` 与 §7.1 冻结值表做严格等于校验（见 [`combo2/config.py:177-200`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/config.py#L177-L200)）。
  - [`spec_v56_draft.md:385`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L385) 新增行写为 `| 满层时间止损豁免 | 买入层数 6/6 时跳过 7d/14d，止盈与 halt 照常 | §5.3（V5.6） |`，是文字描述而非配置常量定义（[`combo2/config.py`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/config.py#L105-L163) 未加该键，[`backtest_holdmax.py:579`](file:///home/shenb/myQ/projects/spot-martingale-sens3/backtest_holdmax.py#L579) 则直接硬编码了 `_bought >= 6`）。
  - 同时 [`spec_v56_draft.md:429`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L429)（§8.3 第 6 项单元测试清单）尚未加入满层组豁免 7d/14d 时间止损的单测要求。
- **修复建议**：在 §7.1 明确 `config.py` / `FROZEN_VALUES` 常量名与类型（例如 `HOLDMAX_EXEMPT_TIME_STOP = True`），并在 §8.3(6) 单测清单中补入：“满层组（`bought_layers == 6`）跳过 7d/14d 时间止损且不置 `half_time_stopped`/`stop_adding`、止盈与 halt 照常；未满层组（`< 6`）照常执行 7d/14d 时间止损”。

#### [P2-3] 间距系数上调至 `2.0× / 2.5×` 后，高波动下间距 `d >= 3.0%` 时受 §4.2 停加仓线（`< 0.85C`）拦截，数学上无法买到第 6 层触发 `holdmax`
- **位置与依据**：
  - 依 [`spec_v56_draft.md:182-185`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L182-L185)（§3.2），BTC 间距钳制上限为 `3.0%`（此时 L6 触发价 `= 0.85C`），ETH 间距钳制上限为 `4.0%`（当 `d >= 3.0%` 时 L6 触发价 `<= 0.85C`；当 `d > 3.75%` 时 L5 触发价 `< 0.85C`）。
  - 依 [`spec_v56_draft.md:223`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L223)（§4.2）及 [`monitor.py:768`](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L768)、[`backtest_holdmax.py:671`](file:///home/shenb/myQ/projects/spot-martingale-sens3/backtest_holdmax.py#L671)，每轮加仓前先判断 `last < 0.85C`（严格小于），满足即置 `stop_adding = 1` 并取消全部 `PENDING` 层；而层买入条件同样是 `last < trigger_price`（严格小于）。
  - 因此当 BTC `d = 3.0%` 或 ETH `d >= 3.0%` 时，价格跌破 `0.85C` 会先触发 `stop_adding = 1` 将第 6 层（及 ETH `d > 3.75%` 时的第 5 层）置为 `CANCELLED`，使该组最多只能买到 5 层（或 4 层），`bought_layers` 永远 `< 6`，从而无法享受 §5.3 的 `holdmax` 豁免。
- **修复建议**：规则字面无冲突（§5.3 已规定 `bought_layers < 6` 按未满层执行 7d/14d 止损），但建议在 §3.2 或 §5.3 补一句说明该边界效应（这也是 `combo` 3.0×/3.5× 档位经常被钳制在上限、无法买满 6 层触发 `holdmax` 而退化的重要原因）。

---

## 二、评审范围 7 项逐条核验记录

### 1. 两处改动与 SPEC 其余部分的一致性核验（§3.2、§3.3、§5.0、§5.3、§6.1、§7.1、§8.2）
- **§3.2（[`spec_v56_draft.md:180-195`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L180-L195)）**：BTC 间距公式改为 `min(max(2.0 × ATR / C, 0.008), 0.030)`，ETH 改为 `min(max(2.5 × ATR / C, 0.010), 0.040)`。与 §2 层数上限 6、加仓倍数 1.3、`S = 12.75603` 及权威层名义列表无冲突。高间距下与 §4.2 `< 0.85C` 停加仓线的耦合见 **[P2-3]**。
- **§3.3（[`spec_v56_draft.md:196-203`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L196-L203)）**：单层止盈价公式、整组止盈 `+1.5%`、关组规则、`close_reason` 与 `trades.reason` 枚举映射与满层豁免时间止损完全兼容（满层组通过 `TAKE_PROFIT`、`ALL_SOLD` 或 `DRAWDOWN_HALT` 关组，无需新增枚举值）。
- **§5.0（[`spec_v56_draft.md:238-252`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L238-L252)）**：第 (c) 步退出优先级第 2 项（14 天全清，L246）和第 4 项（7 天减半，L248）均已同步加上“仅当组未满层时执行，见 §5.3 holdmax 条款”，与第 1 项（回撤 halt）、第 3 项（整组止盈 +1.5%）、第 5 项（单层止盈）顺序闭合、无矛盾。
- **§5.3（[`spec_v56_draft.md:271-279`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L271-L279)）**：定义了 `bought_layers = COUNT(层状态 ∈ {OPEN, SOLD, TIME_STOPPED}) = 6` 时跳过 7d/14d 时间止损，并写死推论（7d 减半一旦执行即置 `stop_adding = 1` 并取消剩余 `PENDING` 层，故只有 7 天到期前已买满 6 层的组才会满层；满层后即使部分层单层止盈变为 `SOLD`，`bought_layers` 仍恒为 6；若触发 halt 则整组 `HALT_CLOSED` 并立即 `CLOSED`）。状态机严密闭合。
- **§6.1（[`spec_v56_draft.md:292-306`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L292-L306)）**：`bought_layers` 由 `layers.status` 实时聚合得出，不新增表或字段，重启恢复幂等性与 SQLite 事务原子性不受影响。
- **§7.1（[`spec_v56_draft.md:358-396`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L358-L396)）**：L367–368 已更新为 `2.0×ATR`、`2.5×ATR`；L385 新增了满层时间止损豁免行（需明确 `config.py` 对应字段名，见 **[P2-2]**）。
- **§8.2 与 §8.3(5)（[`spec_v56_draft.md:407-428`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L407-L428)）**：§8.2 已更新为新固定间距 `2.0% / 2.5%` 及对应预期数，但 §8.3(5)（L428）遗漏未更新单币最大亏损数（见 **[P1-2]**）。

---

### 2. 第 8.2 节压力测试预期数独立复算
按 §2 权威层名义（BTC：`[235.18, 305.74, 397.46, 516.70, 671.71, 873.21]`，合计 `3,000.00`；ETH：`[156.79, 203.83, 264.97, 344.46, 447.80, 582.15]`，合计 `2,000.00`）与 §8.2 新固定间距（BTC $d = 2.0\%$，各层触发价 $1.00C, 0.98C, 0.96C, 0.94C, 0.92C, 0.90C$；ETH $d = 2.5\%$，各层触发价 $1.000C, 0.975C, 0.950C, 0.925C, 0.900C, 0.875C$）独立复算：

1. **按中心价 $C$ 归一化的持仓等效名义 $K = \sum_{n=1}^6 \frac{\text{notional}_n}{1 - (n-1)d}$**：
   - $K_{\text{BTC}} = \frac{235.18}{1.00} + \frac{305.74}{0.98} + \frac{397.46}{0.96} + \frac{516.70}{0.94} + \frac{671.71}{0.92} + \frac{873.21}{0.90} = \mathbf{3,211.214175\text{ USDT}}$
   - $K_{\text{ETH}} = \frac{156.79}{1.000} + \frac{203.83}{0.975} + \frac{264.97}{0.950} + \frac{344.46}{0.925} + \frac{447.80}{0.900} + \frac{582.15}{0.875} = \mathbf{2,180.021230\text{ USDT}}$
   - $K_{\text{dual}} = K_{\text{BTC}} + K_{\text{ETH}} = \mathbf{5,391.235405\text{ USDT}}$
2. **持仓到 $0.5C$ 不卖的仓位浮亏（不含初始买入手续费与滑点，与 v5.5 口径一致）**：
   - BTC 期末市值 $= 0.5 \times 3,211.214175 = 1,605.607087\text{ USDT}$，仓位浮亏 $= 3,000.00 - 1,605.607087 = 1,394.392913 \approx \mathbf{1,394.39\text{ USDT}}$（若含 $3.60\text{ USDT}$ 买入成本，账户总亏损为 $1,397.99\text{ USDT}$，回撤 $13.98\%$）。
   - ETH 期末市值 $= 0.5 \times 2,180.021230 = 1,090.010615\text{ USDT}$，仓位浮亏 $= 2,000.00 - 1,090.010615 = 909.989385 \approx \mathbf{909.99\text{ USDT}}$（若含 $2.40\text{ USDT}$ 买入成本，账户总亏损为 $912.39\text{ USDT}$，回撤 $9.12\%$）。
   - 双币合计仓位浮亏 $= 1,394.392913 + 909.989385 = 2,304.382298 \approx \mathbf{2,304.38\text{ USDT}}$。
3. **双币情形在 $0.70C$、$0.65C$ 的权益与回撤，以及 15% 熔断触发点与小时数**：
   - 双币起始现金 $= 4,994.00\text{ USDT}$，初始峰值 `peak_equity` $= 10,000.00\text{ USDT}$。
   - $0.70C$ 时：$\text{equity} = 4,994.00 + 0.70 \times 5,391.235405 = 8,767.864783 \approx \mathbf{8,767.86\text{ USDT}}$，回撤 $= (10,000 - 8,767.864783)/10,000 = 12.3214\% \approx \mathbf{12.32\%}$。
   - $0.65C$ 时：$\text{equity} = 4,994.00 + 0.65 \times 5,391.235405 = 8,498.303013 \approx \mathbf{8,498.30\text{ USDT}}$，回撤 $= (10,000 - 8,498.303013)/10,000 = 15.0170\% \approx \mathbf{15.02\%}$。
   - 连续价格下恰好触发 15% 回撤（$\text{equity} = 8,500.00\text{ USDT}$）的价格比：
     $$p/C = \frac{8,500.00 - 4,994.00}{5,391.235405} = \mathbf{0.65031477C \approx 0.650C}$$
   - 离散整点步 $r = 0.5^{1/720}$ 下逐小时核对：
     - 第 **446** 小时：$r^{446} = 0.650922C$，$\text{equity} = 8,503.2712\text{ USDT}$，回撤 $= 14.9673\% < 15\%$（未触发）；
     - 第 **447** 小时：$r^{447} = 0.650295C$，$\text{equity} = 8,499.8945\text{ USDT}$，回撤 $= 15.0011\% \ge 15\%$（**触发 halt**，强平扣费后落地权益 $8,495.69\text{ USDT}$，落地回撤 $15.04\%$）。
- **核验结论**：草稿 [`spec_v56_draft.md:97`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L97) 与 [`:418`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L418) 的全部预期数（`1,394.39`、`909.99`、`2,304.38`、`0.650C`、`第 447 小时`、`8,767.86 / 12.32%`、`8,498.30 / 15.02%`）**分毫不差，全部算对**。唯需修正 §8.3(5) L428 遗漏的旧数（**[P1-2]**）及回测脚本中的硬编码（**[P1-3]**）。

---

### 3. 第 5.3 节 `holdmax` 定义与 `backtest_holdmax.py` 的逐行对照
对照 [`spec_v56_draft.md:246-249, 275-277`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L275-L277) 与 [`backtest_holdmax.py:570-666`](file:///home/shenb/myQ/projects/spot-martingale-sens3/backtest_holdmax.py#L570-L666)：

| 规范条款（`spec_v56_draft.md`） | 代码实现（`backtest_holdmax.py`） | 对照结果 |
|---|---|---|
| `bought_layers = COUNT(层状态 ∈ {OPEN, SOLD, TIME_STOPPED})`，当 `bought_layers = 6` 时称“满层”（§5.3 L275） | L578–579：`_bought = sum(1 for l in grp["layers"] if l["status"] in ("OPEN", "SOLD", "TIME_STOPPED"))`；`fully_filled = _bought >= 6`（组最多 `MAX_LAYERS = 6` 层，见 L787，故 `_bought >= 6` 与 `== 6` 严格等价） | **完全一致** |
| 优先级 2：满 14 天时间止损（仅当组未满层时执行，§5.0 L246、§5.3 L275） | L582：`if (not fully_filled) and current_dt - first_buy_dt >= timedelta(days=config.TIME_STOP_14D_DAYS):` | **完全一致** |
| 优先级 3：整组止盈 +1.5%（满层组照常判断，§5.0 L247、§5.3 L275） | L596–600：`if not skip_tp:` 计算 `avg_cost` 并判断 `px >= avg_cost * (1.0 + config.GROUP_TP_PCT)`，不受 `fully_filled` 拦截 | **完全一致** |
| 优先级 4：满 7 天时间止损减半（仅当组未满层时执行；满层组不执行、不记 `half_time_stopped`、不置 `stop_adding`，§5.0 L248、§5.3 L275） | L614：`if (not fully_filled) and grp.get("half_time_stopped", 0) == 0 and (current_dt - first_buy_dt >= timedelta(days=config.TIME_STOP_7D_DAYS)):` | **完全一致** |
| 优先级 5：单层止盈 & 优先级 1：回撤 halt（满层组照常判断，§5.0 L245/L249、§5.3 L275） | L645–665（单层止盈）与 L418–446（回撤 halt）均不受 `fully_filled` 拦截 | **完全一致** |

---

### 4. 解释 A 与解释 B 的处理核验
- **诚实性与可执行性**：草稿在 `V5.6-3`（L98）、`V5.6-4`（L99）、`§5.0`（L246/L248）、`§5.3`（L275）及 `§9 第 9 条`（L445）明确选定**解释 A**（满层组只豁免 7d/14d 时间止损，单层止盈、整组止盈与回撤 halt 照常执行），且如实说明这正是 `combo2` 回测（`backtest_holdmax.py`）实测的逻辑，而**解释 B 未经回测、不在本草稿内**，留给 moka 拍板确认。处理诚实，解释 A 本身无任何执行歧义。
- **文本细节**：L99 与 L445 对被排除的“解释 B”括号注释不一致（见 **[P2-1]**）。

---

### 5. 证据陈述诚实性核验
逐项核对 [`spec_v56_draft.md:95`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L95) 与 [`combo2/bt.log`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/bt.log)、[`combo/bt.log`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo/bt.log)、[`sens3/RESULT.md`](file:///home/shenb/myQ/projects/spot-martingale-sens3/RESULT.md)、[`sens2/SWEEP.md`](file:///home/shenb/myQ/projects/spot-martingale-sens2/SWEEP.md)：
- **回测指标完全真实**：
  - 训练集：`+1,720.77 USDT`、`PF 1.53`、最大回撤 `15.02%`、盈利月 `84.62% (33/39)`、总成交 `1,856` 笔（卖单 `927` 笔），与 [`combo2/bt.log:24-28`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/bt.log#L24-L28) 完全一致。
  - 测试集：`-173.45 USDT`、`PF 14.83`、最大回撤 `9.37%`、盈利月 `100.00% (10/10)`、总成交 `264` 笔（卖单 `129` 笔），与 [`combo2/bt.log:47-51`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/bt.log#L47-L51) 完全一致。
  - 压力测试 4：`-182.12 USDT`、`PF 1.75`、总成交 `57` 笔（卖单 `25` 笔），与 [`combo2/bt.log:83-87`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/bt.log#L83-L87) 完全一致。
- **风险与局限披露非常诚实，无夸大或隐瞒**：
  1. 明确写出测试集总盈亏仍为 `-173.45 USDT`、§8.3 第 1 项仍为 **FAIL**，声明“本草稿不是‘已证明盈利的策略’，只是目前测过最接近通过的候选”；
  2. 明确解释了为什么测试集 `PF = 14.83`、盈利月 `100% (10/10)` 但总盈亏仍为 `-173.45 USDT`：因为 PF 与盈利月按 §8.3(3)(4) 只统计已平仓 `SELL` 成交，而满层豁免时间止损后，测试集期末未平仓满层组的浮亏计入期末总盈亏但未触发卖出；且测试集仅有 10 个有成交自然月、264 笔成交，样本量偏小；
  3. 明确披露了在 `sens` / `sens2` / `sens3` 累计测试约 10 档变体（`adx20/25/30/35`、`zerocost`、`widespace`、`tp25`、`tp35`、`space25`、`space30`、`holdmax`、`bysymbol`、`combo`、`combo2`）并按测试集表现挑出 `combo2` 的**测试集过拟合风险**（违反 §8.2“调参只许在训练集”的原则），且披露了 `combo`（`3.0×/3.5× + holdmax`，见 [`combo/bt.log:24,47`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo/bt.log#L24)）训练 `-362.96`、测试 `-600.59` 的不叠加事实。

---

### 6. 其他逻辑缺口或歧义核验
- 除上述 **[P1-1]**、**[P1-2]**、**[P1-3]** 及 **[P2-1]**、**[P2-2]**、**[P2-3]** 外，其余章节未发现新的逻辑缺口。

---

### 7. `combo2` 回测可复现性独立重跑核验
- 在 `/home/shenb/myQ/projects/spot-martingale-sens3/` 下创建临时独立副本运行 `combo2` 配置与 `backtest_holdmax.py --all`（跑完后已清理临时副本，全程未触碰 live 目录）：
  - 原日志 [`spot-martingale-sens3/combo2/bt.log`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/bt.log) sha256：`7ff6555153d2107c584534c838097f83b07a8416ca1e3f3625e6e431f51416f8`
  - 独立重跑输出 `bt_repro.log` sha256：`7ff6555153d2107c584534c838097f83b07a8416ca1e3f3625e6e431f51416f8`
  - `diff -u` 对比结果：**0 字节差异，100% 逐字节完全可复现**。

---

## 三、准入结论与下一步建议

- **结论**：**FAIL（暂不可直接冻结）**。
- **修复闭环路径（仅需改 3 处文本即可消除全部 P1）**：
  1. 在 [`spec_v56_draft.md:89`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L89) 补回 [`spec.md:3`](file:///home/shenb/myQ/projects/spot-martingale/spec.md#L3) / [`merged_review_v55.md:23`](file:///home/shenb/myQ/projects/spot-martingale/results/merged_review_v55.md#L23) 的 `P-1` 权威解释（**[P1-1]**）；
  2. 将 [`spec_v56_draft.md:428`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L428) 的 `BTC 1,422.5 / ETH 929.6` 更新为 `BTC 1,394.39 / ETH 909.99`（**[P1-2]**）；
  3. 在 [`spec_v56_draft.md:95`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L95) 注明 `combo2/bt.log` 中 ST1–ST3 仍为旧硬编码间距 `0.015 / 0.020`，并附上新间距 `0.020 / 0.025` 下 ST2 Dual `Halt hour = 447, post-halt dd = 15.04%` 的实测结果（**[P1-3]**）；同时建议顺手清理 **[P2-1]**、**[P2-2]**、**[P2-3]**。
  4. **策略层决策提请 moka 裁决**：修完上述文本问题后，规范逻辑本身即完全自洽；但因 `combo2` 测试集总盈亏仍为 `-173.45 USDT`（未满足 §8.3 第 1 项 `> 0` 的硬门槛），是否特批豁免 §8.3(1) 将 v5.6 冻结并切换模拟盘，须由 moka 最终拍板。
