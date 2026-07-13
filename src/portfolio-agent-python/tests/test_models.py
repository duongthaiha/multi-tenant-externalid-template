# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for portfolio_agent.models: Portfolio/Position parsing and lookup."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from portfolio_agent.models import Portfolio, Position, find_portfolio


class TestPortfolioFromDict:
    def test_parses_camel_case_backend_payload(self) -> None:
        portfolio = Portfolio.from_dict(
            {
                "id": "alpha-growth",
                "tenantId": "AlphaCapital",
                "name": "Alpha Growth Portfolio",
                "currency": "USD",
                "marketValue": "1234567.89",
                "asOfDate": "2024-05-01",
            }
        )

        assert portfolio.id == "alpha-growth"
        assert portfolio.tenant_id == "AlphaCapital"
        assert portfolio.name == "Alpha Growth Portfolio"
        assert portfolio.currency == "USD"
        assert portfolio.market_value == Decimal("1234567.89")
        assert portfolio.as_of_date == dt.date(2024, 5, 1)

    def test_tolerates_pascal_case_keys(self) -> None:
        portfolio = Portfolio.from_dict(
            {
                "Id": "alpha-growth",
                "TenantId": "AlphaCapital",
                "Name": "Alpha Growth Portfolio",
                "Currency": "USD",
                "MarketValue": 1000,
                "AsOfDate": "2024-05-01",
            }
        )

        assert portfolio.id == "alpha-growth"
        assert portfolio.market_value == Decimal("1000")

    def test_missing_fields_do_not_raise(self) -> None:
        portfolio = Portfolio.from_dict({})
        assert portfolio.id == ""
        assert portfolio.market_value == Decimal("0")


class TestPositionFromDict:
    def test_parses_camel_case_backend_payload(self) -> None:
        position = Position.from_dict(
            {
                "id": "pos-msft",
                "tenantId": "AlphaCapital",
                "portfolioId": "alpha-growth",
                "instrumentName": "Microsoft Corp",
                "assetClass": "Equity",
                "quantity": "100.5",
                "marketValue": "50250.00",
            }
        )

        assert position.id == "pos-msft"
        assert position.instrument_name == "Microsoft Corp"
        assert position.quantity == Decimal("100.5")
        assert position.market_value == Decimal("50250.00")


class TestFindPortfolio:
    def _portfolios(self) -> list[Portfolio]:
        return [
            Portfolio("alpha-growth", "AlphaCapital", "Alpha Growth Portfolio", "USD", Decimal("100"), dt.date.today()),
            Portfolio("alpha-income", "AlphaCapital", "Alpha Income Portfolio", "USD", Decimal("200"), dt.date.today()),
        ]

    def test_matches_by_exact_id(self) -> None:
        match = find_portfolio(self._portfolios(), "alpha-growth")
        assert match is not None
        assert match.id == "alpha-growth"

    def test_matches_by_exact_name_case_insensitive(self) -> None:
        match = find_portfolio(self._portfolios(), "ALPHA INCOME PORTFOLIO")
        assert match is not None
        assert match.id == "alpha-income"

    def test_matches_by_name_substring(self) -> None:
        match = find_portfolio(self._portfolios(), "growth")
        assert match is not None
        assert match.id == "alpha-growth"

    def test_returns_none_when_no_match(self) -> None:
        assert find_portfolio(self._portfolios(), "nonexistent") is None
