# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Backend Asset MCP client mediation for the portfolio agent.

Ports ``PortfolioBackendClient`` (C#, ``src/portfolio-agent/Program.cs``).

Tools call ``BACKEND_MCP_SERVER_URL`` (APIM's native MCP gateway in front of
the Backend API) exclusively -- **never** the Backend API or Cosmos DB
directly. A fresh MCP client session is opened per tool call, carrying the
caller's trusted, request-scoped headers:

- ``Authorization: Bearer <user access token>`` -- APIM/backend re-validate
  this token; the agent never trusts it locally.
- ``X-Correlation-ID`` -- propagated end to end for tracing.
- ``X-Agent-Id`` -- identifies this agent to APIM/backend diagnostics.

A new session per call (rather than a long-lived, reused MCP session) is a
deliberate choice, matching the C# implementation
(``PortfolioBackendClient.CreateMcpClientAsync`` is also called fresh per
tool invocation): the trusted headers are per-request and per-user, so the
session must not be reused across different callers.
"""

from __future__ import annotations

import os
from http import HTTPStatus
from typing import Any, Generic, Mapping, Optional, TypeVar

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Implementation, TextContent

from .constants import Headers
from .context import PortfolioToolContext
from .models import Portfolio, Position
from .telemetry import PortfolioTelemetry

_AGENT_ID = "portfolio-agent-python"
_CLIENT_INFO = Implementation(name=_AGENT_ID, version="1.0.0")
_MCP_REQUEST_TIMEOUT_SECONDS = 30.0

T = TypeVar("T")


class BackendToolResult(Generic[T]):
    """Result of a single Backend Asset MCP tool call.

    Ports ``BackendToolResult<T>`` (C#).
    """

    __slots__ = ("is_success", "status_code", "value")

    def __init__(self, is_success: bool, status_code: int, value: Optional[T]) -> None:
        self.is_success = is_success
        self.status_code = status_code
        self.value = value

    @classmethod
    def success(cls, value: T) -> "BackendToolResult[T]":
        return cls(True, HTTPStatus.OK, value)

    @classmethod
    def failure(cls, status_code: int) -> "BackendToolResult[T]":
        return cls(False, status_code, None)


def _resolve_mcp_server_uri() -> str:
    """Resolve the APIM-fronted MCP server URL.

    Ports ``PortfolioBackendClient.ResolveMcpServerUri`` (C#): prefers
    ``BACKEND_MCP_SERVER_URL``, falls back to ``BACKEND_API_BASE_URL`` (kept
    only for local-dev parity with the C# fallback), and fails closed
    (raises) if neither is set -- the agent must never silently guess an
    endpoint.
    """
    mcp_server_url = os.environ.get("BACKEND_MCP_SERVER_URL", "").strip()
    if mcp_server_url:
        return mcp_server_url

    base_address = os.environ.get("BACKEND_API_BASE_URL", "").strip()
    if base_address:
        return base_address

    raise RuntimeError("BACKEND_MCP_SERVER_URL environment variable is not set.")


def _extract_status_code(exc: BaseException) -> Optional[int]:
    """Best-effort extraction of an HTTP status code from an MCP/transport exception.

    The Python MCP client SDK (preview) does not guarantee a single typed
    exception shape for transport-level HTTP failures the way the C#
    ``HttpRequestException.StatusCode`` does, so this checks the common
    attribute shapes defensively: ``httpx.HTTPStatusError.response``,
    a bare ``status_code`` attribute, and a nested ``response.status_code``.
    Returns ``None`` when no status code can be determined (mapped to
    ``502 Bad Gateway`` by callers, matching the C# fallback).
    """
    response = getattr(exc, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            return status_code
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    return None


def _deserialize_structured(result: CallToolResult) -> Optional[Mapping[str, Any]]:
    return result.structuredContent


def _deserialize_text(result: CallToolResult) -> Optional[str]:
    texts = [block.text for block in result.content if isinstance(block, TextContent)]
    return "\n".join(texts) if texts else None


async def _call_mcp_tool(
    context: PortfolioToolContext,
    tool_name: str,
    arguments: Mapping[str, Any],
    telemetry: PortfolioTelemetry,
) -> tuple[bool, int, Optional[CallToolResult]]:
    """Open a fresh MCP session, call ``tool_name``, and return the raw result.

    Returns ``(is_success, status_code, result)``. ``result`` is ``None`` on
    failure. Errors are logged (with tenant-mismatch 403 given its own log
    semantics) but never raised -- callers convert this into a
    :class:`BackendToolResult`.
    """
    url = _resolve_mcp_server_uri()
    headers = {
        "Authorization": f"Bearer {context.user_access_token}",
        Headers.CORRELATION_ID: context.correlation_id,
        Headers.AGENT_ID: _AGENT_ID,
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=_MCP_REQUEST_TIMEOUT_SECONDS) as http_client:
            async with streamable_http_client(url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream, client_info=_CLIENT_INFO) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, dict(arguments))
    except Exception as exc:  # noqa: BLE001 - defensive: MCP/transport error shape is not fully typed
        status_code = _extract_status_code(exc)
        if status_code == HTTPStatus.FORBIDDEN:
            telemetry.log_tenant_mismatch_403(tool_name, context.tenant_id, context.user_id, context.correlation_id)
        else:
            telemetry.log_mcp_tool_exception(
                tool_name,
                context.tenant_id,
                context.correlation_id,
                type(exc).__name__,
                str(exc),
            )
        return False, status_code or HTTPStatus.BAD_GATEWAY, None

    if result.isError:
        telemetry.log_mcp_tool_failure(tool_name, context.tenant_id, context.correlation_id, "mcp-result-error")
        return False, HTTPStatus.BAD_GATEWAY, None

    return True, HTTPStatus.OK, result


async def list_portfolios(
    context: PortfolioToolContext, telemetry: PortfolioTelemetry
) -> BackendToolResult[list[Portfolio]]:
    """Call the ``listPortfolios`` Backend Asset MCP tool for the authenticated tenant."""
    is_success, status_code, result = await _call_mcp_tool(
        context, "listPortfolios", {"tenantId": context.tenant_id}, telemetry
    )
    if not is_success or result is None:
        return BackendToolResult.failure(status_code)

    value = _parse_portfolio_list(result, telemetry, "listPortfolios", context)
    if value is None:
        return BackendToolResult.failure(HTTPStatus.BAD_GATEWAY)
    return BackendToolResult.success(value)


async def get_position(
    context: PortfolioToolContext,
    telemetry: PortfolioTelemetry,
    portfolio_id: str,
    position_id: str,
) -> BackendToolResult[Position]:
    """Call the ``getPositionDetail`` Backend Asset MCP tool for the authenticated tenant."""
    is_success, status_code, result = await _call_mcp_tool(
        context,
        "getPositionDetail",
        {"tenantId": context.tenant_id, "portfolioId": portfolio_id, "positionId": position_id},
        telemetry,
    )
    if not is_success or result is None:
        return BackendToolResult.failure(status_code)

    value = _parse_position(result, telemetry, "getPositionDetail", context)
    if value is None:
        return BackendToolResult.failure(HTTPStatus.BAD_GATEWAY)
    return BackendToolResult.success(value)


def _parse_portfolio_list(
    result: CallToolResult,
    telemetry: PortfolioTelemetry,
    tool_name: str,
    context: PortfolioToolContext,
) -> Optional[list[Portfolio]]:
    import json

    structured = _deserialize_structured(result)
    raw_items: Optional[Any] = None
    if structured is not None:
        raw_items = structured.get("value", structured) if isinstance(structured, Mapping) else structured
    if raw_items is None:
        text = _deserialize_text(result)
        if not text:
            telemetry.log_mcp_tool_failure(tool_name, context.tenant_id, context.correlation_id, "empty-result")
            return None
        try:
            raw_items = json.loads(text)
        except ValueError:
            telemetry.log_mcp_tool_failure(tool_name, context.tenant_id, context.correlation_id, "unparseable-result")
            return None

    if not isinstance(raw_items, list):
        telemetry.log_mcp_tool_failure(tool_name, context.tenant_id, context.correlation_id, "unexpected-shape")
        return None

    return [Portfolio.from_dict(item) for item in raw_items if isinstance(item, Mapping)]


def _parse_position(
    result: CallToolResult,
    telemetry: PortfolioTelemetry,
    tool_name: str,
    context: PortfolioToolContext,
) -> Optional[Position]:
    import json

    structured = _deserialize_structured(result)
    raw_item: Optional[Any] = structured
    if raw_item is None:
        text = _deserialize_text(result)
        if not text:
            telemetry.log_mcp_tool_failure(tool_name, context.tenant_id, context.correlation_id, "empty-result")
            return None
        try:
            raw_item = json.loads(text)
        except ValueError:
            telemetry.log_mcp_tool_failure(tool_name, context.tenant_id, context.correlation_id, "unparseable-result")
            return None

    if not isinstance(raw_item, Mapping):
        telemetry.log_mcp_tool_failure(tool_name, context.tenant_id, context.correlation_id, "unexpected-shape")
        return None

    return Position.from_dict(raw_item)
