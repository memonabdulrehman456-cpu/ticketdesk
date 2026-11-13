# TicketDesk - Customer Service Ticket System

Flask backend + responsive dashboard for ticket creation, assignment, prioritisation, search, live SLA tracking and a full audit trail.

**Demo logins:** `admin@demo.com / admin123` and `sara@demo.com / agent123`

## Features
- Sign up and sign in with sessions and password hashing, JSON REST API
- Create, search and filter tickets; assign agents; change status and priority
- SLA per priority (Critical 4h, High 8h, Medium 24h, Low 72h): on track / at risk / breached
- Audit trail for every create, assign, status, priority change and note
- Dashboard stats: active, breached, at risk, unassigned, SLA compliance, average resolution time
- Input validation, logging, JSON error handling, env-based config, pytest suite

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python app.py        # http://localhost:5000
pytest               # run tests
```

## API
| Method | Endpoint | Purpose |
|---|---|---|
| POST | /api/signup, /api/login, /api/logout | Auth |
| GET | /api/tickets?status=&priority=&assignee=&q=&sla= | Search / filter |
| POST | /api/tickets | Create ticket |
| GET | /api/tickets/{id} | Ticket + audit trail |
| PATCH | /api/tickets/{id} | Update status, priority, assignee_id |
| POST | /api/tickets/{id}/comments | Add note |
| GET | /api/tickets/{id}/audit | Audit trail |
| GET | /api/stats, /api/agents, /api/health | Dashboard data |

## Deploy on Vercel
1. Push to GitHub, import the repo in Vercel (framework preset: Other).
2. Add env var `SECRET_KEY` (long random string).
3. For persistent data add `DATABASE_URL` from a free Postgres (Neon or Supabase). Without it the app uses SQLite in `/tmp`, which resets on cold starts (fine for demos).
4. Deploy. Tables and demo data are created automatically on first start.

## Limitations and next steps
Role-based permissions, email notifications, pagination beyond 200 tickets, rate limiting, Alembic migrations, file attachments.
