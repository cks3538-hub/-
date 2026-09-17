"""adapters 시험 공용 mock HTTP 서버 (http.server, 127.0.0.1 임의 포트). 네트워크 외부 접근 없음.

다른 test_adapters_*.py / test_security_network.py 가 `from test_adapters_mockserver import MockServer` 로 사용한다.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit


@dataclass
class Recorded:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: Any
    raw: bytes


@dataclass
class Scripted:
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)


Responder = Callable[[Recorded], Scripted]


class MockServer:
    """queue 방식(enqueue) 또는 responder 함수 방식으로 응답을 정한다. 요청은 requests 에 기록된다."""

    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self.queue: list[Scripted] = []
        self.responder: Responder | None = None
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                n = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except ValueError:
                    body = raw.decode("utf-8", errors="replace")
                parts = urlsplit(self.path)
                rec = Recorded(
                    method=self.command,
                    path=parts.path,
                    query=parse_qs(parts.query),
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=body,
                    raw=raw,
                )
                with outer._lock:
                    outer.requests.append(rec)
                    if outer.responder is not None:
                        resp = outer.responder(rec)
                    elif outer.queue:
                        resp = outer.queue.pop(0)
                    else:
                        resp = Scripted(500, {"error": "no scripted response"})
                data: bytes
                if resp.body is None:
                    data = b""
                elif isinstance(resp.body, bytes):
                    data = resp.body
                elif isinstance(resp.body, str):
                    data = resp.body.encode("utf-8")
                else:
                    data = json.dumps(resp.body, ensure_ascii=False).encode("utf-8")
                self.send_response(resp.status)
                self.send_header("content-type", resp.headers.get("content-type", "application/json"))
                for k, v in resp.headers.items():
                    if k.lower() != "content-type":
                        self.send_header(k, v)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                if data:
                    self.wfile.write(data)

            do_GET = _handle
            do_POST = _handle
            do_PUT = _handle
            do_DELETE = _handle

            def log_message(self, *args: Any) -> None:  # 시험 출력 소음 제거
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> MockServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> MockServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ------------------------------------------------------------------ helpers
    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def enqueue(self, status: int = 200, body: Any = None, headers: dict[str, str] | None = None) -> None:
        with self._lock:
            self.queue.append(Scripted(status, body, dict(headers or {})))

    def set_responder(self, fn: Responder | None) -> None:
        with self._lock:
            self.responder = fn

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()
            self.queue.clear()
            self.responder = None


def test_mock_server_records_requests_and_serves_queue() -> None:
    import http.client

    with MockServer() as srv:
        srv.enqueue(201, {"ok": True}, {"x-test": "1"})
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        conn.request("POST", "/p?a=1", body=json.dumps({"k": "v"}), headers={"content-type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 201 and resp.getheader("x-test") == "1"
        assert json.loads(resp.read()) == {"ok": True}
        conn.close()
        assert len(srv.requests) == 1
        rec = srv.requests[0]
        assert rec.method == "POST" and rec.path == "/p" and rec.query == {"a": ["1"]} and rec.body == {"k": "v"}
        # queue 소진 시 500
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        conn.request("GET", "/x")
        assert conn.getresponse().status == 500
        conn.close()
