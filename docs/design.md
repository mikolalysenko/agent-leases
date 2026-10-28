# How it works

There are three pieces:

1. **The operator** approves a policy, adds budget, or revokes it using local commands.
2. **The gateway** keeps the token and SQLite state. It checks requests and constructs GitHub API calls.
3. **The worker** uses CLI, MCP, or the Unix socket. On Linux it runs in a sandbox with that socket as its only service connection.

All worker interfaces reach the same checks. The worker cannot edit the policy, see the token, or call administrative commands through the socket. There is one gateway per lease; access to its socket is access to that lease.

## Policy and counters

`lease init` writes the full policy; [the example](../examples/lease.json) shows its fields. Approval validates every field and displays the resulting permissions. Unknown fields and ambiguous JSON are rejected. `purpose` is an explanation, not a permission.

`repo` selects one GitHub repository. `base_sha` pins reads and the parent of new commits. `base` names the PR target branch, which can move. The operator must choose a reviewed commit and appropriate base. `ttl_seconds` sets a lifetime of at most one day.

| Counter | What consumes it |
| --- | --- |
| `read_file`, `publish`, `open_pr`, `comment` | One admitted attempt of that operation |
| `operations` | Any admitted operation |
| `write_operations` | An admitted publish, PR, or comment |
| `payload_bytes` | Canonical JSON bytes of admitted input parameters |

Counters are reserved together before sending anything. A publish is one logical operation: getting the parent, creating a tree, creating a commit, and creating a new branch. That includes three remote writes. The byte counter measures input, not network traffic. Status, denied requests, and replayed results do not consume these budgets.

Files must be ordinary UTF-8 text with relative paths. Reads are limited to 256 KiB. Publishing accepts 1–50 files, makes them regular `100644` files, and forbids `.github` changes, deletions, symlinks, submodules, and executable modes. These restrictions do not make existing CI safe.

## One request from start to finish

1. Validate its operation, parameters, and request ID.
2. In one SQLite transaction, check expiration, revocation, resource ownership, and all budgets. Reserve the budgets and save the pending request.
3. Before each GitHub call, check expiration/revocation again and log the dispatch authorization.
4. Save the result and any branch/PR it created. The result can then be returned again without sending another request.

A changed request under an existing ID is refused. CLI and MCP derive default IDs from the operation and parameters; explicit IDs remain available for intentional repeats.

Failures keep their reservation. After a restart, unfinished requests become uncertain and are not replayed. A crash can therefore consume budget without creating a remote effect, or leave an effect whose ownership was not recorded. There is no automatic refund or reconciliation. The operator must inspect GitHub before replacing uncertain work.

Revocation stops new dispatch authorizations, including remaining steps of a publish. A previously authorized request can still complete. Revocation does not kill the worker, delete files, or revoke the operator's GitHub token.

## Token and state

The gateway reads one operator-supplied token file. It never uses ambient `gh` or environment credentials. The operator controls the token's repository scope, permissions, expiry, and replacement. There is no token issuance or refresh service.

HTTPS goes only to `api.github.com`, verifies TLS, ignores proxy environment variables, rejects redirects, bounds responses, and does not retry. The worker cannot supply URLs, credentials, HTTP methods, or a different repo. PRs and comments are restricted using locally recorded successful creations, not name prefixes or body stamps.

The private state directory contains policy, counters, recorded results, and events. Results can include source code; protect the directory accordingly. Events omit request bodies. Their hash chain can detect local record changes, but is not immutable or externally anchored. Do not restore a stale database as active authority.

A grant raises exactly one ceiling after displaying the diff and checking that the policy has not changed during approval. It cannot change the repo or expiration. The worker gets no grant endpoint.

## Wire format

One newline-terminated JSON request per Unix socket connection; maximum 1 MiB:

```json
{"id":"read-1","operation":"read_file","params":{"path":"README.md"}}
```

Status needs only `{"operation":"status"}`. Responses use the same shape described in [agent setup](agents.md). There are up to eight connections and one executing operation. The server takes an exclusive lock before restart recovery. Restart workers after restarting the gateway because the socket inode changes.
