# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Responses protocol handler for the portfolio hosted agent.

Wires a single ``@app.response_handler`` to:

1. Resolve the trusted :class:`~portfolio_agent.context.PortfolioToolContext`
   from the Responses ``metadata`` bag and ``x-client-*`` forwarded headers
   (ports ``PortfolioHostedSessionIsolationKeyProvider.GetKeysAsync`` /
   ``PortfolioToolContext.FromMetadataAndClientHeaders``, C#).
2. Publish that context (and the process telemetry instance) to the
   request-scoped :mod:`contextvars` accessor tool functions read from.
3. Load (or create) Cosmos-backed conversation memory for the resolved
   tenant/user/conversation.
4. Run the Agent Framework agent and map its retained function-call/result
   contents into Responses ``function_call`` / ``function_call_output`` items.
5. Persist the (possibly tool-updated) session back to Cosmos.
6. Return the final answer as a :class:`TextResponse`.

A tool invoked without a complete trusted context fails closed (raises) from
``portfolio_agent.context.require_tool_context`` -- this propagates through
the Agent Framework's function-invocation layer and, like the C# agent,
turns into a failed response rather than silently answering with
cross-tenant or unauthenticated data. Session persistence is also fail-closed
by tenant id (ports ``CosmosAgentSessionStore.SaveSessionAsync`` /
``RequireContext``, C#): a request with no resolvable tenant id fails when
the handler tries to save conversation memory, even if the model never
invoked a tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Mapping, Optional

from agent_framework import Agent, AgentResponse
from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponseEventStream,
)

from .agent_factory import build_agent
from .context import PortfolioToolContext, PortfolioToolContextAccessor
from .memory import CosmosAgentSessionStore
from .telemetry import PortfolioTelemetry

logger = logging.getLogger("portfolio_agent")


class PortfolioAgentRuntime:
    """Process-wide singletons used by the response handler.

    Built once at startup (see ``portfolio_agent.main``) and passed into
    :func:`handle_create_response` via a closure, avoiding any hidden global
    mutable state beyond the (already request-scoped) contextvars in
    ``portfolio_agent.context``.
    """

    def __init__(
        self,
        *,
        agent: Optional[Agent] = None,
        telemetry: Optional[PortfolioTelemetry] = None,
        session_store: Optional[CosmosAgentSessionStore] = None,
    ) -> None:
        self.telemetry = telemetry or PortfolioTelemetry()
        self.agent = agent or build_agent()
        self.session_store = session_store or CosmosAgentSessionStore.create_from_environment()

    async def aclose(self) -> None:
        await self.session_store.close()


def _metadata_to_dict(metadata: Any) -> Mapping[str, str]:
    """Normalize the Responses ``Metadata`` model into a plain string dict.

    ``Metadata`` (``azure.ai.agentserver.responses.models``) is a generated,
    dict-like model with no fixed field set; this defends against SDK
    revisions that change its exact shape by falling back to attribute
    introspection.
    """
    if metadata is None:
        return {}
    try:
        return {str(key): str(value) for key, value in dict(metadata).items() if value is not None}
    except (TypeError, ValueError):
        pass
    additional = getattr(metadata, "additional_properties", None) or getattr(metadata, "__dict__", None)
    if isinstance(additional, Mapping):
        return {str(key): str(value) for key, value in additional.items() if value is not None}
    return {}


def resolve_tool_context(request: CreateResponse, context: ResponseContext) -> PortfolioToolContext:
    metadata = _metadata_to_dict(getattr(request, "metadata", None))
    return PortfolioToolContext.from_metadata_and_client_headers(metadata, context.client_headers)


def _resolve_conversation_id(request: CreateResponse, context: ResponseContext) -> str:
    return (
        context.conversation_id
        or getattr(request, "previous_response_id", None)
        or context.response_id
    )


async def handle_create_response(
    request: CreateResponse,
    context: ResponseContext,
    cancellation_signal: asyncio.Event,
    *,
    runtime: PortfolioAgentRuntime,
) -> AsyncIterator[Any]:
    """Handle one Responses ``POST /responses`` create request.

    Bound to the hosting SDK via a partial application in
    ``portfolio_agent.main`` (the SDK's ``@app.response_handler`` requires a
    3-argument callable, so ``runtime`` is supplied by
    ``functools.partial``, not passed on the wire).
    """
    del cancellation_signal  # Cooperative cancellation is handled by the hosting SDK's orchestrator.

    tool_context = resolve_tool_context(request, context)
    conversation_id = _resolve_conversation_id(request, context)
    agent_name = runtime.agent.name or "portfolio-agent-python"

    persist_session = tool_context.is_complete
    session = None
    if persist_session:
        session = await runtime.session_store.get_session(
            tool_context, agent_name, conversation_id, runtime.telemetry
        )
    if session is None:
        session = runtime.agent.create_session(session_id=conversation_id)

    input_text = await context.get_input_text()

    async def _generate_response() -> AsyncIterator[Any]:
        stream = ResponseEventStream(response_id=context.response_id, request=request)
        yield stream.emit_created()
        yield stream.emit_in_progress()

        context_token = PortfolioToolContextAccessor.set_current(tool_context)
        telemetry_token = PortfolioToolContextAccessor.set_telemetry(runtime.telemetry)
        try:
            try:
                response = await runtime.agent.run(input_text, session=session)
            finally:
                if persist_session:
                    await runtime.session_store.save_session(
                        tool_context, agent_name, conversation_id, session, runtime.telemetry
                    )
        finally:
            PortfolioToolContextAccessor.reset_telemetry(telemetry_token)
            PortfolioToolContextAccessor.reset_current(context_token)

        for event in _tool_events(stream, response):
            yield event
        for event in stream.output_item_message(
            response.text or "The portfolio agent returned an empty answer."
        ):
            yield event
        yield stream.emit_completed()

    return _generate_response()


def _tool_events(
    stream: ResponseEventStream, response: AgentResponse[Any]
) -> list[Any]:
    """Translate Agent Framework function contents into Responses output items."""
    events: list[Any] = []
    for message in response.messages:
        for content in message.contents:
            if content.type == "function_call":
                if not content.name or not content.call_id:
                    raise RuntimeError("Agent Framework returned a malformed function call.")
                arguments = (
                    content.arguments
                    if isinstance(content.arguments, str)
                    else json.dumps(content.arguments or {}, default=str)
                )
                events.extend(
                    stream.output_item_function_call(content.name, content.call_id, arguments)
                )
            elif content.type == "function_result":
                if not content.call_id:
                    raise RuntimeError("Agent Framework returned a malformed function result.")
                output = (
                    content.result
                    if isinstance(content.result, str)
                    else json.dumps(content.result, default=str)
                )
                events.extend(stream.output_item_function_call_output(content.call_id, output))
    return events
