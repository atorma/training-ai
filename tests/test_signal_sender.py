import asyncio
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from training_ai.signal_sender import SignalSenderHttp, SignalSendError


class FakeSignalApi:
    def __init__(self) -> None:
        self.status = 201
        self.body = b'{"timestamp":"123"}'
        self.content_type = "application/json"
        self.requests: list[dict] = []


@pytest.fixture
def signal_api() -> Iterator[tuple[FakeSignalApi, str]]:
    api = FakeSignalApi()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            api.requests.append(json.loads(self.rfile.read(length)))
            self.send_response(api.status)
            self.send_header("Content-Type", api.content_type)
            self.send_header("Content-Length", str(len(api.body)))
            self.end_headers()
            self.wfile.write(api.body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield api, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_send_returns_timestamp(signal_api):
    api, url = signal_api
    result = asyncio.run(SignalSenderHttp(url, "+10000000000").send("hello"))

    assert result.timestamp == "123"
    assert api.requests == [
        {"message": "hello", "number": "+10000000000", "recipients": ["+10000000000"]}
    ]


def test_send_error_includes_signal_api_error_field(signal_api):
    api, url = signal_api
    api.status = 400
    api.body = b'{"error":"Failed to send message: rate limit exceeded"}'

    with pytest.raises(SignalSendError) as exc_info:
        asyncio.run(SignalSenderHttp(url, "+10000000000").send("hello"))

    assert str(exc_info.value) == (
        "Signal API returned HTTP 400: Failed to send message: rate limit exceeded"
    )


def test_send_error_includes_truncated_plain_body(signal_api):
    api, url = signal_api
    api.status = 400
    api.content_type = "text/plain"
    api.body = b"bad\n" + b"x" * 1000

    with pytest.raises(SignalSendError) as exc_info:
        asyncio.run(SignalSenderHttp(url, "+10000000000").send("hello"))

    message = str(exc_info.value)
    assert message.startswith("Signal API returned HTTP 400: bad xxx")
    assert message.endswith("...")
    assert len(message) < 600
