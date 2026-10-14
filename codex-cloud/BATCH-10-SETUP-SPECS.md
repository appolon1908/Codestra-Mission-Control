# Codex Cloud — Batch 10 Individual Environment Setup Specs

Date: 2026-10-02  
Invariant: **one repository per environment**. No real-device or workstation mutation from cloud environment preparation.

## 91 — Mobile-Control-Server
- Repository/environment: `appolon1908/Mobile-Control-Server` / `Mobile-Control-Server`
- Current repository contains governance plus a component README.
- No dependency install required until server implementation source/manifests are added.
- Preserve independent service boundary.

## 92 — Mobile-Android-Agent
- Repository/environment: `appolon1908/Mobile-Android-Agent` / `Mobile-Android-Agent`
- Current repository contains governance plus a component README.
- No Android SDK/Gradle install until implementation source requires it.
- No real-device management, FRP/lock bypass, or device mutation during environment creation.

## 93 — Mobile-Control-Console
- Repository/environment: `appolon1908/Mobile-Control-Console` / `Mobile-Control-Console`
- Current repository is component scaffold/docs.
- No dependency install required until console source is added.

## 94 — Mobile-Device-Gateway
- Repository/environment: `appolon1908/Mobile-Device-Gateway` / `Mobile-Device-Gateway`
- Current repository is component scaffold/docs.
- No ADB/Fastboot/device connections during environment creation.

## 95 — Mobile-Sync
- Repository/environment: `appolon1908/Mobile-Sync` / `Mobile-Sync`
- Current repository is component scaffold/docs.
- No production synchronization endpoints or credentials during setup.

## 96 — Mobile-Contracts-SDK
- Repository/environment: `appolon1908/Mobile-Contracts-SDK` / `Mobile-Contracts-SDK`
- Current repository is component scaffold/docs.
- Keep isolated as the future mobile contract/SDK authority.
- Do not publish packages during setup.

## 97 — Mobile-Control-UI
- Repository/environment: `appolon1908/Mobile-Control-UI` / `Mobile-Control-UI`
- Current repository is operator-UI scaffold/docs.
- No dependency install until frontend package manifests/source are added.
- No production device-control endpoints during setup.

## 98 — Codestra-Workstation
- Repository/environment: `appolon1908/Codestra-Workstation` / `Codestra-Workstation`
- Current GitHub default branch: `feat/windows-professional-workstation-20260926`.
- Type: Windows workstation configuration scripts.
- Codex Cloud is a source-review/test environment only; it must not attempt to apply Windows desktop policy to the cloud Linux worker.
- PowerShell syntax/static checks may be used where supported.
- Do not run elevated workstation install scripts against any connected real machine from cloud setup.

## 99 — Brainbend
- Repository/environment: `appolon1908/Brainbend` / `Brainbend`
- Current repository contains governance metadata only.
- No dependency install required until implementation source is added.

## 100 — CRM-
- Repository/environment: `appolon1908/CRM-` / `CRM-`
- Current repository contains governance metadata only.
- No dependency install required yet.
- If/when the complete Odoo Community source is added, update this environment separately from `appolon1908/Odoo`; do not merge the two repositories into one environment.

## UI creation
Create ten individual Codex Cloud environments. Each must attach exactly its named repository and no others. Keep secrets empty, publish individually, smoke-test, and update the central manifest.
