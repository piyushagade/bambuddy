# Bambuddy — Repository Context & Architecture Guide

> **Self-hosted command center for Bambu Lab 3D printers — from a single printer to an entire print farm.**
> Version source of truth: `APP_VERSION` in `backend/app/core/config.py`.

---

## 1. Project Overview

**Bambuddy** replaces or augments Bambu Cloud by communicating directly with Bambu Lab printers over the local network (or Tailscale/VPN) using Bambu Lab's **Developer Mode** (MQTT over TLS on port `8883`, implicit FTPS on port `990`, and camera streaming on ports `6000` / `322`).

Core capabilities include:
- **Real-time Printer Monitoring & Control** (MQTT telemetry, AMS management, HMS error actions, fan/heater control, camera streaming fan-out, Cam Wall, OBS overlays)
- **3MF Print Archiving & Print Log** (automatic `.gcode.3mf` capture, metadata & thumbnail extraction, per-run history, cost & energy tracking, G-code & 3D preview)
- **File Manager (Library) & Integrated Slicing** (3MF/STL/STEP/PDF management, external host folder mounts, MakerWorld direct import, server-side slicing via `slicer-api`, reusable Slicer Pipelines)
- **Unified Print Queue & Farm Scheduling** (immediate & scheduled jobs, Shortest-Job-First, model/class-based load balancing, batch orders, preheat/heat-soak orchestration, AMS auto-drying)
- **Spool Inventory, Spoolman Sync & SpoolBuddy Kiosk** (spool tracking, K-profile management, printable QR/Avery labels, and Raspberry Pi NFC/scale hardware integration)
- **Virtual Printer & Remote Proxy Mode** (emulates a Bambu printer via SSDP + TLS MQTT + FTPS for direct slicer uploads or transparent remote TLS relaying)
- **Multi-User Auth, RBAC & Billing** (local JWT, TOTP MFA, LDAP, OIDC, OAuth2+PKCE Connected Apps, per-group printer/location access control, job review workflows, Cost Centers & Finance ledger)

---

## 2. Repository Structure

```text
bambuddy/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI application, lifespan, MQTT callbacks, static mount
│   │   ├── cli.py               # CLI utilities (admin password reset, etc.)
│   │   ├── api/routes/          # ~63 FastAPI route modules mounted under /api/v1
│   │   ├── core/                # Config, DB engine/migrations, Auth, RBAC permissions, WebSocket, crypto
│   │   ├── data/                # Bundled static catalogs (HMS errors, filaments, printer presets)
│   │   ├── i18n/                # Backend translations (notifications, localized strings)
│   │   ├── models/              # ~61 SQLAlchemy 2.0 declarative ORM models
│   │   ├── schemas/             # Pydantic v2 request/response schemas
│   │   ├── services/            # Business logic, MQTT/FTPS clients, scheduler, virtual printer, integrations
│   │   └── utils/               # Pure helpers (3MF parsing, paths, filament/nozzle/FTS routing, time)
│   └── tests/                   # Pytest unit & integration test suites
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # React Router v7 routes, context providers, auth/permission guards
│   │   ├── api/client.ts        # Typed HTTP client (/api/v1), token handling (auth, stream, media)
│   │   ├── components/          # Reusable UI components, modals, cards, SpoolBuddy kiosk components
│   │   ├── contexts/            # AuthContext, ThemeContext, ToastContext, ColorCatalogContext, etc.
│   │   ├── hooks/               # Custom hooks (useWebSocket, usePrintProgressTitle, camera tokens)
│   │   ├── i18n/locales/        # 15 locale files (en.ts is the reference schema)
│   │   ├── pages/               # Top-level route views (Printers, Archives, Queue, Files, Finance, etc.)
│   │   ├── types/               # Shared TypeScript interfaces
│   │   ├── utils/               # Frontend formatting, sorting, filament/printer helpers
│   │   ├── workers/             # Web Workers (e.g., STEP 3D model preview)
│   │   └── __tests__/           # Vitest + React Testing Library + MSW tests
├── slicer-api/                  # Optional Docker Compose sidecar for OrcaSlicer (:3003) & BambuStudio (:3001)
├── spoolbuddy/                  # Raspberry Pi daemon (PN5180 SPI NFC + NAU7802 I2C scale) & setup scripts
├── deploy/                      # Docker entrypoint & deployment helpers
├── install/ & installers/       # Native Linux & Windows installation scripts/bundlers
└── tests/                       # Top-level test helpers
```

---

## 3. Technology Stack

### Backend
- **Runtime**: Python 3.10+ (CI/Docker targets Python 3.13)
- **Web Framework**: FastAPI + Uvicorn (`--loop asyncio` is required; avoid `uvloop` due to a TLS bug that can truncate Virtual Printer FTPS uploads)
- **Database**: SQLAlchemy 2.0 Async (`AsyncSession`) supporting:
  - **SQLite** (`sqlite+aiosqlite`, default `bambuddy.db` with `PRAGMA journal_mode = WAL` and `busy_timeout = 15000`)
  - **PostgreSQL** (`postgresql+asyncpg` via `DATABASE_URL`, session pinned to `timezone=UTC`)
- **Printer Protocols**: `paho-mqtt` (TLS port 8883), `aioftp` (FTPS client), `pyftpdlib` + `cryptography` + `pyOpenSSL` (Virtual Printer FTPS server)
- **Media & 3D Processing**: `trimesh`, `matplotlib`, `fast-simplification`, `networkx`, `lxml` (STL/3MF thumbnails), `pypdfium2` (PDF thumbnails), `reportlab` + `qrcode` (spool labels), `opencv-python-headless` (build-plate empty detection), `ffmpeg` (timelapse processing)
- **Auth & Security**: `PyJWT`, `passlib[bcrypt]`, `pyotp` (TOTP), `ldap3`, `httpx` (OIDC), `defusedxml` (safe 3MF XML parsing), `asyncssh` (SpoolBuddy remote updates)

### Frontend
- **Framework**: React 19 + TypeScript 5.9 + Vite 8
- **State & Fetching**: TanStack React Query v5 (`@tanstack/react-query`) + WebSocket live invalidation/updates (`useWebSocket`)
- **Styling & UI**: TailwindCSS v4, `lucide-react` icons, `@dnd-kit` drag-and-drop, `@floating-ui/dom`, `@tiptap` rich text editor, `recharts`
- **3D & File Rendering**: `three` (Three.js), `occt-import-js` (STEP files), `pdfjs-dist`, `jszip`, `xlsx`, `papaparse`
- **Internationalization**: `i18next` + `react-i18next`

---

## 4. Key Architectural Patterns & Conventions

### 4.1 Database & Schema Migrations (`backend/app/core/database.py`)
- **No Alembic**: Schema creation and migrations run automatically at startup inside `init_db()` in `backend/app/core/database.py`.
- `Base.metadata.create_all` creates new tables, while idempotent migration functions (`_safe_execute`, `_migrate_*`) alter existing tables and backfill data across both SQLite and PostgreSQL dialects (`is_sqlite()` in `backend/app/core/db_dialect.py`).
- **Timestamps**: All `DateTime` columns store **naive UTC** timestamps (`utcnow_naive()` in `backend/app/utils/local_time.py`). Never pass timezone-aware datetimes into DB parameters (a SQLAlchemy `before_cursor_execute` hook strips `tzinfo` as a safeguard, and PostgreSQL connections force `timezone=UTC`).

### 4.2 Authentication, Tokens & Permissions (`backend/app/core/auth.py`, `permissions.py`)
- **Opt-in Auth**: When authentication is disabled in Settings, all routes remain open.
- **Route Protection**: Always protect API routes using `RequirePermissionIfAuthEnabled(Permission.<RESOURCE_ACTION>)` (from `backend/app/core/auth.py` and `backend/app/core/permissions.py`).
- **Printer Access Scoping**: Groups can restrict users to specific printers or locations. Routes returning printer-scoped data (printers, archives, queue, camera, stats, maintenance) must filter by the user's accessible printer IDs.
- **Three Frontend Token Types** (`frontend/src/api/client.ts`):
  1. `authToken`: Standard Bearer JWT for API requests (stored in `sessionStorage` or `localStorage`).
  2. `streamToken`: Short-lived query-param token (`?token=...`) exclusively for live camera streams (`camera:view` permission).
  3. `mediaToken`: Short-lived query-param token carrying user identity for `<img>`/`<video>` tags loading thumbnails, timelapses, and cover photos.

### 4.3 Printer State & Unified Print Dispatch
- **`printer_manager` (`backend/app/services/printer_manager.py`)**: Central singleton managing `BambuMQTTClient` instances per printer. State changes trigger callbacks registered in `backend/app/main.py` (archiving on print start/finish, notifications, HMS alerts, energy tracking, WebSocket broadcasts).
- **Unified Queue Dispatch (`backend/app/services/print_scheduler.py`)**: Every print started from Bambuddy (File Manager, Archive reprint, Virtual Printer queue mode, Slicer Pipeline, or scheduled queue item) flows through `PrintQueueItem` and `print_scheduler`. Immediate prints require both `printers:control` and `queue:create` permissions.

### 4.4 Internationalization (i18n)
- **Never hardcode user-visible strings in the frontend.**
- Add new translation keys to `frontend/src/i18n/locales/en.ts` **and every other locale file** in `frontend/src/i18n/locales/*.ts` with identical nesting and real translations (not English copies).
- Verify parity with `cd frontend && npm run check:i18n` (also run automatically by `npm run test:run`).
- Backend error responses that surface in the UI should return structured detail `{ "code": "stable_snake_case_code", "message": "English fallback" }` so `ApiError` in `frontend/src/api/client.ts` can map `code` to an i18n key.

### 4.5 Security & Safe Path Handling
- **File Paths**: Always validate user-supplied or archive-extracted paths using `backend/app/utils/safe_path.py`, `archive_paths.py`, or `library_paths.py` to prevent path traversal. External library folders must reside within `BAMBUDDY_EXTERNAL_ROOTS`.
- **XML Parsing**: Always use `defusedxml` when parsing `.3mf` / XML metadata.
- **Secrets at Rest**: Sensitive settings (cloud tokens, MQTT/plug passwords, OIDC secrets, MFA keys) are encrypted using `backend/app/core/encryption.py`.

---

## 5. Development & Testing Commands

### Backend
```bash
# Run dev server (always use --loop asyncio)
DEBUG=true uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000 --loop asyncio

# Lint & format (Ruff)
ruff check backend/
ruff format --check backend/

# Run tests (conftest.py automatically isolates DATABASE_URL to a temp SQLite DB)
pytest backend/tests/ -v
pytest backend/tests/unit/
```

### Frontend
```bash
cd frontend
npm run dev          # Vite dev server on http://localhost:5173 (proxies /api and /ws to :8000)
npm run typecheck    # tsc -b
npm run lint         # ESLint
npm run test:run     # Vitest single run + check:i18n parity check
npm run build        # Production build (outputs to ../static) + check:baseline
```

### Root Test Scripts
```bash
./test_backend.sh    # Ruff check/format + pytest
./test_frontend.sh   # tsc + ESLint + Vitest + i18n parity
./test_docker.sh     # Docker build + container unit & integration tests
./test_security.sh   # Bandit, pip-audit, npm-audit (--full for CodeQL/Trivy)
./test_all.sh        # Runs frontend, backend, and docker suites sequentially
```

---

## 6. Contribution & Git Workflow Notes
- **Git Remotes**:
  - `origin`: `https://github.com/piyushagade/bambuddy.git` (Primary fork repo; deployed branch is `main`)
  - `upstream`: `https://github.com/maziggy/bambuddy.git`
- **Adding a Permission**:
  1. Add enum value to `Permission` in `backend/app/core/permissions.py`.
  2. Register in `PERMISSION_CATEGORIES` and default group sets (`DEFAULT_GROUPS`).
  3. Enforce on routes with `RequirePermissionIfAuthEnabled`.
  4. Mirror in frontend permission checks (`useAuth().hasPermission(...)`).
- **Adding a Database Column/Table**:
  1. Define or update the model in `backend/app/models/` and import it in `backend/app/models/__init__.py`.
  2. Add an idempotent migration step in `backend/app/core/database.py` (`init_db()`) that works on both SQLite and PostgreSQL.

---

## 7. Fork Deployment & Remote Update Workflow

### 7.1 Local Build & Push (`c:\AI\apps\BamBuddy`)
Before pushing changes to `origin/main`, run `npm run build` in `frontend/` so the compiled `static/` assets are committed alongside source changes (ensuring servers without Node/npm still serve the updated frontend):

```powershell
cd frontend
npm run build
cd ..
git add backend/ frontend/ static/
git commit -m "feat: description of changes"
git push origin main
```

### 7.2 Remote Server Deployment (`abe` at `/opt/bambuddy`)
The remote server runs Bambuddy natively via `systemd` (`bambuddy.service`) out of `/opt/bambuddy`.

```bash
cd /opt/bambuddy

# One-time setup (allows sudo/root git commands on /opt/bambuddy and sets fork remote)
sudo git config --global --add safe.directory /opt/bambuddy
sudo git remote set-url origin https://github.com/piyushagade/bambuddy.git

# Pull latest fork changes, backup DB, update Python/frontend deps, and restart service
sudo /opt/bambuddy/install/update.sh
```

> **Ownership Note:** Because `/opt/bambuddy` is owned by the `bambuddy` service user while `update.sh` runs under `sudo`, if `systemctl status bambuddy` ever reports permission errors after an update, run:
> ```bash
> sudo chown -R bambuddy:bambuddy /opt/bambuddy
> sudo systemctl restart bambuddy
> ```

### 7.3 Integrating Upstream Updates (`maziggy/bambuddy`)

#### Option A: Full Upstream Merge
```powershell
git fetch upstream
git log HEAD..upstream/main --oneline
git merge upstream/main
```
**Conflict Resolution Rules:**
1. **`static/` (Compiled Frontend Assets)**: Both upstream and this fork commit pre-built assets in `static/`. Do **not** resolve `static/` conflicts manually—resolve source conflicts in `frontend/src/` and `backend/`, then rebuild:
   ```powershell
   cd frontend
   npm install
   npm run build
   cd ..
   git add static/
   ```
2. **`backend/app/core/database.py` (`init_db()`)**: Migrations are idempotent `_migrate_*()` functions (no Alembic revision chain). Keep both upstream's and the fork's `_migrate_*()` calls.
3. **Shared UI / Route / Locale Files (`SettingsPage.tsx`, `Layout.tsx`, `en.ts`)**: Preserve both upstream additions and fork features (cohort onboarding, user expiration, filament deficit), then commit and push to `origin/main`.

#### Option B: Selective Feature Cherry-Pick
To pull only a targeted upstream change (e.g., new printer model support):
```powershell
git fetch upstream
git log upstream/main --oneline --grep="printer" -n 20
git cherry-pick <commit-hash>

cd frontend && npm run build && cd ..
git add static/
git commit --amend --no-edit
git push origin main
```
