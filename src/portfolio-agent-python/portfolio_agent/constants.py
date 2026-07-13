# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Contoso Asset Management tenant constants.

Ports the subset of ``Contoso.AssetManagement.Shared.TenantConstants`` (C#,
``src/shared/TenantConstants.cs``) that the portfolio agent needs: the
trusted-header contract used by the Frontend API/BFF, and the authorization
decision strings used in tenant-mismatch log/telemetry semantics.

This module intentionally mirrors the C# constant names and values exactly so
the two hosted-agent implementations (``portfolio-agent`` and
``portfolio-agent-python``) stay wire-compatible with the same Frontend
API/BFF and APIM MCP gateway.
"""

from __future__ import annotations


class Headers:
    """HTTP header names used across the trusted request-context contract."""

    CLIENT_FORWARDED_PREFIX = "x-client-"
    AUTHORIZATION = "Authorization"
    CORRELATION_ID = "X-Correlation-ID"
    AUTHENTICATED_TENANT = "X-Authenticated-Tenant"
    AUTHENTICATED_USER = "X-Authenticated-User"
    USER_AUTHORIZATION = "X-User-Authorization"
    SERVICE_AUTHORIZATION = "X-Service-Authorization"
    TENANT_ID = "X-Tenant-Id"
    USER_ID = "X-User-Id"
    FORWARDED_USER = "X-Forwarded-User"
    AGENT_ID = "X-Agent-Id"
    AUTHORIZATION_DECISION = "X-Authorization-Decision"
    FOUNDRY_USER_IDENTITY = "x-ms-user-identity"

    @staticmethod
    def client_forwarded(header_name: str) -> str:
        """Return the ``x-client-`` forwarded form of a header name.

        The Frontend API/BFF (and, in hosted-Responses mode, the Foundry
        hosting platform) forwards trusted headers to the agent container
        prefixed with ``x-client-``. See ``CLIENT_HEADER_PREFIX`` in
        ``azure.ai.agentserver.core`` for the platform-side constant this
        mirrors.
        """
        return f"{Headers.CLIENT_FORWARDED_PREFIX}{header_name}"


class TenantStatus:
    ACTIVE = "active"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"


class Scopes:
    ASSETS_READ = "assets.read"
    ASSETS_WRITE = "assets.write"


class Roles:
    TENANT_ADMIN = "TenantAdmin"
    PORTFOLIO_MANAGER = "PortfolioManager"
    PORTFOLIO_VIEWER = "PortfolioViewer"

    ASSET_READERS = (TENANT_ADMIN, PORTFOLIO_MANAGER, PORTFOLIO_VIEWER)
    ASSET_WRITERS = (TENANT_ADMIN, PORTFOLIO_MANAGER)


class AuthorizationDecisions:
    ALLOWED = "allowed"
    MISSING_TENANT_CLAIM = "missing-tenant-claim"
    TENANT_INACTIVE = "tenant-inactive"
    TENANT_MISMATCH = "tenant-mismatch"
    MISSING_SCOPE = "missing-scope"
    MISSING_ROLE = "missing-role"
    MISSING_SERVICE_AUTHENTICATION = "missing-service-authentication"
    RESOURCE_TENANT_MISMATCH = "resource-tenant-mismatch"


class Tenants:
    ALPHA_CAPITAL = "AlphaCapital"
    BETA_WEALTH = "BetaWealth"
    GAMMA_FUND = "GammaFund"
    DELTA_EQUITY = "DeltaEquity"

    INITIAL_TENANTS = (ALPHA_CAPITAL, BETA_WEALTH, GAMMA_FUND)


class LogFields:
    """Structured log field names, ported from ``Contoso.AssetManagement.Shared.Observability.LogFields``."""

    TENANT_ID = "tenantId"
    USER_ID = "userId"
    CORRELATION_ID = "correlationId"
    OPERATION = "operation"
    AUTHORIZATION_DECISION = "authorizationDecision"
    RESULT = "result"
    STATUS_CODE = "statusCode"
