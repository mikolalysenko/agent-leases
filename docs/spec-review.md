# Review of the v0.2 proposal

The useful core is simple: approve a bounded task, keep credentials outside the worker, and enforce the budget at the gateway. The implementation keeps that core. Version 0.3 moves service behavior into adapters and adds the missing sandbox launch and operator request flow.

These are the changes the original spec needs before its stronger promises can hold.

## Count attempts before sending them

Two workers can race for the last PR allowance. A timed-out request can still succeed at GitHub. Reserve all relevant budgets atomically, record an ID before dispatch, and never automatically retry an uncertain write. Failed attempts should keep their reservation until an operator reconciles them.

The example also allows pushes without giving them a cap: its 15 external actions already equal five PRs plus ten comments. Define precisely what a push and a multi-step publish consume.

## Describe revocation precisely

Revocation stops new authorization decisions. It cannot recall a request already sent, undo a notification, or cancel CI that GitHub already started. Check revocation between steps, and separate local gateway revocation from provider-token expiry.

## Include CI in the boundary

A branch or draft PR can trigger code with secrets and network access. A fork, environment approval, or lack of Workflows permission is not sufficient by itself; the whole automation path matters. GitHub's [secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use) describes the risk of privileged execution of untrusted code.

This prototype requires a disposable repo with Actions disabled and no privileged integrations. It does not enforce that setting.

## Keep the first API small

A transparent Git/REST/GraphQL proxy has to understand every accepted request form, redirect, credential location, and write path. That is a large security surface. Four named operations provide a useful workflow with much less machinery. Fresh branches also avoid ref-update and force-push rules.

Those four operations belong in a GitHub adapter, not in the lease engine. Other services can use their own adapters; explicit MCP bindings cover tools, resources, prompts, and other request methods. A generic MCP call is one budgeted attempt, not a guarantee about every effect the server causes. Trusted server code and semantic resource checks remain necessary.

The execution boundary should be concrete: launch the whole command in a network-isolated sandbox, expose only the gateway socket, and give the worker a request/answer channel. This blocks native clients as well as agent tools. Transparent native-client compatibility and full MCP protocol coverage are separate features, not prerequisites for that boundary.

The source spec's proposed ruleset excludes `lease/**`; that same rule cannot block force pushes on the excluded branches. Separate rules would be required if ref updates are later supported.

## Treat trust and taint as unresolved

Merged code can still contain hostile instructions. A `Lease:` trailer can be copied or edited. Neither proves trustworthy input or authenticated provenance. An in-process child can use its parent's authority unless there is another enforceable boundary.

Likewise, an allowed host can accept anonymous or attacker-directed data. Model prompts are outbound data too. Credential filtering and host allowlists do not, by themselves, prevent disclosure.

## Promise only budgets you can enforce

Usage reported after a model call cannot establish an exact dollar ceiling. Reservations must cover worst-case cost, including streaming, retries, concurrent calls, and missing usage. Even [OpenAI's documented hard spend limits](https://developers.openai.com/api/docs/guides/spend-limits) can slightly overshoot while enforcement propagates.

This version counts operations and input bytes. It has no model connection or dollar field, and rejects unsupported policy fields.

## State what is trusted

The gateway, local state, operator, kernel, sandbox configuration, and mounted system tools are trusted. [Bubblewrap's protection depends on its launch arguments](https://github.com/containers/bubblewrap). A local hash chain does not stop a trusted host rewriting the log or restoring old counters.

The review was made on October 27, 2026, with targeted checks of relevant primary documentation. It does not verify all incident statistics, product comparisons, or novelty claims in the supplied text; many references there lacked direct URLs.

The current implementation and its tested limits are described in [how it works](design.md), [security](security.md), and [testing](testing.md).
