"""At-most-once reservation is tied to the handler's transaction."""

import unittest
from types import SimpleNamespace
from typing import Annotated
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.common.idempotency import (
    MAX_KEYED_BODY_BYTES,
    _request_hash,
    needs_http_idempotency,
    reserve_http_command,
)
from src.db.session import get_session
from src.views._generated_router import router


def fake_request(body: bytes = b'{"value":1}') -> SimpleNamespace:
    return SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/access/create_resident_request", query=""),
        body=AsyncMock(return_value=body),
    )


class IdempotencyRouteTests(unittest.TestCase):
    def test_subject_changes_have_guard_but_suggestion_and_demo_do_not(self) -> None:
        self.assertTrue(needs_http_idempotency("/access/create_resident_request", "POST"))
        self.assertTrue(needs_http_idempotency("/issues/add_comment", "POST"))
        self.assertTrue(needs_http_idempotency("/drafts/save", "POST"))
        self.assertTrue(needs_http_idempotency("/drafts/submit", "POST"))
        self.assertFalse(needs_http_idempotency("/issues/suggest", "POST"))
        self.assertFalse(needs_http_idempotency("/access/list_requests", "GET"))
        self.assertFalse(needs_http_idempotency("/demo/next_image", "POST"))

    def test_generated_router_attaches_guard_only_to_mutations(self) -> None:
        for route in router.routes:
            guarded = any(
                dependency.call is reserve_http_command
                for dependency in route.dependant.dependencies
            )
            expected = any(
                needs_http_idempotency(route.path, method) for method in route.methods
            )
            self.assertEqual(guarded, expected, route.path)

    def test_hash_binds_path_query_and_canonical_json(self) -> None:
        first = _request_hash("POST", "/access/create", "b=2&a=1", b'{"b":2,"a":1}')
        self.assertEqual(
            first,
            _request_hash("post", "/access/create", "a=1&b=2", b'{"a":1,"b":2}'),
        )
        self.assertNotEqual(first, _request_hash("POST", "/access/other", "a=1&b=2", b'{"a":1,"b":2}'))


class IdempotencyReservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_keyless_request_is_unchanged(self) -> None:
        request = fake_request()
        session = SimpleNamespace(scalar=AsyncMock(), commit=AsyncMock())
        await reserve_http_command(request, SimpleNamespace(id=uuid4()), session, None)
        request.body.assert_not_awaited()
        session.scalar.assert_not_awaited()
        session.commit.assert_not_awaited()

    async def test_first_key_is_reserved_without_commit(self) -> None:
        request = fake_request()
        session = SimpleNamespace(scalar=AsyncMock(return_value=uuid4()), commit=AsyncMock())
        await reserve_http_command(
            request, SimpleNamespace(id=uuid4()), session,
            "123e4567-e89b-42d3-a456-426614174000",
        )
        self.assertEqual(session.scalar.await_count, 1)
        session.commit.assert_not_awaited()

    async def test_repeat_is_rejected_without_running_handler(self) -> None:
        request = fake_request()
        digest = _request_hash(request.method, request.url.path, request.url.query, await request.body())
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[None, SimpleNamespace(request_hash=digest)]),
            commit=AsyncMock(),
        )
        with self.assertRaises(HTTPException) as raised:
            await reserve_http_command(
                request, SimpleNamespace(id=uuid4()), session,
                "123e4567-e89b-42d3-a456-426614174000",
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "already_processed")
        session.commit.assert_not_awaited()

    async def test_reused_key_with_other_body_is_rejected(self) -> None:
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[None, SimpleNamespace(request_hash="another-hash")])
        )
        with self.assertRaises(HTTPException) as raised:
            await reserve_http_command(
                fake_request(), SimpleNamespace(id=uuid4()), session,
                "123e4567-e89b-42d3-a456-426614174000",
            )
        self.assertEqual(raised.exception.detail["code"], "idempotency_key_reused")

    async def test_invalid_key_or_excessive_body_is_rejected_before_insert(self) -> None:
        session = SimpleNamespace(scalar=AsyncMock())
        for key, body, status in (
            ("short", b"{}", 400),
            ("123e4567-e89b-42d3-a456-426614174000", b"x" * (MAX_KEYED_BODY_BYTES + 1), 413),
        ):
            with self.subTest(status=status):
                with self.assertRaises(HTTPException) as raised:
                    await reserve_http_command(
                        fake_request(body), SimpleNamespace(id=uuid4()), session, key
                    )
                self.assertEqual(raised.exception.status_code, status)
        session.scalar.assert_not_awaited()


class IdempotencyDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_guard_and_handler_share_cached_session(self) -> None:
        actor = SimpleNamespace(id=uuid4())
        session = SimpleNamespace(scalar=AsyncMock(return_value=uuid4()), commit=AsyncMock())
        seen_sessions: list[object] = []
        session_creations = 0
        app = FastAPI()

        async def session_override():
            nonlocal session_creations
            session_creations += 1
            yield session

        async def actor_override():
            return actor

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_current_user] = actor_override

        @app.post("/access/example", dependencies=[Depends(reserve_http_command)])
        async def handler(
            current_session: Annotated[AsyncSession, Depends(get_session)],
        ):
            seen_sessions.append(current_session)
            await current_session.commit()
            return {"ok": True}

        sent = []
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": b'{"value":1}', "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        await app(
            {
                "type": "http",
                "method": "POST",
                "path": "/access/example",
                "raw_path": b"/access/example",
                "query_string": b"",
                "root_path": "",
                "scheme": "http",
                "http_version": "1.1",
                "server": ("test", 80),
                "client": ("test", 1234),
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"idempotency-key", b"123e4567-e89b-42d3-a456-426614174000"),
                ],
            },
            receive,
            send,
        )
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(session_creations, 1)
        self.assertEqual(seen_sessions, [session])
        session.scalar.assert_awaited_once()
        session.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
