"""GitHub-only HTTPS transport and credentials. No proxy, redirects, or retries."""

import http.client
import os
from pathlib import Path
import stat

from .policy import Denied, UpstreamError, canonical, parse_json


def private_file(filename):
    filename = Path(filename).resolve(strict=True)
    info = filename.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("credential file must be owned by this user and have mode 0600")
    return filename


def https(method, path, token, data=None):
    connection = http.client.HTTPSConnection("api.github.com", timeout=20)
    try:
        connection.request(method, path, body=None if data is None else canonical(data).encode(),
                           headers={"Authorization": "Bearer " + token,
                                    "Accept": "application/vnd.github+json",
                                    "Content-Type": "application/json",
                                    "X-GitHub-Api-Version": "2026-03-10",
                                    "User-Agent": "agent-leases/0.2"})
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            # Do not echo upstream bodies, headers, or credentials to the worker.
            raise UpstreamError("GitHub returned HTTP %d" % response.status, response.status)
        body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise UpstreamError("GitHub response exceeds 2 MiB")
        return parse_json(body)
    except (OSError, http.client.HTTPException, Denied) as exc:
        raise UpstreamError("GitHub transport failed; the remote outcome may be unknown") from exc
    finally:
        connection.close()


class GitHub:
    """One repository, one operator-supplied token file. No ambient credentials."""
    def __init__(self, repo, token_file):
        self.root = "/repos/" + repo + "/"
        self.token_file = private_file(token_file)

    def request(self, method, path, data=None):
        if not path.startswith(self.root):
            raise ValueError("internal error: request escaped its repository")
        token = private_file(self.token_file).read_text().strip()
        if not token or len(token) > 16384 or any(c.isspace() for c in token):
            raise UpstreamError("invalid token file")
        return https(method, path, token, data)
