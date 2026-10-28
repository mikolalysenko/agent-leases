# Work log

## Simplification and permission-matrix integration

The tool now uses one operator-supplied GitHub token file. Token issuance, signing, caching, and refresh machinery were removed.

The user workflow is `init → approve → serve`, followed by `read`, `publish`, `pr`, and `comment`. Humans get readable output; scripts can use `--json`; agents can discover the same operations through `lease mcp`. Identical calls reuse a stable request ID across interfaces. The offline `lease demo` works from an installed package without importing test code.

The README now starts with the task-budget example. Setup, agent use, design, and security limits have separate short references. The original spec review was condensed around the engineering decisions that affect this implementation.

Permission Matrix gained a dedicated lease adapter. It observes the actual gateway through CLI, socket, and MCP worker processes and repeats those interactions inside the Linux sandbox. It preserves per-cell evidence and treats missing controls as unknown. Its observer tests ensure that a forbidden effect wins over a claimed denial or a worker crash.

Initial local verification: 164 host-interface matrix checks passed with no gaps or unknowns. Two malformed-file probes initially failed in the driver before reaching the tool; they were corrected to send the invalid payload through the JSON interface. The expanded Linux run and final counts are recorded after CI verification.

## Original MVP

The first version established transactional budgets, durable request results, four explicit GitHub operations, operator approval/grants/revocation, and the Linux sandbox. [Its Linux run](https://github.com/mikolalysenko/agent-leases/actions/runs/40616732020) passed 28 tests. A read-only live GitHub check matched the committed README, then confirmed revocation denied the next read. Live writes were not exercised.
