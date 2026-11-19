import os,sqlite3,json,hashlib
from pathlib import Path
from app.errors import DomainError
BILL_DDL=("CREATE TABLE IF NOT EXISTS bills ("
          "id INTEGER PRIMARY KEY AUTOINCREMENT,month TEXT NOT NULL,station TEXT NOT NULL,"
          "revision INTEGER NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL,"
          "status TEXT NOT NULL DEFAULT '当前',review TEXT NOT NULL DEFAULT '待复核',"
          "reviewed_at TEXT,replaces INTEGER,created TEXT NOT NULL,run_id TEXT,"
          "UNIQUE(month,station,revision))")
# 这些补数状态下的结算不可用于出账；已有可读账单时保留旧版。
UNBILLABLE={"补数失败","无回传","需量表不唯一"}
def connection():
    path=Path(os.environ.get("DATA_DIR","data"));path.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path/"runs.sqlite",timeout=15)
    db.row_factory=sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,review TEXT NOT NULL DEFAULT '待复核')")
    db.execute(BILL_DDL)
    return db
def save(result):
    with connection() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES(?,?)",(result["manifest"]["run_id"],json.dumps(result,ensure_ascii=False)))
    return fetch(result["manifest"]["run_id"])
def fetch(run_id):
    with connection() as db: row=db.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
    if row is None: raise DomainError("NOT_FOUND","批次不存在",404)
    result=json.loads(row["payload"]);result["version"]=row["version"];result["review"]=row["review"]
    return result
def history():
    with connection() as db: rows=db.execute("SELECT id,version,review FROM runs ORDER BY rowid DESC LIMIT 100").fetchall()
    return [dict(row) for row in rows]
def review(run_id,version,decision):
    if decision not in ("通过","退回"): raise DomainError("REVIEW","请选择通过或退回")
    if not isinstance(version,int): raise DomainError("STALE","版本必须为整数")
    with connection() as db:
        cur=db.execute("UPDATE runs SET review=?,version=version+1 WHERE id=? AND version=?",(decision,run_id,version))
        if cur.rowcount!=1: raise DomainError("STALE","复核版本已更新",409)
    return fetch(run_id)

def _decode(row):
    item=json.loads(row["payload"]);item["id"]=row["id"];item["month"]=row["month"]
    item["station"]=row["station"];item["revision"]=row["revision"]
    item["status"]=row["status"];item["review"]=row["review"];item["reviewed_at"]=row["reviewed_at"]
    item["replaces"]=row["replaces"];item["created"]=row["created"];item["run_id"]=row["run_id"]
    item["fingerprint"]=row["fingerprint"]
    return item

def _current(db,month,station):
    return db.execute("SELECT * FROM bills WHERE month=? AND station=? AND status='当前' ORDER BY revision DESC LIMIT 1",(month,station)).fetchone()

def settle_month(result):
    """把需量结算写入月度账单台账。

    - 同一站点同一月份只有一笔“当前”账单；相同输入重试复用原账单，不新增。
    - 输入变化且原账单未复核：就地升版，仍只有一笔当前账单。
    - 输入变化且原账单已复核：原记录保留为“已替代”历史，再插入新的当前版本。
    - 补数失败：保留上一版可读结果，不用不可结算的数据覆盖它。
    """
    month=result.get("demand_month") or result["params"].get("month")
    stamp=result["manifest"]["created"]
    saved=[]
    with connection() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES(?,?)",(result["manifest"]["run_id"],json.dumps(result,ensure_ascii=False)))
        for settlement in result.get("demand",[]):
            station=settlement["station"]
            payload=dict(settlement)
            payload["responsibility"]=[dict(item) for item in result.get("demand_responsibility",[]) if item["station"]==station]
            text=json.dumps(payload,ensure_ascii=False,default=str,sort_keys=True)
            # 指纹只看本站自身的结算内容，其他站点变化不会牵连本站版本。
            fingerprint=hashlib.sha256((text+"|"+result["manifest"]["rules"]).encode()).hexdigest()
            current=_current(db,month,station)
            if settlement["completeness"] in UNBILLABLE:
                if current is None:
                    saved.append({"station":station,"id":None,"revision":None,"action":"未出账",
                                  "reason":f"{settlement['completeness']}且无历史可读结算，本月暂不出账"})
                else:
                    saved.append({**_decode(current),"action":"保留旧版",
                                  "reason":f"{settlement['completeness']}，沿用上一版可读结算"})
                continue
            if current is not None and current["fingerprint"]==fingerprint:
                saved.append({**_decode(current),"action":"复用","reason":"同月同输入重试，不新增账单"})
                continue
            if current is not None and current["reviewed_at"] is None:
                db.execute("UPDATE bills SET revision=revision+1,fingerprint=?,payload=?,created=?,run_id=? WHERE id=?",
                           (fingerprint,text,stamp,result["manifest"]["run_id"],current["id"]))
                fresh=db.execute("SELECT * FROM bills WHERE id=?",(current["id"],)).fetchone()
                saved.append({**_decode(fresh),"action":"升版","reason":"原账单未复核，就地更新为当前版本"})
                continue
            if current is not None:
                db.execute("UPDATE bills SET status='已替代' WHERE id=?",(current["id"],))
            revision=(current["revision"]+1) if current is not None else 1
            cur=db.execute("INSERT INTO bills(month,station,revision,fingerprint,payload,status,review,replaces,created,run_id) "
                           "VALUES(?,?,?,?,?,'当前','待复核',?,?,?)",
                           (month,station,revision,fingerprint,text,current["id"] if current is not None else None,
                            stamp,result["manifest"]["run_id"]))
            fresh=db.execute("SELECT * FROM bills WHERE id=?",(cur.lastrowid,)).fetchone()
            saved.append({**_decode(fresh),"action":"新建" if current is None else "复查替代",
                          "reason":"首次结算" if current is None else "原账单已复核，保留原记录并生成新版本"})
    result["billing"]=saved
    result["billing_month"]=month
    result["billing_actions"]={item["station"]:{"action":item["action"],"reason":item["reason"],"id":item["id"],"revision":item["revision"]} for item in saved}
    with connection() as db:
        db.execute("UPDATE runs SET payload=? WHERE id=?",
                   (json.dumps(result,ensure_ascii=False),result["manifest"]["run_id"]))
    return result

def periods(month=None):
    with connection() as db:
        if month:
            rows=db.execute("SELECT * FROM bills WHERE month=? ORDER BY station,revision DESC",(month,)).fetchall()
        else:
            rows=db.execute("SELECT * FROM bills ORDER BY month DESC,station,revision DESC").fetchall()
    return [_decode(row) for row in rows]

def review_period(month,station,version,decision):
    if decision not in ("通过","退回"): raise DomainError("REVIEW","请选择通过或退回")
    if not isinstance(version,int): raise DomainError("STALE","版本必须为整数")
    import datetime
    stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
    with connection() as db:
        row=_current(db,month,station)
        if row is None: raise DomainError("NOT_FOUND","该月份站点没有当前账单",404)
        if row["revision"]!=version: raise DomainError("STALE","复核版本已更新",409)
        db.execute("UPDATE bills SET review=?,reviewed_at=? WHERE id=? AND revision=? AND reviewed_at IS NULL",(decision,stamp,row["id"],version))
        if db.execute("SELECT changes()").fetchone()[0]!=1: raise DomainError("STALE","复核版本已更新",409)
        fresh=db.execute("SELECT * FROM bills WHERE id=?",(row["id"],)).fetchone()
    return _decode(fresh)
