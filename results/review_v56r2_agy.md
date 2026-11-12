# BTC/ETH 现货马丁策略 SPEC v5.6 草稿（第 2 轮）盲审报告

- **评审对象**：[`spec_v56_draft.md`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md)（第 2 轮草稿，466 行，实测 md5 `98f08a6d569073e85d0b767e916ca76f`）
- **对照基准**：[`spec.md`](file:///home/shenb/myQ/projects/spot-martingale/spec.md)（已冻结 v5.5，438 行，评审前后实测 md5 均为 `faa9b30a3f7e2fd651a7acc97cc1e023`，分毫无改）
- **核对材料**：
  - [`results/merged_review_v56.md`](file:///home/shenb/myQ/projects/spot-martingale/results/merged_review_v56.md)（第 1 轮合并评审，77 行）
  - [`results/review_v56_agy.md`](file:///home/shenb/myQ/projects/spot-martingale/results/review_v56_agy.md)（第 1 轮第一路盲审报告，178 行）
  - [`results/review_v56_agy2.md`](file:///home/shenb/myQ/projects/spot-martingale/results/review_v56_agy2.md)（第 1 轮第二路盲审报告，90 行）
  - [`spot-martingale-sens3/combo2/bt.log`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/bt.log)（100 行，sha256 `7ff6555153d2107c584534c838097f83b07a8416ca1e3f3625e6e431f51416f8`）
  - [`spot-martingale-sens3/stverify/bt.log`](file:///home/shenb/myQ/projects/spot-martingale-sens3/stverify/bt.log)（新间距 ST1–ST3 重跑日志，100 行，sha256 `a50b7458e02b54bc3609c83384a68e362bf1916d85fcf5fa22dd8ebfea5b68e1`）
  - [`spot-martingale-sens3/backtest_holdmax.py`](file:///home/shenb/myQ/projects/spot-martingale-sens3/backtest_holdmax.py)（1,471 行）与 [`spot-martingale-sens3/stverify/backtest.py`](file:///home/shenb/myQ/projects/spot-martingale-sens3/stverify/backtest.py)（1,471 行）
- **盲审结论**：**PASS（第 1 轮合并评审提出的 M-1 至 M-10 修复项与 D-1 豁免项已全部准确落实到位，无新引入的文本矛盾或数字错误，可以进入冻结评审）**
  - **分级汇总**：**P0: 0 项｜P1: 0 项｜P2: 1 项（非阻塞的代码实现提示）**

---

## 一、分级问题清单（P0 / P1 / P2）

### P0（致命架构或账务缺陷）：无（0 项）

### P1（阻塞冻结的规范矛盾、遗漏或回测口径偏差）：无（0 项）

### P2（非阻塞的代码实现注意事项）：1 项

#### [P2-1] `V5.6-2` / `§8.2` 要求压力测试固定间距读取 `BTC_SPACING_COEFF / ETH_SPACING_COEFF` 时，代码实现须注意量纲换算（除以 100）
- **位置与依据**：
  - [`spec_v56_draft.md:97`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L97)（`V5.6-2`）与 [`spec_v56_draft.md:425`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L425)（`§8.2`）规定：
    > `实现要求：压力测试 1–3 的固定间距取配置值（BTC_SPACING_COEFF / ETH_SPACING_COEFF），不得硬编码；回测报告须记录实际使用的固定间距值。`
  - 在 [`combo2/config.py:40-44`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/config.py#L40-L44) 中，`BTC_SPACING_COEFF = 2.0`、`ETH_SPACING_COEFF = 2.5` 是无量纲的 ATR 倍数；而 [`stverify/backtest.py:1171-1173, 1271-1272`](file:///home/shenb/myQ/projects/spot-martingale-sens3/stverify/backtest.py#L1171-L1173) 中压力测试的固定间距参数 `d` 是小数比例 `0.020`（2.0%）与 `0.025`（2.5%）。
  - 规范正文在同一段（[`spec_v56_draft.md:97, 425`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L425)）已明确写死“BTC d=2.0% 固定（L1 C、L2 0.98C……），ETH d=2.5% 固定（L1 C、L2 0.975C……）”，因此规范本身无歧义；但在后续正式改写 `backtest.py` 时，须写为 `config.BTC_SPACING_COEFF / 100.0`（或新增专用百分比常量），切勿将 `2.0 / 2.5` 直接代入 `1.0 - (lno - 1) * d`。

---

## 二、M-1 至 M-10 逐项核验表（含文件路径与行号证据）

| 编号 | 修复要求（来自 `merged_review_v56.md`） | 第 2 轮草稿落实位置与原文字据 | 核验结论 |
|---|---|---|---|
| **M-1** | `P-1` 权威解释补回 `V5.5-2` | [`spec_v56_draft.md:3`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L3)、[`:89`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L89)、[`:102`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L102)：第 89 行 `V5.5-2（O-2）` 已完整补入 `P-1（moka 冻结 v5.5 时的裁决，见 merged_review_v55.md §二 P-1，仍有效）：在首根有效 bar 到来之前的每轮调用 evaluate_symbol_price 时均可显式传 latest_kline_close=None，该轮不进入跳变分支，只按 V5.4-1(b) 计一次价格错误；首根有效 bar 所在的轮次按 V5.4-1(a) 回退分支初始化（将其 close 写入 last_valid_price），不计价格错误。` 与 [`spec.md:3`](file:///home/shenb/myQ/projects/spot-martingale/spec.md#L3) 及 [`merged_review_v55.md:23`](file:///home/shenb/myQ/projects/spot-martingale/results/merged_review_v55.md#L23) 完全一致。 | **PASS（已改到位）** |
| **M-2** | `§8.3(5)` 单币亏损数更新为含成本口径 | [`spec_v56_draft.md:441`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L441)、[`:103`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L103)：第 441 行已将旧间距 `BTC 1,422.5 / ETH 929.6` 更新为 `单币满仓跌 50% 最多亏 BTC 1,397.99 / ETH 912.39，含买入成本，对应最大回撤 13.98% / 9.12%，数学上触发不了 15% halt`，与 `§8.2` 及 [`stverify/bt.log:59-60`](file:///home/shenb/myQ/projects/spot-martingale-sens3/stverify/bt.log#L59-L60) 完全一致。 | **PASS（已改到位）** |
| **M-3** | `§8.2` 亏损数统一为含成本口径并注明仓位浮亏分量 | [`spec_v56_draft.md:97`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L97)、[`:103`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L103)、[`:431`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L431)：第 97 行与第 431 行均已统一为 `含买入成本口径，与回测报告一致：BTC 亏 1,397.99（仓位浮亏 1,394.39 + 买入成本 3.60）、ETH 亏 912.39（仓位浮亏 909.99 + 买入成本 2.40）、双币合计亏 2,310.38`，第 103 行同步注明仓位浮亏分量 `1,394.39 / 909.99 / 2,304.38`。 | **PASS（已改到位）** |
| **M-4** | 压力测试固定间距与证据跟随新间距、`§8.3(5)` 在新间距下是否仍 PASS | [`spec_v56_draft.md:95`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L95)、[`:97`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L97)、[`:104`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L104)、[`:425`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L425)：第 95 行明确说明 `combo2/bt.log` 中 ST1–ST3 为旧间距（`0.015 / 0.020`），并附上新间距（`0.020 / 0.025`）重跑实测数（`ST1 +3,080.85、ST2 双币 -1,504.31 / 回撤 15.04% / halt 第 447 小时、ST2 BTC -1,397.99 / 回撤 13.98%、ST2 ETH -912.39 / 回撤 9.12%、ST3 -477.36 / 回撤 12.32%`）；经核对 [`stverify/bt.log:55-63, 97`](file:///home/shenb/myQ/projects/spot-martingale-sens3/stverify/bt.log#L55-L63)，`§8.3(5)` 在新间距下仍为 **PASS**（双币强平落地回撤 `15.04% <= 16.00%`，`halt_hour=447`，`recovery=True`；单币回撤 `13.98% / 9.12% <= 16.00%`）。第 97、425 行已加上禁止硬编码、须跟随配置并记录实际间距值的实现要求。 | **PASS（已改到位）** |
| **M-5** | 解释 B 拆为 B1 与 B2 且两处表述统一 | [`spec_v56_draft.md:99`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L99)、[`:105`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L105)、[`:458`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L458)：第 99 行与第 458 行均已统一拆分为 `解释 B1（满层组连止盈也不卖，只等 halt 或无限持有）` 与 `解释 B2（全部组彻底取消 7d/14d 时间止损）`，并一致声明两者均未测试、不在本草稿内，本草稿采用解释 A。 | **PASS（已改到位）** |
| **M-6** | `§7.1` 给出 `HOLDMAX_EXEMPT_TIME_STOP` 常量名、`§8.3(6)` 补入 `holdmax` 单测 | [`spec_v56_draft.md:398`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L398)、[`:442`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L442)、[`:106`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L106)：第 398 行已明确定义为 `\| HOLDMAX_EXEMPT_TIME_STOP \| True（布尔）：买入层数 6/6 时跳过 7d/14d 时间止损，止盈与 halt 照常 \| §5.3（V5.6） \|`；第 442 行已补入 `满层组（bought_layers == 6）跳过 7d/14d 时间止损且不置 half_time_stopped、不置 stop_adding，止盈与 halt 照常；未满层组（bought_layers < 6）照常执行 7d/14d 时间止损`。 | **PASS（已改到位）** |
| **M-7** | `§5.3` 推论补全并写清 `d >= 3.0%` 的边界效应 | [`spec_v56_draft.md:288`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L288)、[`:107`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L107)：第 288 行已完整列出阻止满层的三种情形（① 7 天减半、② §3.2 上限拒买、③ §4.2 价格跌破 `C×0.85` 停加仓），并写死边界效应：`BTC d=3.0% 时 L6 触发价恰为 0.85C、ETH d≥3.0% 时 L6 触发价 ≤0.85C、ETH d>3.75% 时 L5 触发价 <0.85C——价格跌破 0.85C 会先触发 §4.2 停加仓而取消 PENDING 层，此类组最多买到 5 层（或 4 层），永远达不到满层、不享受本条豁免`。 | **PASS（已改到位）** |
| **M-8** | 满层组无限持有后果披露 | [`spec_v56_draft.md:289`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L289)、[`:108`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L108)：第 289 行新增 `满层组无时间上限（v5.6 披露）` 子项，明确说明单层止盈变为 `SOLD` 仍计入 `bought_layers`、组保持满层豁免，最长持有时间取决于价格回到止盈价或触发 `§5.1 halt`，持有期间该币种不开新组、资金持续占用。 | **PASS（已改到位）** |
| **M-9** | `§3.2` 示例改为 `d = 2.0%` | [`spec_v56_draft.md:198`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L198)、[`:109`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L109)：第 198 行已改为 `BTC C=80,000，d=2.0%，各层触发价为 L2 78,400、L3 76,800、L4 75,200、L5 73,600、L6 72,000`，算术完全正确。 | **PASS（已改到位）** |
| **M-10** | `§9` 第 10 条线上过渡列为明确裁决项 | [`spec_v56_draft.md:459`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L459)、[`:110`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L110)：第 459 行标题改为 `若 v5.6 冻结，线上模拟盘的过渡（冻结时必须由 moka 明确裁决，不可只列“待定”）`，详述了线上 ETH 组 2 已满层、切 v5.6 后将从原 7d/14d 时间止损变为无限期持有直到止盈或 halt 的实质性行为变化，并给出 `(a) 立即重启切 v5.6；(b) 等两组自然了结后再切` 两个明确选项交由 moka 在冻结时裁决。 | **PASS（已改到位）** |

---

## 三、D-1 豁免条款（§9 第 11 条）专项核验

- **位置与原文**：
  - [`spec_v56_draft.md:3`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L3)：`§8.3(1) 已由 moka 豁免（见 §9 第 11 条）。`
  - [`spec_v56_draft.md:95`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L95)：`§8.3(1) 已由 moka 豁免（见 §9 第 11 条），豁免后 v5.6 的冻结不再以第 1 项通过为前提；其余 §8.3 各项仍须满足。`
  - [`spec_v56_draft.md:111`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L111)：`#  R2-10（D-1）：§9 新增第 11 条——§8.3(1) 已由 moka 豁免，v5.6 作为例外候选冻结；豁免仅适用于 v5.6 本轮。`
  - [`spec_v56_draft.md:460`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L460)：
    > `11. **§8.3(1) 豁免裁决（moka 2026-11-10 决定，已豁免）**：combo2 测试集总盈亏 -173.45 USDT，未满足 §8.3 第 1 项（测试集扣成本后总盈亏 > 0）。moka 决定豁免本项、把 v5.6 作为例外候选冻结：v5.6 的冻结不以 §8.3(1) 通过为前提；其余 §8.3 各项（2/3/4/5/6/7）仍须满足。豁免仅适用于 v5.6 本轮，不自动延及后续版本。`
- **核验结论**：
  1. **事实表述准确**：如实记录 `combo2` 测试集总盈亏 `-173.45 USDT` 未满足 `§8.3(1)`，且未篡改 [`spec_v56_draft.md:437`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L437) 的原验收标准条文。
  2. **验收项边界清晰**：明确限定**只针对 `§8.3(1)`**，显式写明“其余 §8.3 各项（2/3/4/5/6/7）仍须满足”，未扩大到任何其他验收项。
  3. **版本边界清晰**：显式写明“豁免仅适用于 v5.6 本轮，不自动延及后续版本”，未扩大到后续版本。

---

## 四、第 8.2 节预期数与 `stverify` 重跑日志独立复算及可复现性核验

### 1. 独立算术复算与 `stverify/bt.log` 逐项比对
基于 [`spec_v56_draft.md:180-181`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md#L180-L181) 权威层名义（BTC：`[235.18, 305.74, 397.46, 516.70, 671.71, 873.21]`，合计 `3,000.00`；ETH：`[156.79, 203.83, 264.97, 344.46, 447.80, 582.15]`，合计 `2,000.00`）与 `V5.6-2` 新固定间距（BTC $d = 0.020$，ETH $d = 0.025$），用 Python 独立复算并与草稿及 [`stverify/bt.log`](file:///home/shenb/myQ/projects/spot-martingale-sens3/stverify/bt.log) 核对如下：

- **等效持仓常数 $K = \sum_{n=1}^6 \frac{\text{notional}_n}{1 - (n-1)d}$**：
  - $K_{\text{BTC}} = 3,211.214175\text{ USDT}$，$K_{\text{ETH}} = 2,180.021230\text{ USDT}$，$K_{\text{dual}} = 5,391.235405\text{ USDT}$。

| 测试项 / 指标 | 独立复算精确值（四舍五入值） | 草稿第 2 轮数值（L95/L97/L431/L441） | `stverify/bt.log` 实测值（行号） | 对照结果 |
|---|---|---|---|---|
| **ST1 (+50%)** 期末权益 / 总盈亏 | `13,080.8531` (`13,080.85`) / `+3,080.8531` (`+3,080.85`) | `+3,080.85`（L95） | `end_equity: 13080.85`, `total_pnl: 3080.85`（L55） | **完全一致** |
| **ST2 (-50%) BTC 单独** 仓位浮亏 + 成本 = 总亏损（回撤） | `1,394.3929 + 3.60 = 1,397.9929` (`1,397.99`, `13.98%`) | `1,397.99（仓位浮亏 1,394.39 + 买入成本 3.60）`，回撤 `13.98%`（L95/L97/L431/L441） | `total_pnl: -1397.99`, `max_drawdown_pct: 13.98`, `end_equity: 8602.01`（L59） | **完全一致** |
| **ST2 (-50%) ETH 单独** 仓位浮亏 + 成本 = 总亏损（回撤） | `909.9894 + 2.40 = 912.3894` (`912.39`, `9.12%`) | `912.39（仓位浮亏 909.99 + 买入成本 2.40）`，回撤 `9.12%`（L95/L97/L431/L441） | `total_pnl: -912.39`, `max_drawdown_pct: 9.12`, `end_equity: 9087.61`（L60） | **完全一致** |
| **ST2 (-50%) 双币不卖** 仓位浮亏 + 成本 = 合计亏损 | `2,304.3823 + 6.00 = 2,310.3823` (`2,310.38`) | `2,310.38`（浮亏分量 `2,304.38`，L97/L103/L431） | （解析参考值，实盘/回测在 `h=447` 触发 halt 平仓） | **完全一致** |
| **ST2 双币 `0.70C`** 权益 / 回撤 | `8,767.8648` (`8,767.86`) / `12.3214%` (`12.32%`) | `8,767.86` / `12.32%`（L97/L431） | 对应 ST3 最低点回撤 `12.32%`（L63） | **完全一致** |
| **ST2 双币 `0.65C`** 权益 / 回撤 | `8,498.3030` (`8,498.30`) / `15.0170%` (`15.02%`) | `8,498.30` / `15.02%`（L97/L431） | （解析参考值） | **完全一致** |
| **ST2 双币 halt 触发点与小时** | 连续阈值 `0.650315C`；离散第 `446` 小时回撤 `14.9673% < 15%`，第 **`447`** 小时价格比 `0.650295C`、权益 `8,499.8945`、回撤 `15.0011% >= 15%` 触发 halt | `约 0.650C、约第 447 小时`（L95/L97/L431） | `Halt hour: 447, recovery verified: True`（L58、L97） | **完全一致** |
| **ST2 双币 halt 强平后** 期末权益 / 总盈亏 / 落地回撤 | 第 `447` 小时强平扣双边费率后：`8,495.6873` (`8,495.69`) / `-1,504.3127` (`-1,504.31`) / `15.0431%` (`15.04%`) | `-1,504.31 / 回撤 15.04% / halt 第 447 小时`（L95） | `total_pnl: -1504.31`, `max_drawdown_pct: 15.04`, `post_halt_max_dd_pct: 15.04`, `end_equity: 8495.69`（L58） | **完全一致** |
| **ST3 (-30% 后 +20%)** 期末权益 / 总盈亏 / 最大回撤 | `9,522.6377` (`9,522.64`) / `-477.3623` (`-477.36`) / `12.3214%` (`12.32%`) | `-477.36 / 回撤 12.32%`（L95） | `total_pnl: -477.36`, `max_drawdown_pct: 12.32`, `end_equity: 9522.64`（L63） | **完全一致** |

### 2. 第 2 轮改动全文 diff 审查与可复现性重跑验证
1. **全文 diff 核查**：对 [`spec.md`](file:///home/shenb/myQ/projects/spot-martingale/spec.md)（438 行）与 [`spec_v56_draft.md`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md)（466 行）执行 `diff -u` 逐行比对，第 2 轮草稿除状态头、v5.5→v5.6 变更日志、第 2 轮变更日志（`R2-1`～`R2-10`）、`§3.2` 间距系数与示例、`§5.0` 优先级注记、`§5.3` holdmax 条款（含推论、边界效应、无限持有披露、重启恢复）、`§6.2` 目录说明、`§7.1` 冻结值表、`§8.2` 压力测试新间距与预期数、`§8.3(5)(6)` 验收项更新及 `§9` 第 8–11 条外，**未触碰任何已冻结章节或数字，未引入任何新的矛盾、歧义或笔误**。
2. **评审副本独立重跑可复现性**：在 `/home/shenb/myQ/projects/spot-martingale-sens3/` 下建立临时评审副本分别重跑 `combo2` 与 `stverify`（验证后已删除临时副本，全程只读 live 目录）：
   - `combo2` 重跑日志与 [`combo2/bt.log`](file:///home/shenb/myQ/projects/spot-martingale-sens3/combo2/bt.log) `diff -u` 零差异，sha256 均为 `7ff6555153d2107c584534c838097f83b07a8416ca1e3f3625e6e431f51416f8`；
   - `stverify` 重跑日志与 [`stverify/bt.log`](file:///home/shenb/myQ/projects/spot-martingale-sens3/stverify/bt.log) `diff -u` 零差异，sha256 均为 `a50b7458e02b54bc3609c83384a68e362bf1916d85fcf5fa22dd8ebfea5b68e1`；
   - 训练集（`+1,720.77 USDT`）、测试集（`-173.45 USDT`）、压力测试 4（`-182.12 USDT`）在两份日志中完全一致，证实 ST1–ST3 间距修正不影响策略回放结果。

---

## 五、最终结论

- **评审结论**：**PASS**。[`spec_v56_draft.md`](file:///home/shenb/myQ/projects/spot-martingale/spec_v56_draft.md)（第 2 轮）已将 `M-1` 至 `M-10` 与 `D-1` 全部准确修改到位，文本逻辑严密自洽，所有数字与独立算术复算及回测重跑日志 100% 吻合，可进入冻结评审（冻结时由 moka 对 `§9` 第 10 条线上过渡方案作最终选定）。
