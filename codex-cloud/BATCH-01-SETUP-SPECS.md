# Codex Cloud — Batch 01 Individual Environment Setup Specs

Date: 2026-10-02  
Mission: one repository per reusable Codex Cloud environment  
Scope: first 10 repositories only  
Safety: no production effects, no direct pushes to `main`, no force-pushes, no secret import unless explicitly required.

These are **ten separate environments**. Do not attach more than one repository to any environment.

## 01 — Middleware-

- Repository: `appolon1908/Middleware-`
- Environment name: `Middleware-`
- Default branch: `main`
- Runtime: Python 3.12 required by the repository Makefile. Project metadata allows >=3.12,<3.15, but repository verification is pinned to Python 3.12.
- Suggested setup:
  ```bash
  python3.12 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e '.[test]'
  ```
- Verification:
  ```bash
  . .venv/bin/activate
  make verify
  ```
- Do not run `make test-integration` until an isolated PostgreSQL/Redis test environment is explicitly provisioned.
- Do not run any deployment path. `deploy-internal` is intentionally disabled.
- Secrets: none for source-level verification.
- Production effects: OFF.

## 02 — Kong

- Repository: `appolon1908/Kong`
- Environment name: `Kong`
- Default branch: `main`
- Runtime: Python 3.x for repository tests/validators; Kong runtime itself is not required for every source-level task.
- Initial setup: let Codex inspect tests/scripts first. Install only the Python test dependencies actually imported by the repository.
- Verification starting point:
  ```bash
  python -m pytest -q
  ```
- Read `AGENTS.md` before changes.
- Preserve the Caddy -> Kong -> Middleware architecture boundary.
- Do not mutate the Kong Admin API, database cutover, DNS, or live traffic.
- Production effects: OFF.

## 03 — Caddy

- Repository: `appolon1908/Caddy`
- Environment name: `Caddy`
- Default branch: `main`
- Runtime: Python 3 for repository validators; Caddy CLI may be installed when a task requires native Caddy validation.
- Source-level verification starting point:
  ```bash
  python3 scripts/validate_repository.py
  ```
- When Caddy CLI is available, validate generated/runtime config without applying or reloading production.
- Preserve the canonical edge chain: client -> Caddy -> Kong -> Middleware.
- Never expose private `/metrics`, `/internal`, Kong Admin API, Middleware private ports, Redis, or PostgreSQL.
- Production effects: OFF.

## 04 — Keycloak

- Repository: `appolon1908/Keycloak`
- Environment name: `Keycloak`
- Default branch: `main`
- Runtime: Bash + Python 3 + Docker/Compose for full local runtime checks.
- Source-level verification:
  ```bash
  make validate
  make test-runtime-preflight
  ```
- Container build is optional for tasks that require it:
  ```bash
  make build
  ```
- Do not run `make up`, `apply-plan`, backup/restore, reconciliation apply, or any live mutation without a separately approved task and required non-secret test credentials.
- Compose requires runtime values such as database/admin credentials; do not import production values into Codex Cloud.
- Production effects: OFF.

## 05 — Odoo

- Repository: `appolon1908/Odoo`
- Environment name: `Odoo`
- Default branch: `main`
- Runtime: Python 3 + Node.js. Docker is useful for hosted runtime parity but is not required for the repository's initial source-level CI.
- Verification:
  ```bash
  bash scripts/run_ci.sh
  ```
- The CI script checks shell syntax, Python compilation, frontend Node tests, custom addon manifests/assets, integration boundaries, API contracts, security policy, release policy, and source-level mission tests.
- Never store PostgreSQL dumps, filestore content, live `.env`, credentials, keys, certificates, or production `odoo.conf` in the environment definition.
- Do not upgrade production modules or touch a live Odoo database.
- Production effects: OFF.

## 06 — websocket

- Repository: `appolon1908/websocket`
- Environment name: `websocket`
- Default branch: `main`
- Runtime: Node.js.
- Deterministic setup:
  ```bash
  npm ci
  ```
- Verification:
  ```bash
  npm test
  ```
- Local start command for task-specific smoke testing:
  ```bash
  npm start
  ```
- Do not expose `/internal` or `/metrics`.
- Production effects: OFF.

## 07 — N8N

- Repository: `appolon1908/N8N`
- Environment name: `N8N`
- Default branch: `main`
- Runtime: Python 3 + Bash; Docker Compose required for the repository's compose-config validation.
- Verification:
  ```bash
  make validate
  ```
- Repository policy keeps workflow activation and external delivery disabled.
- n8n may call Middleware only; it must not directly bypass Middleware into provider/admin systems.
- Do not activate workflows or perform a live server deployment from Codex Cloud.
- Production effects: OFF.

## 08 — codestra-provisioning-service

- Repository: `appolon1908/codestra-provisioning-service`
- Environment name: `codestra-provisioning-service`
- Default branch: `main`
- Runtime: Python 3.12.
- Suggested setup:
  ```bash
  python3.12 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e .
  python -m pip install -r requirements-dev.txt
  ```
- Verification:
  ```bash
  . .venv/bin/activate
  python -m pytest
  ```
- The service is staging-only and private by design. Mutating routes require Middleware attestation in addition to JWT authorization.
- Do not enable provider adapters or issue live credentials.
- Production effects: OFF.

## 09 — Codestra-Mission-Control

- Repository: `appolon1908/Codestra-Mission-Control`
- Environment name: `Codestra-Mission-Control`
- Default branch: `main`
- Runtime: Python >=3.12.
- Suggested setup:
  ```bash
  python3.12 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e '.[dev,temporal]'
  ```
- Verification:
  ```bash
  . .venv/bin/activate
  pytest
  ruff check .
  ```
- Environment is for implementation/control-plane development only. It does not self-certify staging or production.
- Production effects: OFF.

## 10 — Codestra-OpenBao

- Repository: `appolon1908/Codestra-OpenBao`
- Environment name: `Codestra-OpenBao`
- Default branch in GitHub: `main`
- Repository governance additionally declares the canonical active development lane `governance/single-active-lane-20260926`; tasks must inspect current authority before editing.
- Runtime: Bash + Python 3. OpenBao binary should be installed only when a task requires native config/runtime validation.
- Mandatory repository preflight for implementation work:
  ```bash
  ./scripts/agent_preflight.sh
  ```
- Certification path, only when the task explicitly reaches that stage:
  ```bash
  ./scripts/agent_preflight.sh --certify
  ```
- Never expose bootstrap/recovery material, root tokens, private keys, provider credentials, native storage/backend/admin ports, or alternate public hostnames.
- Production effects: OFF.

## Codex Cloud UI creation rule

For each entry above:

1. Create a **new** Codex Cloud environment.
2. Attach **exactly one** repository.
3. Use the environment name listed above.
4. Run Codex environment preparation.
5. Review its proposed install/setup commands against this file and the repository's own `AGENTS.md`/governance files.
6. Keep credentials empty unless a source-level task genuinely requires a non-production test value.
7. Keep production/provider effects disabled.
8. Publish the environment.
9. Start one fresh smoke task using only that environment.
10. Record the result in `ENVIRONMENT-MANIFEST.md`.

No environment in this batch may contain a second repository.
