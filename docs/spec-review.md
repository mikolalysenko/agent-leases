# Review of “Agent Leases — spec v0.2”

Reviewed October 27, 2026. This is an engineering review of the supplied specification, with targeted checks of the GitHub, sandbox, and spend-limit claims that affect implementation. It is not a complete verification of every incident, prior-art product, date, or quoted research statistic in the source text.

The useful core is a credential-holding reference monitor with a human-approved, finite authority envelope. Keep the compiled approval, deny-by-default operations, and budget reservation outside the worker. The spec currently promises more than its accounting and execution model establish. Resolve the following before implementing transparent native-protocol support.

## 1. Define the transaction and failure model first

“Decrements a counter for each” leaves races, retries, crashes, partial operations, and ambiguous upstream responses unspecified. Two workers can both pass a check for the last PR. A timeout can occur after GitHub created the PR. Refunding that request and retrying can exceed the approved cap.

Require an atomic reservation across every applicable cap, a durable client-supplied idempotency key, a persisted request digest, and an explicit outcome state. Reserve before sending. On ambiguity, keep the reservation and do not automatically resend. A multi-step operation needs a definition of partial success and reconciliation. Do not claim exactly-once remote effects when the upstream API offers no corresponding guarantee.

**MVP:** SQLite transactions reserve first; repeated IDs return the recorded outcome. Interrupted reservations become `uncertain` on gateway restart. Failed attempts are charged. This trades unused budget for a simple, conservative upper bound.

## 2. Narrow the revocation promise

“Ending it is instant” is true only for new authorization decisions at the gateway. It cannot undo an accepted push, retract a PR notification, cancel an already queued CI job, or necessarily stop an HTTP request in flight. Specify the linearization point, and check revocation between each step of a compound operation.

The credential table also mixes different expiry and revocation properties; “tokens it already minted die within the hour” is not a general statement for AWS sessions, PATs, or project service-account keys. Token cleanup and stopping gateway use are separate mechanisms.

**MVP:** local revocation prevents subsequent dispatch authorizations. Already-authorized calls may complete. Tokens remain in the gateway and expire independently; no provider token-revocation guarantee is claimed.

## 3. Make the example and approval text agree

- The example authorizes pushes under `resources` but gives them no operation cap. Its 15 external actions exactly equal 5 PRs plus 10 comments. Are pushes charged, and if so how many ref updates count?
- `network.allow` names npm and Anthropic, while GitHub is implicitly reachable. Specify whether service packs add hosts and show the effective host set in approval.
- `egress_mb` needs a unit, direction, accounting layer, and treatment of retries, TLS overhead, streaming, uploads, and broker traffic.
- Delegation mentions a maximum child count but the example has no corresponding field. “Only the person can approve” conflicts with parent-approved child extensions unless budget transfers are explicitly a separate authorization class.
- A ruleset excluding `lease/**` cannot itself prevent force pushes on those excluded branches. Use separate rulesets for protected branches and allowed lease branches, and specify their interactions and bypass actors.

**MVP:** four explicit counters and two aggregate counters plus payload bytes. Publishing always creates a fresh branch; no force push or ref update exists. The schema rejects unsupported fields rather than silently ignoring promised controls.

## 4. Treat CI and downstream automation as part of the authority

The spec correctly calls CI a write path. An App-owned fork alone does not prove safety: consider the fork's own workflows, target-repo `pull_request_target` behavior, privileged follow-on workflows, webhooks, installed Apps, and artifacts consumed later. Environment protection only helps resources actually gated behind that environment. Withholding Workflows permission does not protect scripts that existing workflows execute. GitHub's [secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use) discusses the risks of running untrusted code with privileged contexts.

**MVP:** require a disposable repo with Actions disabled and no privileged integrations. This is an explicit operator prerequisite, not an enforced gateway property. Do not describe this version as safe for arbitrary production repositories. Fork orchestration and inspection of effective CI authority are future work.

## 5. Host allowlists and credential filtering do not close every exfiltration path

Blocking a foreign API key handles one case. An allowed service can also accept anonymous writes, presigned URLs, attacker-owned tenant identifiers, alternate authentication fields, or data-bearing URLs. Model prompts themselves send data outside the sandbox; they should count in the disclosure analysis even if there is no public GitHub write. Package firewall checks address known packages, not arbitrary install-script behavior or all supply-chain risk.

“Only allowed hosts resolve” needs exact-name and subdomain semantics, redirect handling, IP pinning, CONNECT/SOCKS behavior, IPv6, and restrictions on credential-bearing requests. Mark the threat-table claims conditional on those mechanisms rather than unqualified “Yes.”

**MVP:** no worker DNS or Internet access. The gateway constructs fixed GitHub REST requests and never accepts a URL, method, token, repository override, or arbitrary GraphQL document. HTTPS uses a fixed host, rejects redirects, and does not inherit proxy configuration.

## 6. Taint is useful metadata, not proof of trustworthy content

Merged code is a policy choice, not proof of review or absence of planted instructions. Review bypasses, compromised maintainers, generated content, vendored dependencies, and instructions already present in a worktree all matter. “Lease first” only holds if *all* worker inputs, including startup configuration and preexisting model history, follow the same staging rule.

A `Lease:` trailer or PR-body stamp is forgeable and editable. Cross-agent taint propagation needs authenticated provenance tied to immutable content, not just a string another agent can copy. Define propagation through files, tool outputs, summaries, subagent IPC, and updated PRs. A child in the same process cannot be isolated from its parent's authority by a handle the untrusted harness chooses.

**MVP:** stamps provide attribution hints; the private gateway log is authoritative. No untainted or semantic-purpose guarantee. Delegation and drift are omitted.

## 7. A hard dollar ceiling needs worst-case admission control

Usage fields arrive after work has happened. “Expected cost” reservations cannot guarantee an exact maximum unless they upper-bound all allowed outcomes. Define output-token ceilings, pricing versions, missing usage, streaming disconnects, model/tool subcalls, concurrent requests, and provider-side background work. A shared provider limit is a backstop, not a per-lease counter.

Current [OpenAI spend-limit documentation](https://developers.openai.com/api/docs/guides/spend-limits) supports project and organization hard limits, but explicitly allows a small overshoot while enforcement propagates. Do not turn that into an exact $5 guarantee. This review does not establish the claimed key-expiry behavior or Anthropic federation details.

**MVP:** no model API and no dollar field. Unsupported dollar-limit policies fail validation.

## 8. Keep protocol compatibility out of the first implementation

Parsing Git smart HTTP, GraphQL documents, aliases, fragments, IDs, credential placement, REST variants, uploads, LFS, redirects, and tool-specific errors is most of the security product, not a small mapping file. Each pack needs a versioned, closed request schema and adversarial conformance tests. A permissive read rule can also become a side channel or fetch an unintended resource.

**MVP:** explicit typed operations over a local socket. GitHub's [tree](https://docs.github.com/en/rest/git/trees#create-a-tree), [commit](https://docs.github.com/en/rest/git/commits#create-a-commit), [reference](https://docs.github.com/en/rest/git/refs#create-a-reference), and [PR](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request) APIs provide a usable publish-and-review path without parsing arbitrary worker-generated traffic. Ordinary `git` and `gh` compatibility is deferred.

## 9. Specify the trusted computing base and audit durability

Hash chaining is not append-only storage by itself. An attacker controlling the DB can rewrite the chain or truncate its suffix unless an external party holds a checkpoint. Define log commit ordering, disk-full behavior, replay protection, backup/restore semantics, and which processes can alter counters. Availability attacks also need separate CPU, process, disk, and connection budgets.

[Bubblewrap explicitly delegates the security policy to its caller](https://github.com/containers/bubblewrap). The launcher's mounts, inherited file descriptors, host sockets, namespace settings, working directory, and runtime files are part of the implementation, not details inherited automatically from a sandbox brand.

**MVP:** private SQLite state, atomic local reservations, a locally verifiable hash chain, fail-closed authority failures, a bounded number of socket handlers, and a Linux launcher tested for basic exits. No off-host checkpoints, host-compromise resistance, cgroups, or exhaustive sandbox-escape claim.

## Targeted source corrections and unresolved checks

The [October 2 GitHub changelog](https://github.blog/changelog/2026-10-02-stateless-github-app-installation-tokens-rolled-out/) confirms completion of the stateless-token rollout and unchanged one-hour expiry and repository/permission scoping. That announcement alone does not establish early revocation semantics. Keep the revocation question open and test it against a real installation rather than inferring “stateless means not revocable.”

The [installation-token API](https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app) supports explicit repository and permission restrictions. The implementation always supplies them in App mode. The App's own installation remains the outer bound. Treat token strings as opaque; no fixed 40-character validation.

The supplied text names many references without actual URLs. The incident summaries, novelty comparison, approval statistics, srt decider status, and other providers' credential behavior need a separately maintained source table with direct links and exact versions. “No known deployment” and “what doesn't exist yet” should be scoped as research findings rather than universal claims.

## Recommended v0.3 shape

Lead with a normative state machine and explicit guarantees. Keep one-repo typed operations, durable budgets, credential custody, rendered approval, local grants, and revocation as the core. Make transparent adapters, child leases, taint, standing leases, provider metering, and drift separate extensions. Implement each only after its failure and provenance semantics can be tested. The rest of this repository implements that smaller core.
