"""Plain commands for people; JSON and MCP for agents. One gateway for all three."""

import argparse
import os
from pathlib import Path
import sqlite3
import signal
import sys
import time

from .gateway import client, operation_request, serve
from .github import private_file
from .policy import (Denied, canonical, compile_policy, digest,
                     integer, parse_json, path, render, starter_policy)
from .store import Store


class CommandParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        argv = list(sys.argv[1:] if args is None else args)
        if argv and argv[0] in ("run", "launch") and "--" in argv:
            split = argv.index("--")
            parsed = super().parse_args(argv[:split], namespace)
            parsed.argv = argv[split + 1:]
            return parsed
        return super().parse_args(argv, namespace)


def parser():
    root = CommandParser(usage="lease COMMAND ...",
        description="Run a command with a task budget. Keep credentials outside it.",
        epilog="Start with: lease demo. Use lease COMMAND --help for details.")
    sub = root.add_subparsers(prog="lease", dest="command", required=True)
    init = sub.add_parser("init", help="write a starter policy to stdout")
    init.add_argument("repo", help="owner/repo")
    init.add_argument("--commit", required=True, help="reviewed parent commit SHA")
    init.add_argument("--base", default="main")
    init.add_argument("--minutes", type=int, default=60)
    init.add_argument("--purpose", default="Make a small change")
    approve = sub.add_parser("approve", help="review a policy and create a lease")
    approve.add_argument("policy")
    grant = sub.add_parser("grant", help="raise one budget, e.g. grant comment 1")
    grant.add_argument("cap")
    grant.add_argument("amount", type=int)
    revoke = sub.add_parser("revoke", help="stop this lease's future requests")
    audit = sub.add_parser("audit", help="show the lease's log")
    audit.add_argument("--verify", action="store_true")
    server = sub.add_parser("serve", help="start the gateway with your token file")
    launch = sub.add_parser("launch", help="approve, start gateway, run sandbox, and clean up (Linux)",
                           epilog="Append -- COMMAND [ARGS...] to choose the sandboxed command.")
    launch.add_argument("policy")
    launch.add_argument("--workspace", required=True)
    launch.set_defaults(argv=[])
    for item in (server, launch):
        source = item.add_mutually_exclusive_group(required=True)
        source.add_argument("--token-file", help="legacy GitHub policy: private token file")
        source.add_argument("--config", help="private JSON file mapping service names to connection settings")
    inbox = sub.add_parser("inbox", help="show questions and budget requests from the worker")
    inbox.add_argument("--json", action="store_true")
    answer = sub.add_parser("answer", help="answer a question; grants no access")
    answer.add_argument("id")
    answer.add_argument("message")
    decide = sub.add_parser("decide", help="approve or deny one budget request")
    decide.add_argument("id")
    decide.add_argument("decision", choices=("approve", "deny"))
    decide.add_argument("--amount", type=int, help="approve fewer units than requested")
    decide.add_argument("--message", default="")
    for item in (approve, grant, revoke, audit, server, launch, inbox, answer, decide):
        item.add_argument("--state", default=".lease", help="private state directory (default: .lease)")
    for item in (approve, grant, launch, decide):
        item.add_argument("--yes", action="store_true", help="approve without a prompt (operator only)")
    status = sub.add_parser("status", help="show what this lease can still do")
    read = sub.add_parser("read", help="read a file at the approved commit")
    read.add_argument("path")
    publish = sub.add_parser("publish", help="publish local text files to a new lease branch")
    publish.add_argument("files", nargs="+")
    publish.add_argument("--message", required=True)
    pr = sub.add_parser("pr", help="open a draft PR from a lease branch")
    pr.add_argument("branch")
    pr.add_argument("--title", required=True)
    pr.add_argument("--body", default="")
    comment = sub.add_parser("comment", help="comment on a PR this lease created")
    comment.add_argument("number", type=int)
    comment.add_argument("body")
    call = sub.add_parser("call", help="send operation parameters as JSON")
    call.add_argument("operation", help="an operation from lease catalog")
    params = call.add_mutually_exclusive_group(required=True)
    params.add_argument("--params", help="JSON object")
    params.add_argument("--params-file", help="JSON file, or - for stdin")
    for item in (read, publish, pr, comment, call):
        item.add_argument("--id", help="override the stable ID; identical commands otherwise reuse their result")
    for item in (status, read, publish, pr, comment):
        item.add_argument("--json", action="store_true", help="return structured output for scripts and agents")
    mcp = sub.add_parser("mcp", help="expose worker tools over MCP stdio")
    catalog = sub.add_parser("catalog", help="list this lease's operations and input schemas")
    ask = sub.add_parser("ask", help="send the operator a question")
    ask.add_argument("message")
    request = sub.add_parser("request", help="ask for more of one existing cap")
    request.add_argument("cap")
    request.add_argument("amount", type=int)
    request.add_argument("--reason", required=True)
    for item in (ask, request):
        item.add_argument("--id", help="stable request ID")
        item.add_argument("--json", action="store_true")
    wait = sub.add_parser("wait", help="wait for an operator answer, or poll once")
    wait.add_argument("id")
    wait.add_argument("--poll", action="store_true")
    wait.add_argument("--json", action="store_true")
    run = sub.add_parser("run", help="Linux: launch a worker with only its workspace and lease socket",
                        epilog="Append -- COMMAND [ARGS...] to choose the sandboxed command.")
    run.add_argument("--workspace", required=True)
    run.set_defaults(argv=[])
    for item in (status, read, publish, pr, comment, call, mcp, run, catalog, ask, request, wait):
        item.add_argument("--socket", default=os.environ.get("LEASE_SOCKET", ".lease/gateway.sock"))
    sub.add_parser("demo", help="try the workflow offline, without a token")
    return root


def confirm(args):
    return args.yes or input("Approve? [y/N] ").strip().lower() == "y"


def worker_request(args):
    if args.command == "status":
        return {"operation": "status"}
    if args.command == "read":
        op, params = "read_file", {"path": args.path}
    elif args.command == "publish":
        op = "publish"
        params = {"message": args.message,
                  "files": {path(name, writing=True): Path(name).read_text(encoding="utf-8") for name in args.files}}
    elif args.command == "pr":
        op, params = "open_pr", {"branch": args.branch, "title": args.title, "body": args.body}
    elif args.command == "comment":
        op, params = "comment", {"number": args.number, "body": args.body}
    else:
        raw = args.params
        if args.params_file:
            raw = sys.stdin.buffer.read() if args.params_file == "-" else Path(args.params_file).read_bytes()
        op, params = args.operation, parse_json(raw)
    return operation_request(op, params, args.id)


def connections(args):
    if args.token_file:
        return {"github": {"token_file": str(private_file(args.token_file))}}
    config = parse_json(private_file(args.config).read_bytes())
    if not isinstance(config, dict):
        raise ValueError("gateway configuration must map service names to connection settings")
    return config


def check_connection_paths(value, workspace):
    if isinstance(value, dict):
        for item in value.values():
            check_connection_paths(item, workspace)
    elif isinstance(value, list):
        for item in value:
            check_connection_paths(item, workspace)
    elif isinstance(value, str) and value.startswith("/"):
        if Path(value).resolve().is_relative_to(workspace):
            raise ValueError("gateway files and commands must be outside the workspace")


def show(response, json_output=False):
    if json_output:
        print(canonical(response))
    elif not response["ok"]:
        error = response["error"]
        print("lease: %s (%s)" % (error["rule"], error["code"]), file=sys.stderr)
        if error.get("fix"):
            print(error["fix"], file=sys.stderr)
    else:
        result = response["result"]
        if isinstance(result, dict) and "policy" in result:
            print("%s — %ds left" % (result["state"], result["seconds_left"]))
            for cap in result["left"]:
                print("  %-18s %s left" % (cap, result["left"][cap]))
        elif isinstance(result, dict) and "request" in result:
            print(result["id"] + " — " + result["state"])
            if result["answer"]:
                print(canonical(result["answer"]))
        elif isinstance(result, dict) and isinstance(result.get("content"), str):
            print(result["content"], end="")
        else:
            print((result.get("url") or result.get("branch") or canonical(result))
                  if isinstance(result, dict) else canonical(result))
    return 0 if response["ok"] else 1


def execute(args):
    if args.command == "init":
        import json
        print(json.dumps(starter_policy(args.repo, args.commit, args.base, args.minutes * 60, args.purpose), indent=2))
    elif args.command in ("approve", "launch"):
        if args.command == "launch":
            from .sandbox import check_platform
            check_platform()
            if not args.argv:
                raise ValueError("provide a worker command after --")
            config = connections(args)
            workspace = Path(args.workspace).resolve(strict=True)
            check_connection_paths(config, workspace)
            # The worker must not be able to modify connection settings or credentials.
            for filename in (args.policy, args.config, args.token_file, args.state):
                if filename and Path(filename).resolve().is_relative_to(workspace):
                    raise ValueError("policy, connection settings, credentials, and state must be outside the workspace")
        policy = compile_policy(parse_json(Path(args.policy).read_bytes()))
        print(render(policy))
        if args.command == "launch":
            print("Trusted gateway services: " + ", ".join(config))
        if not confirm(args):
            return 1
        store = Store.create(args.state, policy)
        if args.command == "launch":
            from .sandbox import launch
            argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            return launch(store, config, workspace, argv)
        print("Approved. Start the gateway with lease serve --state %s and --config or --token-file." % store.directory)
    elif args.command == "grant":
        integer(args.amount, "grant amount", 1)
        store = Store(args.state)
        lease = store.status()
        if args.cap not in lease["left"]:
            raise Denied("invalid", "unknown cap")
        section = "capabilities" if args.cap in lease["policy"]["capabilities"] else "limits"
        before = lease["policy"][section][args.cap]
        print("%s %d -> %d; %ds left" % (args.cap, before,
                                           before + args.amount, lease["seconds_left"]))
        print("Other budgets and expiration stay the same.")
        if not confirm(args):
            return 1
        store.grant(args.cap, args.amount, digest(lease["policy"]))
    elif args.command == "revoke":
        Store(args.state).revoke()
        print("Revoked. Requests already sent may still complete.")
    elif args.command == "audit":
        store = Store(args.state)
        if args.verify:
            print("Audit head: " + store.verify())
        else:
            for event in store.events():
                print(canonical(event))
    elif args.command == "serve":
        store = Store(args.state)
        serve(store, connections(args))
    elif args.command == "inbox":
        items = Store(args.state).inbox()
        if args.json:
            print(canonical(items))
        else:
            for item in items:
                print("%s — %s — %s" % (item["id"], item["state"], item["request"]["kind"]))
                print("  Agent's words: " + canonical(item["request"]["message"]))
                if item["request"]["kind"] == "more":
                    print("  Requested: +%d %s" % (item["request"]["amount"], item["request"]["cap"]))
                if item["answer"]:
                    print("  Answer: " + canonical(item["answer"]))
    elif args.command in ("answer", "decide"):
        store = Store(args.state)
        lease, item = store.status(), store.inbox(args.id)
        decision = "answer" if args.command == "answer" else args.decision
        amount = getattr(args, "amount", None)
        print("Agent's words: " + canonical(item["request"]["message"]))
        if decision == "approve":
            request = item["request"]
            if request["kind"] != "more":
                raise Denied("invalid", "use answer for questions")
            granted = request["amount"] if amount is None else amount
            integer(granted, "approved amount", 1, request["amount"])
            cap = request["cap"]
            before = {**lease["policy"]["capabilities"], **lease["policy"]["limits"]}[cap]
            print("%s: %d -> %d; %ds left. Other caps and expiry stay the same." % (
                cap, before, before + granted, lease["seconds_left"]))
            if not confirm(args):
                return 1
        store.resolve(args.id, decision, args.message, amount, digest(lease["policy"]))
        print("Answer recorded.")
    elif args.command in ("ask", "request"):
        from .mcp import submission
        request = (submission("ask", args.message, request_id=args.id) if args.command == "ask" else
                   submission("more", args.reason, args.cap, args.amount, args.id))
        return show(client(args.socket, request), args.json)
    elif args.command == "wait":
        while True:
            response = client(args.socket, {"operation": "poll", "request_id": args.id})
            if args.poll or not response["ok"] or response["result"]["state"] != "pending":
                return show(response, args.json)
            time.sleep(1)
    elif args.command == "catalog":
        return show(client(args.socket, {"operation": "catalog"}), True)
    elif args.command == "run":
        from .sandbox import run
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        return run(args.socket, args.workspace, argv)
    elif args.command == "mcp":
        from .mcp import serve_mcp
        serve_mcp(args.socket)
    elif args.command == "demo":
        from .demo import main
        main()
    else:
        return show(client(args.socket, worker_request(args)), args.command == "call" or args.json)
    return 0


def main():
    os.umask(0o077)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    args = parser().parse_args()
    try:
        sys.exit(execute(args))
    except Denied as exc:
        sys.exit(show({"ok": False, "error": exc.data}, getattr(args, "json", False) or args.command == "call"))
    except (OSError, ValueError, sqlite3.Error) as exc:
        response = {"ok": False, "error": {"code": "unavailable", "rule": str(exc), "retryable": False}}
        sys.exit(show(response, getattr(args, "json", False) or args.command == "call"))
    except KeyboardInterrupt:
        sys.exit(130)
