"""LocalServer: how it binds and how it reports a request that fails."""

import contextlib
import io
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from unittest import mock

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo.local_server import LocalServer


def serve_once(failure: Exception) -> str:
    """What the server prints to stderr when handling one request raises `failure`."""

    class Failing(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            raise failure

    server = LocalServer(("127.0.0.1", 0), Failing)
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        with socket.create_connection(server.server_address) as client:
            client.sendall(b"GET / HTTP/1.0\r\n\r\n")
            client.recv(1)
        thread.join(5)
    server.server_close()
    return stderr.getvalue()


class BindTests(unittest.TestCase):
    def test_binding_asks_no_dns_server(self):
        # A slow resolver must not delay the preview's start.
        with mock.patch("socket.getfqdn", side_effect=AssertionError("getfqdn called")):
            server = LocalServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.addCleanup(server.server_close)
        self.assertEqual((server.server_name, server.server_port), server.server_address[:2])
        self.assertNotEqual(server.server_port, 0)


class ErrorReportTests(unittest.TestCase):
    def test_a_client_that_leaves_mid_reply_is_not_reported(self):
        for failure in (BrokenPipeError(32, "Broken pipe"), ConnectionResetError(54, "Connection reset by peer")):
            with self.subTest(failure=type(failure).__name__):
                self.assertEqual(serve_once(failure), "")

    def test_other_failures_are_still_reported(self):
        self.assertIn("RuntimeError: boom", serve_once(RuntimeError("boom")))


if __name__ == "__main__":
    unittest.main()
