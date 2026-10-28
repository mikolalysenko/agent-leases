"""GitHub-only HTTPS transport and credentials. No proxy, redirects, or retries."""

import base64
from datetime import datetime
import http.client
import os
from pathlib import Path
import stat
import subprocess
import time

from .policy import Denied, canonical, parse_json


class UpstreamError(Exception):
    pass


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
                                    "User-Agent": "agent-leases/0.1"})
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            # Do not echo upstream bodies, headers, or credentials to the worker.
            raise UpstreamError("GitHub returned HTTP %d; no automatic retry" % response.status)
        body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise UpstreamError("GitHub response exceeds 2 MiB")
        return parse_json(body)
    except (OSError, http.client.HTTPException, Denied) as exc:
        raise UpstreamError("GitHub transport failed; the remote outcome may be unknown") from exc
    finally:
        connection.close()


class TokenFile:
    """Operator-supplied fallback. The operator is responsible for its scope/expiry."""
    def __init__(self, filename):
        self.filename = private_file(filename)

    def token(self, repo, permissions):
        value = private_file(self.filename).read_text().strip()
        if not value or len(value) > 16384 or any(c.isspace() for c in value):
            raise UpstreamError("invalid token file")
        return value


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class GitHubApp:
    """One broker per lease. Mint repository/permission-scoped tokens lazily."""
    def __init__(self, app_id, installation_id, private_key):
        self.app_id = str(app_id)
        self.installation_id = int(installation_id)
        if self.installation_id < 1:
            raise ValueError("installation ID must be positive")
        self.private_key = private_file(private_key)
        self.cache = {}

    def token(self, repo, permissions):
        key = (repo, canonical(permissions))
        cached = self.cache.get(key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        now = int(time.time())
        header = b64(canonical({"alg": "RS256", "typ": "JWT"}).encode())
        claims = b64(canonical({"iat": now - 60, "exp": now + 540, "iss": self.app_id}).encode())
        message = (header + "." + claims).encode()
        signed = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.private_key)],
                                input=message, capture_output=True, check=True, timeout=10)
        jwt = message.decode() + "." + b64(signed.stdout)
        result = https("POST", "/app/installations/%d/access_tokens" % self.installation_id,
                       jwt, {"repositories": [repo.split("/")[1]], "permissions": permissions})
        expires = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00")).timestamp()
        self.cache[key] = (result["token"], expires)
        return result["token"]


class GitHub:
    def __init__(self, credentials):
        self.credentials = credentials

    def request(self, repo, permissions, method, path, data=None):
        if not path.startswith("/repos/" + repo + "/"):
            raise ValueError("internal error: request escaped its repository")
        return https(method, path, self.credentials.token(repo, permissions), data)
