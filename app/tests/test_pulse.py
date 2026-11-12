"""Критерии УП-01…УП-05 для управленческого контура «Пульс»."""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="picpac_pulse_test_")
os.environ["SEED_TEST_USERS"] = "1"
os.environ["OPERATOR_PASSWORD"] = ""
os.environ["PULSE_PASSWORD"] = ""  # тестовые пароли ролей, а не пароль стенда из .env
os.environ["TELEGRAM_BOT_TOKEN"] = ""

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import db as D  # noqa: E402
from backend import main as M  # noqa: E402
from backend import pulse as P  # noqa: E402

D.init_db()
P.init()
pulse_app = FastAPI()
pulse_app.include_router(M.api)
pulse_app.include_router(P.router)


@pytest.fixture(scope="module")
def client():
    with TestClient(pulse_app) as c:
        yield c


def login(client, name, password):
    response = client.post("/api/auth/login", json={"login": name, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def test_client_denied_and_director_gets_kpis(client):
    client_headers = login(client, "demo", "demo123")
    assert client.get("/api/pulse/kpi", headers=client_headers).status_code == 403

    director = login(client, "director", "director123")
    response = client.get("/api/pulse/kpi", headers=director)
    assert response.status_code == 200
    rows = response.json()["items"]
    assert len(rows) >= 10
    assert all({"formula", "source", "updated_at", "plan", "fact", "status", "dept", "data"} <= set(row) for row in rows)
    assert {row["data"] for row in rows} == {"real", "synthetic"}


def test_filters_and_red_drill(client):
    h = login(client, "director", "director123")
    sales = client.get("/api/pulse/kpi?dept=sales&period=4w", headers=h).json()["items"]
    production = client.get("/api/pulse/kpi?dept=production&period=4w", headers=h).json()["items"]
    assert sales and production
    assert {row["code"] for row in sales} != {row["code"] for row in production}
    drill = client.get("/api/pulse/kpi/defect_rate/drill?period=4w&dept=production", headers=h)
    assert drill.status_code == 200
    body = drill.json()
    assert body["kpi"]["status"] == "red"
    assert "смена" in body["explanation"].lower()
    assert body["levels"]["orders"]
    assert {"id", "shift", "area", "defects"} <= set(body["levels"]["orders"][0])


def test_decision_create_patch_logs_and_audit(client):
    h = login(client, "director", "director123")
    created = client.post("/api/pulse/decisions", headers=h, json={
        "problem": "Рост доли перепечаток",
        "kpi_code": "defect_rate",
        "impact": "Задержка выпуска и рост себестоимости",
        "reason": "Простой переплётного участка в смене B",
        "action": "Провести обслуживание и контроль первых 10 экземпляров",
        "owner": "Начальник производства",
        "due_at": "2026-11-15",
        "priority": "high",
        "status": "open",
        "dept": "production",
    })
    assert created.status_code == 201, created.text
    did = created.json()["id"]
    patched = client.patch(f"/api/pulse/decisions/{did}", headers=h, json={"status": "verified"})
    assert patched.status_code == 200
    assert patched.json()["status"] == "verified"
    assert patched.json()["verification_kpi"]["code"] == "defect_rate"
    with D.db() as c:
        assert c.execute("SELECT COUNT(*) FROM pulse_decision_log WHERE decision_id=?", (did,)).fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM audit WHERE object=? AND action='pulse_decision_update'", (f"decision:{did}",)).fetchone()[0] == 1


def test_department_head_cannot_read_other_department(client):
    h = login(client, "head_sales", "head123")
    own = client.get("/api/pulse/kpi", headers=h)
    assert own.status_code == 200
    assert {row["dept"] for row in own.json()["items"]} == {"sales"}
    denied = client.get("/api/pulse/kpi?dept=production", headers=h)
    assert denied.status_code == 403
    with D.db() as c:
        assert c.execute("SELECT COUNT(*) FROM audit WHERE user_id=(SELECT id FROM users WHERE login='head_sales') "
                         "AND result='denied'").fetchone()[0] >= 1
