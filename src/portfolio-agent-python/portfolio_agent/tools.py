# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Portfolio Q&A tool functions bound to the hosted agent.

Ports ``PortfolioTools`` (C#, ``src/portfolio-agent/Program.cs``). Every tool:

1. Resolves the trusted, request-scoped :class:`PortfolioToolContext`
   (fails closed if it is missing or incomplete -- see
   :func:`portfolio_agent.context.require_tool_context`).
2. Starts a tool span and records invocation/miss counters.
3. Calls the Backend Asset MCP tool through ``BACKEND_MCP_SERVER_URL`` --
   never the Backend API or Cosmos DB directly.
4. Renders a plain-text answer for the model, or a safe, non-leaking failure
   message when the backend call fails.

Tool names are pinned to ``ListPortfolios`` / ``GetPortfolioSummary`` /
``GetPositionDetail`` (matching the C# agent's registered function names)
because the shared Foundry evaluation datasets under
``src/portfolio-agent/datasets`` assert on these exact tool-call names for
both hosted-agent profiles.
"""

from __future__ import annotations

from decimal import Decimal
from http import HTTPStatus
from typing import Annotated

from agent_framework import tool
from pydantic import Field

from . import mcp_client
from .context import PortfolioToolContextAccessor, require_tool_context
from .models import find_portfolio
from .telemetry import PortfolioTelemetry


def _format_money(value: Decimal, currency: str) -> str:
    return f"{currency} {value:,.0f}"


def _format_usd(value: Decimal) -> str:
    return f"USD {value:,.0f}"


def _to_tool_failure(status_code: int, message: str) -> str:
    if status_code == HTTPStatus.FORBIDDEN:
        return "The backend denied this portfolio request for the authenticated tenant."
    if status_code == HTTPStatus.UNAUTHORIZED:
        return "The backend could not authenticate this portfolio request."
    return message


def _telemetry() -> PortfolioTelemetry:
    telemetry = PortfolioToolContextAccessor.get_telemetry()
    return telemetry if telemetry is not None else PortfolioTelemetry()


async def _list_portfolios_impl() -> str:
    context = require_tool_context("ListPortfolios")
    telemetry = _telemetry()
    with telemetry.start_tool_span("ListPortfolios", context.tenant_id, context.correlation_id):
        telemetry.record_tool_invocation("ListPortfolios")
        telemetry.log_tool_invocation("ListPortfolios", context.tenant_id, "all")

        result = await mcp_client.list_portfolios(context, telemetry)
        if not result.is_success or result.value is None:
            telemetry.record_tool_miss("ListPortfolios")
            return _to_tool_failure(result.status_code, "I could not list portfolios for the authenticated tenant.")

        if len(result.value) == 0:
            return "No portfolios were found for the authenticated tenant."

        lines = [
            f"{portfolio.id}: {portfolio.name} - total value "
            f"{_format_money(portfolio.market_value, portfolio.currency)} as of {portfolio.as_of_date.isoformat()}."
            for portfolio in result.value
        ]
        return "\n".join(lines)


async def _get_portfolio_summary_impl(
    portfolio: Annotated[str, Field(description="Portfolio name or ID in the authenticated tenant.")],
) -> str:
    context = require_tool_context("GetPortfolioSummary")
    telemetry = _telemetry()
    with telemetry.start_tool_span("GetPortfolioSummary", context.tenant_id, context.correlation_id):
        telemetry.record_tool_invocation("GetPortfolioSummary")
        telemetry.log_tool_invocation("GetPortfolioSummary", context.tenant_id, portfolio)

        result = await mcp_client.list_portfolios(context, telemetry)
        if not result.is_success or result.value is None:
            telemetry.record_tool_miss("GetPortfolioSummary")
            return _to_tool_failure(
                result.status_code, f"I could not retrieve portfolio '{portfolio}' for the authenticated tenant."
            )

        match = find_portfolio(result.value, portfolio)
        if match is None:
            telemetry.record_tool_miss("GetPortfolioSummary")
            return (
                f"No portfolio matched '{portfolio}' for the authenticated tenant. "
                "Use ListPortfolios to see available portfolio IDs and names."
            )

        return (
            f"Portfolio: {match.name}\n"
            f"ID: {match.id}\n"
            f"Tenant: {match.tenant_id}\n"
            f"Currency: {match.currency}\n"
            f"Market value: {_format_money(match.market_value, match.currency)}\n"
            f"As of: {match.as_of_date.isoformat()}"
        )


async def _get_position_detail_impl(
    portfolio: Annotated[str, Field(description="Portfolio name or ID in the authenticated tenant.")],
    position_id: Annotated[str, Field(description="Position ID within the portfolio.")],
) -> str:
    context = require_tool_context("GetPositionDetail")
    telemetry = _telemetry()
    with telemetry.start_tool_span("GetPositionDetail", context.tenant_id, context.correlation_id):
        telemetry.record_tool_invocation("GetPositionDetail")
        telemetry.log_tool_invocation("GetPositionDetail", context.tenant_id, f"{portfolio}:{position_id}")

        portfolios = await mcp_client.list_portfolios(context, telemetry)
        if not portfolios.is_success or portfolios.value is None:
            telemetry.record_tool_miss("GetPositionDetail")
            return _to_tool_failure(
                portfolios.status_code, f"I could not retrieve portfolio '{portfolio}' for the authenticated tenant."
            )

        match = find_portfolio(portfolios.value, portfolio)
        if match is None:
            telemetry.record_tool_miss("GetPositionDetail")
            return f"No portfolio matched '{portfolio}' for the authenticated tenant."

        position = await mcp_client.get_position(context, telemetry, match.id, position_id)
        if not position.is_success or position.value is None:
            telemetry.record_tool_miss("GetPositionDetail")
            if position.status_code == HTTPStatus.NOT_FOUND:
                return (
                    f"Portfolio '{match.name}' does not contain a position with ID '{position_id}' "
                    "for the authenticated tenant."
                )
            return _to_tool_failure(
                position.status_code, f"I could not retrieve position '{position_id}' for the authenticated tenant."
            )

        return (
            f"Portfolio: {match.name}\n"
            f"Position ID: {position.value.id}\n"
            f"Instrument: {position.value.instrument_name}\n"
            f"Asset class: {position.value.asset_class}\n"
            f"Quantity: {position.value.quantity:,.2f}\n"
            f"Market value: {_format_usd(position.value.market_value)}"
        )


list_portfolios = tool(
    _list_portfolios_impl,
    name="ListPortfolios",
    description="List portfolios for the authenticated tenant only.",
)
get_portfolio_summary = tool(
    _get_portfolio_summary_impl,
    name="GetPortfolioSummary",
    description="Get a summary for one portfolio in the authenticated tenant by portfolio name or ID.",
)
get_position_detail = tool(
    _get_position_detail_impl,
    name="GetPositionDetail",
    description=(
        "Get a position detail from a portfolio in the authenticated tenant by portfolio name or ID "
        "and position ID."
    ),
)

PORTFOLIO_TOOLS = (list_portfolios, get_portfolio_summary, get_position_detail)
