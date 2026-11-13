import os
os.environ["DATABASE_URL"] = "sqlite://"
import pytest
from datetime import timedelta
from app import app, db, Ticket, utcnow


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def auth(client):
    r = client.post("/api/login", json={"email": "admin@demo.com", "password": "admin123"})
    assert r.status_code == 200
    return client


NEW = dict(title="Test issue", description="Something broke", customer_name="Test User",
           customer_email="t@example.com", priority="high")


def test_home_and_health(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/health").json["status"] == "ok"


def test_auth_required(client):
    assert client.get("/api/tickets").status_code == 401
    assert client.post("/api/login", json={"email": "x@y.com", "password": "bad"}).status_code == 401
    assert client.post("/api/login", data="not json").status_code == 401


def test_create_validation(auth):
    r = auth.post("/api/tickets", json={"title": "x", "customer_email": "bad"})
    assert r.status_code == 400 and "title" in r.json["fields"] and "customer_email" in r.json["fields"]


def test_create_assign_status_audit(auth):
    t = auth.post("/api/tickets", json=NEW).json["ticket"]
    assert t["sla"] == "on_track" and t["status"] == "open"
    agents = auth.get("/api/agents").json["agents"]
    auth.patch(f"/api/tickets/{t['id']}", json={"assignee_id": agents[1]["id"], "status": "resolved"})
    d = auth.get(f"/api/tickets/{t['id']}").json
    assert d["ticket"]["assignee"]["id"] == agents[1]["id"]
    assert d["ticket"]["sla"] == "met"
    assert {a["action"] for a in d["audit"]} >= {"created", "assigned", "status"}
    assert auth.patch(f"/api/tickets/{t['id']}", json={"status": "bogus"}).status_code == 400
    assert auth.get("/api/tickets/99999").status_code == 404


def test_sla_breach_and_search(auth):
    t = auth.post("/api/tickets", json={**NEW, "title": "Unique zebra problem", "priority": "critical"}).json["ticket"]
    with app.app_context():
        row = db.session.get(Ticket, t["id"])
        row.created_at = utcnow() - timedelta(hours=10)
        row.due_at = row.created_at + timedelta(hours=4)
        db.session.commit()
    assert auth.get(f"/api/tickets/{t['id']}").json["ticket"]["sla"] == "breached"
    assert any(x["id"] == t["id"] for x in auth.get("/api/tickets?sla=breached").json["tickets"])
    assert auth.get("/api/tickets?q=zebra").json["count"] == 1
    assert auth.get("/api/stats").json["breached"] >= 1


def test_comment(auth):
    assert auth.post("/api/tickets/1/comments", json={"text": ""}).status_code == 400
    assert auth.post("/api/tickets/1/comments", json={"text": "Called customer"}).status_code == 201


def test_signup(client):
    bad = client.post("/api/signup", json={"name": "A", "email": "nope", "password": "123"})
    assert bad.status_code == 400 and {"name", "email", "password"} <= set(bad.json["fields"])
    ok = client.post("/api/signup", json={"name": "New Agent", "email": "New@Example.com", "password": "secret1"})
    assert ok.status_code == 201 and ok.json["user"]["role"] == "agent"
    assert client.get("/api/tickets").status_code == 200  # auto signed in
    assert client.post("/api/signup", json={"name": "Dup", "email": "new@example.com", "password": "secret1"}).status_code == 409
    c2 = app.test_client()
    assert c2.post("/api/login", json={"email": "new@example.com", "password": "secret1"}).status_code == 200
