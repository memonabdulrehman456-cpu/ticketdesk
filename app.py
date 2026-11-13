"""TicketDesk - Customer Service Ticket API + dashboard (Flask)."""
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, jsonify, render_template, request, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ticketdesk")

_default_db = "sqlite:///" + os.path.join(tempfile.gettempdir(), "ticketdesk.db").replace("\\", "/")
db_url = os.getenv("DATABASE_URL") or _default_db
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-only-change-me"),
    SQLALCHEMY_DATABASE_URI=db_url,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
db = SQLAlchemy(app)

PRIORITIES = ["low", "medium", "high", "critical"]
STATUSES = ["open", "in_progress", "resolved", "closed"]
SLA_HOURS = {"critical": 4, "high": 8, "medium": 24, "low": 72}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(d):
    return d.isoformat() + "Z" if d else None


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), default="agent", nullable=False)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "email": self.email, "role": self.role}


class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    customer_name = db.Column(db.String(80), nullable=False)
    customer_email = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(30), default="general", nullable=False)
    priority = db.Column(db.String(10), default="medium", nullable=False, index=True)
    status = db.Column(db.String(12), default="open", nullable=False, index=True)
    assignee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    assignee = db.relationship("User")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    due_at = db.Column(db.DateTime, nullable=False)
    resolved_at = db.Column(db.DateTime)

    def sla(self):
        if self.resolved_at:
            return "met" if self.resolved_at <= self.due_at else "breached"
        total = (self.due_at - self.created_at).total_seconds()
        left = (self.due_at - utcnow()).total_seconds()
        if left < 0:
            return "breached"
        return "at_risk" if left < total * 0.25 else "on_track"

    def to_dict(self):
        return {
            "id": self.id, "title": self.title, "description": self.description,
            "customer_name": self.customer_name, "customer_email": self.customer_email,
            "category": self.category, "priority": self.priority, "status": self.status,
            "assignee": self.assignee.to_dict() if self.assignee else None,
            "created_at": iso(self.created_at), "due_at": iso(self.due_at),
            "resolved_at": iso(self.resolved_at), "sla": self.sla(),
            "hours_left": round((self.due_at - utcnow()).total_seconds() / 3600, 1),
        }


class Audit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("ticket.id"), nullable=False, index=True)
    actor = db.Column(db.String(80), nullable=False)
    action = db.Column(db.String(30), nullable=False)
    detail = db.Column(db.String(300), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def to_dict(self):
        return {"id": self.id, "actor": self.actor, "action": self.action,
                "detail": self.detail, "created_at": iso(self.created_at)}


def audit(ticket, actor, action, detail):
    db.session.add(Audit(ticket_id=ticket.id, actor=actor, action=action, detail=detail))


def current_user():
    uid = session.get("uid")
    return db.session.get(User, uid) if uid else None


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        user = current_user()
        if not user:
            return jsonify(error="Please sign in to continue."), 401
        return fn(user, *a, **kw)
    return wrapper


def body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@app.errorhandler(Exception)
def handle_error(e):
    if isinstance(e, HTTPException):
        if request.path.startswith("/api"):
            return jsonify(error=e.description), e.code
        return e
    log.exception("Unhandled error")
    db.session.rollback()
    return jsonify(error="Something went wrong on our side. Please try again."), 500


# ---------- pages & health ----------
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", time=iso(utcnow()))


# ---------- auth ----------
@app.post("/api/login")
def login():
    d = body()
    user = User.query.filter_by(email=(d.get("email") or "").strip().lower()).first()
    if not user or not check_password_hash(user.password_hash, d.get("password") or ""):
        log.warning("Failed login for %s", d.get("email"))
        return jsonify(error="Email or password is incorrect."), 401
    session["uid"] = user.id
    log.info("Login: %s", user.email)
    return jsonify(user=user.to_dict())


@app.post("/api/signup")
def signup():
    d = body()
    name = (d.get("name") or "").strip()
    email = (d.get("email") or "").strip().lower()
    pw = d.get("password") or ""
    errs = {}
    if not 2 <= len(name) <= 80:
        errs["name"] = "Enter your name (2 to 80 characters)."
    if not EMAIL_RE.match(email) or len(email) > 120:
        errs["email"] = "Enter a valid email address."
    if len(pw) < 6:
        errs["password"] = "Password must be at least 6 characters."
    if errs:
        return jsonify(error=next(iter(errs.values())), fields=errs), 400
    if User.query.filter_by(email=email).first():
        return jsonify(error="An account with this email already exists. Sign in instead."), 409
    user = User(name=name, email=email, password_hash=generate_password_hash(pw), role="agent")
    db.session.add(user)
    db.session.commit()
    session["uid"] = user.id
    log.info("Signup: %s", email)
    return jsonify(user=user.to_dict()), 201


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    user = current_user()
    return jsonify(user=user.to_dict() if user else None)


@app.get("/api/agents")
@login_required
def agents(user):
    return jsonify(agents=[u.to_dict() for u in User.query.order_by(User.name)])


# ---------- tickets ----------
def validate_new(d):
    errs = {}
    title = (d.get("title") or "").strip()
    desc = (d.get("description") or "").strip()
    name = (d.get("customer_name") or "").strip()
    email = (d.get("customer_email") or "").strip().lower()
    priority = (d.get("priority") or "medium").lower()
    category = (d.get("category") or "general").strip().lower()[:30]
    if not 3 <= len(title) <= 120:
        errs["title"] = "Title must be 3 to 120 characters."
    if len(desc) < 5:
        errs["description"] = "Describe the issue in at least 5 characters."
    if not name:
        errs["customer_name"] = "Customer name is required."
    if not EMAIL_RE.match(email):
        errs["customer_email"] = "Enter a valid customer email."
    if priority not in PRIORITIES:
        errs["priority"] = "Priority must be low, medium, high or critical."
    return dict(title=title, description=desc, customer_name=name, customer_email=email,
                priority=priority, category=category or "general"), errs


@app.get("/api/tickets")
@login_required
def list_tickets(user):
    q = Ticket.query
    status, priority = request.args.get("status"), request.args.get("priority")
    assignee, text = request.args.get("assignee"), request.args.get("q", "").strip()
    if status in STATUSES:
        q = q.filter_by(status=status)
    elif status == "active":
        q = q.filter(Ticket.status.in_(["open", "in_progress"]))
    if priority in PRIORITIES:
        q = q.filter_by(priority=priority)
    if assignee == "unassigned":
        q = q.filter(Ticket.assignee_id.is_(None))
    elif assignee and assignee.isdigit():
        q = q.filter_by(assignee_id=int(assignee))
    if text:
        like = f"%{text}%"
        conds = [Ticket.title.ilike(like), Ticket.description.ilike(like),
                 Ticket.customer_name.ilike(like), Ticket.customer_email.ilike(like)]
        if text.lstrip("#").isdigit():
            conds.append(Ticket.id == int(text.lstrip("#")))
        q = q.filter(or_(*conds))
    items = [t.to_dict() for t in q.order_by(Ticket.created_at.desc()).limit(200)]
    if request.args.get("sla") in ("breached", "at_risk"):
        items = [t for t in items if t["sla"] == request.args["sla"]]
    return jsonify(tickets=items, count=len(items))


@app.post("/api/tickets")
@login_required
def create_ticket(user):
    clean, errs = validate_new(body())
    if errs:
        return jsonify(error="Please fix the highlighted fields.", fields=errs), 400
    now = utcnow()
    t = Ticket(**clean, created_at=now, due_at=now + timedelta(hours=SLA_HOURS[clean["priority"]]))
    db.session.add(t)
    db.session.flush()
    audit(t, user.name, "created", f"Ticket created with {t.priority} priority (SLA {SLA_HOURS[t.priority]}h)")
    db.session.commit()
    log.info("Ticket #%s created by %s", t.id, user.email)
    return jsonify(ticket=t.to_dict()), 201


@app.get("/api/tickets/<int:tid>")
@login_required
def get_ticket(user, tid):
    t = db.get_or_404(Ticket, tid, description="Ticket not found.")
    logs = Audit.query.filter_by(ticket_id=tid).order_by(Audit.id.desc()).all()
    return jsonify(ticket=t.to_dict(), audit=[a.to_dict() for a in logs])


@app.get("/api/tickets/<int:tid>/audit")
@login_required
def ticket_audit(user, tid):
    db.get_or_404(Ticket, tid, description="Ticket not found.")
    logs = Audit.query.filter_by(ticket_id=tid).order_by(Audit.id.desc()).all()
    return jsonify(audit=[a.to_dict() for a in logs])


@app.patch("/api/tickets/<int:tid>")
@login_required
def update_ticket(user, tid):
    t = db.get_or_404(Ticket, tid, description="Ticket not found.")
    d = body()
    if "status" in d:
        s = d["status"]
        if s not in STATUSES:
            return jsonify(error="Invalid status."), 400
        if s != t.status:
            audit(t, user.name, "status", f"Status changed from {t.status} to {s}")
            t.status = s
            t.resolved_at = utcnow() if s in ("resolved", "closed") and not t.resolved_at else (
                t.resolved_at if s in ("resolved", "closed") else None)
    if "priority" in d:
        p = d["priority"]
        if p not in PRIORITIES:
            return jsonify(error="Invalid priority."), 400
        if p != t.priority:
            audit(t, user.name, "priority", f"Priority changed from {t.priority} to {p}")
            t.priority = p
            if not t.resolved_at:
                t.due_at = t.created_at + timedelta(hours=SLA_HOURS[p])
    if "assignee_id" in d:
        aid = d["assignee_id"]
        new = None
        if aid not in (None, ""):
            new = db.session.get(User, int(aid)) if str(aid).isdigit() else None
            if not new:
                return jsonify(error="Agent not found."), 400
        if (new.id if new else None) != t.assignee_id:
            audit(t, user.name, "assigned", f"Assigned to {new.name}" if new else "Unassigned")
            t.assignee_id = new.id if new else None
    db.session.commit()
    return jsonify(ticket=t.to_dict())


@app.post("/api/tickets/<int:tid>/comments")
@login_required
def add_comment(user, tid):
    t = db.get_or_404(Ticket, tid, description="Ticket not found.")
    text = (body().get("text") or "").strip()
    if not text or len(text) > 300:
        return jsonify(error="Note must be 1 to 300 characters."), 400
    audit(t, user.name, "note", text)
    db.session.commit()
    return jsonify(ok=True), 201


@app.get("/api/stats")
@login_required
def stats(user):
    tickets = Ticket.query.all()
    active = [t for t in tickets if t.status in ("open", "in_progress")]
    done = [t for t in tickets if t.resolved_at]
    met = [t for t in done if t.sla() == "met"]
    hrs = [(t.resolved_at - t.created_at).total_seconds() / 3600 for t in done]
    return jsonify(
        total=len(tickets), active=len(active),
        breached=sum(t.sla() == "breached" for t in active),
        at_risk=sum(t.sla() == "at_risk" for t in active),
        unassigned=sum(t.assignee_id is None for t in active),
        resolved=len(done),
        sla_compliance=round(100 * len(met) / len(done)) if done else 100,
        avg_resolution_hours=round(sum(hrs) / len(hrs), 1) if hrs else 0,
        by_priority={p: sum(t.priority == p for t in active) for p in PRIORITIES},
    )


# ---------- seed ----------
def seed():
    if User.query.first():
        return
    users = [("Admin User", "admin@demo.com", "admin123", "admin"),
             ("Sara Khan", "sara@demo.com", "agent123", "agent"),
             ("Ali Raza", "ali@demo.com", "agent123", "agent")]
    objs = []
    for n, e, p, r in users:
        u = User(name=n, email=e, password_hash=generate_password_hash(p), role=r)
        db.session.add(u)
        objs.append(u)
    db.session.flush()
    rows = [
        ("Payment deducted but order not placed", "Money was charged twice and no order confirmation arrived.", "Hina Malik", "hina@example.com", "critical", "open", "billing", 1, 3),
        ("Cannot reset account password", "Reset link email never arrives.", "Usman Tariq", "usman@example.com", "high", "in_progress", "account", 2, 5),
        ("Wrong item delivered", "Received a blue shirt instead of black.", "Ayesha Noor", "ayesha@example.com", "medium", "open", "delivery", None, 6),
        ("App crashes on startup", "Crash after latest update on Android 14.", "Bilal Ahmed", "bilal@example.com", "high", "open", "technical", None, 2),
        ("Request invoice copy", "Need invoice for March for tax filing.", "Fatima Zahra", "fatima@example.com", "low", "resolved", "billing", 1, 30),
        ("Change delivery address", "Moved to a new address, order not shipped yet.", "Zain Abbas", "zain@example.com", "medium", "resolved", "delivery", 2, 20),
        ("Feature request: dark mode", "Would love a dark theme in the app.", "Maria Qureshi", "maria@example.com", "low", "open", "feedback", 2, 10),
        ("Refund not received after 10 days", "Return approved but refund still pending.", "Hamza Sheikh", "hamza@example.com", "high", "in_progress", "billing", 1, 9),
        ("Login blocked after wrong attempts", "Account locked, need urgent access.", "Noor Fatima", "noor@example.com", "critical", "resolved", "account", 1, 12),
        ("Coupon code not working", "SAVE20 shows invalid at checkout.", "Daniyal Shah", "daniyal@example.com", "low", "closed", "billing", 2, 60),
    ]
    now = utcnow()
    for title, desc, cn, ce, pr, st, cat, ai, ago in rows:
        created = now - timedelta(hours=ago)
        t = Ticket(title=title, description=desc, customer_name=cn, customer_email=ce, category=cat,
                   priority=pr, status=st, assignee_id=objs[ai].id if ai is not None else None,
                   created_at=created, due_at=created + timedelta(hours=SLA_HOURS[pr]))
        if st in ("resolved", "closed"):
            t.resolved_at = created + timedelta(hours=SLA_HOURS[pr] * (0.6 if ago < 25 else 1.4))
        db.session.add(t)
        db.session.flush()
        db.session.add(Audit(ticket_id=t.id, actor="System", action="created", detail=f"Ticket created with {pr} priority", created_at=created))
        if t.assignee_id:
            db.session.add(Audit(ticket_id=t.id, actor="Admin User", action="assigned", detail=f"Assigned to {objs[ai].name}", created_at=created + timedelta(minutes=10)))
    db.session.commit()
    log.info("Database seeded with demo data")


with app.app_context():
    try:
        db.create_all()
        seed()
    except Exception:
        log.exception("Database init failed")

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("PORT", 5000)))
