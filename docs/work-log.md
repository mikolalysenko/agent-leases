# Work log

## October 27, 2026 — first concrete MVP

Reviewed the supplied Agent Leases v0.2 specification and wrote [the design review](spec-review.md). The implementation keeps the externally enforced lease boundary and narrows the integration surface to one repository and four explicit GitHub operations.

Implemented:

- Strict JSON policy validation, deterministic approval text, and a policy hash.
- A single SQLite authority with atomic reservations, persisted idempotency, conservative failure accounting, ownership records, and a local audit hash chain.
- A Unix-socket worker API for file reads, new-branch publishing, draft PR creation, and comments on this lease's PRs.
- GitHub App JWT signing and one-repo, per-permission token caching/refresh; an explicitly configured token-file fallback.
- Separate operator CLI commands for approval, one-cap grants, revocation, and audit inspection.
- A Linux bubblewrap launcher that exposes the socket and a clean workspace with no network, credential environment, host home, or authority DB.
- A credential-free demo, adversarial gateway/provider tests, and Linux sandbox CI.

Decisions:

- **Explicit operations first.** No transparent TLS interception, native Git pack parser, GraphQL, package firewall, or service-pack framework.
- **Conservative budgets.** Admission is the charged event. Failures, crashes, and ambiguous outcomes keep their reservations; no automatic retry or refund.
- **Fresh branches only.** Publishing creates a branch from an approved SHA. It never updates an existing ref, so force pushes are not an available action.
- **Precise metrics.** Logical operation counts and canonical input bytes are implemented. Raw network bytes, dollars, and CPU limits are not advertised.
- **Operator grants only.** An increased cap is a separate local approval. There is no delegation, autonomous authority expansion, or automatic queued execution.
- **Explicit CI prerequisite.** The prototype requires a disposable repository without privileged automation. Blocking `.github` edits is additional protection, not sufficient isolation.
- **Linux execution only.** macOS can run the gateway and client for development but cannot claim the worker sandbox boundary.

Verification:

- macOS, Python 3.9.6 and 3.14.8: 27 tests passed; the one Linux integration test was correctly skipped.
- [Ubuntu 24.04 CI](https://github.com/mikolalysenko/agent-leases/actions/runs/40616732020), implementation commit `6755f13`: all 28 tests passed, including real bubblewrap TCP/UDP/IPv6/DNS isolation, hidden host files/environment, socket operation restrictions, and alternate-socket rejection. The required sandbox test was not skipped.
- The credential-free demo completed in local runs and CI.
- A built wheel installed in a fresh virtual environment. The installed CLI successfully approved a policy, granted one cap, revoked the lease, and verified its audit chain. CI also built/installed the package and checked the entry point.
- A live, read-only GitHub smoke check used an explicitly supplied operator credential in a temporary private token file. The gateway read `README.md` at the committed SHA and matched the local file exactly. After revocation, the next read returned `revoked`. No remote writes were issued by that check, and its token/state directory was removed afterward.
- `git diff --check` passed. Source and fixtures were checked for credential material before the public push.

Repository: [mikolalysenko/agent-leases](https://github.com/mikolalysenko/agent-leases). Local checkout: `agent-leases` in the requested Projects workspace.

Live GitHub App installation token minting requires an operator-owned installed App and key; those were not provisioned. Tests verify actual JWT signatures plus mocked issuance, scoping, refresh, and operation construction. Live publish/PR/comment calls were not exercised against GitHub; their exact request sequences and failure cases were tested against the recording provider. The offline demo is explicitly simulated.
