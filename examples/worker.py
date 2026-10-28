"""A command that uses a service, asks a question, and requests more budget."""

import os
import time

from agent_leases.gateway import client, operation_request
from agent_leases.mcp import submission


def send(request):
    response = client(os.environ["LEASE_SOCKET"], request)
    if not response["ok"]:
        raise RuntimeError(response["error"])
    return response["result"]


def answer(request_id):
    print("Waiting for operator: " + request_id, flush=True)
    while True:
        response = send({"operation": "poll", "request_id": request_id})
        if response["state"] == "answered":
            return response["answer"]
        if response["state"] != "pending":
            raise RuntimeError("lease ended before an answer arrived")
        time.sleep(1)


print(send(operation_request("notes.guide", {})), flush=True)
send(submission("ask", "What should the first note say?", request_id="note-text"))
text = answer("note-text")["message"]
print(send(operation_request("notes.add", {"text": text}, "first-note")), flush=True)

request = operation_request("notes.add", {"text": "The second note needed your approval."}, "second-note")
denial = client(os.environ["LEASE_SOCKET"], request)
assert denial["error"]["code"] == "limit", denial
send(submission("more", "May I save one more note?", "notes.add", 1, "one-more"))
if answer("one-more")["decision"] == "approve":
    print(send(request), flush=True)
else:
    print("Operator declined. Finished with one note.", flush=True)
