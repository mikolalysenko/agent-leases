"""Small, real MCP service for the sandbox walkthrough. Stores notes on the host.

Run with an absolute output directory as the only argument. The gateway owns this
process. It is intentionally separate from the untrusted worker.
"""

import json
from pathlib import Path
import sys


def dispatch(method, params, directory):
    if method == "initialize":
        return {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "example-notes", "version": "1"}}
    if method == "resources/read" and params == {"uri": "notes://guide"}:
        return {"contents": [{"uri": "notes://guide", "mimeType": "text/plain",
                              "text": "Save a short note to the demo notebook."}]}
    if method == "prompts/get" and params.get("name") == "write_note":
        topic = params.get("arguments", {}).get("topic", "today's work")
        return {"messages": [{"role": "user", "content": {"type": "text", "text": "Write a note about " + topic}}]}
    if method == "tools/call" and params.get("name") == "add_note":
        args = params["arguments"]
        if args.get("notebook") != "demo" or not isinstance(args.get("text"), str) or len(args["text"]) > 2000:
            raise ValueError("invalid note")
        with (directory / "demo.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps({"text": args["text"]}) + "\n")
        return {"content": [{"type": "text", "text": "Note saved."}]}
    raise ValueError("unsupported request")


def main():
    directory = Path(sys.argv[1]).resolve(strict=True)
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        response = {"jsonrpc": "2.0", "id": request["id"]}
        try:
            response["result"] = dispatch(request["method"], request.get("params", {}), directory)
        except (ValueError, KeyError, TypeError):
            response["error"] = {"code": -32602, "message": "Invalid notes request"}
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
