# Codex Cloud — Batch 06 Individual Environment Setup Specs

Date: 2026-10-02  
Invariant: **one repository per environment**. No production/server mutation during environment preparation.

## 51 — Database-migrations-
- Repository/environment: `appolon1908/Database-migrations-` / `Database-migrations-`
- Type: canonical migration packages and provenance/certification tooling.
- Setup: Python 3 + Bash as required by `scripts/`.
- Verification: compile/validate migration source and lineage using repository-owned scripts.
- Never apply migrations to production from Codex Cloud. CI certification is source-candidate evidence only.

## 52 — codestra-server-c
- Repository/environment: `appolon1908/codestra-server-c` / `codestra-server-c`
- Stack: Django backend under `backend/` plus Node/Vite frontend under `frontend/`.
- Backend setup:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r backend/requirements.txt
  ```
- Frontend setup:
  ```bash
  cd frontend
  npm ci
  ```
- Verification: source-level backend checks/tests and frontend lint/test/build using repository commands.
- Database migrations, external providers, production deploys, and destructive effects remain OFF.

## 53 — codestra-ruleset-toolkit
- Repository/environment: `appolon1908/codestra-ruleset-toolkit` / `codestra-ruleset-toolkit`
- Runtime: Python 3 + Bash.
- Verification starting point:
  ```bash
  python -m unittest -v test_rulesetlib.py
  bash test-gate-logic.sh
  ```
- Ruleset mutations, branch-protection changes, and GitHub policy rollout must not run during environment setup.
- Use read-only/dry-run tooling by default.

## 54 — Sentry
- Repository/environment: `appolon1908/Sentry` / `Sentry`
- Type: governed deployment wrapper for self-hosted Sentry.
- Setup: source/scripts/docs inspection only.
- Do **not** run `sudo ./scripts/deploy.sh`, connect to the named production host, or load administrator credentials.
- No persistent Sentry volumes or production ingestion in the cloud environment.

## 55 — Wazuh
- Repository/environment: `appolon1908/Wazuh` / `Wazuh`
- Type: governed Wazuh deployment wrapper.
- Setup: source/scripts/docs validation only.
- Do **not** run deployment, agent enrollment, or connect to production manager/indexer/API addresses during setup.
- No manager credentials or security telemetry from production.

## 56 — Agent-Desktop-WebRTC
- Repository/environment: `appolon1908/Agent-Desktop-WebRTC` / `Agent-Desktop-WebRTC`
- Current repository root contains only governance/agent metadata.
- Environment requires no dependency install until source is added or the task explicitly introduces it.
- Preserve the repository as an independent future WebRTC application environment.
- No live calling.

## 57 — OpenAPI
- Repository/environment: `appolon1908/OpenAPI` / `OpenAPI`
- Current repository root contains governance/agent metadata only.
- No dependency install required yet.
- Keep this environment isolated for shared OpenAPI/schema work as source is added.
- No production gateway publication.

## 58 — codestra-platform
- Repository/environment: `appolon1908/codestra-platform` / `codestra-platform`
- Runtime: Node.js.
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
- Do not inject production API tokens/endpoints into the reusable environment.

## 59 — kyyow-contracts
- Repository/environment: `appolon1908/kyyow-contracts` / `kyyow-contracts`
- Runtime: Node.js; repository also contains .NET/C# contract source.
- Setup:
  ```bash
  npm ci
  ```
- Verification:
  ```bash
  npm run check
  npm run validate:component
  npm run format-check
  ```
- Install .NET SDK only when a task requires the C# compatibility surface.
- Do not publish packages during setup.

## 60 — Codestra-Device-Forge
- Repository/environment: `appolon1908/Codestra-Device-Forge` / `Codestra-Device-Forge`
- Type: mobile-device control plane and research-lab architecture.
- Setup: documentation/compose validation initially; install ADB/Fastboot/libimobiledevice/Heimdall/emulator tooling only for tasks that explicitly need those capabilities.
- Real-device operations must not bypass FRP, Activation Lock, MDM ownership, carrier/financing locks, or stolen-device protections.
- Bypass research is limited to synthetic/emulated Codestra-owned lab targets.
- No real-device flashing during environment creation.

## UI creation
Create ten separate Codex Cloud environments, attach exactly one matching repository to each, prepare source-level tooling, keep secrets empty, publish individually, smoke-test, and update the central manifest.
