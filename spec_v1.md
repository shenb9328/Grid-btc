# SPEC v1 — BTC/ETH 现货有限层数网格马丁（Binance Spot，模拟）
# 项目：/home/shenb/myQ/projects/spot-martingale/
# 状态：草稿 v1，未冻结。第 2 轮（v0 共 FAIL，见 results/merged_review_v0.md）。
# 日期：2026-11-06
# 变更来源：moka 决定（2026-11-06 11:57）冷静期改为 1 天；其余按 agy + Codex 盲审的 12 项 MUST-FIX 修改。
# v0 → v1 变更日志（每条对应 merged_review_v0.md 的编号）：
#  M1 权益公式：现金 + 持仓市值，删 −未实现亏损；定义估值价、入账口径、峰值生命周期与 halt 恢复重置。
#  M2 验收 §8.3 第 5 条重写：去掉“halt 必须触发且最大回撤 ≤15%”的不可能要求，改为容差 16.00%，并写清压力测试是单币还是组合。
#  M3 S 更正为 12.75603；层名义唯一公式 round(cap×1.3^(n−1)/S, 2)，第 6 层用残差法使合计严格等于上限。
#  M4 成本模型二选一已定：直接取 last 价，手续费 0.10% + 滑点 0.02% 单独扣现金，单边 0.12%，不做价格偏移。
#  M5 网格状态机重写：开组即买第 1 层、无空组；OPEN 层为 0 立即关组；中间层卖出后下一层仍可触发（以“曾买入”为准）；同轮跌破多层依次买齐；整组止盈优先于单层止盈。
#  M6 §4.1.2 重写：前 480 根已收盘 1h K 线（不含当前根），上一根收盘价严格在区间内；ADX 用 Wilder，需 ≥500 根已收盘 K 线。
#  M7 现金储备冲突解决：已部署 ≤ min(5000, 权益×50%)，按实际成交名义 + 手续费计量。
#  M8 800 USDT 死条款删除，改为单币浮亏 > 已部署成本 12% 时停加仓。
#  M9 时间止损加 half_time_stopped 标志，只执行一次；算法定死为整层从高往低卖。
#  M10 回测撮合定死为 1h 收盘价 close-only；压力切片给精确定义（合成 + 真实日期）；利润因子 / 盈利月占比口径定死。
#  M11 退出优先级全局规则 + 状态机（HALTED vs CLOSED）+ DB 字段补齐。
#  M12 冷静期：7 天 → 1 天（24 小时），moka 2026-11-06 11:57 定；恢复时重置峰值权益，且须重新通过 §4.1 才能开新组。
# 另修：15m K 线删除；DRY_RUN 配置唯一来源；watchdog 明确为无；“测试集只跑一次”适用范围；看板 API JSON 契约；时间基准与各阈值边界比较符。

## 0. 目标与非目标

### 0.1 目标
在 BTCUSDT、ETHUSDT 现货上，用有限层数网格马丁做“经常小赚”，核心是活得久。
先证明可回测、有统计优势，再考虑模拟盘长时间跑。v1 阶段不碰实盘、不接实盘 API。

### 0.2 非目标（v1 明确不做）
- 不做合约、永续、杠杆、做空、资金费率套利。
- 不做黄金、SOL、Meme 币。
- 不做新闻抢单（1-10 秒 News Scalping）。新闻只做风控过滤（见 §4）。
- 不做自动实盘下单。v1 只有 DRY_RUN 模拟撮合。
- 不推 GitHub。线上代码只读。

### 0.3 成功标准
回测通过才算有资格进模拟盘；模拟盘通过才谈别的。标准见 §8。

## 1. 输入与输出

### 1.1 输入
| 输入 | 来源 | 频率 / 格式 |
|---|---|---|
| BTCUSDT、ETHUSDT 现货 last 价 | Binance Spot 公共行情 https://data-api.binance.vision ，GET /api/v3/ticker/price。无 API Key、无 Secret。**注意：该接口只返回 last 价，不含 bid/ask；v1 不用 bid/ask，不用 bookTicker。** | 轮询每 60 秒 |
| 1h K 线（已收盘） | 同上，GET /api/v3/klines（interval=1h）。每次拉取最近 500 根已收盘 K 线（不含当前未收盘的那根）。 | 每轮拉取一次，缓存本地 |
| ATR(14)、ADX(14) | 由 1h 已收盘 K 线本地计算，定义见 §3.4 | 每轮计算一次 |
| 经济事件日历 | 本地文件 `data/events.csv`，字段：utc_time, event, country, impact。初始覆盖 CPI、非农、FOMC 决议（2020-01-01 至 2026-12-31，UTC）。impact 取值只能是 HIGH / MEDIUM / LOW；“高影响”指 impact=HIGH。 | 启动时加载，每轮检查 stale（见 §7） |
| 配置 | `config.py`，全部阈值见 §2-§6。**配置唯一来源就是 config.py，不读环境变量，不读其他文件。EXECUTION_MODE 是 config.py 里的常量 "DRY_RUN"，不允许改成其他值，代码中不得存在 LIVE 下单路径。**启动时把全部配置与 EXECUTION_MODE 记入日志。 | 启动时一次性读入 |

### 1.2 输出
| 输出 | 位置 / 格式 |
|---|---|
| 模拟账户状态 | SQLite `data/spot_martingale.sqlite3`，表见 §6.1 |
| 每笔模拟成交 | 同库 `trades` 表：utc（ISO8601，UTC）、symbol、side（BUY/SELL）、price、qty、notional_usdt、fee_usdt、slippage_usdt、group_id、layer_no、reason |
| 每日汇总 | 同库 `daily_summary` 表 + 日志 `logs/spot_YYYYMMDD.log`（日期用 UTC 日期） |
| 看板数据 | 只读 HTTP API（见 §6.3），由 web_dashboard.py 读取本库，不写本库 |

### 1.3 模拟撮合规则（必须写死，二选一已定：取 last 价方案）
- 买入价 = 当时 last 价；卖出价 = 当时 last 价。**不做 last×(1±0.0002) 的价格偏移。**
- 手续费 = 成交名义 × 0.0010（0.10%，Binance 现货 VIP0 taker 口径）。
- 滑点 = 成交名义 × 0.0002（0.02%），**作为单独的现金扣款，不调整成交价**。
- 单边总成本 = 0.10% + 0.02% = 0.12%。往返成本在买卖名义相同时为 0.24%；若卖出名义比买入名义高（例如整组 +1.5% 止盈），往返成本占买入名义的比例为 0.12% + 0.12%×1.015 = 0.2418%，以实际现金流水为准，不许在报告里写成 0.24%。
- 最小下单：BTC 0.001 BTC、ETH 0.01 ETH、单笔名义 ≥ 5 USDT（用 `>= 5.00` 判断，恰好 5.00 可以下单）。低于此值的层不下单，记 WARNING；**若第 1 层低于此值，则不开组（见 §3.1），不产生空组。**
- 数量 = 层名义 / 成交价，再按最小步长向下取整。取整后名义 < 5 USDT 的层跳过，不把金额累到下一层。

### 1.4 会计口径（v1 新增，全部写死）
- 现金 cash_usdt：买入时扣 成交名义 + 手续费 + 滑点；卖出时加 成交名义 − 手续费 − 滑点。
- 持仓成本 basis：某层成本 = qty × buy_price（**不含**手续费与滑点，手续费只走现金）。
- 持仓市值 market_value = Σ(OPEN 层 qty × 当前 last 价)。
- **账户权益 equity = cash_usdt + market_value。**（v0 的“− 未实现亏损”是错的，已删除。）
- 未实现盈亏 unrealized_pnl = Σ(OPEN 层 qty × (last 价 − buy_price))。
- 已实现盈亏 realized_pnl：卖出时记 (sell_price − buy_price) × qty − 该笔卖出手续费 − 该笔卖出滑点，累计进 account 表（不重复计买入手续费，买入手续费已在买入时扣过现金）。
- 估值价：权益、回撤、日亏损一律用 last 价，不用 bid/ask、不用 K 线收盘价。

## 2. 资金与仓位（数字全部冻结在 v1）

- 模拟初始本金：10,000 USDT（与 perp-test 的 SIMULATION_INITIAL_BALANCE 一致，便于对照）。
- BTC 网格总上限：3,000 USDT（本金的 30%）。
- ETH 网格总上限：2,000 USDT（本金的 20%）。
- 两币合计已部署上限：**任何时候，总已部署成本 ≤ min(5,000 USDT, 当前账户权益 × 50%)。**其中“总已部署成本”= 全部 OPEN 层的 basis 之和 + 这些层买入时已付的手续费与滑点之和。现金储备不再写成独立的“≥5,000 不得动用”，v0 那个写法在任何亏损后都不可能成立，已删除。
- 单币种同时只能有一个活跃网格组（grid_group）。一个组没关闭，不开第二个组。BTC 与 ETH 的组相互独立，但合计上限共同约束。
- 层数：每组最多 6 层。买满 6 层后禁止再加仓。
- 加仓倍数：1.3（固定，不是 2.0）。
- 层名义唯一公式：`layer_notional(n) = round(cap_usdt × 1.3^(n−1) / S, 2)`，其中 S = 1 + 1.3 + 1.3^2 + 1.3^3 + 1.3^4 + 1.3^5 = **12.75603**（v0 写成 12.756115 是错的）。
  - 第 6 层用残差法：`layer_notional(6) = cap_usdt − Σ(layer_notional(1..5))`，使 6 层合计**严格等于**上限，不许出现 2000.03 这种超上限。
  - BTC 各层（cap=3000）：235.18、305.74、397.46、516.70、671.71、873.21，合计 3,000.00。
  - ETH 各层（cap=2000）：156.79、203.83、264.97、344.46、447.80、582.15，合计 2,000.00。
  - **上限校验用实际成交名义 + 手续费 + 滑点，不用计划名义。**买入前必须校验本笔买入后仍满足本节的合计上限，否则拒绝买入并记 ERROR。
- 数量单位：全部以 USDT 名义定层，不以币数定层。价格变了，层名义不变，币数跟着变。

## 3. 网格定义

### 3.1 开组与第 1 层（v1 重写，无空组）
- 满足 §4.1 全部条件时开组，**开组即买第 1 层**：以当时 last 价买入第 1 层名义，成交价记为中心价 C。C = 第 1 层实际成交价（last 价），组内 C 不变。
- 若第 1 层按 §1.3 取整后名义 < 5 USDT，或买入后违反 §2 合计上限，**则不开组**，不建 grid_group 记录，只记 WARNING。不存在 0 持仓的 OPEN 组。
- `first_buy_utc` = 第 1 层成交时间（UTC），时间止损从此时起算。

### 3.2 间距与后续层
- 间距 d（每层下跌幅度）：
  - BTC：1.5 × ATR(14, 1h) / C，再换算为百分比后钳制在 [0.8%, 3.0%]。即 d = min(max(1.5 × ATR / C, 0.008), 0.030)。
  - ETH：2.0 × ATR(14, 1h) / C，钳制在 [1.0%, 4.0%]。即 d = min(max(2.0 × ATR / C, 0.010), 0.040)。
  - ATR 定义见 §3.4。d 在开组时算一次，组内不变。
- 第 n 层（n=2..6）的触发价 = C × (1 − (n−1) × d)。
  - 举例（仅示意）：BTC C=80,000，d=1.5%，各层触发价为 L2 78,800、L3 77,600、L4 76,400、L5 75,200、L6 74,000。
  - 第 6 层之后价格继续下跌：**不再买入**，组保持 OPEN 直到 §5 的退出条件触发。
- 买入条件（每轮按顺序判断）：
  - 用当时 last 价判断，比较符为 `last < trigger_price`（严格小于，等于不触发）。
  - 第 n 层可买入当且仅当：第 1..n−1 层**曾经买入过**（状态为 OPEN、SOLD 或 TIME_STOPPED 任一），且第 n 层尚未买入过（状态为 PENDING），且未触发 §4.2 停加仓，且满足 §2 合计上限。
  - **同轮跌破多层：同轮内按 n 从小到大依次买齐所有触发价已被跌破的层**，每买一层立即更新已部署与现金，再判断下一层，不许用轮询开始时的旧现金判断。
  - 中间层卖出后不影响下一层：例如第 2 层已 SOLD，第 3 层仍可在价格跌破其触发价时买入（因为第 1、2 层都“曾经买入过”）。已 SOLD 的层在本组内**不重新买入**。

### 3.3 卖出
- 每层独立止盈：Ln 的止盈价 = 其买入价 + d × C（即上一层触发价；L1 的止盈价 = C × (1 + d)）。当 last 价 `>=` 止盈价时，卖出该层全部数量。比较符为 `>=`，等于触发。
- 整组止盈：当 last 价 `>=` 加权平均成本 × 1.015 时，卖出组内全部剩余 OPEN 层，组关闭。其中加权平均成本 = Σ(OPEN 层 qty × buy_price) / Σ(OPEN 层 qty)，**只算剩余 OPEN 层，不含已 SOLD / TIME_STOPPED 的层，不含手续费**。
- **组关闭规则：组内 OPEN 层数量变为 0，且至少买入过 1 层时，立即将组标记为 CLOSED。**不许出现 0 持仓的 OPEN 组。
- 卖出价按 §1.3 的 last 价，手续费与滑点照计。

### 3.4 指标定义（v1 新增，全部写死）
- 只用**已收盘**的 1h K 线，不用当前未收盘的那根。每次拉取最近 500 根已收盘 K 线；**不足 500 根时禁止开新组**，已有组只按 §5 卖出。
- ATR(14, 1h)：True Range = max(high − low, |high − prev_close|, |low − prev_close|)，第 1 根 K 线没有 prev_close 时 TR = high − low；ATR = 最近 14 个 TR 的简单平均（SMA）。需要至少 15 根 K 线才有第一个 ATR 值。
- ADX(14, 1h)：Wilder 算法。+DM / −DM、TR、+DI / −DI、DX、ADX 均用 Wilder 平滑（α=1/14）。**至少需要 28 根已收盘 K 线才有第一个 ADX 值**，500 根已满足。ADX 取上一根已收盘 K 线的 ADX 值。
- §4.1.3 的“过去 20 日 ATR 中位数”：取过去 480 根已收盘 K 线对应的 480 个 ATR(14) 值的中位数；480 个为偶数，中位数取中间两个数的算术平均。

## 4. 市场状态过滤（只在震荡市开新仓）

### 4.1 允许开新组（开第 1 层）必须同时满足以下全部条件
1. ADX(14, 1h) < 25（严格小于，等于 25 不满足）。
2. 用**前 480 根已收盘 1h K 线（不含当前未收盘根，也不含上一根已收盘根作为比较对象）**算 HH_480 = 最高价、LL_480 = 最低价；要求**上一根已收盘 1h K 线的 close 严格满足 LL_480 < close < HH_480**。比较对象是上一根收盘价，不是轮询时刻实时价；比较符是严格 < 与 >，等于边界不满足。
3. 当前 ATR(14, 1h) < 过去 20 日 ATR 中位数的 2.0 倍（严格小于，等于不满足）。中位数定义见 §3.4。
4. 未来 24 小时内**且过去 2 小时内**无 `data/events.csv` 中的高影响事件（impact=HIGH，CPI、非农、FOMC 决议）。事件时间用 UTC 比较；区间为 (now−2h, now+24h]，即过去 2 小时不含端点、未来 24 小时含端点。
5. 账户未触发 §5 的任何停手状态（halt 未到期、日亏损已触发）。
6. 该币种无活跃网格组，且买入第 1 层后仍满足 §2 合计上限。

以上任一不满足，禁止开新组，但不影响已有组的按 §5 卖出。

### 4.2 已有组的事中保护（开组后也要执行）
- 价格跌破中心价 −15%：当 last 价 `< C × 0.85`（严格小于）时，立即将该组的 `stop_adding` 置为 1 并持久化（见 §6.1），停止该组继续加仓（未买的层改为 CANCELLED），已有持仓继续按 §5 卖出。价格反弹回 0.85C 以上**不恢复**加仓。
- 单币浮亏停加仓：当某币种未实现亏损 < −12% × 该币种已部署成本（即 unrealized_pnl < −0.12 × Σ(OPEN 层 basis)，严格小于）时，将该组的 `stop_adding` 置为 1 并持久化，停止加仓。**v0 的“800 USDT”是死条款，已删除；12% 是按已部署成本算的比例，BTC 前 5 层在 0.85C 时浮亏约 218 USDT、已部署约 2,127 USDT，占比约 10.2%，12% 在 −15% 停加仓之前或附近可触发，不是死条款。**
- 行情异常：连续 3 次轮询拿不到有效价格（超时 >10 秒、HTTP 非 200、价格为 0 或负数、价格较上一轮跳变 >10% 且无 K 线佐证），停止开新仓与加仓，已有持仓**仍按 §5 卖出，尤其是 §5.1 的回撤强平不得被跳过**（见 §5.5）。异常计数器持久化在 account 表（`price_error_streak`），重启后不清零。恢复条件：连续 3 次轮询都拿到有效价格且跳变 ≤10%，计数器清零，恢复开新仓资格（仍需满足 §4.1）。

## 5. 风控与退出（硬线）

### 5.0 全局退出优先级（v1 新增，每轮每组只执行一个退出动作）
同一轮、同一组内若多个退出条件同时满足，按以下顺序只执行第一个，不许重复卖出：
1. §5.1 回撤 halt（最高）
2. §5.3 满 14 天时间止损（全清）
3. §3.3 整组止盈 +1.5%
4. §5.3 满 7 天时间止损（减半，只执行一次）
5. §3.3 单层止盈
日亏损（§5.2）不触发卖出，只禁止新买入 / 加仓，不参与本优先级。

### 5.1 账户级最大回撤 15%（moka 已定）
- 定义：峰值权益 peak_equity = 自账户创建以来账户权益（§1.4）的历史最高值，持久化在 account 表，**只升不降**，重启后从库恢复，不重置。初始值 = 10,000 USDT。
- 当前回撤 = (peak_equity − equity) / peak_equity。
- 触发：当前回撤 `>= 0.15`（含等于）时，立即按 last 价卖出全部持仓（全部组），关闭全部网格组（状态置为 CLOSED，close_reason="DRAWDOWN_HALT"），记录 `halt_trigger_equity` = 平仓后权益，设置 `halted_until_utc` = 触发时间 + **24 小时（1 天，moka 2026-11-06 11:57 定；v0 的 7 天是 Muse 随手写的、无依据，已删除）**。在 halt 期间禁止开新组、禁止加仓。
- 恢复：halt 期满后，**将 peak_equity 重置为恢复时刻的当前权益**，清除 halt 状态，`halt_trigger_equity` 保留备查。不自动恢复旧组。恢复后开新组仍须满足 §4.1 全部条件。**不重置峰值权益会在恢复后第一秒立即再触发 halt（永久停机死锁），v0 的 bug 已修。**
- 闪崩例外：§4.2 的“可疑价格跳过”**不适用于**本条。即使价格跳变 >10%，只要 last 价有效（>0、HTTP 200），回撤计算与强平照常执行，不得跳过。

### 5.2 每日亏损上限 3%
- 定义：`day_start_equity` = 当日 UTC 00:00 时的账户权益，持久化在 account 表（字段 `day_start_equity_usdt`、`day_start_date_utc`）。每天第一次轮询时若日期已变，先把 `day_start_equity` 设为当前权益、日期设为当日，再继续。
- 当日亏损 = equity − day_start_equity。触发条件为当日亏损 `< −300 USDT`（严格小于，恰好 −300 不触发）。
- 触发后当日禁止开新组与加仓，已有持仓继续按 §5 卖出。次日 UTC 0 点自动解除（除非 §5.1 的 halt 仍在有效期内）。

### 5.3 时间止损
- 一组网格组从 `first_buy_utc`（第 1 层买入时间）起计时：
  - 满 7 天（`now − first_buy_utc >= 7 天`，含等于）且 `half_time_stopped = 0` 时：**只执行一次**。从最高层号的 OPEN 层往低层号整层卖出（不拆层），直到累计卖出的初始层名义 ≥ 组内当前全部 OPEN 层初始名义之和的 50%。若组内仅有 1 层 OPEN，则卖出该层并关组。执行后立即将 `half_time_stopped` 置为 1、`stop_adding` 置为 1 并持久化。**没有此标志会在第 7 天后每 60 秒重复砍仓 50%，v0 的 bug 已修。**
  - 满 14 天（`now − first_buy_utc >= 14 天`，含等于）时：卖出组内全部剩余持仓，组关闭（close_reason="TIME_STOP_14D"）。
- 时间止损的卖出价按 §1.3 的 last 价，手续费与滑点照计。

### 5.4 禁止行为（写死）
- 禁止无限加仓：层数 > 6、合计已部署违反 §2 上限、单币超上限，任一触发即拒绝下单并记 ERROR。
- 禁止在 halt / 日亏上限 / 行情异常期间开新仓。
- 禁止使用杠杆、合约、做空。代码中不得出现 LIVE 下单路径；若未来要加 LIVE，必须另起 SPEC、另冻结，本 SPEC 不覆盖。
- 禁止把利润复投到超出 §2 上限的仓位。盈利只增加现金，不提高上限。

### 5.5 闪崩与跳空（v1 新增）
- 价格在 60 秒内跳变 >10% 时，本轮仍用 last 价计算权益与回撤，§5.1 强平照常执行，不许以“可疑价格”为由跳过。
- 回测中 1h K 线内先跌后涨或先涨后跌的顺序问题见 §8.1（close-only，不用 low/high）。

## 6. 状态、持久化与看板

### 6.1 持久化（SQLite，独立库）
- 库路径：`/home/shenb/myQ/projects/spot-martingale/data/spot_martingale.sqlite3`（与 solana 的 monitor.sqlite3、perp 的 copier.sqlite3 分开，互不读写）。
- 表（v1，字段缺一不可；v0 缺的字段已补）：
  - `account(id=1, cash_usdt, initial_usdt=10000, peak_equity_usdt, halted_until_utc, halt_trigger_equity_usdt, day_start_equity_usdt, day_start_date_utc, price_error_streak, realized_pnl_usdt, updated_utc)`。其中 id=1 是唯一行，建库时初始化：cash=10000、peak_equity=10000、day_start_equity=10000、其余为 NULL 或 0。
  - `grid_groups(id, symbol, center_price, spacing_pct, atr_pct, status[OPEN/CLOSED], opened_utc, first_buy_utc, closed_utc, close_reason[TAKE_PROFIT/TIME_STOP_7D/TIME_STOP_14D/DRAWDOWN_HALT/ALL_SOLD], stop_adding, half_time_stopped)`。**注意：组状态只有 OPEN / CLOSED，没有 HALTED；halt 时组直接置为 CLOSED，close_reason 记 DRAWDOWN_HALT，halt 状态只存在 account 表里。** `stop_adding`、`half_time_stopped` 为 0/1 整数，默认 0。`id` 唯一，`symbol + status=OPEN` 唯一（同一币种同时最多一个 OPEN 组）。
  - `layers(id, group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, status[PENDING/OPEN/SOLD/TIME_STOPPED/CANCELLED/HALT_CLOSED], bought_utc, sold_utc, sell_price)`。PENDING = 尚未买入；CANCELLED = 因停加仓作废；HALT_CLOSED = 因回撤强平卖出。`(group_id, layer_no)` 唯一。
  - `trades(id, utc, symbol, side, price, qty, notional_usdt, fee_usdt, slippage_usdt, group_id, layer_no, reason)`。`id` 自增唯一。
  - `daily_summary(date_utc, realized_pnl, unrealized_pnl, equity, drawdown_pct, trades_count)`。`date_utc` 为 UTC 日期（YYYY-MM-DD），唯一。
  - `risk_events(id, utc, type, detail)`（类型：DRAWDOWN_15 / DAILY_LOSS_3 / REGIME_BLOCK / PRICE_STALE / LAYER_CAP / STOP_ADDING）。
  - `regime_log(utc, symbol, adx, atr_pct, in_range, event_in_24h, event_in_past_2h, allow_new_group)`。
- 原子性：每笔模拟成交 + 现金更新 + 层状态更新 + 组状态更新必须在**同一个 SQLite 事务**里完成，任一步失败则整个事务回滚，不许出现“钱扣了、层没记”或“层卖了、钱没加”的中间态。
- 重启幂等：启动时从库里恢复 OPEN 的组与层、`stop_adding`、`half_time_stopped`、`peak_equity`、`day_start_equity`、`halted_until_utc`、`price_error_streak`，不重复建组、不重复下单、不重复执行 7 天减半。库文件不存在时允许首次建库（按本节初始化）；库文件存在但打不开 / 校验失败（SQLite header 损坏）时，禁止交易，只记 ERROR 并退出（不自动建新库覆盖旧库）。

### 6.2 进程与文件结构（独立项目文件夹）
```
/home/shenb/myQ/projects/spot-martingale/
  spec.md                 # 本文件（冻结版）
  spec_v0.md              # v0 备份，不许改
  config.py               # 全部阈值，见 §2-§5；EXECUTION_MODE="DRY_RUN" 常量
  monitor.py              # 轮询主循环（60 秒），只读行情、写本库
  backtest.py             # 离线回测（见 §8），不连实时行情
  data/events.csv         # 经济事件日历
  data/spot_martingale.sqlite3
  tests/test_*.py         # 单元测试，见 §8.3
  logs/
  results/                # 回测结果 CSV / 报告、评审文件
```
- 进程名：`spot-martingale-monitor`，与 `solana-wallet-monitor`、`perp` 进程隔离，不得共用进程、不得互相 kill / restart。
- **watchdog：v1 没有 watchdog，没有 systemd service。** monitor 进程退出后保持退出状态，等人工处理，不自动重启。§7 里“由外部 watchdog 决定是否重启”的写法已删除。
- 线上代码只读：不得修改 `/home/shenb/myQ/projects/solana-wallet-monitor/monitor.py`、`/home/shenb/myQ/projects/perp-wallet-copier/` 下的任何文件。看板集成只改 `web_dashboard.py` 的只读展示部分（见 §6.3），且改动前备份、改动后由 Codex 审、moka 批准后才重启看板。

### 6.3 看板集成（同一个 web 看板，8081）
- 看板：现有 `web_dashboard.py`（8081，[dashboard-host-redacted]），已有两个顶级应用：Solana（紫）、Perp / Binance 跟单（amber，currentApp='perp'）。
- 本项目新增第三个顶级应用：`currentApp='spot'`，标题“现货马丁 (Binance Spot · 模拟)”，与前两者并列，不得改动前两者的 tab、API 与数据。
- 新增只读 API（GET，仅读本库），返回 JSON 契约如下（字段名、类型、单位写死；无数据时返回默认值，不返回 null 字段）：
  - `/api/spot/summary`：`{"cash_usdt": number, "equity_usdt": number, "peak_equity_usdt": number, "drawdown_pct": number, "realized_pnl_usdt": number, "unrealized_pnl_usdt": number, "halted_until_utc": string|null, "groups": [{"symbol": string, "status": string, "center_price": number, "open_layers": int}]}`。时间用 UTC ISO8601；金额单位 USDT，保留 2 位小数。
  - `/api/spot/positions`：`{"positions": [{"symbol": string, "layer_no": int, "trigger_price": number, "buy_price": number, "qty": number, "unrealized_pnl_usdt": number}]}`，按 symbol、layer_no 升序。
  - `/api/spot/trades?limit=50`：`{"trades": [...]}`，limit 默认 50、最大 100、最小 1，超出范围按边界截断；按 utc 降序；每条含 §6.1 trades 表的全部字段。
  - `/api/spot/regime`：`{"regime": [{"symbol": string, "adx": number, "atr_pct": number, "allow_new_group": bool, "event_in_24h": bool, "event_in_past_2h": bool}]}`。
- 看板不得提供任何交易操作按钮（不提供买入、卖出、加仓、清仓、改参数入口）。v1 看板只做展示。
- 看板读取本库时用只读连接（SQLite `mode=ro`），不得写本库。若读库失败（锁 / 文件不存在），该 tab 显示“数据暂不可用（最后成功读取时间：<UTC 时间>）”，不影响 monitor 进程，反之亦然。
- 端口：不新增端口。复用 8081。不得占用 80/443/8080、3082、1080 等已用端口。

## 7. 异常处理（全部分支必须写清）

| 异常 | 处理 | 日志级别 |
|---|---|---|
| Binance 公共行情 HTTP 超时（>10 秒，含等于 10 秒不算超时，>10 秒才算）/ 非 200 / 返回空 | 本轮跳过，不开新仓、不加仓；`price_error_streak` +1；已有持仓仍按 §5 卖出（§5.1 强平不得跳过） | WARNING；连续 3 次记 ERROR |
| 价格跳变 >10%（与上一轮 last 价比，`abs(last−prev)/prev > 0.10`，严格大于） | 视为可疑价格，但**仍用该 last 价计算权益与回撤**，§5.1 强平照常执行；开新仓与加仓暂停，计入 `price_error_streak` | WARNING；连续 3 次记 ERROR |
| K 线不足 500 根已收盘 | 禁止开新组，已有组只按 §5 卖出 | WARNING |
| `data/events.csv` 缺失 / 解析失败 / 过期 | 禁止开新组（fail-safe），已有组可卖出，记 ERROR；**每轮都检查**，不只在启动时检查。过期定义：文件最后一条事件时间 < 当前时间 + 7 天 | ERROR |
| SQLite 写失败 / 磁盘满 / 锁超时（>5 秒） | 立即停止下单，记 ERROR，进程退出码非 0，**不自动重启**（v1 无 watchdog） | ERROR |
| 配置值越界（如倍数 ≤1、层数 >10、回撤上限 >20%、冷静期 ≠24 小时） | 启动时校验失败，直接退出，不启动交易循环 | ERROR |
| 进程重启 | 从库恢复全部持久化状态（§6.1），不重复建组、不重复执行 7 天减半 | INFO |
| 系统时间跳变（NTP 校正，墙钟跳变绝对值 >60 秒） | 本轮跳过，用 UTC 时间记日志并记 WARNING；**时间止损与 halt 到期用墙钟 UTC 算，不用单调时钟**，跳变后按跳变后的墙钟重新算剩余时间，不补算跳过的轮次 | WARNING |
| 看板读库失败 | 看板该 tab 显示“数据暂不可用”，不影响 monitor 进程 | WARNING（看板侧） |
| Binance 返回 null / K 线乱序 / 重复时间戳 / 缺口 | 丢弃该轮 K 线数据，禁止开新组，已有组只按 §5 卖出，记 WARNING；K 线按时间升序去重，缺口超过 3 根 1h K 线记 ERROR | WARNING / ERROR |

## 8. 回测与验收

### 8.1 回测数据与撮合规则（v1 定死）
- 标的：BTCUSDT、ETHUSDT Binance 现货 1h K 线，2021-01-01 至 2026-10-31。
- 来源：Binance 公共 K 线（data-api.binance.vision），本地缓存为 CSV/Parquet，禁止在回测中调用实时行情。
- 事件：`data/events.csv` 中的 CPI、非农、FOMC 时间（UTC），回测中同样执行 §4 的过滤；**stale 判断按模拟时间算**（模拟时间 + 7 天），不用真实当前日期。
- **撮合规则：close-only。** 每根 1h K 线只在收盘时刻用 close 价判断一次买入 / 卖出，顺序为：先判断 §5.1 回撤 halt，再判断整组止盈 / 时间止损 / 单层止盈（按 §5.0 优先级），最后判断买入（开组 / 加仓）。**不用 low/high，不猜测盘中先跌后涨的顺序。**回测粒度比实盘 60 秒轮询粗，这是一个已知的局限性，在回测报告中写明。
- 成本：与 §1.3 完全一致（last=close 价，手续费 0.10%，滑点 0.02% 单独扣现金）。

### 8.2 回测划分（防过拟合）与压力测试
- 训练：2021-01-01 至 2024-12-31（调参只许在这里）。
- 测试：2025-01-01 至 2026-10-31。
- **“测试集只跑一次”的适用范围：只指“用测试集调参的最终评估只跑一次”。**训练集回测、单元测试、压力测试、以及**不改参数、只修 bug 后的重跑**不受限。改参数后必须回 §2 重新冻结，算新一轮。
- 压力测试（必须单独跑并出报告，定义精确，无挑选空间）：
  1. 合成 +50%：BTC 与 ETH 各自从测试集第一根收盘价起，30 天内每日按固定比例上涨，30 天累计 +50%（日收益率 = 1.5^(1/30) − 1），其余规则与回测一致。
  2. 合成 −50%：同上，30 天累计 −50%（日收益率 = 0.5^(1/30) − 1）。**本测试同时测 BTC 单独满仓与 BTC+ETH 同时满仓两种情形**，分别报告。
  3. 合成先跌后涨：15 天累计 −30%，随后 15 天累计 +20%，日收益率按复利均分。
  4. 真实切片：2022-06-01 00:00 UTC 至 2022-06-30 23:00 UTC，BTCUSDT 与 ETHUSDT 1h 收盘价原样回放。

### 8.3 验收标准（全部满足才算通过，任一不满足即不通过）
1. 测试集扣成本后总盈亏 > 0。
2. 测试集最大净值回撤 ≤ 16.00%（按峰值算，在 1h 收盘价采样；**容差说明：v0 的 ≤15.00% 在离散采样 + 平仓成本下数学上不可能，v1 改为 16.00%，其中 1.00% 为跳空与平仓成本容差**）。
3. 利润因子（总盈利 / 总亏损）≥ 1.20。**口径：按每笔 SELL 成交算**（每笔卖出的已实现盈亏，盈利的求和 / 亏损的求和取绝对值）。若总亏损 = 0 且总盈利 > 0，利润因子记为 +∞，通过；若总盈利 = 0 且总亏损 = 0（无成交），利润因子未定义，**不通过**。
4. 盈利月占比 ≥ 60%。**口径：分母 = 测试集内有 ≥1 笔已平仓成交的自然月数；分子 = 其中已实现盈亏 > 0 的月数；盈亏 = 0 的月不计入分子。**
5. 压力测试 2（合成 −50%）：**halt 必须触发**（回撤达到 ≥15% 并执行平仓），且平仓落地后的最大回撤 ≤ 16.00%，且 halt 期间无新开仓、恢复后峰值权益已重置。**不再要求“最大回撤 ≤15.00%”与“halt 必须触发”同时成立。**
6. 单元测试全过：层金额求和 = 上限（BTC 3,000.00、ETH 2,000.00，误差 0.00）；层数上限 6 不可突破；回撤 15% 触发 halt 且恢复后不立即再触发；事件过滤在事件前 24h 内与事件后 2h 内禁止开组；重启恢复不重复建组、不重复执行 7 天减半；成本模型每笔单边 = 0.12%；同轮跌破多层依次买齐；OPEN 层为 0 立即关组。
7. 回测结果可复现：同一数据 + 同一配置，两次运行结果完全一致（容差：总盈亏 ±0.01 USDT；比较未舍入值，显示值四舍五入到 2 位小数）。

### 8.4 模拟盘验收（回测通过后，另行冻结）
- v1 不定义模拟盘验收标准。回测通过后，由 Muse 另起 SPEC 补充模拟盘的运行时长、观察指标与上线标准，moka 批准后才长时间跑。
- 在模拟盘验收前，不得用真金、不得接 API Key、不得开 LIVE。

## 9. 待 moka / 盲审确认的开放问题（v1 明确标出，不许编程时自行定）
1. Binance 现货行情用 data-api.binance.vision 是否可达（oci2 上实测）？若不可达，备选是 api.binance.com 还是本地缓存 K 线先跑回测？
2. 手续费 0.10% + 滑点 0.02% 是否偏保守？Binance 现货实际费率以盲审核实为准，改动需回 SPEC。
3. 时间止损 7 天减半 / 14 天清仓、整组止盈 +1.5%、单币浮亏停加仓 12%（按已部署成本），这三个数是 v1 暂定值，盲审可挑战，改动需重新冻结。
4. 看板集成是否只做只读展示（v1 定为只读）？若 moka 要在看板上加“暂停/恢复”按钮，需另起 SPEC。
5. 本项目与 perp-test 的关系：独立文件夹、独立库、独立进程，仅共享看板 8081 的展示层，不共享配置、DB 与执行器。
6. **冷静期 1 天是 moka 2026-11-06 11:57 定的。**若回测显示 1 天太短（恢复后立即再跌）或太长（错过反弹），改动需回 SPEC、重新冻结。

## 10. 安全与红线
- DRY_RUN only。代码中不得出现 Binance API Key / Secret 字段、不得读 `~/.config` 下的任何密钥文件、不得在日志中打印任何密钥。
- 不推 GitHub。除非 moka 明确下达。
- 不动线上：solana monitor（PID 1466554，2026-11-06 部署的 Config 1）、perp copier、Hermes 网关、agy remote-control、Caddy 配置。看板重启需 moka 批准。
- 模拟资金 10,000 USDT 是账面数字，不是真实入金。任何“充值 / 提现 / 转币”操作均不在 v1 范围内。
