"""零第三方依赖的 HTTP 服务：ThreadingHTTPServer + 路由表 + chunked SSE。"""

from __future__ import annotations

import json
import logging
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, unquote, urlparse

from .config import settings
from .errors import AppError, NotFoundError
from .security import check_token

logger = logging.getLogger("agent.http")

MAX_BODY_BYTES = 20 * 1024 * 1024


class Request:
    def __init__(self, method: str, path: str, query: dict[str, list[str]], headers: dict[str, str], body: bytes, runtime: Any):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
        self.runtime = runtime
        self.client_ip = ""
        self.identity: dict[str, Any] | None = None

    def header(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name.lower(), default)

    def q(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        return values[0] if values else default

    def q_int(self, name: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
        try:
            value = int(self.q(name) or default)
        except ValueError:
            value = default
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        return value

    def q_bool(self, name: str, default: bool = False) -> bool:
        raw = (self.q(name) or "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return default

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AppError(f"请求体不是合法 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise AppError("请求体必须是 JSON 对象")
        return data


class Response:
    def __init__(
        self,
        status: int = 200,
        body: Any = None,
        headers: dict[str, str] | None = None,
        content_type: str = "application/json; charset=utf-8",
    ):
        self.status = status
        self.headers = headers or {}
        self.content_type = content_type
        if body is None:
            self.payload = b""
        elif isinstance(body, bytes):
            self.payload = body
        elif isinstance(body, str):
            self.payload = body.encode("utf-8")
        else:
            self.payload = json.dumps(body, ensure_ascii=False).encode("utf-8")


class StreamResponse:
    def __init__(self, iterator: Iterable[str], content_type: str = "text/event-stream; charset=utf-8"):
        self.iterator = iterator
        self.content_type = content_type
        self.on_close: Callable[[], None] | None = None


def json_response(body: Any, status: int = 200) -> Response:
    return Response(status=status, body=body)


Handler = Callable[[Request, dict[str, str]], Response | StreamResponse]


class Router:
    def __init__(self) -> None:
        self._routes: list[tuple[str, re.Pattern[str], Handler, bool]] = []

    def add(self, method: str, pattern: str, handler: Handler, auth: bool = True) -> None:
        normalized = pattern.rstrip("/") or "/"
        regex = "^" + re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", r"(?P<\1>[^/]+)", normalized) + "$"
        self._routes.append((method.upper(), re.compile(regex), handler, auth))

    def dispatch(self, request: Request) -> Response | StreamResponse:
        path = request.path.rstrip("/") or "/"
        for method, regex, handler, auth in self._routes:
            if method != request.method:
                continue
            match = regex.match(path)
            if not match:
                continue
            if auth:
                service = getattr(request.runtime, "auth", None)
                if service is not None:
                    service.require(request)
                else:
                    check_token(request.header("x-api-token"))
            params = {key: unquote(value) for key, value in match.groupdict().items()}
            return handler(request, params)
        raise NotFoundError(f"未知接口：{request.method} {path}")


def _make_handler(router: Router):
    class AgentHTTPHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "AgentAPI/0.1"

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle("DELETE")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors_headers()
            self.send_header("Allow", "GET, POST, DELETE, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug("%s - %s", self.address_string(), fmt % args)

        def _cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Token, Authorization, X-API-Key")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")

        def _handle(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            headers = {key.lower(): value for key, value in self.headers.items()}
            length_header = headers.get("content-length") or "0"
            try:
                length = int(length_header)
            except ValueError:
                length = 0
            if length > MAX_BODY_BYTES:
                self._send(Response(413, {"ok": False, "error": "请求体过大"}))
                return
            body = self.rfile.read(length) if length > 0 else b""
            request = Request(method, path, query, headers, body, self.server.runtime)  # type: ignore[attr-defined]
            request.client_ip = str(self.client_address[0] or "")

            try:
                response = router.dispatch(request)
            except AppError as exc:
                response = Response(exc.status_code, {"ok": False, "error": exc.message, "code": exc.code})
            except Exception as exc:  # noqa: BLE001
                logger.error("未处理异常：%s\n%s", exc, traceback.format_exc())
                response = Response(500, {"ok": False, "error": f"内部错误：{type(exc).__name__}: {exc}"})

            if isinstance(response, StreamResponse):
                self._stream(response)
            else:
                self._send(response)

        def _send(self, response: Response) -> None:
            try:
                self.send_response(response.status)
                self.send_header("Content-Type", response.content_type)
                self.send_header("Content-Length", str(len(response.payload)))
                self._cors_headers()
                for key, value in response.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if response.payload:
                    self.wfile.write(response.payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _stream(self, response: StreamResponse) -> None:
            try:
                self.send_response(200)
                self.send_header("Content-Type", response.content_type)
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self._cors_headers()
                self.end_headers()
                for chunk in response.iterator:
                    data = chunk.encode("utf-8")
                    self.wfile.write(f"{len(data):x}\r\n".encode("ascii") + data + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                if response.on_close is not None:
                    try:
                        response.on_close()
                    except Exception:  # noqa: BLE001
                        pass

    return AgentHTTPHandler


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], router: Router, runtime: Any):
        self.router = router
        self.runtime = runtime
        super().__init__(address, _make_handler(router))


def build_server(runtime: Any, host: str | None = None, port: int | None = None) -> AgentHTTPServer:
    from .api import build_router
    from .webui import mount as mount_webui

    router = build_router(runtime)
    mount_webui(router)
    return AgentHTTPServer((host or settings.host, port or settings.port), router, runtime)


def serve(runtime: Any, host: str | None = None, port: int | None = None) -> None:
    server = build_server(runtime, host, port)
    actual_host, actual_port = server.server_address[:2]
    logger.info("listening http://%s:%s (docs 见 README)", actual_host, actual_port)
    print(f"Agent API 已启动：http://{actual_host}:{actual_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在退出...")
    finally:
        server.server_close()
