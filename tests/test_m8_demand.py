from datetime import timedelta
from decimal import Decimal as D

import pytest

from app.errors import DomainError
from app.modules.m8_demand.rules import (backfill, demo_demand, month_bounds, read_intervals,
                                         settle, slots)
from app.pipeline import run
from app.config import SAMPLES
from app import store


def interval(station="S", kwh="10", at="2026-11-02T00:00:00", meter="M1"):
    from app.normalize import instant
    begin = instant(at + "+08:00")
    return {"meter": meter, "station": station, "start": begin,
            "end": begin + timedelta(minutes=30), "kwh": D(kwh), "source": "回传"}


def order(order_id="O1", station="S", energy="10", start="2026-11-02T00:00:00+08:00",
          end="2026-11-02T00:30:00+08:00"):
    from app.normalize import instant
    return {"order": order_id, "id": order_id, "station": station, "energy": D(energy),
            "start": instant(start), "end": instant(end), "amount": D("1.00")}


def test_month_bounds_and_slot_count():
    start, end = month_bounds("2026-11")
    assert len(slots("2026-11")) == 30 * 48
    assert start.isoformat() == "2026-10-31T16:00:00+00:00"
    assert end.isoformat() == "2026-11-30T16:00:00+00:00"


def test_month_label_rejected():
    with pytest.raises(DomainError):
        month_bounds("2026-13")


def test_single_station_fee_not_duplicated_per_period():
    # TRAP-32：站点只收一笔需量费，不按各时段峰重复计费。
    rows = [interval(kwh="20", at="2026-11-02T00:00:00"),
            interval(kwh="40", at="2026-11-02T12:00:00"),
            interval(kwh="30", at="2026-11-03T08:00:00")]
    settlements, _ = settle(rows, [order()], "2026-11", D("10"), expected=[row["start"] for row in rows])
    assert len(settlements) == 1
    assert settlements[0]["peak_demand"] == D("80.000")
    assert settlements[0]["demand_fee"] == D("800.00")


def test_order_responsibility_conserves_fee():
    # TRAP-33：订单责任合计恰等于站点唯一需量费，不重复也不丢失。
    rows = [interval(kwh="40", at="2026-11-02T00:00:00")]
    orders = [order("O1", energy="10"), order("O2", energy="30")]
    settlements, shares = settle(rows, orders, "2026-11", D("10"), expected=[row["start"] for row in rows])
    assert settlements[0]["demand_fee"] == D("800.00")
    assert sum((item["responsible_fee"] for item in shares), D(0)) == settlements[0]["demand_fee"]


def test_responsibility_penny_split_stable():
    rows = [interval(kwh="10", at="2026-11-02T00:00:00")]
    orders = [order(f"O{i}", energy="1") for i in range(1, 4)]
    _, shares = settle(rows, orders, "2026-11", D("1"), expected=[row["start"] for row in rows])
    assert sum((item["responsible_fee"] for item in shares), D(0)) == D("20.00")
    assert all(D(item["responsible_fee"]).as_tuple().exponent == -2 for item in shares)


def test_order_outside_peak_not_responsible():
    rows = [interval(kwh="40", at="2026-11-02T00:00:00")]
    orders = [order("O1", energy="10"),
              order("O2", energy="10", start="2026-11-02T06:00:00+08:00", end="2026-11-02T06:30:00+08:00")]
    _, shares = settle(rows, orders, "2026-11", D("10"), expected=[row["start"] for row in rows])
    assert [item["order"] for item in shares] == ["O1"]
    assert shares[0]["responsible_fee"] == D("800.00")


def test_partial_overlap_proportional():
    rows = [interval(kwh="20", at="2026-11-02T00:00:00")]
    orders = [order("O1", energy="20", start="2026-11-02T00:00:00+08:00", end="2026-11-02T01:00:00+08:00")]
    _, shares = settle(rows, orders, "2026-11", D("10"), expected=[row["start"] for row in rows])
    # 一小时的订单有半小时落在峰值区间，责任电量按其区间内电量计。
    assert shares[0]["share_kwh"] == D("10")


def test_backfill_prefers_prior_same_clock():
    from app.normalize import instant
    history = {instant(f"2026-11-{day:02d}T00:00:00+08:00"): {"kwh": D(str(day)), "start": instant(f"2026-11-{day:02d}T00:00:00+08:00")}
               for day in range(1, 10)}
    filled, failures = backfill("S", [instant("2026-11-10T00:00:00+08:00")], history, "M1")
    assert not failures
    assert filled[0]["kwh"] == D("6")  # 最近 7 天 3..9 的均值
    assert filled[0]["fill_source"] == "前7日同刻均值"


def test_backfill_rejects_wholesale_gap():
    from datetime import timedelta
    from app.normalize import instant
    start = instant("2026-11-02T00:00:00+08:00")
    history = {start: {"kwh": D("5"), "start": start}}
    missing = [start + timedelta(minutes=30) * i for i in range(1, 5)]
    filled, failures = backfill("S", missing, history, "M1")
    assert not filled and failures


def test_backfill_without_history_fails():
    from app.normalize import instant
    filled, failures = backfill("S", [instant("2026-11-02T00:00:00+08:00")], {}, "M1")
    assert not filled and failures[0]["reason"] == "无任何可用回传值"


def test_end_of_month_missing_last_slot_is_filled():
    result = run((SAMPLES / "clean.csv").read_bytes(), stop=8)
    assert result["demand"][0]["completeness"] == "已补数"
    assert result["demand"][0]["settlement"] == "通过"


def test_missing_whole_month_is_unbillable_and_keeps_old(isolate):
    base = (SAMPLES / "clean.csv").read_bytes()
    good = demo_demand("2026-11")
    run(base, stop=8, demand=good)
    # 只剩一天回传：缺失远超可补比例，补数失败。
    thin = "\n".join(["meter,station,start,end,kwh,source"] + [line for line in good.splitlines()[1:] if "2026-11-01" in line])
    result = run(base, stop=8, demand=thin)
    actions = result["billing_actions"]
    assert all(item["action"] == "保留旧版" for item in actions.values())
    assert all(period["status"] == "当前" for period in store.periods())


def test_retry_same_month_does_not_add_bill(isolate):
    base = (SAMPLES / "clean.csv").read_bytes()
    demand = demo_demand("2026-11")
    run(base, stop=8, demand=demand)
    first = store.periods()
    again = run(base, stop=8, demand=demand)
    assert all(item["action"] == "复用" for item in again["billing_actions"].values())
    assert [(p["id"], p["revision"]) for p in store.periods()] == [(p["id"], p["revision"]) for p in first]


def test_reviewed_month_keeps_original_record(isolate):
    base = (SAMPLES / "clean.csv").read_bytes()
    demand = demo_demand("2026-11")
    run(base, stop=8, demand=demand)
    store.review_period("2026-11", "站甲", 1, "通过")
    changed = demand.replace("D-JA,站甲,2026-11-16T14:30:00+08:00,2026-11-16T15:00:00+08:00,80,",
                             "D-JA,站甲,2026-11-16T14:30:00+08:00,2026-11-16T15:00:00+08:00,95,")
    assert changed != demand
    result = run(base, stop=8, demand=changed)
    assert result["billing_actions"]["站甲"]["action"] == "复查替代"
    records = [p for p in store.periods("2026-11") if p["station"] == "站甲"]
    assert len(records) == 2
    old = next(p for p in records if p["status"] == "已替代")
    assert old["review"] == "通过" and old["revision"] == 1
    new = next(p for p in records if p["status"] == "当前")
    assert new["replaces"] == old["id"] and new["review"] == "待复核"


def test_read_intervals_quarantines_conflict():
    csv = ("meter,station,start,end,kwh\n"
           "M1,S,2026-11-02T00:00:00+08:00,2026-11-02T00:30:00+08:00,5\n"
           "M1,S,2026-11-02T00:00:00+08:00,2026-11-02T00:30:00+08:00,6\n")
    good, bad = read_intervals(csv, "2026-11")
    assert good == [] and {item["code"] for item in bad} == {"CONFLICT"}


def test_read_intervals_requires_half_hour():
    csv = "meter,station,start,end,kwh\nM1,S,2026-11-02T00:00:00+08:00,2026-11-02T01:00:00+08:00,5\n"
    good, bad = read_intervals(csv, "2026-11")
    assert not good and bad[0]["code"] == "DURATION"


def test_read_intervals_rejects_other_month():
    csv = "meter,station,start,end,kwh\nM1,S,2026-12-02T00:00:00+08:00,2026-12-02T00:30:00+08:00,5\n"
    good, bad = read_intervals(csv, "2026-11")
    assert not good and bad[0]["code"] == "RANGE"


def test_demand_preview_does_not_bill(client):
    page = client.get("/m/8")
    assert page.status_code == 200
    assert client.get("/api/bills").json == []  # 预览页不写台账
    # 未显式提交回传时即便 POST 也只留预览，不用样例数据出账
    assert client.post("/api/m8", json={}).status_code == 200
    assert client.get("/api/bills").json == []


def test_demand_page_review_flow(client):
    assert client.get("/m/8").status_code == 200
    reply = client.post("/api/m8", json={"demand": demo_demand("2026-11")})
    assert reply.status_code == 200
    bills = client.get("/api/bills").json
    assert bills and bills[0]["status"] == "当前"
    body = {"version": bills[0]["revision"], "decision": "通过"}
    reply = client.post(f"/api/bills/{bills[0]['month']}/{bills[0]['station']}", json=body)
    assert reply.status_code == 200 and reply.json["review"] == "通过"
    assert client.post(f"/api/bills/{bills[0]['month']}/{bills[0]['station']}", json=body).status_code == 409
