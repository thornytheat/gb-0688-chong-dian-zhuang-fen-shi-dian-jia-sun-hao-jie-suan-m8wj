from importlib import import_module
from time import perf_counter
from app.config import STAGES, DEFAULTS, SAMPLES
from app.manifest import make
from app.normalize import serial
from app.errors import DomainError
from app import store

def run(raw=None, params=None, stop=7, persist=True, demand=None):
    if raw is None: raw=(SAMPLES/"dirty.csv").read_bytes()
    if isinstance(raw,str): raw=raw.encode("utf-8")
    if not isinstance(params,(dict,type(None))): raise DomainError("PARAMS","参数必须是对象")
    actual={**DEFAULTS,**(params or {})}
    stamp=perf_counter();ctx={"source":raw,"params":actual,"stages":[],"quarantine":[]}
    if demand is not None: ctx["demand_source"]=demand
    for stage in range(1,stop+1):
        mod=import_module(f"app.modules.m{stage}_{STAGES[stage-1]}.service")
        ctx=mod.execute(ctx)
    ctx.pop("source",None);ctx.pop("raw",None);ctx.pop("demand_source",None)
    result=serial(ctx)
    if "demand_sha" in result:
        result["manifest"]=make(raw,actual,result["demand_sha"],result.get("demand_month"),actual.get("demand_rate"))
    else:
        result["manifest"]=make(raw,actual)
    result["elapsed_ms"]=round((perf_counter()-stamp)*1000,3)
    result["version"]=1;result["review"]="待复核"
    # 只有显式提交需量回传才出月度账单；缺少时仅保存预览快照，避免用样例数据出账。
    if persist and stop==len(STAGES) and demand is not None: return store.settle_month(store.save(result))
    if persist and stop in (7, len(STAGES)): return store.save(result)
    return result
