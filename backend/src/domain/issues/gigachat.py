"""Optional GigaChat transport for issue suggestions.

The caller owns the prompt and validates the model output. This module never
logs response bodies, credentials, access tokens, or resident text.
"""

import asyncio
import hashlib
import json
import logging
import ssl
import time
from dataclasses import dataclass
from typing import Callable, TypeVar
from uuid import uuid4

import aiohttp

DEFAULT_AUTH_URL = "https://api.giga.chat/api/v2/oauth"
LEGACY_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://api.giga.chat/v1/chat/completions"
DEFAULT_MODEL = "GigaChat-2-Pro"
_ALLOWED_AUTH_URLS = frozenset({DEFAULT_AUTH_URL, LEGACY_AUTH_URL})
_TOTAL_TIMEOUT_SECONDS = 10
_TOKEN_EXPIRY_MARGIN_SECONDS = 60
_logger = logging.getLogger(__name__)
_Result = TypeVar("_Result")


@dataclass(frozen=True)
class _Token:
    value: str
    expires_at: float


class _ProviderFailure(Exception):
    def __init__(self, code: str, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


_token_cache: dict[tuple[bytes, str, str], _Token] = {}
_token_lock = asyncio.Lock()


def _scope(value: str) -> str:
    raw = value.strip().upper()
    if raw in {"PERS", "B2B", "CORP"}:
        return f"GIGACHAT_API_{raw}"
    if raw in {"GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}:
        return raw
    raise _ProviderFailure("invalid_scope")


def _ssl_option(ca_bundle: str) -> bool | ssl.SSLContext:
    # A custom CA extends normal certificate verification; it never disables it.
    if not ca_bundle.strip():
        return True
    try:
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=ca_bundle.strip())
        return context
    except (OSError, ValueError):
        raise _ProviderFailure("invalid_ca_bundle") from None


def _cache_key(auth_key: str, scope: str, auth_url: str) -> tuple[bytes, str, str]:
    return hashlib.sha256(auth_key.encode("utf-8")).digest(), scope, auth_url


async def _access_token(
    client: aiohttp.ClientSession,
    *,
    auth_key: str,
    auth_url: str,
    scope: str,
    ssl_option: bool | ssl.SSLContext,
) -> str:
    cache_key = _cache_key(auth_key, scope, auth_url)
    async with _token_lock:
        cached = _token_cache.get(cache_key)
        if (
            cached is not None
            and cached.expires_at > time.time() + _TOKEN_EXPIRY_MARGIN_SECONDS
        ):
            return cached.value

        basic_key = auth_key.strip()
        if basic_key.lower().startswith("basic "):
            basic_key = basic_key[6:].strip()
        async with client.post(
            auth_url,
            headers={
                "Authorization": f"Basic {basic_key}",
                "RqUID": str(uuid4()),
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"scope": scope},
            ssl=ssl_option,
        ) as response:
            if response.status != 200:
                raise _ProviderFailure("auth_http_status", response.status)
            payload = await response.json()

        if not isinstance(payload, dict):
            raise _ProviderFailure("invalid_auth_response")
        token = payload.get("access_token")
        expiry = payload.get("expires_at")
        if (
            not isinstance(token, str)
            or not token
            or isinstance(expiry, bool)
            or not isinstance(expiry, (int, float))
        ):
            raise _ProviderFailure("invalid_auth_response")
        # GigaChat deployments have returned both Unix seconds and milliseconds.
        expires_at = float(expiry) / 1000 if expiry > 100_000_000_000 else float(expiry)
        if expires_at <= time.time() + _TOKEN_EXPIRY_MARGIN_SECONDS:
            raise _ProviderFailure("invalid_auth_response")
        _token_cache[cache_key] = _Token(token, expires_at)
        return token


async def _completion(
    client: aiohttp.ClientSession,
    *,
    token: str,
    body: dict[str, object],
    ssl_option: bool | ssl.SSLContext,
) -> tuple[int, object | None]:
    async with client.post(
        CHAT_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        json=body,
        ssl=ssl_option,
    ) as response:
        if response.status != 200:
            return response.status, None
        payload = await response.json()
    if not isinstance(payload, dict):
        raise _ProviderFailure("invalid_response", 200)
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise _ProviderFailure("invalid_response", 200) from None
    if not isinstance(content, str):
        raise _ProviderFailure("invalid_response", 200)
    return 200, json.loads(content)


async def gigachat_suggestion(
    *,
    auth_key: str,
    auth_url: str,
    scope: str,
    model: str,
    ca_bundle: str,
    messages: list[dict[str, str]],
    response_schema: dict[str, object],
    validate: Callable[[object], _Result | None],
) -> _Result | None:
    started = time.monotonic()
    _logger.info(
        "issue_suggestion_gigachat",
        extra={
            "event": "issue_suggestion_gigachat",
            "phase": "attempt",
            "result": "started",
        },
    )
    try:
        if auth_url not in _ALLOWED_AUTH_URLS:
            raise _ProviderFailure("invalid_auth_url")
        normalized_scope = _scope(scope)
        ssl_option = _ssl_option(ca_bundle)
        body: dict[str, object] = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 512,
            "response_format": {
                "type": "json_schema",
                "schema": response_schema,
                "strict": True,
            },
        }
        timeout = aiohttp.ClientTimeout(total=_TOTAL_TIMEOUT_SECONDS)
        async with asyncio.timeout(_TOTAL_TIMEOUT_SECONDS):
            async with aiohttp.ClientSession(timeout=timeout) as client:
                token = await _access_token(
                    client,
                    auth_key=auth_key,
                    auth_url=auth_url,
                    scope=normalized_scope,
                    ssl_option=ssl_option,
                )
                refreshed = False

                async def complete_with_refresh(
                    request_body: dict[str, object],
                ) -> tuple[int, object | None]:
                    nonlocal token, refreshed
                    status, payload = await _completion(
                        client, token=token, body=request_body, ssl_option=ssl_option
                    )
                    if status == 401:
                        _token_cache.pop(
                            _cache_key(auth_key, normalized_scope, auth_url), None
                        )
                        if not refreshed:
                            refreshed = True
                            token = await _access_token(
                                client,
                                auth_key=auth_key,
                                auth_url=auth_url,
                                scope=normalized_scope,
                                ssl_option=ssl_option,
                            )
                            status, payload = await _completion(
                                client,
                                token=token,
                                body=request_body,
                                ssl_option=ssl_option,
                            )
                            if status == 401:
                                _token_cache.pop(
                                    _cache_key(auth_key, normalized_scope, auth_url),
                                    None,
                                )
                    return status, payload

                status, payload = await complete_with_refresh(body)
                if status in {400, 422}:
                    # Some account/model combinations reject JSON schema output.
                    # Retry once with the same bounded data and a plain JSON instruction.
                    plain_messages = [
                        {
                            **messages[0],
                            "content": messages[0]["content"]
                            + " Верни один JSON-объект без Markdown по этой схеме: "
                            + json.dumps(response_schema, ensure_ascii=False),
                        },
                        *messages[1:],
                    ]
                    plain_body = {**body, "messages": plain_messages}
                    plain_body.pop("response_format")
                    status, payload = await complete_with_refresh(plain_body)
                if status != 200:
                    raise _ProviderFailure("http_status", status)
                result = validate(payload)
                if result is None:
                    raise _ProviderFailure("invalid_response", 200)
    except _ProviderFailure as error:
        code, status_code, exception_type = error.code, error.status_code, None
    except TimeoutError:
        code, status_code, exception_type = "timeout", None, "TimeoutError"
    except (
        aiohttp.ClientError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
    ) as error:
        code = (
            "request_error"
            if isinstance(error, aiohttp.ClientError)
            else "invalid_response"
        )
        status_code, exception_type = None, type(error).__name__
    else:
        _logger.info(
            "issue_suggestion_gigachat",
            extra={
                "event": "issue_suggestion_gigachat",
                "phase": "complete",
                "result": "success",
                "status_code": 200,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            },
        )
        return result

    _logger.warning(
        "issue_suggestion_gigachat",
        extra={
            "event": "issue_suggestion_gigachat",
            "phase": "complete",
            "result": "fallback",
            "error_code": code,
            "status_code": status_code,
            "exception_type": exception_type,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        },
    )
    return None
