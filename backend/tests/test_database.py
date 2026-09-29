import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from src.core.config import load_database_config
from src.main import create_app
from src.views import router


class DatabaseConfigTests(unittest.TestCase):
    def test_requires_all_database_settings(self):
        variables = {
            "DB_HOST": "db",
            "DB_PORT": "5432",
            "DB_NAME": "app",
            "DB_USER": "app_user",
            "DB_PASSWORD": "secret",
        }
        for missing in variables:
            with self.subTest(missing=missing):
                without_setting = variables.copy()
                del without_setting[missing]
                with patch.dict(os.environ, without_setting, clear=True), patch(
                    "src.core.config.load_dotenv"
                ):
                    with self.assertRaises(RuntimeError):
                        load_database_config()


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_queries_database(self):
        app = create_app()
        session = SimpleNamespace(scalar=AsyncMock(return_value=1))
        context = AsyncMock()
        context.__aenter__.return_value = session
        app.state.db_session_factory = lambda: context
        request = Request({"type": "http", "app": app})
        ready = next(
            route.endpoint for route in router.routes if route.path == "/service/ready"
        )

        response = await ready(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), {"status": "ok"})
        session.scalar.assert_awaited_once()
        self.assertEqual(str(session.scalar.await_args.args[0]), "SELECT 1")

    async def test_ready_returns_503_when_database_is_unavailable(self):
        app = create_app()
        session = SimpleNamespace(scalar=AsyncMock(side_effect=OSError("connection lost")))
        context = AsyncMock()
        context.__aenter__.return_value = session
        app.state.db_session_factory = lambda: context
        request = Request({"type": "http", "app": app})
        ready = next(
            route.endpoint for route in router.routes if route.path == "/service/ready"
        )

        response = await ready(request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), {"status": "unavailable"})


if __name__ == "__main__":
    unittest.main()
