# Codex Cloud — Batch 05 Individual Environment Setup Specs

Date: 2026-10-02  
Invariant: **one repository per environment**; production/provider effects disabled.

## 41 — Codestra-cAdvisor
- Repository/environment: `appolon1908/Codestra-cAdvisor` / `Codestra-cAdvisor`
- Source/config wrapper with pinned upstream and hostname authority.
- Minimal source-validation environment; preserve `CODESTRA_UPSTREAM_LOCK.json`.
- Do not start host-monitoring agents against production or expose metrics publicly.

## 42 — Codestra-Redis-Exporter
- Repository/environment: `appolon1908/Codestra-Redis-Exporter` / `Codestra-Redis-Exporter`
- Preserve upstream lock and repository authority.
- No production Redis credentials or targets in the reusable environment.
- Validate only repository source/config contracts.

## 43 — Codestra-Blackbox-Exporter
- Repository/environment: `appolon1908/Codestra-Blackbox-Exporter` / `Codestra-Blackbox-Exporter`
- Preserve upstream lock and hostname authority.
- Do not probe production/private endpoints from environment setup.
- Use synthetic/local targets for task-specific probe tests.

## 44 — Codestra-Alloy
- Repository/environment: `appolon1908/Codestra-Alloy` / `Codestra-Alloy`
- Preserve upstream lock.
- No production telemetry endpoints, credentials, or external export during setup.
- Run static/config checks only unless an isolated collector runtime is explicitly required.

## 45 — Codestra-Postgres-Exporter
- Repository/environment: `appolon1908/Codestra-Postgres-Exporter` / `Codestra-Postgres-Exporter`
- Preserve upstream lock and repository configuration authority.
- Never inject production PostgreSQL credentials.
- Use disposable/local PostgreSQL only for connector tests.

## 46 — Codestra-Marketing-
- Repository/environment: `appolon1908/Codestra-Marketing-` / `Codestra-Marketing-`
- Runtime: Python >=3.12.
- Setup:
  ```bash
  python3.12 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e '.[test]'
  ```
- Verification: `pytest -q`.
- Tests marked `postgres` require a disposable test database.
- Do not activate real ad/provider spend, callbacks, or production credentials.

## 47 — Codestra-Communication-CC
- Repository/environment: `appolon1908/Codestra-Communication-CC` / `Codestra-Communication-CC`
- Runtime: Python >=3.12.
- Setup:
  ```bash
  python3.12 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e '.[test]'
  ```
- Verification: `pytest -q`.
- PostgreSQL-marked tests use disposable test infrastructure only.
- Do not send live email/SMS/calls/WhatsApp.

## 48 — Codesrea-Social-
- Repository/environment: `appolon1908/Codesrea-Social-` / `Codesrea-Social-`
- Runtime: Python >=3.12.
- Setup:
  ```bash
  python3.12 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e '.[test]'
  ```
- Verification: `pytest -q`.
- No production social-provider posting or credentials.

## 49 — Codestra-AI
- Repository/environment: `appolon1908/Codestra-AI` / `Codestra-AI`
- Runtime: Python >=3.12.
- Setup:
  ```bash
  python3.12 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e '.[test]'
  ```
- Verification: `pytest -q`.
- Use mocks/fakes for external AI providers by default. No production provider keys.
- PostgreSQL/Redis integration tests must use disposable resources.

## 50 — codestra-foundation
- Repository/environment: `appolon1908/codestra-foundation` / `codestra-foundation`
- Runtime: Python >=3.10; prefer Python 3.12 for Codestra consistency unless repository tests require otherwise.
- Setup:
  ```bash
  python3.12 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e '.[dev]'
  ```
- Verification:
  ```bash
  pytest
  ruff check .
  ```
- Database migrations/tests must target disposable non-production PostgreSQL only.

## UI creation
Create ten distinct Codex Cloud environments, one repository each, with no production secrets. Publish and smoke-test individually, then record results in the central manifest.
