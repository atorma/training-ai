import asyncio
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from training_ai.signal_sender import (
    MAX_MESSAGE_BYTES,
    SignalSenderHttp,
    SignalSendError,
    split_message,
)


class FakeSignalApi:
    def __init__(self) -> None:
        self.status = 201
        self.body = b'{"timestamp":"123"}'
        self.statuses: list[int] = []
        self.content_type = "application/json"
        self.requests: list[dict] = []


@pytest.fixture
def signal_api() -> Iterator[tuple[FakeSignalApi, str]]:
    api = FakeSignalApi()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            api.requests.append(json.loads(self.rfile.read(length)))
            status = api.statuses.pop(0) if api.statuses else api.status
            body = api.body if status != 201 else f'{{"timestamp":"{len(api.requests)}"}}'.encode()
            self.send_response(status)
            self.send_header("Content-Type", api.content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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

    assert result.timestamp == "1"
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


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def test_long_message_is_sent_as_several_messages_under_the_limit(signal_api):
    api, url = signal_api
    paragraphs = [f"Paragraph {i}: " + "äö " * 150 for i in range(8)]
    message = "\n\n".join(paragraphs)
    assert _byte_len(message) > 2000

    result = asyncio.run(SignalSenderHttp(url, "+10000000000").send(message))

    sent = [request["message"] for request in api.requests]
    assert len(sent) > 1
    assert all(_byte_len(part) <= MAX_MESSAGE_BYTES for part in sent)
    assert "\n\n".join(sent) == message
    assert result.timestamp == "1"


def test_failure_of_a_later_part_raises(signal_api):
    api, url = signal_api
    api.statuses = [201, 400]
    api.body = b'{"error":"Failed to send message"}'

    with pytest.raises(SignalSendError, match="HTTP 400: Failed to send message"):
        asyncio.run(SignalSenderHttp(url, "+10000000000").send("x " * 2000))

    assert len(api.requests) == 2


def test_split_message_keeps_short_message_whole():
    assert split_message("hello\n\nworld") == ["hello\n\nworld"]


def test_split_message_falls_back_to_lines_words_and_characters():
    lines = "\n".join(["line " + "w" * 30] * 10)
    assert split_message(lines, max_bytes=80) == ["\n".join(["line " + "w" * 30] * 2)] * 5

    assert split_message("aaa bbb ccc", max_bytes=7) == ["aaa bbb", "ccc"]
    assert split_message("ääääää", max_bytes=5) == ["ää", "ää", "ää"]
