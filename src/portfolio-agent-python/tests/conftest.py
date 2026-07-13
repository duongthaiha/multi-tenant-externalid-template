# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Shared pytest fixtures for the portfolio-agent-python test suite."""

from __future__ import annotations

import pytest

from portfolio_agent.context import PortfolioToolContextAccessor


@pytest.fixture(autouse=True)
def _reset_request_scoped_state():
    """Reset request-scoped context variables between tests.

    Prevents state leaking across tests that exercise
    ``portfolio_agent.context`` directly or indirectly (tools, handler).
    """
    PortfolioToolContextAccessor.set_current(None)
    PortfolioToolContextAccessor.set_telemetry(None)
    yield
    PortfolioToolContextAccessor.set_current(None)
    PortfolioToolContextAccessor.set_telemetry(None)
