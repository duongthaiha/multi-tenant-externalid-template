# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for portfolio_agent.memory: Cosmos-backed conversation session storage.

Fakes the ``azure-cosmos`` async client/container so no real Cosmos account
is required, while using the real ``CosmosResourceNotFoundError`` /
``CosmosHttpResponseError`` exception types so the except-clauses in
``portfolio_agent.memory`` are exercised faithfully.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import pytest
from agent_framework import AgentSession
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError

from portfolio_agent.context import PortfolioToolContext
from portfolio_agent.memory import AgentMemoryOptions, CosmosAgentSessionStore
from portfolio_agent.telemetry import PortfolioTelemetry


class _FakeContainer:
    def __init__(self) -> None:
        self.upserted: list[dict[str, Any]] = []
        self._read_item_result: Optional[dict[str, Any]] = None
        self._read_item_exception: Optional[BaseException] = None
        self._upsert_exception: Optional[BaseException] = None

    def set_read_item_result(self, result: dict[str, Any]) -> None:
        self._read_item_result = result

    def set_read_item_exception(self, exc: BaseException) -> None:
        self._read_item_exception = exc

    def set_upsert_exception(self, exc: BaseException) -> None:
        self._upsert_exception = exc

    async def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        if self._read_item_exception is not None:
            raise self._read_item_exception
        assert self._read_item_result is not None
        return self._read_item_result

    async def upsert_item(self, body: dict[str, Any]):
        if self._upsert_exception is not None:
            raise self._upsert_exception
        self.upserted.append(body)
        return body


class _FakeDatabase:
    def __init__(self, container: _FakeContainer) -> None:
        self._container = container

    def get_container_client(self, name: str) -> _FakeContainer:
        return self._container


class _FakeCosmosClient:
    def __init__(self) -> None:
        self.databases: dict[str, _FakeDatabase] = {}
        self.closed = False

    def get_database_client(self, name: str) -> _FakeDatabase:
        if name not in self.databases:
            self.databases[name] = _FakeDatabase(_FakeContainer())
        return self.databases[name]

    async def close(self) -> None:
        self.closed = True


def _context(tenant_id: str = "AlphaCapital") -> PortfolioToolContext:
    return PortfolioToolContext(tenant_id, "user-1", "user-token", "service-token", "corr-1")


def _options() -> AgentMemoryOptions:
    return AgentMemoryOptions(
        endpoint="https://fake-cosmos.documents.azure.com:443/",
        database_prefix="agent-memory-python-",
        container_name="agentSessions",
        session_ttl_seconds=2592000,
    )


class TestAgentMemoryOptionsFromEnv:
    def test_requires_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CONTOSO_AGENT_MEMORY_ENDPOINT", raising=False)

        with pytest.raises(RuntimeError):
            AgentMemoryOptions.from_env()

    def test_defaults_to_python_specific_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONTOSO_AGENT_MEMORY_ENDPOINT", "https://cosmos.example.com:443/")
        monkeypatch.delenv("CONTOSO_AGENT_MEMORY_DATABASE_PREFIX", raising=False)

        options = AgentMemoryOptions.from_env()

        assert options.database_prefix == "agent-memory-python-"
        # Never falls back to the C# agent's shared prefix.
        assert options.database_prefix != "agent-memory-"

    def test_reads_all_values_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONTOSO_AGENT_MEMORY_ENDPOINT", "https://cosmos.example.com:443/")
        monkeypatch.setenv("CONTOSO_AGENT_MEMORY_DATABASE_PREFIX", "agent-memory-python-")
        monkeypatch.setenv("CONTOSO_AGENT_MEMORY_CONTAINER_NAME", "customSessions")
        monkeypatch.setenv("CONTOSO_AGENT_MEMORY_SESSION_TTL_SECONDS", "60")

        options = AgentMemoryOptions.from_env()

        assert options.endpoint == "https://cosmos.example.com:443/"
        assert options.container_name == "customSessions"
        assert options.session_ttl_seconds == 60


@pytest.mark.asyncio
class TestCosmosAgentSessionStore:
    async def test_get_session_returns_none_when_tenant_missing(self) -> None:
        store = CosmosAgentSessionStore(_FakeCosmosClient(), _options())
        context = PortfolioToolContext("", "user-1", "token", "service-token", "corr-1")

        session = await store.get_session(context, "portfolio-agent-python", "conv-1", PortfolioTelemetry())

        assert session is None

    async def test_get_session_returns_none_when_not_found(self) -> None:
        cosmos_client = _FakeCosmosClient()
        store = CosmosAgentSessionStore(cosmos_client, _options())
        context = _context()
        database = cosmos_client.get_database_client(f"agent-memory-python-{context.tenant_id.lower()}")
        container = database.get_container_client("agentSessions")
        container.set_read_item_exception(CosmosResourceNotFoundError(status_code=404, message="not found"))

        session = await store.get_session(context, "portfolio-agent-python", "conv-1", PortfolioTelemetry())

        assert session is None

    async def test_get_session_logs_and_returns_none_on_cosmos_error(self) -> None:
        cosmos_client = _FakeCosmosClient()
        store = CosmosAgentSessionStore(cosmos_client, _options())
        context = _context()
        database = cosmos_client.get_database_client(f"agent-memory-python-{context.tenant_id.lower()}")
        container = database.get_container_client("agentSessions")
        container.set_read_item_exception(CosmosHttpResponseError(status_code=503, message="unavailable"))

        telemetry = PortfolioTelemetry()
        logged: list[str] = []
        telemetry.log_cosmos_session_store_failure = lambda op, tenant_id, exc: logged.append(op)  # type: ignore[method-assign]

        session = await store.get_session(context, "portfolio-agent-python", "conv-1", telemetry)

        assert session is None
        assert logged == ["read"]

    async def test_get_session_restores_state_bag(self) -> None:
        cosmos_client = _FakeCosmosClient()
        store = CosmosAgentSessionStore(cosmos_client, _options())
        context = _context()
        original_session = AgentSession(session_id="conv-1")
        original_session.state["messages"] = ["hello"]
        serialized = original_session.to_dict()

        database = cosmos_client.get_database_client(f"agent-memory-python-{context.tenant_id.lower()}")
        container = database.get_container_client("agentSessions")
        container.set_read_item_result(
            {
                "id": "agent-session-doc",
                "tenantId": context.tenant_id,
                "stateBag": serialized["state"],
                "serviceSessionId": None,
            }
        )

        restored = await store.get_session(context, "portfolio-agent-python", "conv-1", PortfolioTelemetry())

        assert restored is not None
        assert restored.state["messages"] == ["hello"]

    async def test_save_session_raises_when_tenant_missing(self) -> None:
        store = CosmosAgentSessionStore(_FakeCosmosClient(), _options())
        context = PortfolioToolContext("", "user-1", "token", "service-token", "corr-1")
        session = AgentSession(session_id="conv-1")

        with pytest.raises(RuntimeError):
            await store.save_session(context, "portfolio-agent-python", "conv-1", session, PortfolioTelemetry())

    async def test_save_session_persists_to_correct_tenant_database(self) -> None:
        cosmos_client = _FakeCosmosClient()
        store = CosmosAgentSessionStore(cosmos_client, _options())
        context = _context("AlphaCapital")
        session = AgentSession(session_id="conv-1")
        session.state["messages"] = ["hi"]

        await store.save_session(context, "portfolio-agent-python", "conv-1", session, PortfolioTelemetry())

        database = cosmos_client.databases["agent-memory-python-alphacapital"]
        container = database.get_container_client("agentSessions")
        assert len(container.upserted) == 1
        document = container.upserted[0]
        assert document["tenantId"] == "AlphaCapital"
        assert document["stateBag"]["messages"] == ["hi"]
        assert document["ttl"] == 2592000

    async def test_save_session_logs_and_swallows_cosmos_error(self) -> None:
        cosmos_client = _FakeCosmosClient()
        store = CosmosAgentSessionStore(cosmos_client, _options())
        context = _context()
        database = cosmos_client.get_database_client(f"agent-memory-python-{context.tenant_id.lower()}")
        container = database.get_container_client("agentSessions")
        container.set_upsert_exception(CosmosHttpResponseError(status_code=503, message="unavailable"))

        telemetry = PortfolioTelemetry()
        logged: list[str] = []
        telemetry.log_cosmos_session_store_failure = lambda op, tenant_id, exc: logged.append(op)  # type: ignore[method-assign]

        # Must not raise -- a save failure is logged, not propagated (matches
        # the C# CosmosAgentSessionStore.SaveSessionAsync catch-and-log behavior).
        await store.save_session(context, "portfolio-agent-python", "conv-1", AgentSession(session_id="conv-1"), telemetry)

        assert logged == ["save"]

    async def test_normalizes_tenant_id_for_database_name(self) -> None:
        cosmos_client = _FakeCosmosClient()
        store = CosmosAgentSessionStore(cosmos_client, _options())
        context = _context("Delta-Equity")  # hyphenated, mixed case

        await store.save_session(context, "portfolio-agent-python", "conv-1", AgentSession(session_id="conv-1"), PortfolioTelemetry())

        assert "agent-memory-python-deltaequity" in cosmos_client.databases
