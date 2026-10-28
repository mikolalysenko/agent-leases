# Security boundary and limits

The boundary applies to a whole command started through `lease launch` or `lease run`. A gateway alone, a tool allowlist, or an MCP frontend attached to a host agent does not isolate that agent.

## Network boundary

Bubblewrap creates a private network namespace with no host network connection. The worker cannot use TCP, UDP, DNS, QUIC, SSH, host loopback, or a proxy on the host. Changing clients does not change that boundary: native `gh`, `git`, `curl`, raw sockets, hooks, and MCP subprocesses are all inside it.

The only service connection mounted into the worker is its lease Unix socket. The gateway accepts structured operations through that socket, validates them, and performs permitted calls through adapters. It does not forward arbitrary HTTP traffic. Direct `gh` calls fail even when the corresponding lease operation would be allowed.

The host gateway and explicitly configured upstream MCP servers are outside this restriction. They are trusted service implementations, not worker subprocesses. An untrusted MCP server must run inside the worker boundary; do not configure it as a trusted gateway backend merely to give it network access.

## Deployment conditions

- Linux and bubblewrap with suitable user namespaces are required. There is no macOS or unsandboxed execution fallback.
- Keep state, configuration, tokens, adapter code, upstream server code and dependencies outside worker-writable paths. Gateway configuration is operator-owned mode `0600`; do not run the gateway as root.
- Use a private workspace containing only ordinary files and directories. The launcher rejects existing symlinks, hard links, sockets, devices, and FIFOs. Other host processes must not change its contents or add sockets/credentials during a run.
- Trust the kernel, clock, SQLite state, bubblewrap, package, adapters, and system directories mounted read-only. Those system directories must not contain sensitive credentials or service sockets.
- For GitHub writes, use a disposable repository with Actions disabled and no privileged integrations. Repository settings are an operator prerequisite, not enforced by this MVP.

The sandbox clears the environment, closes inherited descriptors, detaches stdin, hides host home/state, drops capabilities, and separates mount, process, IPC, and network namespaces. It retains the workspace, temporary files, system tools, stdout/stderr, and the lease socket. A caller outside this sandbox may still have its own credentials and network access.

## What is bounded

Per-operation and aggregate counters reserve attempts atomically. Repeated IDs cannot cause another dispatch. The worker cannot grant itself authority, answer its own questions, or select private connection settings. Revocation and expiration block future dispatch authorizations. GitHub resource ownership is checked per service in the lease.

MCP fixed parameters cannot be replaced. However, a generic binding does not prove what a tool does. Its remaining input slot is unrestricted, and one call may cause many effects. Read/write classification is an explicit reviewed declaration. Use narrow tools and a trusted implementation; add a semantic adapter when you need stronger parameter or resource checks.

## What remains possible

**Authorized disclosure.** An allowed write can contain anything the worker can read. Prompts, questions, arguments, and service results can carry sensitive information. There is no taint analysis, data loss prevention, or semantic purpose enforcement.

**Effects outside a call.** CI, webhooks, notifications, asynchronous MCP tasks, and downstream consumers can continue after a lease ends. A timeout may follow a successful remote write. Inspect uncertain outcomes; reservations remain spent.

**Trusted-component compromise.** A gateway, plugin, upstream MCP server, token, or host compromise defeats its corresponding boundary. MCP initialization and server callbacks are not general sandboxing mechanisms. Server output is trusted to avoid exposing credentials; the gateway does not scrub arbitrary tool results.

**Host resource exhaustion.** This shares the host kernel and has no dedicated seccomp profile or CPU/disk/process quotas. The operator inbox is rate-limited, but a worker can still spam other denials or exhaust resources. This is not a multi-tenant service.

**Editable evidence.** A host operator can rewrite the database and local hash chain. Public `Lease:` stamps are attribution hints, not authenticated provenance. A host crash can leave uncertain attempts and a stale socket; restart recovery does not imply the lease was revoked at the time of the crash.

Tests exercise real Linux isolation, controlled network receivers, and observed service effects. They are evidence for those cases, not a sandbox certification. See [testing](testing.md).
