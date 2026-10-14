# Implementation-Only Agent Contract v2

## Purpose

Codestra agents are implementation workers. Mission Control must distinguish reviewed or discussed work from material implementation that was actually delivered.

The contract prevents review-only loops and makes each coding delivery machine-verifiable.

## Execution identity

Every coding run receives:

- agent_number: monotonically increasing portfolio-local integer
- execution_id: globally unique execution identifier
- mission_id
- agent_id
- provider
- workstation
- branch
- worktree
- api_required

The same identity stays attached to checkpoints and delivery proof.

## Required execution sequence

1. Read Linear execution state and current Notion mission context.
2. Verify repository, branch, HEAD, dirty state, upstream SHA and PR state.
3. Acquire the writer lease and isolated worktree.
4. Review logic, API design and workflow.
5. Implement the fixes discovered by that review.
6. Run tests and static or contract checks.
7. Commit the implementation.
8. Fetch remote authority again.
9. Push the non-main task branch with a normal non-force push.
10. Open or update the pull request.
11. Read back remote branch SHA and PR head SHA.
12. Record implementation proof.
13. If proof is PROVEN, release the coding agent to the next implementation mission while review and CI proceed independently.

## Delivery proof gate

PROVEN requires all of the following:

- at least one material implementation file
- passing test evidence
- API or endpoint evidence when api_required is true
- local delivery commit SHA
- pushed branch SHA
- PR head SHA
- PR URL
- exact equality between local commit SHA, pushed branch SHA and PR head SHA

Any missing evidence or SHA mismatch is NEEDS_REWORK.

## Private implementation API

Mission Control exposes these private or local control-plane endpoints:

- POST /platform/v1/agent-executions
- GET /platform/v1/agent-executions
- GET /platform/v1/agent-executions/{execution_id}
- POST /platform/v1/agent-executions/{execution_id}/proof
- GET /health

Start the private API with:

    codestra-mission-control serve-implementation-api --host 127.0.0.1 --port 8790

This endpoint is not public edge authority and must not be exposed through Caddy or Kong without a separate security design.

## CLI workflow

Start an implementation run with implementation-start and include mission, agent, workstation, provider, branch and worktree. Use --api-required for missions that change or add endpoints.

Record proof with implementation-proof and include:

- one or more --implementation-file values
- one or more --api-endpoint values when API work is required
- --tests-json with passing machine-readable evidence
- --local-sha
- --pushed-sha
- --pr-number
- --pr-url
- --pr-head-sha

Inspect current tracking with implementation-list and implementation-status.

## Controller behavior

- IN_REVIEW or MERGE_READY without a PROVEN implementation delivery: REASSIGN to an implementation agent.
- IN_REVIEW or MERGE_READY with PROVEN delivery: NEXT_IMPLEMENTATION; external review and CI proceed independently.
- Review-only output never advances implementation truth.

## Safety

This contract does not authorize:

- force pushes
- direct pushes to main
- reset, clean, stash, discard or overwriting unknown work
- merge bypass
- staging mutation
- production or provider effects

Those remain controlled by existing approval policy.
