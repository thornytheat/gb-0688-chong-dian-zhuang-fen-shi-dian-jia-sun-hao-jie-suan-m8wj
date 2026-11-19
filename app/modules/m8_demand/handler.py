from flask import Blueprint, request, jsonify
from app.pipeline import run
from app.errors import DomainError
from app import store

bp = Blueprint("m8", __name__)


@bp.route("/api/m8", methods=["GET", "POST"])
def endpoint():
    payload = request.get_json(silent=True) if request.method == "POST" else {}
    if not isinstance(payload, dict):
        raise DomainError("BODY", "请求必须是对象")
    raw = payload.get("text")
    if raw is not None and not isinstance(raw, str):
        raise DomainError("TEXT", "数据必须为文本")
    demand = payload.get("demand")
    if demand is not None and not isinstance(demand, str):
        raise DomainError("DEMAND", "需量数据必须为 CSV 文本")
    return jsonify(run(raw, payload.get("params"), stop=8, persist=request.method == "POST", demand=demand))


@bp.get("/api/bills")
def bills():
    return jsonify(store.periods(request.args.get("month")))


@bp.post("/api/bills/<month>/<station>")
def review(month, station):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise DomainError("BODY", "请求必须是对象")
    return jsonify(store.review_period(month, station, data.get("version"), data.get("decision")))
