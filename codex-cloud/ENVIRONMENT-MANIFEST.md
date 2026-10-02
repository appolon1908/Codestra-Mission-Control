# Codex Cloud — One Repository Per Environment

Date: 2026-10-02  
Owner: `appolon1908`  
Target: **1 repository = 1 separate Codex Cloud environment**

## Non-negotiable rules

- Every GitHub repository gets its **own individual Codex Cloud environment**.
- Do **not** group repositories into shared environments.
- Environment name should match the repository name unless Codex requires a uniqueness suffix.
- Each environment must include **only its own repository**.
- Use the repository's current default branch for setup discovery.
- Default privacy: **Only me**.
- Default internet policy: **Package managers only**, expanding domains only when the repository setup proves it needs them.
- Do not add secrets unless the repository explicitly requires them.
- Do not enable production side effects during environment preparation.
- Do not push directly to `main`, do not force-push, and preserve repository governance.
- Publish only after Codex has inspected dependencies/tools and setup validation has completed.

## Status legend

- `PENDING_CREATE`: environment has not been created yet.
- `SETUP_SPEC_READY`: repository-specific environment setup/verification instructions are prepared; the Codex Cloud UI object is not yet created.
- `SETTING_UP`: Codex is inspecting/installing/testing.
- `BLOCKED`: user input, credentials, permissions, or repository repair is required.
- `READY_TO_PUBLISH`: setup passed and is awaiting Publish.
- `PUBLISHED`: reusable Codex Cloud environment is published.
- `VERIFIED`: a fresh cloud task was started successfully from the published environment.

## Environment inventory

| # | Repository | Environment | Default branch | Visibility | Status |
|---:|---|---|---|---|---|
| 1 | `appolon1908/codestra` | `codestra` | `main` | private | SETUP_SPEC_READY |
| 2 | `appolon1908/beyvra-backend` | `beyvra-backend` | `main` | public | SETUP_SPEC_READY |
| 3 | `appolon1908/codestra-backend` | `codestra-backend` | `main` | private | SETUP_SPEC_READY |
| 4 | `appolon1908/backend2` | `backend2` | `main` | private | SETUP_SPEC_READY |
| 5 | `appolon1908/beyvra-frontend` | `beyvra-frontend` | `main` | public | SETUP_SPEC_READY |
| 6 | `appolon1908/scrapper` | `scrapper` | `main` | private | SETUP_SPEC_READY |
| 7 | `appolon1908/Breero.com` | `Breero.com` | `main` | public | SETUP_SPEC_READY |
| 8 | `appolon1908/booked4seasons` | `booked4seasons` | `main` | private | SETUP_SPEC_READY |
| 9 | `appolon1908/kyqra` | `kyqra` | `main` | private | SETUP_SPEC_READY |
| 10 | `appolon1908/telnexa` | `telnexa` | `main` | private | SETUP_SPEC_READY |
| 11 | `appolon1908/kyqra-crawler` | `kyqra-crawler` | `main` | private | SETUP_SPEC_READY |
| 12 | `appolon1908/klyrow.com` | `klyrow.com` | `main` | private | SETUP_SPEC_READY |
| 13 | `appolon1908/codestra-provisioning-service` | `codestra-provisioning-service` | `main` | private | SETUP_SPEC_READY |
| 14 | `appolon1908/Moneybee-frontend-` | `Moneybee-frontend-` | `main` | private | SETUP_SPEC_READY |
| 15 | `appolon1908/Moneybee-Backend` | `Moneybee-Backend` | `main` | private | SETUP_SPEC_READY |
| 16 | `appolon1908/LARIM-A-Backend` | `LARIM-A-Backend` | `main` | private | SETUP_SPEC_READY |
| 17 | `appolon1908/Telnexa-web` | `Telnexa-web` | `main` | private | SETUP_SPEC_READY |
| 18 | `appolon1908/klyrow-Website-` | `klyrow-Website-` | `main` | private | SETUP_SPEC_READY |
| 19 | `appolon1908/Odoo` | `Odoo` | `main` | public | SETUP_SPEC_READY |
| 20 | `appolon1908/Keycloak` | `Keycloak` | `main` | public | SETUP_SPEC_READY |
| 21 | `appolon1908/Middleware-` | `Middleware-` | `main` | public | SETUP_SPEC_READY |
| 22 | `appolon1908/N8N` | `N8N` | `main` | private | SETUP_SPEC_READY |
| 23 | `appolon1908/Vicidialer-Codestra` | `Vicidialer-Codestra` | `main` | private | SETUP_SPEC_READY |
| 24 | `appolon1908/Kong` | `Kong` | `main` | public | SETUP_SPEC_READY |
| 25 | `appolon1908/social.codestra.co` | `social.codestra.co` | `main` | private | SETUP_SPEC_READY |
| 26 | `appolon1908/SDK-repository` | `SDK-repository` | `main` | private | SETUP_SPEC_READY |
| 27 | `appolon1908/Caddy` | `Caddy` | `main` | public | SETUP_SPEC_READY |
| 28 | `appolon1908/Infustruction-repo` | `Infustruction-repo` | `main` | private | PENDING_CREATE |
| 29 | `appolon1908/communication-platform-` | `communication-platform-` | `main` | private | PENDING_CREATE |
| 30 | `appolon1908/Codestra-Grafana-` | `Codestra-Grafana-` | `main` | private | PENDING_CREATE |
| 31 | `appolon1908/Codestra-Prometheus` | `Codestra-Prometheus` | `main` | private | PENDING_CREATE |
| 32 | `appolon1908/Codestra-Alertmanager` | `Codestra-Alertmanager` | `main` | private | PENDING_CREATE |
| 33 | `appolon1908/Codestra-Loki` | `Codestra-Loki` | `main` | private | PENDING_CREATE |
| 34 | `appolon1908/Codestra-Telemetry` | `Codestra-Telemetry` | `main` | private | PENDING_CREATE |
| 35 | `appolon1908/Codestra-Tempo` | `Codestra-Tempo` | `main` | private | PENDING_CREATE |
| 36 | `appolon1908/Superset` | `Superset` | `main` | private | PENDING_CREATE |
| 37 | `appolon1908/Codestra-Node-Exporter` | `Codestra-Node-Exporter` | `main` | private | PENDING_CREATE |
| 38 | `appolon1908/Codestra-cAdvisor` | `Codestra-cAdvisor` | `main` | private | PENDING_CREATE |
| 39 | `appolon1908/Codestra-Redis-Exporter` | `Codestra-Redis-Exporter` | `main` | private | PENDING_CREATE |
| 40 | `appolon1908/Codestra-Blackbox-Exporter` | `Codestra-Blackbox-Exporter` | `main` | private | PENDING_CREATE |
| 41 | `appolon1908/Codestra-Alloy` | `Codestra-Alloy` | `main` | private | PENDING_CREATE |
| 42 | `appolon1908/Codestra-OpenBao` | `Codestra-OpenBao` | `main` | private | SETUP_SPEC_READY |
| 43 | `appolon1908/Codestra-Postgres-Exporter` | `Codestra-Postgres-Exporter` | `main` | private | PENDING_CREATE |
| 44 | `appolon1908/Codestra-Marketing-` | `Codestra-Marketing-` | `main` | private | PENDING_CREATE |
| 45 | `appolon1908/Codestra-Communication-CC` | `Codestra-Communication-CC` | `main` | private | PENDING_CREATE |
| 46 | `appolon1908/Codesrea-Social-` | `Codesrea-Social-` | `main` | private | PENDING_CREATE |
| 47 | `appolon1908/Codestra-AI` | `Codestra-AI` | `main` | private | PENDING_CREATE |
| 48 | `appolon1908/codestra-foundation` | `codestra-foundation` | `main` | private | PENDING_CREATE |
| 49 | `appolon1908/Database-migrations-` | `Database-migrations-` | `main` | private | PENDING_CREATE |
| 50 | `appolon1908/codestra-server-c` | `codestra-server-c` | `main` | private | PENDING_CREATE |
| 51 | `appolon1908/codestra-ruleset-toolkit` | `codestra-ruleset-toolkit` | `main` | private | PENDING_CREATE |
| 52 | `appolon1908/Sentry` | `Sentry` | `main` | private | PENDING_CREATE |
| 53 | `appolon1908/Wazuh` | `Wazuh` | `main` | private | PENDING_CREATE |
| 54 | `appolon1908/Agent-Desktop-WebRTC` | `Agent-Desktop-WebRTC` | `main` | public | PENDING_CREATE |
| 55 | `appolon1908/OpenAPI` | `OpenAPI` | `main` | public | PENDING_CREATE |
| 56 | `appolon1908/codestra-platform` | `codestra-platform` | `main` | private | PENDING_CREATE |
| 57 | `appolon1908/kyyow-contracts` | `kyyow-contracts` | `main` | private | PENDING_CREATE |
| 58 | `appolon1908/websocket` | `websocket` | `main` | public | SETUP_SPEC_READY |
| 59 | `appolon1908/Codestra-Mission-Control` | `Codestra-Mission-Control` | `main` | private | SETUP_SPEC_READY |
| 60 | `appolon1908/Codestra-Device-Forge` | `Codestra-Device-Forge` | `main` | public | PENDING_CREATE |
| 61 | `appolon1908/DJONE` | `DJONE` | `main` | public | PENDING_CREATE |
| 62 | `appolon1908/OBS-Studio-Nabeel-` | `OBS-Studio-Nabeel-` | `main` | public | PENDING_CREATE |
| 63 | `appolon1908/Chiaki` | `Chiaki` | `main` | public | PENDING_CREATE |
| 64 | `appolon1908/MediaMTX` | `MediaMTX` | `main` | public | PENDING_CREATE |
| 65 | `appolon1908/Owncast-Nabeel` | `Owncast-Nabeel` | `main` | public | PENDING_CREATE |
| 66 | `appolon1908/Meltano` | `Meltano` | `main` | public | PENDING_CREATE |
| 67 | `appolon1908/Codestra-Lead-importer-api` | `Codestra-Lead-importer-api` | `main` | private | PENDING_CREATE |
| 68 | `appolon1908/openrerefine-` | `openrerefine-` | `main` | public | PENDING_CREATE |
| 69 | `appolon1908/Leads-Workstation` | `Leads-Workstation` | `main` | private | PENDING_CREATE |
| 70 | `appolon1908/Meltano-Leads-Importer` | `Meltano-Leads-Importer` | `main` | private | PENDING_CREATE |
| 71 | `appolon1908/FACE-ID` | `FACE-ID` | `main` | public | PENDING_CREATE |
| 72 | `appolon1908/InsightFace` | `InsightFace` | `main` | public | PENDING_CREATE |
| 73 | `appolon1908/WhatsApp` | `WhatsApp` | `main` | public | PENDING_CREATE |
| 74 | `appolon1908/Evolution-API` | `Evolution-API` | `main` | public | PENDING_CREATE |
| 75 | `appolon1908/chatbox` | `chatbox` | `main` | public | PENDING_CREATE |
| 76 | `appolon1908/Codestra-Face-Liveness` | `Codestra-Face-Liveness` | `main` | private | PENDING_CREATE |
| 77 | `appolon1908/Codestra-Camera-Gateway` | `Codestra-Camera-Gateway` | `main` | private | PENDING_CREATE |
| 78 | `appolon1908/Codestra-PostgreSQL` | `Codestra-PostgreSQL` | `main` | private | PENDING_CREATE |
| 79 | `appolon1908/Codestra-Connect` | `Codestra-Connect` | `main` | private | PENDING_CREATE |
| 80 | `appolon1908/Codestra-Document-Intelligence` | `Codestra-Document-Intelligence` | `main` | private | PENDING_CREATE |
| 81 | `appolon1908/Codestra-OCR-Workers` | `Codestra-OCR-Workers` | `main` | private | PENDING_CREATE |
| 82 | `appolon1908/Codestra-Document-Schemas` | `Codestra-Document-Schemas` | `main` | private | PENDING_CREATE |
| 83 | `appolon1908/Codestra-Document-SDK` | `Codestra-Document-SDK` | `main` | private | PENDING_CREATE |
| 84 | `appolon1908/Codestra-Document-Console` | `Codestra-Document-Console` | `main` | private | PENDING_CREATE |
| 85 | `appolon1908/WhatsApp-Frontend` | `WhatsApp-Frontend` | `development` | public | PENDING_CREATE |
| 86 | `appolon1908/Codestra-TTS-Kokoro` | `Codestra-TTS-Kokoro` | `main` | private | PENDING_CREATE |
| 87 | `appolon1908/Codestra-Mixxx-Native` | `Codestra-Mixxx-Native` | `main` | private | PENDING_CREATE |
| 88 | `appolon1908/Codestra-Voice-Agent` | `Codestra-Voice-Agent` | `main` | private | PENDING_CREATE |
| 89 | `appolon1908/Codestra-TTS-Piper` | `Codestra-TTS-Piper` | `main` | private | PENDING_CREATE |
| 90 | `appolon1908/Codestra-TTS-F5` | `Codestra-TTS-F5` | `main` | private | PENDING_CREATE |
| 91 | `appolon1908/Mobile-Control-Server` | `Mobile-Control-Server` | `main` | private | PENDING_CREATE |
| 92 | `appolon1908/Mobile-Android-Agent` | `Mobile-Android-Agent` | `main` | private | PENDING_CREATE |
| 93 | `appolon1908/Mobile-Control-Console` | `Mobile-Control-Console` | `main` | private | PENDING_CREATE |
| 94 | `appolon1908/Mobile-Device-Gateway` | `Mobile-Device-Gateway` | `main` | private | PENDING_CREATE |
| 95 | `appolon1908/Mobile-Sync` | `Mobile-Sync` | `main` | private | PENDING_CREATE |
| 96 | `appolon1908/Mobile-Contracts-SDK` | `Mobile-Contracts-SDK` | `main` | private | PENDING_CREATE |
| 97 | `appolon1908/Mobile-Control-UI` | `Mobile-Control-UI` | `main` | private | PENDING_CREATE |
| 98 | `appolon1908/Codestra-Workstation` | `Codestra-Workstation` | `feat/windows-professional-workstation-20260926` | private | PENDING_CREATE |
| 99 | `appolon1908/Brainbend` | `Brainbend` | `main` | public | PENDING_CREATE |
| 100 | `appolon1908/CRM-` | `CRM-` | `main` | public | PENDING_CREATE |

## Execution procedure for every row

1. Open **Settings → Codex Cloud → Environments → Create environment**.
2. Select exactly the repository shown in that row and no other repository.
3. Select **Get started**.
4. Let Codex inspect the repository, install dependencies/tools, and test the workflow.
5. Resolve only repository-specific setup blockers; do not merge unrelated work.
6. Review install script, start skill, network policy, environment variables, and privacy.
7. Select **Publish**.
8. Start one fresh cloud task from the published environment to verify checkout and basic setup.
9. Update that row to `PUBLISHED` or `VERIFIED` with any blocker note.

## Current batch

Batch 01 repository-specific setup specifications are now recorded in `codex-cloud/BATCH-01-SETUP-SPECS.md`. The Codex Cloud UI objects still require Create environment -> Get started -> Publish.

Start with the platform/control-plane repositories first while preserving the same one-repo/one-environment rule:

- `Middleware-`
- `Kong`
- `Caddy`
- `Keycloak`
- `Odoo`
- `websocket`
- `N8N`
- `codestra-provisioning-service`
- `Codestra-Mission-Control`
- `Codestra-OpenBao`

Then continue until all 100 repositories are published independently.


## Batch 02 preparation

Repository-specific setup specifications for `codestra`, `beyvra-backend`, `codestra-backend`, `backend2`, `beyvra-frontend`, `scrapper`, `Breero.com`, `booked4seasons`, `kyqra`, and `telnexa` are recorded in `codex-cloud/BATCH-02-SETUP-SPECS.md`.


## Batch 03 preparation

Repository-specific setup specifications for the next ten repositories are recorded in `codex-cloud/BATCH-03-SETUP-SPECS.md`.
