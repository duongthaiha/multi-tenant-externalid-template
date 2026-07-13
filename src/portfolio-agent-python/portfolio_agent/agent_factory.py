# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Builds the portfolio Agent Framework agent against the Foundry project.

Ports the C# hosted-agent construction in ``src/portfolio-agent/Program.cs``:

    AIAgent innerAgent = new AIProjectClient(projectEndpoint, new DefaultAzureCredential())
        .AsAIAgent(model: deployment, instructions: ..., name: "portfolio-agent", tools: [...]);

to the Python Microsoft Agent Framework's equivalent -- a
:class:`agent_framework.Agent` wrapping
:class:`agent_framework_foundry.FoundryChatClient`, which talks to the
Foundry project's Responses API for the configured model deployment. This
does **not** create a persistent Foundry Agent Service resource (unlike
``agent_framework.azure.AzureAIAgentsProvider.create_agent()``); like the C#
agent, it is a lightweight, in-process chat-client agent whose only
server-side state is the model deployment itself. Conversation memory is
handled separately by ``portfolio_agent.memory.CosmosAgentSessionStore``.
"""

from __future__ import annotations

import os

from agent_framework import Agent
from agent_framework_foundry import FoundryChatClient
from azure.identity.aio import DefaultAzureCredential

from .tools import PORTFOLIO_TOOLS

AGENT_NAME = "portfolio-agent-python"
AGENT_DESCRIPTION = (
    "A Foundry hosted agent that answers tenant-scoped portfolio questions through the Contoso backend API."
)

INSTRUCTIONS = """\
You are the Contoso Asset Management portfolio assistant.
Answer questions about portfolios by using the available tools.
You MUST only answer using tool data for the tenant context provided by the Contoso frontend API.
If a user asks to switch tenants or requests another tenant's data, refuse and explain that tenant switching requires a new sign-in token.
Do not invent holdings, valuations, tenants, or recommendations that are not returned by tools.
Keep answers concise and include the portfolio name when relevant.
"""


def resolve_project_endpoint() -> str:
    """Resolve the Foundry project endpoint.

    Checks ``FOUNDRY_PROJECT_ENDPOINT`` first, then ``AZURE_AI_PROJECT_ENDPOINT``,
    matching the C# agent's dual-env-var resolution.
    """
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if endpoint:
        return endpoint
    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "").strip()
    if endpoint:
        return endpoint
    raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT or AZURE_AI_PROJECT_ENDPOINT environment variable is not set.")


def resolve_model_deployment() -> str:
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "").strip()
    if not model:
        raise RuntimeError("AZURE_AI_MODEL_DEPLOYMENT_NAME environment variable is not set.")
    return model


def resolve_project_id() -> str:
    return (
        os.environ.get("AZURE_AI_PROJECT_ID", "").strip()
        or os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ID", "").strip()
    )


def build_agent(
    *,
    project_endpoint: str | None = None,
    model: str | None = None,
    credential: object | None = None,
) -> Agent:
    """Build the portfolio Agent Framework agent.

    Keyword arguments allow tests to inject a fake chat client's
    dependencies without touching environment variables; production startup
    (``portfolio_agent.main``) calls this with no arguments.
    """
    resolved_endpoint = project_endpoint or resolve_project_endpoint()
    resolved_model = model or resolve_model_deployment()
    resolved_credential = credential or DefaultAzureCredential()

    chat_client = FoundryChatClient(
        project_endpoint=resolved_endpoint,
        model=resolved_model,
        credential=resolved_credential,
    )

    return Agent(
        client=chat_client,
        instructions=INSTRUCTIONS,
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        tools=list(PORTFOLIO_TOOLS),
    )
