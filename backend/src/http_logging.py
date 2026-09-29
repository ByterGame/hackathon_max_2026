"""Safe, structured HTTP request logging and correlation IDs."""

import json
import logging
import re
import time
import traceback
from typing import Any
from uuid import UUID, uuid4

from .core.logging import current_request_id, safe_exception_message

logger = logging.getLogger("src.http")
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_METHOD = re.compile(r"[A-Z]{1,16}\Z")
_MAX_ERROR_BODY_BYTES = 2048
_QUIET_SUCCESS_PATHS = frozenset({"/service/health", "/service/ready"})


def _request_id(headers: list[tuple[bytes, bytes]]) -> str:
    incoming = [value for name, value in headers if name.lower() == b"x-request-id"]
    if len(incoming) == 1:
        try:
            return str(UUID(incoming[0].decode("ascii")))
        except (UnicodeDecodeError, ValueError):
            pass
    return str(uuid4())


def _safe_code(value: Any) -> str | None:
    if isinstance(value, str) and _ERROR_CODE.fullmatch(value):
        return value
    return None


def _safe_method(value: Any) -> str:
    if isinstance(value, str) and _METHOD.fullmatch(value):
        return value
    return "<invalid>"


def _response_error_code(body: bytes) -> str | None:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    code = _safe_code(payload.get("code"))
    if code is not None:
        return code
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return _safe_code(detail.get("code"))
    return None


def _safe_stack(error: Exception) -> list[str]:
    # Traceback.format_exception includes exception messages (including SQL
    # parameters). Frame locations retain the useful call path without them.
    return [
        f"{frame.filename}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(error.__traceback__)[-12:]
    ]


class HTTPLoggingMiddleware:
    """Emit one completion event per HTTP request without request contents."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope.get("headers", []))
        context_token = current_request_id.set(request_id)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        started_at = time.monotonic()
        status_code = 500
        response_started = False
        json_error_response = False
        error_body = bytearray()
        error_body_complete = False
        error: Exception | None = None

        async def send_with_request_id(message: dict) -> None:
            nonlocal status_code, response_started, json_error_response, error_body_complete
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = message.get("headers", [])
                content_types = [
                    value.split(b";", 1)[0].strip().lower()
                    for name, value in headers
                    if name.lower() == b"content-type"
                ]
                json_error_response = 400 <= status_code and any(
                    value in (b"application/json", b"application/problem+json")
                    for value in content_types
                )
                message["headers"] = [
                    (name, value)
                    for name, value in headers
                    if name.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode("ascii"))]
            elif message["type"] == "http.response.body" and json_error_response:
                chunk = message.get("body", b"")
                if len(error_body) + len(chunk) <= _MAX_ERROR_BODY_BYTES:
                    error_body.extend(chunk)
                    error_body_complete = not message.get("more_body", False)
                else:
                    json_error_response = False
                    error_body.clear()
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as caught:
            error = caught
            raise
        finally:
            # The router supplies a route template. Unmatched paths are
            # attacker-controlled and may contain a phone number or token.
            route = scope.get("route")
            path = getattr(route, "path", None) or "<unmatched>"
            # StaticFiles does not put its matched mount in scope["route"].
            # In this app it is the only source of successful unmatched paths.
            quiet_success = (
                error is None
                and status_code < 400
                and (path in _QUIET_SUCCESS_PATHS or path == "<unmatched>")
            )
            if not quiet_success:
                context: dict[str, Any] = {
                    "event": "http_request",
                    "request_id": request_id,
                    "method": _safe_method(scope.get("method")),
                    "path": path,
                    "status_code": status_code if response_started else 500,
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 2),
                }
                if status_code >= 400 or error is not None:
                    if error is not None:
                        context["error_code"] = "internal_error"
                    else:
                        code = _safe_code(state.get("error_code"))
                        if code is None and json_error_response and error_body_complete:
                            code = _response_error_code(error_body)
                        context["error_code"] = code or f"http_{status_code}"
                    if 400 <= status_code < 500:
                        known_message = state.get("exception_message")
                        if isinstance(known_message, str):
                            context["exception_message"] = safe_exception_message(
                                ValueError(known_message)
                            )
                cause = error or state.get("http_exception")
                if isinstance(cause, Exception) and (
                    error is not None or status_code >= 500
                ):
                    context["exception_type"] = type(cause).__name__
                    context["exception_message"] = safe_exception_message(cause)
                    context["stack"] = _safe_stack(cause)
                if error is not None or status_code >= 500:
                    logger.error("http_request", extra=context)
                elif status_code >= 400:
                    logger.warning("http_request", extra=context)
                else:
                    logger.info("http_request", extra=context)
            current_request_id.reset(context_token)
