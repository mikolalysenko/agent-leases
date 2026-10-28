# Agent and command interface

Launch the whole agent with [the sandbox workflow](quickstart.md). Inside it, `python3 -m agent_leases` is available and `LEASE_SOCKET` is already set. The host-side `lease` executable may live in a virtual environment that is not mounted inside the sandbox.

## Discover and act

```sh
python3 -m agent_leases status --json
python3 -m agent_leases catalog
python3 -m agent_leases call notes.add --params '{"text":"Finished the tests"}'
```

The catalog comes from the approved adapters. `call` works with every registered operation and always returns JSON. `--params-file FILE` reads larger arguments; `--params-file -` reads stdin.

```json
{"ok":true,"result":{"content":[{"type":"text","text":"Note saved."}]}}
```

A refusal has `ok: false`, `error.code`, `error.rule`, and `retryable: false`. A budget refusal also includes `used`, `left`, and `needed`. The CLI exits with status 1 on refusal.

For the original GitHub policy, convenience commands remain available: `read`, `publish`, `pr`, and `comment`. In a modular policy, use names such as `github.read_file` or `github.open_pr` from the catalog. [GitHub setup](adapters.md#github).

## Ask the operator

The control channel remains available when an operation budget is exhausted:

```sh
python3 -m agent_leases ask "Which test suite should I run?" --id tests --json
python3 -m agent_leases wait tests --poll --json
```

`--poll` checks once. Without it, `wait` polls until the operator answers or the lease closes. The answer is data, not a grant:

```json
{"ok":true,"result":{"id":"tests","state":"answered","request":{"id":"tests","kind":"ask","message":"Which test suite should I run?"},"answer":{"decision":"answer","message":"Run unit tests","granted":0}}}
```

To request more of a specific budget:

```sh
python3 -m agent_leases request notes.add 1 --reason "Need one more note" --id more --json
python3 -m agent_leases wait more --json
```

The operator uses `lease inbox`, `lease answer`, or `lease decide` outside the sandbox. A request can be approved in full, approved for fewer units, or denied. It never changes other caps, service scope, or expiration. Check `answer.decision` and `answer.granted` before continuing. A closed lease ends pending requests.

There are at most three new questions or budget requests per hour. Repeating an identical request ID returns the existing request without consuming another slot. An already-denied operation must be explicitly retried after approval; the gateway does not queue automatic effects.

## MCP

Start this stdio server **inside the sandbox**, as a child of the agent:

```json
{
  "mcpServers": {
    "leases": {
      "command": "python3",
      "args": ["-m", "agent_leases", "mcp"]
    }
  }
}
```

The frontend exposes four control tools:

| Tool | Arguments | Result |
| --- | --- | --- |
| `lease_status` | none | Policy, remaining budgets, time |
| `lease_ask` | `message`, optional `request_id` | A question ID |
| `lease_request` | `cap`, `amount`, `message`, optional `request_id` | A budget request ID |
| `lease_poll` | `id` | Pending state or operator answer |

It also generates a tool for each permitted service operation. For example:

```json
{"name":"lease_notes.add","arguments":{"params":{"text":"Done"},"request_id":"note-1"}}
```

Modular service tools wrap adapter arguments under `params`; this keeps upstream fields separate from the lease's `request_id`. The original GitHub tools keep their flat argument format for compatibility. Tool results include JSON text and `structuredContent`; refusals set `isError: true`.

The frontend never exposes grant, approval, revoke, arbitrary server selection, or raw network access. MCP is an interface to the gateway, not a sandbox for an agent running on the host.

## Retries and refusals

CLI and MCP derive stable default IDs from the operation and parameters. Repeating them returns the recorded result without another dispatch. Use CLI `--id` or MCP `request_id` only for a deliberate new attempt. Raw socket callers must supply their own ID.

| Code | Next step |
| --- | --- |
| `limit` | Request the missing cap; continue other permitted work. |
| `scope`, `invalid`, `unknown_operation` | Correct the request or stay within the approved scope. |
| `conflict` | The ID has different contents, or the reviewed policy changed. Inspect before continuing. |
| `upstream` | The service rejected the operation. The attempt remains charged. |
| `uncertain` | An effect may have happened. Ask the operator to inspect it; do not create a fresh ID to retry. |
| `expired`, `revoked` | Stop using the lease. |
| `unavailable` | Inspect the gateway. Keep the same operation ID when checking again. |

## Raw socket

One JSON line per connection, at most 1 MiB:

```json
{"id":"note-1","operation":"notes.add","params":{"text":"Done"}}
```

Control messages:

```json
{"operation":"status"}
{"operation":"catalog"}
{"operation":"submit","request":{"id":"q","kind":"ask","message":"Which tests?"}}
{"operation":"submit","request":{"id":"more","kind":"more","cap":"notes.add","amount":1,"message":"One more note"}}
{"operation":"poll","request_id":"more"}
```

The socket identifies one lease; there is no bearer token or signed token to manage. Possession of the socket grants that lease's worker permissions.
