# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for portfolio_agent.mcp_client: Backend Asset MCP mediation.

These tests fake the ``mcp`` SDK's transport/session layer (never opening a
real network connection) so they can pin the exact request/response shapes
this module depends on -- see the "API stability notes" section of the
README for why this boundary is faked rather than mocked loosely.
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from portfolio_agent import mcp_client
from portfolio_agent.context import PortfolioToolContext
from portfolio_agent.telemetry import PortfolioTelemetry


def _context() -> PortfolioToolContext:
    return PortfolioToolContext("AlphaCapital", "user-1", "user-token", "service-token", "corr-1")


class _FakeClientSession:
    """Fakes ``mcp.ClientSession`` for a single ``call_tool`` invocation."""

    def __init__(self, result: CallToolResult | None = None, raise_exc: BaseException | None = None) -> None:
        self._result = result
        self._raise_exc = raise_exc
        self.initialized = False
        self.called_with: tuple[str, dict[str, Any]] | None = None

    async def __aenter__(self) -> "_FakeClientSession":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def initialize(self) -> None:
        self.initialized = True

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        self.called_with = (name, arguments)
        if self._raise_exc is not None:
            raise self._raise_exc
        assert self._result is not None
        return self._result


def _patch_transport(monkeypatch: pytest.MonkeyPatch, session: _FakeClientSession) -> None:
    @contextlib.asynccontextmanager
    async def fake_streamable_http_client(url: str, *, http_client=None):
        yield (None, None, lambda: None)

    monkeypatch.setattr(mcp_client, "streamable_http_client", fake_streamable_http_client)
    monkeypatch.setattr(mcp_client, "ClientSession", lambda *args, **kwargs: session)


class TestResolveMcpServerUri:
    def test_prefers_backend_mcp_server_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_MCP_SERVER_URL", "https://apim.example.com/mcp")
        monkeypatch.delenv("BACKEND_API_BASE_URL", raising=False)

        assert mcp_client._resolve_mcp_server_uri() == "https://apim.example.com/mcp"

    def test_falls_back_to_backend_api_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BACKEND_MCP_SERVER_URL", raising=False)
        monkeypatch.setenv("BACKEND_API_BASE_URL", "https://backend.example.com")

        assert mcp_client._resolve_mcp_server_uri() == "https://backend.example.com"

    def test_raises_when_neither_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BACKEND_MCP_SERVER_URL", raising=False)
        monkeypatch.delenv("BACKEND_API_BASE_URL", raising=False)

        with pytest.raises(RuntimeError):
            mcp_client._resolve_mcp_server_uri()


class TestExtractStatusCode:
    def test_reads_response_status_code(self) -> None:
        class _Response:
            status_code = 403

        class _Exc(Exception):
            response = _Response()

        assert mcp_client._extract_status_code(_Exc()) == 403

    def test_reads_bare_status_code_attribute(self) -> None:
        class _Exc(Exception):
            status_code = 502

        assert mcp_client._extract_status_code(_Exc()) == 502

    def test_returns_none_when_no_status_code_available(self) -> None:
        assert mcp_client._extract_status_code(ValueError("boom")) is None


@pytest.mark.asyncio
class TestListPortfolios:
    async def test_returns_success_from_structured_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_MCP_SERVER_URL", "https://apim.example.com/mcp")
        result = CallToolResult(
            content=[],
            structuredContent={
                "value": [
                    {
                        "id": "alpha-growth",
                        "tenantId": "AlphaCapital",
                        "name": "Alpha Growth Portfolio",
                        "currency": "USD",
                        "marketValue": "1000",
                        "asOfDate": "2024-05-01",
                    }
                ]
            },
            isError=False,
        )
        session = _FakeClientSession(result=result)
        _patch_transport(monkeypatch, session)

        outcome = await mcp_client.list_portfolios(_context(), PortfolioTelemetry())

        assert outcome.is_success
        assert outcome.value is not None
        assert len(outcome.value) == 1
        assert outcome.value[0].id == "alpha-growth"
        assert session.initialized
        assert session.called_with == ("listPortfolios", {"tenantId": "AlphaCapital"})

    async def test_returns_success_from_text_content_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_MCP_SERVER_URL", "https://apim.example.com/mcp")
        result = CallToolResult(
            content=[TextContent(type="text", text='[{"id": "alpha-income", "tenantId": "AlphaCapital", '
                                                     '"name": "Alpha Income", "currency": "USD", '
                                                     '"marketValue": "500", "asOfDate": "2024-05-01"}]')],
            isError=False,
        )
        session = _FakeClientSession(result=result)
        _patch_transport(monkeypatch, session)

        outcome = await mcp_client.list_portfolios(_context(), PortfolioTelemetry())

        assert outcome.is_success
        assert outcome.value[0].id == "alpha-income"

    async def test_mcp_result_error_is_bad_gateway(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_MCP_SERVER_URL", "https://apim.example.com/mcp")
        result = CallToolResult(content=[], isError=True)
        session = _FakeClientSession(result=result)
        _patch_transport(monkeypatch, session)

        outcome = await mcp_client.list_portfolios(_context(), PortfolioTelemetry())

        assert not outcome.is_success
        assert outcome.status_code == 502

    async def test_transport_403_logs_tenant_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_MCP_SERVER_URL", "https://apim.example.com/mcp")

        class _Response:
            status_code = 403

        class _ForbiddenError(Exception):
            response = _Response()

        session = _FakeClientSession(raise_exc=_ForbiddenError("forbidden"))
        _patch_transport(monkeypatch, session)

        telemetry = PortfolioTelemetry()
        logged: list[tuple[str, str, str, str]] = []
        monkeypatch.setattr(
            telemetry,
            "log_tenant_mismatch_403",
            lambda tool_name, tenant_id, user_id, correlation_id: logged.append(
                (tool_name, tenant_id, user_id, correlation_id)
            ),
        )

        outcome = await mcp_client.list_portfolios(_context(), telemetry)

        assert not outcome.is_success
        assert outcome.status_code == 403
        assert logged == [("listPortfolios", "AlphaCapital", "user-1", "corr-1")]

    async def test_transport_exception_without_status_is_bad_gateway(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_MCP_SERVER_URL", "https://apim.example.com/mcp")
        session = _FakeClientSession(raise_exc=ConnectionError("network unreachable"))
        _patch_transport(monkeypatch, session)

        outcome = await mcp_client.list_portfolios(_context(), PortfolioTelemetry())

        assert not outcome.is_success
        assert outcome.status_code == 502


@pytest.mark.asyncio
class TestGetPosition:
    async def test_returns_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_MCP_SERVER_URL", "https://apim.example.com/mcp")
        result = CallToolResult(
            content=[],
            structuredContent={
                "id": "pos-msft",
                "tenantId": "AlphaCapital",
                "portfolioId": "alpha-growth",
                "instrumentName": "Microsoft Corp",
                "assetClass": "Equity",
                "quantity": "10",
                "marketValue": "5000",
            },
            isError=False,
        )
        session = _FakeClientSession(result=result)
        _patch_transport(monkeypatch, session)

        outcome = await mcp_client.get_position(_context(), PortfolioTelemetry(), "alpha-growth", "pos-msft")

        assert outcome.is_success
        assert outcome.value.instrument_name == "Microsoft Corp"
        assert session.called_with == (
            "getPositionDetail",
            {"tenantId": "AlphaCapital", "portfolioId": "alpha-growth", "positionId": "pos-msft"},
        )
