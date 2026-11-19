from pathlib import Path
TITLE = '充电桩分时电价与损耗结算'
SHORT = 'charge-tariff'
STAGES = ['intake', 'tariffs', 'split', 'loss', 'fees', 'bills', 'batches', 'demand']
MODULES = ['电量接收', '费率版本', '订单切段', '站内损耗', '费用重算', '账单对照', '结算批次', '需量结算']
COLUMNS = {'id': '读数号', 'station': '站点', 'order': '订单', 'start': '开始 UTC', 'end': '结束 UTC', 'energy': '电量 kWh', 'kwh': '分段 kWh', 'unit': '单位', 'rate': '电价 CNY/kWh', 'loss_kwh': '损耗 kWh', 'amount': '应收 CNY', 'bill': '账单 CNY', 'delta': '差额 CNY', 'state': '状态', 'kind': '读数类型', 'code': '原因码', 'reason': '原因', 'line': '行号', 'count': '数量'}
DEFAULTS = {'loss_rate': '0.65', 'max_hours': '72', 'bill_tolerance': '0.01', 'demand_rate': '38.00'}
RULE_VERSION = "2026.11.18-v1"
DEMAND_RULE_VERSION = "2026.11.19-v1"
DEMAND_SAMPLE_MONTH = "2026-11"
SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"
MAX_BYTES = 4 * 1024 * 1024

COLUMNS.update({'key':'业务键','detail':'依据','meter_kwh':'总表 kWh','order_kwh':'订单 kWh','loss_state':'损耗判定','raw_amount':'未舍入金额'})
COLUMNS.update({'month':'结算月份','peak_kwh':'峰值槽电量 kWh','peak_demand':'需量 kW','peak_at':'峰值开始','peak_end':'峰值结束','peak_source':'峰值来源','meter_id':'需量表号','completeness':'补数状态','fill_source':'补数依据','fill_note':'补数说明','share_kwh':'责任电量 kWh','demand_fee':'需量费 CNY','energy_fee':'电度费 CNY','responsible_fee':'责任费 CNY','fee_total':'电度+需量 CNY','settlement':'结算状态','orders':'责任订单数','source':'数据来源'})
