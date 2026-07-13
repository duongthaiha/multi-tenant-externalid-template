# ADR 0006: Pattern 2 Portfolio Agent Public Boundary

## Status

Proposed

## Context

The portfolio agent is currently a Foundry hosted agent with local demo tools and static cross-tenant sample data. It is intentionally disconnected from the Contoso API and Cosmos data path.

The multi-tenant architecture requires `extension_tenantId` from a validated token to remain the only tenant authority. The SPA calls APIM, APIM forwards to the Frontend API/BFF, and the Backend API is the final authorization and data-access boundary. Tenant Cosmos access is performed only by backend managed identity.

Pattern 2 introduces a shared portfolio agent for a POC chat experience. The main design question is whether the public chat boundary should be:

1. APIM -> Frontend API/BFF -> Portfolio Agent.
2. APIM -> Portfolio Agent directly.

## Decision

Use **APIM -> Frontend API/BFF -> Portfolio Agent** as the default Pattern 2 public boundary.

The Frontend API/BFF will expose `POST /api/tenants/{tenantId}/agent/chat`, reuse existing tenant authorization, resolve correlation IDs, and invoke the shared hosted portfolio agent with server-side tenant context. Tenant data access will continue through Backend API routes. The portfolio agent must not access Cosmos directly.

Direct APIM-to-agent routing remains an evaluated alternative, but it must prove all of the following before replacing the BFF route:

- APIM can authenticate to and invoke the Foundry hosted agent endpoint correctly.
- The agent receives trusted tenant context without accepting prompt, body, query string, or header tenant values as authority.
- Backend API calls from the agent have approved service authentication.
- User tokens, service tokens, and sensitive claims are never logged, captured in prompts, or exposed in traces.
- Correlation IDs, per-tenant rate limits, and tenant-mismatch behavior remain equivalent to the BFF path.

## Consequences

Positive consequences:

- Preserves the current SPA -> APIM -> BFF API shape.
- Reuses existing BFF tenant authorization and backend service-token acquisition.
- Keeps the Backend API as the final data authority.
- Allows a safer BFF-hosted tool adapter fallback if hosted-agent tool context cannot safely receive per-request tokens.
- Avoids exposing Foundry hosted-agent endpoint details to the SPA.

Negative consequences:

- Adds a BFF route and agent invocation abstraction.
- The BFF remains in the latency path for agent chat.
- Direct APIM-to-agent routing may be revisited later if Foundry endpoint auth, identity, and networking support become simpler and equally safe.

