# JABU Smart-Gate & Access Management System (SAMS)
### Joseph Ayo Babalola University — Final Year Project v0.0.1

An enterprise-grade, offline-first campus access control and logistics ecosystem that eliminates gate congestion and double-luggage searches through TOTP-based QR scanning, local SQLite caching, and asynchronous cloud sync.

---

## 🏗️ Architecture Overview

```
[Student App (Flutter)] ──QR optical scan──► [Guard Tablet (Flutter + SQLite)]
                                                        │
                                          [Background Sync Manager]
                                                        │ bulk POST (when online)
                                                        ▼
                                         [Cloud Core: Flask API + PostgreSQL]
```

---

## 📁 Phase 1 Project Structure

```
JABU---SAMS-0.0.1/
├── run.py                    # Application entry point
├── requirements.txt          # Python dependencies
├── .env.example              # Environment variable template
├── .gitignore
└── app/
    ├── __init__.py           # Flask Application Factory
    ├── config.py             # Environment-specific configuration
    ├── admin_views.py        # Flask-Admin model views
    ├── celery_worker.py      # Phase 2: Celery task definitions
    ├── models/
    │   ├── __init__.py
    │   └── core.py           # SQLAlchemy ORM models (UUID PKs)
    ├── edge/                 # Phase 3: Tablet-side offline layer
    │   ├── __init__.py
    │   ├── sqlite_schema.py  # Canonical SQLite DDL (5 tables)
    │   └── validators.py     # Offline TOTP + RBAC validation engine
    └── api/
        ├── __init__.py
        ├── auth.py           # JWT login / refresh / /me
        ├── users.py          # User CRUD + TOTP secret generation
        ├── rooms.py          # Rooms & session-based allocations
        ├── access_logs.py    # Immutable scan event audit trail
        ├── sync.py           # Phase 2: POST /upload, GET /status, GET /health
        └── pull_sync.py      # Phase 3: GET /pull/* delta sync endpoints
```

---

## 🚀 Quick Start

### 1. Clone and set up environment
```bash
git clone <repo-url>
cd JABU---SAMS-0.0.1

python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/macOS

pip install -r requirements.txt
```

### 2. Configure environment variables
```bash
copy .env.example .env
# Edit .env with your PostgreSQL credentials and secret keys
```

### 3. Set up the PostgreSQL database
```sql
-- Run in psql:
CREATE USER jabu_user WITH PASSWORD 'jabu_password';
CREATE DATABASE jabu_sams_db OWNER jabu_user;
GRANT ALL PRIVILEGES ON DATABASE jabu_sams_db TO jabu_user;
```

### 4. Run database migrations
```bash
flask db init      # Only once — creates the migrations/ folder
flask db migrate -m "Initial schema: Users, Rooms, Allocations, AccessLogs"
flask db upgrade   # Applies migrations to PostgreSQL
```

### 5. Start the development server
```bash
python run.py
# Server runs at http://localhost:5000
# Admin panel at http://localhost:5000/sams-admin
```

---

## 🔑 Key Design Decisions

| Decision | Rationale |
|---|---|
| **UUID Primary Keys** | Offline tablets generate PKs locally. UUIDs prevent collision when two tablets sync to the cloud simultaneously. |
| **Two-Timestamp Strategy** | `scan_timestamp` (tablet) vs `server_timestamp` (cloud) measures offline duration and resolves sync conflicts. |
| **Soft Deletes** | No record is hard-deleted. `is_active` flag preserves full audit trail for security compliance. |
| **Immutable AccessLog** | `can_create=False, can_edit=False, can_delete=False` in admin. The scan history is tamper-evident. |
| **TOTP Secret One-Time Delivery** | Secret is returned once at user creation. Never exposed again through any API endpoint. |
| **Session-Dynamic Rooms** | `Room_Allocations` join table with `academic_session` string decouples room records from user records. |

---

## 🗺️ Development Roadmap

- [x] **Phase 1** — Cloud Core: Flask + SQLAlchemy + PostgreSQL models + base API
- [x] **Phase 2** — Async Sync Engine: Redis + Celery + batch upload endpoint
- [x] **Phase 3** — Edge Database: SQLite schema + offline event queue + pull-sync API
- [ ] **Phase 4** — Flutter Client: UI + TOTP QR generation + optical scanning

---

## 📡 API Endpoints (Phase 1 + 2 + 3)

### Auth
| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/v1/auth/login` | Staff login → JWT token pair |
| POST | `/api/v1/auth/refresh` | Refresh access token |
| GET  | `/api/v1/auth/me` | Current user profile |

### Users & Rooms
| Method | Endpoint | Description |
|---|---|---|
| GET  | `/api/v1/users/` | List users (paginated, filterable) |
| POST | `/api/v1/users/` | Register new principal + generate TOTP |
| GET  | `/api/v1/users/<id>` | Get single user |
| PATCH| `/api/v1/users/<id>` | Update user fields |
| GET  | `/api/v1/rooms/` | List rooms |
| POST | `/api/v1/rooms/` | Create room |
| POST | `/api/v1/rooms/allocations` | Allocate user to room (per session) |
| POST | `/api/v1/logs/` | Record a scan event |
| GET  | `/api/v1/logs/` | Query audit trail |

### Phase 2: Async Sync (Tablet → Cloud)
| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/v1/sync/upload` | Upload offline batch → Celery queue (202 Accepted) |
| GET  | `/api/v1/sync/status/<task_id>` | Poll batch processing status |
| GET  | `/api/v1/sync/health` | Public broker reachability check |

### Phase 3: Pull Sync (Cloud → Tablet)
| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/sync/pull/bootstrap` | First-time full download for a new tablet |
| GET | `/api/v1/sync/pull/users` | Delta of changed student records since `?since=` |
| GET | `/api/v1/sync/pull/rooms` | Delta of changed room records since `?since=` |
| GET | `/api/v1/sync/pull/allocations` | Delta of changed room allocations since `?since=` |
