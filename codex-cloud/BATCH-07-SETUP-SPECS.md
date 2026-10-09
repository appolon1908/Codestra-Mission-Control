# Codex Cloud — Batch 07 Individual Environment Setup Specs

Date: 2026-10-02  
Invariant: **one repository per environment**; cloud preparation must not create production/media/device side effects.

## 61 — DJONE
- Repository/environment: `appolon1908/DJONE` / `DJONE`
- Type: DJ control plane with native Mixxx integration plus Docker services.
- Initial cloud setup: validate `compose.yaml`, service source, contracts, and scripts.
- Run `./scripts/preflight.sh` only in non-destructive source-validation mode.
- Do not attempt native audio-device control, TV streaming, or attach to the user's Mixxx instance from Codex Cloud.
- Auto-DJ remains disabled unless explicitly enabled in a dedicated test task.

## 62 — OBS-Studio-Nabeel-
- Repository/environment: `appolon1908/OBS-Studio-Nabeel-` / `OBS-Studio-Nabeel-`
- Current repository is governance/docs oriented.
- No dependency install required until implementation source is added.
- Do not start broadcasts or connect to live streaming credentials during environment setup.

## 63 — Chiaki
- Repository/environment: `appolon1908/Chiaki` / `Chiaki`
- Current repository is governance/docs oriented.
- No dependency install required until implementation/upstream source is added.
- Do not initiate remote-play sessions or use console credentials during setup.

## 64 — MediaMTX
- Repository/environment: `appolon1908/MediaMTX` / `MediaMTX`
- Current repository contains governance/docs/scripts.
- Setup only the tools required by repository scripts.
- Do not bind public streaming ports or connect production camera/stream sources during setup.

## 65 — Owncast-Nabeel
- Repository/environment: `appolon1908/Owncast-Nabeel` / `Owncast-Nabeel`
- Current repository is governance/docs oriented.
- No live stream keys, public broadcasting, or production chat integration during environment creation.

## 66 — Meltano
- Repository/environment: `appolon1908/Meltano` / `Meltano`
- Current repository contains governance metadata only.
- No dependency install required until Meltano project source/config is added.
- Keep this environment independent from `Meltano-Leads-Importer`.

## 67 — Codestra-Lead-importer-api
- Repository/environment: `appolon1908/Codestra-Lead-importer-api` / `Codestra-Lead-importer-api`
- Current repository contains governance metadata only.
- No dependency install required until API implementation source is added.
- Do not import or mutate real lead datasets during environment creation.

## 68 — openrerefine-
- Repository/environment: `appolon1908/openrerefine-` / `openrerefine-`
- Current repository contains governance metadata only.
- Keep independent for OpenRefine integration/source work when implementation is added.
- No real data cleanup/deletion during setup.

## 69 — Leads-Workstation
- Repository/environment: `appolon1908/Leads-Workstation` / `Leads-Workstation`
- Current repository is architecture/contracts oriented.
- No application dependency install required until build source lands.
- Preserve ownership rule: Leads API owns canonical lead writes; Meltano owns raw imports; n8n may not write canonical lead rows directly.
- No real lead import, dedupe, or deletion in environment setup.

## 70 — Meltano-Leads-Importer
- Repository/environment: `appolon1908/Meltano-Leads-Importer` / `Meltano-Leads-Importer`
- Current repository is architecture/contracts oriented.
- No Meltano/plugin install until implementation manifests are present or a task explicitly adds them.
- Test with synthetic fixtures only.
- Raw imports are never silently deleted; canonical promotion stays outside environment setup.

## UI creation
Create ten distinct Codex Cloud environments, attach only the matching repository, keep credentials empty by default, publish individually after source-level preparation succeeds, smoke-test, and update the central manifest.
