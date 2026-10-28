from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_leases.github import GitHub, UpstreamError, https


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
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token"
            token.write_text("CANARY_TOKEN")
            token.chmod(0o600)
            backend = GitHub("a/b", token)
            with patch("agent_leases.github.https") as transport:
                with self.assertRaises(ValueError):
                    backend.request("GET", "/repos/a/other/contents/x")
                transport.assert_not_called()

    def test_credentials_are_explicit_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "token"
            file.write_text("CANARY_TOKEN")
            file.chmod(0o644)
            with self.assertRaises(ValueError):
                GitHub("a/b", file)
            file.chmod(0o600)
            credential = GitHub("a/b", file)
            with patch("agent_leases.github.https", return_value={}) as transport:
                credential.request("GET", "/repos/a/b/contents/x")
                self.assertEqual(transport.call_args.args[2], "CANARY_TOKEN")
            file.write_text("token\r\nInjected-Header: bad")
            with self.assertRaises(UpstreamError):
                credential.request("GET", "/repos/a/b/contents/x")


if __name__ == "__main__":
    unittest.main()
