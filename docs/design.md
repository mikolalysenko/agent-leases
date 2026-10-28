# How it works

There are three roles. The **operator** approves and answers requests. The **gateway** holds state and service connections. The **worker** runs in a sandbox with a socket to that gateway. `lease launch` owns their lifecycle for one command.

## Service-independent enforcement

The gateway does not construct GitHub calls or interpret MCP tool arguments. [Adapters](adapters.md) do that. The core owns:

- Strict policy compilation and approval rendering.
- Persistent, atomic counter reservations.
- Stable request IDs and recorded results.
- Expiration, revocation, and audit records.
- The worker-to-operator inbox.

A modular policy selects services, each with an adapter and public scope. Capability keys are `service.operation`. Private connection settings come from a separate operator file. The old single-GitHub policy is accepted for compatibility and routed through the GitHub adapter.

All interfaces reach the same checks. A lease has one gateway and one Unix socket; access to that socket grants its worker permissions. There is no signed token or offline authority.

## One operation

1. Validate the request envelope and adapter-specific parameters.
2. In one SQLite transaction, check the lease, request ID, service-scoped ownership, and all budgets. Reserve the budgets and record the attempt as pending.
3. Record authorization to enter the adapter. The adapter rechecks before each external dispatch.
4. Save the result and any newly owned resource. Repeated requests return that result without dispatching again.

| Counter | Unit |
| --- | --- |
| Each capability | One admitted attempt |
| `operations` | Any admitted operation |
| `write_operations` | An attempt the approved adapter definition classifies as a write |
| `payload_bytes` | Canonical JSON bytes of admitted input parameters |

These count operations, not every downstream effect. A GitHub publish includes three writes. One MCP tool can perform multiple effects. The byte budget covers inputs, not wire traffic, responses, or total exfiltration. Control messages and replayed/denied operations do not consume task counters.

A changed request under an existing ID is refused. Failures keep reservations. Recovery marks unfinished operations uncertain and never replays them. The operator must reconcile uncertain effects before issuing new work. Reads also use recorded results: choose a new ID for an intentional fresh read of changing service data.

## Questions and extensions

Workers can submit a question or ask to raise one existing cap. SQLite stores the request, then the worker polls for the answer. Submission grants nothing. There are at most three new requests per hour; IDs are idempotent and messages are limited to 2,000 characters.

Operator commands read private state directly. `decide` displays a budget diff and the agent's words separately. Approval checks the rendered policy hash and commits the grant and answer in one transaction. A partial approval cannot exceed the requested amount. Questions and denials never grant authority. Resource additions, lifetime extensions, and automatic queued effects are not implemented.

The inbox is independent of operation execution, so a slow operation does not block questions and an exhausted task budget does not prevent a request. Expiration/revocation closes pending requests.

## Lifecycle and revocation

`launch` approves a new lease, starts the gateway, starts bubblewrap, and watches the worker. Worker exit or interruption revokes the lease and closes the gateway and upstream MCP processes. Expiration or operator revocation also causes the supervisor to stop the worker. State and workspace files remain for inspection.

`approve` / `serve` / `run` expose the same pieces separately. In that mode, the operator owns their lifecycle: revocation blocks subsequent dispatch, but does not kill independently attached workers.

Revocation is checked at dispatch authorization. A request authorized just before revocation can still complete. Neither revocation nor killing an MCP process recalls remote work already accepted by a service.

## State and transport

SQLite stores policy, counters, ownership, results, the inbox, and events. Results and inbox messages may contain sensitive data. Protect the entire state directory and never restore a stale database as active authority.

Events omit argument and answer bodies and include hashes. The local hash chain detects altered records relative to its saved history; it is not immutable or externally anchored. The server takes an exclusive lock before restart recovery.

The worker protocol uses one JSON line per socket connection, at most 1 MiB. Up to eight connections can be active, with one executing adapter operation at a time. Denials include stable machine-readable reasons. CLI and MCP use the same protocol and derive default IDs from operation and parameters.

The MCP frontend discovers approved operations from the gateway. It does not maintain an ecosystem-specific tool list. The MCP service adapter is a separate component: it talks to a trusted upstream server outside the worker.
