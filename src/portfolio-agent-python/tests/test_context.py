# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for portfolio_agent.context: trusted context resolution, contextvars, TTL cache."""

from __future__ import annotations



from portfolio_agent.constants import Headers
from portfolio_agent.context import (
    PortfolioToolContext,
    PortfolioToolContextAccessor,
    require_tool_context,
)


def _client_headers(**trusted: str) -> dict[str, str]:
    """Build a client_headers dict in the ``x-client-<lower-header>`` shape the SDK produces."""
    return {Headers.client_forwarded(name).lower(): value for name, value in trusted.items()}


class TestPortfolioToolContext:
    def test_is_complete_requires_all_fields(self) -> None:
        complete = PortfolioToolContext("AlphaCapital", "user-1", "user-token", "service-token", "corr-1")
        assert complete.is_complete

        incomplete = PortfolioToolContext("AlphaCapital", "user-1", "", "service-token", "corr-1")
        assert not incomplete.is_complete

    def test_from_client_headers_extracts_bearer_tokens(self) -> None:
        headers = _client_headers(**{
            Headers.AUTHENTICATED_TENANT: "AlphaCapital",
            Headers.AUTHENTICATED_USER: "user-1",
            Headers.USER_AUTHORIZATION: "Bearer user-token-value",
            Headers.SERVICE_AUTHORIZATION: "Bearer service-token-value",
            Headers.CORRELATION_ID: "corr-1",
        })

        context = PortfolioToolContext.from_client_headers(headers)

        assert context.tenant_id == "AlphaCapital"
        assert context.user_id == "user-1"
        assert context.user_access_token == "user-token-value"
        assert context.service_token == "service-token-value"
        assert context.correlation_id == "corr-1"
        assert context.is_complete

    def test_from_client_headers_generates_correlation_id_when_missing(self) -> None:
        headers = _client_headers(**{
            Headers.AUTHENTICATED_TENANT: "AlphaCapital",
            Headers.AUTHENTICATED_USER: "user-1",
            Headers.USER_AUTHORIZATION: "Bearer token",
            Headers.SERVICE_AUTHORIZATION: "Bearer token2",
        })

        context = PortfolioToolContext.from_client_headers(headers)

        assert context.correlation_id  # non-empty, generated
        assert len(context.correlation_id) == 32  # uuid4().hex

    def test_from_client_headers_rejects_non_bearer_authorization(self) -> None:
        headers = _client_headers(**{
            Headers.USER_AUTHORIZATION: "Basic dXNlcjpwYXNz",
        })

        context = PortfolioToolContext.from_client_headers(headers)

        assert context.user_access_token == ""

    def test_from_metadata_reads_contoso_keys(self) -> None:
        metadata = {
            "contoso_tenant_id": "BetaWealth",
            "contoso_user_id": "user-2",
            "contoso_user_access_token": "meta-user-token",
            "contoso_service_token": "meta-service-token",
            "contoso_correlation_id": "corr-2",
        }

        context = PortfolioToolContext.from_metadata(metadata)

        assert context.tenant_id == "BetaWealth"
        assert context.is_complete

    def test_from_metadata_none_returns_incomplete_context(self) -> None:
        context = PortfolioToolContext.from_metadata(None)
        assert not context.is_complete
        assert context.correlation_id  # still generated

    def test_from_metadata_and_client_headers_prefers_metadata_for_identity(self) -> None:
        metadata = {
            "contoso_tenant_id": "BetaWealth",
            "contoso_user_id": "meta-user",
            "contoso_correlation_id": "meta-corr",
        }
        headers = _client_headers(**{
            Headers.AUTHENTICATED_TENANT: "AlphaCapital",  # should lose to metadata
            Headers.AUTHENTICATED_USER: "header-user",  # should lose to metadata
            Headers.USER_AUTHORIZATION: "Bearer header-user-token",  # should win (header wins tokens)
            Headers.SERVICE_AUTHORIZATION: "Bearer header-service-token",  # should win
            Headers.CORRELATION_ID: "header-corr",  # should lose to metadata
        })

        context = PortfolioToolContext.from_metadata_and_client_headers(metadata, headers)

        assert context.tenant_id == "BetaWealth"
        assert context.user_id == "meta-user"
        assert context.correlation_id == "meta-corr"
        assert context.user_access_token == "header-user-token"
        assert context.service_token == "header-service-token"

    def test_from_metadata_and_client_headers_falls_back_to_headers_for_identity(self) -> None:
        headers = _client_headers(**{
            Headers.AUTHENTICATED_TENANT: "GammaFund",
            Headers.AUTHENTICATED_USER: "header-user",
            Headers.USER_AUTHORIZATION: "Bearer header-user-token",
            Headers.SERVICE_AUTHORIZATION: "Bearer header-service-token",
            Headers.CORRELATION_ID: "header-corr",
        })

        context = PortfolioToolContext.from_metadata_and_client_headers(None, headers)

        assert context.tenant_id == "GammaFund"
        assert context.user_id == "header-user"
        assert context.is_complete
        # Ports a subtle, deliberately-preserved C# quirk: `from_metadata` (both
        # implementations) always synthesizes a correlation id when metadata is
        # absent/empty, so the *metadata* side of the merge is never actually
        # empty for correlation id -- the header correlation id can never win
        # here. This matches `PortfolioToolContext.FromMetadataAndClientHeaders`
        # (C#) exactly; it is intentional, not a bug in this port.
        assert context.correlation_id != "header-corr"
        assert len(context.correlation_id) == 32


class TestPortfolioToolContextAccessorAndCache:
    def test_require_tool_context_returns_current_context(self) -> None:
        context = PortfolioToolContext("AlphaCapital", "user-1", "token", "service-token", "corr-1")
        PortfolioToolContextAccessor.set_current(context)

        resolved = require_tool_context("ListPortfolios")

        assert resolved is context

    def test_require_tool_context_fails_closed_when_nothing_available(self) -> None:
        try:
            require_tool_context("ListPortfolios")
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "trusted" in str(exc).lower() or "context" in str(exc).lower()

    def test_require_tool_context_fails_closed_for_incomplete_current_context(self) -> None:
        incomplete = PortfolioToolContext("AlphaCapital", "", "", "", "corr-1")
        PortfolioToolContextAccessor.set_current(incomplete)

        try:
            require_tool_context("ListPortfolios")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass
