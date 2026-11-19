import hashlib
from decimal import Decimal as D

from app.config import DEMAND_SAMPLE_MONTH, DEFAULTS
from app.errors import DomainError
from app.normalize import instant, number, serial
from .rules import demo_demand, read_intervals, settle


def execute(ctx):
    month = ctx["params"].get("month") or DEMAND_SAMPLE_MONTH
    rate = number(ctx["params"].get("demand_rate", DEFAULTS["demand_rate"]))
    if rate < 0:
        raise DomainError("RANGE", "需量单价无效")
    raw = ctx.get("demand_source")
    sample = raw is None
    if sample:
        raw = demo_demand(month)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    intervals, bad = read_intervals(raw, month)
    ctx["quarantine"].extend(bad)
    settlements, responsibilities = settle(intervals, ctx["bills"], month, rate)
    ctx["demand"] = settlements
    ctx["demand_responsibility"] = responsibilities
    ctx["demand_month"] = month
    ctx["demand_sha"] = hashlib.sha256(bytes(raw)).hexdigest()
    ctx["demand_sample"] = sample
    ctx["demand_fee_total"] = str(sum((item["demand_fee"] for item in settlements), D(0)))
    ctx["stages"].append({
        "title": "需量结算",
        "rows": settlements,
        "metrics": {
            "月份": month,
            "结算站": len(settlements),
            "需量费 CNY": ctx["demand_fee_total"],
            "补数失败站": sum(item["completeness"] in ("补数失败", "无回传") for item in settlements),
        },
    })
    return ctx


def attach(result, raw=None):
    """给不含需量阶段的旧批次追加只读预览；沿用该批次自己的订单与参数。"""
    if len(result["stages"]) >= 8:
        return result
    orders = [{**row, "start": instant(row["start"]), "end": instant(row["end"]),
               "energy": D(str(row["energy"])), "amount": D(str(row["amount"]))}
              for row in result["bills"]]
    ctx = {"params": result["params"], "bills": orders,
           "quarantine": list(result["quarantine"]), "stages": list(result["stages"])}
    if raw is not None:
        ctx["demand_source"] = raw
    execute(ctx)
    merged = dict(result)
    merged["stages"] = ctx["stages"]
    merged["quarantine"] = ctx["quarantine"]
    for key in ("demand", "demand_responsibility", "demand_month", "demand_sha", "demand_sample", "demand_fee_total"):
        if key in ctx:
            merged[key] = ctx[key]
    return serial(merged)

