# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tenant portfolio/position data models.

Ports ``Portfolio`` and ``Position`` from
``Contoso.AssetManagement.Shared`` (``src/shared/Models/TenantDataModels.cs``).
These are the shapes returned by the Backend API's MCP tools
(``listPortfolios`` / ``getPositionDetail``) through the APIM MCP gateway.
The agent never talks to the Backend API or Cosmos DB directly.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional


def _get_ci(data: Mapping[str, Any], *keys: str) -> Any:
    """Case-insensitive lookup across a small set of candidate key spellings.

    Backend MCP tool results may be serialized as camelCase (the
    ``JsonSerializerDefaults.Web`` convention the C# backend/agent use) or, in
    some MCP client/server implementations, PascalCase. Trying both keeps
    this parser robust to either without guessing at a single canonical case.
    """
    for key in keys:
        if key in data:
            return data[key]
    lowered = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None:
            return value
    return None


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _to_date(value: Any) -> dt.date:
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            pass
    return dt.date.today()


@dataclass(frozen=True)
class Portfolio:
    id: str
    tenant_id: str
    name: str
    currency: str
    market_value: Decimal
    as_of_date: dt.date

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Portfolio":
        return cls(
            id=str(_get_ci(data, "id") or ""),
            tenant_id=str(_get_ci(data, "tenantId") or ""),
            name=str(_get_ci(data, "name") or ""),
            currency=str(_get_ci(data, "currency") or ""),
            market_value=_to_decimal(_get_ci(data, "marketValue")),
            as_of_date=_to_date(_get_ci(data, "asOfDate")),
        )


@dataclass(frozen=True)
class Position:
    id: str
    tenant_id: str
    portfolio_id: str
    instrument_name: str
    asset_class: str
    quantity: Decimal
    market_value: Decimal

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Position":
        return cls(
            id=str(_get_ci(data, "id") or ""),
            tenant_id=str(_get_ci(data, "tenantId") or ""),
            portfolio_id=str(_get_ci(data, "portfolioId") or ""),
            instrument_name=str(_get_ci(data, "instrumentName") or ""),
            asset_class=str(_get_ci(data, "assetClass") or ""),
            quantity=_to_decimal(_get_ci(data, "quantity")),
            market_value=_to_decimal(_get_ci(data, "marketValue")),
        )


def find_portfolio(portfolios: list[Portfolio], value: str) -> Optional[Portfolio]:
    """Match a portfolio by ID (exact) or name (exact or substring), case-insensitive.

    Ports ``PortfolioTools.FindPortfolio`` (C#).
    """
    needle = value.strip().lower()
    for portfolio in portfolios:
        if portfolio.id.lower() == needle or portfolio.name.lower() == needle:
            return portfolio
    for portfolio in portfolios:
        if needle in portfolio.name.lower():
            return portfolio
    return None
