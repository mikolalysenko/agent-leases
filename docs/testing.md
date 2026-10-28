# Testing

The unit suite checks the policy, SQLite counters, transport, and interfaces:

```sh
python3 -m unittest discover -v
```

[Permission Matrix](https://github.com/mikolalysenko/permission-matrix) tests the running system through separate worker processes:

```sh
cd ../permission-matrix
npm run build
node dist/src/cli.js leases --repo ../agent-leases --output artifacts/leases
```

It compares CLI, raw socket, and MCP calls, then repeats them inside `lease run` on Linux. Each check has a working allowed control and inspects actual calls received by a recording GitHub provider. A returned refusal cannot hide an observed forbidden effect. No model or real GitHub credentials are used.

Coverage includes the full read/publish/PR/comment workflow, each budget, concurrent attempts, retries across interfaces, changed request IDs, partial writes, restart recovery, grants and revocation, resource overrides, malformed input, and MCP discovery/notifications. Linux probes cover token/state files, protected writes, environment, inherited descriptors, worker administration, TCP/UDP/IPv6/abstract sockets, and unsafe workspace mounts.

Results distinguish **passed**, **gap**, and **unknown**. Unknown is never evidence of protection. macOS runs the host interfaces but reports Linux cells as unknown; use explicit `--path cli --path socket --path mcp` for a host-only run. Do not use that narrower run to claim sandbox coverage.

Reports contain per-cell policy, argv, raw output, provider effects, counters, and audit events. Use `--case CASE --path PATH` to rerun a finding in fresh state. The adapter also tests itself by injecting a provider write followed by a denial or worker crash; both must be classified as gaps.

These are deterministic integration tests against a synthetic GitHub provider. They do not establish live GitHub write compatibility, model behavior, CI safety, or resistance to every sandbox escape. See the [work log](work-log.md) for verified runs and [security limits](security.md) for the deployment assumptions.
