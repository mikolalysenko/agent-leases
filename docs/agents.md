# Using a lease from an agent

Start with `lease status --json`. It shows the repo, expiration, and each remaining budget. Use the worker commands for the task. Approval, grants, and revocation belong to the operator.

## CLI

```sh
lease read README.md --json
lease publish test.py --message "Fix test" --json
lease pr RETURNED_BRANCH --title "Fix test" --body "Checked locally" --json
lease comment RETURNED_PR_NUMBER "Tests pass" --json
```

`publish` reads local text files. Run it from the worker workspace with relative file names. Use the returned branch for `pr`, then the returned number for `comment`.

Inside `lease run`, invoke `python3 -m agent_leases` in place of `lease`; the package and socket are provided automatically. Outside it, set `LEASE_SOCKET` or pass `--socket PATH` if the default `.lease/gateway.sock` is elsewhere.

For programmatic file contents, use the same operation through JSON:

```sh
lease call publish --params '{"files":{"test.txt":"fixed\n"},"message":"Fix test"}'
```

All worker operations return the same JSON shape with `--json` (and always for `call`):

```json
{"ok":true,"result":{"branch":"lease/ID/REQUEST_ID","sha":"..."}}
```

A refusal has `ok: false`, `error.code`, `error.rule`, and `retryable: false`. A budget refusal also includes `used`, `left`, `needed`, and a suggested next step. The process exits with status 1 on refusal.

## MCP

Configure a stdio server in your MCP client:

```json
{
  "mcpServers": {
    "leases": {
      "command": "lease",
      "args": ["mcp", "--socket", "/absolute/path/.lease/gateway.sock"]
    }
  }
}
```

The server exposes `lease_status`, `lease_read_file`, `lease_publish`, `lease_open_pr`, and `lease_comment`, with parameter schemas and descriptions. Tool results carry both JSON text and `structuredContent`; refusals set `isError: true`. No token is given to the MCP client.

The MCP server is a socket client, not a sandbox for its parent agent. For isolation, launch the whole worker with `lease run` and start MCP as a child inside it. The worker still needs a local model or an integration supplying its work; this project does not provide model network access.

## Retries and refusals

Identical operation parameters get the same request ID across CLI and MCP. Repeating them returns the recorded result without another GitHub call. Raw socket callers must supply an ID. For an intentional new attempt, use CLI `--id` or MCP `request_id`. Never generate a fresh ID just to retry an uncertain write.

| Code | Next step |
| --- | --- |
| `limit` | Ask the operator for the specific missing budget. Other available work can continue. |
| `scope`, `invalid`, `unknown_operation` | Correct the request or stay within the approved task. |
| `conflict` | An explicit request ID was reused with different parameters; inspect the original request. |
| `upstream` | GitHub rejected a request. The attempt is charged; inspect the HTTP status and any earlier steps. |
| `uncertain` | A write may have happened. Stop that action and ask the operator to inspect GitHub. |
| `expired`, `revoked` | Stop using the lease. Only the operator can approve a new one. |
| `unavailable` | The gateway or its state could not be reached. Keep the same request ID when checking again. |

The same rules apply when using a shell, a subprocess, or another agent tool. None can extend the lease through the worker socket.
