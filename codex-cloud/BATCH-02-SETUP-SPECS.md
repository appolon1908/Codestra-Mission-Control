# Codex Cloud — Batch 02 Individual Environment Setup Specs

Date: 2026-10-02  
Mission: one repository per reusable Codex Cloud environment  
Scope: 10 additional repositories  
Rule: **each environment contains exactly one repository**.

## 11 — codestra

- Repository: `appolon1908/codestra`
- Environment: `codestra`
- Runtime: Node.js >=22.22.0.
- Setup:
  ```bash
  npm ci
  ```
- Verification:
  ```bash
  npm run lint
  npm test
  npm run build
  ```
- Optional source-only security gate:
  ```bash
  npm run audit:production
  ```
- Do not copy production `.env` values into the cloud environment.
- Production effects: OFF.

## 12 — beyvra-backend

- Repository: `appolon1908/beyvra-backend`
- Environment: `beyvra-backend`
- Stack: Django/DRF + PostgreSQL with Docker Compose development support.
- Initial environment preparation should inspect `docker-compose.yaml`, `docker-compose-dev.yaml`, and the web image dependency files before installing packages.
- Prefer source-level checks without real provider credentials. When Docker is available, use the development compose topology only.
- Do not create a real superuser, connect live Stripe, activate real wallet/payment effects, or point at production PostgreSQL.
- Do not copy a live `.env`; use only non-secret test placeholders required to load settings.
- Production effects: OFF.

## 13 — codestra-backend

- Repository: `appolon1908/codestra-backend`
- Environment: `codestra-backend`
- Stack: Django 5.1 + Channels/Celery + PostgreSQL/Redis dependencies.
- Setup:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  ```
- Source-level starting gates:
  ```bash
  . .venv/bin/activate
  python -m compileall -q .
  python manage.py check
  ```
- Full repository tests may require its Compose services and non-production environment values.
- Never run database backup/restore, migrations, or superuser creation against a live database.
- Production effects: OFF.

## 14 — backend2

- Repository: `appolon1908/backend2`
- Environment: `backend2`
- Stack: Django 5.2 + Channels/Celery + PostgreSQL/Redis.
- Setup:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  ```
- Source-level starting gates:
  ```bash
  . .venv/bin/activate
  python -m compileall -q .
  python manage.py check
  ```
- Full tests can use the repository's isolated Compose topology when needed.
- Never run `migrate`, `dbbackup`, `dbrestore`, `createsuperuser`, or data population against production.
- Production effects: OFF.

## 15 — beyvra-frontend

- Repository: `appolon1908/beyvra-frontend`
- Environment: `beyvra-frontend`
- Working application: `client-portal/`.
- Runtime: Node.js.
- Setup:
  ```bash
  cd client-portal
  npm ci
  ```
- Verification:
  ```bash
  cd client-portal
  npm run test
  npm run test:contract
  npm run test:errors
  npm run test:realtime
  npm run lint
  ```
- Install Playwright browsers only for tasks that require `npm run test:e2e`.
- Do not inject production API/payment credentials.
- Production effects: OFF.

## 16 — scrapper

- Repository: `appolon1908/scrapper`
- Environment: `scrapper`
- Runtime: Node.js >=22.
- Setup:
  ```bash
  npm ci
  ```
- Verification:
  ```bash
  npm run check
  ```
- Install Playwright/Chromium runtime dependencies only when browser crawling/integration tests need them.
- Unit/source validation must not crawl arbitrary live targets or deliver data to production CRM/provider endpoints.
- Production effects: OFF.

## 17 — Breero.com

- Repository: `appolon1908/Breero.com`
- Environment: `Breero.com`
- Stack: pnpm/Turborepo frontend + FastAPI/SQLAlchemy backend.
- Root setup:
  ```bash
  corepack enable
  corepack prepare pnpm@10.0.0 --activate
  pnpm install --frozen-lockfile
  ```
- Backend setup:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  cd apps/api
  python -m pip install --upgrade pip
  python -m pip install -e '.[dev]'
  ```
- Source-level verification:
  ```bash
  pnpm lint
  pnpm typecheck
  pnpm test
  pnpm contract:check
  ```
- Backend gates should follow `AGENTS.md`; database/concurrency/migration gates must use isolated non-production PostgreSQL.
- Payments, payouts, automatic booking/assignment/confirmation, provider dispatch, email/SMS, callbacks, Odoo writes, and external automations remain disabled.
- Production effects: OFF.

## 18 — booked4seasons

- Repository: `appolon1908/booked4seasons`
- Environment: `booked4seasons`
- Runtime: Node.js with npm 11.5.2 package metadata.
- Setup:
  ```bash
  npm ci
  ```
- Verification:
  ```bash
  npm run lint
  npm run typecheck
  npm run build
  npm run test:compliance
  ```
- Run visual/Playwright QA only when browser support is required by the task.
- Production effects: OFF.

## 19 — kyqra

- Repository: `appolon1908/kyqra`
- Environment: `kyqra`
- Runtime: Node.js.
- Repository currently has no root npm lockfile, so environment preparation must not silently commit a generated lockfile.
- Suggested ephemeral setup:
  ```bash
  npm install --no-package-lock
  ```
- Verification:
  ```bash
  npm run check
  npm test
  ```
- Crawlee/Playwright browser dependencies may be installed when a task needs live browser execution.
- Do not connect crawlers/workers to production Redis, provider targets, or delivery destinations.
- Production effects: OFF.

## 20 — telnexa

- Repository: `appolon1908/telnexa`
- Environment: `telnexa`
- Stack: Python billing/control-plane code plus Docker/Jasmin deployment authority.
- Runtime baseline: Python 3.11 for Ruff policy; Bash and Docker/Compose for deployment validation.
- Initial verification should be source/test oriented:
  ```bash
  python -m pytest -q
  ```
- If the test dependency set is not yet represented by a root requirements/project dependency declaration, let Codex inspect imports and install only non-production development dependencies. Do not invent or persist production secrets.
- `docker compose config` may be used with safe placeholder values for syntax/config validation.
- Do not run `scripts/start.sh`, TLS initialization, live SMS routes, carrier credentials, real billing charges, or production provider dispatch.
- Production SMS remains disabled.
- Production effects: OFF.

## UI creation procedure

For all ten entries above:

1. Create a new Codex Cloud environment.
2. Attach only the matching repository.
3. Use the environment name above.
4. Let Codex prepare and test the environment.
5. Keep privacy personal unless the user explicitly requests workspace sharing.
6. Keep secrets empty by default.
7. Publish only after setup verification succeeds.
8. Start one fresh smoke task in the published environment.
9. Record status in `ENVIRONMENT-MANIFEST.md`.

No environment may contain another repository.
