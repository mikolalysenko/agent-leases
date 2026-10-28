"""Plain commands for people; JSON and MCP for agents. One gateway for all three."""

import argparse
import os
from pathlib import Path
import sqlite3
import sys

from .gateway import client, operation_request, serve
from .github import GitHub
from .policy import (Denied, LIMITS, OPERATIONS, canonical, compile_policy, digest,
                     integer, parse_json, path, render, starter_policy)
from .store import Store


def parser():
    root = argparse.ArgumentParser(usage="lease COMMAND ...",
        description="Give an agent a GitHub task budget. Keep your token outside it.",
        epilog="Start with: lease demo. Use lease COMMAND --help for details.")
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="write a starter policy to stdout")
    init.add_argument("repo", help="owner/repo")
    init.add_argument("--commit", required=True, help="reviewed parent commit SHA")
    init.add_argument("--base", default="main")
    init.add_argument("--minutes", type=int, default=60)
    init.add_argument("--purpose", default="Make a small change")
    approve = sub.add_parser("approve", help="review a policy and create a lease")
    approve.add_argument("policy")
    grant = sub.add_parser("grant", help="raise one budget, e.g. grant comment 1")
    grant.add_argument("cap", choices=OPERATIONS + LIMITS)
    grant.add_argument("amount", type=int)
    revoke = sub.add_parser("revoke", help="stop this lease's future requests")
    audit = sub.add_parser("audit", help="show the lease's log")
    audit.add_argument("--verify", action="store_true")
    server = sub.add_parser("serve", help="start the gateway with your token file")
    server.add_argument("--token-file", required=True)
    for item in (approve, grant, revoke, audit, server):
        item.add_argument("--state", default=".lease", help="private state directory (default: .lease)")
    for item in (approve, grant):
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
    call.add_argument("operation", help="read_file, publish, open_pr, or comment")
    params = call.add_mutually_exclusive_group(required=True)
    params.add_argument("--params", help="JSON object")
    params.add_argument("--params-file", help="JSON file, or - for stdin")
    for item in (read, publish, pr, comment, call):
        item.add_argument("--id", help="override the stable ID; identical commands otherwise reuse their result")
    for item in (status, read, publish, pr, comment):
        item.add_argument("--json", action="store_true", help="return structured output for scripts and agents")
    mcp = sub.add_parser("mcp", help="expose worker tools over MCP stdio")
    run = sub.add_parser("run", help="Linux: launch a worker with only its workspace and lease socket")
    run.add_argument("--workspace", required=True)
    run.add_argument("argv", nargs=argparse.REMAINDER)
    for item in (status, read, publish, pr, comment, call, mcp, run):
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
        if "state" in result:
            print("%s — %s — %ds left" % (result["policy"]["repo"], result["state"], result["seconds_left"]))
            for cap in OPERATIONS + LIMITS:
                print("  %-18s %s left" % (cap, result["left"][cap]))
        elif "content" in result:
            print(result["content"], end="")
        else:
            print(result.get("url") or result.get("branch") or "Comment added.")
    return 0 if response["ok"] else 1


def execute(args):
    if args.command == "init":
        import json
        print(json.dumps(starter_policy(args.repo, args.commit, args.base, args.minutes * 60, args.purpose), indent=2))
    elif args.command == "approve":
        policy = compile_policy(parse_json(Path(args.policy).read_bytes()))
        print(render(policy))
        if not confirm(args):
            return 1
        store = Store.create(args.state, policy)
        print("Approved. Start with: lease serve --state %s --token-file TOKEN_FILE" % store.directory)
    elif args.command == "grant":
        integer(args.amount, "grant amount", 1)
        store = Store(args.state)
        lease = store.status()
        section = "capabilities" if args.cap in OPERATIONS else "limits"
        before = lease["policy"][section][args.cap]
        print("%s: %s %d -> %d; %ds left" % (lease["policy"]["repo"], args.cap, before,
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
        serve(store, GitHub(store.status()["policy"]["repo"], args.token_file))
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
