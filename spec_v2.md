# SPEC v2 — BTC/ETH 现货有限层数网格马丁（Binance Spot，模拟）
# 项目：/home/shenb/myQ/projects/spot-martingale/
# 状态：草稿 v2，未冻结。第 3 轮（流程上限 3 轮；v0 FAIL，v1 FAIL，见 results/merged_review_v0.md、merged_review_v1.md）。
# 日期：2026-11-06
# 变更来源：moka 决定（2026-11-06 11:57）冷静期 1 天；其余按 v1 两份盲审（Codex 完整 FAIL + agy 部分，timeout 600 秒截断）与 Muse 独立复算修改。
# v1 → v2 变更日志（每条对应 merged_review_v1.md 的编号）：
#  V2-P0-1 halt 无限重触发：加 halt_active 标志；每轮先执行“halt 期满恢复并重置峰值”，再判断 §5.1；halt 期间跳过 §5.1。
#  V2-P0-2 halt 清仓原子性：整个 halt 清仓放一个 SQLite 事务；加 account.halt_state（NONE/HALTING/HALTED）；失败保持 HALTING，下一轮先续清。
#  V2-P0-3 realized_pnl 漏计买入成本：公式改为卖出时扣买卖双边手续费与滑点；layers 表加 buy_fee_usdt、buy_slippage_usdt；写清现金不变量。
#  V2-P0-4 止盈价公式矛盾：TP_n 改为上一层触发价（n≥2），L1 为 C×(1+d)，与成交价无关；删“买入价 + d×C”的写法。
#  V2-P0-5 上限拒买无限重试：拒买入时该层置 CANCELLED、组 stop_adding 置 1，只记一次 ERROR。
#  V2-P0-6 上限口径：已部署 = Σ basis（不含手续费/滑点）；合计上限改为固定 5,000（= 初始权益×50%），删动态当前权益×50%；动态保护只靠 15% halt。
#  V2-P0-7 压力测试改为确定性持仓测试：起始状态两币各 6 层按触发价全买（d_BTC=1.5%、d_ETH=2.0% 固定），不经过 §4.1/§4.2，直接施加合成价格路径，逐小时算权益与 halt；§8.3(5) 拆分单币/双币：halt 必须触发只适用于双币组合，单币要求最大回撤 ≤16.00% 且不要求 halt。
#  V2-P1-8 480 窗口与指标种子定死：HH/LL 取最近 480 根已收盘 K 线（含上一根比较对象）；ATR 中位数取同一批 480 根；TR 首根 = high−low，第一个 ATR(14) 在第 14 根；ADX Wilder 种子 = 前 14 个 DX 的平均，第一个 ADX 在第 28 根；28 根只是数学最小值，交易要求 500 根。
#  V2-P1-9 日亏损基准：每轮持久化 last_equity；跨日时 day_start_equity = 午夜前最后一次记录的权益；无午夜前记录时用首次轮询权益并记 WARNING。
#  V2-P1-10 价格源边界：有效价加 serverTime 偏差 ≤60 秒；跳变 >10% 记可疑价，本轮不用它做 halt/止盈判断，改用上一轮有效价，连续两轮同向或 K 线收盘价偏差 ≤5% 时接受；行情异常期间单层/整组止盈跳过，halt 评估用上一轮有效价。
#  V2-P1-11 时间跳变守卫：墙钟向前跳 >60 秒，本轮跳过 halt/时间止损到期判断；向后跳不重新触发已过期计时器；冷静期仍为触发时刻 +24 小时（墙钟 UTC）。
#  V2-P1-12 close_reason 与层状态：7 天减半卖空整组记 TIME_STOP_7D（优先于 ALL_SOLD）；减半卖出的层 = TIME_STOPPED；组关闭时剩余 PENDING 层同一事务内置 CANCELLED；TAKE_PROFIT = 整组止盈，ALL_SOLD = 单层止盈卖空整组；trades.reason 加枚举。
#  V2-P1-13 事件日历：stale 改为最后一条事件 < 当前时间 + 90 天；文件须有版本头、按 utc_time 升序、无重复（加载校验）；回测报告记录 events.csv 的 sha256。
#  V2-P1-14 配置校验：启动校验改为严格等于 SPEC 冻结值，附冻结值表；任一不等于即退出。
#  V2-P1-15 看板 API：halted_until_utc 允许 null 是明确例外；其余数值默认 0、字符串默认 ""；summary 加 last_successful_read_utc；API 在一次只读事务内读全部表，保证同一快照。
#  V2-P1-16 16% 容差口径：16.00% 是阈值参数，不是推导出的保证；最大回撤同时报收盘采样峰值回撤与强平落地后回撤，任一 >16.00% 即不通过。
#  V2-P1-17 超时与佐证：超时指单次请求总耗时 >10 秒；K 线佐证 = 最近一根已收盘 1h K 线 close 与可疑价偏差 ≤5%；price_error_streak 只计拿不到有效价的轮次，可疑价单独计 suspect_price_streak。
#  V2-P2-18 优先级 5 多层同轮达标：同轮卖出全部达标层。
#  V2-P2-19 account 表加字段：last_valid_price、last_valid_price_utc、last_equity_usdt、last_equity_utc、halt_state、halt_active、suspect_price_streak、recovery_streak。
#  V2-P2-20 浮亏停加仓规则删除：12%（及任何固定 % of deployed）是间距依赖的死条款——d=0.8% 时约 0.90C 触发，d=1.5% 时约 0.845C（晚于 −15%），d=3.0% 时约 0.825C（晚于 −15%）；v1 的 218/10.2% 是 v0 触发价抄错的数，正确是 247.47/11.64%（d=1.5%）。删除该规则，停加仓只靠 §4.2 的 −15% 价格规则与 §5.1 的 15% halt。
#  V2-P2-21 合成压力不再需要 720 根 K 线拆分与 500 根预热（改为持仓测试，无指标、无过滤）。

## 0. 目标与非目标

### 0.1 目标
在 BTCUSDT、ETHUSDT 现货上，用有限层数网格马丁做“经常小赚”，核心是活得久。
先证明可回测、有统计优势，再考虑模拟盘长时间跑。v2 阶段不碰实盘、不接实盘 API。

### 0.2 非目标（v2 明确不做）
- 不做合约、永续、杠杆、做空、资金费率套利。
- 不做黄金、SOL、Meme 币。
- 不做新闻抢单（1-10 秒 News Scalping）。新闻只做风控过滤（见 §4）。
- 不做自动实盘下单。v2 只有 DRY_RUN 模拟撮合。
- 不推 GitHub。线上代码只读。

### 0.3 成功标准
回测通过才算有资格进模拟盘；模拟盘通过才谈别的。标准见 §8。

## 1. 输入与输出

### 1.1 输入
| 输入 | 来源 | 频率 / 格式 |
|---|---|---|
| BTCUSDT、ETHUSDT 现货 last 价 | Binance Spot 公共行情 https://data-api.binance.vision ，GET /api/v3/ticker/price。无 API Key、无 Secret。**注意：该接口只返回 last 价，不含 bid/ask；v2 不用 bid/ask，不用 bookTicker。** | 轮询每 60 秒 |
| 1h K 线（已收盘） | 同上，GET /api/v3/klines（interval=1h）。每次拉取最近 500 根已收盘 K 线（不含当前未收盘的那根）。 | 每轮拉取一次，缓存本地 |
| ATR(14)、ADX(14) | 由 1h 已收盘 K 线本地计算，定义见 §3.4 | 每轮计算一次 |
| 经济事件日历 | 本地文件 `data/events.csv`，字段：utc_time, event, country, impact；文件头第一行为版本行 `# version: YYYYMMDD, sha256: <值>`（建文件时写，改动即换版本）。初始覆盖 CPI、非农、FOMC 决议（2020-01-01 至 2026-12-31，UTC）。impact 取值只能是 HIGH / MEDIUM / LOW；“高影响”指 impact=HIGH。加载时校验：按 utc_time 升序、无重复 utc_time+event、无空字段，失败则禁开新组并记 ERROR。 | 启动时加载，每轮检查 stale（见 §7） |
| 配置 | `config.py`，全部阈值见 §2-§6 与 §7 的冻结值表。**配置唯一来源就是 config.py，不读环境变量，不读其他文件。EXECUTION_MODE 是 config.py 里的常量 "DRY_RUN"，不允许改成其他值，代码中不得存在 LIVE 下单路径。**启动时把全部配置与 EXECUTION_MODE 记入日志，并按 §7 做严格等于校验。 | 启动时一次性读入 |

### 1.2 输出
| 输出 | 位置 / 格式 |
|---|---|
| 模拟账户状态 | SQLite `data/spot_martingale.sqlite3`，表见 §6.1 |
| 每笔模拟成交 | 同库 `trades` 表：utc（ISO8601，UTC）、symbol、side（BUY/SELL）、price、qty、notional_usdt、fee_usdt、slippage_usdt、group_id、layer_no、reason（枚举见 §6.1） |
| 每日汇总 | 同库 `daily_summary` 表 + 日志 `logs/spot_YYYYMMDD.log`（日期用 UTC 日期） |
| 看板数据 | 只读 HTTP API（见 §6.3），由 web_dashboard.py 读取本库，不写本库 |

### 1.3 模拟撮合规则（必须写死）
- 买入价 = 当时 last 价；卖出价 = 当时 last 价。**不做 last×(1±0.0002) 的价格偏移。**
- 手续费 = 成交名义 × 0.0010（0.10%，Binance 现货 VIP0 taker 口径）。
- 滑点 = 成交名义 × 0.0002（0.02%），**作为单独的现金扣款，不调整成交价**。
- 单边总成本 = 0.10% + 0.02% = 0.12%。往返成本在买卖名义相同时为 0.24%；若卖出名义比买入名义高（例如整组 +1.5% 止盈），往返成本占买入名义的比例为 0.12% + 0.12%×1.015 = 0.2418%，以实际现金流水为准，不许在报告里写成 0.24%。
- 最小下单：BTC 0.001 BTC、ETH 0.01 ETH、单笔名义 ≥ 5 USDT（用 `>= 5.00` 判断，恰好 5.00 可以下单）。低于此值的层不下单，记 WARNING；**若第 1 层低于此值，则不开组（见 §3.1），不产生空组。**
- 数量 = 层名义 / 成交价，再按最小步长向下取整。取整后名义 < 5 USDT 的层跳过，不把金额累到下一层。

### 1.4 会计口径（v2，全部写死）
- 现金 cash_usdt：买入时扣 成交名义 + 手续费 + 滑点；卖出时加 成交名义 − 手续费 − 滑点。
- 持仓成本 basis：某层成本 = qty × buy_price（**不含**手续费与滑点，手续费只走现金）。
- 持仓市值 market_value = Σ(OPEN 层 qty × 当前 last 价)。
- **账户权益 equity = cash_usdt + market_value。**
- 未实现盈亏 unrealized_pnl = Σ(OPEN 层 qty × (last 价 − buy_price))。
- 已实现盈亏 realized_pnl：某层卖出时记 (sell_price − buy_price) × qty **− 该层买入手续费 − 该层买入滑点 − 该笔卖出手续费 − 该笔卖出滑点**，累计进 account 表。买入手续费与滑点存在 layers 表的 buy_fee_usdt、buy_slippage_usdt 字段里，卖出时从该层记录取，不许重算。**v1 只扣卖出成本是错的：同一价格买卖 realized_pnl 会只记 −0.12% 而不是 −0.24%，利润因子与盈利月占比被系统性虚增。**
- **现金不变量（单测必须断言）**：任意时刻，cash_usdt = initial_usdt + realized_pnl_usdt − Σ(OPEN 层 basis) − Σ(OPEN 层 buy_fee_usdt + buy_slippage_usdt)。即已实现盈亏已含买卖双边成本，未实现部分只剩 OPEN 层的 basis 与买入成本。违反此式即会计 bug。
- 估值价：权益、回撤、日亏损一律用 last 价（或上一轮有效价，见 §4.2 的可疑价规则），不用 bid/ask、不用 K 线收盘价。

## 2. 资金与仓位（数字全部冻结在 v2，冻结值表见 §7）

- 模拟初始本金：10,000 USDT（与 perp-test 的 SIMULATION_INITIAL_BALANCE 一致，便于对照）。
- BTC 网格总上限：3,000 USDT（本金的 30%）。
- ETH 网格总上限：2,000 USDT（本金的 20%）。
- 两币合计已部署上限：**任何时候，Σ(全部 OPEN 层 basis) ≤ 5,000 USDT。**其中 5,000 = min(5,000, 初始权益 10,000 × 50%)，是**固定值**，不用当前权益算。**v1 的“min(5000, 当前权益×50%)”是错的：双币跌到第 6 层触发价时权益约 9,610–9,715，上限会降到约 4,805–4,857，双币永远买不满 6 层。**动态保护只靠 §5.1 的 15% halt，不靠这个上限。取舍已写明：固定上限允许权益下跌后仍按计划买满，代价是回撤保护完全交给 halt。
- **已部署的口径（v2 统一）**：已部署 = Σ(OPEN 层 basis)，**不含**手续费与滑点。手续费与滑点只走现金，不计入上限。v1 的“已部署含费”与“层名义合计严格等于上限”同时成立是不可能的（BTC 含费 3,003.60 > 3,000），已删除含费口径。
- 单币种同时只能有一个活跃网格组（grid_group）。一个组没关闭，不开第二个组。BTC 与 ETH 的组相互独立，但合计上限共同约束。
- 层数：每组最多 6 层。买满 6 层后禁止再加仓。
- 加仓倍数：1.3（固定，不是 2.0）。
- 层名义唯一公式：`layer_notional(n) = round(cap_usdt × 1.3^(n−1) / S, 2)`，其中 S = 1 + 1.3 + 1.3^2 + 1.3^3 + 1.3^4 + 1.3^5 = **12.75603**。
  - 第 6 层用残差法：`layer_notional(6) = cap_usdt − Σ(layer_notional(1..5))`，使 6 层合计**严格等于**上限。
  - BTC 各层（cap=3000）：235.18、305.74、397.46、516.70、671.71、873.21，合计 3,000.00。
  - ETH 各层（cap=2000）：156.79、203.83、264.97、344.46、447.80、582.15，合计 2,000.00。
  - **上限校验用实际成交名义（basis），不用计划名义。**买入前必须校验本笔买入后仍满足本节的单币上限与合计上限，否则拒绝买入并按 §3.2 处理（置 CANCELLED、stop_adding=1、只记一次 ERROR，不无限重试）。
- 数量单位：全部以 USDT 名义定层，不以币数定层。价格变了，层名义不变，币数跟着变。

## 3. 网格定义

### 3.1 开组与第 1 层（无空组）
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
  - **上限拒买的处理（v2 新增）**：第 n 层因违反 §2 上限被拒绝买入时，将该层置为 CANCELLED、将组的 `stop_adding` 置为 1、只记一次 ERROR，之后不再重试该层。**不许让 PENDING 层每 60 秒无限重试刷 ERROR。**
  - **停加仓后高层处理（v2 新增）**：§4.2 停加仓时，未买入的层全部置为 CANCELLED（不只是 <0.85C 那一条），之后不再触发。已买入的层不受影响。

### 3.3 卖出
- 每层独立止盈：Ln 的止盈价 = **上一层触发价**，即 n≥2 时 TP_n = C × (1 − (n−2) × d)，L1 的止盈价 = C × (1 + d)。**v1 的“买入价 + d×C（即上一层触发价）”是错的：n≥2 时买入价严格小于本层触发价，买入价 + d×C 严格小于上一层触发价，两者不等。v2 只用触发价定义，与成交价无关。**当 last 价 `>=` 止盈价时，卖出该层全部数量。比较符为 `>=`，等于触发。
- 整组止盈：当 last 价 `>=` 加权平均成本 × 1.015 时，卖出组内全部剩余 OPEN 层，组关闭。其中加权平均成本 = Σ(OPEN 层 qty × buy_price) / Σ(OPEN 层 qty)，**只算剩余 OPEN 层，不含已 SOLD / TIME_STOPPED 的层，不含手续费**。
- **组关闭规则：组内 OPEN 层数量变为 0，且至少买入过 1 层时，立即将组标记为 CLOSED。**不许出现 0 持仓的 OPEN 组。组关闭时，组内剩余的 PENDING 层在**同一事务**内置为 CANCELLED。
- **close_reason 映射（v2 定死）**：整组止盈 → TAKE_PROFIT；单层止盈卖空整组 → ALL_SOLD；7 天减半卖空整组 → TIME_STOP_7D（优先于 ALL_SOLD）；14 天清仓 → TIME_STOP_14D；回撤 halt → DRAWDOWN_HALT。
- 卖出价按 §1.3 的 last 价，手续费与滑点照计。7 天减半卖出的层状态记为 TIME_STOPPED，不是 SOLD。

### 3.4 指标定义（v2，全部写死）
- 只用**已收盘**的 1h K 线，不用当前未收盘的那根。每次拉取最近 500 根已收盘 K 线；**不足 500 根时禁止开新组**，已有组只按 §5 卖出。
- ATR(14, 1h)：True Range = max(high − low, |high − prev_close|, |low − prev_close|)；**第 1 根 K 线没有 prev_close 时 TR = high − low**；ATR = 最近 14 个 TR 的简单平均（SMA）。**第一个 ATR(14) 值在第 14 根 K 线**（14 个 TR 已齐）。v1 写“需要至少 15 根”是错的，已更正。
- ADX(14, 1h)：Wilder 算法。+DM / −DM、TR、+DI / −DI、DX 均用 Wilder 平滑（α=1/14）。**种子：第一个 ADX 值 = 前 14 个 DX 值的算术平均，出现在第 28 根 K 线。**28 根只是**数学最小值**，不是交易阈值；**交易要求 500 根已收盘 K 线**，两者不要混淆。
- **区间窗口（v2 定死）**：HH_480 / LL_480 取**最近 480 根已收盘 K 线（含上一根比较对象那根）**的最高价 / 最低价。§4.1.3 的“过去 20 日 ATR 中位数”取**同一批 480 根**已收盘 K 线对应的 480 个 ATR(14) 值的中位数；480 个为偶数，中位数取中间两个数的算术平均。

## 4. 市场状态过滤（只在震荡市开新仓）

### 4.1 允许开新组（开第 1 层）必须同时满足以下全部条件
1. ADX(14, 1h) < 25（严格小于，等于 25 不满足）。取上一根已收盘 K 线的 ADX 值。
2. 用最近 480 根已收盘 1h K 线（含上一根）算 HH_480 = 最高价、LL_480 = 最低价；要求**上一根已收盘 1h K 线的 close 严格满足 LL_480 < close < HH_480**。比较对象是上一根收盘价，不是轮询时刻实时价；比较符是严格 < 与 >，等于边界不满足。**注意：HH/LL 含比较对象那根，所以上一根收盘价若为 480 根内新高/新低，条件不满足（视为突破）。**
3. 当前 ATR(14, 1h) < 过去 20 日 ATR 中位数的 2.0 倍（严格小于，等于不满足）。中位数定义见 §3.4。
4. 未来 24 小时内**且过去 2 小时内**无 `data/events.csv` 中的高影响事件（impact=HIGH，CPI、非农、FOMC 决议）。事件时间用 UTC 比较；区间为 (now−2h, now+24h]，即过去 2 小时不含端点、未来 24 小时含端点。
5. 账户未触发 §5 的任何停手状态（halt 未到期、日亏损已触发）。
6. 该币种无活跃网格组，且买入第 1 层后仍满足 §2 合计上限。

以上任一不满足，禁止开新组，但不影响已有组的按 §5 卖出。

### 4.2 已有组的事中保护与价格源（v2 重写）
- 价格跌破中心价 −15%：当 last 价 `< C × 0.85`（严格小于）时，立即将该组的 `stop_adding` 置为 1 并持久化，停止该组继续加仓（未买的层全部置为 CANCELLED，见 §3.2），已有持仓继续按 §5 卖出。价格反弹回 0.85C 以上**不恢复**加仓。
- **浮亏停加仓规则已删除（v2）**：v0 的 800 USDT 与 v1 的 12%（按已部署成本）都是间距依赖的死条款。Muse 复算（v1 网格，L1 在 C，前 5 层在 0.85C）：d=0.8% 时浮亏 281.75、占比 13.25%；d=1.5% 时浮亏 **247.47**、占比 **11.64%**（v1 写的 218 / 10.2% 是 v0 触发价抄错的数）；d=3.0% 时浮亏 168.12、占比 7.90%。12% 阈值在 d=0.8% 时约 0.90C 触发，在 d=1.5% 时约 0.845C（晚于 −15%），在 d=3.0% 时约 0.825C（晚于 −15%）。任何固定 % of deployed 都会在宽间距路径上晚于 −15% 价格规则，是冗余的。**停加仓只靠 −15% 价格规则与 §5.1 的 15% halt。**
- **有效价格定义（v2 新增）**：一次价格读取为“有效”当且仅当 HTTP 200、价格 > 0、且 Binance 返回的 serverTime 与本地 UTC 偏差 ≤ 60 秒。**超时指单次请求总耗时 > 10 秒**（含等于 10 秒不算超时，>10 秒才算）。
- **可疑价（v2 新增）**：有效价较上一轮有效价跳变 `abs(last−prev)/prev > 0.10`（严格大于）时，记为可疑价：本轮**不用它**做 halt 判断与止盈判断，改用上一轮有效价估值；可疑价计入单独的 `suspect_price_streak`（不计入 `price_error_streak`）。接受条件（二选一）：连续两轮同向跳变（第二轮价格与可疑价偏差 ≤5%），或最近一根已收盘 1h K 线的 close 与可疑价偏差 ≤5%（**K 线佐证标准**）。接受后该价成为新的上一轮有效价，`suspect_price_streak` 清零。**取舍已写明：真实闪崩时 halt 最多延迟一轮（60 秒），换取孤立坏值不误强平。**
- 行情异常：连续 3 次轮询拿不到有效价格（超时 / 非 200 / 价格 ≤0 / serverTime 偏差 >60 秒），停止开新仓与加仓；已有持仓的单层/整组止盈**跳过**（无有效价可判断），halt 评估改用上一轮有效价（见 §5.1）。异常计数器 `price_error_streak` 持久化在 account 表，重启后不清零。恢复条件：连续 3 次轮询都拿到有效价格且跳变 ≤10%，`price_error_streak` 清零，恢复开新仓资格（仍需满足 §4.1）。恢复计数用 account 表的 `recovery_streak` 字段持久化。

## 5. 风控与退出（硬线）

### 5.0 全局退出优先级（每轮每组只执行一个退出动作）与每轮顺序
- **每轮处理顺序（v2 新增，定死）**：(a) 先执行 halt 期满恢复（见 §5.1）：若 `halt_active=1` 且 `now >= halted_until_utc`，重置 peak_equity 为当前权益、清除 halt 状态；(b) 再判断 §5.1 回撤 halt；(c) 再按以下优先级执行组内退出。**halt 期间（halt_active=1 且未到期）跳过 (b)(c) 的全部交易判断，只做估值与日志。**
- 同一轮、同一组内若多个退出条件同时满足，按以下顺序只执行第一个，不许重复卖出：
  1. §5.1 回撤 halt（最高）
  2. §5.3 满 14 天时间止损（全清）
  3. §3.3 整组止盈 +1.5%
  4. §5.3 满 7 天时间止损（减半，只执行一次）
  5. §3.3 单层止盈。**优先级 5 在同轮内有多层同时达到止盈价时，卖出全部达标层，不是每轮只卖一层。**
- 日亏损（§5.2）不触发卖出，只禁止新买入 / 加仓，不参与本优先级。

### 5.1 账户级最大回撤 15%（moka 已定）
- 定义：峰值权益 peak_equity = 自账户创建以来账户权益（§1.4）的历史最高值，持久化在 account 表，**只升不降**，重启后从库恢复，不重置。初始值 = 10,000 USDT。
- 当前回撤 = (peak_equity − equity) / peak_equity。
- **halt 状态机（v2 新增）**：account 表有 `halt_active`（0/1）与 `halt_state`（NONE/HALTING/HALTED）。未触发时 halt_active=0、halt_state=NONE。触发时先置 halt_state=HALTING，执行 §5.1 的清仓事务；事务全部成功后置 halt_state=HALTED、halt_active=1、设置 `halted_until_utc` = 触发时间 + **24 小时（1 天，moka 2026-11-06 11:57 定）**。**触发条件只在 halt_active=0 时判断；halt 期间跳过 §5.1 判断，不许把 halted_until 重置为 now+24h（v1 的永久停机 bug 已修）。**
- 触发：当前回撤 `>= 0.15`（含等于）且 halt_active=0 时，立即按 last 价（或上一轮有效价，见 §4.2 可疑价规则）卖出全部持仓（全部组），关闭全部网格组（状态置为 CLOSED，close_reason="DRAWDOWN_HALT"），记录 `halt_trigger_equity` = 平仓后权益。**整个 halt 清仓（全部组、全部层、现金更新、组关闭）在一个 SQLite 事务里完成；事务失败则 halt_state 保持 HALTING、halt_active 保持 0、已提交的部分不回滚（事务本身保证要么全成要么全不成），下一轮先续清剩余持仓，不做其他动作。**
- 恢复：halt 期满后（now >= halted_until_utc），**将 peak_equity 重置为恢复时刻的当前权益**，halt_active 置 0、halt_state 置 NONE、halted_until_utc 置 NULL，`halt_trigger_equity` 保留备查。不自动恢复旧组。恢复后开新组仍须满足 §4.1 全部条件。
- **重启恢复顺序（v2 新增）**：进程启动时若 halt_active=1 且 halted_until_utc 已过，先执行恢复（重置峰值、清除 halt），再做当轮回撤检查；不许先按旧峰值检查再恢复（会造成恢复瞬间再次 halt）。若 halt_state=HALTING，先续清剩余持仓，再判断恢复。
- 闪崩例外：§4.2 的“可疑价格跳过”**不适用于**本条的估值——halt 评估用上一轮有效价（见 §4.2），不许用可疑价直接强平，也不许以“可疑价格”为由跳过 halt 评估。

### 5.2 每日亏损上限 3%
- 定义：`day_start_equity` = 当日 UTC 00:00 时的账户权益，持久化在 account 表（字段 `day_start_equity_usdt`、`day_start_date_utc`）。
- **跨日规则（v2 修正）**：每轮持久化 `last_equity_usdt` 与 `last_equity_utc`。每天第一次轮询时若日期已变，`day_start_equity` = **午夜前最后一次记录的 last_equity_usdt**（不是当前权益），日期设为当日，再继续。若无午夜前记录（首次运行 / 长时间停机），day_start_equity = 首次轮询时的当前权益，并记 WARNING（当日风控为近似值，日亏损可能被低估）。
- 当日亏损 = equity − day_start_equity。触发条件为当日亏损 `< −300 USDT`（严格小于，恰好 −300 不触发）。
- 触发后当日禁止开新组与加仓，已有持仓继续按 §5 卖出。次日 UTC 0 点自动解除（除非 §5.1 的 halt 仍在有效期内）。

### 5.3 时间止损
- 一组网格组从 `first_buy_utc`（第 1 层买入时间）起计时：
  - 满 7 天（`now − first_buy_utc >= 7 天`，含等于）且 `half_time_stopped = 0` 时：**只执行一次**。从最高层号的 OPEN 层往低层号整层卖出（不拆层），直到累计卖出的初始层名义 ≥ 组内当前全部 OPEN 层初始名义之和的 50%。若组内仅有 1 层 OPEN，则卖出该层并关组。执行后立即将 `half_time_stopped` 置为 1、`stop_adding` 置为 1 并持久化。卖出的层状态记为 TIME_STOPPED。若减半卖空整组，close_reason=TIME_STOP_7D（优先于 ALL_SOLD）。
  - 满 14 天（`now − first_buy_utc >= 14 天`，含等于）时：卖出组内全部剩余持仓，组关闭（close_reason="TIME_STOP_14D"）。
- 时间止损的卖出价按 §1.3 的 last 价，手续费与滑点照计。

### 5.4 禁止行为（写死）
- 禁止无限加仓：层数 > 6、合计已部署违反 §2 上限、单币超上限，任一触发即拒绝下单并按 §3.2 处理。
- 禁止在 halt / 日亏上限 / 行情异常期间开新仓。
- 禁止使用杠杆、合约、做空。代码中不得出现 LIVE 下单路径；若未来要加 LIVE，必须另起 SPEC、另冻结，本 SPEC 不覆盖。
- 禁止把利润复投到超出 §2 上限的仓位。盈利只增加现金，不提高上限。

### 5.5 闪崩与跳空
- 价格在 60 秒内跳变 >10% 时按 §4.2 可疑价规则处理：本轮不用可疑价做 halt/止盈判断，改用上一轮有效价；真实闪崩时 halt 最多延迟一轮（60 秒）。
- 回测中 1h K 线内先跌后涨或先涨后跌的顺序问题见 §8.1（close-only，不用 low/high）。

## 6. 状态、持久化与看板

### 6.1 持久化（SQLite，独立库）
- 库路径：`/home/shenb/myQ/projects/spot-martingale/data/spot_martingale.sqlite3`（与 solana 的 monitor.sqlite3、perp 的 copier.sqlite3 分开，互不读写）。
- 表（v2，字段缺一不可）：
  - `account(id=1, cash_usdt, initial_usdt=10000, peak_equity_usdt, halted_until_utc, halt_trigger_equity_usdt, halt_active, halt_state, day_start_equity_usdt, day_start_date_utc, price_error_streak, suspect_price_streak, recovery_streak, last_valid_price, last_valid_price_utc, last_equity_usdt, last_equity_utc, realized_pnl_usdt, updated_utc)`。其中 id=1 是唯一行，建库时初始化：cash=10000、peak_equity=10000、day_start_equity=10000、halt_active=0、halt_state='NONE'、其余为 NULL 或 0。`halt_state` 取值只能是 NONE/HALTING/HALTED；清除 halt 时 halted_until_utc 置 NULL（这是规定的清除编码）。
  - `grid_groups(id, symbol, center_price, spacing_pct, atr_pct, status[OPEN/CLOSED], opened_utc, first_buy_utc, closed_utc, close_reason[TAKE_PROFIT/ALL_SOLD/TIME_STOP_7D/TIME_STOP_14D/DRAWDOWN_HALT], stop_adding, half_time_stopped)`。**组状态只有 OPEN / CLOSED，没有 HALTED；halt 时组直接置为 CLOSED，close_reason 记 DRAWDOWN_HALT，halt 状态只存在 account 表里。** `stop_adding`、`half_time_stopped` 为 0/1 整数，默认 0。`id` 唯一，`symbol + status=OPEN` 唯一（同一币种同时最多一个 OPEN 组）。
  - `layers(id, group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status[PENDING/OPEN/SOLD/TIME_STOPPED/CANCELLED/HALT_CLOSED], bought_utc, sold_utc, sell_price)`。PENDING = 尚未买入；CANCELLED = 因停加仓 / 上限拒买 / 组关闭作废；HALT_CLOSED = 因回撤强平卖出；TIME_STOPPED = 7 天减半卖出。`(group_id, layer_no)` 唯一。
  - `trades(id, utc, symbol, side, price, qty, notional_usdt, fee_usdt, slippage_usdt, group_id, layer_no, reason)`。`reason` 枚举：GROUP_OPEN / LAYER_BUY / LAYER_TP / GROUP_TP / TIME_STOP_7D / TIME_STOP_14D / DRAWDOWN_HALT。`id` 自增唯一。
  - `daily_summary(date_utc, realized_pnl, unrealized_pnl, equity, drawdown_pct, trades_count)`。`date_utc` 为 UTC 日期（YYYY-MM-DD），唯一。
  - `risk_events(id, utc, type, detail)`（类型：DRAWDOWN_15 / DAILY_LOSS_3 / REGIME_BLOCK / PRICE_STALE / LAYER_CAP / STOP_ADDING / SUSPECT_PRICE）。
  - `regime_log(utc, symbol, adx, atr_pct, in_range, event_in_24h, event_in_past_2h, allow_new_group)`。
- 原子性：每笔模拟成交 + 现金更新 + 层状态更新 + 组状态更新必须在**同一个 SQLite 事务**里完成，任一步失败则整个事务回滚，不许出现“钱扣了、层没记”或“层卖了、钱没加”的中间态。**halt 清仓整个动作（全部组）在一个事务里完成（见 §5.1），不是逐组事务。**
- 重启幂等：启动时从库里恢复 OPEN 的组与层、`stop_adding`、`half_time_stopped`、`peak_equity`、`day_start_equity`、`halt_active`、`halt_state`、`halted_until_utc`、`price_error_streak`、`suspect_price_streak`、`last_valid_price`、`last_equity_usdt`，不重复建组、不重复下单、不重复执行 7 天减半。库文件不存在时允许首次建库（按本节初始化）；库文件存在但打不开 / 校验失败（SQLite header 损坏）时，禁止交易，只记 ERROR 并退出（不自动建新库覆盖旧库）。

### 6.2 进程与文件结构（独立项目文件夹）
```
/home/shenb/myQ/projects/spot-martingale/
  spec.md                 # 本文件（冻结版）
  spec_v0.md              # v0 备份，不许改
  spec_v1.md              # v1 备份，不许改
  config.py               # 全部阈值，见 §2-§5 与 §7 冻结值表；EXECUTION_MODE="DRY_RUN" 常量
  monitor.py              # 轮询主循环（60 秒），只读行情、写本库
  backtest.py             # 离线回测（见 §8），不连实时行情
  data/events.csv         # 经济事件日历（含版本头）
  data/spot_martingale.sqlite3
  tests/test_*.py         # 单元测试，见 §8.3
  logs/
  results/                # 回测结果 CSV / 报告、评审文件
```
- 进程名：`spot-martingale-monitor`，与 `solana-wallet-monitor`、`perp` 进程隔离，不得共用进程、不得互相 kill / restart。
- **watchdog：v2 没有 watchdog，没有 systemd service。** monitor 进程退出后保持退出状态，等人工处理，不自动重启。
- 线上代码只读：不得修改 `/home/shenb/myQ/projects/solana-wallet-monitor/monitor.py`、`/home/shenb/myQ/projects/perp-wallet-copier/` 下的任何文件。看板集成只改 `web_dashboard.py` 的只读展示部分（见 §6.3），且改动前备份、改动后由 Codex 审、moka 批准后才重启看板。

### 6.3 看板集成（同一个 web 看板，8081）
- 看板：现有 `web_dashboard.py`（8081，[dashboard-host-redacted]），已有两个顶级应用：Solana（紫）、Perp / Binance 跟单（amber，currentApp='perp'）。
- 本项目新增第三个顶级应用：`currentApp='spot'`，标题“现货马丁 (Binance Spot · 模拟)”，与前两者并列，不得改动前两者的 tab、API 与数据。
- 新增只读 API（GET，仅读本库），返回 JSON 契约如下（字段名、类型、单位写死；**`halted_until_utc` 允许 null 是明确例外，其余数值字段无数据时默认 0、字符串字段默认 ""，不返回缺失字段**）：
  - `/api/spot/summary`：`{"cash_usdt": number, "equity_usdt": number, "peak_equity_usdt": number, "drawdown_pct": number, "realized_pnl_usdt": number, "unrealized_pnl_usdt": number, "halted_until_utc": string|null, "last_successful_read_utc": string, "groups": [{"symbol": string, "status": string, "center_price": number, "open_layers": int}]}`。时间用 UTC ISO8601；金额单位 USDT，保留 2 位小数。`last_successful_read_utc` = 看板最后一次成功读到本库的时间，持久化在看板进程内存外的小文件 `data/spot_dashboard_last_read.txt`，进程重启后仍保留。
  - `/api/spot/positions`：`{"positions": [{"symbol": string, "layer_no": int, "trigger_price": number, "buy_price": number, "qty": number, "unrealized_pnl_usdt": number}]}`，按 symbol、layer_no 升序。
  - `/api/spot/trades?limit=50`：`{"trades": [...]}`，limit 默认 50、最大 100、最小 1，超出范围按边界截断；按 utc 降序；每条含 §6.1 trades 表的全部字段。
  - `/api/spot/regime`：`{"regime": [{"symbol": string, "adx": number, "atr_pct": number, "allow_new_group": bool, "event_in_24h": bool, "event_in_past_2h": bool}]}`。
- **同一快照（v2 新增）**：API 处理函数在一次只读事务内读全部表（一个 ro 连接、BEGIN DEFERRED 读事务），保证同一响应内的 cash、equity、positions 来自同一快照，不许逐表分开读。
- 看板不得提供任何交易操作按钮（不提供买入、卖出、加仓、清仓、改参数入口）。v2 看板只做展示。
- 看板读取本库时用只读连接（SQLite `mode=ro`），不得写本库。若读库失败（锁 / 文件不存在 / 字段损坏 / 版本不兼容），该 tab 显示“数据暂不可用（最后成功读取时间：<UTC 时间>）”，不影响 monitor 进程，反之亦然。
- 端口：不新增端口。复用 8081。不得占用 80/443/8080、3082、1080 等已用端口。

## 7. 异常处理与配置冻结值（全部分支必须写清）

| 异常 | 处理 | 日志级别 |
|---|---|---|
| Binance 公共行情 HTTP 超时（单次请求总耗时 >10 秒）/ 非 200 / 返回空 / serverTime 偏差 >60 秒 | 本轮跳过，不开新仓、不加仓；`price_error_streak` +1；已有持仓的单层/整组止盈跳过，halt 评估用上一轮有效价 | WARNING；连续 3 次记 ERROR |
| 价格跳变 >10%（与上一轮有效价比，`abs(last−prev)/prev > 0.10`，严格大于） | 按 §4.2 可疑价规则处理：本轮不用该价做 halt/止盈判断，改用上一轮有效价；计入 `suspect_price_streak` | WARNING；连续 3 次记 ERROR |
| K 线不足 500 根已收盘 | 禁止开新组，已有组只按 §5 卖出 | WARNING |
| `data/events.csv` 缺失 / 解析失败 / 过期 / 版本头缺失 / 乱序 / 重复 | 禁止开新组（fail-safe），已有组可卖出，记 ERROR；**每轮都检查**，不只在启动时检查。过期定义：文件最后一条事件时间 < 当前时间 + 90 天 | ERROR |
| SQLite 写失败 / 磁盘满 / 锁超时（>5 秒） | 立即停止下单，记 ERROR，进程退出码非 0，**不自动重启**（v2 无 watchdog） | ERROR |
| 配置值与冻结值表不相等 | 启动时校验失败，直接退出，不启动交易循环 | ERROR |
| 进程重启 | 从库恢复全部持久化状态（§6.1），先处理 halt 恢复顺序（§5.1），不重复建组、不重复执行 7 天减半 | INFO |
| 系统时间跳变（墙钟跳变绝对值 >60 秒） | 本轮跳过，用 UTC 时间记日志并记 WARNING；**向前跳 >60 秒时，本轮跳过 halt/时间止损的到期判断（延后一轮再判），不提前解除；向后跳不重新触发已过期的计时器。**冷静期仍为触发时刻 +24 小时（墙钟 UTC）。残余风险已写明：停机期间的跳变无法用单调时钟核对，重启后按墙钟重新算剩余时间。 | WARNING |
| 看板读库失败 | 看板该 tab 显示“数据暂不可用”，不影响 monitor 进程 | WARNING（看板侧） |
| Binance 返回 null / K 线乱序 / 重复时间戳 / 缺口 | 丢弃该轮 K 线数据，禁止开新组，已有组只按 §5 卖出，记 WARNING；K 线按时间升序去重，缺口超过 3 根 1h K 线记 ERROR | WARNING / ERROR |

### 7.1 冻结值表（启动时严格等于校验，任一不等于即退出）
| 参数 | 冻结值 | 出处 |
|---|---|---|
| EXECUTION_MODE | "DRY_RUN" | §1.1 |
| 初始本金 | 10,000 USDT | §2 |
| BTC 上限 / ETH 上限 / 合计上限 | 3,000 / 2,000 / 5,000 USDT | §2 |
| 层数上限 | 6 | §2 |
| 加仓倍数 | 1.3 | §2 |
| S | 12.75603 | §2 |
| BTC 间距系数 / 钳制 | 1.5×ATR，[0.8%, 3.0%] | §3.2 |
| ETH 间距系数 / 钳制 | 2.0×ATR，[1.0%, 4.0%] | §3.2 |
| 整组止盈 | +1.5% | §3.3 |
| ADX 开组上限 | < 25 | §4.1 |
| 停加仓价格 | < C×0.85 | §4.2 |
| 回撤 halt | ≥ 15% | §5.1 |
| 冷静期 | 24 小时 | §5.1 |
| 日亏损上限 | < −300 USDT | §5.2 |
| 时间止损 | 7 天减半 / 14 天清仓 | §5.3 |
| 手续费 / 滑点 | 0.10% / 0.02% | §1.3 |
| 轮询间隔 | 60 秒 | §1.1 |

## 8. 回测与验收

### 8.1 回测数据与撮合规则（v2 定死）
- 标的：BTCUSDT、ETHUSDT Binance 现货 1h K 线，2021-01-01 至 2026-10-31。
- 来源：Binance 公共 K 线（data-api.binance.vision），本地缓存为 CSV/Parquet，禁止在回测中调用实时行情。
- 事件：`data/events.csv` 中的 CPI、非农、FOMC 时间（UTC），回测中同样执行 §4 的过滤；**stale 判断按模拟时间算**（模拟时间 + 90 天），不用真实当前日期；回测报告记录 events.csv 的版本号与 sha256。
- **撮合规则：close-only。** 每根 1h K 线只在收盘时刻用 close 价判断一次买入 / 卖出，顺序为：先执行 halt 期满恢复，再判断 §5.1 回撤 halt，再判断整组止盈 / 时间止损 / 单层止盈（按 §5.0 优先级），最后判断买入（开组 / 加仓）。**不用 low/high，不猜测盘中先跌后涨的顺序。**回测粒度比实盘 60 秒轮询粗，这是一个已知的局限性，在回测报告中写明。
- 成本：与 §1.3 完全一致（last=close 价，手续费 0.10%，滑点 0.02% 单独扣现金）。

### 8.2 回测划分（防过拟合）与压力测试
- 训练：2021-01-01 至 2024-12-31（调参只许在这里）。
- 测试：2025-01-01 至 2026-10-31。
- **“测试集只跑一次”的适用范围：只指“用测试集调参的最终评估只跑一次”。**训练集回测、单元测试、压力测试、以及**不改参数、只修 bug 后的重跑**不受限。改参数后必须回 §2 重新冻结，算新一轮。
- 压力测试（v2 改为**确定性持仓测试**，不是策略模拟）：
  - **起始状态（三个子情形共用）**：两币各 6 层按触发价全买，d_BTC=1.5%、d_ETH=2.0% 固定（不算 ATR）。BTC 触发价：C、0.985C、0.97C、0.955C、0.94C、0.925C；ETH 触发价：C、0.98C、0.96C、0.94C、0.92C、0.90C。层名义按 §2。买入后现金 = 10,000 − 5,000 − 6.00（买入手续费与滑点）= 4,994 USDT。**不经过 §4.1 过滤、不经过 §4.2 停加仓、不重新开组；只算持仓盈亏与 halt。**
  - **价格路径**：720 个整点步（30 天），每步收盘价 = 起始价 × r^h，r = (1+总涨跌幅)^(1/720)。子情形：(a) BTC 单独，(b) ETH 单独，(c) BTC+ETH 同时。
  - 1. 合成 +50%：r = 1.5^(1/720)。预期：无 halt，期末权益 > 初始。
  - 2. 合成 −50%：r = 0.5^(1/720)。**Muse 复算（持仓到 0.5C 不卖）：BTC 亏 1,422.5、ETH 亏 929.6、双币合计亏 2,352.1。**但本测试逐小时算权益，halt 在回撤 ≥15% 时触发并平仓，不持有到 0.5C。双币情形 halt 触发点约在 0.66–0.67C（复算：0.70C 时权益约 8,701、回撤 12.99%；0.65C 时权益约 8,436、回撤 15.64%）。
  - 3. 合成先跌后涨：前 360 步 r1 = 0.7^(1/360)（累计 −30%），后 360 步 r2 = 1.2^(1/360)（累计 +20%）。
  - 4. 真实切片：2022-06-01 00:00 UTC 至 2022-06-30 23:00 UTC，BTCUSDT 与 ETHUSDT 1h 收盘价原样回放，策略正常跑（含 §4 过滤与 §5 风控）。

### 8.3 验收标准（全部满足才算通过，任一不满足即不通过）
1. 测试集扣成本后总盈亏 > 0。
2. 测试集最大净值回撤 ≤ 16.00%（按峰值算，在 1h 收盘价采样；**16.00% 是阈值参数，不是推导出的保证；同时报告两个数：收盘采样峰值回撤与强平落地后回撤，任一 >16.00% 即不通过**）。
3. 利润因子（总盈利 / 总亏损）≥ 1.20。**口径：按每笔 SELL 成交算**（每笔卖出的已实现盈亏，盈利的求和 / 亏损的求和取绝对值；已实现盈亏按 §1.4 含买卖双边成本）。若总亏损 = 0 且总盈利 > 0，利润因子记为 +∞，通过；若总盈利 = 0 且总亏损 = 0（无成交），利润因子未定义，**不通过**。
4. 盈利月占比 ≥ 60%。**口径：分母 = 测试集内有 ≥1 笔已平仓成交的自然月数；分子 = 其中已实现盈亏 > 0 的月数；盈亏 = 0 的月不计入分子。**
5. 压力测试 2（合成 −50%，确定性持仓测试）：**(c) 双币组合情形：halt 必须触发**（回撤达到 ≥15% 并执行平仓），且平仓落地后的最大回撤 ≤ 16.00%，且 halt 期间无新开仓、恢复后峰值权益已重置。**(a)(b) 单币情形：不要求 halt 触发**（单币满仓跌 50% 最多亏 BTC 1,422.5 / ETH 929.6，数学上触发不了 15% halt）；要求最大回撤 ≤ 16.00%，且无新开仓。
6. 单元测试全过：层金额求和 = 上限（BTC 3,000.00、ETH 2,000.00，误差 0.00）；层数上限 6 不可突破；回撤 15% 触发 halt 且恢复后不立即再触发（**含 halt 期间每轮不重触发**）；halt 清仓失败保持 HALTING 并续清；事件过滤在事件前 24h 内与事件后 2h 内禁止开组；重启恢复不重复建组、不重复执行 7 天减半；成本模型每笔单边 = 0.12%；realized_pnl 满足 §1.4 现金不变量；同轮跌破多层依次买齐；OPEN 层为 0 立即关组；上限拒买后不无限重试。
7. 回测结果可复现：同一数据 + 同一配置，两次运行结果完全一致（容差：总盈亏 ±0.01 USDT；比较未舍入值，显示值四舍五入到 2 位小数）。

### 8.4 模拟盘验收（回测通过后，另行冻结）
- v2 不定义模拟盘验收标准。回测通过后，由 Muse 另起 SPEC 补充模拟盘的运行时长、观察指标与上线标准，moka 批准后才长时间跑。
- 在模拟盘验收前，不得用真金、不得接 API Key、不得开 LIVE。

## 9. 待 moka / 盲审确认的开放问题（v2 明确标出，不许编程时自行定）
1. Binance 现货行情用 data-api.binance.vision 是否可达（oci2 上实测）？若不可达，备选是 api.binance.com 还是本地缓存 K 线先跑回测？
2. 手续费 0.10% + 滑点 0.02% 是否偏保守？Binance 现货实际费率以盲审核实为准，改动需回 SPEC。
3. 时间止损 7 天减半 / 14 天清仓、整组止盈 +1.5%，这三个数是 v2 暂定值，盲审可挑战，改动需重新冻结。
4. 看板集成是否只做只读展示（v2 定为只读）？若 moka 要在看板上加“暂停/恢复”按钮，需另起 SPEC。
5. 本项目与 perp-test 的关系：独立文件夹、独立库、独立进程，仅共享看板 8081 的展示层，不共享配置、DB 与执行器。
6. **冷静期 1 天是 moka 2026-11-06 11:57 定的。**若回测显示 1 天太短（恢复后立即再跌）或太长（错过反弹），改动需回 SPEC、重新冻结。
7. **浮亏停加仓规则已删除（v2）**：停加仓只靠 −15% 价格规则与 15% halt。若盲审认为需要独立的浮亏规则，需给出间距无关的定义（不能是固定 % of deployed），回 SPEC 改。

## 10. 安全与红线
- DRY_RUN only。代码中不得出现 Binance API Key / Secret 字段、不得读 `~/.config` 下的任何密钥文件、不得在日志中打印任何密钥。
- 不推 GitHub。除非 moka 明确下达。
- 不动线上：solana monitor（PID 1466554，2026-11-06 部署的 Config 1）、perp copier、Hermes 网关、agy remote-control、Caddy 配置。看板重启需 moka 批准。
- 模拟资金 10,000 USDT 是账面数字，不是真实入金。任何“充值 / 提现 / 转币”操作均不在 v2 范围内。
