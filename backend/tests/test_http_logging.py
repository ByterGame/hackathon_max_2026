import io
import json
import logging
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from src.core.logging import JsonLogFormatter, current_request_id
from src.http_logging import HTTPLoggingMiddleware
from src.http_logging import logger as http_logger
from src.main import app_lifespan, create_app
from src.main import logger as app_logger
from src.views.auth.verify_contact import verify_contact


async def call_app(
    app: FastAPI,
    path: str,
    *,
    method: str = "GET",
    query: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    body: bytes = b"",
) -> list[dict]:
    sent: list[dict] = []
    received = False

    async def receive() -> dict:
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "root_path": "",
            "scheme": "http",
            "http_version": "1.1",
            "server": ("test", 80),
            "client": ("test", 1234),
            "headers": headers or [],
        },
        receive,
        send,
    )
    return sent


def response_header(messages: list[dict], name: bytes) -> bytes | None:
    for key, value in messages[0]["headers"]:
        if key.lower() == name:
            return value
    return None


class HTTPLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_failure_logs_sanitized_error_text(self) -> None:
        app = create_app()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLogFormatter())
        app_logger.addHandler(handler)
        secret = "private-database-password"
        try:
            with (
                patch.dict(os.environ, {"DB_PASSWORD": secret}),
                patch(
                    "src.main.load_database_config",
                    side_effect=ValueError(f"DB_PASSWORD={secret} is invalid"),
                ),
                patch.object(app_logger, "propagate", False),
            ):
                with self.assertRaises(ValueError):
                    async with app_lifespan(app):
                        pass
        finally:
            app_logger.removeHandler(handler)
        event = json.loads(stream.getvalue())
        self.assertEqual(event["event"], "http_process_failed")
        self.assertEqual(event["phase"], "create_database_engine")
        self.assertNotIn(secret, stream.getvalue())

    async def test_success_logs_route_and_correlation_without_request_data(
        self,
    ) -> None:
        app = FastAPI()
        app.add_middleware(HTTPLoggingMiddleware)
        seen_request_ids: list[str | None] = []

        @app.post("/example")
        async def example():
            seen_request_ids.append(current_request_id.get())
            return {"ok": True}

        request_id = str(uuid4())
        with patch("src.http_logging.logger") as logger:
            sent = await call_app(
                app,
                "/example",
                method="POST",
                query=b"token=query-secret",
                headers=[
                    (b"x-request-id", request_id.encode()),
                    (b"authorization", b"header-secret"),
                ],
                body=b"phone=body-secret",
            )
        event = logger.info.call_args.kwargs["extra"]
        self.assertEqual(response_header(sent, b"x-request-id"), request_id.encode())
        self.assertEqual(event["request_id"], request_id)
        self.assertEqual(seen_request_ids, [request_id])
        self.assertIsNone(current_request_id.get())
        self.assertEqual(event["method"], "POST")
        self.assertEqual(event["path"], "/example")
        self.assertEqual(event["status_code"], 200)
        self.assertGreaterEqual(event["duration_ms"], 0)
        for secret in ("query-secret", "header-secret", "body-secret"):
            self.assertNotIn(secret, str(event))

    async def test_domain_error_code_is_extracted_without_message(self) -> None:
        app = FastAPI()
        app.add_middleware(HTTPLoggingMiddleware)

        @app.post("/domain")
        async def domain():
            return JSONResponse(
                status_code=409,
                content={"code": "stale_revision", "message": "phone=private-value"},
            )

        with patch("src.http_logging.logger") as logger:
            sent = await call_app(app, "/domain", method="POST")
        event = logger.warning.call_args.kwargs["extra"]
        self.assertEqual(sent[0]["status"], 409)
        self.assertEqual(event["error_code"], "stale_revision")
        self.assertNotIn("private-value", str(event))

    async def test_untrusted_numeric_code_is_not_logged(self) -> None:
        app = FastAPI()
        app.add_middleware(HTTPLoggingMiddleware)

        @app.get("/error")
        async def error():
            return JSONResponse(status_code=400, content={"code": "79991234567"})

        with patch("src.http_logging.logger") as logger:
            await call_app(app, "/error")
        event = logger.warning.call_args.kwargs["extra"]
        self.assertEqual(event["error_code"], "http_400")
        self.assertNotIn("79991234567", str(event))

    async def test_http_exception_code_is_extracted_without_detail(self) -> None:
        app = FastAPI()
        app.add_middleware(HTTPLoggingMiddleware)

        @app.get("/error")
        async def error():
            raise HTTPException(
                403, detail={"code": "forbidden", "message": "token=private"}
            )

        with patch("src.http_logging.logger") as logger:
            await call_app(app, "/error")
        event = logger.warning.call_args.kwargs["extra"]
        self.assertEqual(event["error_code"], "forbidden")
        self.assertNotIn("private", str(event))

    async def test_explicit_safe_client_error_text_is_logged(self) -> None:
        app = FastAPI()
        app.add_middleware(HTTPLoggingMiddleware)

        @app.get("/contact")
        async def contact(request: Request):
            request.state.error_code = "expired_max_contact_data"
            request.state.exception_message = "Expired MAX contact data"
            raise HTTPException(400, detail="Expired MAX contact data")

        with patch("src.http_logging.logger") as logger:
            await call_app(app, "/contact")
        event = logger.warning.call_args.kwargs["extra"]
        self.assertEqual(event["error_code"], "expired_max_contact_data")
        self.assertEqual(event["exception_message"], "Expired MAX contact data")

    async def test_bad_or_duplicate_request_id_is_replaced(self) -> None:
        app = FastAPI()
        app.add_middleware(HTTPLoggingMiddleware)

        @app.get("/ok")
        async def ok():
            return {"ok": True}

        with patch("src.http_logging.logger") as logger:
            sent = await call_app(
                app,
                "/ok",
                headers=[(b"x-request-id", b"secret"), (b"x-request-id", b"another")],
            )
        generated = response_header(sent, b"x-request-id").decode()
        self.assertEqual(str(UUID(generated)), generated)
        self.assertEqual(logger.info.call_args.kwargs["extra"]["request_id"], generated)

    async def test_unmatched_path_does_not_log_attacker_supplied_segment(self) -> None:
        app = FastAPI()
        app.add_middleware(HTTPLoggingMiddleware)

        with patch("src.http_logging.logger") as logger:
            await call_app(app, "/token-secret")
        event = logger.warning.call_args.kwargs["extra"]
        self.assertEqual(event["path"], "<unmatched>")
        self.assertNotIn("token-secret", str(event))

    async def test_health_success_is_quiet_but_failure_is_logged(self) -> None:
        app = create_app()
        with patch("src.http_logging.logger") as logger:
            sent = await call_app(app, "/service/health")
        self.assertEqual(sent[0]["status"], 200)
        logger.info.assert_not_called()

        async def broken_health(request):
            return JSONResponse(status_code=503, content={"code": "health_failed"})

        app.router.routes.insert(
            0, Route("/service/health", broken_health, methods=["GET"])
        )
        with patch("src.http_logging.logger") as logger:
            sent = await call_app(app, "/service/health")
        self.assertEqual(sent[0]["status"], 503)
        self.assertEqual(
            logger.error.call_args.kwargs["extra"]["error_code"], "health_failed"
        )

    async def test_static_success_is_quiet(self) -> None:
        with TemporaryDirectory() as directory:
            Path(directory, "index.html").write_text("ok")
            app = FastAPI()
            app.add_middleware(HTTPLoggingMiddleware)
            app.mount("/", StaticFiles(directory=directory, html=True))
            with patch("src.http_logging.logger") as logger:
                sent = await call_app(app, "/")
            self.assertEqual(sent[0]["status"], 200)
            logger.info.assert_not_called()

    async def test_unexpected_error_logs_safe_stack_and_reraises(self) -> None:
        app = create_app()

        async def broken(request):
            raise RuntimeError("database unavailable")

        app.router.routes.insert(0, Route("/broken", broken, methods=["GET"]))
        with patch("src.http_logging.logger") as logger:
            with self.assertRaises(RuntimeError):
                await call_app(app, "/broken")
        event = logger.error.call_args.kwargs["extra"]
        self.assertEqual(event["status_code"], 500)
        self.assertEqual(event["error_code"], "internal_error")
        self.assertEqual(event["exception_type"], "RuntimeError")
        self.assertEqual(event["exception_message"], "database unavailable")
        self.assertTrue(any("broken" in frame for frame in event["stack"]))
        self.assertLessEqual(len(event["stack"]), 12)

    async def test_exception_message_is_visible_but_configured_secret_is_redacted(
        self,
    ) -> None:
        app = create_app()
        secret = "secret-token-value-123"
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLogFormatter())

        async def broken(request):
            raise RuntimeError(f"database unavailable; BOT_TOKEN={secret}")

        app.router.routes.insert(0, Route("/broken", broken, methods=["GET"]))
        http_logger.addHandler(handler)
        try:
            with (
                patch.dict(os.environ, {"BOT_TOKEN": secret}),
                patch.object(http_logger, "propagate", False),
            ):
                with self.assertRaises(RuntimeError):
                    await call_app(app, "/broken")
        finally:
            http_logger.removeHandler(handler)
        entry = json.loads(stream.getvalue())
        self.assertIn("database unavailable", entry["exception_message"])
        self.assertNotIn(secret, stream.getvalue())

    async def test_handled_server_exception_has_stack_without_exception_text(
        self,
    ) -> None:
        app = create_app()

        async def broken(request):
            raise HTTPException(503, detail="upstream unavailable")

        app.router.routes.insert(0, Route("/broken", broken, methods=["GET"]))
        with patch("src.http_logging.logger") as logger:
            sent = await call_app(app, "/broken")
        event = logger.error.call_args.kwargs["extra"]
        self.assertEqual(sent[0]["status"], 503)
        self.assertEqual(event["exception_type"], "HTTPException")
        self.assertIn("upstream unavailable", event["exception_message"])
        self.assertTrue(event["stack"])

    async def test_verify_contact_marks_expired_signature_data_with_safe_code(
        self,
    ) -> None:
        request = SimpleNamespace(state=SimpleNamespace())
        body = SimpleNamespace(
            phone="79991234567", auth_date="old", signature="private"
        )
        session = SimpleNamespace(rollback=AsyncMock())
        with (
            patch(
                "src.views.auth.verify_contact.apply_verified_contact",
                new_callable=AsyncMock,
            ) as apply,
            patch(
                "src.views.auth.verify_contact.load_bot_token", return_value="private"
            ),
        ):
            apply.side_effect = ValueError("Expired MAX contact data")
            with self.assertRaises(HTTPException) as raised:
                await verify_contact(
                    request, body, session, SimpleNamespace(max_user_id="1")
                )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(request.state.error_code, "expired_max_contact_data")
        self.assertEqual(request.state.exception_message, "Expired MAX contact data")
        session.rollback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
