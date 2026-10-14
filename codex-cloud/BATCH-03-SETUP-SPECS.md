# Codex Cloud — Batch 03 Individual Environment Setup Specs

Date: 2026-10-02  
Mission: one repository per reusable Codex Cloud environment  
Rule: **one repository = one isolated environment**.

## 21 — kyqra-crawler

- Repository: `appolon1908/kyqra-crawler`
- Environment: `kyqra-crawler`
- Runtime: Node.js >=22.12.
- Setup:
  ```bash
  npm ci
  ```
- Verification:
  ```bash
  npm run lint
  npm run typecheck
  npm run test:unit
  npm run test:policy
  npm run build
  ```
- Integration tests use PostgreSQL/Redis testcontainers and should run only when container support is available.
- Do not point crawlers, queues, or delivery workers at production endpoints.
- Production effects: OFF.

## 22 — klyrow.com

- Repository: `appolon1908/klyrow.com`
- Environment: `klyrow.com`
- Stack: FastAPI/Klyrow SaaS backend plus Postal/Mautic integration and Docker deployment authority.
- Environment preparation should inspect repository test/dependency declarations under `apps/`, `tests/`, and deployment manifests before installing.
- Source-level validation should remain in safe mode and never run production deploy scripts.
- Do not run `scripts/generate-env` with production intent, `scripts/deploy`, real Postal/Mautic submission, DNS/PTR changes, or disable Klyrow safe mode.
- No carrier/provider/API secrets in the reusable environment.
- Production email sending: OFF.

## 23 — Moneybee-frontend-

- Repository: `appolon1908/Moneybee-frontend-`
- Environment: `Moneybee-frontend-`
- Runtime: Node.js + pnpm 10.15.0.
- Setup:
  ```bash
  corepack enable
  corepack prepare pnpm@10.15.0 --activate
  pnpm install --frozen-lockfile
  ```
- Verification:
  ```bash
  pnpm typecheck
  pnpm test
  pnpm contracts:check
  pnpm build
  ```
- External Playwright launch tests are optional and must use non-production endpoints.
- Production effects: OFF.

## 24 — Moneybee-Backend

- Repository: `appolon1908/Moneybee-Backend`
- Environment: `Moneybee-Backend`
- Runtime: Python >=3.13.
- Setup:
  ```bash
  python3.13 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e '.[dev]'
  ```
- Note: the project currently pins one SDK dependency to a GitHub source reference. Environment network access must permit GitHub package/source retrieval for that install.
- Verification:
  ```bash
  . .venv/bin/activate
  ruff check .
  pytest -q
  ```
- Do not enable live lending, billing, payments, email/SMS, or production database effects.
- Production effects: OFF.

## 25 — LARIM-A-Backend

- Repository: `appolon1908/LARIM-A-Backend`
- Environment: `LARIM-A-Backend`
- Runtime: Python 3.13.
- Preferred deterministic installer: `uv` using committed `uv.lock`.
- Setup:
  ```bash
  uv sync --dev --frozen
  ```
- Verification:
  ```bash
  uv run ruff check .
  uv run mypy src
  uv run pytest -q
  ```
- Database, Redis, Celery, Azure, and external-service integration tests must use isolated test resources only.
- Production effects: OFF.

## 26 — Telnexa-web

- Repository: `appolon1908/Telnexa-web`
- Environment: `Telnexa-web`
- Runtime: Node 24.19.x + npm 11.17.x.
- Setup:
  ```bash
  npm ci
  ```
- Verification:
  ```bash
  npm run check
  ```
- Local smoke tests may be run against the generated app only.
- Do not connect the UI to live SMS/billing/provider credentials.
- Production effects: OFF.

## 27 — klyrow-Website-

- Repository: `appolon1908/klyrow-Website-`
- Environment: `klyrow-Website-`
- Runtime: Node >=22 <25 + pnpm 10.x.
- Setup:
  ```bash
  corepack enable
  corepack prepare pnpm@10.32.1 --activate
  pnpm install --frozen-lockfile
  ```
- Verification:
  ```bash
  pnpm validate
  pnpm scan:secrets
  ```
- Playwright accessibility/E2E is optional when browser dependencies are installed.
- This repository remains the public website frontend only; do not turn it into a second email/provider backend.
- Production effects: OFF.

## 28 — Vicidialer-Codestra

- Repository: `appolon1908/Vicidialer-Codestra`
- Environment: `Vicidialer-Codestra`
- Runtime: Python/Bash plus repository-specific VICIdial/Asterisk validation tools.
- Initial setup should inspect `scripts/`, `tests/`, `ci/`, and `vicidial/` before installing extra packages.
- Run source/policy tests only with synthetic or isolated test configuration.
- Never connect to live AMI/ARI, VICIdial MariaDB, live dial routes, real campaigns, or production calling.
- New campaign/list/user resources remain disabled/non-dialing by default.
- Production dialing and call control: OFF.

## 29 — social.codestra.co

- Repository: `appolon1908/social.codestra.co`
- Environment: `social.codestra.co`
- Runtime: Node >=22.12 <23 + pnpm 10.34.0.
- Setup:
  ```bash
  corepack enable
  corepack prepare pnpm@10.34.0 --activate
  pnpm install --frozen-lockfile
  ```
- Verification starting point:
  ```bash
  pnpm validate:backend-policy
  pnpm contracts:routes:check
  pnpm validate:kong-social
  pnpm validate:social-contract-drift
  pnpm build
  pnpm test:security
  ```
- Do not run destructive Prisma reset/push, Stripe listener, production social posting, or live provider integrations.
- Production effects: OFF.

## 30 — SDK-repository

- Repository: `appolon1908/SDK-repository`
- Environment: `SDK-repository`
- Runtime: Node >=22 + pnpm 10.15.0; repository also contains Python SDK packages.
- Setup:
  ```bash
  corepack enable
  corepack prepare pnpm@10.15.0 --activate
  pnpm install --frozen-lockfile
  ```
- Verification:
  ```bash
  pnpm ci
  ```
- Python subpackages should be installed only when the assigned task touches them.
- Do not publish packages or mutate production registries from environment setup.
- Production effects: OFF.

## UI creation procedure

Create ten distinct Codex Cloud environments, one for each repository above. Attach exactly one repository, use the matching environment name, keep credentials empty by default, let Codex prepare and test, publish after source-level setup succeeds, then run a fresh smoke task and update the central manifest.
