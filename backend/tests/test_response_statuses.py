import inspect
import json
import unittest

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.responses import Response

from src.views._generated_router import _with_response_codes


class Result(BaseModel):
    value: int


class ResponseStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_type_selects_status_code_and_json_body(self):
        async def handler(value: int) -> Result:
            return Result(value=value)

        endpoint = _with_response_codes(handler, {Result: 201})
        response = await endpoint(7)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(json.loads(response.body), {"value": 7})
        self.assertEqual(inspect.signature(endpoint), inspect.signature(handler))

        router = APIRouter()
        router.add_api_route("/result", endpoint, methods=["GET"])
        self.assertEqual(
            [parameter.name for parameter in router.routes[0].dependant.query_params],
            ["value"],
        )

    async def test_response_is_returned_unchanged(self):
        expected = Response(status_code=204)

        async def handler() -> Response:
            return expected

        response = await _with_response_codes(handler, {})()
        self.assertIs(response, expected)

    async def test_undeclared_result_type_fails(self):
        async def handler():
            return {"value": 7}

        endpoint = _with_response_codes(handler, {Result: 200})
        with self.assertRaisesRegex(TypeError, "returned dict"):
            await endpoint()
