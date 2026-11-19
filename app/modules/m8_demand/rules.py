"""月度最大需量结算规则。

电表按半小时回传，站点每月只有一个需量结算值（全月最高半小时的平均功率）。
每站因此只产生一笔需量费；形成该峰值的订单按峰值半小时内电量占比分摊这同一笔费用，
订单责任合计恰等于站点需量费，不会把各时段峰重复计费。

月末最后一段常常晚一天到，缺失槽按同刻历史回传补数并记录依据；补数失败时该站
保持待复核，不生成新的需量费，由上层沿用它上一版可读结算。
"""
import csv
import io
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, ROUND_HALF_UP, ROUND_FLOOR

from app.errors import DomainError
from app.normalize import decode, instant, number
from app.reasons import rejected

ZONE = timezone(timedelta(hours=8))
SLOT = timedelta(minutes=30)
SLOT_HOURS = D("0.5")
CENT = D(".01")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MONTH_FIELDS = ["meter", "station", "start", "end", "kwh"]


def month_bounds(label):
    """返回本地 +08 月区间对应的 UTC 边界 [start, end)。"""
    if not isinstance(label, str) or not MONTH_RE.match(label):
        raise DomainError("MONTH", "结算月份必须为 YYYY-MM")
    year, month = (int(part) for part in label.split("-"))
    start = datetime(year, month, 1, tzinfo=ZONE)
    end = datetime(year + 1, 1, 1, tzinfo=ZONE) if month == 12 else datetime(year, month + 1, 1, tzinfo=ZONE)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def slots(label):
    """全月应回传的半小时槽起点（UTC）。"""
    start, end = month_bounds(label)
    count = int((end - start).total_seconds() // SLOT.total_seconds())
    return [start + SLOT * i for i in range(count)]


def read_intervals(raw, label):
    """解析半小时回传 CSV，返回（采信槽, 隔离行）。

    字段 meter,station,start,end,kwh，source 可选，仅作说明。
    """
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, (bytes, bytearray)):
        raise DomainError("DEMAND", "需量数据必须为 CSV 文本")
    text, _ = decode(bytes(raw))
    if not text.strip():
        raise DomainError("EMPTY", "需量数据没有内容")
    reader = csv.reader(io.StringIO(text.lstrip("﻿"), newline=""), strict=True)
    try:
        header = [cell.strip() for cell in next(reader)]
    except (StopIteration, csv.Error):
        raise DomainError("HEADER", "需量字段名读取失败")
    if len(header) != len(set(header)) or not set(MONTH_FIELDS) <= set(header):
        raise DomainError("HEADER", "需量字段名不符合契约")
    month_start, month_end = month_bounds(label)
    good, bad, seen = [], [], {}
    try:
        for cells in reader:
            if not cells or not any(cell.strip() for cell in cells):
                continue
            if len(cells) != len(header):
                bad.append(rejected({"_line": reader.line_num}, "COLUMN_COUNT", repr(cells)))
                continue
            row = dict(zip(header, (cell.strip() for cell in cells)))
            row["_line"] = reader.line_num
            try:
                if not row["meter"] or not row["station"]:
                    raise DomainError("MISSING", "表号或站点缺失")
                begin = instant(row["start"])
                finish = instant(row["end"])
                if not month_start <= begin < month_end:
                    raise DomainError("RANGE", "计量区间不在结算月份")
                if finish - begin != SLOT:
                    raise DomainError("DURATION", "需量表必须按半小时回传")
                kwh = number(row["kwh"])
                if kwh < 0:
                    raise DomainError("NEGATIVE", "需量电量不能为负")
            except DomainError as exc:
                bad.append(rejected(row, exc.code, exc.message))
                continue
            item = {"meter": row["meter"], "station": row["station"], "start": begin,
                    "end": finish, "kwh": kwh, "source": row.get("source", "") or "回传",
                    "_line": row["_line"]}
            key = (item["station"], begin)
            if key in seen:
                prior = seen[key]
                if prior["kwh"] == kwh and prior["meter"] == item["meter"]:
                    bad.append(rejected(row, "DUPLICATE", "同站同半小时重复回传"))
                else:
                    # 冲突双方的读数都不可采信，已入 good 的前一条一并撤下。
                    good.remove(prior)
                    bad.append(rejected(prior, "CONFLICT", "同站同半小时读数冲突"))
                    bad.append(rejected(row, "CONFLICT", "同站同半小时读数冲突"))
                    del seen[key]
                continue
            seen[key] = item
            good.append(item)
    except csv.Error:
        raise DomainError("CSV", "需量 CSV 引号未闭合")
    return good, bad


def _slot_key(moment):
    local = moment.astimezone(ZONE)
    return (local.date(), local.hour, local.minute)


def backfill(station, missing, history, meter_hint, max_ratio=D("0.10")):
    """为缺失槽估补读数，返回（补齐槽, 失败清单）。

    依据优先级：缺失日前最近 7 个同刻回传值的均值 → 全月同刻均值 → 站点可用均值。
    每段补数都带依据说明，结算页可逐条核对。缺失超过全月约定比例时判定补数失败，
    不做整月的估算填充。
    """
    filled, failures = [], []
    history = dict(sorted(history.items()))
    if history and missing:
        expected = len(history) + len(missing)
        if max_ratio is not None and expected and D(len(missing)) / D(expected) > max_ratio:
            reason = f"缺失 {len(missing)} 段，超过可补比例 {max_ratio}"
            return [], [{"start": slot, "end": slot + SLOT, "reason": reason} for slot in missing]
    for moment in missing:
        key = _slot_key(moment)
        same_clock = [(slot, item) for slot, item in history.items() if _slot_key(slot)[1:] == key[1:]]
        prior = [(slot, item) for slot, item in same_clock if _slot_key(slot)[0] < key[0]]
        basis, note = None, ""
        if prior:
            prior.sort(key=lambda pair: pair[0], reverse=True)
            chosen = prior[:7]
            basis = sum((item["kwh"] for _, item in chosen), D(0)) / D(len(chosen))
            dates = "、".join(sorted({item["start"].astimezone(ZONE).date().isoformat() for _, item in chosen}))
            note = f"前7日同刻均值，采用 {len(chosen)} 个回传值（{dates}）"
        elif same_clock:
            basis = sum((item["kwh"] for _, item in same_clock), D(0)) / D(len(same_clock))
            note = f"全月同刻均值，采用 {len(same_clock)} 个回传值"
        elif history:
            usable = list(history.values())
            basis = sum((item["kwh"] for item in usable), D(0)) / D(len(usable))
            note = f"站点可用均值，采用 {len(usable)} 个回传值"
        if basis is None:
            failures.append({"start": moment, "end": moment + SLOT, "reason": "无任何可用回传值"})
            continue
        filled.append({"meter": meter_hint, "station": station, "start": moment,
                       "end": moment + SLOT, "kwh": basis, "source": "补数",
                       "fill_source": note.split("，")[0], "fill_note": note, "_line": ""})
    return filled, failures


def responsibility(orders, station, peak_start, peak_end, fee):
    """把站点唯一的需量费按峰值半小时内订单电量占比分摊到订单。"""
    shares = []
    for order in orders:
        if order["station"] != station:
            continue
        begin, finish = order["start"], order["end"]
        seconds = (finish - begin).total_seconds()
        if seconds <= 0:
            continue
        overlap = (min(finish, peak_end) - max(begin, peak_start)).total_seconds()
        if overlap <= 0:
            continue
        shares.append({"order": order["order"], "id": order["id"],
                       "share_kwh": order["energy"] * D(str(overlap)) / D(str(seconds))})
    total = sum((item["share_kwh"] for item in shares), D(0))
    if not shares or total == 0:
        return []
    raw = [fee * item["share_kwh"] / total for item in shares]
    amounts = [value.quantize(CENT, rounding=ROUND_FLOOR) for value in raw]
    pennies = int((fee - sum(amounts, D(0))) / CENT)
    order_index = sorted(range(len(shares)),
                         key=lambda i: (-(raw[i] - amounts[i]), shares[i]["order"], shares[i]["id"]))
    for i in order_index[:max(pennies, 0)]:
        amounts[i] += CENT
    return [{"order": shares[i]["order"], "id": shares[i]["id"], "station": station,
             "share_kwh": shares[i]["share_kwh"], "responsible_fee": amounts[i],
             "peak_start": peak_start, "peak_end": peak_end} for i in range(len(shares))]


def settle(intervals, orders, label, demand_rate, expected=None):
    """生成每站唯一需量结算与峰值订单责任。"""
    expected = list(expected) if expected is not None else slots(label)
    by_station = defaultdict(list)
    for item in intervals:
        by_station[item["station"]].append(item)
    stations = sorted(set(by_station) | {order["station"] for order in orders})
    settlements, responsibilities = [], []
    for station in stations:
        group = sorted(by_station[station], key=lambda item: (item["start"], item["meter"]))
        meters = {item["meter"] for item in group}
        present = defaultdict(list)
        for item in group:
            present[item["start"]].append(item)
        unique = {moment: dup[0] for moment, dup in present.items()}
        missing = [moment for moment in expected if moment not in unique]
        meter_hint = sorted(meters)[0] if meters else ""
        if group:
            filled, failures = backfill(station, missing, unique, meter_hint)
        else:
            filled, failures = [], [{"start": slot, "end": slot + SLOT, "reason": "整月无回传"}
                                    for slot in missing[:1]]

        measured = [{"start": moment, "end": moment + SLOT, "kwh": item["kwh"],
                     "source": "回传", "meter": item["meter"]}
                    for moment, item in unique.items()]
        # 半小时内若有多块表，按槽汇总，保持整站唯一口径。
        aggregated = defaultdict(lambda: D(0))
        for item in measured:
            aggregated[item["start"]] += item["kwh"]
        measured = [{"start": moment, "end": moment + SLOT, "kwh": amount, "source": "回传",
                     "meter": meter_hint} for moment, amount in aggregated.items()]
        timeline = sorted(measured + filled, key=lambda item: item["start"])

        if not group:
            completeness = "无回传"
            fill_source = "—"
            note = "整月没有任何半小时回传"
        elif len(meters) != 1:
            completeness = "需量表不唯一"
            fill_source = "—"
            note = f"站点存在 {len(meters)} 个需量表号：{'、'.join(sorted(meters))}"
        elif failures:
            completeness = "补数失败"
            fill_source = "—"
            note = "；".join(f"{item['start'].astimezone(ZONE).isoformat()} {item['reason']}" for item in failures)
        elif filled:
            completeness = "已补数"
            fill_source = filled[-1]["fill_source"]
            note = "；".join(sorted({item["fill_note"] for item in filled}))
        else:
            completeness = "完整"
            fill_source = "—"
            note = "全月半小时回传齐全"

        demand_fee, peak_kw = D(0), D(0)
        peak_start = peak_end = None
        peak_source = "—"
        # 峰值取整月时间轴；若峰值落在补数槽，明确标注，便于财务核补数依据。
        peak = max(timeline, key=lambda item: (item["kwh"], -item["start"].timestamp())) if timeline else None
        if peak is not None:
            peak_kw = (peak["kwh"] / SLOT_HOURS).quantize(D(".001"), rounding=ROUND_HALF_UP)
            peak_start, peak_end = peak["start"], peak["end"]
            peak_source = peak["source"]
            if completeness in ("完整", "已补数"):
                demand_fee = (peak_kw * demand_rate).quantize(CENT, rounding=ROUND_HALF_UP)

        energy_fee = sum((D(str(order.get("amount", 0))) for order in orders
                          if order["station"] == station), D(0))
        settlement_state = "通过" if completeness in ("完整", "已补数") else "待复核"
        settlements.append({
            "month": label, "station": station, "meter_id": "、".join(sorted(meters)) or "—",
            "peak_demand": peak_kw, "peak_kwh": peak["kwh"] if peak else D(0),
            "peak_at": peak_start, "peak_end": peak_end, "peak_source": peak_source,
            "demand_rate": demand_rate, "demand_fee": demand_fee,
            "energy_fee": energy_fee, "fee_total": energy_fee + demand_fee,
            "completeness": completeness, "fill_source": fill_source, "fill_note": note,
            "settlement": settlement_state, "orders": 0,
        })
        if completeness in ("完整", "已补数") and peak is not None:
            allocated = responsibility(orders, station, peak_start, peak_end, demand_fee)
            responsibilities.extend(allocated)
            settlements[-1]["orders"] = len(allocated)
            if not allocated:
                settlements[-1]["fill_note"] = note + "；峰值半小时内无订单承接"
    return settlements, responsibilities


def demo_demand(label):
    """整月半小时回传样例；月末最后一段故意缺报，用于展示补数依据。"""
    _, end = month_bounds(label)
    lines = ["meter,station,start,end,kwh,source"]
    profiles = {"站甲": ("D-JA", D("18"), D("80"), "2026-11-16T14:30:00"),
                "站乙": ("D-YI", D("16"), D("70"), "2026-11-16T06:30:00"),
                "站丙": ("D-BING", D("15"), D("60"), "2026-11-16T14:30:00")}
    last_slot = end - SLOT
    for station, (meter, base, peak_kwh, peak_local) in profiles.items():
        peak_at = datetime.fromisoformat(peak_local).replace(tzinfo=ZONE).astimezone(timezone.utc)
        for moment in slots(label):
            if moment == last_slot:
                continue  # 月末最后一段晚一天到
            local = moment.astimezone(ZONE)
            kwh = peak_kwh if moment == peak_at else base + D((local.hour * 7 + local.minute // 10 + local.day) % 5)
            lines.append(",".join([meter, station, local.isoformat(), (local + SLOT).isoformat(),
                                   str(kwh), "meter"]))
    return "\n".join(lines) + "\n"
