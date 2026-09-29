import unittest
from unittest.mock import patch

from src.main import create_app


class AppTests(unittest.TestCase):
    def test_http_app_does_not_load_bot_configuration(self):
        with patch("src.core.config.load_dotenv") as load_dotenv:
            app = create_app()
            self.assertEqual(app.title, "Hackathon MAX API")
            load_dotenv.assert_not_called()


if __name__ == "__main__":
    unittest.main()
