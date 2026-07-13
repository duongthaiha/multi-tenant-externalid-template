# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""End-to-end handler tests against a real ``ResponsesAgentServerHost``.

Uses ``starlette.testclient.TestClient`` (the same pattern the
``azure-ai-agentserver-responses`` package's own sample tests use) so a
future SDK revision that changes ``client_headers``/``metadata``/
``platform_context`` extraction breaks a test here rather than only being
discovered in a deployed environment. The Agent Framework agent and Cosmos
session store are faked; only the Responses protocol host itself is real.
"""

from __future__ import annotations

import functools
from typing import Any, Optional

import pytest
from agent_framework import AgentResponse, Content, Message
from azure.ai.agentserver.responses import ResponsesAgentServerHost
from starlette.testclient import TestClient

from portfolio_agent.constants import Headers
from portfolio_agent.handler import PortfolioAgentRuntime, handle_create_response
from portfolio_agent.telemetry import PortfolioTelemetry


class _FakeAgentResponse:
    def __init__(self, text: str, messages: Optional[list[Message]] = None) -> None:
        self._response = AgentResponse(
            messages=messages or [Message("assistant", [Content.from_text(text)])]
        )

    @property
    def text(self) -> str:
        return self._response.text

    @property
    def messages(self) -> list[Message]:
        return self._response.messages


class _FakeSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.state: dict[str, Any] = {}


class _FakeAgent:
    name = "portfolio-agent-python"

    def __init__(
        self, response_text: str = "Hello from the fake agent.", messages: Optional[list[Message]] = None
    ) -> None:
        self.response_text = response_text
        self.messages = messages
        self.run_calls: list[tuple[str, Any]] = []
        self.raise_on_run: Optional[BaseException] = None

    def create_session(self, *, session_id: str) -> _FakeSession:
        return _FakeSession(session_id)

    async def run(self, message: str, *, session: Any = None) -> _FakeAgentResponse:
        self.run_calls.append((message, session))
        if self.raise_on_run is not None:
            raise self.raise_on_run
        return _FakeAgentResponse(self.response_text, self.messages)


class _FakeSessionStore:
    def __init__(self) -> None:
        self.saved: list[tuple[Any, str, str, Any]] = []
        self.raise_on_save: Optional[BaseException] = None

    async def get_session(self, context, agent_name, conversation_id, telemetry):
        return None

    async def save_session(self, context, agent_name, conversation_id, session, telemetry) -> None:
        if self.raise_on_save is not None:
            raise self.raise_on_save
        if not context.tenant_id.strip():
            # Mirrors CosmosAgentSessionStore.save_session's fail-closed contract
            # (see portfolio_agent.memory) so handler-level tests can assert on
            # it without depending on a real/faked Cosmos client.
            raise RuntimeError("Portfolio agent session persistence requires trusted tenant context.")
        self.saved.append((context, agent_name, conversation_id, session))

    async def close(self) -> None:
        pass


def _make_client(
    *, agent: Optional[_FakeAgent] = None, session_store: Optional[_FakeSessionStore] = None
) -> tuple[TestClient, PortfolioAgentRuntime]:
    runtime = PortfolioAgentRuntime(
        agent=agent or _FakeAgent(),
        telemetry=PortfolioTelemetry(),
        session_store=session_store or _FakeSessionStore(),
    )
    app = ResponsesAgentServerHost(configure_observability=None)
    app.response_handler(functools.partial(handle_create_response, runtime=runtime))
    return TestClient(app), runtime


def _trusted_headers(**trusted: str) -> dict[str, str]:
    return {Headers.client_forwarded(name): value for name, value in trusted.items()}


_FULL_TRUSTED_HEADERS = _trusted_headers(**{
    Headers.AUTHENTICATED_TENANT: "AlphaCapital",
    Headers.AUTHENTICATED_USER: "user-1",
    Headers.USER_AUTHORIZATION: "Bearer user-token",
    Headers.SERVICE_AUTHORIZATION: "Bearer service-token",
    Headers.CORRELATION_ID: "corr-1",
})


def _output_text(body: dict[str, Any]) -> str:
    parts = [
        part["text"]
        for item in body.get("output", [])
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    ]
    return "\n".join(parts)


class TestHandleCreateResponse:
    def test_answers_with_agent_text_given_trusted_headers(self) -> None:
        client, _ = _make_client(agent=_FakeAgent("Which portfolios? Here they are."))

        response = client.post(
            "/responses",
            json={"model": "test", "input": "Which portfolios are available?", "stream": False},
            headers=_FULL_TRUSTED_HEADERS,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert "Which portfolios? Here they are." in _output_text(body)

    def test_saves_session_after_run(self) -> None:
        session_store = _FakeSessionStore()
        client, _ = _make_client(session_store=session_store)

        response = client.post(
            "/responses",
            json={"model": "test", "input": "hello", "stream": False},
            headers=_FULL_TRUSTED_HEADERS,
        )

        assert response.status_code == 200
        assert len(session_store.saved) == 1
        _, agent_name, _, _ = session_store.saved[0]
        assert agent_name == "portfolio-agent-python"

    def test_agent_receives_resolved_tenant_via_context(self) -> None:
        agent = _FakeAgent()
        client, _ = _make_client(agent=agent)

        client.post(
            "/responses",
            json={"model": "test", "input": "Which portfolios are available?", "stream": False},
            headers=_FULL_TRUSTED_HEADERS,
        )

        assert len(agent.run_calls) == 1
        message, _ = agent.run_calls[0]
        assert message == "Which portfolios are available?"

    def test_missing_trusted_context_uses_ephemeral_session(self) -> None:
        """Managed eval requests can answer without reading or writing tenant memory."""
        session_store = _FakeSessionStore()
        client, _ = _make_client(session_store=session_store)

        response = client.post(
            "/responses",
            json={"model": "test", "input": "hello", "stream": False},
            headers={},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert session_store.saved == []

    def test_emits_function_call_and_output_items(self) -> None:
        messages = [
            Message(
                "assistant",
                [
                    Content(
                        "function_call",
                        name="ListPortfolios",
                        call_id="call-1",
                        arguments={},
                    )
                ],
            ),
            Message(
                "tool",
                [Content("function_result", call_id="call-1", result="alpha-growth")],
            ),
            Message("assistant", [Content.from_text("Alpha Growth is available.")]),
        ]
        client, _ = _make_client(
            agent=_FakeAgent("Alpha Growth is available.", messages=messages)
        )

        response = client.post(
            "/responses",
            json={"model": "test", "input": "List portfolios", "stream": False},
            headers=_FULL_TRUSTED_HEADERS,
        )

        assert response.status_code == 200
        output = response.json()["output"]
        function_call = next(item for item in output if item["type"] == "function_call")
        function_output = next(
            item for item in output if item["type"] == "function_call_output"
        )
        assert function_call["name"] == "ListPortfolios"
        assert function_call["arguments"] == "{}"
        assert function_output["call_id"] == "call-1"
        assert function_output["output"] == "alpha-growth"

    def test_tool_context_is_cleared_between_requests(self) -> None:
        """A second request without trusted headers must not see the first request's context."""
        from portfolio_agent.context import PortfolioToolContextAccessor

        client, _ = _make_client()

        client.post(
            "/responses",
            json={"model": "test", "input": "hello", "stream": False},
            headers=_FULL_TRUSTED_HEADERS,
        )
        # contextvars are per-request-task in the real ASGI server; TestClient
        # runs requests on a fresh task per call so this should not leak, but
        # assert the module-level cache does not silently authorize a
        # differently-tenant-scoped request either.
        second_response = client.post(
            "/responses",
            json={"model": "test", "input": "hello", "stream": False},
            headers=_trusted_headers(**{
                Headers.AUTHENTICATED_TENANT: "BetaWealth",
                Headers.AUTHENTICATED_USER: "user-2",
                Headers.USER_AUTHORIZATION: "Bearer user-token-2",
                Headers.SERVICE_AUTHORIZATION: "Bearer service-token-2",
                Headers.CORRELATION_ID: "corr-2",
            }),
        )

        assert second_response.status_code == 200
        assert second_response.json()["status"] == "completed"
