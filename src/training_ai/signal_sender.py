from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

from training_ai.config import Config


class SignalSendError(RuntimeError):
    pass


@dataclass(frozen=True)
class SignalSendResult:
    timestamp: Optional[str] = None


class SignalSender(Protocol):
    async def send(self, message: str) -> SignalSendResult: ...


class SignalSenderHttp:
    def __init__(
        self,
        api_url: str,
        number: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ):
        self._api_url = api_url.rstrip("/")
        self._number = number
        self._timeout = timeout
        self._headers = headers

    async def send(self, message: str) -> SignalSendResult:
        """Send as one or more messages; the result carries the first message's timestamp."""
        url = f"{self._api_url}/v2/send"
        timestamp: Optional[str] = None
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
                for index, part in enumerate(split_message(message)):
                    part_timestamp = await self._send_part(client, url, part)
                    if index == 0:
                        timestamp = part_timestamp
        except httpx.RequestError as exc:
            raise SignalSendError("Signal API request failed") from exc
        return SignalSendResult(timestamp=timestamp)

    async def _send_part(self, client: httpx.AsyncClient, url: str, part: str) -> Optional[str]:
        payload = {
            "message": part,
            "number": self._number,
            "recipients": [self._number],
        }
        response = await client.post(url, json=payload)
        if 500 <= response.status_code < 600:
            response = await client.post(url, json=payload)

        if response.status_code != 201:
            detail = _error_detail(response)
            suffix = f": {detail}" if detail else ""
            raise SignalSendError(f"Signal API returned HTTP {response.status_code}{suffix}")

        return _extract_timestamp(response)


# Signal sends texts over 2000 UTF-8 bytes as a long-text attachment. Since Signal-Server
# v20261104.0.0 that upload fails with HTTP 400 in signal-cli, so long texts go out as several
# plain messages, with margin below the limit.
MAX_MESSAGE_BYTES = 1900
_SEPARATORS = ("\n\n", "\n", " ")


def split_message(message: str, max_bytes: int = MAX_MESSAGE_BYTES) -> list[str]:
    """Split at paragraph, then line, then word breaks; cut words only as a last resort."""
    if _byte_len(message) <= max_bytes:
        return [message]
    return [part for part in _split(message, max_bytes, 0) if part.strip()]


def _split(text: str, max_bytes: int, level: int) -> list[str]:
    if _byte_len(text) <= max_bytes:
        return [text]
    if level >= len(_SEPARATORS):
        return _hard_split(text, max_bytes)

    separator = _SEPARATORS[level]
    parts: list[str] = []
    current = ""
    for piece in text.split(separator):
        candidate = f"{current}{separator}{piece}" if current else piece
        if _byte_len(candidate) <= max_bytes:
            current = candidate
            continue
        if current:
            parts.append(current)
        if _byte_len(piece) <= max_bytes:
            current = piece
        else:
            parts.extend(_split(piece, max_bytes, level + 1))
            current = ""
    if current:
        parts.append(current)
    return parts


def _hard_split(text: str, max_bytes: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for char in text:
        if _byte_len(current + char) > max_bytes:
            parts.append(current)
            current = ""
        current += char
    if current:
        parts.append(current)
    return parts


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def build_signal_sender(config: Config) -> SignalSender | None:
    if not config.signal_api_url or not config.signal_number:
        return None
    return SignalSenderHttp(
        config.signal_api_url,
        config.signal_number,
        headers=config.signal_headers(),
    )


_ERROR_DETAIL_MAX_CHARS = 500


def _error_detail(response: httpx.Response) -> str:
    """signal-cli-rest-api explains failures in an `error` field; fall back to the raw body."""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict) and data.get("error"):
        detail = str(data["error"])
    else:
        detail = response.text
    detail = " ".join(detail.split())
    if len(detail) > _ERROR_DETAIL_MAX_CHARS:
        detail = detail[:_ERROR_DETAIL_MAX_CHARS] + "..."
    return detail


def _extract_timestamp(response: httpx.Response) -> Optional[str]:
    try:
        data = response.json()
    except ValueError:
        return None

    if isinstance(data, dict):
        if "timestamp" in data:
            return str(data["timestamp"])

    return None
