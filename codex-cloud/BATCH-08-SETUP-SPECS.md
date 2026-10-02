# Codex Cloud — Batch 08 Individual Environment Setup Specs

Date: 2026-10-02  
Invariant: **one repository per environment**. Use synthetic test data by default; no production/device/provider effects.

## 71 — FACE-ID
- Repository/environment: `appolon1908/FACE-ID` / `FACE-ID`
- Runtime: Python 3.
- Setup:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  ```
- Verification: repository-local tests/scripts and import/compile checks.
- Use synthetic/test images only in environment preparation. Do not import production biometric/photo datasets or camera credentials.

## 72 — InsightFace
- Repository/environment: `appolon1908/InsightFace` / `InsightFace`
- Runtime: Python 3.
- Setup:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  ```
- Use synthetic/test imagery only. No production identity enrollment or external camera access.

## 73 — WhatsApp
- Repository/environment: `appolon1908/WhatsApp` / `WhatsApp`
- Runtime: Node >=20.
- Current package declares no external npm dependencies.
- Verification:
  ```bash
  npm test
  ```
- Do not send real WhatsApp messages or inject live provider/session credentials.

## 74 — Evolution-API
- Repository/environment: `appolon1908/Evolution-API` / `Evolution-API`
- Runtime: Node >=20.
- Current package declares no external npm dependencies.
- Verification:
  ```bash
  npm test
  ```
- Keep this a thin provider adapter behind Middleware; no direct production sending or provider session activation during setup.

## 75 — chatbox
- Repository/environment: `appolon1908/chatbox` / `chatbox`
- Current repository is governance/docs oriented.
- No dependency install required until implementation source is added.
- No live messaging connections during setup.

## 76 — Codestra-Face-Liveness
- Repository/environment: `appolon1908/Codestra-Face-Liveness` / `Codestra-Face-Liveness`
- Current repository contains architecture/OpenAPI authority but no implementation dependency manifest.
- Environment preparation is contract/schema focused.
- Use synthetic media only; no production biometric processing.

## 77 — Codestra-Camera-Gateway
- Repository/environment: `appolon1908/Codestra-Camera-Gateway` / `Codestra-Camera-Gateway`
- Current repository contains architecture/OpenAPI authority.
- Validate API/schema documentation only until runtime source lands.
- Do not connect RTSP/ONVIF/live cameras during environment creation.

## 78 — Codestra-PostgreSQL
- Repository/environment: `appolon1908/Codestra-PostgreSQL` / `Codestra-PostgreSQL`
- Current repository contains architecture/OpenAPI authority.
- Do not connect production databases or store database credentials.
- Any future database test must use disposable isolated PostgreSQL.

## 79 — Codestra-Connect
- Repository/environment: `appolon1908/Codestra-Connect` / `Codestra-Connect`
- Runtime: Python >=3.11 plus repository app/service packages.
- Setup:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e .
  ```
- Verification:
  ```bash
  make validate
  make test
  ```
- Load only non-secret test settings.

## 80 — Codestra-Document-Intelligence
- Repository/environment: `appolon1908/Codestra-Document-Intelligence` / `Codestra-Document-Intelligence`
- Current repository is governance/docs oriented.
- No dependency install required until implementation source is added.
- Use synthetic documents only during environment setup; no production customer/identity documents.

## UI creation
Create ten individual Codex Cloud environments with exactly one repository each. Keep credentials empty, validate source/contracts, publish separately, smoke-test, and update the central manifest.
