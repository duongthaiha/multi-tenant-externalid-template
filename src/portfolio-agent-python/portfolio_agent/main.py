# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Entry point for the Contoso Portfolio Agent (Python hosted agent).

Ports the top-level wiring in ``src/portfolio-agent/Program.cs``: build the
Agent Framework agent, the Cosmos conversation-memory store, and the
Responses protocol host, then register the single request handler.

Run locally with:

    python -m portfolio_agent.main

or via the Foundry/azd hosted-agent container entry point (see ``Dockerfile``).
"""

from __future__ import annotations

import functools
import logging
import urllib.parse

from azure.ai.agentserver.responses import ResponsesAgentServerHost

from .agent_factory import resolve_model_deployment, resolve_project_endpoint
from .handler import PortfolioAgentRuntime, handle_create_response

logger = logging.getLogger("portfolio_agent")


def _project_host_for_logging() -> str:
    """Return only the scheme+host of the Foundry project endpoint, never the full URL."""
    try:
        parsed = urllib.parse.urlparse(resolve_project_endpoint())
    except RuntimeError:
        return "(not set)"
    return parsed.hostname or "(not set)"


def _model_deployment_for_logging() -> str:
    try:
        return resolve_model_deployment()
    except RuntimeError:
        return "(not set)"


def create_app(
    *,
    runtime: PortfolioAgentRuntime | None = None,
    **host_kwargs: object,
) -> ResponsesAgentServerHost:
    """Build the Responses protocol host and register the create-response handler.

    Keyword-only ``runtime`` lets tests inject a fake agent/telemetry/session
    store without touching process environment variables or network calls.
    Additional ``host_kwargs`` are forwarded to :class:`ResponsesAgentServerHost`
    (e.g. tests pass ``configure_observability=None`` to skip Azure Monitor/OTel
    setup and the Azure-VM-metadata resource-detection probe it triggers).
    """
    app = ResponsesAgentServerHost(**host_kwargs)
    resolved_runtime = runtime or PortfolioAgentRuntime()
    resolved_runtime.telemetry.log_agent_starting(_project_host_for_logging(), _model_deployment_for_logging())
    app.response_handler(functools.partial(handle_create_response, runtime=resolved_runtime))
    return app


def main() -> None:
    create_app().run()


if __name__ == "__main__":
    main()
