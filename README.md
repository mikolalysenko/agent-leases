# Agent Leases

Approve a small GitHub task once. A gateway holds the credentials, counts operations before sending them, and refuses work outside the lease.

This is a concrete, deliberately narrow implementation of the [v0.2 spec review](docs/spec-review.md): **one repository, four operations, one worker sandbox**. Python 3.9+, no runtime packages. The gateway and CLI run on macOS or Linux; worker isolation requires Linux and bubblewrap.

| Operation | Enforced behavior |
| --- | --- |
| `read_file` | Read one UTF-8 file at the approved commit SHA |
| `publish` | Create a commit and a fresh `lease/<lease-id>/<request-id>` branch |
| `open_pr` | Open a draft PR from a branch created by this lease into the approved base |
| `comment` | Comment only on a PR created by this lease |

Each operation has a cap. The lease also caps total operations, total write operations, canonical input bytes, and its lifetime. SQLite reserves all applicable counters atomically before dispatch. A stable request ID prevents the gateway from replaying the same attempt after a timeout or restart. The operator can raise exactly one cap or revoke the lease while the gateway runs.

The worker gets a Unix socket, not a GitHub token. On Linux, `lease run` exposes that socket and a clean workspace in a bubblewrap sandbox with no network. The socket accepts only these four operations and status. There is no administrative API, arbitrary HTTP forwarding, Git/GraphQL parser, or shell-command policy.

**Prototype boundary:** use a disposable repository with Actions disabled and no privileged webhooks or integrations. The gateway does not enforce that repository configuration. A draft PR or branch can trigger external automation. There is no model connection, dollar cap, delegation, taint tracking, or production security certification. This does not yet run an unmodified cloud-backed coding agent. See [security assumptions](docs/security.md) and [exact accounting semantics](docs/design.md).

## Try it without credentials

From this checkout:

```sh
python3 -m unittest discover -v
python3 -m examples.demo
```

The demo uses a recording fake GitHub provider. It publishes once, replays that request without another write, hits a cap, continues a read, opens a draft PR, receives a narrow operator grant, and gets denied after revocation. It makes no network calls. Linux CI additionally exercises the real sandbox with TCP, UDP, IPv6, DNS, hidden-file, environment, and alternate-socket probes.

For the `lease` executable:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/lease --help
```

All commands below also work as `python3 -m agent_leases ...` from the checkout.

## Use a real repository

1. Create a disposable GitHub repository with an initial commit. Disable Actions and remove privileged integrations. Install a GitHub App on **only that repository**, with Contents: read/write and Pull requests: read/write. Keep its private key outside every worker workspace, mode `0600`. Do not grant Workflows, Administration, or a ruleset bypass.
2. Copy [examples/lease.json](examples/lease.json). Set `repo`, the base branch, and `base_sha` to the full SHA of a reviewed commit. The SHA pins reads and the parent of new commits; the PR base branch itself may move. Review the caps. The example SHA is a placeholder.
3. Approve and start the gateway from a trusted operator terminal:

```sh
lease approve my-lease.json --state /tmp/my-lease

lease serve --state /tmp/my-lease \
  --app-id 123456 --installation-id 789012 \
  --private-key /private/operator/github-app.pem
```

The state directory must not already exist. Approval prints the compiled policy and its hash, including CI and disclosure risks. `--yes` is available for a trusted operator's automation; it is never exposed on the socket. Use a private persistent directory instead of `/tmp` for real ongoing work.

The broker signs App JWTs with `openssl`, mints installation tokens for the one repository and the current operation's permissions, and refreshes them before expiry. An existing installation token or fine-grained PAT can instead be supplied through `--token-file /private/operator/token`, mode `0600`. In that mode **you** control scope, expiry, and rotation; the gateway does not silently use `gh`, `GITHUB_TOKEN`, or ambient credentials.

From another trusted operator terminal on Linux, create an empty workspace and launch a worker:

```sh
mkdir /tmp/lease-work
lease run --socket /tmp/my-lease/gateway.sock --workspace /tmp/lease-work -- \
  python3 -m agent_leases call read_file --id read-1 \
  --params '{"path":"README.md"}'
```

The same launcher can run a script that edits files and issues several requests. Inside the sandbox, `python3 -m agent_leases` is available and `LEASE_SOCKET` is set automatically. There is no general network or package installation. Local tools and preinstalled dependencies are available under `/usr`.

To publish, copy [examples/change.json](examples/change.json) into the workspace as `change.json` and run:

```sh
lease run --socket /tmp/my-lease/gateway.sock --workspace /tmp/lease-work -- \
  python3 -m agent_leases call publish --id change-1 --params-file change.json
```

Use the returned `branch` in an `open_pr` request with `branch`, `title`, and `body`. Use its returned `number` in a `comment` request with `number` and `body`. [The protocol reference](docs/design.md#worker-protocol) includes full examples. `publish` supports 1–50 text files as regular `100644` files; deletions, executable modes, symlinks, submodules, binary files, and `.github` writes are deliberately unsupported.

Direct `lease call --socket ...` is useful for development on macOS, but a process launched directly on the host is **not sandboxed**. Only the Linux launcher establishes the worker boundary.

## Inspect, extend, revoke

```sh
lease status --socket /tmp/my-lease/gateway.sock
lease grant --state /tmp/my-lease --cap comment --add 1
lease revoke --state /tmp/my-lease
lease audit --state /tmp/my-lease
lease audit --state /tmp/my-lease --verify
```

A grant displays one exact diff and leaves other caps and expiration untouched. Raising `comment` does not also raise `write_operations`. Expired or revoked leases cannot be extended. To change the repo, branch, or lifetime, approve a new lease.

Revoke stops future **dispatch authorizations**, including the remaining steps of a multi-step publish. A request already dispatched may still succeed remotely. Failed or ambiguous attempts retain their budget; the gateway never retries them. Reuse the same request ID to retrieve its recorded result. Do not invent a new ID to retry an uncertain write until a person has inspected GitHub.

## Read more

- [Spec review](docs/spec-review.md): what to fix before broadening v0.2.
- [Implementation design](docs/design.md): schema, protocol, lifecycle, and counter semantics.
- [Security model](docs/security.md): trusted components and explicit limitations.
- [Work log](docs/work-log.md): implementation choices and verification evidence.

MIT licensed. This repository is a starting point for testing the lease boundary, not an implementation of every v0.2 feature.
