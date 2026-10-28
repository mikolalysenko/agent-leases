# Agent Leases

**Approve a task budget. Let the agent work within it.**

The next design separates the lease from its runtime: [portable leases and an OpenShell integration proposal](docs/portable-leases.md). It is a proposal; the implementation below remains the working prototype.

Give a command permission to do a small job: read these files, publish one change, or call an MCP tool five times. A gateway enforces that budget and keeps credentials outside the command. The agent can ask for more, but only you can approve it.

```text
You approve a lease
        │
        ▼
Sandboxed command ── lease socket ── Gateway ── Service adapters
  agent + its children               budgets    GitHub, MCP, your plugin
  no direct network                  inbox      credentials stay here
```

The sandbox covers the whole process tree. `gh`, `curl`, a script, and an MCP subprocess all have the same restriction: no direct network access. Approved operations go through the gateway.

## Run the complete example

The [sandbox walkthrough](docs/quickstart.md) starts a gateway and an isolated worker with one command:

```sh
lease launch examples/mcp-lease.json \
  --config gateway.json --workspace work --state .lease \
  -- python3 worker.py
```

The example uses a small MCP notes service. The worker asks what to write, saves one note, hits its budget, then asks for one more. You answer from another terminal:

```sh
lease inbox
lease answer note-text "Hello from the operator"
lease decide one-more approve
```

`launch` shows the policy for approval, starts the gateway, runs the command in Linux bubblewrap, and closes the lease when the command finishes. Revoking or expiring the lease stops the supervised worker too. Use a fresh state directory for each run.

Linux, Python 3.9+, and bubblewrap are required for isolation. The gateway has no Python runtime dependencies. For a simulated introduction that also runs on macOS:

```sh
python3 -m agent_leases demo
```

## Use it with your own command

Replace `python3 worker.py` with your agent or command. Put its task files in the workspace. Inside the sandbox it can use:

```sh
python3 -m agent_leases status --json
python3 -m agent_leases catalog
python3 -m agent_leases call notes.add --params '{"text":"Done"}'
python3 -m agent_leases ask "Which test suite should I run?" --id tests
python3 -m agent_leases request notes.add 1 --reason "One more note" --id more
python3 -m agent_leases wait more
```

Agents that use MCP can start `python3 -m agent_leases mcp` inside the sandbox. It exposes the approved operations plus status, question, request, and polling tools. Neither interface exposes approval.

Native `gh` and cloud-model clients do **not** gain transparent network access. Use lease operations for permitted service calls. A networked agent also needs an explicit integration for its model; this version does not provide a model proxy. See [agent setup](docs/agents.md).

## Add a service

The core handles approval, counters, request IDs, revocation, and the inbox. Adapters handle service behavior:

- **GitHub:** pinned file reads, new lease branches, draft PRs, and comments on PRs created by the lease.
- **MCP:** explicit bindings to a trusted stdio server's tools, resources, prompts, or other request methods. Each binding fixes the method and selected parameters, with its own cap.
- **Plugins:** an installed Python adapter can add another protocol without changing the gateway, store, CLI, or MCP frontend.

One lease can combine services. Worker permissions live in the policy; server commands and credential settings live in a separate operator configuration. [Adapter guide and examples](docs/adapters.md).

## What the budget means

Counters reserve **attempts** before dispatch. Failed or uncertain attempts remain charged. Repeating the same request ID returns its recorded result. The combined limits cover operation attempts, declared writes, input bytes, and elapsed time.

A service can cause more than one effect per call. MCP servers and adapters run on the trusted side: their implementation must honor the scope you approved. Revocation stops new dispatches; it cannot recall an operation already sent.

This is an experimental single-host MVP. It does not meter dollars, manage subagents, inspect intent, or make approved writes safe. [Security limits](docs/security.md) · [Tests](docs/testing.md) · [Design](docs/design.md) · [Spec review](docs/spec-review.md) · [Work log](docs/work-log.md).

MIT licensed.
