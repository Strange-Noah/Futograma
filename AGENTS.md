# Futograma — Permanent Operating Rules

These rules apply to ALL Futograma tasks, sessions, agents, and scheduled executions. They are permanent project constraints, not task-specific preferences.

## 1. Architecture and Repository Responsibilities

Futograma is a cloud-first project. GitHub is the authoritative environment for development, execution, persistent project state, and production.

- `Strange-Noah/Futograma` (public): source code, scripts, pipeline logic, tests, GitHub Actions, website code, public documentation, and sanitized engineering reports.
- `Strange-Noah/Futograma-data` (private): versioned databases, private evidence, data manifests, candidate checkpoints, recovery assets, and authoritative website data.

Develop and persist new engineering assets directly in their appropriate GitHub repositories. Prefer existing GitHub-hosted execution and Actions. Temporary checkouts are disposable execution environments, not authoritative project storage.

Routine collection, database processing, and website deployment must operate independently of Agent sessions and the local Mac. Preserve the separation between collection and deployment workflows.

GitHub Actions handles deterministic scheduled production. Agents handle development, investigation, exceptional decisions, and tasks requiring model reasoning.

## 2. Workspace, Permissions, and Security

The sole authorized local project root is:

`/Users/strangenoah/Desktop/Futbol`

Local storage is reserved for historical project assets, recovery of existing work, and exceptional copies of critical deliverables. Do not make local storage the default destination for new scripts, databases, logs, or raw responses.

Before dependent operations, verify the actual execution environment, workspace binding, canonical paths, symlinks, filesystem permissions, network permissions, GitHub access, and approval policy.

- Never access unrelated local directories or revive obsolete workspaces.
- Never bypass sandbox, approval, network, or source-access restrictions.
- Preserve existing local assets, uncommitted work, conflicts, and teammates' contributions.
- ChatGPT Chat has read-only GitHub authorization.
- Work agents may write only within explicitly authorized branches, candidates, and reporting scopes.
- Technical access does not constitute user authorization.
- No additional paid services or newly allocated compute without approval.
- Never expose private data, credentials, raw private evidence, or sensitive configuration in the public repository.
- Before repository mutations, verify the intended repository, personal branch, remotes, and applicable worktree status.
- Read current authoritative production pointers before proposing any production mutation.
- Unavoidable tool-managed temporary directories do not extend local workspace authorization. Document their purpose and lifetime.
- Preserve recovery assets and unresolved conflicts. Copy and verify before cleanup; never delete recovery sources or change Google Drive settings without separate authorization.

Do not modify production databases, production pins, active schedules, deployment configuration, or protected branches without explicit authorization. Preserve rollback capability.

## 3. Persistent Memory

Maintain project memory continuously. Do not rely on conversation history for project continuity.

Separate three layers:

- **Permanent rules:** architecture, permissions, business semantics, and operating requirements.
- **Active state:** current versions, checkpoints, acceptance matrix, unresolved obligations, and next actions.
- **Historical evidence:** detailed reports, raw observations, logs, and previous decisions.

AGENTS.md contains permanent rules only.

Persist material decisions when they are made, execution state when checkpoints advance, and verification evidence when acceptance is completed.

Keep active state concise, authoritative, versioned, and directly discoverable. Historical evidence must remain retrievable but must not be loaded by default.

Reuse existing memory mechanisms. Update superseded rules instead of accumulating conflicting instructions. Do not create parallel memory systems or duplicate documentation.

Do not unilaterally change permanent rules or expand permissions.

Every new session must be able to resume from persistent state without reconstructing previous conversations.

## 4. Database Construction

The fundamental entity is player-match workload. Squad membership reflects the club's ability to manage a player's workload during the applicable period.

- Respect half-season boundaries, transfers, loans, and verified membership intervals.
- Include incoming loans; exclude outgoing loans and departed players from the corresponding management periods.
- Do not infer departure from absence alone.
- Preserve observed squad membership and inferred membership as distinct evidence.
- Apply existing FIRST_TEAM, YOUTH, UNKNOWN, and membership eligibility policies.
- Preserve established Club, Total, competition, national-team, and rolling-window semantics.

Verify competition completeness, canonical fixture identity, ordered home/away teams, match status, score, starting XI, bench, player identity, participation, minutes, and applicable international coverage.

Never fabricate football facts, membership intervals, source identities, or minute values. UNKNOWN is not zero. Unused-substitute zeroes require positive evidence. Fuzzy names alone cannot justify identity merges.

Reuse canonical identities and preserve verified historical facts. Maintain provenance, source observations, and evidence-backed decisions.

## 5. Auditing and Acceptance

The existing R1–R6 audit specification is mandatory. Locate its current authoritative implementation and rules; never replace it with a simplified interpretation.

In particular:

- R3: investigate applicable non-offseason absences of at least five days from match squads.
- R4: use teammate participation as corroborating evidence according to the established conditions.
- R5: identify international windows before evaluating R3/R4, distinguishing club and national-team obligations.

Triggered anomalies require actual evidence-driven investigation across permitted sources. Pending status is not a substitute for investigation. Prioritize missing data and source-identity errors when elite-club absences appear implausible.

Respect established national-team scope and geographic coverage policies.

Keep SOURCE-READY, ordinary export PASS, DATA-ACCEPTED, and production promotion strictly distinct.

STABLE requires its established independent evidence and fingerprint conditions. Conflicts or dependency changes reopen affected verification obligations.

Never weaken acceptance criteria to improve completion statistics. Preserve unknowns, isolate conflicts, and distinguish unavailable evidence from verified failure.

Use delayed reconciliation for recent incomplete source data. Escalate persistent unresolved failures according to existing policy.

## 6. Quota and Context Efficiency

Agent reasoning is for solving unresolved problems, not repeatedly executing solved procedures.

### Execution

- Reuse existing pipelines, scripts, caches, parsers, writers, and tests.
- Delegate deterministic collection, filtering, matching, aggregation, and validation to programs.
- Batch homogeneous operations; avoid per-record model interaction.
- Persist checkpoints and ensure idempotent continuation.
- Reuse verified observations and avoid redundant retrieval or parsing.
- Prefer targeted tests before broader regression; never omit mandatory acceptance gates.
- Diagnose repeated failures instead of mechanically retrying.
- Avoid unnecessary polling. Long-running deterministic commands are preferable to fragmented model-driven execution.

### Generic Deterministic Execution — Mandatory

Routine database construction must run through reusable, parameterized, deterministic execution pipelines:

Gap discovery → task generation → collection → parsing and verification → canonical ingestion → auditing → renewed gap discovery.

The pipeline must continue autonomously within authorized limits, without requiring Agent intervention between routine stages.

Agents control execution primarily by changing keywords, target scopes, structured inputs, and task parameters. Agents inspect concise results and resolve only exceptional cases that cannot be handled deterministically.

Do not implement routine target changes through one-off scripts, hardcoded fixtures or players, repeated workflow modifications, or unnecessary code revisions. Pin trusted code and data versions through validated manifests and handoffs.

New logic must address reusable problem classes rather than isolated cases wherever feasible.

Return only aggregated progress and actionable unresolved exceptions to the model. Persist full evidence and logs separately.

Feed Agent-supplied evidence and decisions back into the deterministic pipeline as structured inputs.

Engineering improvements must serve actual database delivery. Evaluate efficiency primarily by verified data increments, closed audit obligations, and accepted units, not by the number of Agent actions or engineering changes.

### Context

- Read exact paths and relevant sections only.
- Do not routinely scan entire repositories, historical reports, databases, or raw logs.
- Persist full program output outside the model context in authorized storage.
- Return concise counts, state changes, exceptions, and necessary evidence.
- Avoid redundant tool calls, repeated explanations, and excessive intermediate reports.
- Use the least costly adequate model and reasoning configuration when selectable.
- Avoid unnecessary context compaction and repeated reconstruction of previously established knowledge.

Rotate sessions at safe checkpoints when beneficial. Do not sacrifice correctness, evidence, or acceptance standards merely to reduce quota usage.

## 7. Execution Lifecycle and Reporting

At startup:

1. Verify permissions relevant to the task.
2. Read applicable rules and the latest authoritative handoff.
3. Verify checkpoint, candidate version, and write ownership.
4. Resume unfinished work without repeating accepted work.

During execution, persist material results and state changes. Preserve isolation between historical candidates and production.

At a safe checkpoint, verify consistency, complete required acceptance, persist updated state, release locks, and prepare a concise resumable handoff.

Final reports must identify actual progress, verified results, blockers, remaining obligations, and the precise next action.

Maintain GitHub work records and provide direct links to summaries, acceptance reports, and handoffs. Clearly label local-only or unverified results. Never fabricate remote links or claim unexecuted verification.

Do not create or modify other Work sessions, scheduled tasks, production resources, or project settings without user authorization.

## 8. Conflict Resolution

Explicit current user instructions take precedence over superseded project decisions, subject to actual permissions and platform restrictions.

If a required boundary cannot be verified, stop the dependent operation and report the precise blocker. Continue independent authorized work where safe.

Do not restart the project, reconstruct accepted work, or redesign functioning components merely because a new session has started.
