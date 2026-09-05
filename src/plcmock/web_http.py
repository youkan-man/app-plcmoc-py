from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
import json
import logging
from typing import Any, Mapping, TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlsplit

from .logging_config import level_number
from .web_state import ApiError

if TYPE_CHECKING:
    from .web_dashboard import WebDashboardServer

LOGGER = logging.getLogger("plcmock.web")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app-core.js": ("app-core.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


def handler_class(dashboard: WebDashboardServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PLCMockDashboard/0.5"
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch("PUT")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self._headers(None, 0)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            del format, args  # Dashboard polling must not recursively create logs.

        def _dispatch(self, method: str) -> None:
            try:
                parsed = urlsplit(self.path)
                if parsed.path.startswith("/api/"):
                    self._api(method, parsed.path, parse_qs(parsed.query))
                    return
                if method != "GET":
                    raise ApiError(
                        HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed"
                    )
                asset = ASSETS.get(parsed.path)
                if asset is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, "page not found")
                filename, content_type = asset
                self._bytes(
                    HTTPStatus.OK,
                    dashboard.asset(filename),
                    content_type,
                )
            except ApiError as exc:
                self._json(exc.status, {"ok": False, "error": exc.message})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                LOGGER.exception(
                    "web request failed method=%s path=%s",
                    method,
                    self.path,
                    extra={"event": "web_request_failed"},
                )
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": (
                            "internal server error: "
                            f"{type(exc).__name__}"
                        ),
                    },
                )

        def _api(
            self,
            method: str,
            path: str,
            query: Mapping[str, list[str]],
        ) -> None:
            if method == "GET" and path == "/api/health":
                self._json(HTTPStatus.OK, dashboard.health())
            elif method == "GET" and path == "/api/status":
                self._json(HTTPStatus.OK, dashboard.status())
            elif method == "GET" and path == "/api/settings":
                self._json(HTTPStatus.OK, dashboard.settings())
            elif method == "GET" and path == "/api/config/export":
                self._bytes(
                    HTTPStatus.OK,
                    dashboard.export_config(),
                    "application/yaml; charset=utf-8",
                    extra_headers={
                        "Content-Disposition": (
                            'attachment; filename="plcmock-runtime.yml"'
                        )
                    },
                )
            elif method == "GET" and path == "/api/logs":
                level = _query_text(query, "level")
                try:
                    minimum = (
                        None
                        if not level or level.lower() == "all"
                        else level_number(level.upper())
                    )
                except ValueError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
                self._json(
                    HTTPStatus.OK,
                    dashboard.log_handler.query(
                        after=_query_int(query, "after", 0, 0),
                        limit=_query_int(query, "limit", 200, 1, 500),
                        endpoint=_query_text(query, "endpoint"),
                        minimum_level=minimum,
                        search=_query_text(query, "search"),
                    ),
                )
            elif method == "POST" and path == "/api/logs/clear":
                dashboard.log_handler.clear()
                self._json(HTTPStatus.OK, {"ok": True})
            elif method == "POST" and path == "/api/metrics/reset":
                self._json(HTTPStatus.OK, dashboard.reset_metrics())
            elif method == "GET" and path == "/api/memory":
                area = _query_text(query, "area")
                if not area:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "area is required")
                self._json(
                    HTTPStatus.OK,
                    dashboard.read_memory(
                        _query_text(query, "storage") or "word",
                        area,
                        _query_int(query, "start", 0, 0),
                        _query_int(
                            query,
                            "count",
                            32,
                            1,
                            dashboard.max_memory_points,
                        ),
                    ),
                )
            elif method == "PUT" and path == "/api/memory":
                self._json(
                    HTTPStatus.OK,
                    dashboard.write_memory(self._body()),
                )
            elif method == "POST" and path == "/api/logging":
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "logging": dashboard.set_logging(self._body()),
                    },
                )
            elif path.startswith("/api/endpoints/"):
                self._endpoint_api(method, path)
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "API route not found")

        def _endpoint_api(self, method: str, path: str) -> None:
            parts = path.split("/")
            # /api/endpoints/{name}[/action]
            if len(parts) not in (4, 5) or not parts[3]:
                raise ApiError(HTTPStatus.NOT_FOUND, "endpoint API route not found")
            name = unquote(parts[3])
            if method == "PUT" and len(parts) == 4:
                self._json(
                    HTTPStatus.OK,
                    dashboard.apply_endpoint(name, self._body()),
                )
                return
            if method == "POST" and len(parts) == 5 and parts[4] == "action":
                self._json(
                    HTTPStatus.OK,
                    dashboard.endpoint_action(name, self._body().get("action")),
                )
                return
            raise ApiError(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "unsupported endpoint operation",
            )

        def _body(self) -> Mapping[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST, "invalid Content-Length"
                ) from exc
            if not 0 < length <= 1024 * 1024:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    "JSON request body is required and must be at most 1 MiB",
                )
            try:
                value = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST, "invalid JSON request body"
                ) from exc
            if not isinstance(value, Mapping):
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    "JSON body must be an object",
                )
            return value

        def _json(
            self,
            status: HTTPStatus,
            payload: Mapping[str, Any],
        ) -> None:
            self._bytes(
                status,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ).encode(),
                "application/json; charset=utf-8",
            )

        def _bytes(
            self,
            status: HTTPStatus,
            data: bytes,
            content_type: str,
            *,
            extra_headers: Mapping[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self._headers(content_type, len(data), extra_headers=extra_headers)
            self.end_headers()
            self.wfile.write(data)

        def _headers(
            self,
            content_type: str | None,
            length: int,
            *,
            extra_headers: Mapping[str, str] | None = None,
        ) -> None:
            if content_type:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; "
                "frame-ancestors 'none'",
            )
            if extra_headers:
                for name, value in extra_headers.items():
                    self.send_header(name, value)
            self.send_header("Connection", "close")

    return Handler


def _query_text(query: Mapping[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values:
        return None
    return values[-1].strip() or None


def _query_int(
    query: Mapping[str, list[str]],
    name: str,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw = _query_text(query, name)
    if raw is None:
        return default
    try:
        value = int(raw, 10)
    except ValueError as exc:
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"{name} must be an integer"
        ) from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{name} is out of range")
    return value
