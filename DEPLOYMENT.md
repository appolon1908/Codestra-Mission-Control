# Deployment design — appolon1908/Codestra-Mission-Control

This repository is classified as **Data/platform** and remains independently deployable. Its accountable deployment owner is **appolon1908**.

## Canonical integration boundary

Mission Control has **no public ingress**. Its API and dashboard remain private on the tailnet. **Caddy -> Kong** is the public ingress path for product APIs; it does not authorize public exposure of Mission Control. Cross-system commands/effects use **Middleware :8095 /platform/v1**. This repository keeps its own domain/runtime ownership and must not become a duplicate command authority.

## Detected deployment assets

- Dockerfile/Containerfile detected: **false**
- Compose detected: **false**
- Kubernetes/Helm detected: **false**
- Health/readiness signal detected: **false**

The values above describe the current repository tree. They do not claim a runtime is deployed.

## Required deployment gates

1. Build from an immutable Git SHA and, where containerized, record the immutable image digest.
2. Keep provider/business effects disabled by default until the repository-specific production certification passes.
3. Consume secrets by OpenBao/governed references only; do not commit credentials or copy secret values into evidence.
4. Define health/readiness before staging activation. Current status: **design-required**.
5. Keep Mission Control APIs, /internal/* and /metrics private on the tailnet. Product API routes through Caddy/Kong do not create a public Mission Control route.
6. Record staging deployment, API/readback, observability and rollback evidence before production promotion.
7. Rollback must identify the prior SHA/image/config and must not require provider effects to validate.
8. Cross-repo provider-changing effects must be issued through the Middleware command lifecycle with idempotency and readback/reconciliation.

## Next implementation step

This document and .codestra/deployment-contract.json are the deployment-design authority. Any missing runtime artifact (Docker/Compose/Kubernetes/health probe) must be added in a repo-owned implementation PR only when that runtime is actually required; do not invent an unused deployment stack.
