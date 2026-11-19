from flask import render_template, request
from app import store
from app.pipeline import run
from app.config import TITLE, MODULES, COLUMNS, SAMPLES, DEFAULTS
from app.modules.m8_demand.rules import demo_demand
from app.modules.m8_demand.service import attach

def view(stage):
    if not 1 <= stage <= len(MODULES): return "页面不存在", 404
    run_id = request.args.get("run")
    result = store.fetch(run_id) if run_id else None
    if result is None:
        # 预览只存批次快照，不写月度账单；出账走 POST /api/m8。
        result = store.save(run(stop=len(MODULES), persist=False) if stage == len(MODULES) else run())
    elif stage == len(MODULES):
        # 旧批次只跑到第 7 步：沿用其订单与参数补出预览，不改动旧批次。
        result = attach(result)
    sample = (SAMPLES / "dirty.csv").read_text(encoding="utf-8-sig")
    data = result["stages"][stage - 1]
    periods = store.periods(result.get("demand_month")) if stage == len(MODULES) else []
    demand_sample = demo_demand(result.get("demand_month") or "2026-11") if stage == len(MODULES) else ""
    return render_template(f"m{stage}.html", title=TITLE, modules=MODULES, stage=stage, result=result,
                           data=data, sample=sample, demand_sample=demand_sample, defaults=DEFAULTS,
                           columns=COLUMNS, history=store.history(), periods=periods)
