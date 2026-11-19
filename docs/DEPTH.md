# 深度登记 充电桩分时电价与损耗结算

- 规模：中型
- 核心规则：电量按计量段与费率区间交集时长分配，跨日费率按当地日期选择，未知电量不作零，损耗分到分
- 使用者与工作流：充电站财务核算员；接收计量间隔与订单，按实际时段重算电费及站内损耗

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 电量接收 | 接收订单及总表计量 | `/api/m1` | `app/modules/m1_intake/rules.py` `app/modules/m1_intake/service.py` | `tests/test_m1_intake.py` |
| M2 费率版本 | 校核费率版本与单位 | `/api/m2` | `app/modules/m2_tariffs/rules.py` `app/modules/m2_tariffs/service.py` | `tests/test_m2_tariffs.py` |
| M3 订单切段 | 按时段交集拆分电量 | `/api/m3` | `app/modules/m3_split/rules.py` `app/modules/m3_split/service.py` | `tests/test_m3_split.py` |
| M4 站内损耗 | 按站点分配总表损耗 | `/api/m4` | `app/modules/m4_loss/rules.py` `app/modules/m4_loss/service.py` | `tests/test_m4_loss.py` |
| M5 费用重算 | 精确计算并分配分币 | `/api/m5` | `app/modules/m5_fees/rules.py` `app/modules/m5_fees/service.py` | `tests/test_m5_fees.py` |
| M6 账单对照 | 对照账单与损耗异常 | `/api/m6` | `app/modules/m6_bills/rules.py` `app/modules/m6_bills/service.py` | `tests/test_m6_bills.py` |
| M7 结算批次 | 核对电量行数并复核 | `/api/m7` | `app/modules/m7_batches/rules.py` `app/modules/m7_batches/service.py` | `tests/test_m7_batches.py` |
| M8 需量结算 | 生成每站唯一月度需量账单并复核 | `/api/m8` `/api/bills` | `app/modules/m8_demand/rules.py` `app/modules/m8_demand/service.py` `app/store.py` | `tests/test_m8_demand.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-06 | 全角数字 | 数值或归属错误 | 显式契约和守恒检查 | `app/modules/m2_tariffs/rules.py` | `tests/test_m2_tariffs.py::test_fullwidth_energy` |
| TRAP-07 | 未知电量 | 数值或归属错误 | 显式契约和守恒检查 | `app/modules/m2_tariffs/rules.py` | `tests/test_m2_tariffs.py::test_sentinel` |
| TRAP-10 | 浮点累加 | 数值或归属错误 | 显式契约和守恒检查 | `app/modules/m5_fees/rules.py` | `tests/test_m5_fees.py::test_decimal_exact` |
| TRAP-11 | 时区混写 | 数值或归属错误 | 显式契约和守恒检查 | `app/modules/m3_split/rules.py` | `tests/test_m3_split.py::test_timezone_equivalent` |
| TRAP-14 | 同版计量矛盾 | 数值或归属错误 | 显式契约和守恒检查 | `app/modules/m2_tariffs/rules.py` | `tests/test_m2_tariffs.py::test_real_conflict` |
| TRAP-15 | MWh换算 | 数值或归属错误 | 显式契约和守恒检查 | `app/modules/m2_tariffs/rules.py` | `tests/test_m2_tariffs.py::test_mixed_units` |
| TRAP-23 | 分时端点 | 数值或归属错误 | 显式契约和守恒检查 | `app/modules/m3_split/rules.py` | `tests/test_m3_split.py::test_boundary_split` |
| TRAP-24 | 分币尾差 | 数值或归属错误 | 显式契约和守恒检查 | `app/modules/m5_fees/rules.py` | `tests/test_m5_fees.py::test_pennies_conserved` |
| TRAP-25 | 费率版本重叠 | 数值或归属错误 | 显式契约和守恒检查 | `app/modules/m2_tariffs/rules.py` | `tests/test_m2_tariffs.py::test_overlap_versions` |
| TRAP-32 | 各时段峰重复计费 | 每段峰各收一次需量费，整站被重复收费 | 每站每月只取全月最高半小时一次，仅生成一笔需量费 | `app/modules/m8_demand/rules.py` | `tests/test_m8_demand.py::test_single_station_fee_not_duplicated_per_period` |
| TRAP-33 | 峰值订单责任 | 整站计费导致峰值订单看不到责任，或责任分摊不闭合 | 峰值半小时内订单按区间电量占比分摊，责任合计等于站点需量费，分币稳定 | `app/modules/m8_demand/rules.py` | `tests/test_m8_demand.py::test_order_responsibility_conserves_fee` |
| TRAP-34 | 月末补数 | 末段晚到就漏计或直接零值 | 缺段按同刻历史补数并记录依据；缺报超 10% 判补数失败并保留上一版可读结算 | `app/modules/m8_demand/rules.py` `app/store.py` | `tests/test_m8_demand.py::test_end_of_month_missing_last_slot_is_filled` `tests/test_m8_demand.py::test_missing_whole_month_is_unbillable_and_keeps_old` |
| TRAP-35 | 同月重试新增账单 | 每跑一次多一条账单，财务对不上 | 按站按内容指纹幂等，同输入重试复用；未复核就地升版；已复核保留原记录并生成替代版本 | `app/store.py` | `tests/test_m8_demand.py::test_retry_same_month_does_not_add_bill` `tests/test_m8_demand.py::test_reviewed_month_keeps_original_record` |

## 对账规则

输入逻辑行数 = 采信行数 + 隔离行数；空行单列。每条重复、冲突、超限、缺失均保留原始行号与原因码。计算阶段的输出数量可聚合，原始采信records始终保留，业务平衡另按模块给出的重量、时长、电量或座席数量核对。
相同输入摘要、规则版本、参数产生相同批次ID。运行原子插入SQLite；复核使用整数版本乐观锁。
月度需量另存账单台账：每站每月仅一笔“当前”账单，账单版本用整数乐观锁复核；补数失败不出账、保留旧版；已复核版本被替代后仍可查阅。

## 已知不覆盖

计量段内假定匀速用电，站点总表必须唯一且覆盖所导入的全部订单；真实采样差异应预先切成计量段。只计算电费及损耗，不含停车服务费。需量按半小时回传的最大平均功率计，不含容量费、基本电费的地区差异或行政分摊。
