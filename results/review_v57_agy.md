# BTC/ETH 现货马丁策略 SPEC v5.7 草案（第 1 轮）独立盲审报告

- **评审对象**：[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md)（518 行，MD5: `378e16ac27fee10e339c1f32c98f758a`）
- **对照基准**：[spec.md](file:///home/shenb/myQ/projects/spot-martingale/spec.md)（已冻结 v5.6，477 行，评审前后校验 MD5 均为 `79eabf69bb27ec30530073a166c54194`，未发生任何改动）
- **核对代码与文件**：[engine.py](file:///home/shenb/myQ/projects/spot-martingale/engine.py)、[backtest.py](file:///home/shenb/myQ/projects/spot-martingale/backtest.py)、[monitor.py](file:///home/shenb/myQ/projects/spot-martingale/monitor.py)、[config.py](file:///home/shenb/myQ/projects/spot-martingale/config.py)、[merged_review_v56r2.md](file:///home/shenb/myQ/projects/spot-martingale/results/merged_review_v56r2.md)、只读查询线上库 `data/spot_martingale.sqlite3`（`mode=ro`）
- **评审时间**：2026-11-12 UTC
- **总体结论**：**FAIL**（草稿数学复算全部准确、证据披露诚实，但存在 **0 个 P0、4 个 P1、6 个 P2**，涉及动态倍数截断与最小步长取整的死循环漏洞、动态名义下 7 天减半口径冲突、趋势状态在 K 线缺失/不足 500 根时的未定义行为与冷却边界缺口，以及 §9 第 18 条过渡项漏掉 BTC 组 1 同样已买满 6 层及数据库无 L7–L9 `PENDING` 行的事实，须修订后再进入冻结评审）

---

## 一、问题分级汇总（P0 / P1 / P2）

### 1. P0 级问题（0 项）
无。

---

### 2. P1 级阻塞问题（4 项，阻塞冻结评审）

#### **P1-1：V5.7-3 剩余额度截断与 §1.3 最小步长向下取整的顺序冲突，导致剩余额度在 `[5 USDT, 1 最小步长名义)` 区间时陷入无限重试死循环，且 §2 漏写取消其余 PENDING 层**
- **依据位置**：[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L176-L177)（§1.3）、[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L203)（§2）、[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L224-L227)（§3.2）；对照 [engine.py](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L441-L461)（[`compute_order_qty`](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L441-L461)）、[monitor.py](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L809-L843)、[backtest.py](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L691-L706)。
- **问题详情**：
  1. **步长取整死循环漏洞**：§2 第 203 行规定：`每笔买入的实际名义 = min(计划名义, 单币剩余额度, 合计剩余额度)... 截断后名义 < 5 USDT 时该层不买入、置 CANCELLED、组置 stop_adding=1（上限已用满）、只记一次 ERROR，不无限重试；截断后名义 ≥ 5 USDT 时按截断后名义买入（残差行为）。`
     但 §1.3 第 176–177 行规定：`最小下单：BTC 0.001 BTC、ETH 0.01 ETH、单笔名义 ≥ 5 USDT... 数量 = 层名义 / 成交价，再按最小步长向下取整。取整后名义 < 5 USDT 的层跳过，不把金额累到下一层。`
     以 BTC 价格 $P = 75,000\text{ USDT}$ 为例，最小步长 $0.001\text{ BTC}$ 价值 **$75.00\text{ USDT}$**。当前序层因为步长向下取整或动态倍数接近 $3,000\text{ USDT}$ 上限时，单币剩余额度极易落在 **$[5.00, 74.99]\text{ USDT}$** 区间（例如剩余额度为 $50.00\text{ USDT}$）：
     - 若先按 §2 第 203 行判断“截断后名义 `< 5 USDT`”，因 $50.00 \ge 5.00$，不触发上限用满作废；
     - 随后进入 §1.3 / [`compute_order_qty`](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L441-L461) 按 $0.001\text{ BTC}$ 向下取整，算出 `qty = floor(50 / 75000 / 0.001) * 0.001 = 0.000 BTC`，取整后实际成交名义 `actual_notional = 0.00 < 5.00 USDT`；
     - 按 §1.3 第 177 行（及 [monitor.py](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L811-L813) / [backtest.py](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L693-L694) 现有逻辑），`actual_notional < 5.00` 执行“跳过该层（保留 `PENDING`、记 WARNING）”。结果该层既不置 `CANCELLED` 也不置 `stop_adding = 1`，在实盘每 60 秒轮询中**每轮无限重试并刷屏 WARNING**，且后续更高层也因 `bought_prior != lno - 1` 永远卡死，直接违反 §2 第 203 行与 §8.3(6)“上限拒买后不无限重试”。
  2. **术语混用与漏置其余 PENDING 层**：§2 第 203 行将 `min(计划名义, 单币剩余额度, 合计剩余额度)` 称为“实际名义”，但经过 §1.3 步长取整后的 `qty × buy_price` 才是真正的“实际成交名义（basis）”。此外，§2 第 203 行在描述截断作废时漏写了 §3.2 第 227 行的**“并将组内其余 PENDING 层一并置为 CANCELLED”**（在 v5.7 截断机制下，下单前已截断至剩余额度，不可能再发生旧版 `deployed + actual_notional > cap` 的越界，截断/取整后不足最小下单量即为唯一的上限用满拒买出口）。
- **修订建议**：
  在 §2 第 203 行与 §3.2 第 227 行明确写死三步计算与判定：
  1. `计划名义 = round(上一层实际成交名义 × 当前分档倍数, 2)`（L1 取种子名义）；
  2. `截断后计划名义 = min(计划名义, 单币剩余额度, 合计剩余额度)`；
  3. 将 `截断后计划名义` 代入 §1.3 按最小步长向下取整得 `qty` 与 `实际成交名义 = qty × buy_price`。
  凡因剩余额度截断导致 `截断后计划名义 < 5.00 USDT`，**或截断后再经最小步长向下取整导致 `实际成交名义 < 5.00 USDT`（含 `qty == 0`，即剩余额度不足以买 1 个最小步长）**，一律视为上限已用满：将该层及组内其余全部 `PENDING` 层一并置为 `CANCELLED`、组置 `stop_adding = 1`、只记一次 ERROR，不再重试。

---

#### **P1-2：V5.7-3 动态层名义与 §5.3 满 7 天减半“初始层名义”口径冲突**
- **依据位置**：[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L198-L203)（§2）、[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L325)（§5.3）；对照 [monitor.py](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L722-L732)、[backtest.py](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L616-L626)。
- **问题详情**：
  - §5.3 第 325 行沿用了 v5.6 原文：`从最高层号的 OPEN 层往低层号整层卖出（不拆层），直到累计卖出的初始层名义 ≥ 组内当前全部 OPEN 层初始名义之和的 50%。`
  - 在 v5.6 中每层计划名义固定，故 [monitor.py](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L722) 与 [backtest.py](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L616) 均通过 [`engine.get_layer_planned_notional(sym, l["layer_no"])`](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L432-L439) 查 §2 均匀表计算 50% 阈值。
  - 在 v5.7 中，实盘与策略回测改用动态倍数递归（`1.2/1.3/1.4/1.5`）加剩余额度截断，§2 第 203 行明确声明“均匀路径表不等于实际买入名义”，且 `layers` 表（§6.1 第 351 行）只存实际成交的 `notional_usdt`（即 `qty × buy_price`），不存动态计划名义。
  - **反例**：若某 BTC 组买到第 8 层时因剩余额度截断，第 8 层仅买入残差 $15.00\text{ USDT}$（前 7 层共 $2,985.00\text{ USDT}$），满 7 天触发减半时：若“初始层名义”仍查 §2 均匀表（将第 8 层算作 $587.99\text{ USDT}$），则会严重高估第 8 层权重而导致减半卖出比例失真；若改用各 OPEN 层实际买入名义 `layers.notional_usdt`，则与现有代码 `get_layer_planned_notional` 不同。草稿未定义 v5.7 下“初始层名义”指代 `layers.notional_usdt` 还是 §2 均匀表名义。
- **修订建议**：
  在 §5.3 第 325 行明确写死：v5.7 下 7 天减半的“初始层名义”取该层买入时的实际成交名义 `layers.notional_usdt`（即 `qty × buy_price`，压力测试 1–3 不执行时间止损故无影响）。

---

#### **P1-3：§4.3 趋势状态在 K 线缺失/不足 500 根时的未定义行为、每轮执行时点错位，以及 `SLOW_DOWN` 24h 冷却边界与同轮多层买入缺口**
- **依据位置**：[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L224-L225)（§3.2）、[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L232)（§3.3）、[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L239-L242)（§3.4）、[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L271-L285)（§4.3）、[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L358)（§6.1）、[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L404-L411)（§7）；对照 [monitor.py](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L542-L551)、[backtest.py](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L958-L968)、[backtest.py](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L1072-L1074)。
- **问题详情**：
  1. **K 线不足 500 根或拉取失败/缺 bar 时瀑布兜底误入 `RANGING`**：
     §4.3 第 276 行将 `4. RANGING（震荡）` 定义为“其余一切情形（默认兜底）”。当实盘 1h K 线拉取失败 / 乱序丢弃 / 不足 500 根（§3.4 第 239 行、§7 第 404/411 行），或回测窗口预热不足 500 根 / 网格缺 bar（[backtest.py](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L958-L968) 中指标为 `None`）时，`ADX/+DI/-DI/RET24` 均为 `None`。
     - 若走 §4.3 瀑布默认兜底落入 `RANGING`：① 在实盘 K 线接口故障但 ticker 正常时，已有组会被当作 `RANGING` 放行加仓，既绕过了 `STRONG_DOWN`/`SLOW_DOWN` 趋势门，又直接违反 §3.4 第 239 行与 §7 第 404/411 行“**不足 500 根 / 丢弃 K 线数据时禁止开新组，已有组只按 §5 卖出**”（只按 §5 卖出即禁止加仓）；② 会将 `上一轮状态` 污染为 `RANGING`，导致下一轮 K 线恢复时无法触发 `REVERSAL`；③ “已有组只按 §5 卖出”包含 §5.0(c) 优先级 3 的整组止盈（§3.3），当 K 线指标不可用时整组止盈系数取 `1.015` 还是 `1.008` 未定义。
  2. **每轮趋势状态判定时点与 §5.0 顺序及现有代码结构错位**：
     §4.3 第 272 行写“每轮、每币种，在 §4.1 判定之前，先用上一根已收盘 K 线的 ADX、+DI、−DI 与 RET24 判定趋势状态”。但按 §5.0 顺序，**§5.0(c) 组内退出（含优先级 3 整组止盈，需根据是否 `REBOUND` 决定 `1.008` 或 `1.015`）和 §3.2 已有组加仓均在 §4.1 之前执行（且有活跃组时根本不走 §4.1）**；而现有 [monitor.py](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L542-L551) 仅在无活跃组（`_consider_open_new_group`）时才计算指标并写入 `regime_log`。
  3. **`SLOW_DOWN` 24 小时冷却的比较符与同轮多层买入交互未定死**：
     - §4.3 第 280 行写“24 小时内最多加仓 1 层——以 layers 表该组 `layer_no >= 2` 的层的 `MAX(bought_utc)` 为准……24 小时内已有加仓买入时触发轮的层保留 PENDING”，未写死边界比较符是 `now_utc - max_bought_utc < 24 小时` 禁买（即 `>= 24 小时` 含等于解禁，与 §5.1 24h 冷静期和 §5.3 7d/14d 时间止损一致）还是 `<= 24 小时` 禁买。在 1h 整点回测中，第 24 小时步恰好等于 24 小时，含不含等于直接改变回测行为。
     - §3.2 第 225 行写“同轮内按 n 从小到大依次买齐所有触发价已被跌破的层，每买一层立即更新已部署与现金，再判断下一层”，漏写**每买一层立即更新 `bought_utc`**（ sehingga 在 `SLOW_DOWN` 状态下同轮跌破多层时，买完第 1 个待买层后 `now_utc - max_bought_utc == 0 < 24h`，同轮后续层必须立即受冷却限制保留 `PENDING`，不得同轮买齐多层）。
  4. **`REVERSAL` 的“上一轮状态”跨重启恢复与 60s 轮询语义**：
     §6.1 第 358 行（重启幂等）未写明启动时是否从 `regime_log` 恢复各币种最新一条非空 `regime` 作为“上一轮状态”；且实盘 60 秒轮询下，同一根 1h K 线在整点后第 1 分钟由 `STRONG_DOWN/SLOW_DOWN` 转为 `REVERSAL` 后，第 2 分钟因“上一轮状态”已是 `REVERSAL`（不在 `{STRONG_DOWN, SLOW_DOWN}` 中）会变为 `RANGING`。
- **修订建议**：
  1. 在 §4.3 明确：每轮在 §5.0(c) 组内退出之前，对每个币种拉取 500 根已收盘 1h K 线计算指标并判定状态；若 K 线拉取失败、被丢弃或不足 500 根，则本轮 `regime` 记 `NULL`（**保持上一轮有效状态不变，不覆盖**）、**禁止开新组且禁止已有组加仓**，已有组按默认整组止盈系数 `1.015` 正常执行 §5 退出。
  2. 在 §4.3 第 280 行写死 `SLOW_DOWN` 冷却判定式：`now_utc − MAX(bought_utc) < 24 小时`（即 `< 86400 秒`）时禁止加仓，`>= 24 小时`（含等于）时允许加仓；并在 §3.2 第 225 行补明同轮跌破多层时每买一层立即更新 `bought_utc`，故 `SLOW_DOWN` 下同轮最多只买 1 层，其余跌破层保留 `PENDING`。
  3. 在 §4.3 第 277 行与 §6.1 第 358 行注明：`上一轮状态` 取该币种上一有效指标轮的 `regime`（重启时从 `regime_log` 最新非空行恢复）。

---

#### **P1-4：§9 第 18 条模拟盘过渡风险重大遗漏——漏掉 BTC 组 1 同样已买满 6 层，且漏掉既有 OPEN 组在 SQLite `layers` 表中无 L7–L9 `PENDING` 行的结构性冲突**
- **依据位置**：[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L512)（§9 第 18 条）；对照线上数据库 `data/spot_martingale.sqlite3` 只读查询结果与 [monitor.py](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1004-L1010)。
- **线上库实测核对数据**（只读查询 `spot_martingale.sqlite3`）：
  - **BTC 组 1（`id=1`）**：`first_buy_utc = '2026-11-08T08:00:52.878242+00:00'`，`spacing_pct = 0.008`。`layers` 表共 6 行（`id=1..6`，`layer_no=1..6`）：其中 L1、L2 为 `OPEN`，L3、L4（2026-11-08 买入并止盈）及 **L5、L6（2026-11-10 15:38:30 与 16:37:00 UTC 买入并止盈）均为 `SOLD`**。按 §5.3 公式 `bought_layers = COUNT(status IN ('OPEN', 'SOLD', 'TIME_STOPPED')) = 2 + 4 = 6`。
  - **ETH 组 2（`id=2`）**：`first_buy_utc = '2026-11-08T10:00:11.374463+00:00'`，`spacing_pct = 0.01`。`layers` 表共 6 行（`id=7..12`，`layer_no=1..6`）：其中 L1–L5 为 `OPEN`，L6 为 `SOLD`，`bought_layers = 5 + 1 = 6`。
- **问题详情**：
  1. **遗漏 BTC 组 1 已满层事实**：草稿第 512 行只写“ETH 组 2（已买入 6 层）在 v5.6 下算满层、在 v5.7 下不再算满层”，沿用了 2026-11-10 上午的旧状态，漏掉了 **BTC 组 1 在 2026-11-10 16:37 UTC 也已买满全部 6 层（`bought_layers = 6`）** 的事实！在 v5.6 下 BTC 组 1 同样享受满层豁免；若切 v5.7，BTC 组 1 同样退化为未满层（`6 < 9`），且其 7 天点为 **`2026-11-15 08:00:52 UTC`**（北京时间 `16:00:52`），比 ETH 组 2 还早 2 小时面临 7 天减半（届时将卖掉其 L2 持仓并置 `half_time_stopped=1, stop_adding=1`）。
  2. **遗漏既有组在 `layers` 表无 L7–L9 `PENDING` 行的结构性死锁**：[monitor.py](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L1004-L1010) 仅在开组时插入 `1..MAX_LAYERS` 的行。线上库中组 1 与组 2 均只有 `layer_no = 1..6` 共 6 行记录，**不存在 `layer_no = 7, 8, 9` 的 `PENDING` 行**。若按选项 (a) 直接重启切 v5.7 而不迁移表数据，两组既永远无法加仓第 7–9 层（无 `PENDING` 行可触发），又因 `bought_layers = 6 < 9` 被剥夺满层豁免，在 2026-11-15 必然被执行 7 天减半！反之，若选选项 (c)“对已有组沿用 v5.6 的满层定义”，只需将满层判定写为 `bought_layers == COUNT(该组全部 layers 行)`（既有 6 层组为 6/6，新开 9 层组为 9/9），两组即自然保持满层豁免且无需补插 L7–L9。
  3. **7 天点时区未标且混用北京时间**：ETH 组 2 首买时间为 `2026-11-08 10:00:11 UTC`，7 天点实为 **`2026-11-15 10:00:11 UTC`**（北京时间 `18:00:11`）。草稿第 504、512 行写 `2026-11-15 18:00` 未标时区，与 SPEC 全文 UTC 口径不一致。
- **修订建议**：
  在 §9 第 18 条补齐 BTC 组 1（`bought_layers = 6`，2 OPEN + 4 SOLD，7 天点 `2026-11-15 08:00:52 UTC`）与 ETH 组 2（`bought_layers = 6`，5 OPEN + 1 SOLD，7 天点 `2026-11-15 10:00:11 UTC` / 北京时间 `18:00:11`）的完整实盘状态，并披露既有组在 `layers` 表中仅有 6 行记录（无 L7–L9 `PENDING` 行）的事实及选项 (a)/(c) 对应的具体实现规则。

---

### 3. P2 级非阻塞问题与改进建议（6 项）

- **P2-1（§5.3 第 328 行边界效应与动态倍数对 `BTC d=2.0%` 的相互作用表述不准确）**：
  - **依据**：[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L328) 写：“只有 BTC d=2.0%（L9 0.84C，高于 0.80C）时 9 层触发价全部高于停加仓线、可买满。动态倍数 1.4/1.5 档提前用满上限时同样阻止满层（情形 ②）。”
  - **复算事实**：
    1. 单看触发价 $> 0.80C$（$1 - 8d > 0.80 \iff d < 2.5\%$），任何 **$d \in [0.8\%, 2.5\%)$ 的 BTC 组**与 **$d \in [1.0\%, 2.5\%)$ 的 ETH 组**，其 9 层触发价均严格高于 $0.80C$（第 328 行写“只有 BTC d=2.0%”仅是针对前文所列离散示例 $d \in \{2.0\%, 2.5\%, 3.0\%, 4.0\%\}$ 而言，易被误读为只有 $d=2.0\%$ 满足）。
    2. 更重要的是：一旦代入 V5.7-3 动态倍数（在无中间层止盈卖出的单边下跌中），**`BTC d=2.0%` 本身恰恰买不满 9 层**！因为 $d=2.0\%$ 时 L2 触发条件为 $\text{last} < 0.98C \implies \text{DD} > 2.0\%$（落入 $1.3\times$ 档而非 $1.2\times$ 档），L2..L8 的分档倍数依次为 `[1.3, 1.3, 1.4, 1.4, 1.5, 1.5, 1.5]`，前 8 层计划名义之和达 **$3,117.72\text{ USDT} > 3,000.00\text{ USDT}$**（ETH $d=2.0\%$ 同理为 $2,078.28 > 2,000.00$），在第 8 层即被截断为 $929.92\text{ USDT}$ 用满全部上限，第 9 层因剩余额度为 $0 < 5\text{ USDT}$ 被作废，组停在 **8 层**（`bought_layers = 8 < 9`）！单边下跌中真正能买满 9 层的间距范围实为 **$d < 2.0\%$**（BTC $[0.8\%, 2.0\%)$、ETH $[1.0\%, 2.0\%)$，前 8 层计划名义和 $\le 2,749.07\text{ USDT}$ / $1,832.46\text{ USDT}$）。同时，这意味着 $d \in [2.0\%, 2.5\%)$ 的组即便已 100% 部署满 $3,000 / 2,000\text{ USDT}$ 上限，仍因 `bought_layers = 8 < 9` 不享受 holdmax 豁免、会在 7d/14d 被时间止损——建议在 §5.3 第 328 行把这一精确边界（触发价边界 $d < 2.5\%$、单边下跌动态倍数不触顶边界 $d < 2.0\%$）写清。
- **P2-2（§6.1 第 355 行 `regime_log` 新增四列在既有 SQLite 库上的迁移机制未说明）**：
  - **依据**：[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L355)；对照 [engine.py](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L127-L137)、[monitor.py](file:///home/shenb/myQ/projects/spot-martingale/monitor.py#L140-L156)。
  - **说明**：`regime_log` 新增 `regime, ret24_pct, plus_di, minus_di` 四列（可空，历史行记 NULL）。现有 `engine.init_db` 仅执行 `CREATE TABLE IF NOT EXISTS`，对已存在的 `data/spot_martingale.sqlite3` 不会自动加列，直接 `INSERT` 新列会触发 `sqlite3.OperationalError: table regime_log has no column named regime` 导致进程退出。建议在 §6.1 补一句：启动建库/校验时对既有库 `regime_log` 检查并执行幂等 `ALTER TABLE regime_log ADD COLUMN ...`。
- **P2-3（§9 第 7 条残留旧停加仓线 `−15%`）**：
  - **依据**：[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L501)。
  - **说明**：§4.2 第 258–259 行已将停加仓线改为 `−20%`（`< C × 0.80`），但 §9 第 7 条（第 501 行）仍写“停加仓只靠 −15% 价格规则与 15% halt”，建议同步改为 `−20%`。
- **P2-4（§5.0(c) 第 298 行退出优先级列表仍写死 `+1.5%`）**：
  - **依据**：[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L298)。
  - **说明**：第 298 行写 `3. §3.3 整组止盈 +1.5%`，虽在第 302 行注明了 `（REBOUND 的整组止盈系数除外，见 §3.3）`，建议第 298 行同步写为 `3. §3.3 整组止盈（平时 +1.5%，REBOUND 时 +0.8%）`。
- **P2-5（§1.1 第 159 行输入表与 §7.1 冻结值表新增常量名对齐）**：
  - **依据**：[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L159)、[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L420-L443)。
  - **说明**：§1.1 第 159 行指标栏仅列 `ATR(14)、ADX(14)`，建议补入 `+DI(14)、−DI(14)、RET24`；§7.1 冻结值表中新增的动态倍数分档、趋势状态阈值、REBOUND 止盈系数（`1.008`）、SLOW_DOWN 冷却时间（`24h`）建议给出明确的 `config.py` 常量名（如同 `HOLDMAX_EXEMPT_TIME_STOP`），便于启动时严格等于校验与单测引用。
- **P2-6（§9 第 15 条补入“开组后不重算网格”的交叉引用）**：
  - **依据**：[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L285)、[spec_v57_draft.md](file:///home/shenb/myQ/projects/spot-martingale/spec_v57_draft.md#L509)。
  - **说明**：§4.3 末段（第 285 行）已清楚披露拒绝在趋势反转时重算已有组网格区间的原因，建议在 §9 第 15 条（决策项 D-4）中也显式点出“且开组后永不重算已有组网格区间（见 §4.3 末段）”，方便 moka 冻结裁决时一并确认。

---

## 二、评审范围 11 项逐项核验记录

### 1. 八处改动（V5.7-1 ～ V5.7-8）与 SPEC 其余部分的一致性核查
- **V5.7-1（层数 6→9）**：§2（第 197–202 行）、§3.2（第 218–220 行）、§5.3（第 327 行）、§5.4（第 334 行）、§7.1（第 419、440 行）、§8.2（第 470–473 行）、§8.3(6)（第 487 行）已全部同步为 9 层，正文无残留的 6 层上限。
- **V5.7-2（趋势状态分类与加仓门）& V5.7-3（动态加仓倍数）**：与 §1.3 最小步长取整、§3.2 同轮多层买入、§5.3 满 7 天减半、§7 K 线不足 500 根规则存在 **P1-1、P1-2、P1-3** 三处交互缺口（见上文 §一-2）。另发现：V5.7-3 中 `10% ≤ DD ≤ 20% → 1.5（仅当趋势检查通过，即当前不处于 STRONG_DOWN）` 与 §4.3 全局规则（`STRONG_DOWN` 在所有 DD 分档下均禁止加仓）逻辑一致但完全同义冗余（非冲突）。
- **V5.7-4（停加仓线 −15%→−20%）**：§4.2（第 258–259 行）、§5.3（第 328 行）、§7.1（第 431 行）、§8.3(6)（第 487 行）、§9 第 12 条（第 506 行）已更新为 `−20%` / `0.80C`；仅 §9 第 7 条（第 501 行）残留一处 `−15%`（**P2-3**）。
- **V5.7-5（间距与预留不变）**：§2 与 §3.2 保持 BTC `3,000` / ETH `2,000` / 合计 `5,000 USDT` 与 ATR 间距（BTC `2.0× [0.8%, 3.0%]`、ETH `2.5× [1.0%, 4.0%]`），并在 §9 第 13、14 条（D-2、D-3）诚实列出与 moka 文字的差异供裁决。 Note: 因 `3,000 + 2,000 == 5,000` 且单币最多 1 个活跃组，数学上恒有 `合计剩余额度 >= 单币剩余额度`。
- **V5.7-6（holdmax 满层定义 6→9）**：§5.3 与 §7.1、§8.3(6) 已同步为 `bought_layers == 9`；边界分析与线上过渡存在 **P2-1** 与 **P1-4**。
- **V5.7-7（指标扩展）& V5.7-8（压力测试与验收）**：指标、表结构、ST2 重算与 §8.3 验收标准均已对应更新（迁移细节见 **P2-2**）。

---

### 2. 独立数学复算结果（全部独立编程复算，逐项比对）

#### (1) 级数和 $S$ 与九层均匀路径计划名义（half-up 四舍五入，第 9 层残差法）
- **级数和 $S$**：
  $$S = \sum_{k=0}^{8} 1.3^k = 1 + 1.3 + 1.69 + 2.197 + 2.8561 + 3.71293 + 4.826809 + 6.2748517 + 8.15730721 = \mathbf{32.01499791}$$
  精确有限小数（8 位小数），与草稿第 125、199、421 行完全一致。
- **BTC 各层名义（`cap = 3000`）**：

| 层号 $n$ | $1.3^{n-1}$ | 精确值 $3000 \times 1.3^{n-1} / S$ | Half-Up 舍入值 | 草稿值 | 核对 |
|---|---|---|---|---|---|
| L1 | $1.00000000$ | $93.70608140\dots$ | **93.71** | 93.71 | 一致 |
| L2 | $1.30000000$ | $121.81790582\dots$ | **121.82** | 121.82 | 一致 |
| L3 | $1.69000000$ | $158.36327757\dots$ | **158.36** | 158.36 | 一致 |
| L4 | $2.19700000$ | $205.87226084\dots$ | **205.87** | 205.87 | 一致 |
| L5 | $2.85610000$ | $267.63393909\dots$ | **267.63** | 267.63 | 一致 |
| L6 | $3.71293000$ | $347.92412082\dots$ | **347.92** | 347.92 | 一致 |
| L7 | $4.82680900$ | $452.30135706\dots$ | **452.30** | 452.30 | 一致 |
| L8 | $6.27485170$ | $587.99176418\dots$ | **587.99** | 587.99 | 一致 |
| L9（残差） | $8.15730721$ | 公式直算 $764.389293\dots \to 764.39$；残差 $3000 - 2235.60 =$ **764.40** | **764.40** | 764.40 | 一致（合计 **3,000.00**） |

- **ETH 各层名义（`cap = 2000`）**：

| 层号 $n$ | 精确值 $2000 \times 1.3^{n-1} / S$ | Half-Up 舍入值 | 草稿值 | 核对 |
|---|---|---|---|---|
| L1 | $62.47072093\dots$ | **62.47** | 62.47 | 一致 |
| L2 | $81.21193721\dots$ | **81.21** | 81.21 | 一致 |
| L3 | $105.57551838\dots$ | **105.58** | 105.58 | 一致 |
| L4 | $137.24817389\dots$ | **137.25** | 137.25 | 一致 |
| L5 | $178.42262606\dots$ | **178.42** | 178.42 | 一致 |
| L6 | $231.94941388\dots$ | **231.95** | 231.95 | 一致 |
| L7 | $301.53423804\dots$ | **301.53** | 301.53 | 一致 |
| L8 | $391.99450946\dots$ | **391.99** | 391.99 | 一致 |
| L9（残差） | 公式直算 $509.592862\dots \to 509.59$；残差 $2000 - 1490.40 =$ **509.60** | **509.60** | 509.60 | 一致（合计 **2,000.00**） |

#### (2) 压力测试 2（合成 −50%，确定性持仓测试）预期数独立复算
- **触发价序列**：
  - BTC（$d = 2.0\%$）：$1.00C, 0.98C, 0.96C, 0.94C, 0.92C, 0.90C, 0.88C, 0.86C, 0.84C$
  - ETH（$d = 2.5\%$）：$1.00C, 0.975C, 0.95C, 0.925C, 0.90C, 0.875C, 0.85C, 0.825C, 0.80C$
- **持仓在价格为 $1.00C$ 时的基准市值 $V(1.0C) = \sum_{n=1}^9 \frac{\text{notional}_n}{1 - (n-1)d}$**：
  - $V_{\text{BTC}}(1.0C) = 3,387.151621\text{ USDT}$
  - $V_{\text{ETH}}(1.0C) = 2,335.488257\text{ USDT}$
  - $V_{\text{Dual}}(1.0C) = 5,722.639878\text{ USDT}$
- **持仓至 $0.50C$ 不卖的亏损复算**：
  - **BTC 单独**：$0.50C$ 市值 $= 1,693.575810\text{ USDT}$；仓位浮亏 $= 3,000.00 - 1,693.575810 = 1,306.424190 \to \mathbf{1,306.42\text{ USDT}}$；加买入成本 $3.60\text{ USDT}$ 后总亏损 $= 1,310.024190 \to \mathbf{1,310.02\text{ USDT}}$（对应最大回撤 $\mathbf{13.10\%}$）。与草稿完全一致。
  - **ETH 单独**：$0.50C$ 市值 $= 1,167.744129\text{ USDT}$；仓位浮亏 $= 2,000.00 - 1,167.744129 = 832.255871 \to \mathbf{832.26\text{ USDT}}$；加买入成本 $2.40\text{ USDT}$ 后总亏损 $= 834.655871 \to \mathbf{834.66\text{ USDT}}$（对应最大回撤 $\mathbf{8.35\%}$）。与草稿完全一致。
  - **双币合计**：总亏损 $= 1,310.024190 + 834.655871 = 2,144.680061 \to \mathbf{2,144.68\text{ USDT}}$（其中仓位浮亏 $\mathbf{2,138.68\text{ USDT}} + \text{买入成本 } 6.00\text{ USDT}$）。与草稿完全一致。
- **双币情形特定价格点权益、回撤与 15% halt 触发点复算**（起始现金 $= 4,994.00\text{ USDT}$，初始峰值 $= 10,000.00\text{ USDT}$）：
  - **$0.70C$ 时**：$\text{equity} = 4,994.00 + 5,722.639878 \times 0.70 = 8,999.8479 \to \mathbf{8,999.85\text{ USDT}}$，回撤 $= (10,000 - 8,999.8479)/10,000 = 10.0015\% \to \mathbf{10.00\%}$。与草稿完全一致。
  - **$0.65C$ 时**：$\text{equity} = 4,994.00 + 5,722.639878 \times 0.65 = 8,713.7159 \to \mathbf{8,713.72\text{ USDT}}$，回撤 $= (10,000 - 8,713.7159)/10,000 = 12.8628\% \to \mathbf{12.86\%}$。与草稿完全一致。
  - **15% halt 触发点（$\text{equity} \le 8,500.00\text{ USDT}$）**：
    - 连续解析解：$r_{\text{halt}} = (8,500.00 - 4,994.00) / 5,722.639878 = 0.612654C \approx \mathbf{0.613C}$。
    - 离散小时步（$r = 0.5^{1/720}$）：第 $508$ 小时 $r^{508} = 0.613206C$，权益 $8,503.16\text{ USDT}$（回撤 $14.9684\% < 15\%$）；**第 $509$ 小时** $r^{509} = 0.612616C \approx \mathbf{0.613C}$，平仓前权益 $8,499.78\text{ USDT}$（回撤 $15.0022\% \ge 15\%$），触发 halt！平仓扣除 $0.12\%$ 卖出成本（$4.20\text{ USDT}$）后落地权益为 $8,495.58\text{ USDT}$，强平落地最大回撤为 $\mathbf{15.04\%} \le 16.00\%$。与草稿完全一致。

#### (3) 边界表复算（各钳制间距下触发价与 `0.80C` 的关系）
- **触发价恰等于 `0.80C` 的层永不买入**：因加仓触发条件为 `last < trigger_price`（严格小于），当 `trigger_price == 0.80C` 时，触发加仓要求 `last < 0.80C`，而 §4.2 停加仓条件同样为 `last < 0.80C` 且在加仓循环前先执行（置 `stop_adding=1` 并取消全部 `PENDING` 层），故触发价 $\le 0.80C$ 的层永不买入，逻辑完全严密。
- **各间距下触发价与动态倍数上限复算表**（无中间层止盈卖出的单边下跌）：

| 币种与间距 $d$ | L6 触发价 | L7 触发价 | L8 触发价 | L9 触发价 | 仅看触发价 $>0.80C$ 可买层数 | 叠加动态倍数后单边下跌实际可买层数 |
|---|---|---|---|---|---|---|
| BTC $d=0.8\%$（下限） | $0.960C$ | $0.952C$ | $0.944C$ | $0.936C$ | 9 层 | **9 层**（累计部署 $2,721.42 < 3,000$） |
| BTC $d \in [1.0\%, 1.95\%]$ | $>0.80C$ | $>0.80C$ | $>0.80C$ | $>0.80C$ | 9 层 | **9 层**（L1..L8 合计 $\le 2,749.07$，L9 残差买入满 $3,000$） |
| **BTC $d=2.0\%$** | $0.900C$ | $0.880C$ | $0.860C$ | $0.840C$ | 9 层 | **8 层**（L1..L8 计划和 $3,117.72 > 3,000$，L8 截断用满上限，L9 作废，见 **P2-1**） |
| BTC $d=2.5\%$ | $0.875C$ | $0.850C$ | $0.825C$ | $0.800C$（永不买） | 8 层 | **8 层**（L8 截断用满上限 + L9 触发价 $=0.80C$） |
| BTC $d=3.0\%$（上限） | $0.850C$ | $0.820C$ | $0.790C$（$<0.80C$） | $0.760C$（$<0.80C$） | **7 层** | **7 层** |
| ETH $d \in [1.0\%, 1.95\%]$ | $>0.80C$ | $>0.80C$ | $>0.80C$ | $>0.80C$ | 9 层 | **9 层**（L1..L8 合计 $\le 1,832.46$，L9 残差买入满 $2,000$） |
| ETH $d=2.5\%$ | $0.875C$ | $0.850C$ | $0.825C$ | $0.800C$（永不买） | **8 层** | **8 层**（L8 已截断用满 $2,000$ 上限） |
| ETH $d=3.0\%$ | $0.850C$ | $0.820C$ | $0.790C$（$<0.80C$） | $0.760C$（$<0.80C$） | **7 层** | **7 层** |
| ETH $d=4.0\%$（上限） | $0.800C$（永不买） | $0.760C$（$<0.80C$） | $0.720C$ | $0.680C$ | **5 层** | **5 层** |

---

### 3. 趋势状态瀑布穷尽性、边界值与 REVERSAL 自洽性
- **有限实数域上的穷尽性与互斥性**：当 `(ADX, +DI, -DI, RET24)` 均为有限实数时，按序瀑布 1→2→3→4 对状态空间形成严格完备且互斥的划分：
  - `ADX = 25`：严格落入 `ADX >= 25` 分支（不满足 `ADX < 25` 的 `REBOUND`）；
  - `RET24 = -5%`：在 `ADX >= 25` 且 `-DI > +DI` 时严格落入 `1. STRONG_DOWN`；
  - `RET24 = +3%`：在 `ADX < 25` 时严格落入 `3. REBOUND`；
  - `+DI = -DI`：既不满足 `-DI > +DI`（不入 1、2），也不满足 `+DI > -DI`（不触发 `REVERSAL`），无歧义。
- **边界缺口**：当指标为 `None`（K 线不足 500 根或拉取失败/缺 bar）时的行为未从瀑布第 4 条默认兜底中剥离，见 **P1-3(1)**。

---

### 4. 动态倍数递归、截断、同轮多层买入与 holdmax 一致性
- **同轮多层共用同一 DD 分档**：§3.2 第 221、225 行规定 DD 只依赖触发轮的单一估值价 `last` 与中心价 `C`（`DD = (C − last) / C`），同轮内估值价固定不变，故同轮跌破多层共用同一 DD 分档倍数在数学与撮合语义上完全合理。
- **递归与截断缺口**：存在 **P1-1**（截断后经最小步长向下取整 `< 5 USDT` 时的无限重试漏洞，及 §2 漏写取消其余 `PENDING` 层）、**P1-2**（§5.3 7 天减半“初始层名义”口径未随动态名义更新）以及 **P1-3(3)**（`SLOW_DOWN` 下同轮跌破多层须每买一层更新 `bought_utc` 以限制同轮最多买 1 层）。

---

### 5. 停加仓线移至 −20% 的安全含义与未披露风险
- **与 §5.1 的 15% halt 关系**：因单币/双币部署上限（`3,000 / 2,000 / 5,000 USDT`）未变，将层数扩至 9 层、停加仓线移至 `0.80C` 实质上是将同等上限摊入更深的价格区间，降低了满仓加权平均成本（例如在 `0.80C` 时双币满仓浮亏仅约 `496 ~ 764 USDT`，即初始权益的 `5.0% ~ 7.6%`，距 `15%` 账户级 halt 尚有充足缓冲）。
- **未充分披露的交互风险（见 P2-1）**：在单边下跌中，凡间距 $d \in [2.0\%, 2.5\%)$ 的组（含 `BTC d=2.0%`），虽然 9 层触发价全部高于 `0.80C`，但动态倍数 `1.4× / 1.5×` 会使第 8 层就用满 100% 的单币上限（`3,000 / 2,000 USDT`），导致第 9 层被上限截断作废、组最终停在 `bought_layers = 8 < 9`——这意味着**即便该组已 100% 满额部署了全部资金，仍不被算作“满层（9/9）”，在满 7 天和满 14 天时仍会被强制执行时间止损割肉**。

---

### 6. RET24 定义与回测预热期核查
- **下标口径**：在已收盘 K 线序列 `closes` 中，`closes[-1]` 为上一根已收盘 K 线，往前推 24 根（即 24 小时前）恰为 `closes[-1 - 24] = closes[-25]`（在 [backtest.py](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L981) 0-based 下标 `i` 中对应 `closes[i] / closes[i - 24] - 1`），下标口径完全正确。
- **500 根门槛下恒可算**：`closes[-25]` 仅需序列长度 $\ge 25$ 且 `close > 0`（已由 V5.4-2(a) 保证），在 500 根已收盘 K 线门槛下恒可计算。
- **数据实测与缺口**：实测本地 `BTCUSDT_1h.csv` 与 `ETHUSDT_1h.csv` 首根 K 线为 `2020-12-10 00:00 UTC`，在训练集起点 `2021-01-01 00:00 UTC` 前有 523 根有效预热 bar（$> 500$）。但在一般规则定义上，当有效 K 线 `< 500` 根或评估网格缺 bar 时指标返回 `None`，需按 **P1-3(1)** 补齐趋势状态在指标为 `None` 时的禁加仓与不覆盖状态规则。

---

### 7. `+DI/−DI` 暴露断言逐行代码核验
- **[engine.py](file:///home/shenb/myQ/projects/spot-martingale/engine.py#L260-L274)**：第 267–268 行在 `for i in range(14, n):` 循环内已逐根算出 Wilder 平滑后的 `plus_di = 100.0 * (smooth_plus_dm[i] / tr_val)` 与 `minus_di = 100.0 * (smooth_minus_dm[i] / tr_val)`（当 `tr_val == 0` 时第 265 行 `dx[i] = 0.0; continue`，暴露时将 `plus_di/minus_di` 置 `0.0` 即可），循环结束时 `i = n - 1` 恰与 `current_adx = adx_arr[-1]`（第 298 行）同序列、同根 K 线。
- **[backtest.py](file:///home/shenb/myQ/projects/spot-martingale/backtest.py#L939-L953)**：第 945–946 行在 `for i in range(14, n):` 循环内同样已逐根算出 `p_di = 100.0 * (smooth_pdm[i] / tr_val)` 与 `m_di = 100.0 * (smooth_mdm[i] / tr_val)`，只需存入长度为 `n` 的数组并在第 981 行随 `adx_arr[i]` 一并放入 `res[i]` 字典，即与 ADX 严格同序列、同根 K 线。
- **核验结论**：草稿 V5.7-7 与 §3.4 的断言 **100% 属实**。

---

### 8. `REVERSAL` 无独立动作与“开组后永不重算网格”的披露核查
- 草稿在 V5.7-2（第 126 行）、§4.3（第 277、285 行）及 §9 第 15 条（第 509 行）已诚实说明：moka 原文中的“趋势反转重新计算网格区间”只适用于未来新开组，已有组重算网格等于篡改历史成本故予禁止，`REVERSAL` 仅作日志过渡标签、无独立交易动作，交由盲审与 moka 裁决。披露诚实（仅建议按 **P2-6** 在 §9 第 15 条加半句交叉引用）。

---

### 9. 证据诚实性核查
- 草稿在第 3、124、132、476、482、487、511 行均明确声明：**尚无任何 v5.7 回测证据**，压力测试 2 数字为解析复算值（待第 ⑧ 步实测核对），且 **§8.3(1) 的 v5.6 豁免不延及 v5.7**。全文无任何暗示已有回测证据或默认通过的表述。

---

### 10. §9 第 18 条模拟盘过渡项核查
- 存在重大事实遗漏与结构性缺口（见 **P1-4**）：只读核查线上 SQLite 库证实，**BTC 组 1 与 ETH 组 2 当前均已买满 6 层（`bought_layers == 6`）**，且两组在 `layers` 表中均只有 6 行记录（无 L7–L9 `PENDING` 行）；BTC 组 1 的 7 天点为 `2026-11-15 08:00:52 UTC`，ETH 组 2 的 7 天点为 `2026-11-15 10:00:11 UTC`（北京时间 `18:00:11`）。§9 第 18 条漏掉了 BTC 组 1、漏掉了 `layers` 表无 L7–L9 `PENDING` 行的结构冲突，且未标明 `18:00` 的时区。

---

### 11. 其他逻辑缺口或歧义
- 均已归入上文 **P1-1 ～ P1-4** 及 **P2-1 ～ P2-6**。

---

## 三、进入冻结评审前的必修清单（供第 2 轮修订对照）

1. **修 P1-1（§2 第 203 行、§3.2 第 227 行）**：写死“计划名义 → 剩余额度截断 → §1.3 最小步长向下取整”的三步顺序；明确凡因剩余额度截断导致截断后计划名义 `< 5.00 USDT` **或取整后实际成交名义 `< 5.00 USDT`（含 `qty == 0`）**，均将该层与组内其余全部 `PENDING` 层一并置 `CANCELLED`、组置 `stop_adding = 1`、只记一次 ERROR，不无限重试。
2. **修 P1-2（§5.3 第 325 行）**：明确 v5.7 下 7 天减半的“初始层名义”取各 OPEN 层的实际买入名义 `layers.notional_usdt`（即 `qty × buy_price`）。
3. **修 P1-3（§4.3、§3.2 第 225 行、§6.1 第 358 行、§7 第 404/411 行）**：
   - 明确每轮在 §5.0(c) 组内退出前对双币种计算指标并判定 §4.3 趋势状态、写入 `regime_log`；
   - 明确当 K 线不足 500 根、拉取失败或回测网格缺 bar（指标为 `None`）时，不进入 §4.3 瀑布兜底（`regime` 记 `NULL`，保留上一有效轮状态不覆盖），该币种本轮禁止开新组且禁止已有组加仓，已有组按默认整组止盈系数 `1.015` 执行 §5 退出；
   - 明确 `SLOW_DOWN` 24 小时冷却判定为 `now_utc − MAX(bought_utc) < 24 小时` 时禁买、`>= 24 小时`（含等于）时可买，且同轮跌破多层时每买一层立即更新 `bought_utc`（故 `SLOW_DOWN` 下同轮最多买 1 层）；
   - 明确 `REVERSAL` 的“上一轮状态”在进程重启时从 `regime_log` 该币种最新一条非空 `regime` 恢复。
4. **修 P1-4（§9 第 18 条）**：补入 BTC 组 1 同样已买满 6 层（2 OPEN + 4 SOLD，7 天点 `2026-11-15 08:00:52 UTC`）的事实，将 ETH 组 2 的 7 天点写明为 `2026-11-15 10:00:11 UTC`（北京时间 `18:00:11`），并披露线上库既有组仅有 `layer_no = 1..6` 行（无 L7–L9 `PENDING` 行）在选项 (a)/(c) 下的处理规则。
5. **顺修 P2-1 ～ P2-6**：修正 §5.3 第 328 行 `BTC d=2.0%` 在动态倍数下 L8 即用满上限停在 8 层的边界说明、补充 §6.1 `regime_log` 启动 `ALTER TABLE` 幂等迁移、修正 §9 第 7 条 `−15%` 残留与 §5.0(c) `+1.5%` 表述等。
