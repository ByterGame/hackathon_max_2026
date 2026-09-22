"""Сборка документов ручек и внутренних схем в OpenAPI 3.1."""

import keyword
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag

import yaml
from openapi_spec_validator import validate
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError


class ContractError(ValueError):
    pass


class UniqueKeyLoader(yaml.SafeLoader):
    """Повторный ключ YAML — ошибка, а не молчаливая замена значения."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in result:
                raise ContractError(f"Повторный ключ YAML: {key!r}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        raise ContractError(f"Ожидается Python-имя без точек и дефисов: {value!r}")
    if keyword.iskeyword(value):
        raise ContractError(f"Имя зарезервировано Python: {value}")
    return value


def read_document(path: Path) -> dict[str, Any]:
    try:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (yaml.YAMLError, OSError) as error:
        raise ContractError(f"{path}: {error}") from error
    if not isinstance(document, dict):
        raise ContractError(f"{path}: корнем YAML должен быть объект")
    return document


@dataclass
class Endpoint:
    source: Path
    path: str
    method: str
    module: str
    operation: dict[str, Any]
    schemas: dict[str, Any]
    body_model: str | None
    has_query: bool

    @property
    def function(self) -> str:
        return self.module.rsplit(".", 1)[-1]

    @property
    def models_module(self) -> str:
        domain, *remaining = self.module.removeprefix("views.").split(".")
        return ".".join(("gen", domain, "api", *remaining))


@dataclass
class Contracts:
    specification: dict[str, Any]
    endpoints: list[Endpoint]


def load_contracts(root: Path, *, validate_specification: bool = True) -> Contracts:
    docs = root / "docs"
    components: dict[str, Any] = {}
    references: dict[tuple[Path, str], str] = {}
    document_kinds: dict[Path, str] = {}
    pending: list[tuple[str, Path, dict[str, Any]]] = []
    endpoints: list[Endpoint] = []
    paths: dict[str, Any] = {}
    operation_ids: set[str] = set()

    def add_schema(source: Path, fragment: str, module: str, name: str, schema: Any):
        identifier(name)
        if not isinstance(schema, dict):
            raise ContractError(f"{source}: схема {name} должна быть объектом")
        qualified = f"{module}.{name}"
        if qualified in components:
            raise ContractError(f"Две схемы попадают в {qualified}")
        components[qualified] = None
        references[source.resolve(), fragment] = qualified
        pending.append((qualified, source, schema))

    for source in sorted(docs.glob("*/internal/*.yaml")):
        domain, name = identifier(source.parent.parent.name), identifier(source.stem)
        document_kinds[source.resolve()] = "internal"
        document = read_document(source)
        schemas = document.get("schemas")
        if not isinstance(schemas, dict) or not schemas:
            raise ContractError(
                f"{source}: нужен непустой раздел schemas с именованными схемами"
            )
        for schema_name, schema in schemas.items():
            add_schema(
                source,
                f"/schemas/{schema_name}",
                f"gen.{domain}.internal.{name}",
                schema_name,
                schema,
            )

    for source in sorted(docs.glob("*/api/*.yaml")):
        document_domain = identifier(source.parent.parent.name)
        identifier(source.stem)
        document_kinds[source.resolve()] = "api"
        document = read_document(source)
        path = document.get("path", "")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ContractError(f"{source}: нужен абсолютный URL-путь в поле path")
        segments = [identifier(segment) for segment in path[1:].split("/")]
        if path in paths:
            raise ContractError(
                f"{source}: пока поддерживается одна ручка на URL {path}"
            )
        for existing in paths:
            if path.startswith(existing + "/") or existing.startswith(path + "/"):
                raise ContractError(
                    f"Конфликт файла и каталога ручек: {path}, {existing}"
                )
        method = document.get("method", "")
        if method not in {"get", "post", "put", "patch", "delete"}:
            raise ContractError(
                f"{source}: method должен быть get/post/put/patch/delete"
            )
        module = "views." + ".".join(segments)
        operation = {
            k: v for k, v in document.items() if k not in {"path", "method", "schemas"}
        }
        operation_id = operation.setdefault("operationId", "_".join(segments))
        if not isinstance(operation_id, str) or operation_id in operation_ids:
            raise ContractError(f"{source}: operationId должен быть уникальной строкой")
        operation_ids.add(operation_id)
        operation.setdefault("tags", [document_domain])
        schemas = document.get("schemas", {})
        if not isinstance(schemas, dict):
            raise ContractError(f"{source}: schemas должен быть объектом")
        schemas = dict(schemas)

        # Пока только query-параметры с обычной form/explode сериализацией.
        properties, required = {}, []
        for parameter in operation.get("parameters", []):
            if not isinstance(parameter, dict) or parameter.get("in") != "query":
                raise ContractError(
                    f"{source}: пока поддерживаются только query-параметры"
                )
            allowed = {
                "name",
                "in",
                "required",
                "description",
                "schema",
                "style",
                "explode",
            }
            if (
                set(parameter) - allowed
                or parameter.get("style", "form") != "form"
                or parameter.get("explode", True) is not True
            ):
                raise ContractError(f"{source}: неподдержанный формат query-параметра")
            name = identifier(parameter.get("name"))
            if name in properties:
                raise ContractError(f"{source}: повторный параметр {name}")
            parameter_schema = parameter.get("schema")
            if not isinstance(parameter_schema, dict):
                raise ContractError(f"{source}: у параметра {name} нужна schema")
            properties[name] = dict(parameter_schema)
            if "description" in parameter:
                properties[name]["description"] = parameter["description"]
            if parameter.get("required"):
                required.append(name)
        if properties:
            if "QueryParams" in schemas:
                raise ContractError(
                    f"{source}: имя QueryParams зарезервировано для parameters"
                )
            schemas["QueryParams"] = {
                "type": "object",
                "properties": properties,
                "required": required,
            }

        def local_model(content):
            if not isinstance(content, dict) or set(content) != {"application/json"}:
                raise ContractError(f"{source}: поддерживается только application/json")
            schema = content["application/json"].get("schema", {})
            ref = schema.get("$ref", "")
            if (
                set(schema) != {"$ref"}
                or not ref.startswith("#/schemas/")
                or ref[10:] not in schemas
            ):
                raise ContractError(
                    f"{source}: тело должно ссылаться на #/schemas/<имя>"
                )
            return ref[10:]

        body_model = None
        if "requestBody" in operation:
            body = operation["requestBody"]
            if not isinstance(body, dict) or body.get("required") is not True:
                raise ContractError(
                    f"{source}: пока requestBody поддерживается только с required: true"
                )
            body_model = local_model(body.get("content"))
        responses = operation.get("responses", {})
        if not isinstance(responses, dict) or not responses:
            raise ContractError(f"{source}: нужны responses")
        response_models = {}
        for status, response in responses.items():
            if not isinstance(status, str) or not re.fullmatch(
                r"[1-5][0-9]{2}", status
            ):
                raise ContractError(
                    f"{source}: статус ответа должен быть строкой, например '200'"
                )
            if (
                not isinstance(response, dict)
                or "$ref" in response
                or "headers" in response
                or "links" in response
            ):
                raise ContractError(
                    f"{source}: нужны явные ответы без headers/links/$ref"
                )
            if "content" in response:
                response_model = local_model(response["content"])
                previous_status = response_models.get(response_model)
                if previous_status is not None:
                    raise ContractError(
                        f"{source}: схема {response_model} используется для ответов "
                        f"{previous_status} и {status}; статус нельзя определить по типу модели"
                    )
                response_models[response_model] = status
        if not any(status.startswith("2") for status in responses):
            raise ContractError(f"{source}: нужен хотя бы один успешный ответ 2xx")
        if any(key in operation for key in ("security", "callbacks", "servers")):
            raise ContractError(
                f"{source}: security/callbacks/servers требуют отдельной реализации"
            )
        endpoint = Endpoint(
            source,
            path,
            method,
            module,
            operation,
            schemas,
            body_model,
            bool(properties),
        )
        for name, schema in schemas.items():
            add_schema(source, f"/schemas/{name}", endpoint.models_module, name, schema)
        endpoints.append(endpoint)
        paths[path] = {method: operation}

    def rewrite(value: Any, source: Path) -> Any:
        if isinstance(value, list):
            return [rewrite(item, source) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key in {"$id", "$anchor", "$dynamicRef", "$dynamicAnchor"}:
                raise ContractError(f"{source}: {key} пока не поддерживается")
            if key != "$ref":
                # Содержимое примеров и значений — данные, не ссылки на схемы.
                result[key] = (
                    item
                    if key in {"example", "examples", "default", "enum", "const"}
                    else rewrite(item, source)
                )
                continue
            if not isinstance(item, str):
                raise ContractError(f"{source}: $ref должен быть строкой")
            filename, fragment = urldefrag(item)
            if ":" in filename:
                raise ContractError(
                    f"{source}: внешние URL в $ref пока не поддерживаются"
                )
            target = (
                (source.parent / filename).resolve() if filename else source.resolve()
            )
            if (
                document_kinds.get(source.resolve()) == "internal"
                and document_kinds.get(target) == "api"
            ):
                raise ContractError(
                    f"{source}: внутренняя схема не должна зависеть от HTTP-схемы"
                )
            qualified = references.get((target, fragment))
            if qualified is None:
                raise ContractError(f"{source}: неизвестная ссылка {item}")
            result[key] = f"#/components/schemas/{qualified}"
        return result

    for qualified, source, schema in pending:
        converted = rewrite(schema, source)
        # Модуль задаётся именем компонента, а не пользовательским title.
        converted.pop("title", None)
        components[qualified] = converted
    for endpoint in endpoints:
        paths[endpoint.path][endpoint.method] = rewrite(
            endpoint.operation, endpoint.source
        )
    specification = {
        "openapi": "3.1.0",
        "info": {"title": "Hackathon MAX API", "version": "0.1.0"},
        "paths": paths,
        "components": {"schemas": components},
    }
    if validate_specification:
        try:
            validate(specification)
        except OpenAPIValidationError as error:
            raise ContractError(f"Некорректный OpenAPI: {error}") from error
    return Contracts(specification, endpoints)
