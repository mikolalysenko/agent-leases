# Security limits

The lease limits a worker's GitHub actions. It does not establish that code is correct or stop disclosure through an action you approved.

## Conditions for isolation

- Run the **whole worker** through `lease run` on Linux with bubblewrap. Direct host commands and an MCP server alone do not isolate the parent agent. macOS has no sandbox fallback.
- Keep the token and state outside the worker directory. Use a repo-scoped token in an operator-owned private file. Do not run the gateway as root.
- Use a disposable repo with Actions disabled and no privileged integrations. Branches and draft PRs can trigger CI, webhooks, or later automation. The gateway does not enforce this repo configuration.
- Start from an empty, private workspace. The launcher rejects existing symlinks, hard links, sockets, devices, and FIFOs. Do not let other host processes change its mounts or add sensitive files/sockets during a run.
- Trust the host clock, database, kernel, bubblewrap, package, and system directories mounted read-only. Do not put credentials or service sockets in those system directories. Do not roll back a live lease database.

The sandbox clears the environment, hides the host home and state, closes inherited descriptors, detaches stdin, and creates separate mount, process, IPC, and network namespaces. The worker keeps its workspace, temporary directory, system tools, stdout/stderr, and the lease socket.

## What is enforced

The worker cannot choose a different repo, overwrite a branch, merge, approve, or make arbitrary API calls. Per-operation and aggregate counters are reserved atomically. Repeated request IDs cannot cause another dispatch. Only locally recorded successful branches and PRs can be used by later write operations. Revocation and expiration stop subsequent dispatch authorizations.

These guarantees depend on the gateway and sandbox remaining intact. A host process outside the sandbox may have its own network and credentials.

## What remains possible

**Authorized disclosure.** A worker can put any data it has read into a permitted file, PR, or comment. Budgets bound attempts and input size, not the sensitivity or value of that data. There is no taint analysis or semantic purpose enforcement.

**External automation.** CI, notifications, webhooks, and later consumers of published code are outside the counters. Other GitHub users can change the base or a created branch. Repository names can be reassigned; operators must not rename or transfer the repo during a lease.

**Partial work.** A timed-out write may have succeeded. Revocation cannot recall it or cancel already-triggered automation. Reservations remain spent. A person must reconcile uncertain outcomes.

**Host compromise and resource exhaustion.** This shares the host kernel, has no dedicated seccomp profile or CPU/disk/process quotas, and is not a multi-tenant service. A worker can exhaust resources or spam the log. A compromised gateway, token file, operator, or kernel defeats the boundary.

**Editable evidence.** Logs are local. Their hash chain cannot detect a fully rewritten or truncated history without an external checkpoint. Public `Lease:` stamps are attribution hints, not authenticated provenance.

See [testing](testing.md) for the actual interactions and boundary probes covered. Passing those tests is evidence for those cases, not a sandbox certification.
