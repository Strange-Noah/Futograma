# Futograma operating rules

## Startup verification before filesystem writes
1. Locate `/Users/strangenoah/Desktop/Futbol` and read this AGENTS.md and any applicable repository-level instructions.
2. Resolve canonical paths and symlinks. Verify the working directory and every intended write target are within that authorized root.
3. Verify the intended GitHub repository, personal branch, remotes and worktree status. Preserve existing uncommitted work.
4. If a required boundary cannot be confirmed, stop the dependent write and ask for explicit authorization.

## Workspace boundary
- Authorized project root: `/Users/strangenoah/Desktop/Futbol`.
- Do not create project workspaces under `~/Documents/ChatGPT`, Google Drive CloudStorage, or unrelated directories.
- Keep branches, worktrees, test databases and temporary project artifacts inside the authorized root wherever feasible.
- External paths require explicit approval. Tool-managed temporary directories (e.g. OS temporary files or attachments) may be unavoidable: document their purpose and lifetime; do not use them as primary project storage or silently extend this exception to arbitrary external paths.
- These are project operating instructions, not OS-enforced filesystem restrictions. If the app's actual write permissions exclude this root, request the required tool approval rather than claiming these rules grant filesystem access.

## GitHub, data and production
- Use personal branches for experimental changes. Do not modify teammates' work or overwrite conflicting local files.
- Never modify production databases, production pins, active collection schedules or published site deployments without authorization.
- Read current authoritative production pointers before proposing a production mutation. GitHub production code and private versioned data remain authoritative for their respective assets; local placement is governed by this workspace root.
- Preserve source observations, canonical facts, backups, Git history and rollback capability. Never invent unverified football facts or silently reinterpret existing semantics.
- Do not incur additional fees without approval.

## Reporting and collaboration
- Reuse prior project decisions and established roster, source identity, minutes and competition policies.
- Maintain GitHub work records on personal branches; provide direct links to summaries, acceptance and handoff documents.
- Clearly distinguish verified facts, assumptions, unavailable evidence and unexecuted tests.
- Copy and verify before considering cleanup. Documents recovery sources remain intact pending separate cleanup authorization; do not change Google Drive settings or perform automatic cleanup.

## Recovery layout
The original root repository has pre-existing staged/untracked work and an unborn `codex/github-preparation` branch; preserve it.
The recovered current GitHub checkout is `recovery/20261104/documents/World Cup/remediation-20261103/repo` within this root. Use that checkout for candidate operations after startup verification, not the Documents source.
Recovery manifests and reconciliation records are in `recovery/20261104/records`. Do not add private databases, source evidence or local configuration to the public code repository.
