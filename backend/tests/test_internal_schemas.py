import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from tools.codegen.contracts import ContractError, load_contracts
from tools.codegen.render import render_models


class InternalSchemaTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def write_document(self, relative, document):
        path = self.root / "docs" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(document), encoding="utf-8")

    def write_related_schemas(self):
        self.write_document(
            "request/internal/models.yaml",
            {
                "schemas": {
                    "RequestStatus": {"type": "string", "enum": ["new", "closed"]},
                    "RequestData": {
                        "type": "object",
                        "required": ["status"],
                        "properties": {"status": {"$ref": "#/schemas/RequestStatus"}},
                    },
                }
            },
        )

    def test_local_reference_generates_one_module(self):
        self.write_related_schemas()
        schemas = load_contracts(self.root).specification["components"]["schemas"]
        self.assertEqual(
            set(schemas),
            {
                "gen.request.internal.models.RequestStatus",
                "gen.request.internal.models.RequestData",
            },
        )
        self.assertEqual(
            schemas["gen.request.internal.models.RequestData"]["properties"]["status"],
            {"$ref": "#/components/schemas/gen.request.internal.models.RequestStatus"},
        )
        files = render_models(load_contracts(self.root))
        modules = {
            path: code for path, code in files.items() if path.name != "__init__.py"
        }
        self.assertEqual(set(modules), {Path("gen/request/internal/models.py")})
        code = modules[Path("gen/request/internal/models.py")]
        self.assertIn("class RequestStatus(", code)
        self.assertIn("class RequestData(", code)
        self.assertIn("status: RequestStatus", code)

    def test_api_can_reference_a_schema_in_internal_group(self):
        self.write_related_schemas()
        self.write_document(
            "request/api/get_request.yaml",
            {
                "path": "/request/client/get_request",
                "method": "get",
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/schemas/Response200"}
                            }
                        },
                    }
                },
                "schemas": {
                    "Response200": {
                        "type": "object",
                        "properties": {
                            "data": {
                                "$ref": "../internal/models.yaml#/schemas/RequestData"
                            }
                        },
                    }
                },
            },
        )
        schemas = load_contracts(self.root).specification["components"]["schemas"]
        self.assertEqual(
            schemas["gen.request.api.client.get_request.Response200"]["properties"][
                "data"
            ],
            {"$ref": "#/components/schemas/gen.request.internal.models.RequestData"},
        )

    def test_unknown_schema_reference_fails(self):
        self.write_document(
            "request/internal/models.yaml",
            {
                "schemas": {
                    "RequestData": {
                        "type": "object",
                        "properties": {"status": {"$ref": "#/schemas/Missing"}},
                    }
                }
            },
        )
        with self.assertRaisesRegex(ContractError, "неизвестная ссылка"):
            load_contracts(self.root)

    def test_api_module_uses_url_domain_not_document_directory(self):
        document = {
            "path": "/inventory/client/list_items",
            "method": "get",
            "responses": {"204": {"description": "No content"}},
        }
        self.write_document("catalog/api/list_items.yaml", document)
        endpoint = load_contracts(self.root).endpoints[0]
        self.assertEqual(endpoint.module, "views.inventory.client.list_items")
        self.assertEqual(endpoint.models_module, "gen.inventory.api.client.list_items")
        self.assertEqual(endpoint.operation["tags"], ["catalog"])

        document["tags"] = ["public_inventory"]
        self.write_document("catalog/api/list_items.yaml", document)
        endpoint = load_contracts(self.root).endpoints[0]
        self.assertEqual(endpoint.operation["tags"], ["public_inventory"])

    def test_one_response_model_cannot_have_two_status_codes(self):
        self.write_document(
            "catalog/api/list_items.yaml",
            {
                "path": "/catalog/client/list_items",
                "method": "get",
                "responses": {
                    status: {
                        "description": "Result",
                        "content": {
                            "application/json": {"schema": {"$ref": "#/schemas/Result"}}
                        },
                    }
                    for status in ("200", "202")
                },
                "schemas": {"Result": {"type": "object"}},
            },
        )
        with self.assertRaisesRegex(
            ContractError, "статус нельзя определить по типу модели"
        ):
            load_contracts(self.root)

    def test_reference_to_internal_schema_in_another_domain(self):
        self.write_related_schemas()
        self.write_document(
            "report/internal/result.yaml",
            {
                "schemas": {
                    "Result": {
                        "type": "object",
                        "properties": {
                            "request": {
                                "$ref": "../../request/internal/models.yaml#/schemas/RequestData"
                            }
                        },
                    }
                }
            },
        )
        schemas = load_contracts(self.root).specification["components"]["schemas"]
        self.assertEqual(
            schemas["gen.report.internal.result.Result"]["properties"]["request"],
            {"$ref": "#/components/schemas/gen.request.internal.models.RequestData"},
        )
        files = render_models(load_contracts(self.root))
        self.assertIn(
            "from src.gen.request.internal.models import RequestData",
            files[Path("gen/report/internal/result.py")],
        )

    def test_internal_cannot_reference_api_in_any_domain(self):
        self.write_document(
            "request/api/get_request.yaml",
            {
                "path": "/request/client/get_request",
                "method": "get",
                "responses": {"204": {"description": "No content"}},
                "schemas": {"Response200": {"type": "object"}},
            },
        )
        for domain, reference in (
            ("request", "../api/get_request.yaml#/schemas/Response200"),
            ("report", "../../request/api/get_request.yaml#/schemas/Response200"),
        ):
            with self.subTest(domain=domain):
                self.write_document(
                    f"{domain}/internal/result.yaml",
                    {
                        "schemas": {
                            "Result": {
                                "type": "object",
                                "properties": {"data": {"$ref": reference}},
                            }
                        }
                    },
                )
                with self.assertRaisesRegex(
                    ContractError, f"{domain}/internal/result.yaml: внутренняя схема"
                ):
                    load_contracts(self.root)


if __name__ == "__main__":
    unittest.main()
