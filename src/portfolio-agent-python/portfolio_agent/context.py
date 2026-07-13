# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Trusted request-scoped tenant/user/token/correlation context for tool calls.

Ports ``PortfolioToolContext`` and ``PortfolioToolContextAccessor`` from the C# hosted agent
(``src/portfolio-agent/Program.cs``) to Python.

The Responses protocol handler (see ``portfolio_agent.handler``) resolves this
context once per request from the Foundry-forwarded ``x-client-*`` headers and
(when present) the ``CreateResponse.metadata`` bag, then makes it available to
tool functions via a :mod:`contextvars` context variable -- the async
equivalent of the C# ``AsyncLocal<T>`` accessor.

Every tool call MUST resolve a complete context (tenant, user, user token,
service token, correlation id) or fail closed -- never fall back to
cross-tenant sample data or an empty tenant scope.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional

from .constants import Headers

if TYPE_CHECKING:
    from .telemetry import PortfolioTelemetry

_TENANT_HEADER = Headers.AUTHENTICATED_TENANT
_USER_HEADER = Headers.AUTHENTICATED_USER
_USER_AUTHORIZATION_HEADER = Headers.USER_AUTHORIZATION
_SERVICE_AUTHORIZATION_HEADER = Headers.SERVICE_AUTHORIZATION
_CORRELATION_HEADER = Headers.CORRELATION_ID

def _extract_bearer(value: Optional[str]) -> str:
    """Return the bearer token from an ``Authorization``-shaped header value.

    Returns an empty string (never ``None``) when the header is missing or
    not a well-formed ``Bearer <token>`` value, so callers can uniformly
    check truthiness.
    """
    if not value:
        return ""
    prefix = "Bearer "
    if not value.lower().startswith(prefix.lower()):
        return ""
    return value[len(prefix):].strip()


def _first_present(primary: str, fallback: str) -> str:
    return primary if primary.strip() else fallback


def _new_correlation_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class PortfolioToolContext:
    """Trusted, request-scoped tenant/user/token/correlation context.

    Every field must be populated from a validated upstream source (the
    Frontend API/BFF via ``x-client-*`` headers, or Responses ``metadata``
    written by the same trusted caller) -- never from an agent prompt, tool
    argument, or unauthenticated request input.
    """

    tenant_id: str
    user_id: str
    user_access_token: str
    service_token: str
    correlation_id: str

    @property
    def is_complete(self) -> bool:
        return bool(
            self.tenant_id.strip()
            and self.user_id.strip()
            and self.user_access_token.strip()
            and self.service_token.strip()
            and self.correlation_id.strip()
        )

    @classmethod
    def empty(cls) -> "PortfolioToolContext":
        return cls("", "", "", "", _new_correlation_id())

    @classmethod
    def from_client_headers(cls, client_headers: Mapping[str, str]) -> "PortfolioToolContext":
        """Resolve context from ``x-client-*`` forwarded headers.

        ``client_headers`` keys are expected in the lowercased,
        ``x-client-``-prefixed form that
        ``azure.ai.agentserver.responses.ResponseContext.client_headers``
        provides (e.g. ``"x-client-x-authenticated-tenant"``).
        """
        tenant_id = _get_client_header(client_headers, _TENANT_HEADER) or ""
        user_id = _get_client_header(client_headers, _USER_HEADER) or ""
        user_access_token = _extract_bearer(_get_client_header(client_headers, _USER_AUTHORIZATION_HEADER))
        service_token = _extract_bearer(_get_client_header(client_headers, _SERVICE_AUTHORIZATION_HEADER))
        correlation_id = _get_client_header(client_headers, _CORRELATION_HEADER) or ""

        return cls(
            tenant_id,
            user_id,
            user_access_token,
            service_token,
            correlation_id if correlation_id.strip() else _new_correlation_id(),
        )

    @classmethod
    def from_metadata(cls, metadata: Optional[Mapping[str, str]]) -> "PortfolioToolContext":
        """Resolve context from Responses ``CreateResponse.metadata``.

        Uses the same ``contoso_*`` metadata keys as the C# hosted agent's
        ``PortfolioToolContext.FromMetadata``.
        """
        if not metadata:
            return cls.empty()

        tenant_id = metadata.get("contoso_tenant_id", "") or ""
        user_id = metadata.get("contoso_user_id", "") or ""
        user_access_token = metadata.get("contoso_user_access_token", "") or ""
        service_token = metadata.get("contoso_service_token", "") or ""
        correlation_id = metadata.get("contoso_correlation_id", "") or ""

        return cls(
            tenant_id,
            user_id,
            user_access_token,
            service_token,
            correlation_id if correlation_id.strip() else _new_correlation_id(),
        )

    @classmethod
    def from_metadata_and_client_headers(
        cls,
        metadata: Optional[Mapping[str, str]],
        client_headers: Mapping[str, str],
    ) -> "PortfolioToolContext":
        """Combine metadata and forwarded-header context, metadata wins for identity.

        Ports ``PortfolioToolContext.FromMetadataAndClientHeaders`` (C#):
        tenant id, user id, and correlation id prefer the ``metadata`` bag;
        user/service tokens prefer the forwarded headers. This matches the
        Responses v2 hosted-session isolation key resolution path.
        """
        metadata_context = cls.from_metadata(metadata)
        header_context = cls.from_client_headers(client_headers)

        return cls(
            _first_present(metadata_context.tenant_id, header_context.tenant_id),
            _first_present(metadata_context.user_id, header_context.user_id),
            _first_present(header_context.user_access_token, metadata_context.user_access_token),
            _first_present(header_context.service_token, metadata_context.service_token),
            _first_present(metadata_context.correlation_id, header_context.correlation_id),
        )


def _get_client_header(client_headers: Mapping[str, str], header_name: str) -> Optional[str]:
    """Look up a trusted header among its ``x-client-``-forwarded variants.

    Tries, in order: the forwarded-prefixed exact name, the forwarded-prefixed
    lowercase name. ``ResponseContext.client_headers`` keys are already
    lowercased by the hosting SDK, so the lowercase forwarded form is the one
    that normally matches; the exact-case form is tried first for robustness
    against direct (non-SDK) callers such as unit tests.
    """
    forwarded = Headers.client_forwarded(header_name)
    forwarded_lower = forwarded.lower()
    if forwarded in client_headers:
        return client_headers[forwarded]
    if forwarded_lower in client_headers:
        return client_headers[forwarded_lower]
    return None


# ---------------------------------------------------------------------------
# Request-scoped propagation (contextvars)
# ---------------------------------------------------------------------------

_current_context: ContextVar[Optional[PortfolioToolContext]] = ContextVar(
    "portfolio_tool_context", default=None
)
_current_telemetry: ContextVar[Optional["PortfolioTelemetry"]] = ContextVar(
    "portfolio_telemetry", default=None
)


class PortfolioToolContextAccessor:
    """Request-scoped accessor for the current tool context and telemetry.

    Backed by :class:`contextvars.ContextVar`, the async equivalent of the C#
    ``AsyncLocal<T>``-based ``PortfolioToolContextAccessor``.
    """

    @staticmethod
    def get_current() -> Optional[PortfolioToolContext]:
        return _current_context.get()

    @staticmethod
    def set_current(context: Optional[PortfolioToolContext]) -> Token[Optional[PortfolioToolContext]]:
        return _current_context.set(context)

    @staticmethod
    def reset_current(token: Token[Optional[PortfolioToolContext]]) -> None:
        _current_context.reset(token)

    @staticmethod
    def get_telemetry() -> Optional["PortfolioTelemetry"]:
        return _current_telemetry.get()

    @staticmethod
    def set_telemetry(telemetry: Optional["PortfolioTelemetry"]) -> Token[Optional["PortfolioTelemetry"]]:
        return _current_telemetry.set(telemetry)

    @staticmethod
    def reset_telemetry(token: Token[Optional["PortfolioTelemetry"]]) -> None:
        _current_telemetry.reset(token)


def require_tool_context(tool_name: str) -> PortfolioToolContext:
    """Resolve the trusted context for a tool call, or fail closed.

    Raises :class:`RuntimeError` when the request-scoped context is incomplete
    so tools never reuse another request's credentials or tenant authority.
    """
    context = PortfolioToolContextAccessor.get_current()
    if context is not None and context.is_complete:
        return context

    telemetry = PortfolioToolContextAccessor.get_telemetry()
    if telemetry is not None:
        telemetry.log_missing_tool_context(tool_name, context)
        telemetry.record_tool_miss(tool_name)

    raise RuntimeError(
        "Portfolio agent tools require BFF-provided tenant, user token, service token, "
        "and correlation context."
    )
