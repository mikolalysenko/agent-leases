import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_leases.github import GitHub, GitHubApp, TokenFile, UpstreamError, https


class GitHubTests(unittest.TestCase):
    def test_transport_has_fixed_host_no_redirects_no_retry(self):
        response = Mock(status=302)
        connection = Mock()
        connection.getresponse.return_value = response
        with patch("http.client.HTTPSConnection", return_value=connection) as constructor:
            with self.assertRaises(UpstreamError):
                https("POST", "/repos/a/b/pulls", "CANARY_TOKEN", {"draft": True})
        constructor.assert_called_once_with("api.github.com", timeout=20)
        self.assertEqual(connection.request.call_count, 1)
        response.read.assert_not_called()
        connection.close.assert_called_once()

    def test_backend_rejects_paths_outside_bound_repo(self):
        credentials = Mock()
        with self.assertRaises(ValueError):
            GitHub(credentials).request("a/b", {}, "GET", "/repos/a/other/contents/x")
        credentials.token.assert_not_called()

    def test_credentials_are_explicit_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "token"
            file.write_text("CANARY_TOKEN")
            file.chmod(0o644)
            with self.assertRaises(ValueError):
                TokenFile(file)
            file.chmod(0o600)
            credential = TokenFile(file)
            self.assertEqual(credential.token("a/b", {}), "CANARY_TOKEN")
            file.write_text("token\r\nInjected-Header: bad")
            with self.assertRaises(UpstreamError):
                credential.token("a/b", {})

    def test_app_tokens_are_scoped_signed_cached_and_refreshed(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "app.pem"
            subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"],
                           check=True, capture_output=True)
            key.chmod(0o600)
            broker = GitHubApp("123", 456, key)
            response = {"token": "SCOPED_CANARY", "expires_at": "2099-01-01T00:00:00Z"}
            with patch("agent_leases.github.https", return_value=response) as transport:
                permissions = {"contents": "read"}
                broker.token("acme/repo", permissions)
                broker.token("acme/repo", permissions)
                transport.assert_called_once()
                method, path, jwt, body = transport.call_args.args
                self.assertEqual((method, path), ("POST", "/app/installations/456/access_tokens"))
                self.assertEqual(body, {"repositories": ["repo"], "permissions": permissions})
                head, claims, signature = jwt.split(".")
                payload = json.loads(base64.urlsafe_b64decode(claims + "=="))
                self.assertEqual(payload["iss"], "123")
                self.assertEqual(payload["exp"] - payload["iat"], 600)
                public = Path(directory) / "public.pem"
                subprocess.run(["openssl", "rsa", "-in", str(key), "-pubout", "-out", str(public)],
                               check=True, capture_output=True)
                sig = Path(directory) / "signature"
                sig.write_bytes(base64.urlsafe_b64decode(signature + "=="))
                subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(public),
                                "-signature", str(sig)], input=(head + "." + claims).encode(),
                               check=True, capture_output=True)
                broker.token("acme/repo", {"contents": "write"})
                self.assertEqual(transport.call_count, 2)
                broker.cache[("acme/repo", '{"contents":"read"}')] = ("old", 0)
                broker.token("acme/repo", permissions)
                self.assertEqual(transport.call_count, 3)


if __name__ == "__main__":
    unittest.main()
