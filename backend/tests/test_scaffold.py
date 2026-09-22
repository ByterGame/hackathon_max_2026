import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.bot.handlers.echo import echo_message
from src.main import create_app


class EchoTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_and_attachments_are_preserved(self):
        for text, attachments in [
            ("hello", []),
            (None, [object()]),
            ("caption", [object()]),
        ]:
            with self.subTest(text=text, attachments=bool(attachments)):
                answer = AsyncMock()
                event = SimpleNamespace(
                    message=SimpleNamespace(
                        body=SimpleNamespace(text=text, attachments=attachments),
                        answer=answer,
                    )
                )
                await echo_message(event)
                answer.assert_awaited_once_with(
                    text=text, attachments=attachments or None
                )

    async def test_empty_messages_are_ignored(self):
        for body in [None, SimpleNamespace(text=None, attachments=[])]:
            answer = AsyncMock()
            event = SimpleNamespace(message=SimpleNamespace(body=body, answer=answer))
            await echo_message(event)
            answer.assert_not_awaited()


class AppTests(unittest.TestCase):
    def test_http_app_does_not_load_bot_configuration(self):
        with patch("src.core.config.load_dotenv") as load_dotenv:
            app = create_app()
            self.assertEqual(app.title, "Hackathon MAX API")
            load_dotenv.assert_not_called()


if __name__ == "__main__":
    unittest.main()
