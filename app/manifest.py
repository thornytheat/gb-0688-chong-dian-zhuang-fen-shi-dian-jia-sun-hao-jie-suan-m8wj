import hashlib, json
from datetime import datetime, timezone
from app.config import RULE_VERSION, DEMAND_RULE_VERSION
def make(raw,params,demand=b"",demand_month=None,demand_rate=None):
    digest=hashlib.sha256(raw).hexdigest()
    key=hashlib.sha256((digest+json.dumps(params,sort_keys=True,ensure_ascii=False)+RULE_VERSION).encode()).hexdigest()
    if demand:
        demand_digest=demand if isinstance(demand,str) else hashlib.sha256(demand).hexdigest()
        extra=json.dumps({"demand":demand_digest,"month":demand_month,
                          "rate":str(demand_rate),"rules":DEMAND_RULE_VERSION},
                         sort_keys=True,ensure_ascii=False)
        key=hashlib.sha256((key+extra).encode()).hexdigest()
    return {"run_id":key[:20],"sha256":digest,"rules":RULE_VERSION,"params":params,
            "created":datetime.now(timezone.utc).isoformat(),"bytes":len(raw)}
