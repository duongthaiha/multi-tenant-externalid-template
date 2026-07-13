# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for portfolio_agent.tools: tool-level formatting, fail-closed behavior, and naming."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from http import HTTPStatus

import pytest

from portfolio_agent import mcp_client, tools
from portfolio_agent.context import PortfolioToolContext, PortfolioToolContextAccessor
from portfolio_agent.models import Portfolio, Position


def _set_context() -> PortfolioToolContext:
    context = PortfolioToolContext("AlphaCapital", "user-1", "user-token", "service-token", "corr-1")
    PortfolioToolContextAccessor.set_current(context)
    return context


class TestToolNaming:
    def test_tool_names_match_csharp_agent_and_shared_eval_datasets(self) -> None:
        assert tools.list_portfolios.name == "ListPortfolios"
        assert tools.get_portfolio_summary.name == "GetPortfolioSummary"
        assert tools.get_position_detail.name == "GetPositionDetail"
        assert tools.PORTFOLIO_TOOLS == (
            tools.list_portfolios,
            tools.get_portfolio_summary,
            tools.get_position_detail,
        )


@pytest.mark.asyncio
class TestListPortfoliosImpl:
    async def test_fails_closed_without_trusted_context(self) -> None:
        with pytest.raises(RuntimeError):
            await tools._list_portfolios_impl()

    async def test_formats_portfolio_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_context()
        portfolio = Portfolio("alpha-growth", "AlphaCapital", "Alpha Growth Portfolio", "USD", Decimal("1234567"), dt.date(2024, 5, 1))

        async def fake_list_portfolios(context, telemetry):
            return mcp_client.BackendToolResult.success([portfolio])

        monkeypatch.setattr(mcp_client, "list_portfolios", fake_list_portfolios)

        result = await tools._list_portfolios_impl()

        assert "alpha-growth" in result
        assert "Alpha Growth Portfolio" in result
        assert "USD 1,234,567" in result
        assert "2024-05-01" in result

    async def test_empty_list_returns_friendly_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_context()

        async def fake_list_portfolios(context, telemetry):
            return mcp_client.BackendToolResult.success([])

        monkeypatch.setattr(mcp_client, "list_portfolios", fake_list_portfolios)

        result = await tools._list_portfolios_impl()

        assert "No portfolios were found" in result

    async def test_forbidden_backend_response_is_safe_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_context()

        async def fake_list_portfolios(context, telemetry):
            return mcp_client.BackendToolResult.failure(HTTPStatus.FORBIDDEN)

        monkeypatch.setattr(mcp_client, "list_portfolios", fake_list_portfolios)

        result = await tools._list_portfolios_impl()

        assert result == "The backend denied this portfolio request for the authenticated tenant."


@pytest.mark.asyncio
class TestGetPortfolioSummaryImpl:
    async def test_no_match_suggests_list_portfolios(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_context()

        async def fake_list_portfolios(context, telemetry):
            return mcp_client.BackendToolResult.success([])

        monkeypatch.setattr(mcp_client, "list_portfolios", fake_list_portfolios)

        result = await tools._get_portfolio_summary_impl("nonexistent")

        assert "No portfolio matched 'nonexistent'" in result
        assert "ListPortfolios" in result

    async def test_returns_formatted_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_context()
        portfolio = Portfolio("alpha-growth", "AlphaCapital", "Alpha Growth Portfolio", "USD", Decimal("1000"), dt.date(2024, 5, 1))

        async def fake_list_portfolios(context, telemetry):
            return mcp_client.BackendToolResult.success([portfolio])

        monkeypatch.setattr(mcp_client, "list_portfolios", fake_list_portfolios)

        result = await tools._get_portfolio_summary_impl("Alpha Growth Portfolio")

        assert "Portfolio: Alpha Growth Portfolio" in result
        assert "Tenant: AlphaCapital" in result
        assert "Market value: USD 1,000" in result


@pytest.mark.asyncio
class TestGetPositionDetailImpl:
    async def test_position_not_found_is_specific_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_context()
        portfolio = Portfolio("alpha-growth", "AlphaCapital", "Alpha Growth Portfolio", "USD", Decimal("1000"), dt.date(2024, 5, 1))

        async def fake_list_portfolios(context, telemetry):
            return mcp_client.BackendToolResult.success([portfolio])

        async def fake_get_position(context, telemetry, portfolio_id, position_id):
            return mcp_client.BackendToolResult.failure(HTTPStatus.NOT_FOUND)

        monkeypatch.setattr(mcp_client, "list_portfolios", fake_list_portfolios)
        monkeypatch.setattr(mcp_client, "get_position", fake_get_position)

        result = await tools._get_position_detail_impl("Alpha Growth Portfolio", "pos-999")

        assert "does not contain a position with ID 'pos-999'" in result

    async def test_returns_formatted_position(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_context()
        portfolio = Portfolio("alpha-growth", "AlphaCapital", "Alpha Growth Portfolio", "USD", Decimal("1000"), dt.date(2024, 5, 1))
        position = Position("pos-msft", "AlphaCapital", "alpha-growth", "Microsoft Corp", "Equity", Decimal("100.5"), Decimal("50250"))

        async def fake_list_portfolios(context, telemetry):
            return mcp_client.BackendToolResult.success([portfolio])

        async def fake_get_position(context, telemetry, portfolio_id, position_id):
            assert portfolio_id == "alpha-growth"
            assert position_id == "pos-msft"
            return mcp_client.BackendToolResult.success(position)

        monkeypatch.setattr(mcp_client, "list_portfolios", fake_list_portfolios)
        monkeypatch.setattr(mcp_client, "get_position", fake_get_position)

        result = await tools._get_position_detail_impl("Alpha Growth Portfolio", "pos-msft")

        assert "Instrument: Microsoft Corp" in result
        assert "Quantity: 100.50" in result
        assert "Market value: USD 50,250" in result
