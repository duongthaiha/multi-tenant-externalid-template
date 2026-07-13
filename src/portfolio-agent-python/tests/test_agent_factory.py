# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for portfolio_agent.agent_factory: environment variable resolution.

These only test the pure env-var resolution helpers; building a real
:class:`agent_framework.Agent` would require a live Foundry project, which
is out of scope for a unit test.
"""

from __future__ import annotations

import pytest

from portfolio_agent import agent_factory


class TestResolveProjectEndpoint:
    def test_prefers_foundry_project_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://foundry.example.com/api/projects/p1")
        monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://ignored.example.com")

        assert agent_factory.resolve_project_endpoint() == "https://foundry.example.com/api/projects/p1"

    def test_falls_back_to_azure_ai_project_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
        monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://fallback.example.com/api/projects/p1")

        assert agent_factory.resolve_project_endpoint() == "https://fallback.example.com/api/projects/p1"

    def test_raises_when_neither_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

        with pytest.raises(RuntimeError):
            agent_factory.resolve_project_endpoint()


class TestResolveModelDeployment:
    def test_reads_azure_ai_model_deployment_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")

        assert agent_factory.resolve_model_deployment() == "gpt-4.1-mini"

    def test_raises_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)

        with pytest.raises(RuntimeError):
            agent_factory.resolve_model_deployment()


class TestResolveProjectId:
    def test_prefers_azure_ai_project_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_AI_PROJECT_ID", "/subscriptions/x/projects/p1")
        monkeypatch.setenv("AZURE_AI_FOUNDRY_PROJECT_ID", "/subscriptions/x/projects/ignored")

        assert agent_factory.resolve_project_id() == "/subscriptions/x/projects/p1"

    def test_falls_back_to_foundry_project_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AZURE_AI_PROJECT_ID", raising=False)
        monkeypatch.setenv("AZURE_AI_FOUNDRY_PROJECT_ID", "/subscriptions/x/projects/p2")

        assert agent_factory.resolve_project_id() == "/subscriptions/x/projects/p2"

    def test_returns_empty_string_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AZURE_AI_PROJECT_ID", raising=False)
        monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ID", raising=False)

        assert agent_factory.resolve_project_id() == ""
