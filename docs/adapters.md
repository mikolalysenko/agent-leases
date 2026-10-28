# Service adapters

A lease can cover several services. The gateway handles budgets and lifecycle; each adapter defines its operations, validates their scope, and performs them. The worker cannot choose a server process, URL, or credential unless an approved adapter explicitly grants that choice.

## Two files

The **policy** is the document you approve. It contains public scope and budgets:

```json
{
  "version": "mvp-2",
  "purpose": "Save a note about the tests",
  "ttl_seconds": 3600,
  "services": {
    "notes": {
      "adapter": "mcp",
      "operations": {
        "add": {
          "method": "tools/call",
          "params": {"name": "add_note", "arguments": {"notebook": "demo"}},
          "input": "arguments",
          "write": true
        }
      }
    }
  },
  "capabilities": {"notes.add": 2},
  "limits": {"operations": 2, "write_operations": 2, "payload_bytes": 4096}
}
```

The **gateway configuration** maps service names to operator-controlled connections:

```json
{
  "notes": {
    "command": ["/usr/bin/python3", "/opt/trusted/notes_server.py", "/var/lib/notes"]
  }
}
```

Save it outside the worker workspace with mode `0600`. Pass it to `lease launch` or `lease serve` with `--config`. Configuring a server is an operator trust decision: it runs on the gateway side and can access the host. Keep the server, its dependencies, working directory, and credentials outside worker-writable locations. The gateway configuration is not exposed in worker status or discovery.

Service names are local aliases. `notes.add` means operation `add` on the approved `notes` connection. You can add another service and its caps to the same policy; aggregate counters cover them all. Unknown adapters, operation names, and policy fields fail closed.

## MCP bindings

The MCP adapter can bind **request methods**, not just GitHub or `tools/call`. For example:

| Operation | Method | Fixed parameters | Worker input |
| --- | --- | --- | --- |
| `notes.add` | `tools/call` | tool name and notebook | Other `arguments` fields |
| `notes.guide` | `resources/read` | `uri: notes://guide` | None |
| `notes.prompt` | `prompts/get` | prompt name | `arguments` |

The complete [MCP example](../examples/mcp-lease.json) contains all three.

Every binding explicitly supplies `method`, `params`, `input`, and `write`:

- `params` fixes the method's parameters. Put resource identifiers, tenant IDs, tool names, and other scope constraints here.
- `input: "arguments"` accepts an object of additional fields in `params.arguments`.
- `input: ""` accepts additional top-level parameters.
- `input: null` accepts no worker parameters.
- Fixed keys cannot be replaced, even with the same value. All other fields in an open input slot are unrestricted. The approval rendering says this explicitly.
- `write` determines whether the operation spends the combined write budget. It is a reviewed declaration, not something inferred from a method name or server annotation. Treat an operation as a write unless you trust its read semantics.

For example, `lease call notes.add --params '{"text":"Done"}'` sends:

```json
{"method":"tools/call","params":{"name":"add_note","arguments":{"notebook":"demo","text":"Done"}}}
```

Trying to supply `notebook` is refused before dispatch. The generic adapter does not validate the meaning of `text`, interpret tool-specific URLs, or stop a tool from ignoring its notebook argument. For richer restrictions, write a service adapter or expose a narrower trusted MCP tool. Never use unrestricted shell, fetch, or proxy tools as a scoped service boundary.

### Transport and lifecycle

The gateway starts one stdio server per MCP service, lazily on the first admitted call. An absolute executable path is required. Optional `cwd` and `env` are operator configuration; the process receives only a minimal `PATH` plus explicit `env` values. Credentials are not inherited automatically from the operator environment. Do not put secrets in policy parameters or command arguments returned by your server.

The gateway performs MCP initialization, reuses the connection, bounds message size and time, and never retries an uncertain call. A broken connection remains broken until the operator restarts the gateway. Server errors remain charged. Server stderr is discarded to avoid accidental credential logging.

The adapter currently supports MCP `2025-11-25` over stdio. It does not implement Streamable HTTP, server-to-worker notifications, subscriptions, task streaming, sampling, elicitation, or arbitrary client callbacks. It advertises no callback capabilities and refuses callback requests (apart from protocol pings). Client lifecycle and notification methods cannot be leased. Request-based extensions can be explicitly bound, but asynchronous effects remain the trusted server's responsibility.

This is a modular route for MCP operations, not a transparent implementation of every MCP feature. A new transport or mediated callback needs an adapter implementation and its own tests. Protocol references: [stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), [lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle), [tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

## GitHub

The GitHub adapter keeps the original four operations. A modular policy's service entry looks like:

```json
{
  "github": {
    "adapter": "github",
    "repo": "OWNER/REPO",
    "base": "main",
    "base_sha": "REVIEWED_40_CHARACTER_COMMIT_SHA"
  }
}
```

Grant caps under `github.read_file`, `github.publish`, `github.open_pr`, and `github.comment`. Supply the connection separately:

```json
{"github":{"token_file":"/private/path/github-token"}}
```

The token file must be owned by the operator and have mode `0600`. Use a token scoped to the approved repository, with Contents and Pull requests write permissions only as needed. Use a disposable repository with Actions disabled and no privileged integrations: branch creation and PRs can trigger effects outside the gateway.

| Operation | Scope enforced by the adapter |
| --- | --- |
| `read_file` | Ordinary UTF-8 files at the approved commit; up to 256 KiB |
| `publish` | 1–50 text files; a fresh `lease/ID/REQUEST_ID` branch from the approved parent |
| `open_pr` | Draft PR into the approved base, from a branch created by this service in this lease |
| `comment` | A PR created by this service in this lease |

Publishing forbids `.github` edits, deletion, symlinks, submodules, and executable modes. These restrictions do not make CI safe. There is no merge, review, arbitrary HTTP, GraphQL, or native git push operation.

For a single GitHub service, the original `mvp-1` format remains supported:

```sh
lease init OWNER/REPO --commit REVIEWED_COMMIT_SHA > github-policy.json
lease launch github-policy.json --token-file /private/path/github-token \
  --workspace work --state .lease-github -- python3 my_agent.py
```

That format retains the short operation names and `read`, `publish`, `pr`, and `comment` CLI shortcuts. Its enforcement now uses the same GitHub adapter as modular policies.

## Write an adapter

Install trusted Python code with an entry point:

```toml
[project.entry-points."agent_leases.adapters"]
myservice = "my_package.lease_adapter:Adapter"
```

The policy can name `"adapter": "myservice"` only after the operator installs it. Policies cannot supply import paths or source code. The adapter class implements:

| Method | Contract |
| --- | --- |
| `compile(options)` | Validate public service options; reject unknown or unsafe fields. |
| `operations(options)` | Return operation name → `description`, `inputSchema`, and boolean `write`. |
| `describe(options)` | Return approval text derived from the validated scope. Escape untrusted text. |
| `validate(options, operation, params)` | Reject invalid/out-of-scope input. Optionally return `(kind, resource)` that must already be owned. No side effects. |
| `__init__(options, config)` | Accept operator runtime settings. Do not perform task work here. |
| `execute(context, operation, params)` | Perform the operation; return `(result, newly_owned_or_none)`. |
| `close()` | Release connections and child processes. |

The core validates, reserves all counters, and records dispatch authorization before calling `execute`. Before **each** external call, the adapter must also call `context.dispatch(method, resource)` so revocation can stop later steps. The context provides `request`, `lease`, and `service`. Ownership is scoped to the service alias.

Adapters are trusted code, not sandboxed plugins. A malicious adapter can bypass the gateway's checks because it runs with gateway authority. Review and test it accordingly. The bundled [GitHub adapter](../agent_leases/adapters/github.py) and [MCP adapter](../agent_leases/adapters/mcp.py) are small examples; the plugin test proves that an installed adapter composes with another service without changing the core.
