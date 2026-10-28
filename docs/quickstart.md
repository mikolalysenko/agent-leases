# Run a command with a lease

This walkthrough runs a real gateway, Linux sandbox, and MCP service. It needs no API keys or model. The worker is a short Python command so you can see the permission flow directly.

## 1. Install on Linux

On a Linux host with unprivileged user namespaces enabled:

```sh
sudo apt-get install python3 python3-venv bubblewrap
git clone https://github.com/mikolalysenko/agent-leases.git
cd agent-leases
python3 -m venv .venv
.venv/bin/pip install -e .
. .venv/bin/activate
```

If your host disables user namespaces, ask its administrator to enable an appropriate bubblewrap policy. The launcher fails closed; it does not fall back to running on the host. macOS can run the gateway and tests, but cannot run this sandbox.

## 2. Prepare the worker and service

Keep the worker's files separate from gateway configuration, credentials, state, and trusted server code:

```sh
mkdir -m 700 work notes-data
cp examples/worker.py work/worker.py
python3 - <<'PY'
import json
from pathlib import Path
import sys
root = Path.cwd()
config = {
    "notes": {
        "command": [sys.executable, str(root / "examples/notes_server.py"), str(root / "notes-data")]
    }
}
path = root / "gateway.json"
path.touch(mode=0o600, exist_ok=False)
path.write_text(json.dumps(config, indent=2))
PY
```

`gateway.json` belongs to the operator. It chooses the trusted MCP server to run. The worker cannot see or change this file, the server code, or `notes-data/`.

The [example policy](../examples/mcp-lease.json) permits one note write, two guide reads, and one prompt request for one hour. It pins the `add_note` tool to the `demo` notebook. Review or edit those bounds before launching.

## 3. Approve and launch

```sh
lease launch examples/mcp-lease.json \
  --config gateway.json --workspace work --state .lease \
  -- python3 worker.py
```

Read the rendered policy and type `y`. The launcher:

1. Saves the approved policy and counters in `.lease/`.
2. Starts the gateway and mounts its Unix socket into the sandbox.
3. Runs the command in `/workspace` with `LEASE_SOCKET=/run/lease.sock`.
4. Supervises it until completion, revocation, expiration, or interruption.
5. Revokes the lease, stops the worker, closes the MCP server, and removes the gateway socket.

The worker has no host home, credentials, direct network, or interactive stdin. Its stdout/stderr appear in the launching terminal. It can create files in `work/`. System tools under `/usr`, `/bin`, and the library directories are mounted read-only.

## 4. Answer the worker

In a second terminal, enter the same checkout and activate the environment:

```sh
. .venv/bin/activate
lease inbox --state .lease
lease answer note-text "Hello from the operator" --state .lease
```

The worker saves that note. Its next write is refused because the one-note cap is spent. It submits a request named `one-more` and waits:

```sh
lease inbox --state .lease
lease decide one-more approve --state .lease
```

The approval command renders the exact increase and asks for confirmation. Alternatively:

```sh
lease decide one-more deny --message "One note is enough" --state .lease
```

After approval the worker retries its refused operation. After denial it finishes with one note. Nothing is replayed automatically by the gateway.

Read the saved notes and audit trail:

```sh
cat notes-data/demo.jsonl
lease audit --state .lease --verify
lease audit --state .lease
```

Keep `.lease/` as the record of this run. Use a new `--state`, such as `.lease-next`, for another run. The launcher refuses to overwrite existing state.

## 5. Substitute your agent or command

Use the same launch command with a different command after `--`:

```sh
lease launch POLICY.json --config gateway.json --workspace work --state .lease-next \
  -- python3 my_agent.py
```

Put `my_agent.py` in `work/`, or use a runtime installed in the mounted system directories. Absolute host paths outside those mounts are unavailable. Copy the task files the agent needs into the workspace; do not copy credentials, sockets, or host configuration. Workspaces with symlinks, hard links, or special files are refused.

The whole process tree is isolated, including shell commands, hooks, and worker-started MCP servers. Starting only `lease mcp` beside a host agent does not provide this isolation.

To use a hosted model, the agent needs a gateway integration for model calls, for example an operator-trusted MCP server with an explicitly granted inference tool. There is no transparent model endpoint, domain allowlist, package download path, or native `gh` proxy in this MVP. Unadapted network-dependent commands fail inside the sandbox.

## Manual lifecycle

For a long-lived gateway, separate approval, serving, and attachment:

```sh
lease approve POLICY.json --state .lease-manual
lease serve --state .lease-manual --config gateway.json
# Another terminal:
lease run --socket .lease-manual/gateway.sock --workspace work -- python3 my_agent.py
```

`run` attaches to an existing gateway. It does not revoke the lease when the command exits or terminate it on expiry; the gateway still refuses expired/revoked service calls. Prefer `launch` for one command per lease.

Operator commands do not need the socket:

```sh
lease inbox --state .lease-manual
lease grant notes.add 1 --state .lease-manual
lease revoke --state .lease-manual
```

`grant` raises one existing cap directly. `decide` answers a worker request and applies its grant atomically. Restarting a gateway changes the socket inode; restart attached workers too.
