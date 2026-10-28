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

Local verification on macOS passed the Python 3.9 and Python 3.14 gateway suite. The suite was then expanded to include actual OpenSSL signature verification, token refresh, fixed-host HTTPS behavior, and Linux boundary tests. Linux verification and final repository checks are recorded below after CI.

Live GitHub App installation token minting requires an operator-owned installed App and key; those are not provisioned by this repository. Unit tests verify the signing, scoping, refresh, and request construction without real credentials. The offline demo is explicitly simulated.
