# data/ 阅读说明

## K 线文件

- `BTCUSDT_1h.csv`、`ETHUSDT_1h.csv`：Binance 现货 1h K 线（公共接口 `data-api.binance.vision` 的 `GET /api/v3/klines`，本地缓存）。
- 列：`open_time,open,high,low,close,volume,close_time`；`open_time` 为 UTC epoch 毫秒，对齐 UTC 整点。
- 范围：2020-12-10T00:00:00Z 至 2026-10-31T23:00:00Z，各 51,629 根有效 bar（文件各 51,630 行，含表头）。
- 回测加载时清洗（`backtest.py::load_cached_klines`）：仅保留 open_time 为 UTC 整点且 OHLC 均为有限正数的行；按 open_time 升序去重；本仓库数据实测 unmappable=0、dups=0、all_invalid=0。
- 已知缺口：两币种各 1 个连续 4 小时的缺口，结束于 2021-08-13T06:00:00Z（训练集内），回测按整点网格计 4 次价格错误。

## 事件日历

- `events.csv`：经济事件日历，version **20261108**，共 **256 条**事件（CPI / 非农 / FOMC 决议），UTC 时间，覆盖至 2027-12-10。
- 格式：第 1 行为版本头 `# version: 20261108, sha256: <正文 sha256>`；第 2 行为表头 `utc_time,event,country,impact`；其后每行一条事件，按时间升序、无重复、无空字段；`impact` 只能是 HIGH / MEDIUM / LOW。
- 校验：`engine.load_events_calendar` 在启动时对正文（剔除版本头、去 BOM、CRLF→LF、末尾换行原样保留）实算 sha256 并与版本头比对，不符即报错；monitor 每轮都检查 stale。当前正文 sha256 见 `../CHECKSUMS.md`。
- Stale 规则：最后一条事件 < 当前时间 + 90 天时，禁止开新组（fail-safe）。按当前日历，约 2027-09-11 之后须刷新日历；刷新时必须同步更新版本头里的版本号与正文 sha256。

## 运行时生成的文件（不在仓库内）

运行 `monitor.py` 会在本目录生成 `spot_martingale.sqlite3`（模拟账户数据库）与 `spot_dashboard_last_read.txt`（看板元数据），二者均已在 `.gitignore` 中排除，不要提交。
