# Codex Cloud — Batch 09 Individual Environment Setup Specs

Date: 2026-10-02  
Invariant: **one repository per environment**. No production biometric, messaging, audio, or model-serving effects.

## 81 — Codestra-OCR-Workers
- Repository/environment: `appolon1908/Codestra-OCR-Workers` / `Codestra-OCR-Workers`
- Current repository is governance/docs oriented.
- No dependency install required until OCR worker implementation source is added.
- Future setup must use synthetic documents by default.

## 82 — Codestra-Document-Schemas
- Repository/environment: `appolon1908/Codestra-Document-Schemas` / `Codestra-Document-Schemas`
- Current repository is governance/docs oriented.
- Keep isolated as schema authority. No external service credentials required.

## 83 — Codestra-Document-SDK
- Repository/environment: `appolon1908/Codestra-Document-SDK` / `Codestra-Document-SDK`
- Current repository is governance/docs oriented.
- No dependency install required until SDK implementation/manifests are added.
- Do not publish packages during environment creation.

## 84 — Codestra-Document-Console
- Repository/environment: `appolon1908/Codestra-Document-Console` / `Codestra-Document-Console`
- Current repository is governance/docs oriented.
- No runtime dependencies until frontend source/manifests are added.
- No production document data in setup.

## 85 — WhatsApp-Frontend
- Repository/environment: `appolon1908/WhatsApp-Frontend` / `WhatsApp-Frontend`
- Default branch: `development`.
- README defines a frontend-only boundary but the current root does not yet expose the package manifest referenced by local-development instructions.
- Environment creation should succeed as a source/docs workspace now; install frontend dependencies only after the package manifest/source is present on the selected branch.
- Browser must never call Evolution-API/Meta directly.
- No live messaging/provider credentials.

## 86 — Codestra-TTS-Kokoro
- Repository/environment: `appolon1908/Codestra-TTS-Kokoro` / `Codestra-TTS-Kokoro`
- Current repository contains governance metadata only.
- No dependency/model download until implementation source is added.
- Do not download large model weights merely to publish the environment.

## 87 — Codestra-Mixxx-Native
- Repository/environment: `appolon1908/Codestra-Mixxx-Native` / `Codestra-Mixxx-Native`
- Type: native Mixxx ControlObject runtime/relay used by DJONE.
- Cloud setup is source/contract oriented; it cannot represent the user's real audio devices or native Mixxx session.
- Do not attempt localhost/Docker relay control of a real workstation from cloud setup.

## 88 — Codestra-Voice-Agent
- Repository/environment: `appolon1908/Codestra-Voice-Agent` / `Codestra-Voice-Agent`
- Runtime: Python 3.
- Setup:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  ```
- Verification: import/compile and repository-owned tests if present.
- No live calls, TTS provider credentials, or production telephony integration.

## 89 — Codestra-TTS-Piper
- Repository/environment: `appolon1908/Codestra-TTS-Piper` / `Codestra-TTS-Piper`
- Native/Python build uses scikit-build, CMake, Ninja, setuptools, and wheel.
- Setup for source/build tasks:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install cmake ninja scikit-build setuptools wheel
  ```
- Run full editable/native installation only when the assigned task needs it.
- Do not download voice models unnecessarily during reusable environment preparation.

## 90 — Codestra-TTS-F5
- Repository/environment: `appolon1908/Codestra-TTS-F5` / `Codestra-TTS-F5`
- Python project with heavy Torch/audio/model dependencies.
- Reusable environment should start source-oriented:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e . --no-deps
  ```
- Install full CPU/GPU/model dependencies only when a task genuinely requires inference/training.
- Do not download model weights or start public Gradio/model-serving endpoints during environment creation.

## UI creation
Create ten separate Codex Cloud environments, one repository each. Keep credentials/model weights/data absent by default, publish individually after source setup succeeds, smoke-test, and update the manifest.
