# Work log

## Modular adapters and complete command workflow — v0.3

Service behavior now lives in adapters. A modular policy can combine GitHub,
explicit MCP request bindings, and installed Python plugins. The gateway, counters,
and MCP frontend discover their operations rather than maintaining a GitHub tool
list. The existing single-GitHub policy remains compatible.

`lease launch` now owns approval, gateway startup, sandbox execution, and cleanup.
The worker can submit a question or a narrow budget request, poll for the answer,
and retry a refused operation after approval. Only local operator commands can
answer or grant. Requests persist, accept partial grants, and have a spam limit.

The README, sandbox walkthrough, agent instructions, adapter contract, design,
and security boundary were rewritten around an executable two-terminal example.
Direct native-client traffic remains blocked. Transparent `gh` forwarding and
cloud-model connectivity are explicitly outside this version.

Local verification passed 50 tests, with eight Linux integrations deferred to CI,
and 178 permission-matrix checks across CLI, socket, and MCP with no gaps or
unknowns. The new matrix extension was isolated from the user's other working-tree
changes. Linux results are recorded below after the published run completes.

## Simplification and permission-matrix integration

The tool now uses one operator-supplied GitHub token file. Token issuance, signing, caching, and refresh machinery were removed.

The user workflow is `init → approve → serve`, followed by `read`, `publish`, `pr`, and `comment`. Humans get readable output; scripts can use `--json`; agents can discover the same operations through `lease mcp`. Identical calls reuse a stable request ID across interfaces. The offline `lease demo` works from an installed package without importing test code.

The README now starts with the task-budget example. Setup, agent use, design, and security limits have separate short references. The original spec review was condensed around the engineering decisions that affect this implementation.

Permission Matrix gained a dedicated lease adapter. It observes the actual gateway through CLI, socket, and MCP worker processes and repeats those interactions inside the Linux sandbox. It preserves per-cell evidence and treats missing controls as unknown. Its observer tests ensure that a forbidden effect wins over a claimed denial or a worker crash.

Verification:

- [Linux CI](https://github.com/mikolalysenko/agent-leases/actions/runs/40623965239): **371 matrix checks across 75 cases and six paths passed**, with no gaps or unknowns. The saved report was checked against the entire declared case inventory; every cell had a working control and evidence. Provider effects, budget ceilings, partial revocation, and controlled network delivery were also inspected.
- The same suite passed in [Permission Matrix's Linux workflow](https://github.com/mikolalysenko/permission-matrix/actions/runs/40623966782).
- All 32 lease unit/integration tests passed on Linux. Packaging and the offline demo worked from a fresh install outside the checkout. macOS passed 31 tests and correctly skipped the Linux-only unit integration test.
- The isolated permission-matrix branch passed all 31 existing/new tests; the user's current working tree passed all 59 of its tests. Its six observer checks include injected writes followed by misleading denial/error responses.
- A live read through the simplified token-file gateway matched the committed README, repeated without consuming another read, and was refused after revocation. No live writes were performed.

Two malformed-file probes initially failed in the matrix driver before reaching the tool; they were corrected to send the invalid payload through the JSON interface. No enforcement gap was observed in the completed inventory. This is a result for these tested interactions, not a claim about every possible attack.

Existing uncommitted permission-matrix work was preserved. The lease adapter was published on a separate `agent-leases-adapter` branch; Agent Leases CI pins its tested commit.

## Original MVP

The first version established transactional budgets, durable request results, four explicit GitHub operations, operator approval/grants/revocation, and the Linux sandbox. [Its Linux run](https://github.com/mikolalysenko/agent-leases/actions/runs/40616732020) passed 28 tests. A read-only live GitHub check matched the committed README, then confirmed revocation denied the next read. Live writes were not exercised.
