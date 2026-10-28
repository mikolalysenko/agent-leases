"""Operator commands use the state directory; worker commands use only its socket."""

import argparse
import os
from pathlib import Path
import sys
import sqlite3

from .gateway import client, serve
from .github import GitHub, GitHubApp, TokenFile
from .policy import (Denied, LIMITS, OPERATIONS, canonical, compile_policy, digest,
                     integer, parse_json, render)
from .store import Store


def parser():
    root = argparse.ArgumentParser(description="A small GitHub capability lease")
    sub = root.add_subparsers(dest="command", required=True)
    approve = sub.add_parser("approve", help="operator: review and create a lease")
    approve.add_argument("policy")
    approve.add_argument("--yes", action="store_true", help="approve noninteractively as the operator")
    grant = sub.add_parser("grant", help="operator: increase exactly one counter ceiling")
    grant.add_argument("--cap", required=True, choices=OPERATIONS + LIMITS)
    grant.add_argument("--add", required=True, type=int)
    grant.add_argument("--yes", action="store_true")
    revoke = sub.add_parser("revoke", help="operator: deny further dispatches")
    audit = sub.add_parser("audit", help="operator: inspect or verify the local hash chain")
    audit.add_argument("--verify", action="store_true")
    server = sub.add_parser("serve", help="operator: start this lease's gateway")
    auth = server.add_mutually_exclusive_group(required=True)
    auth.add_argument("--token-file", help="operator-scoped token; no automatic refresh")
    auth.add_argument("--app-id", help="GitHub App ID (requires installation ID and private key)")
    server.add_argument("--installation-id", type=int)
    server.add_argument("--private-key")
    for item in (approve, grant, revoke, audit, server):
        item.add_argument("--state", required=True, help="private operator-owned state directory")
    status = sub.add_parser("status", help="worker: inspect the lease and remaining counters")
    call = sub.add_parser("call", help="worker: request an explicit operation")
    call.add_argument("operation", choices=OPERATIONS)
    call.add_argument("--id", required=True, help="stable request ID; reuse it when delivery is uncertain")
    params = call.add_mutually_exclusive_group(required=True)
    params.add_argument("--params", help="JSON object")
    params.add_argument("--params-file", help="JSON file, or - for stdin")
    run = sub.add_parser("run", help="Linux: run a worker with only this socket and workspace")
    run.add_argument("--workspace", required=True)
    run.add_argument("argv", nargs=argparse.REMAINDER)
    for item in (status, call, run):
        item.add_argument("--socket", default=os.environ.get("LEASE_SOCKET", "/run/lease.sock"))
    return root


def confirm(args):
    return args.yes or input("Approve this exact policy? [y/N] ").strip().lower() == "y"


def execute(args):
    if args.command == "approve":
        policy = compile_policy(parse_json(Path(args.policy).read_bytes()))
        print(render(policy))
        if not confirm(args):
            return 1
        print(canonical(Store.create(args.state, policy).status()))
    elif args.command == "grant":
        integer(args.add, "grant amount", 1)
        store = Store(args.state)
        lease = store.status()
        section = "capabilities" if args.cap in OPERATIONS else "limits"
        before = lease["policy"][section][args.cap]
        print("Lease %s (%s): %s %d -> %d; %ds left" % (
            lease["id"], lease["policy"]["repo"], args.cap, before, before + args.add,
            lease["seconds_left"]))
        print("All other caps and the original expiration remain in force.")
        if not confirm(args):
            return 1
        store.grant(args.cap, args.add, digest(lease["policy"]))
    elif args.command == "revoke":
        Store(args.state).revoke()
        print("Revoked. Already-dispatched work may still complete.")
    elif args.command == "audit":
        store = Store(args.state)
        if args.verify:
            print("Audit head: " + store.verify())
        else:
            for event in store.events():
                print(canonical(event))
    elif args.command == "serve":
        if args.app_id:
            if not args.installation_id or not args.private_key:
                raise ValueError("--app-id requires --installation-id and --private-key")
            credentials = GitHubApp(args.app_id, args.installation_id, args.private_key)
        else:
            credentials = TokenFile(args.token_file)
        serve(Store(args.state), GitHub(credentials))
    elif args.command == "run":
        from .sandbox import run
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        return run(args.socket, args.workspace, argv)
    else:
        request = {"operation": "status"}
        if args.command == "call":
            raw = args.params
            if args.params_file:
                raw = sys.stdin.buffer.read() if args.params_file == "-" else Path(args.params_file).read_bytes()
            request = {"id": args.id, "operation": args.operation, "params": parse_json(raw)}
        response = client(args.socket, request)
        print(canonical(response))
        return 0 if response["ok"] else 1
    return 0


def main():
    os.umask(0o077)
    args = parser().parse_args()
    try:
        sys.exit(execute(args))
    except Denied as exc:
        print(canonical({"ok": False, "error": exc.data}), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print("lease: " + str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
