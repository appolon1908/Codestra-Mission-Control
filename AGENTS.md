# Mission Control Agent Contract

1. Read the assigned Linear issue and newest dated handoff before touching code.
2. Verify local path, branch, HEAD, dirty state, upstream and PR.
3. One writer lease per mission. Never write without a valid lease.
4. Use a dedicated Git worktree for parallel work. Never share a writable checkout.
5. Heartbeat while working. If the lease expires, stop writing.
6. Emit a checkpoint after implementation, tests, push, review handoff or blocker.
7. Never self-certify staging or production.
8. Never force-push, reset, clean, delete or overwrite unknown work.
9. Merge, staging mutation and production effects follow approval policy.
10. End completed work with a structured handoff and REQUEST_NEXT_TASK=true.
