# SPEC v5.3（草案，未冻结）— BTC/ETH 现货有限层数网格马丁（Binance Spot，模拟）
# 项目：/home/shenb/myQ/projects/spot-martingale/
# 状态：草案。v5.1 仍为冻结版（moka 2026-11-06 13:18 批准冻结，见 results/merged_review_v5.md；spec.md 仍为 v5.1，md5 caa3ad0d7c70d5d2cd7da899cd6b9404）。本草案取代未冻结的 v5.2 草案（v5.2 两份盲审均 FAIL，见 results/merged_review_v52.md），只补 GAP-1/GAP-2 的定义（见 v5.2 草案 → v5.3 草案变更日志），须经 agy + Codex 盲审、moka 批准后才冻结为 v5.3；冻结前不写业务代码、不动线上。
# 日期：2026-11-06
# v2 → v3 变更日志（每条对应 merged_review_v2.md §三的编号）：
#  V3-P0-1 HALTING/单事务/§7 三方冲突：改方案 (a)——halt 状态用独立事务先提交，逐层卖出每层一个事务并记 durable 进度，全部卖完再用最终事务置 HALTED；§5.0 每轮开头加 HALTING 守卫；§7 写失败分两种：正常操作写失败进程退出，halt 清仓中失败保持 HALTING、下一轮/重启后续清，连续 3 轮零进展则停交易等人工处理。删掉 v2 自相矛盾的“已提交的部分不回滚”一句。
#  V3-P0-2 单行 account 表存不了双币种状态：新建 symbol_state 表（symbol 主键，last_valid_price、last_valid_price_utc、price_error_streak、suspect_price_streak、recovery_streak），account 表只留账户级字段。
#  V3-P0-3 ticker/price 无 serverTime：每轮另调 GET /api/v3/time 取 serverTime，失败时读 ticker 响应的 HTTP Date 头，两者都拿不到则该轮价格无效。
#  V3-P0-4 可疑价漏禁买入 + 同向定义失效：可疑价当轮全流程用上一轮有效价（开组、加仓、halt、止盈一律不用可疑价）；接受条件改为第二轮与可疑价同向（相对上一轮有效价同为涨或同为跌）且 |P2−P1|/P1 ≤ 15%，或 K 线收盘价佐证 ≤5%；suspect_price_streak ≥3 与 price_error_streak ≥3 同级限制；price_error_streak 在任何一次成功读到有效价后即清零。
#  V3-P1-5 压力测试规则闭合：三个子情形起始状态分开写（双币现金 4,994；BTC 单独现金 6,996.40；ETH 单独现金 7,997.60）；C = 第 0 小时收盘价；压力测试中不执行 §3.3 止盈与 §5.3 时间止损，只执行 §5.1 halt；halt 触发 24 小时后执行 §5.0(a) 恢复并记录恢复后权益，恢复后不再开新组。
#  V3-P1-6 恢复轮重触发：恢复那一轮跳过 §5.1 检查，下一轮恢复正常判断。
#  V3-P1-7 时间跳变与恢复顺序：优先级定死——运行中进程相邻两轮墙钟跳变 >60 秒时，本轮跳过 halt/时间止损的到期判断（延后一轮）；重启后的间隔不适用跳变守卫，按 §5.0 先恢复顺序执行；account 表加 last_poll_utc，每轮结束时持久化。
#  V3-P1-8 close_reason 与 trades.reason 映射：trades.reason 枚举加 ALL_SOLD；逐笔 reason 映射定死；§5.1 强平时层置 HALT_CLOSED。
#  V3-P1-9 事件日历 sha256：加载时实算文件 sha256 并与版本头核对，不一致即按损坏处理（禁开新组、记 ERROR）；回测记录的哈希就是加载时核验过的哈希。
#  V3-P1-10 冻结值表补齐：全部硬编码阈值进表（最小下单量、窗口、事件窗口、ATR 倍数、0.85C、超时、锁、可疑价阈值、连续 3 次等）。
#  V3-P1-11 streak 清零规则：price_error_streak 在任何一次成功读到有效价后即清零（不限 <3 次）；suspect_price_streak ≥3 时与 price_error_streak ≥3 同级限制（停开仓/加仓）；recovery_streak 在任何一次失败后即清零；重启后 streak 从 symbol_state 表恢复，不清零。
#  V3-P1-12 看板 last_successful_read：路径写入 §6.2 结构；更新时点定死为读库事务成功且 HTTP 响应成功后，时间戳取读事务开始时间；声明它不是 DB 快照的一部分，是看板侧元数据。
#  V3-P2-13 last_equity 写入时点：每轮结束、全部决策做完后写入 last_equity_usdt + last_equity_utc；“午夜前最后一次记录”判定写清（last_equity_utc < 当日 00:00 UTC 且 day_start_date < 当日）；NULL 与首次运行处理写清。
#  V3-P2-14 §8.3(6) 单测补两项：停加仓后其余 PENDING 全置 CANCELLED 且不再触发；可疑价 >10% 当轮跳过止盈/halt 判断（用上一轮有效价）。

# v3 → v4 变更日志（只改小补丁，不动架构；每条对应 merged_review_v3.md §三的编号）：
#  V4-1 时间跳变检测公式是错的：跳变判定改为墙钟增量与单调时钟增量之差 |(now_utc − last_poll_utc) − Δmonotonic| > 60 秒；account 表加 last_poll_monotonic_s（单调时钟秒数，每轮结束时与 last_poll_utc 一起持久化）；重启后首轮无单调基线，守卫不适用。
#  V4-2 streak 清零自相矛盾：仅当 price_error_streak < 3 时读到正常有效价（跳变 ≤10%）立即清零；一旦 ≥3，必须等 recovery_streak ≥ 3 才清零两者；suspect_price_streak 为 1 或 2 且下一轮价格回到正常范围（相对 last_valid_price 跳变 ≤10%）时立即清零。
#  V4-3 可疑价状态机闭合：symbol_state 加 last_suspect_price 字段；接受条件 (a) 用持久化的 P1 算 |P2−P1|/P1，接受后新的上一轮有效价写死为 P2；K 线佐证 (b) 在可疑价当轮用最新已收盘 K 线 close 判断，当轮接受则下一轮起用新价，当轮决策仍用旧价；可疑价当轮禁止开组、禁止加仓、跳过止盈，仅估值与 halt 判断用上一轮有效价；连续可疑每轮对最近一个可疑价判断，接受一个即作废较早可疑价、suspect streak 清零。
#  V4-4 HALTING 关组事务归属与零进展处置：某组最后一层 OPEN 卖出的同一事务内将组置 CLOSED（close_reason=DRAWDOWN_HALT）并将组内剩余 PENDING 置 CANCELLED；双币同时持仓清仓顺序为 symbol 升序再 layer_no 升序；连续 3 轮零进展后 halt_state 保持 HALTING、每轮仍尝试续清，“停交易”指不做新交易、不指停止续清；人工处理定义为查日志后重启进程，重启后按 §5.0(0) 续清。
#  V4-5 压力测试 peak 基线：初始 peak_equity = 10,000 USDT（第 0 小时买入前的权益），6 USDT 买入手续费计入回撤基线；C = 第 0 小时收盘价，数值由路径生成器定，不在 SPEC 里硬编码。
#  V4-6 压力测试恢复适用范围：§8.3(5)“恢复后峰值已重置”仅适用于 (c) 双币 −50% 情形；(a)(b) 单币情形不要求 halt、不用验证恢复。
#  V4-7 跳变守卫与 HALTING 适用范围：HALTING 续清 (§5.0(0)) 不受跳变守卫影响（它不是到期判断），守卫只跳过到期判断（halt 期满、7d/14d）；向后跳表述统一为“向后跳不重新触发已过期的计时器，且本轮仍跳过到期判断”。
#  V4-8 冻结值表再补 5 项：连续零进展 3 轮、时间跳变 60 秒、压力测试 720 步 / 360+360 步、恢复步数 24 小时（24 个整点步）、验收最大回撤 16.00%。
#  V4-9 sha256 计算范围：对剔除第 1 行版本头后的文件正文（第 2 行起至文件末尾，UTF-8、LF）计算 sha256，生成端与校验端同口径。
#  V4-10 每轮单一估值价：每轮在轮询开始时固定一个估值价（接受的有效价，或可疑/无效时的上一轮有效价），本轮全部决策与写入共用该价。
#  V4-11 层金额舍入模式：round 为四舍五入到 2 位小数（half-up）；列表值是权威值，重算因舍入模式不同有出入时以列表为准。
#  V4-12 文字清理：删 §5.1 L181 残留“再做当轮回撤检查”；§6.1 last_equity 行自查：字节实为全角标点、无 U+FFFC/U+FFFD，v3 汇总所称乱码是显示 artifact，不改内容；§8.3(6) 可疑价单测口径改清（禁开组/加仓、跳过止盈，halt 判断仍执行、用上一轮有效价）。

# v4 → v5 变更日志（只改小澄清，不动架构；每条对应 merged_review_v4.md §二-2 的编号）：
#  V5-1 HALTING 零进展计数持久化：account 表加 halt_zero_progress_rounds（INTEGER，默认 0）；HALTING 轮未成功卖出任何一层时 +1，成功卖出任何一层时清零；满 3 轮记 ERROR 并停新交易；重启时从库恢复、不重置。
#  V5-2 单一估值价与无效价跳过分工：单价规则只约束“执行中的决策用哪个价”，跳过规则决定“哪些决策执行”；无效价轮：止盈跳过，halt 用固定价评估，时间止损（7d/14d）是时间到期型退出、仍按固定价评估并执行（卖出价 = 固定价）。
#  V5-3 可疑价读写顺序 + 条件 (a) 当轮生效：已有 last_suspect_price（P1）且新价 P2 跳变 >10% 时，先用 P1 判条件 (a)；通过则当轮即接受 P2 为有效价，正常参与本轮估值/决策；(a)(b) 都不满足才用 P2 覆盖 last_suspect_price 并按可疑价限制当轮交易。
#  V5-4 recovery_streak 边界：进入恢复计数期的条件是 price_error_streak ≥ 3 OR suspect_price_streak ≥ 3；恢复期内任何一轮未拿到“跳变 ≤10% 的正常有效价”（HTTP 失败或未接受的可疑价），recovery_streak 均清零；满 3 轮正常读价后同时清零两 streak 并将 last_suspect_price 置 NULL。
#  V5-5 sha256 字节规范：生成与校验同口径——先去文件首部 UTF-8 BOM（若有），正文 CRLF→LF 归一化；末尾换行原样保留（不增不删）；哈希算在归一化后的字节上。
#  V5-6 冻结值表补一行：恢复所需连续正常读价 3 次（§4.2）。
#  V5-7 措辞：§8.2 的“6 USDT 买入手续费”改成“买入总成本 6.00 USDT（手续费 5.00 + 滑点 1.00）”——数字不变，6.00 本来就是费 + 滑点的总额（Codex v4 的 4,993 指控是把滑点算了两次，已复算推翻）。

# v5 → v5.1 变更日志（微补丁，只补 merged_review_v5.md §二-3 的 2 个 P2；moka 2026-11-06 13:18 批准改完直接冻结，不再盲审）：
#  V5.1-1 recovery_streak 残留：接受可疑价导致脱离恢复期条件时（两 streak 均 < 3），recovery_streak 同步归零——只有“跳变 ≤10% 的正常有效价”才计入恢复，被接受的可疑价是有效新价、不是正常读价。
#  V5.1-2 零进展计数复位：进入 HALTING 的事务与最终 HALTED 事务中均显式置 halt_zero_progress_rounds = 0；触发 halt 时已无 OPEN 层，直接走完成事务置 HALTED，不进 HALTING 空转；ERROR 只在计数首次达到 3 时记一次（== 3），之后每轮不再重复记。

# v5.1 → v5.2 变更日志（只补 GAP-1/GAP-2 两处定义，不动任何策略参数与已冻结逻辑；对应 review_codex_v51_fix1.md §E 的两项 P1/P2）：
#  V5.2-1 GAP-1 条件 (b) 回测参考价（§8.1 新增）：回测执行 §4.2 价格异常状态机。close-only 回测中 ticker 价与 K 线 close 是同一数据源（当根 K 线 close），不存在独立的 K 线佐证源；条件 (b) 的“最近一根已收盘 1h K 线 close”定义为上一根 K 线的 close（prev_kline_close），回测每轮须显式传入 evaluate_symbol_price，不得留空（不得用 None 跳过）。可疑价当轮：上一根 close 若为正常价，则可疑价与它的偏差 >10% > 5%，条件 (b) 恒不成立；仅当连续可疑且 P2 与上一根可疑价偏差 ≤5% 时条件 (b) 才可能成立——此时 |P2−P1|/P1 ≤ 5% < 15% 且 P2 不可能越过 last_valid_price 变向，故条件 (a) 必然同时成立；因此条件 (b) 在回测中不改变任何接受结果，但须按定义完整执行，不得以“无影响”为由跳过。注意：不得用当根 close 作参考价（偏差恒为 0%，条件 (b) 恒成立，会废掉可疑价过滤）。
#  V5.2-2 GAP-2 离线 price_error_streak 触发源（§8.1 新增）：离线回测无 HTTP 状态码与 serverTime，price_error_streak 的触发源定义为：(i) 预期小时网格缺 bar——回测按预期小时网格逐轮推进（从首根 bar 的整点时刻到末根 bar 的整点时刻，步长 1 小时），网格内无 bar 的小时计一次价格错误（该轮不交易，halt 用上一轮有效价评估，price_error_streak +1）；(ii) bar 的 close 缺失、非数值或 ≤ 0，计一次价格错误。除此之外历史 CSV 一律视为有效价格源，不伪造 HTTP/serverTime 错误。回测报告须记录价格错误轮数与可疑价轮数（分币种），以便核验该规则是否实际触发。

# v5.2 草案 → v5.3 草案变更日志（v5.2 两份盲审均 FAIL，见 results/merged_review_v52.md；只改措辞与定义，不动任何策略参数与已冻结逻辑；上节的 V5.2-1/V5.2-2 两条定义被本节取代，以本节为准；每条对应 merged_review_v52.md §二的编号）：
#  V5.3-1 GAP-1 闭合：首根初始化 + 条件 (b) 参考价（M-1、M-2；§8.1 新增）：
#    (a) 首根初始化：回测首个有效 bar（last_valid_price 为 NULL 时）走初始化分支——将该 bar 的 close 写入 last_valid_price，last_suspect_price 置 NULL，两 streak 与 recovery_streak 归零，不进入跳变 >10% 分支，不读 prev_kline_close。
#    (b) 尚无有效价的轮次：若某币种尚未建立有效价（last_valid_price 仍为 NULL，如评估窗口首轮即缺 bar、或首根 bar 的 close 无效），该轮不估值、不交易、不做 halt 与时间止损判断，只计一次价格错误（price_error_streak +1），等下一网格小时。
#    (c) prev_kline_close 的定义：条件 (b) 的“最近一根已收盘 1h K 线 close”定义为该币种在当前轮之前最近一根已收盘有效 1h K 线的 close（有效 K 线见 V5.3-2(a)；可来自 start_dt 前的预热 K 线）。evaluate_symbol_price 的参考价参数不得设默认值，调用处每轮须显式传参；仅在尚无任何历史有效 K 线的初始轮次允许传 None。
#    (d) 证明（覆盖缺 bar/无效 close 分支）：在任意轮开始时，prev_kline_close 必等于 last_valid_price（V）或 last_suspect_price（P1）二者之一——每根有效 bar 的轮结束时，要么 V = 该 bar 的 close（正常有效轮、首根初始化、经条件 (a) 接受、经条件 (b) 接受），要么 P1 = 该 bar 的 close（未被接受的可疑轮）；价格错误轮既不改 V 也不改 P1，故缺口不破坏该性质。若 prev = V：条件 (b) 成立意味着 |P2−V|/V ≤ 5% < 10%，该轮根本不会进入跳变分支，(b) 在可疑轮中恒不成立；若 prev = P1：条件 (b) 成立意味着 |P2−P1|/P1 ≤ 5% < 15%，且同向论证（P1 > 1.10V 则 P2 ≥ 0.95P1 > 1.045V > V；P1 < 0.90V 则 P2 ≤ 1.05P1 < 0.945V < V）保证 (P2−V)(P1−V) > 0，故条件 (a) 必然同时成立；因 §4.2 先判 (a)、仅 not accepted_a 时才判 (b)，(b) 在回测中从不独立接受价格。但 (b) 须按定义完整执行、不得以“无影响”为由跳过：回测报告须记录 (b) 被评估与接受的轮数（预期独立接受为 0 轮），以此证明等价性而非假定等价性。注意：不得用当根 close 作参考价（偏差恒为 0%，条件 (b) 恒成立，会废掉可疑价过滤）。
#  V5.3-2 GAP-2 闭合：离线 price_error_streak 触发源与缺 bar 轮处理（M-3～M-6；§8.1 新增）：
#    (a) 有效 K 线字典：加载 CSV 时，仅保留 open_time 为 UTC 整点（open_time % 3600000 == 0）且 open/high/low/close 均为有限正数（> 0）的行作为有效 K 线；按 open_time 升序去重，重复时间戳只保留文件中首次出现的行并记 WARNING；无效 bar（close 缺失、非数值或 ≤ 0，或 open/high/low 无效，或 open_time 非整点）在加载时剔除，不纳入 §3.4 的 ATR/ADX/HH_480/LL_480 指标序列（指标仅在有效已收盘 K 线序列上连续计算），并在对应整点网格轮次计一次价格错误。
#    (b) 评估窗口整点网格：回测按各回测评估窗口的起止 UTC 整点 [start_dt, end_dt] 逐小时推进，步长 1 小时 = 3600 秒——训练集 2021-01-01 00:00 UTC 至 2024-12-31 23:00 UTC、测试集 2025-01-01 00:00 UTC 至 2026-10-31 23:00 UTC、压力测试 4 2022-06-01 00:00 UTC 至 2022-06-30 23:00 UTC；以 K 线 open_time 的 UTC 整点时刻匹配；预热期 K 线（start_dt 之前）仅用于预计算 §3.4 指标，不运行价格异常状态机与交易循环。在每个整点 t，对每个币种查 valid_bars[sym].get(t)：命中则传入 raw_price = 该 bar 的 close；未命中（无论原 CSV 缺该整点行，还是因 close 无效被过滤）则传入 raw_price = None，触发该币种 §4.2 价格错误分支。删除 v5.2 的“相邻 bar 间隔 > 3660 秒计一次错误”表述（与网格定义不等价：4 小时缺口按间隔只计 1 次，按网格计 4 次；Binance 1h K 线 open_time 严格对齐 UTC 整点，不需要 60 秒容差）。
#    (c) 与 §7 的关系：§7 的 K 线缺口规则（缺口超过 3 根 1h K 线记 ERROR）在回测中由本条网格机制承接——每个缺失的整点小时已计一次价格错误；连续缺失超过 3 个整点小时的缺口另记一次 ERROR（数据质量事件，按缺口计，不按小时计），不改变状态机行为。
#    (d) 缺 bar 轮处理：网格内无 bar 的小时，该币种按 §4.2 与 §1.4（V5-2）的单币无效价规则处理：price_error_streak +1，恢复期内则 recovery_streak 归零；该币种当轮估值价取 last_valid_price，禁止该币种开新组与加仓、跳过该币种的单层/整组止盈，但 §5.0(0) HALTING 续清、§5.0(a) 期满恢复、§5.1 halt 评估与 §5.3 的 7d/14d 时间止损仍按该固定估值价正常执行；双币独立判定（另一币种有有效价时照常交易）。连续缺失沿用最近有效价（last_valid_price 不变）。尚无有效价时按 V5.3-1(b) 处理。
#    (e) 回测报告计数口径（按 symbol × 预期小时轮统计，每轮每币种最多计一次价格错误；价格错误轮与可疑价轮互斥——同一轮同一币种要么无价（价格错误），要么有价进入跳变判断）：价格错误轮数 = 评估窗口内触发 price_error_streak += 1 的累计轮次数（分币种；另单列缺 bar 与 close 无效各自的轮数）；可疑价轮数 = 评估窗口内跳变 >10% 且未被条件 (a)/(b) 接受、实际触发 suspect_price_streak += 1 的累计轮次数（分币种；另单列经条件 (a) 接受的跳变轮数与经条件 (b) 评估并接受的跳变轮数，后者在回测中预期为 0）。
#  V5.3-3 其他（M-7、M-8）：
#    (a) §8.1 适用范围：§4.2 状态机适用于训练集、测试集及压力测试 4 策略回放；压力测试 1–3 为 §8.2 确定性持仓测试，仅执行 §5.1 halt。
#    (b) §9 第 8 条同步为 v5.3（冻结时生效）。

## 0. 目标与非目标

### 0.1 目标
在 BTCUSDT、ETHUSDT 现货上，用有限层数网格马丁做“经常小赚”，核心是活得久。
先证明可回测、有统计优势，再考虑模拟盘长时间跑。v3 阶段不碰实盘、不接实盘 API。

### 0.2 非目标（v3 明确不做）
- 不做合约、永续、杠杆、做空、资金费率套利。
- 不做黄金、SOL、Meme 币。
- 不做新闻抢单（1-10 秒 News Scalping）。新闻只做风控过滤（见 §4）。
- 不做自动实盘下单。v3 只有 DRY_RUN 模拟撮合。
- 不推 GitHub。线上代码只读。

### 0.3 成功标准
回测通过才算有资格进模拟盘；模拟盘通过才谈别的。标准见 §8。

## 1. 输入与输出

### 1.1 输入
| 输入 | 来源 | 频率 / 格式 |
|---|---|---|
| BTCUSDT、ETHUSDT 现货 last 价 | Binance Spot 公共行情 https://data-api.binance.vision ，GET /api/v3/ticker/price。无 API Key、无 Secret。**注意：该接口只返回 last 价，不含 bid/ask；v3 不用 bid/ask，不用 bookTicker。** | 轮询每 60 秒 |
| 服务器时间 | 同上，GET /api/v3/time（公共接口，无 Key），返回 serverTime（毫秒）。每轮调一次，用于 §4.2 的有效价校验。失败时改读 ticker 响应的 HTTP Date 头（见 §4.2）。 | 每轮一次 |
| 1h K 线（已收盘） | 同上，GET /api/v3/klines（interval=1h）。每次拉取最近 500 根已收盘 K 线（不含当前未收盘的那根）。 | 每轮拉取一次，缓存本地 |
| ATR(14)、ADX(14) | 由 1h 已收盘 K 线本地计算，定义见 §3.4 | 每轮计算一次 |
| 经济事件日历 | 本地文件 `data/events.csv`，字段：utc_time, event, country, impact；文件头第一行为版本行 `# version: YYYYMMDD, sha256: <值>`（建文件时写，改动即换版本）。初始覆盖 CPI、非农、FOMC 决议（2020-01-01 至 2026-12-31，UTC）。impact 取值只能是 HIGH / MEDIUM / LOW；“高影响”指 impact=HIGH。加载时校验：按 utc_time 升序、无重复 utc_time+event、无空字段，**并实算文件 sha256 与版本头核对**（见 §7；**计算范围（v5 补字节规范）：剔除第 1 行版本头后的文件正文，第 2 行起至文件末尾；归一化：先去文件首部 UTF-8 BOM（若有），正文 CRLF→LF；末尾换行原样保留（不增不删）；哈希算在归一化后的字节上；生成端与校验端同口径**）。 | 启动时加载，每轮检查 stale（见 §7） |
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

### 1.4 会计口径（全部写死）
- 现金 cash_usdt：买入时扣 成交名义 + 手续费 + 滑点；卖出时加 成交名义 − 手续费 − 滑点。
- 持仓成本 basis：某层成本 = qty × buy_price（**不含**手续费与滑点，手续费只走现金）。
- 持仓市值 market_value = Σ(OPEN 层 qty × 当前 last 价)。
- **账户权益 equity = cash_usdt + market_value。**
- 未实现盈亏 unrealized_pnl = Σ(OPEN 层 qty × (last 价 − buy_price))。
- 已实现盈亏 realized_pnl：某层卖出时记 (sell_price − buy_price) × qty **− 该层买入手续费 − 该层买入滑点 − 该笔卖出手续费 − 该笔卖出滑点**，累计进 account 表。买入手续费与滑点存在 layers 表的 buy_fee_usdt、buy_slippage_usdt 字段里，卖出时从该层记录取，不许重算。
- **现金不变量（单测必须断言）**：任意时刻，cash_usdt = initial_usdt + realized_pnl_usdt − Σ(OPEN 层 basis) − Σ(OPEN 层 buy_fee_usdt + buy_slippage_usdt)。违反此式即会计 bug。
- 估值价：权益、回撤、日亏损一律用 last 价（或上一轮有效价，见 §4.2 的可疑价规则），不用 bid/ask、不用 K 线收盘价。**每轮单一估值价（v4 新增，v5 补分工）**：每轮在轮询开始时固定一个估值价——接受的有效价，或当前价可疑/无效时的上一轮有效价；**本轮全部决策（halt 检查、止盈、时间止损）与写入（last_equity）共用该价**，不许中途换价。**分工（v5 定死）：单价规则只约束“执行中的决策用哪个价”，跳过规则（§4.2/§7）决定“哪些决策执行”。无效价轮：止盈跳过；halt 用固定价评估；时间止损（7d/14d）是时间到期型退出，仍按固定价评估并执行，卖出价 = 固定价。**

## 2. 资金与仓位（数字全部冻结在 v3，冻结值表见 §7）

- 模拟初始本金：10,000 USDT（与 perp-test 的 SIMULATION_INITIAL_BALANCE 一致，便于对照）。
- BTC 网格总上限：3,000 USDT（本金的 30%）。
- ETH 网格总上限：2,000 USDT（本金的 20%）。
- 两币合计已部署上限：**任何时候，Σ(全部 OPEN 层 basis) ≤ 5,000 USDT。**其中 5,000 = min(5,000, 初始权益 10,000 × 50%)，是**固定值**，不用当前权益算。动态保护只靠 §5.1 的 15% halt。
- **已部署的口径（v3 统一）**：已部署 = Σ(OPEN 层 basis)，**不含**手续费与滑点。手续费与滑点只走现金，不计入上限。
- 单币种同时只能有一个活跃网格组（grid_group）。一个组没关闭，不开第二个组。BTC 与 ETH 的组相互独立，但合计上限共同约束。
- 层数：每组最多 6 层。买满 6 层后禁止再加仓。
- 加仓倍数：1.3（固定，不是 2.0）。
- 层名义唯一公式：`layer_notional(n) = round(cap_usdt × 1.3^(n−1) / S, 2)`，其中 S = 1 + 1.3 + 1.3^2 + 1.3^3 + 1.3^4 + 1.3^5 = **12.75603**。
  - 第 6 层用残差法：`layer_notional(6) = cap_usdt − Σ(layer_notional(1..5))`，使 6 层合计**严格等于**上限。
  - **舍入模式（v4 新增，定死）**：`round` 指四舍五入到 2 位小数（half-up，0.005 进位）。**本节列出的 BTC/ETH 各层金额是权威值**；若按其他舍入模式重算与列表有出入，以列表为准，不许以重算值覆盖列表。
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
  - **上限拒买的处理**：第 n 层因违反 §2 上限被拒绝买入时，将该层置为 CANCELLED、将组的 `stop_adding` 置为 1、**并将组内其余 PENDING 层一并置为 CANCELLED**，只记一次 ERROR，之后不再重试该层。
  - **停加仓后高层处理**：§4.2 停加仓时，未买入的层全部置为 CANCELLED，之后不再触发。已买入的层不受影响。

### 3.3 卖出
- 每层独立止盈：Ln 的止盈价 = **上一层触发价**，即 n≥2 时 TP_n = C × (1 − (n−2) × d)，L1 的止盈价 = C × (1 + d)。当 last 价 `>=` 止盈价时，卖出该层全部数量。比较符为 `>=`，等于触发。
- 整组止盈：当 last 价 `>=` 加权平均成本 × 1.015 时，卖出组内全部剩余 OPEN 层，组关闭。其中加权平均成本 = Σ(OPEN 层 qty × buy_price) / Σ(OPEN 层 qty)，**只算剩余 OPEN 层，不含已 SOLD / TIME_STOPPED 的层，不含手续费**。
- **组关闭规则：组内 OPEN 层数量变为 0，且至少买入过 1 层时，立即将组标记为 CLOSED。**不许出现 0 持仓的 OPEN 组。组关闭时，组内剩余的 PENDING 层在**同一事务**内置为 CANCELLED。
- **close_reason 映射（定死）**：整组止盈 → TAKE_PROFIT；单层止盈卖空整组 → ALL_SOLD；7 天减半卖空整组 → TIME_STOP_7D（优先于 ALL_SOLD）；14 天清仓 → TIME_STOP_14D；回撤 halt → DRAWDOWN_HALT。
- **逐笔 trades.reason 映射（v3 新增，定死）**：开组买第 1 层 → GROUP_OPEN；加仓买入 → LAYER_BUY；单层止盈卖出 → LAYER_TP；整组止盈卖出（每层一笔）→ GROUP_TP；7 天减半卖出 → TIME_STOP_7D；14 天清仓卖出 → TIME_STOP_14D；回撤 halt 卖出 → DRAWDOWN_HALT。单层止盈卖空整组时，最后一笔 reason 仍为 LAYER_TP，组 close_reason 记 ALL_SOLD（组级原因与逐笔原因分开记，不强制一一对应）。
- 卖出价按 §1.3 的 last 价，手续费与滑点照计。7 天减半卖出的层状态记为 TIME_STOPPED，不是 SOLD。

### 3.4 指标定义（全部写死）
- 只用**已收盘**的 1h K 线，不用当前未收盘的那根。每次拉取最近 500 根已收盘 K 线；**不足 500 根时禁止开新组**，已有组只按 §5 卖出。
- ATR(14, 1h)：True Range = max(high − low, |high − prev_close|, |low − prev_close|)；**第 1 根 K 线没有 prev_close 时 TR = high − low**；ATR = 最近 14 个 TR 的简单平均（SMA）。**第一个 ATR(14) 值在第 14 根 K 线**。
- ADX(14, 1h)：Wilder 算法。+DM / −DM、TR、+DI / −DI、DX 均用 Wilder 平滑（α=1/14）。**种子：第一个 ADX 值 = 前 14 个 DX 值的算术平均，出现在第 28 根 K 线。**28 根只是**数学最小值**，不是交易阈值；**交易要求 500 根已收盘 K 线**。
- **区间窗口（定死）**：HH_480 / LL_480 取**最近 480 根已收盘 K 线（含上一根比较对象那根）**的最高价 / 最低价。§4.1.3 的“过去 20 日 ATR 中位数”取**同一批 480 根**已收盘 K 线对应的 480 个 ATR(14) 值的中位数；480 个为偶数，中位数取中间两个数的算术平均。

## 4. 市场状态过滤（只在震荡市开新仓）

### 4.1 允许开新组（开第 1 层）必须同时满足以下全部条件
1. ADX(14, 1h) < 25（严格小于，等于 25 不满足）。取上一根已收盘 K 线的 ADX 值。
2. 用最近 480 根已收盘 1h K 线（含上一根）算 HH_480 = 最高价、LL_480 = 最低价；要求**上一根已收盘 1h K 线的 close 严格满足 LL_480 < close < HH_480**。比较对象是上一根收盘价，不是轮询时刻实时价；比较符是严格 < 与 >，等于边界不满足。**注意：HH/LL 含比较对象那根，所以上一根收盘价若为 480 根内新高/新低，条件不满足（视为突破）。**
3. 当前 ATR(14, 1h) < 过去 20 日 ATR 中位数的 2.0 倍（严格小于，等于不满足）。中位数定义见 §3.4。
4. 未来 24 小时内**且过去 2 小时内**无 `data/events.csv` 中的高影响事件（impact=HIGH，CPI、非农、FOMC 决议）。事件时间用 UTC 比较；区间为 (now−2h, now+24h]，即过去 2 小时不含端点、未来 24 小时含端点。
5. 账户未触发 §5 的任何停手状态（halt 未到期、日亏损已触发）。
6. 该币种无活跃网格组，且买入第 1 层后仍满足 §2 合计上限。

以上任一不满足，禁止开新组，但不影响已有组的按 §5 卖出。

### 4.2 已有组的事中保护与价格源（v3 重写）
- 价格跌破中心价 −15%：当 last 价 `< C × 0.85`（严格小于）时，立即将该组的 `stop_adding` 置为 1 并持久化，停止该组继续加仓（未买的层全部置为 CANCELLED，见 §3.2），已有持仓继续按 §5 卖出。价格反弹回 0.85C 以上**不恢复**加仓。
- **浮亏停加仓规则已删除（v2 起）**：v0 的 800 USDT 与 v1 的 12%（按已部署成本）都是间距依赖的死条款。停加仓只靠 −15% 价格规则与 §5.1 的 15% halt。两家盲审均认可删除合理。
- **有效价格定义（v3，定死）**：一次价格读取为“有效”当且仅当 HTTP 200、价格 > 0、且服务器时间与本地 UTC 偏差 ≤ 60 秒。服务器时间来源按以下顺序：(1) 本轮 GET /api/v3/time 返回的 serverTime（公共接口，无 Key）；(2) 若 /api/v3/time 失败，读 ticker 响应的 HTTP Date 头，解析为 UTC 时间。两者都拿不到 → 该轮价格无效。**注意：GET /api/v3/ticker/price 只返回 symbol + price，不含 serverTime，不得假装它返回 serverTime。**
- **超时指单次请求总耗时 > 10 秒**（含等于 10 秒不算超时，>10 秒才算）。
- **可疑价（v4 重写，v5 补读写顺序）**：有效价较上一轮有效价跳变 `abs(last−prev)/prev > 0.10`（严格大于）时，记为可疑价，**并将该价存入 symbol_state 表的 `last_suspect_price` 字段**。**可疑价当轮禁止开组、禁止加仓、跳过止盈判断；仅估值与 halt 判断改用上一轮有效价**（halt 判断必须执行，不许以可疑价为由跳过，见 §5.1）。可疑价计入该币种的 `suspect_price_streak`（symbol_state 表，不计入 `price_error_streak`）。**读写顺序（v5 定死）：读到跳变 >10% 的新价 P2 且 `last_suspect_price` 已有值（P1）时，先用持久化的 P1 检验接受条件 (a)，再决定是否覆盖；不许先把 P2 存入再取值比较（那样会拿 P2 和自己比，恒通过）。仅当 (a) 与 (b) 均不满足时，才用 P2 覆盖 `last_suspect_price` 并按可疑价限制当轮交易。**
  - **接受条件（二选一）**：(a) 下一轮价格 P2 与可疑价 P1（**取 symbol_state 表持久化的 `last_suspect_price`，不是内存值**）**同向**——即 P2 与上一轮有效价之差、和 P1 与上一轮有效价之差同号（同为涨或同为跌），且 `|P2−P1|/P1 ≤ 15%`（允许继续同向移动，不要求横盘）；或 (b) **可疑价当轮**用最近一根已收盘 1h K 线的 close 佐证：若该 close 与可疑价偏差 ≤5%（**K 线佐证标准**），当轮即接受。**接受后新的上一轮有效价写死为 P2**（条件 (a) 时）或可疑价本身（条件 (b) 时当轮接受），不是过时的 P1；`suspect_price_streak` 清零，`last_suspect_price` 置 NULL。**注意时点（v5 补齐）：条件 (b) 只决定可疑价是否成为下一轮起的新有效价，可疑价当轮的决策仍用旧价（禁开组/加仓、跳过止盈、halt 用旧价），不存在“当轮立即用可疑价交易”；条件 (a) 在第 2 轮满足时则相反——P2 当轮即被接受为有效价，正常参与本轮估值与决策（开组/加仓/止盈均可按 P2 执行）。v2 的“第二轮与可疑价偏差 ≤5%”是错的：真实连续暴跌 −12% 再 −10%，第二轮偏离可疑价 10% > 5%，反而永远不被接受，已删除。**
  - **连续可疑的“上一轮有效价”**：始终指最近一次被接受的有效价，不因可疑轮次而更新；多轮可疑期间估值一直用它。**连续可疑期间每轮都对最近一个可疑价判断**（即当前轮的 `last_suspect_price`）；**接受一个可疑价即作废较早的全部可疑价**，`suspect_price_streak` 清零、`last_suspect_price` 置 NULL。
- **异常计数与清零（v3 定死，按币种独立，symbol_state 表）**：
  - `price_error_streak`：连续拿不到有效价格的轮数。**清零规则（v4 重写，修 v3 自相矛盾）**：仅当 streak < 3 时，读到一次正常有效价（跳变 ≤10%）即立即清零；**一旦 streak ≥ 3，读到有效价不再直接清零，而是计入 `recovery_streak`，必须等 recovery_streak ≥ 3 才同时清零 `price_error_streak` 与 `suspect_price_streak`**。连续 3 次拿不到有效价时，停止该币种开新仓与加仓；已有持仓的单层/整组止盈跳过（无有效价可判断），halt 评估改用上一轮有效价。
  - `suspect_price_streak`：连续可疑价的轮数。**≥3 次时与 price_error_streak ≥3 同级限制**：停止该币种开新仓与加仓，已有持仓止盈跳过、halt 评估用上一轮有效价。接受为新有效价后清零。**为 1 或 2 时，若下一轮价格回到正常范围（相对 `last_valid_price` 跳变 ≤10%），立即清零**（不用等 recovery_streak ≥ 3）。
  - `recovery_streak`：异常后连续成功读到有效价的轮数。**进入恢复计数期的条件（v5 定死）：`price_error_streak ≥ 3 OR suspect_price_streak ≥ 3`，任一成立即进入。恢复期内任何一轮未拿到“跳变 ≤10% 的正常有效价”——无论 HTTP 失败还是读到未被接受的可疑价——`recovery_streak` 均清零**（未被接受的可疑价不算失败，也不算正常读价，但同样打断连续计数）。恢复条件：连续 3 次轮询都拿到有效价格且跳变 ≤10%（即 recovery_streak ≥ 3），`price_error_streak` 与 `suspect_price_streak` 清零，`last_suspect_price` 置 NULL，恢复开新仓资格（仍需满足 §4.1）。**脱离恢复期同步归零（v5.1 定死）：恢复期内通过条件 (a)/(b) 接受可疑价导致两 streak 均 < 3、脱离恢复期条件时，`recovery_streak` 同步归零——被接受的可疑价是有效新价、不是“跳变 ≤10% 的正常有效价”，不计入恢复，残留值不许带到下一次恢复期。****只有进入恢复计数期后的正常读价才计入 recovery_streak；未进入恢复期时（两 streak 均 < 3）的正常读价直接清零对应 streak，不计 recovery_streak。**
  - 重启后 streak 从 symbol_state 表恢复，不清零；双币种各自独立计数，一币异常不影响另一币。

## 5. 风控与退出（硬线）

### 5.0 每轮处理顺序（定死）
每轮按以下顺序执行，**第 (0) 步是 v3 新增的 HALTING 守卫**：
- **(0) 若 `halt_state = 'HALTING'`**：先续清剩余持仓（按 §5.1 的逐层卖出），跳过本轮其他全部判断。**零进展计数（v5 定死）**：本轮未成功卖出任何一层时 `halt_zero_progress_rounds += 1`，成功卖出任何一层时清零；计数值达到 3 时，记 ERROR 并停止交易（进程保持运行、不做任何新交易），等人工处理。**“停止交易”指不做新交易（不开组、不加仓、不执行止盈/时间止损），不是停止续清：halt_state 保持 HALTING，此后每轮仍按本步尝试续清。人工处理定义为：操作员查日志确认原因后重启进程，重启后按 §5.0(0) 续清（见 §5.1）。**
- (a) halt 期满恢复：若 `halt_active=1` 且 `now >= halted_until_utc`，重置 peak_equity 为**本轮估值价算出的当前权益**，清除 halt 状态（halt_active 置 0、halt_state 置 NONE、halted_until_utc 置 NULL）。**恢复那一轮跳过 §5.1 检查，下一轮恢复正常判断（v3 新增，防恢复轮用不同价格重置与检查导致的立即再触发）。**
- (b) 判断 §5.1 回撤 halt（仅当 halt_active=0 且 halt_state=NONE 时）。
- (c) 按以下优先级执行组内退出。**halt 期间（halt_active=1 且未到期）跳过 (b)(c) 的全部交易判断，只做估值与日志。**
- 同一轮、同一组内若多个退出条件同时满足，按以下顺序只执行第一个，不许重复卖出：
  1. §5.1 回撤 halt（最高）
  2. §5.3 满 14 天时间止损（全清）
  3. §3.3 整组止盈 +1.5%
  4. §5.3 满 7 天时间止损（减半，只执行一次）
  5. §3.3 单层止盈。**优先级 5 在同轮内有多层同时达到止盈价时，卖出全部达标层，不是每轮只卖一层。**
- 日亏损（§5.2）不触发卖出，只禁止新买入 / 加仓，不参与本优先级。
- **时间跳变守卫的优先级（v4 重写，修 v3 检测公式）**：跳变判定 = `|(now_utc − last_poll_utc) − Δmonotonic| > 60 秒`，其中 Δmonotonic 为同一两轮之间单调时钟走过的秒数（account 表 `last_poll_monotonic_s`，每轮结束时与 `last_poll_utc` 一起持久化）。**v3 的“相邻两轮墙钟跳变 >60 秒”是错的：轮询间隔本身就是 60 秒，`now_utc − last_poll_utc` 正常恒 >60 秒，按字面实现每一轮都会误判跳变。**判定为跳变时，本轮跳过 halt/时间止损的到期判断（延后一轮再判），即本轮不执行 (a) 中的期满恢复与 §5.3 的到期判断；其他判断照常。**HALTING 续清（本节 (0)）不受跳变守卫影响**（它不是到期判断，每轮照常执行）。**重启后的间隔不适用跳变守卫**：进程启动时无单调基线，不比较，直接按 §5.0 顺序执行（含先恢复）。**向后跳不重新触发已过期的计时器，且本轮仍跳过到期判断。**

### 5.1 账户级最大回撤 15%（moka 已定）
- 定义：峰值权益 peak_equity = 自账户创建以来账户权益（§1.4）的历史最高值，持久化在 account 表，**只升不降**，重启后从库恢复，不重置。初始值 = 10,000 USDT。
- 当前回撤 = (peak_equity − equity) / peak_equity。
- **halt 状态机（v3 重写，修 v2 自相矛盾）**：account 表有 `halt_active`（0/1）、`halt_state`（NONE/HALTING/HALTED）、`halt_triggered_utc`、`halt_progress_sold_layers`、`halt_progress_total_layers`。未触发时 halt_active=0、halt_state=NONE。
  - **触发（halt_active=0 且 halt_state=NONE 且当前回撤 `>= 0.15`，含等于）**：先在**独立事务**里置 halt_state=HALTING、halt_triggered_utc=now、halt_progress_total_layers=全部 OPEN 层数、halt_progress_sold_layers=0、halt_zero_progress_rounds=0，并提交。**这个状态提交与后面的卖出不在同一个事务里，所以中途失败不会把它回滚掉。**若触发时已无 OPEN 层（halt_progress_total_layers=0），不进入 HALTING，直接走“完成”事务置 HALTED（见下），不空转攒零进展轮数。
  - **逐层卖出**：清仓遍历顺序定死——**symbol 升序（BTCUSDT 在前，ETHUSDT 在后），同一币种内 layer_no 升序**。按此顺序逐层卖出，每卖一层用**一个独立事务**完成（现金更新 + 该层状态置 HALT_CLOSED + trades 行 reason=DRAWDOWN_HALT + halt_progress_sold_layers += 1）。**某组最后一层 OPEN 卖出的同一事务内，将该组置为 CLOSED（close_reason=DRAWDOWN_HALT）并将组内剩余 PENDING 层一并置为 CANCELLED**（不许留 0 持仓的 OPEN 组，也不许把关组推迟到最终事务）。某层卖出事务失败时，halt_state 保持 HALTING、进度保持已卖层数，记 ERROR，下一轮（或重启后）按 §5.0(0) 续清剩余层。**v2 的“整个 halt 清仓在一个事务里 + 失败下一轮续清”是自相矛盾的，已删除；v3 没有“已提交的部分不回滚”这种写法。**
  - **完成**：全部 OPEN 层卖完后，在最终事务里置 halt_state=HALTED、halt_active=1、halted_until_utc = halt_triggered_utc + **24 小时（1 天，moka 2026-11-06 11:57 定）**、halt_trigger_equity = 平仓后权益、halt_zero_progress_rounds=0。
  - **连续 3 轮零进展**：§5.0(0) 续清时 `halt_zero_progress_rounds` 首次达到 3（`== 3`，见 §5.0(0) 的计数规则），记 ERROR 一次并停止交易（进程保持运行、不做任何新交易），等人工处理。**ERROR 只在首次到 3 时记一次，之后每轮计数继续 +1 但不再重复记 ERROR**（“停止新交易”状态本身持续有效）。**“停止交易”指不做新交易，halt_state 保持 HALTING、此后每轮仍按 §5.0(0) 尝试续清；人工处理 = 操作员查日志确认原因后重启进程，重启后按 §5.0(0) 续清。**
- 恢复：见 §5.0(a)。恢复后开新组仍须满足 §4.1 全部条件。不自动恢复旧组。
- **重启恢复顺序（定死）**：进程启动时先查 halt_state：若为 HALTING，先续清剩余持仓（§5.0(0)），再判断恢复；若 halt_active=1 且 halted_until_utc 已过，执行恢复（重置峰值、清除 halt）。**恢复那一轮跳过 §5.1 检查（见 §5.0(a)）。不许先按旧峰值检查再恢复。**
- 闪崩例外：halt 评估用上一轮有效价（见 §4.2 可疑价规则），不许用可疑价直接强平，也不许以“可疑价格”为由跳过 halt 评估。

### 5.2 每日亏损上限 3%
- 定义：`day_start_equity` = 当日 UTC 00:00 时的账户权益，持久化在 account 表（字段 `day_start_equity_usdt`、`day_start_date_utc`）。
- **跨日规则**：每轮持久化 `last_equity_usdt` 与 `last_equity_utc`（写入时点见 §6.1：每轮结束、全部决策做完后写入，用该轮估值价算出的权益）。每天第一次轮询时若日期已变，`day_start_equity` = **午夜前最后一次记录的 last_equity_usdt**。判定：`last_equity_utc < 当日 00:00 UTC 且 day_start_date_utc < 当日` 时用该值；若 `last_equity_utc >= 当日 00:00 UTC`，说明 day_start 已在当日写过，不覆盖。若 `last_equity_utc` 为 NULL（首次运行）或无午夜前记录（长时间停机），day_start_equity = 首次轮询时的当前权益，并记 WARNING（当日风控为近似值，日亏损可能被低估）。
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
- 价格在 60 秒内跳变 >10% 时按 §4.2 可疑价规则处理：本轮禁止开组/加仓、跳过止盈，估值与 halt 判断用上一轮有效价；真实闪崩时 halt 最多延迟一轮（60 秒）。
- 回测中 1h K 线内先跌后涨或先涨后跌的顺序问题见 §8.1（close-only，不用 low/high）。

## 6. 状态、持久化与看板

### 6.1 持久化（SQLite，独立库）
- 库路径：`/home/shenb/myQ/projects/spot-martingale/data/spot_martingale.sqlite3`（与 solana 的 monitor.sqlite3、perp 的 copier.sqlite3 分开，互不读写）。
- 表（v3，字段缺一不可）：
  - `account(id=1, cash_usdt, initial_usdt=10000, peak_equity_usdt, halted_until_utc, halt_trigger_equity_usdt, halt_triggered_utc, halt_active, halt_state, halt_progress_sold_layers, halt_progress_total_layers, halt_zero_progress_rounds, day_start_equity_usdt, day_start_date_utc, last_equity_usdt, last_equity_utc, last_poll_utc, last_poll_monotonic_s, realized_pnl_usdt, updated_utc)`。其中 id=1 是唯一行，建库时初始化：cash=10000、peak_equity=10000、day_start_equity=10000、halt_active=0、halt_state='NONE'、halt_progress_sold_layers=0、halt_progress_total_layers=0、halt_zero_progress_rounds=0、其余为 NULL 或 0。`halt_zero_progress_rounds`（v5 新增）：HALTING 续清中连续零进展的轮数，见 §5.0(0) 与 §5.1。`last_poll_monotonic_s` 为单调时钟秒数（REAL），每轮结束时与 `last_poll_utc` 一起写入，供 §5.0 跳变守卫算 Δmonotonic；重启后首轮无基线，守卫不适用。`halt_state` 取值只能是 NONE/HALTING/HALTED；清除 halt 时 halted_until_utc 置 NULL（这是规定的清除编码）。**account 表只存账户级字段，不存按币种的价格与计数（v2 的 last_valid_price 等单值字段已移出，见 symbol_state 表）。**
  - `symbol_state(symbol, last_valid_price, last_valid_price_utc, last_suspect_price, price_error_streak, suspect_price_streak, recovery_streak)`。`symbol` 主键，取值为 BTCUSDT / ETHUSDT；建库时两行初始化：last_valid_price=NULL、last_suspect_price=NULL，其余为 0。**双币种各自独立的价格与异常计数存在这里，不存在 account 表里。**`last_suspect_price` 存最近一个可疑价（见 §4.2 接受条件 (a) 的 |P2−P1|/P1 计算），接受后置 NULL。
  - `grid_groups(id, symbol, center_price, spacing_pct, atr_pct, status[OPEN/CLOSED], opened_utc, first_buy_utc, closed_utc, close_reason[TAKE_PROFIT/ALL_SOLD/TIME_STOP_7D/TIME_STOP_14D/DRAWDOWN_HALT], stop_adding, half_time_stopped)`。**组状态只有 OPEN / CLOSED，没有 HALTED；halt 时组直接置为 CLOSED，close_reason 记 DRAWDOWN_HALT，halt 状态只存在 account 表里。** `stop_adding`、`half_time_stopped` 为 0/1 整数，默认 0。`id` 唯一，`symbol + status=OPEN` 唯一（同一币种同时最多一个 OPEN 组）。
  - `layers(id, group_id, layer_no, trigger_price, buy_price, qty, notional_usdt, buy_fee_usdt, buy_slippage_usdt, status[PENDING/OPEN/SOLD/TIME_STOPPED/CANCELLED/HALT_CLOSED], bought_utc, sold_utc, sell_price)`。PENDING = 尚未买入；CANCELLED = 因停加仓 / 上限拒买 / 组关闭作废；HALT_CLOSED = 因回撤强平卖出；TIME_STOPPED = 7 天减半卖出。`(group_id, layer_no)` 唯一。
  - `trades(id, utc, symbol, side, price, qty, notional_usdt, fee_usdt, slippage_usdt, group_id, layer_no, reason)`。`reason` 枚举：GROUP_OPEN / LAYER_BUY / LAYER_TP / GROUP_TP / ALL_SOLD / TIME_STOP_7D / TIME_STOP_14D / DRAWDOWN_HALT。`id` 自增唯一。
  - `daily_summary(date_utc, realized_pnl, unrealized_pnl, equity, drawdown_pct, trades_count)`。`date_utc` 为 UTC 日期（YYYY-MM-DD），唯一。
  - `risk_events(id, utc, type, detail)`（类型：DRAWDOWN_15 / DAILY_LOSS_3 / REGIME_BLOCK / PRICE_STALE / LAYER_CAP / STOP_ADDING / SUSPECT_PRICE）。
  - `regime_log(utc, symbol, adx, atr_pct, in_range, event_in_24h, event_in_past_2h, allow_new_group)`。
- 原子性：每笔模拟成交 + 现金更新 + 层状态更新 + 组状态更新必须在**同一个 SQLite 事务**里完成，任一步失败则整个事务回滚，不许出现“钱扣了、层没记”或“层卖了、钱没加”的中间态。**例外只有 halt 清仓（见 §5.1）：halt_state 先独立事务提交，逐层卖出每层一个事务，全部卖完最终事务置 HALTED。**
- **last_equity 写入时点（v4 修）**：`last_equity_usdt` 与 `last_equity_utc` 在**每轮结束、全部决策做完后**写入，用该轮估值价算出的权益；`last_poll_utc` 与 `last_poll_monotonic_s` 同一时刻写入。跳过的轮次（时间跳变守卫跳过到期判断那轮）仍写入 last_equity 与 last_poll_utc（估值照常做，只是到期判断跳过）。**写入用的权益按 §1.4 每轮单一估值价算出。**
- 重启幂等：启动时从库里恢复 OPEN 的组与层、`stop_adding`、`half_time_stopped`、`peak_equity`、`day_start_equity`、`halt_active`、`halt_state`、`halted_until_utc`、`halt_zero_progress_rounds`（不断续，不重置）、`last_valid_price`（按币种）、`last_suspect_price`（按币种）、各 streak、`last_equity_usdt`、`last_poll_utc`、`last_poll_monotonic_s`，不重复建组、不重复下单、不重复执行 7 天减半。库文件不存在时允许首次建库（按本节初始化）；库文件存在但打不开 / 校验失败（SQLite header 损坏）时，禁止交易，只记 ERROR 并退出（不自动建新库覆盖旧库）。

### 6.2 进程与文件结构（独立项目文件夹）
```
/home/shenb/myQ/projects/spot-martingale/
  spec.md                 # 本文件（冻结版）
  spec_v0.md              # v0 备份，不许改
  spec_v1.md              # v1 备份，不许改
  spec_v2.md              # v2 备份，不许改
  config.py               # 全部阈值，见 §2-§5 与 §7 冻结值表；EXECUTION_MODE="DRY_RUN" 常量
  monitor.py              # 轮询主循环（60 秒），只读行情、写本库
  backtest.py             # 离线回测（见 §8），不连实时行情
  data/events.csv         # 经济事件日历（含版本头）
  data/spot_martingale.sqlite3
  data/spot_dashboard_last_read.txt   # 看板最后成功读取时间（见 §6.3）
  tests/test_*.py         # 单元测试，见 §8.3
  logs/
  results/                # 回测结果 CSV / 报告、评审文件
```
- 进程名：`spot-martingale-monitor`，与 `solana-wallet-monitor`、`perp` 进程隔离，不得共用进程、不得互相 kill / restart。
- **watchdog：v3 没有 watchdog，没有 systemd service。** monitor 进程退出后保持退出状态，等人工处理，不自动重启。**但 §5.1 的 HALTING 续清不受此限：进程重启（人工重启）后先执行 §5.0(0) 续清。**
- 线上代码只读：不得修改 `/home/shenb/myQ/projects/solana-wallet-monitor/monitor.py`、`/home/shenb/myQ/projects/perp-wallet-copier/` 下的任何文件。看板集成只改 `web_dashboard.py` 的只读展示部分（见 §6.3），且改动前备份、改动后由 Codex 审、moka 批准后才重启看板。

### 6.3 看板集成（同一个 web 看板，8081）
- 看板：现有 `web_dashboard.py`（8081，[dashboard-host-redacted]），已有两个顶级应用：Solana（紫）、Perp / Binance 跟单（amber，currentApp='perp'）。
- 本项目新增第三个顶级应用：`currentApp='spot'`，标题“现货马丁 (Binance Spot · 模拟)”，与前两者并列，不得改动前两者的 tab、API 与数据。
- 新增只读 API（GET，仅读本库），返回 JSON 契约如下（字段名、类型、单位写死；**`halted_until_utc` 允许 null 是明确例外，其余数值字段无数据时默认 0、字符串字段默认 ""，不返回缺失字段**）：
  - `/api/spot/summary`：`{"cash_usdt": number, "equity_usdt": number, "peak_equity_usdt": number, "drawdown_pct": number, "realized_pnl_usdt": number, "unrealized_pnl_usdt": number, "halted_until_utc": string|null, "last_successful_read_utc": string, "groups": [{"symbol": string, "status": string, "center_price": number, "open_layers": int}]}`。时间用 UTC ISO8601；金额单位 USDT，保留 2 位小数。
  - `/api/spot/positions`：`{"positions": [{"symbol": string, "layer_no": int, "trigger_price": number, "buy_price": number, "qty": number, "unrealized_pnl_usdt": number}]}`，按 symbol、layer_no 升序。
  - `/api/spot/trades?limit=50`：`{"trades": [...]}`，limit 默认 50、最大 100、最小 1，超出范围按边界截断；按 utc 降序；每条含 §6.1 trades 表的全部字段。
  - `/api/spot/regime`：`{"regime": [{"symbol": string, "adx": number, "atr_pct": number, "allow_new_group": bool, "event_in_24h": bool, "event_in_past_2h": bool}]}`。
- **同一快照**：API 处理函数在一次只读事务内读全部表（一个 ro 连接、BEGIN DEFERRED 读事务），保证同一响应内的 cash、equity、positions 来自同一快照，不许逐表分开读。**快照范围**：各端点只读自己需要的表，但同一端点内的多表读必须在同一次读事务内；不同端点之间不保证同一快照。
- **`last_successful_read_utc`（v3 定死）**：存放在 `data/spot_dashboard_last_read.txt`（路径已写入 §6.2 结构）。更新时点：**读库事务成功且 HTTP 响应成功发出后**，写入该次读事务开始时间的 UTC ISO8601。**它不是 DB 快照的一部分**，是看板侧元数据，不参与 §6.1 的事务，不保证与 summary 的其他字段同一时刻。
- 看板不得提供任何交易操作按钮（不提供买入、卖出、加仓、清仓、改参数入口）。v3 看板只做展示。
- 看板读取本库时用只读连接（SQLite `mode=ro`），不得写本库。若读库失败（锁 / 文件不存在 / 字段损坏 / 版本不兼容），该 tab 显示“数据暂不可用（最后成功读取时间：<UTC 时间>）”，不影响 monitor 进程，反之亦然。
- 端口：不新增端口。复用 8081。不得占用 80/443/8080、3082、1080 等已用端口。

## 7. 异常处理与配置冻结值（全部分支必须写清）

| 异常 | 处理 | 日志级别 |
|---|---|---|
| Binance 公共行情 HTTP 超时（单次请求总耗时 >10 秒）/ 非 200 / 返回空 / 服务器时间拿不到 | 本轮该币种跳过，不开新仓、不加仓；该币种 `price_error_streak` +1（symbol_state 表）；已有持仓的单层/整组止盈跳过，halt 评估用上一轮有效价 | WARNING；连续 3 次记 ERROR |
| 价格跳变 >10%（与上一轮有效价比，`abs(last−prev)/prev > 0.10`，严格大于） | 按 §4.2 可疑价规则处理：本轮禁止开组/加仓、跳过止盈，估值与 halt 判断用上一轮有效价；计入该币种 `suspect_price_streak` | WARNING；连续 3 次记 ERROR |
| K 线不足 500 根已收盘 | 禁止开新组，已有组只按 §5 卖出 | WARNING |
| `data/events.csv` 缺失 / 解析失败 / 过期 / 版本头缺失 / 乱序 / 重复 / **sha256 与版本头不符** | 禁止开新组（fail-safe），已有组可卖出，记 ERROR；**每轮都检查**，不只在启动时检查。过期定义：文件最后一条事件时间 < 当前时间 + 90 天。**sha256 校验：加载时实算文件内容的 sha256，与版本头 `# version: YYYYMMDD, sha256: <值>` 的值比对，不一致即按损坏处理。计算范围（v5 补字节规范）：剔除第 1 行版本头后的文件正文，第 2 行起至文件末尾；归一化：先去文件首部 UTF-8 BOM（若有），正文 CRLF→LF；末尾换行原样保留（不增不删）；哈希算在归一化后的字节上；生成端与校验端同口径。** | ERROR |
| SQLite 写失败 / 磁盘满 / 锁超时（>5 秒，**锁等待从 BEGIN 到获得锁的总耗时**） | **分两种（v3 新增）**：(a) 正常操作（开组、加仓、止盈、时间止损）：立即停止下单，记 ERROR，进程退出码非 0，**不自动重启**（v3 无 watchdog）；(b) halt 清仓中（halt_state=HALTING）：保持 HALTING 与进度，记 ERROR，下一轮（§5.0(0)）或重启后续清；`halt_zero_progress_rounds` 达到 3 则停新交易等人工处理，halt_state 保持 HALTING、每轮仍尝试续清（见 §5.1）。 | ERROR |
| 配置值与冻结值表不相等 | 启动时校验失败，直接退出，不启动交易循环 | ERROR |
| 进程重启 | 从库恢复全部持久化状态（§6.1），先处理 HALTING 续清与 halt 恢复顺序（§5.0/§5.1），不重复建组、不重复执行 7 天减半 | INFO |
| 系统时间跳变（跳变判定 `|(now_utc − last_poll_utc) − Δmonotonic| > 60 秒`，见 §5.0） | 本轮跳过到期判断，用 UTC 时间记日志并记 WARNING；**向前跳时，本轮跳过 halt/时间止损的到期判断（延后一轮再判），不提前解除；向后跳不重新触发已过期的计时器，且本轮仍跳过到期判断。HALTING 续清 (§5.0(0)) 不受本守卫影响。重启后的间隔不适用本守卫。**冷静期仍为触发时刻 +24 小时（墙钟 UTC）。残余风险已写明：停机期间的跳变无法用单调时钟核对，重启后按墙钟重新算剩余时间。 | WARNING |
| 看板读库失败 | 看板该 tab 显示“数据暂不可用”，不影响 monitor 进程 | WARNING（看板侧） |
| Binance 返回 null / K 线乱序 / 重复时间戳 / 缺口 | 丢弃该轮 K 线数据，禁止开新组，已有组只按 §5 卖出，记 WARNING；K 线按时间升序去重，缺口超过 3 根 1h K 线记 ERROR | WARNING / ERROR |

### 7.1 冻结值表（启动时严格等于校验，任一不等于即退出；v3 已补齐全部硬编码阈值）
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
| ATR 中位数倍数 | < 2.0 倍 | §4.1 |
| 区间窗口 / K 线门槛 | 480 根 / 500 根 | §3.4 |
| ATR 首值 / ADX 首值 | 第 14 根 / 第 28 根 | §3.4 |
| 事件窗口 | (now−2h, now+24h] | §4.1 |
| 事件 stale | 最后一条事件 < 当前时间 + 90 天 | §7 |
| 停加仓价格 | < C×0.85 | §4.2 |
| 服务器时间偏差 | ≤ 60 秒 | §4.2 |
| 可疑价跳变 / 佐证 / 接受 | >10% / K 线 close 偏差 ≤5% / 同向且 \|P2−P1\|/P1 ≤ 15% | §4.2 |
| 异常连续次数 | 3 次 | §4.2 |
| 恢复所需连续正常读价 | 3 次 | §4.2 |
| 回撤 halt | ≥ 15% | §5.1 |
| 冷静期 | 24 小时 | §5.1 |
| 日亏损上限 | < −300 USDT | §5.2 |
| 时间止损 | 7 天减半 / 14 天清仓 | §5.3 |
| 手续费 / 滑点 | 0.10% / 0.02% | §1.3 |
| 最小下单 | BTC 0.001 / ETH 0.01 / ≥ 5 USDT | §1.3 |
| 轮询间隔 | 60 秒 | §1.1 |
| 请求超时 | > 10 秒（总耗时） | §4.2 |
| SQLite 锁超时 | > 5 秒（BEGIN 到获锁总耗时） | §7 |
| HALTING 连续零进展停新交易 | 3 轮 | §5.0 / §5.1 |
| 时间跳变阈值 | 60 秒（墙钟增量与单调增量之差） | §5.0 / §7 |
| 压力测试步数 | 720 步；先跌后涨 360 + 360 步 | §8.2 |
| 压力测试恢复步数 | halt 触发后 24 小时（24 个整点步） | §8.2 |
| 验收最大回撤 | ≤ 16.00% | §8.3 |

## 8. 回测与验收

### 8.1 回测数据与撮合规则（定死）
- 标的：BTCUSDT、ETHUSDT Binance 现货 1h K 线，2021-01-01 至 2026-10-31。
- 来源：Binance 公共 K 线（data-api.binance.vision），本地缓存为 CSV/Parquet，禁止在回测中调用实时行情。
- 事件：`data/events.csv` 中的 CPI、非农、FOMC 时间（UTC），回测中同样执行 §4 的过滤；**stale 判断按模拟时间算**（模拟时间 + 90 天），不用真实当前日期；回测报告记录 events.csv 的版本号与 sha256（**该哈希是加载时实算并与版本头核对过的哈希，计算范围与归一化见 §7：剔除第 1 行版本头后的文件正文，去 BOM、CRLF→LF、末尾换行原样保留**）。
- **撮合规则：close-only。** 每根 1h K 线只在收盘时刻用 close 价判断一次买入 / 卖出，顺序为：先执行 §5.0(0) HALTING 续清，再执行 halt 期满恢复（恢复轮跳过 §5.1 检查），再判断 §5.1 回撤 halt，再判断整组止盈 / 时间止损 / 单层止盈（按 §5.0 优先级），最后判断买入（开组 / 加仓）。**不用 low/high，不猜测盘中先跌后涨的顺序。**回测粒度比实盘 60 秒轮询粗，这是一个已知的局限性，在回测报告中写明。
- 成本：与 §1.3 完全一致（last=close 价，手续费 0.10%，滑点 0.02% 单独扣现金）。
- **回测执行 §4.2（v5.3 定死）**：回测同样执行 §4.2 价格异常状态机（可疑价、error/suspect/recovery streak、接受条件 (a)/(b)、恢复计数），不得省略。close-only 回测的等价规则见 V5.3-1（首根初始化、条件 (b) 参考价 = 最近一根已收盘有效 K 线 close，须显式传入）与 V5.3-2（有效 K 线字典 + 评估窗口整点网格、缺 bar 轮处理、计数口径）。**适用范围：训练集、测试集及压力测试 4 策略回放；压力测试 1–3 为 §8.2 确定性持仓测试，仅执行 §5.1 halt。**回测报告须记录价格错误轮数与可疑价轮数（分币种，口径见 V5.3-2(e)）。

### 8.2 回测划分（防过拟合）与压力测试
- 训练：2021-01-01 至 2024-12-31（调参只许在这里）。
- 测试：2025-01-01 至 2026-10-31。
- **“测试集只跑一次”的适用范围：只指“用测试集调参的最终评估只跑一次”。**训练集回测、单元测试、压力测试、以及**不改参数、只修 bug 后的重跑**不受限。改参数后必须回 §2 重新冻结，算新一轮。
- 压力测试（**确定性持仓测试**，不是策略模拟）：
  - **起始状态（v3 分情形写清，不共用）**：C = 第 0 小时收盘价（合成路径的起始价，**数值由路径生成器定，不在 SPEC 里硬编码**）。层触发价按 §3.2：BTC d=1.5% 固定（L1 C、L2 0.985C、L3 0.97C、L4 0.955C、L5 0.94C、L6 0.925C），ETH d=2.0% 固定（L1 C、L2 0.98C、L3 0.96C、L4 0.94C、L5 0.92C、L6 0.90C）。层名义按 §2。第 0 小时视为两币（或单币）各 6 层已按触发价买入。**初始峰值权益（v4 定死，v5 改措辞、数字不变）：peak_equity = 10,000 USDT，即第 0 小时买入前的权益；买入总成本 6.00 USDT（手续费 5.00 + 滑点 1.00）计入回撤基线（第 0 小时权益 9,994，回撤 0.06%）。**
    - (a) BTC 单独：只有 BTC 6 层，现金 = 10,000 − 3,000 − 3.60 = **6,996.40 USDT**；ETH 无持仓、无组。
    - (b) ETH 单独：只有 ETH 6 层，现金 = 10,000 − 2,000 − 2.40 = **7,997.60 USDT**；BTC 无持仓、无组。
    - (c) BTC+ETH 同时：两币各 6 层，现金 = 10,000 − 5,000 − 6.00 = **4,994 USDT**。
  - **价格路径**：720 个整点步（30 天），每步收盘价 = 起始价 × r^h，r = (1+总涨跌幅)^(1/720)。**压力测试中不执行 §3.3 止盈与 §5.3 时间止损，只执行 §5.1 halt（逐小时算权益，回撤 ≥15% 即平仓）。**这是持仓测试的定义，不是策略模拟。
  - 1. 合成 +50%：r = 1.5^(1/720)。预期：无 halt，期末权益 > 初始。
  - 2. 合成 −50%：r = 0.5^(1/720)。**Muse 复算（持仓到 0.5C 不卖）：BTC 亏 1,422.5、ETH 亏 929.6、双币合计亏 2,352.1；逐小时算权益时双币 halt 触发点约 0.662C、约第 428 小时（0.70C 时权益 8,701.05、回撤 12.99%；0.65C 时 8,436.26、回撤 15.64%）。**
  - 3. 合成先跌后涨：前 360 步 r1 = 0.7^(1/360)（累计 −30%），后 360 步 r2 = 1.2^(1/360)（累计 +20%）。
  - 4. 真实切片：2022-06-01 00:00 UTC 至 2022-06-30 23:00 UTC，BTCUSDT 与 ETHUSDT 1h 收盘价原样回放，策略正常跑（含 §4 过滤与 §5 风控）。**测试 (4) 不用持仓测试的起始状态，用策略正常开组逻辑。**
  - **halt 后的恢复（v4 修适用范围）**：合成 −50% 路径 (c) 双币情形中 halt 触发后，继续走完 720 小时；触发后 24 小时（第 24 个整点步）执行 §5.0(a) 恢复（峰值重置为恢复时权益，清除 halt 状态），**恢复后不再开新组**（压力测试不跑开组逻辑），记录恢复后权益并验证峰值已重置；恢复那一小时跳过 §5.1 检查。**(a)(b) 单币情形不要求 halt 触发，不执行本恢复步骤。**

### 8.3 验收标准（全部满足才算通过，任一不满足即不通过）
1. 测试集扣成本后总盈亏 > 0。
2. 测试集最大净值回撤 ≤ 16.00%（按峰值算，在 1h 收盘价采样；**16.00% 是阈值参数，不是推导出的保证；同时报告两个数：收盘采样峰值回撤与强平落地后回撤，任一 >16.00% 即不通过**）。
3. 利润因子（总盈利 / 总亏损）≥ 1.20。**口径：按每笔 SELL 成交算**（每笔卖出的已实现盈亏，盈利的求和 / 亏损的求和取绝对值；已实现盈亏按 §1.4 含买卖双边成本）。若总亏损 = 0 且总盈利 > 0，利润因子记为 +∞，通过；若总盈利 = 0 且总亏损 = 0（无成交），利润因子未定义，**不通过**。
4. 盈利月占比 ≥ 60%。**口径：分母 = 测试集内有 ≥1 笔已平仓成交的自然月数；分子 = 其中已实现盈亏 > 0 的月数；盈亏 = 0 的月不计入分子。**
5. 压力测试 2（合成 −50%，确定性持仓测试）：**(c) 双币组合情形：halt 必须触发**（回撤达到 ≥15% 并执行平仓），且平仓落地后的最大回撤 ≤ 16.00%，且 halt 期间无新开仓、恢复后峰值权益已重置（按 §8.2 的恢复步骤验证，**本项仅适用于 (c) 双币情形**）。**(a)(b) 单币情形：不要求 halt 触发**（单币满仓跌 50% 最多亏 BTC 1,422.5 / ETH 929.6，数学上触发不了 15% halt），**不用验证恢复**；要求最大回撤 ≤ 16.00%，且无新开仓。
6. 单元测试全过：层金额求和 = 上限（BTC 3,000.00、ETH 2,000.00，误差 0.00）；层数上限 6 不可突破；回撤 15% 触发 halt 且恢复后不立即再触发（**含 halt 期间每轮不重触发、恢复轮跳过 §5.1 检查**）；halt 清仓失败保持 HALTING 并续清（**含独立事务提交状态、逐层进度、连续 3 轮零进展停交易**）；事件过滤在事件前 24h 内与事件后 2h 内禁止开组；重启恢复不重复建组、不重复执行 7 天减半；成本模型每笔单边 = 0.12%；realized_pnl 满足 §1.4 现金不变量；同轮跌破多层依次买齐；OPEN 层为 0 立即关组；上限拒买后不无限重试；**停加仓后其余 PENDING 全置 CANCELLED 且不再触发；可疑价 >10% 当轮禁止开组/加仓、跳过止盈判断，halt 判断仍执行、改用上一轮有效价**。
7. 回测结果可复现：同一数据 + 同一配置，两次运行结果完全一致（容差：总盈亏 ±0.01 USDT；比较未舍入值，显示值四舍五入到 2 位小数）。

### 8.4 模拟盘验收（回测通过后，另行冻结）
- v3 不定义模拟盘验收标准。回测通过后，由 Muse 另起 SPEC 补充模拟盘的运行时长、观察指标与上线标准，moka 批准后才长时间跑。
- 在模拟盘验收前，不得用真金、不得接 API Key、不得开 LIVE。

## 9. 待 moka / 盲审确认的开放问题（v3 明确标出，不许编程时自行定）
1. Binance 现货行情用 data-api.binance.vision 是否可达（oci2 上实测）？若不可达，备选是 api.binance.com 还是本地缓存 K 线先跑回测？
2. 手续费 0.10% + 滑点 0.02% 是否偏保守？Binance 现货实际费率以盲审核实为准，改动需回 SPEC。
3. 时间止损 7 天减半 / 14 天清仓、整组止盈 +1.5%，这三个数是 v3 暂定值，盲审可挑战，改动需重新冻结。
4. 看板集成是否只做只读展示（v3 定为只读）？若 moka 要在看板上加“暂停/恢复”按钮，需另起 SPEC。
5. 本项目与 perp-test 的关系：独立文件夹、独立库、独立进程，仅共享看板 8081 的展示层，不共享配置、DB 与执行器。
6. **冷静期 1 天是 moka 2026-11-06 11:57 定的。**若回测显示 1 天太短（恢复后立即再跌）或太长（错过反弹），改动需回 SPEC、重新冻结。
7. **浮亏停加仓规则已删除（v2 起，两家盲审均认可）**：停加仓只靠 −15% 价格规则与 15% halt。若盲审认为需要独立的浮亏规则，需给出间距无关的定义（不能是固定 % of deployed），回 SPEC 改。
8. **已冻结 v5.3（moka 批准后冻结）。**冻结版本即本文件 v5.3；后续逻辑改动必须回 §2 重走评审、重新冻结。

## 10. 安全与红线
- DRY_RUN only。代码中不得出现 Binance API Key / Secret 字段、不得读 `~/.config` 下的任何密钥文件、不得在日志中打印任何密钥。
- 不推 GitHub。除非 moka 明确下达。
- 不动线上：solana monitor（PID 1466554，2026-11-06 部署的 Config 1）、perp copier、Hermes 网关、agy remote-control、Caddy 配置。看板重启需 moka 批准。
- 模拟资金 10,000 USDT 是账面数字，不是真实入金。任何“充值 / 提现 / 转币”操作均不在 v3 范围内。
