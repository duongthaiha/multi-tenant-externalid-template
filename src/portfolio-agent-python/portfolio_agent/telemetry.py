# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""OpenTelemetry + Azure Monitor telemetry for the Python portfolio agent.

Ports ``PortfolioTelemetry`` (C#, ``src/portfolio-agent/Program.cs``):

- A dedicated tracer/meter (``Contoso.AssetManagement.PortfolioAgent``) with
  the same custom tool-invocation and tool-miss counters.
- Foundry project id / GenAI agent name span+log attributes, so traces can be
  correlated with the Foundry project and agent in Application Insights.
- Safe (token-free) structured logs for startup, tool invocation, missing
  trusted context, MCP tool failures, Cosmos session-store failures, and
  tenant-mismatch 403 responses.

Metric and trace export to Azure Monitor is provided by the
``azure-ai-agentserver-core`` host's ``configure_observability`` (invoked by
``ResponsesAgentServerHost.__init__``), which enables the
``microsoft-opentelemetry`` distro (Azure Monitor trace + log + **metric**
exporters) when ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set. This module
only needs to call the standard OpenTelemetry API (``get_tracer`` /
``get_meter``); the returned proxies bind to that provider automatically once
it is installed, even though these module-level calls happen at import time
before the host is constructed -- this is a documented OpenTelemetry Python
API guarantee (``ProxyTracer`` / ``ProxyMeter``), not something this module
manages itself.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Iterator, Optional

from opentelemetry import metrics, trace

from .constants import AuthorizationDecisions, LogFields
from .context import PortfolioToolContext

logger = logging.getLogger("portfolio_agent")

_INSTRUMENTATION_NAME = "Contoso.AssetManagement.PortfolioAgent"
_AGENT_NAME = "portfolio-agent-python"

_FOUNDRY_PROJECT_ID_ATTR = "microsoft.foundry.project.id"
_GENAI_PROJECT_ID_ATTR = "gen_ai.azure_ai_project.id"
_GENAI_AGENT_NAME_ATTR = "gen_ai.agent.name"

_tracer = trace.get_tracer(_INSTRUMENTATION_NAME)
_meter = metrics.get_meter(_INSTRUMENTATION_NAME)

_tool_invocation_counter = _meter.create_counter(
    "portfolio_agent_tool_invocations",
    description="Number of portfolio-agent tool invocations.",
)
_tool_miss_counter = _meter.create_counter(
    "portfolio_agent_tool_misses",
    description="Number of portfolio-agent tool lookups that did not match demo data.",
)


def _present(value: Optional[str]) -> str:
    return "present" if value and value.strip() else "missing"


def _resolve_project_id() -> Optional[str]:
    return os.environ.get("AZURE_AI_PROJECT_ID") or os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ID") or None


class PortfolioTelemetry:
    """Tool telemetry: spans, counters, and safe structured logs."""

    def log_agent_starting(self, project_host: str, model_deployment: str) -> None:
        with _tracer.start_as_current_span("foundry-project-scope") as span:
            span.set_attribute(_GENAI_AGENT_NAME_ATTR, _AGENT_NAME)
            span.set_attribute("dependency.type", "InProc")
            project_id = _resolve_project_id()
            if project_id:
                span.set_attribute(_FOUNDRY_PROJECT_ID_ATTR, project_id)
                span.set_attribute(_GENAI_PROJECT_ID_ATTR, project_id)
        logger.info(
            "Portfolio agent starting with Foundry project host %s and model deployment %s.",
            project_host,
            model_deployment,
            extra={"projectHost": project_host, "modelDeployment": model_deployment},
        )

    @contextlib.contextmanager
    def start_tool_span(self, tool_name: str, tenant_id: str, correlation_id: str) -> Iterator[trace.Span]:
        """Start (and yield) a span for a single tool invocation.

        Ports ``PortfolioTelemetry.StartToolActivity`` (C#).
        """
        with _tracer.start_as_current_span(f"portfolio-agent.tool.{tool_name}") as span:
            span.set_attribute(_GENAI_AGENT_NAME_ATTR, _AGENT_NAME)
            project_id = _resolve_project_id()
            if project_id:
                span.set_attribute(_FOUNDRY_PROJECT_ID_ATTR, project_id)
                span.set_attribute(_GENAI_PROJECT_ID_ATTR, project_id)
            span.set_attribute("portfolio_agent.tool.name", tool_name)
            span.set_attribute(LogFields.TENANT_ID, tenant_id)
            span.set_attribute(LogFields.CORRELATION_ID, correlation_id)
            yield span

    def record_tool_invocation(self, tool_name: str) -> None:
        _tool_invocation_counter.add(1, {"tool.name": tool_name})

    def record_tool_miss(self, tool_name: str) -> None:
        _tool_miss_counter.add(1, {"tool.name": tool_name})

    def log_tool_invocation(self, tool_name: str, tenant_id: str, lookup: str) -> None:
        logger.info(
            "Portfolio tool %s invoked for tenant %s and lookup %s.",
            tool_name,
            tenant_id,
            lookup,
            extra={
                LogFields.OPERATION: tool_name,
                LogFields.TENANT_ID: tenant_id,
            },
        )

    def log_missing_tool_context(self, tool_name: str, context: Optional[PortfolioToolContext]) -> None:
        logger.warning(
            "Portfolio tool %s missing trusted context. tenant:%s user:%s userToken:%s serviceToken:%s "
            "correlation:%s.",
            tool_name,
            _present(context.tenant_id if context else None),
            _present(context.user_id if context else None),
            _present(context.user_access_token if context else None),
            _present(context.service_token if context else None),
            _present(context.correlation_id if context else None),
            extra={LogFields.OPERATION: tool_name},
        )

    def log_mcp_tool_failure(self, tool_name: str, tenant_id: str, correlation_id: str, reason: str) -> None:
        logger.warning(
            "Portfolio MCP tool %s failed for tenant %s, correlation %s, reason %s.",
            tool_name,
            tenant_id,
            correlation_id,
            reason,
            extra={
                LogFields.OPERATION: tool_name,
                LogFields.TENANT_ID: tenant_id,
                LogFields.CORRELATION_ID: correlation_id,
            },
        )

    def log_mcp_tool_exception(
        self,
        tool_name: str,
        tenant_id: str,
        correlation_id: str,
        exception_type: str,
        message: str,
    ) -> None:
        logger.warning(
            "Portfolio MCP tool %s exception for tenant %s, correlation %s: %s: %s.",
            tool_name,
            tenant_id,
            correlation_id,
            exception_type,
            message,
            extra={
                LogFields.OPERATION: tool_name,
                LogFields.TENANT_ID: tenant_id,
                LogFields.CORRELATION_ID: correlation_id,
            },
        )

    def log_tenant_mismatch_403(self, tool_name: str, tenant_id: str, user_id: str, correlation_id: str) -> None:
        """Log a tenant-mismatch 403 in the exact shape the tenant-mismatch alert rule expects.

        The alert (``infra/modules/monitoring.bicep``) fires on
        ``AppTraces`` rows where the message contains ``"tenant-mismatch"``
        (or ``Properties.authorizationDecision == "tenant-mismatch"``) AND
        ``Properties.statusCode == "403"``. Both the message text and the
        structured ``extra`` properties are set so the query matches
        regardless of which clause it uses.
        """
        logger.warning(
            "Portfolio MCP tool %s received a tenant-mismatch 403 for tenant %s, user %s, correlation %s.",
            tool_name,
            tenant_id,
            user_id,
            correlation_id,
            extra={
                LogFields.OPERATION: tool_name,
                LogFields.TENANT_ID: tenant_id,
                LogFields.USER_ID: user_id,
                LogFields.CORRELATION_ID: correlation_id,
                LogFields.AUTHORIZATION_DECISION: AuthorizationDecisions.TENANT_MISMATCH,
                LogFields.STATUS_CODE: "403",
            },
        )

    def log_cosmos_session_store_failure(self, operation: str, tenant_id: str, exception: BaseException) -> None:
        logger.warning(
            "Portfolio agent Cosmos session store %s failed for tenant %s: %s: %s.",
            operation,
            tenant_id,
            type(exception).__name__,
            exception,
            extra={LogFields.OPERATION: operation, LogFields.TENANT_ID: tenant_id},
        )
