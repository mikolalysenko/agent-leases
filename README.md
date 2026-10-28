# Agent Leases

**Give an agent a GitHub task budget. Keep control of what it can do.**

Approve a task such as: “Read this repo, publish one change, and open one draft PR within an hour.” The agent can work within that budget without asking about every action. When it runs out, you can add more. When you revoke the lease, new requests stop.

A small gateway holds your GitHub token and performs the allowed operations. The worker receives a socket to that gateway. The limits live outside the agent, so changing its prompt or running another script does not increase its budget.

## Try it

Python 3.9 or newer. No runtime dependencies or credentials needed for the demo:

```sh
python3 -m agent_leases demo
```

The demo walks through approval, a successful publish, a refused extra publish, a small budget increase, and revocation. GitHub is simulated; nothing is uploaded.

## Four things a worker can do

```sh
lease read README.md
lease publish test.py --message "Fix the flaky test"
lease pr BRANCH --title "Fix the flaky test"
lease comment PR_NUMBER "Tests pass"
```

Publishing creates a fresh lease branch. PRs are always drafts. Comments can only go on PRs this lease created. There is no merge, approval, force-push, or arbitrary HTTP operation.

Identical commands return their recorded result. Repeating a command after a lost response does not spend another budget unit or create another write. Use `--id new-name` only when you deliberately want a new attempt.

## Set it up

Install the command from this checkout:

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
. .venv/bin/activate
```

Save a GitHub token in a private file outside the worker's directory (`chmod 600 TOKEN_FILE`). Scope it to one disposable repository with Contents and Pull requests write access. Disable Actions and privileged integrations on that repo: a branch or PR can otherwise run code outside the lease.

Create a policy using a reviewed commit's full SHA, inspect it, and approve it:

```sh
lease init OWNER/REPO --commit COMMIT_SHA > policy.json
lease approve policy.json
lease serve --token-file /private/path/github-token
```

The starter policy grants one publish, one draft PR, two comments, twenty file reads, and one hour. Edit `policy.json` before approval to change those limits. The gateway stores the approved policy and counters in `.lease/`.

In another terminal on Linux, start a worker in an empty directory:

```sh
mkdir work
lease run --workspace work -- python3 -m agent_leases read README.md
```

`lease run` uses bubblewrap to give the worker its workspace and lease socket, with no Internet or access to the token/state files. It can run a local worker script the same way. Cloud-model connectivity is not included. Direct host commands are useful for development, but only the Linux launcher establishes this isolation.

## Stay in control

```sh
lease status             # remaining budget and time
lease grant comment 1    # approve one extra comment
lease revoke             # stop new requests
lease audit              # inspect the log
```

A grant changes one limit. Other limits and the expiration stay in place. Revocation cannot recall requests already sent to GitHub. Failed or uncertain writes keep their budget reservation; inspect GitHub before replacing an uncertain attempt.

## For agents and scripts

Add `--json` to worker commands for structured results. Or run `lease mcp` for five discoverable MCP tools: status and the four operations above. Both use the same gateway and budgets. Neither exposes approval or budget increases.

See [agent setup](docs/agents.md) for MCP configuration, request IDs, and how to handle denials.

## What this version covers

One repo, one lease, four operations, persistent budgets, a token kept outside the worker, and Linux worker isolation. It does not meter dollars, connect a model, manage subagents, or make generated code trustworthy. macOS supports gateway/client development; sandboxed workers require Linux and bubblewrap.

The [permission-matrix suite](docs/testing.md) compares CLI, socket, and MCP behavior, then repeats those interactions inside the Linux sandbox. It observes provider calls as well as returned errors.

[How it works](docs/design.md) · [Security limits](docs/security.md) · [Spec review](docs/spec-review.md) · [Work log](docs/work-log.md)

MIT licensed.
