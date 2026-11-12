"""API управленческого контура «Пульс PicPac» (УП-01…УП-05)."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from . import db as D
from . import pulse_data as PD
from . import security as S

router = APIRouter(prefix="/api/pulse", tags=["pulse"])
ALLOWED = {"director", "dept_head", "analyst", "operator"}
STATUSES = {"open", "in_progress", "verified", "cancelled"}


def init():
    PD.init()


def _err(status, code, message):
    return HTTPException(status, detail={"code": code, "message": message})


def pulse_user(authorization: str = Header(default="")):
    user = None
    if authorization.startswith("Bearer "):
        with D.db() as c:
            row = c.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token_hash=? AND s.expires_at>?",
                (S.sha(authorization[7:]), D.now_iso()),
            ).fetchone()
            user = dict(row) if row else None
    if not user:
        raise _err(401, "unauthorized", "Требуется вход")
    if user["role"] not in ALLOWED:
        with D.db() as c:
            D.audit(c, user, "pulse_access", "", "denied")
        raise _err(403, "forbidden", "Нет доступа к управленческому контуру")
    return user


def _scope(c, user, dept=""):
    if user["role"] != "dept_head":
        return dept
    row = c.execute("SELECT dept FROM pulse_user_dept WHERE user_id=?", (user["id"],)).fetchone()
    own = row["dept"] if row else ""
    if not own or (dept and dept != own):
        D.audit(c, user, "pulse_scope", dept, "denied")
        c.commit()  # отказ должен остаться в audit, даже если после него возвращаем 403
        raise _err(403, "forbidden", "Руководитель видит только своё подразделение")
    return own


def _status(fact, plan, higher=True, yellow=.9):
    if plan == 0:
        return "yellow"
    ratio = fact / plan if higher else plan / max(fact, .0001)
    return "green" if ratio >= 1 else ("yellow" if ratio >= yellow else "red")


def _kpi(code, name, dept, formula, source, updated, plan, fact, unit, data, higher=True, yellow=.9):
    return {"code": code, "name": name, "dept": dept, "formula": formula, "source": source,
            "updated_at": updated, "plan": round(plan, 2), "fact": round(fact, 2), "unit": unit,
            "status": _status(fact, plan, higher, yellow), "data": data}


def calculate_kpis(c, period="12w", dept="", product=""):
    orders = PD.order_rows(c, period, product)
    synthetic_ids = [o["id"] for o in orders if o["data"] == "synthetic"]
    prod = []
    if synthetic_ids:
        marks = ",".join("?" for _ in synthetic_ids)
        prod = [dict(r) for r in c.execute(f"SELECT * FROM pulse_production WHERE order_id IN ({marks})", synthetic_ids)]
    updated = max([o["updated_at"] for o in orders] or [D.now_iso()])
    revenue = sum(o["amount_byn"] for o in orders)
    completed = [o for o in orders if o.get("completed_at")]
    on_time = 100 * sum(o["completed_at"] <= o["due_at"] for o in completed) / max(len(completed), 1)
    units = sum(p["fact_units"] for p in prod)
    defects = sum(p["defects"] + p["reprints"] for p in prod)
    defect_rate = 100 * defects / max(units, 1)
    lead_hours = []
    from datetime import datetime
    for o in completed:
        try:
            lead_hours.append((datetime.fromisoformat(o["completed_at"]) - datetime.fromisoformat(o["order_date"])).total_seconds() / 3600)
        except (TypeError, ValueError):
            pass
    avg_check = revenue / max(len(orders), 1)
    new_clients = len({o["customer_id"] for o in orders})
    queue = sum(o["status"] not in ("completed", "ready", "issued") for o in orders)
    capacity = 100 * units / max(sum(p["plan_units"] for p in prod), 1)
    fin = [dict(r) for r in c.execute("SELECT * FROM pulse_finance WHERE week>=?", (PD.period_start(period)[:10],))]
    fin_revenue = sum(r["revenue_fact_byn"] for r in fin)
    fin_cost = sum(r["cost_fact_byn"] for r in fin)
    margin = 100 * (fin_revenue - fin_cost) / max(fin_revenue, 1)
    staff = [dict(r) for r in c.execute("SELECT * FROM pulse_staff WHERE week>=?", (PD.period_start(period)[:10],))]
    coverage = 100 * sum(r["headcount_fact"] for r in staff) / max(sum(r["headcount_plan"] for r in staff), 1)
    event_users = c.execute("SELECT COUNT(DISTINCT user_id) FROM events WHERE name='project_created'").fetchone()[0]
    buyers = c.execute("SELECT COUNT(DISTINCT user_id) FROM events WHERE name='order_created'").fetchone()[0]
    conversion = 100 * buyers / max(event_users, 1)
    tickets = c.execute("SELECT COUNT(*), SUM(CASE WHEN status!='open' THEN 1 ELSE 0 END) FROM tickets").fetchone()
    sla = 100 * (tickets[1] or 0) / max(tickets[0] or 0, 1)
    all_kpis = [
        _kpi("revenue", "Оборот", "sales", "Σ сумма заказов", "orders + mock_erp", updated, 12000, revenue, "BYN", "synthetic", True, .85),
        _kpi("avg_check", "Средний чек", "sales", "оборот / заказы", "orders + mock_erp", updated, 55, avg_check, "BYN", "synthetic"),
        _kpi("new_clients", "Новые клиенты", "sales", "COUNT DISTINCT customer_id", "mock_erp", updated, 55, new_clients, "клиентов", "synthetic"),
        _kpi("funnel_conversion", "Конверсия в заказ", "sales", "покупатели / создатели проекта × 100", "events", D.now_iso(), 28, conversion, "%", "real", True, .75),
        _kpi("on_time", "Заказы в срок", "production", "заказы в срок / завершённые × 100", "mock_erp", updated, 92, on_time, "%", "synthetic", True, .95),
        _kpi("defect_rate", "Брак и перепечатки", "production", "(брак + перепечатки) / выпуск × 100", "mock_mes", updated, 3, defect_rate, "%", "synthetic", False, .75),
        _kpi("queue", "Очередь производства", "production", "незавершённые заказы", "orders + mock_erp", updated, 8, queue, "заказов", "synthetic", False, .7),
        _kpi("lead_time", "Средний lead time", "production", "Σ часов от заказа до выпуска / заказы", "mock_mes", updated, 96, sum(lead_hours) / max(len(lead_hours), 1), "часов", "synthetic", False, .8),
        _kpi("capacity", "Загрузка мощности", "production", "факт выпуска / план × 100", "mock_mes", updated, 92, capacity, "%", "synthetic", True, .9),
        _kpi("gross_margin", "Валовая маржа", "finance", "(выручка - себестоимость) / выручка × 100", "mock_1c", updated, 40, margin, "%", "synthetic", True, .9),
        _kpi("staff_coverage", "Обеспеченность персоналом", "finance", "штат факт / штат план × 100", "mock_hr", updated, 98, coverage, "%", "synthetic", True, .97),
        _kpi("support_sla", "SLA поддержки", "sales", "закрытые обращения / все обращения × 100", "tickets", D.now_iso(), 90, sla, "%", "real", True, .8),
    ]
    return [k for k in all_kpis if not dept or k["dept"] == dept]


@router.get("/kpi")
def kpi(period: str = "12w", dept: str = "", product: str = "", user=Depends(pulse_user)):
    with D.db() as c:
        dept = _scope(c, user, dept)
        return {"period": period, "dept": dept, "product": product,
                "currency": "BYN", "items": calculate_kpis(c, period, dept, product)}


@router.get("/kpi/{code}/drill")
def drill(code: str, period: str = "12w", dept: str = "", product: str = "", user=Depends(pulse_user)):
    with D.db() as c:
        dept = _scope(c, user, dept)
        items = calculate_kpis(c, period, dept, product)
        item = next((x for x in items if x["code"] == code), None)
        if not item:
            raise _err(404, "not_found", "Показатель не найден")
        orders = PD.order_rows(c, period, product)
        details = []
        explanation = "Отклонение не требует отдельного объяснения."
        if code == "defect_rate":
            rows = c.execute(
                "SELECT shift,area,SUM(defects+reprints) bad,SUM(fact_units) units "
                "FROM pulse_production GROUP BY shift,area ORDER BY bad DESC"
            ).fetchall()
            details = [dict(r) for r in rows]
            top = details[0] if details else {"shift": "-", "area": "-", "bad": 0}
            explanation = (f"Красное отклонение локализовано: участок «{top['area']}», смена {top['shift']} "
                           f"дала {top['bad']} случаев брака/перепечатки. В синтетике заложен рост простоев этой смены.")
        elif code in ("revenue", "avg_check", "on_time", "queue", "lead_time"):
            details = orders[-30:]
        return {"kpi": item, "levels": {"dept": item["dept"], "product": product or "Все продукты",
                                          "orders": details}, "explanation": explanation}


class DecisionIn(BaseModel):
    problem: str = Field(min_length=3, max_length=300)
    kpi_code: str = Field(min_length=2, max_length=50)
    impact: str = Field(min_length=2, max_length=300)
    reason: str = Field(min_length=2, max_length=500)
    action: str = Field(min_length=2, max_length=500)
    owner: str = Field(min_length=2, max_length=100)
    due_at: str = Field(min_length=8, max_length=40)
    priority: str = Field(pattern="^(low|medium|high|critical)$")
    status: str = Field(default="open", pattern="^(open|in_progress|verified|cancelled)$")
    dept: str = Field(pattern="^(sales|production|finance)$")


class DecisionPatch(BaseModel):
    problem: Optional[str] = Field(default=None, min_length=3, max_length=300)
    impact: Optional[str] = Field(default=None, min_length=2, max_length=300)
    reason: Optional[str] = Field(default=None, min_length=2, max_length=500)
    action: Optional[str] = Field(default=None, min_length=2, max_length=500)
    owner: Optional[str] = Field(default=None, min_length=2, max_length=100)
    due_at: Optional[str] = Field(default=None, min_length=8, max_length=40)
    priority: Optional[str] = Field(default=None, pattern="^(low|medium|high|critical)$")
    status: Optional[str] = Field(default=None, pattern="^(open|in_progress|verified|cancelled)$")


def _decision(c, did):
    row = c.execute("SELECT * FROM pulse_decisions WHERE id=?", (did,)).fetchone()
    return dict(row) if row else None


@router.get("/decisions")
def decisions(status: str = "", dept: str = "", user=Depends(pulse_user)):
    with D.db() as c:
        dept = _scope(c, user, dept)
        q, args = "SELECT * FROM pulse_decisions WHERE 1=1", []
        if status:
            q += " AND status=?"; args.append(status)
        if dept:
            q += " AND dept=?"; args.append(dept)
        q += " ORDER BY id DESC"
        return [dict(r) for r in c.execute(q, args)]


@router.post("/decisions", status_code=201)
def create_decision(body: DecisionIn, user=Depends(pulse_user)):
    with D.db() as c:
        scoped = _scope(c, user, body.dept)
        now = D.now_iso()
        cur = c.execute(
            "INSERT INTO pulse_decisions(problem,kpi_code,impact,reason,action,owner,due_at,priority,status,dept,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (body.problem, body.kpi_code, body.impact, body.reason, body.action, body.owner, body.due_at,
             body.priority, body.status, scoped or body.dept, now, now),
        )
        row = _decision(c, cur.lastrowid)
        c.execute("INSERT INTO pulse_decision_log(decision_id,user_id,action,before_json,after_json,created_at) VALUES(?,?,?,?,?,?)",
                  (cur.lastrowid, user["id"], "created", None, json.dumps(row, ensure_ascii=False), now))
        D.audit(c, user, "pulse_decision_create", f"decision:{cur.lastrowid}", "ok", body.kpi_code)
        return row


@router.patch("/decisions/{did}")
def patch_decision(did: int, body: DecisionPatch, user=Depends(pulse_user)):
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise _err(422, "empty_patch", "Нет изменений")
    with D.db() as c:
        before = _decision(c, did)
        if not before:
            raise _err(404, "not_found", "Решение не найдено")
        _scope(c, user, before["dept"])
        changes["updated_at"] = D.now_iso()
        sets = ",".join(f"{key}=?" for key in changes)
        c.execute(f"UPDATE pulse_decisions SET {sets} WHERE id=?", (*changes.values(), did))
        after = _decision(c, did)
        action = "verified" if changes.get("status") == "verified" else "updated"
        c.execute("INSERT INTO pulse_decision_log(decision_id,user_id,action,before_json,after_json,created_at) VALUES(?,?,?,?,?,?)",
                  (did, user["id"], action, json.dumps(before, ensure_ascii=False),
                   json.dumps(after, ensure_ascii=False), D.now_iso()))
        D.audit(c, user, "pulse_decision_update", f"decision:{did}", "ok", action)
        return after


@router.get("/decisions/{did}/log")
def decision_log(did: int, user=Depends(pulse_user)):
    with D.db() as c:
        row = _decision(c, did)
        if not row:
            raise _err(404, "not_found", "Решение не найдено")
        _scope(c, user, row["dept"])
        return [dict(r) for r in c.execute("SELECT * FROM pulse_decision_log WHERE decision_id=? ORDER BY id", (did,))]


@router.get("/sources")
def sources(user=Depends(pulse_user)):
    if user["role"] not in ("analyst", "operator", "director"):
        with D.db() as c:
            D.audit(c, user, "pulse_sources", "", "denied")
        raise _err(403, "forbidden", "Источники доступны аналитику")
    with D.db() as c:
        return PD.source_health(c)
