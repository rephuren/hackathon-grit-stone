"""Детерминированные данные управленческого контура «Пульс PicPac»."""
import json
import os
import random
from datetime import datetime, timedelta, timezone

from . import db as D

DEPTS = ("sales", "production", "finance")
PRODUCTS = ("Фотокнига", "Календарь", "Фотопечать")
ANCHOR = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pulse_orders(
 id TEXT PRIMARY KEY, order_date TEXT NOT NULL, customer_id TEXT NOT NULL,
 product TEXT NOT NULL, channel TEXT NOT NULL, segment TEXT NOT NULL,
 amount_byn REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'BYN', status TEXT NOT NULL,
 due_at TEXT NOT NULL, completed_at TEXT, source TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pulse_production(
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL,
 dept TEXT NOT NULL, shift TEXT NOT NULL, area TEXT NOT NULL,
 plan_units INTEGER NOT NULL, fact_units INTEGER NOT NULL,
 defects INTEGER NOT NULL, reprints INTEGER NOT NULL, downtime_min INTEGER NOT NULL,
 started_at TEXT NOT NULL, completed_at TEXT, source TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pulse_finance(
 id INTEGER PRIMARY KEY AUTOINCREMENT, week TEXT NOT NULL, dept TEXT NOT NULL,
 revenue_plan_byn REAL NOT NULL, revenue_fact_byn REAL NOT NULL,
 cost_plan_byn REAL NOT NULL, cost_fact_byn REAL NOT NULL,
 currency TEXT NOT NULL DEFAULT 'BYN', source TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(week, dept));
CREATE TABLE IF NOT EXISTS pulse_staff(
 id INTEGER PRIMARY KEY AUTOINCREMENT, week TEXT NOT NULL, dept TEXT NOT NULL,
 headcount_plan INTEGER NOT NULL, headcount_fact INTEGER NOT NULL, absences INTEGER NOT NULL,
 source TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(week, dept));
CREATE TABLE IF NOT EXISTS pulse_user_dept(
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 dept TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pulse_decisions(
 id INTEGER PRIMARY KEY AUTOINCREMENT, problem TEXT NOT NULL, kpi_code TEXT NOT NULL,
 impact TEXT NOT NULL, reason TEXT NOT NULL, action TEXT NOT NULL, owner TEXT NOT NULL,
 due_at TEXT NOT NULL, priority TEXT NOT NULL, status TEXT NOT NULL, dept TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pulse_decision_log(
 id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER NOT NULL REFERENCES pulse_decisions(id) ON DELETE CASCADE,
 user_id INTEGER, action TEXT NOT NULL, before_json TEXT, after_json TEXT,
 created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_pulse_orders_date ON pulse_orders(order_date);
CREATE INDEX IF NOT EXISTS idx_pulse_prod_order ON pulse_production(order_id);
CREATE INDEX IF NOT EXISTS idx_pulse_decisions_dept ON pulse_decisions(dept, status);
"""


def _iso(value):
    return value.isoformat(timespec="seconds")


def init():
    """Создаёт pulse_* и заполняет только пустые таблицы фиксированной синтетикой."""
    from . import security

    with D.db() as c:
        c.executescript(SCHEMA)
        if os.environ.get("SEED_TEST_USERS", "1") == "1":
            users = [
                ("director", "director123", "director", "Директор (тест)", None),
                ("head_sales", "head123", "dept_head", "Руководитель продаж (тест)", "sales"),
                ("head_prod", "head123", "dept_head", "Руководитель производства (тест)", "production"),
                ("analyst", "analyst123", "analyst", "Аналитик (тест)", None),
            ]
            for login, password, role, name, dept in users:
                row = c.execute("SELECT id FROM users WHERE login=?", (login,)).fetchone()
                if not row:
                    cur = c.execute(
                        "INSERT INTO users(login,pass_hash,role,name,created_at) VALUES(?,?,?,?,?)",
                        (login, security.hash_password(password), role, name, D.now_iso()),
                    )
                    uid = cur.lastrowid
                else:
                    uid = row["id"]
                if dept:
                    c.execute(
                        "INSERT INTO pulse_user_dept(user_id,dept) VALUES(?,?) "
                        "ON CONFLICT(user_id) DO UPDATE SET dept=excluded.dept",
                        (uid, dept),
                    )
        if not c.execute("SELECT 1 FROM pulse_orders LIMIT 1").fetchone():
            _seed(c)


def _seed(c):
    rng = random.Random(2405)
    updated = _iso(ANCHOR)
    for i in range(168):
        created = ANCHOR - timedelta(days=83 - i // 2, hours=i % 7)
        product = PRODUCTS[i % len(PRODUCTS)]
        channel = ("PWA", "Telegram", "Оператор")[i % 3]
        segment = ("семья", "выпускной", "подарок", "свадьба")[i % 4]
        amount = round(28 + (i % 9) * 6.4 + rng.uniform(-4, 8), 2)
        due = created + timedelta(days=3 + i % 3)
        late = i % 13 == 0 or (i > 130 and i % 7 == 0)
        completed = due + timedelta(hours=(18 if late else -rng.randint(2, 28)))
        oid = f"SYN-{i + 1:04d}"
        c.execute(
            "INSERT INTO pulse_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, _iso(created), f"C-{i % 64:03d}", product, channel, segment,
             amount, "BYN", "completed", _iso(due), _iso(completed), "mock_erp", updated),
        )
        shift = "B" if i % 4 == 0 else "A"
        area = "Переплёт" if i % 3 == 0 else ("Печать" if i % 3 == 1 else "Резка")
        plan_units = 1 + i % 4
        # Последний месяц специально содержит красную причину: смена B на переплёте.
        hotspot = i > 112 and shift == "B" and area == "Переплёт"
        defects = 1 + (i % 2) if hotspot else (1 if i % 37 == 0 else 0)
        reprints = defects
        downtime = 42 + i % 31 if hotspot else i % 12
        started = created + timedelta(hours=5)
        c.execute(
            "INSERT INTO pulse_production(order_id,dept,shift,area,plan_units,fact_units,defects,reprints,"
            "downtime_min,started_at,completed_at,source,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, "production", shift, area, plan_units, plan_units, defects, reprints,
             downtime, _iso(started), _iso(completed), "mock_mes", updated),
        )
    for w in range(12):
        week = (ANCHOR - timedelta(weeks=11 - w)).date().isoformat()
        for dept, factor in (("sales", 1.0), ("production", .72), ("finance", .18)):
            plan = 5200 * factor
            fact = plan * (0.94 + (w % 4) * .025)
            cost_plan = plan * .60
            cost_fact = fact * (.63 if w > 8 else .59)
            c.execute(
                "INSERT INTO pulse_finance(week,dept,revenue_plan_byn,revenue_fact_byn,cost_plan_byn,cost_fact_byn,"
                "currency,source,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (week, dept, round(plan, 2), round(fact, 2), round(cost_plan, 2), round(cost_fact, 2),
                 "BYN", "mock_1c", updated),
            )
            hp = {"sales": 12, "production": 24, "finance": 5}[dept]
            hf = hp - (1 if dept == "production" and w > 8 else 0)
            c.execute(
                "INSERT INTO pulse_staff(week,dept,headcount_plan,headcount_fact,absences,source,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (week, dept, hp, hf, (w + len(dept)) % 3, "mock_hr", updated),
            )


def period_start(period):
    value = (period or "12w").lower().strip()
    try:
        if value.endswith("w"):
            days = int(value[:-1]) * 7
        elif value.endswith("d"):
            days = int(value[:-1])
        else:
            days = 84
    except ValueError:
        days = 84
    days = max(7, min(days, 366))
    return _iso(ANCHOR - timedelta(days=days))


def order_rows(c, period="12w", product=""):
    start = period_start(period)
    q = "SELECT *, 'synthetic' AS data FROM pulse_orders WHERE order_date>=?"
    args = [start]
    if product:
        q += " AND product=?"
        args.append(product)
    rows = [dict(r) for r in c.execute(q, args)]
    # TEST-заказы приложения участвуют как real, если попадают в период/продукт.
    real = c.execute("SELECT id,created_at,product_title,total_kop,status,eta FROM orders WHERE created_at>=?", (start,)).fetchall()
    for r in real:
        if product and r["product_title"] != product:
            continue
        rows.append({"id": f"REAL-{r['id']}", "order_date": r["created_at"], "customer_id": "real-test",
                     "product": r["product_title"], "channel": "PWA", "segment": "test",
                     "amount_byn": r["total_kop"] / 100, "currency": "BYN", "status": r["status"],
                     "due_at": r["eta"] or r["created_at"], "completed_at": None,
                     "source": "orders", "updated_at": r["created_at"], "data": "real"})
    return rows


def source_health(c):
    result = []
    for table, source, kind in (
        ("pulse_orders", "mock_erp", "synthetic"),
        ("pulse_production", "mock_mes", "synthetic"),
        ("pulse_finance", "mock_1c", "synthetic"),
        ("pulse_staff", "mock_hr", "synthetic"),
        ("orders", "orders", "real"),
        ("events", "events", "real"),
        ("tickets", "tickets", "real"),
    ):
        count = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        updated = D.now_iso()
        if table.startswith("pulse_") and count:
            updated = c.execute(f"SELECT MAX(updated_at) FROM {table}").fetchone()[0]
        result.append({"source": source, "table": table, "records": count, "updated_at": updated,
                       "data": kind, "missing": 0, "duplicates": 0})
    return result

