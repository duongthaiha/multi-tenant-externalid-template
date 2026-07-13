# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for portfolio_agent.main: app assembly and safe startup logging."""

from __future__ import annotations

from typing import Any

import pytest
from azure.ai.agentserver.responses import ResponsesAgentServerHost

from portfolio_agent import main as main_module
from portfolio_agent.handler import PortfolioAgentRuntime
from portfolio_agent.telemetry import PortfolioTelemetry


class _FakeAgent:
    name = "portfolio-agent-python"

    def create_session(self, *, session_id: str) -> Any:
        raise NotImplementedError

    async def run(self, message: str, *, session: Any = None) -> Any:
        raise NotImplementedError


class _FakeSessionStore:
    async def get_session(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def save_session(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def close(self) -> None:
        return None


class TestProjectHostForLogging:
    def test_returns_only_scheme_and_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://my-account.services.ai.azure.com/api/projects/p1")
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

        host = main_module._project_host_for_logging()

        assert host == "my-account.services.ai.azure.com"
        # Never leaks the project path/id into logs.
        assert "projects" not in host

    def test_returns_placeholder_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

        assert main_module._project_host_for_logging() == "(not set)"


class TestModelDeploymentForLogging:
    def test_returns_placeholder_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)

        assert main_module._model_deployment_for_logging() == "(not set)"


class TestCreateApp:
    def test_builds_host_and_registers_handler_without_network_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)

        runtime = PortfolioAgentRuntime(
            agent=_FakeAgent(),
            telemetry=PortfolioTelemetry(),
            session_store=_FakeSessionStore(),
        )

        app = main_module.create_app(runtime=runtime, configure_observability=None)

        assert isinstance(app, ResponsesAgentServerHost)
