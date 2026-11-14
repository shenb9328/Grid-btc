# 校验清单（CHECKSUMS）

导出日期：2026-11-12（第一次导出 v5.6 + v5.7 草案；同日第二次导出更新至 v5.7 冻结版 + 修复后代码；2026-11-14 第三次导出更新至 v5.8 冻结版 + 线上代码 + E1–E6 实验记录）。代码、测试、数据与评审文件与原始项目文件**逐字节一致**，只有一个例外：24 个 SPEC 文件中各 1 行的看板主机名被替换为 `[dashboard-host-redacted]`（脱敏，见 §1）。除此之外，本仓库的全部原始文件均可直接用 md5 比对验证。

验证命令（在仓库根目录）：

```bash
md5sum config.py engine.py monitor.py backtest.py tests/*.py data/*.csv
sha256sum results/backtest_v56_frozen_bt.log
```

## 1. SPEC 文件：原始 md5 与本仓库 md5 对照

每行只改了 1 行（SPEC §6.3 的看板行），改动内容只有主机名字符串本身（已用 diff 逐文件核验：每个文件恰好 1 行不同）。

| 文件 | 原始 md5（脱敏前） | 本仓库 md5（脱敏后） |
|---|---|---|
| `spec.md`（冻结 v5.8） | `8f6aa4c28dd6e6d74ebc48a62d6e9597` | `f9f813ad8109bd24623db4b147281591` |
| `spec_v57_frozen.md`（冻结 v5.7） | `6969b432170928138cb787d73531a71b` | `ac820a81d9845f5288461051319e38ff` |
| `spec_v58_draft.md`（草案第 1 轮） | `13217f7484acfb4161832e71dead33c6` | `cb88e90be2fe2a21eb2146dd748c63c8` |
| `spec_v58_draft_r2.md`（草案第 2 轮） | `a2407c6ab7c95fa5e21a031d7e3a5dc5` | `ca205a99c3a7d28675ef540630b80412` |
| `spec_v56_frozen.md`（冻结 v5.6） | `79eabf69bb27ec30530073a166c54194` | `c4f7f8ceb91592e64d3b0e7a389e0580` |
| `spec_v0.md` | `95c130a16e9cf506125af72f3a407548` | `01d831352d95f742ec2801e9c1996d1b` |
| `spec_v1.md` | `d0eeeee03c9ba6a2485d99c02fae3a3d` | `be05175248153067a6e156a3d212d5b7` |
| `spec_v2.md` | `5fb85ed34c09fb73df94725ce7948ca1` | `94976dd14639c29432d7adddf042e775` |
| `spec_v3.md` | `379e3e658d5b58b0dfc0f7cbfd40a745` | `a6a685abe7df351ca9e5d30963cf03b4` |
| `spec_v4.md` | `8f0491841e008d3d5bba536e303399a2` | `39b24677c788763758fbc09f44a6c69f` |
| `spec_v5.md` | `61945c30e8285ecfd14e52ead2d1daf0` | `807a083c82875c4c53c24bc32b84e075` |
| `spec_v51.md` | `caa3ad0d7c70d5d2cd7da899cd6b9404` | `e201931aa1fdca10b17073207d3f3b4a` |
| `spec_v52_draft.md` | `79318d79a65408b893a66d4a341fb80a` | `0f5279b70828d029139d84eded672cbf` |
| `spec_v53_draft.md` | `bee7237618b5ac804add89101db720dd` | `b3b3058120d2ebb5052731b756e329ba` |
| `spec_v54_draft.md` | `ee8c4d2304be7cdacb1cf893f8e59c8e` | `86c608eb76cb2536c4c13cb90405a8dd` |
| `spec_v55_draft.md` | `572dcd4fd76c69aafab6ea717da3099d` | `f0051ba1c59e6e48da29f903e6427a64` |
| `spec_v55_frozen.md`（冻结 v5.5） | `faa9b30a3f7e2fd651a7acc97cc1e023` | `c3657230641e0cd5e6fddf6ecf8593ad` |
| `spec_v56_draft.md` | `b245963cd881d46fa259189389c9519a` | `fbf7e3a25e923219afe1fcc12d267a1f` |
| `spec_v57_draft.md`（草案第 1 轮） | `378e16ac27fee10e339c1f32c98f758a` | `91a526bbae70571c367c5518b748908a` |
| `spec_v57_draft_r2.md`（草案第 2 轮） | `48ce334ba633c0f89e5b30c3c4720a70` | `2578be6d2e98586a35f2380fe52365e7` |
| `spec_v57_draft_r3.md`（草案第 3 轮） | `3919db2477d6a00b9f857cf835c003e3` | `aec0c98502f2b3a6d8ca5fe34610c987` |
| `spec_v57_draft_r4.md`（草案第 4 轮） | `625291f006060a13ba1bad75965f9daa` | `fd43ed1b3821263bc76424e882d9e5e5` |
| `spec_v57_draft_r5.md`（草案第 5 轮） | `03c020702125c20fde9a0d2637259033` | `73fcb170f05bc6d025ab8e88a0c08353` |

注意：SPEC 文件正文内部互相引用的 md5（如 `spec.md` 第 3 行引用的 v5.5 md5）是**脱敏前的原始 md5**，与上表“原始 md5”列一致；与本仓库文件的 md5 不一致是预期的，原因就是本节记录的 1 行脱敏。

## 2. 代码与测试（逐字节一致，无改动）

| 文件 | md5 |
|---|---|
| `config.py` | `a280934ed96e120262662e1bf4c054bf` |
| `engine.py` | `b60b29ccdfd4393760f965842ab458d1` |
| `monitor.py` | `c49288b6dd9a23383d7b90365988d0af` |
| `backtest.py` | `69d054be3aa38e1a9ac2c19630cfffb8` |
| `tests/test_spec_v51.py` | `768f0e403ab4cc2f376b0e1acb514647` |
| `tests/test_spec_v55.py` | `8d5a6f315706f6139b8a44b710561e85` |
| `tests/test_spec_v56_holdmax.py` | `8ef62519b616a204f686fe63c1161d7f` |
| `tests/test_spec_v57.py` | `1dd0ea2bf6452eae51ca2f935bc13881` |
| `tests/test_spec_v58.py` | `292c715b7ffbb9c3c101f242bca019a2` |

## 3. 数据（逐字节一致，无改动）

| 文件 | md5 | 备注 |
|---|---|---|
| `data/BTCUSDT_1h.csv` | `0289746956c306950047b8e29598c80b` | 51,629 根，2020-12-10T00:00Z → 2026-10-31T23:00Z |
| `data/ETHUSDT_1h.csv` | `d501512b4ac626405a335c443f4d69cc` | 51,629 根，同上 |
| `data/events.csv` | `1b31d809312fa8f665bd41651fa0a60d` | version 20261108；**正文 sha256**（剔除版本头、SPEC §7 口径）为 `1b039cfa90577b5b59b298b53f5d85a74cd63e03aba0cbde29e382c59b73eecc`，与文件版本头一致，`engine.load_events_calendar` 在启动时实算校验 |

## 4. 回测日志

| 文件 | sha256 |
|---|---|
| `results/backtest_v56_frozen_bt.log` | `7322eebfc3ba18d0e8f51281a34993bdd811b70808c393006da17b0c70a88215` |
| `results/backtest_v57_frozen_bt.log` | `b36904e0bd2c6c6e3f6229b31b9fcdd7aa4048ff6ae09cbcaa3343038da6a52c` |
| `results/backtest_v58_frozen_bt.log` | `f012daab136b9adb5556f4485723f5c392f38ab5f8118d82108f1efd9cb2022b` |

v5.6 日志由实现者与独立复核方各跑一次、输出逐字节一致后归档。v5.7 日志由独立复核方跑两次、导出方本地跑一次，三次输出逐字节一致后归档；本地复跑 `python3 backtest.py` 的输出应与之逐字节一致（总盈亏容差 ±0.01 USDT）。v5.8 日志（md5 `3450be5ad62a008b27f399c2e5770dae`）由实验轮、实现轮、独立核验轮、导出方本地复跑四次运行逐字节一致后归档；本地复跑 `python3 backtest.py` 的输出应与之逐字节一致。

## 5. 评审与实现报告（逐字节一致，无改动）

| 文件 | md5 |
|---|---|
| `results/merged_review_v0.md` | `35733fcabb6412889ff65be94ed631c8` |
| `results/merged_review_v1.md` | `f8e7ac267676d1119f56fe51dfca325f` |
| `results/merged_review_v2.md` | `b7daac6f536d1faa47aea595c1bc65a7` |
| `results/merged_review_v3.md` | `e71433a631ecc1a6618fd7644b1d4283` |
| `results/merged_review_v4.md` | `3038225955bc648eeaf6410151f688b2` |
| `results/merged_review_v5.md` | `36e825ba730ac9491bab825ecec3a813` |
| `results/merged_review_v52.md` | `4577d5c69597f2cbcc2ce0920f59e12e` |
| `results/merged_review_v53.md` | `d62650932dc3dbaf4f8edf127d37edd8` |
| `results/merged_review_v54.md` | `62b7dbf080fd60c20c5c90b6ee19fb06` |
| `results/merged_review_v55.md` | `f3cf908e344cbc13d7e9c0e8bcd7b49a` |
| `results/merged_review_v56.md` | `3ced5ca7f6f49cd2312960d60fbf4d53` |
| `results/merged_review_v56r2.md` | `817aa3c5c11d10883e490183c91d3c5a` |
| `results/review_v56_agy.md` | `35f1a262cd470fcd56505017b7d0e05f` |
| `results/review_v56_agy2.md` | `03311d352761b56a8469c796e092c99a` |
| `results/review_v56r2_agy.md` | `1febca3e7c51bdc89abc0658c0e2ca81` |
| `results/review_v56r2_agy2.md` | `fa676c8b4999bb20f8cc5f302b2caab1` |
| `results/review_v56r3_recheck_agy2.md` | `5ca33c6d392e44c639ff7876b1f2a775` |
| `results/impl_agy_v51.md` | `8ce6d72e1b8a80cb9e1f22d657308769` |
| `results/impl_agy_v55.md` | `1c255ab67fda8e2972225bc6d91c51e2` |
| `results/implement_v56.md` | `3b796bfb3dcabfe150b7b266b1acb410` |
| `results/merged_review_v57.md` | `a6f0e6549e895529777ff42ddae420b9` |
| `results/merged_review_v57r2.md` | `ebc3220cb035475231be8334b7c22bf9` |
| `results/review_v57_agy.md` | `02d75362f5b483f531582e506493d26a` |
| `results/review_v57_agy2.md` | `517b3a65fce7de619ccf31f2c26cf9ce` |
| `results/review_v57r2_agy.md` | `a53ebb6897014c6550e733044b5765d0` |
| `results/review_v57r2_agy2.md` | `b37ff8fe75223bcecc1d88a518655aab` |
| `results/review_v57r3_recheck.md` | `8c2661822db7db107844097b53825f7a` |
| `results/review_v57r4_recheck.md` | `2cc1e542d95b4d42340603644f398c63` |
| `results/implement_v57.md` | `fb57d113501a232defd2792dc4a8fde7` |
| `results/fix_audit_v57.md` | `6ddd3f68064060e0531d4f8ec561c2ca` |
| `results/review_v58_agy.md` | `fa81f0cf04704cbc4c31481c2545ecba` |
| `results/review_v58r2_agy.md` | `7cb2b68c785eaa61b741e5389660f60e` |
| `results/implement_v58.md` | `234ab21f0a54cc42179a9795f4263e24` |
| `results/experiments_round1.md` | `4d7f8865d1a3030182caeb4b5f9c834d` |
| `results/experiments_round2.md` | `cb4b13aa5737dfc8837d4282bb47b3a8` |
| `results/experiments_round3.md` | `1561e4cfdd90f18d96bba466cf6dcfbf` |

## 6. 本仓库新增文件（导出时新建，非原始文件）

`README.md`、`RESULTS.md`、本文件、`data/README.md`、`results/README.md`、`.gitignore`。这些文件不在上表内；它们是对原始材料的总结与索引，不参与 md5 比对。
