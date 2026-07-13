# ADR 0007: APIM backend MCP gateway for hosted agents

## Status

Proposed

## Context

The POC already uses APIM as the shared public front door for SPA traffic. The existing user route is SPA -> APIM -> Frontend API/BFF -> Backend API, and the backend API remains the final authorization and data-access boundary.

The portfolio hosted agent needs a tool surface for the same backend capabilities. Exposing the backend directly to the agent would bypass APIM governance and would make it harder to apply consistent tenant checks, rate limits, diagnostics, and spoofable-header cleanup. The backend API also requires two token planes: the original External ID user token and an internal service proof in `X-Service-Authorization`.

## Decision

Expose backend capabilities to Foundry hosted agents through an APIM-governed MCP API at `/mcp/assets`.

The MCP route is intended for Foundry hosted agents only and maps to the existing backend operations:

- list portfolios,
- get position detail,
- approve transaction.

APIM validates the External ID user token, checks required tenant claims, compares route tenant to `extension_tenantId`, enforces operation scopes and tenant roles, rate-limits tool calls, removes spoofable identity headers, and supplies approved backend service proof. The backend API still re-validates the user token and service proof and remains the final authority.

For this demo, the backend Container App uses public ingress so APIM can directly reach it. Public ingress is acceptable only because backend routes still require both a valid user token and a valid internal Entra service token carrying the required Backend Service Auth app role.

## Consequences

- Foundry hosted agents get a governed MCP tool endpoint without making tenant routing client-controlled.
- APIM becomes the consistent governance point for user and agent/API traffic.
- The backend API keeps its defense-in-depth authorization model.
- Transaction approval as an MCP tool increases risk and must continue to require `assets.write` plus `TenantAdmin` or `PortfolioManager`.
- Backend public ingress is a demo trade-off and should be revisited for production.
