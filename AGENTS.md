# Mission Control Agent Contract — Implementation Only v2

1. Implementation is the assignment. Every coding agent must make a material code, configuration, migration, data, API, endpoint, or automated-test change. Review, audit, analysis, specification, acceptance criteria, or documentation alone never satisfies an implementation task.
2. Review the logic, then fix it in the same mission. Inspect design, workflow, API and endpoint correctness first; any required fix found by that review must be implemented before the agent stops.
3. Every run is numbered. Start the run through Mission Control and record agent_number, execution_id, mission, agent ID, provider, workstation, branch and worktree.
4. Read the assigned Linear issue and newest Notion/handoff context before editing.
5. Verify repository path, branch, local HEAD, dirty state, upstream/remote SHA and existing PR before writing.
6. Use a dedicated Git worktree and a non-main task branch. Never share a writable checkout between agents.
7. One writer lease per mission. Heartbeat while writing; stop writing if the lease expires.
8. Implement the smallest coherent production-quality slice. If the mission touches an API, record the implemented or changed endpoints explicitly.
9. Run focused tests, lint/static checks and contract checks. Fix failures introduced by the agent.
10. Commit only the task's intended files. Do not use reset, clean, stash, force push, destructive checkout, or overwrite unknown user work.
11. Push is part of implementation. Before pushing, fetch the exact remote branch/base, require a clean and non-stale worktree, and use a normal non-force push to the intended non-main branch.
12. Open or update a PR. A local commit without a pushed branch and PR is not delivered implementation.
13. Prove delivery by recording local delivery commit SHA, pushed remote branch SHA, and PR head SHA. All three must be present and exactly equal.
14. Record PR number and URL, implementation files, API endpoints, and machine-readable test evidence. A SHA mismatch or missing evidence is NEEDS_REWORK, never success.
15. IN_REVIEW never creates a review-only coding-agent mission. If implementation proof is missing, assign an implementation agent. If proof is PROVEN, external review and CI continue independently while the coding agent moves to the next implementation mission.
16. Never self-certify staging or production. Merge, staging mutation and production effects remain governed by approval policy.
17. End every proven delivery with a structured handoff and REQUEST_NEXT_TASK=true so the next assignment is another implementation mission.
