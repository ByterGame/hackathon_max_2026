import unittest

from src.main import create_app


class RuntimeOpenAPITests(unittest.TestCase):
    def test_protected_routes_describe_max_launch_authentication(self) -> None:
        schema = create_app().openapi()

        security_schemes = schema["components"]["securitySchemes"]
        self.assertEqual(
            security_schemes["APIKeyHeader"],
            {
                "type": "apiKey",
                "in": "header",
                "name": "X-Max-Init-Data",
                "description": (
                    "Подписанные данные запуска мини-приложения MAX. "
                    "Получаются при открытии приложения в MAX и действуют один час."
                ),
            },
        )
        self.assertEqual(
            schema["paths"]["/issues/create_card"]["post"]["security"],
            [{"APIKeyHeader": []}],
        )
        self.assertNotIn("security", schema["paths"]["/service/health"]["get"])


if __name__ == "__main__":
    unittest.main()
