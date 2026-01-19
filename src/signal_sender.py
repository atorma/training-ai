from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

from config import Config


class SignalSendError(RuntimeError):
    pass


@dataclass(frozen=True)
class SignalSendResult:
    timestamp: Optional[str] = None


class SignalSender(Protocol):
    async def send(self, message: str) -> SignalSendResult:
        ...


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
        payload = {
            "message": message,
            "number": self._number,
            "recipients": [self._number],
        }
        url = f"{self._api_url}/v2/send"

        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
                response = await client.post(url, json=payload)
                if 500 <= response.status_code < 600:
                    response = await client.post(url, json=payload)
        except httpx.RequestError as exc:
            raise SignalSendError("Signal API request failed") from exc

        if response.status_code != 201:
            raise SignalSendError(f"Signal API returned HTTP {response.status_code}")

        return SignalSendResult(timestamp=_extract_timestamp(response))


def build_signal_sender(config: Config) -> SignalSender | None:
    if not config.signal_api_url or not config.signal_number:
        return None
    return SignalSenderHttp(
        config.signal_api_url,
        config.signal_number,
        headers=config.signal_headers(),
    )


def _extract_timestamp(response: httpx.Response) -> Optional[str]:
    try:
        data = response.json()
    except ValueError:
        return None

    if isinstance(data, dict):
        if "timestamp" in data:
            return str(data["timestamp"])

    return None
