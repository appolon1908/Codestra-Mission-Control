# Codex Cloud — Batch 04 Individual Environment Setup Specs

Date: 2026-10-02  
Scope: 10 repositories  
Invariant: **one repository per environment**. Production/server effects remain disabled.

## 31 — Infustruction-repo
- Repository/environment: `appolon1908/Infustruction-repo` / `Infustruction-repo`
- Type: platform infrastructure/governance/certification authority.
- Setup: no runtime secrets. Let Codex inspect `scripts/`, `tests/`, `tools/`, YAML contracts, and governance files.
- Verification: run repository-owned source validators/tests only.
- Never treat historical certification/evidence files as permission to mutate staging or production.
- Production effects: OFF.

## 32 — communication-platform-
- Repository/environment: `appolon1908/communication-platform-` / `communication-platform-`
- Type: contracts/docs/scripts/tests.
- Setup: minimal Python/Bash toolchain as required by repository scripts.
- Verification: run repository-local tests and contract validators discovered under `scripts/` and `tests/`.
- Do not send email, SMS, WhatsApp, or calling traffic.
- Production effects: OFF.

## 33 — Codestra-Grafana-
- Repository/environment: `appolon1908/Codestra-Grafana-` / `Codestra-Grafana-`
- Type: Grafana source/integration wrapper with pinned upstream state.
- Setup: preserve `CODESTRA_UPSTREAM*.json`; do not silently advance upstream.
- Validate repository scripts/tests and staging Compose syntax only. Do not contact a live Grafana instance or modify dashboards/datasources in production.
- Production effects: OFF.

## 34 — Codestra-Prometheus
- Repository/environment: `appolon1908/Codestra-Prometheus` / `Codestra-Prometheus`
- Type: Prometheus source/integration authority with pinned upstream.
- Setup: repository-local validation only; keep upstream lock unchanged unless a dedicated upgrade task says otherwise.
- Validate config/contracts without reloading a server.
- Never expose Prometheus publicly.
- Production effects: OFF.

## 35 — Codestra-Alertmanager
- Repository/environment: `appolon1908/Codestra-Alertmanager` / `Codestra-Alertmanager`
- Type: Alertmanager source/integration authority.
- Suggested setup:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements-validation.txt
  ```
- Verification: run repository-local validators/tests under `scripts/` and `tests/`.
- Do not send real alerts or reload live Alertmanager.
- Production effects: OFF.

## 36 — Codestra-Loki
- Repository/environment: `appolon1908/Codestra-Loki` / `Codestra-Loki`
- Type: Loki source/integration authority with upstream linkage.
- Setup: preserve pinned upstream/submodule identity. Initialize submodules only when the task requires upstream source inspection.
- Verification: repository-local scripts/tests only.
- Never expose Loki publicly or point tests at production log stores.
- Production effects: OFF.

## 37 — Codestra-Telemetry
- Repository/environment: `appolon1908/Codestra-Telemetry` / `Codestra-Telemetry`
- Type: telemetry/collector source integration authority.
- Setup: no credentials by default; preserve upstream lock.
- Verification: static/config validation only until a task explicitly needs an isolated collector runtime.
- No production telemetry export.
- Production effects: OFF.

## 38 — Codestra-Tempo
- Repository/environment: `appolon1908/Codestra-Tempo` / `Codestra-Tempo`
- Type: Tempo source/integration authority.
- Suggested setup:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements-validation.txt
  ```
- Verification: repository scripts/tests plus config validation; no server reload.
- Never expose Tempo publicly or send test traces to production.
- Production effects: OFF.

## 39 — Superset
- Repository/environment: `appolon1908/Superset` / `Superset`
- Type: Superset source/integration authority with pinned upstream and Codestra overlays.
- Suggested source-validation setup:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements-validation.txt
  ```
- Install runtime/OAuth requirements only for tasks that require those surfaces.
- Do not attach production databases, OAuth secrets, or live admin credentials.
- Production effects: OFF.

## 40 — Codestra-Node-Exporter
- Repository/environment: `appolon1908/Codestra-Node-Exporter` / `Codestra-Node-Exporter`
- Type: Node Exporter source/config authority.
- Setup: minimal source-validation environment; preserve upstream lock and hostname authority.
- Do not start/export host metrics to public interfaces from cloud setup.
- Production effects: OFF.

## UI creation rule
Create ten separate Codex Cloud environments, attach only the matching repository, keep credentials empty, prepare/test source validation, publish individually, and record each result in the central environment manifest.
