"""Send a request and hand back just enough of the response to compare runs."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast

from minreq.interop import to_httpx2
from minreq.model import HttpRequest

DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class Response:
    status: int
    reason: str
    content_type: str
    body: bytes
    elapsed: float


class TransportError(RuntimeError):
    pass


class Transport:
    """Base transport: retries transient failures, otherwise stays out of the way."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, retries: int = 1, delay: float = 0.0):
        self.timeout = timeout
        self.retries = retries
        self.delay = delay
        self.count = 0

    def send(self, request: HttpRequest) -> Response:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            if self.delay:
                time.sleep(self.delay)
            try:
                self.count += 1
                return self._send(request)
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < self.retries:
                    time.sleep(min(2.0**attempt, 5.0))
        raise TransportError(str(last)) from last

    def _send(self, request: HttpRequest) -> Response:
        raise NotImplementedError

    def close(self) -> None:
        pass


class Httpx2Transport(Transport):
    """httpx2 with every client default header stripped, so we control the wire."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._clients: dict[tuple[bool, str | None], Any] = {}

    def _client(self, request: HttpRequest) -> Any:
        import httpx2

        key = (request.insecure, request.proxy)
        client = self._clients.get(key)
        if client is None:
            client = httpx2.Client(
                verify=not request.insecure,
                proxy=request.proxy,
                timeout=self.timeout,
                trust_env=False,
                follow_redirects=False,
            )
            client.headers.clear()
            self._clients[key] = client
        return client

    def _send(self, request: HttpRequest) -> Response:
        started = time.monotonic()
        response = self._client(request).send(
            to_httpx2(request), follow_redirects=request.follow_redirects
        )
        return Response(
            status=response.status_code,
            reason=response.reason_phrase or "",
            content_type=response.headers.get("content-type", ""),
            body=response.content,
            elapsed=time.monotonic() - started,
        )

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()


class CurlCffiTransport(Transport):
    """curl_cffi with a browser TLS fingerprint, and no headers we did not ask for."""

    def __init__(self, impersonate: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:
            raise TransportError(
                "--impersonate needs curl_cffi: install 'minimal-request[impersonate]'"
            ) from exc
        self.impersonate = impersonate
        self.session: Any = curl_requests.Session(
            impersonate=impersonate, default_headers=False, trust_env=False
        )

    def _send(self, request: HttpRequest) -> Response:
        started = time.monotonic()
        response = self.session.request(
            cast("Any", request.method),
            request.url,
            headers=list(request.wire_headers()),
            data=request.body_bytes(),
            allow_redirects=request.follow_redirects,
            verify=not request.insecure,
            timeout=self.timeout,
            proxy=request.proxy,
        )
        return Response(
            status=response.status_code,
            reason=getattr(response, "reason", "") or "",
            content_type=response.headers.get("content-type", ""),
            body=response.content,
            elapsed=time.monotonic() - started,
        )

    def close(self) -> None:
        self.session.close()


def build_transport(
    impersonate: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 1,
    delay: float = 0.0,
) -> Transport:
    options: dict[str, Any] = {"timeout": timeout, "retries": retries, "delay": delay}
    if impersonate:
        return CurlCffiTransport(impersonate=impersonate, **options)
    return Httpx2Transport(**options)
