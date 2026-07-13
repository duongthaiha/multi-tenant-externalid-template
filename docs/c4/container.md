# C4 model — Contoso Asset Management multi-tenant POC

This diagram set complements `docs/architecture-design.md` with a C4-style container view. It is the source of truth for container-level shape (what talks to what, and which data store each container is allowed to touch); keep it in sync whenever a container, a Cosmos account, or a trust boundary changes.

## Container diagram

```mermaid
C4Container
title Container diagram — Contoso Asset Management multi-tenant POC

Person(user, "Customer user", "Signs in through Azure External ID and uses the demo SPA.")

System_Boundary(customerIdentity, "Azure External ID (customer identity tenant)") {
    System(externalId, "Azure External ID", "Authenticates customer users and issues API access tokens with extension_tenantId, roles, tenant_status.")
}

System_Boundary(contoso, "Contoso Asset Management POC") {
    Container(spa, "SPA", "Azure Static Web Apps", "Demo UI. Signs in with MSAL. Only requests frontend API/APIM scopes, never backend scopes.")
    Container(apim, "APIM", "Azure API Management", "Public API front door. Validates JWTs, required claims, tenant status, route-to-token tenant binding, strips spoofable headers, rate-limits per tenant. Exposes both the UI API and the Foundry-agent-facing MCP API.")
    Container(ccp, "Custom Claims Provider", "Azure Functions (VNet integrated)", "OnTokenIssuanceStart callback. Resolves active tenant and roles. Fails closed on missing/ambiguous tenant.")
    Container(bff, "Frontend API / BFF", "Azure Container Apps (VNet integrated)", "UI-shaped routes. Re-validates the user token and tenant binding. Never accesses Cosmos directly. Invokes the portfolio agent with the user token, an internal service token, tenant, and correlation context.")
    Container(agent, "Portfolio Agent", "Azure AI Foundry hosted agent (Responses v2 primary; Invocations rollback-only)", "Tenant-scoped portfolio chat. Tool calls go through the APIM MCP route to the backend API, never directly to tenant Cosmos.")
    Container(backend, "Backend API", "Azure Container Apps (VNet integrated)", "Final authorization and data-access boundary. Re-validates user token + internal service token, enforces scopes/roles, resolves tenant Cosmos routing server-side.")
    Container(directory, "TenantDirectory service", "In backend API / Custom Claims Provider", "Resolves tenant metadata and Cosmos routing from the control-plane store.")

    ContainerDb(controlCosmos, "Control-plane Cosmos DB", "Azure Cosmos DB (SQL API), single account", "Tenant directory, user-tenant memberships, role assignments, tenant status, Cosmos routing metadata.")
    ContainerDb(tenantCosmosA, "AlphaCapital Cosmos DB", "Azure Cosmos DB (SQL API), one account per tenant", "Portfolios, positions, transaction approvals for AlphaCapital only.")
    ContainerDb(tenantCosmosB, "BetaWealth Cosmos DB", "Azure Cosmos DB (SQL API), one account per tenant", "Portfolios, positions, transaction approvals for BetaWealth only.")
    ContainerDb(tenantCosmosC, "GammaFund Cosmos DB", "Azure Cosmos DB (SQL API), one account per tenant", "Portfolios, positions, transaction approvals for GammaFund only.")
    ContainerDb(agentMemoryCosmos, "Agent-memory Cosmos DB", "Azure Cosmos DB (SQL API), single account, one database per tenant (agent-memory-{tenant})", "Portfolio agent conversation/tool-run session state only (agentSessions container, partitioned by tenantId, 30-day TTL). No portfolio, position, or transaction data. Accessed only by the portfolio agent's managed identity.")

    Container(appConfig, "App Configuration", "Azure App Configuration", "Non-secret environment settings and feature values.")
    Container(keyVault, "Key Vault", "Azure Key Vault", "Secrets and certificates, accessed via managed identity.")
    Container(monitor, "Observability", "Application Insights + Log Analytics", "Structured traces, dependency telemetry, correlation IDs, tenant-mismatch alerting.")
}

System_Boundary(mngEnv, "Internal MngEnv Entra tenant") {
    System(mngEnvEntra, "MngEnv Entra tenant", "Issues internal service tokens for frontend-to-backend and agent-to-backend service authentication. Distinct from Azure External ID.")
}

Rel(user, spa, "Uses", "HTTPS")
Rel(spa, externalId, "Signs in / acquires token", "OIDC/OAuth2 (MSAL, frontend API/APIM audience only)")
Rel(externalId, ccp, "OnTokenIssuanceStart", "HTTPS callback")
Rel(ccp, directory, "Resolve tenant + roles")
Rel(directory, controlCosmos, "Read tenant/membership/role data", "Managed identity, disableLocalAuth")

Rel(spa, apim, "Calls /tenants/{tenantId}/... with bearer token", "HTTPS")
Rel(apim, bff, "Forwards sanitized request")
Rel(bff, mngEnvEntra, "Acquires internal service token", "Managed identity")
Rel(bff, agent, "Invoke hosted agent (user token + service token + tenant + correlation)")
Rel(bff, backend, "Calls backend (user token + service token)")
Rel(agent, apim, "MCP tool call (delegated user token)")
Rel(apim, backend, "Forward backend route (user token + X-Service-Authorization)")
Rel(backend, mngEnvEntra, "Validates internal service token", "Managed identity")
Rel(backend, directory, "Resolve tenant Cosmos routing")
Rel(backend, tenantCosmosA, "Query/mutate (same tenant only)", "Managed identity, disableLocalAuth, private endpoint")
Rel(backend, tenantCosmosB, "Query/mutate (same tenant only)", "Managed identity, disableLocalAuth, private endpoint")
Rel(backend, tenantCosmosC, "Query/mutate (same tenant only)", "Managed identity, disableLocalAuth, private endpoint")
Rel(agent, agentMemoryCosmos, "Save/read conversation session state (same tenant partition only) — only exercised by the Responses protocol handler; see gap note below", "Managed identity, disableLocalAuth, private endpoint")

Rel(appConfig, bff, "Config")
Rel(appConfig, backend, "Config")
Rel(keyVault, bff, "Secrets via managed identity")
Rel(keyVault, backend, "Secrets via managed identity")
Rel(keyVault, ccp, "Secrets via managed identity")
Rel(monitor, apim, "Telemetry")
Rel(monitor, bff, "Telemetry")
Rel(monitor, agent, "Telemetry")
Rel(monitor, backend, "Telemetry")
Rel(monitor, ccp, "Telemetry")
```

## Key correction vs. earlier revisions

Earlier drafts of the architecture doc described the portfolio agent as never accessing Cosmos directly. That is only true for **tenant business data**. In the actual implementation (`src/portfolio-agent/Program.cs`, `infra/modules/cosmos-agent-memory.bicep`), the portfolio agent's own managed identity connects directly to a **fourth Cosmos data plane** — the agent-memory account — to persist and retrieve its conversation/session state:

- Separate Cosmos account from the tenant business-data accounts and the control-plane account.
- One database per tenant (`agent-memory-{tenant}`), single `agentSessions` container, partitioned by `/tenantId`, 30-day default TTL.
- `disableLocalAuth: true`, `publicNetworkAccess: 'Disabled'`, private endpoint + `privatelink.documents.azure.com`, RBAC-only access for the portfolio agent's managed identity — consistent with the rest of the design's Cosmos conventions.
- The SPA-facing `conversationId` is now an opaque BFF-issued conversation handle, not a source of tenant authority or raw Foundry ID; tenant context always comes from the validated token/service context (`PortfolioToolContext`), never from the handle or its contents.
- This store is unrelated to the `store: false` flag used on the Foundry Responses HTTP call in the frontend API — that flag governs Foundry's own cloud-side response storage for that protocol call only, not this hosted-agent-side Cosmos persistence.

## Implemented — Responses v2 hosted-session affinity

Validated against `src/frontend-api/FrontendHandlers.cs`, `src/frontend-api/Agent/FoundryPortfolioAgentClient.cs`, and the Responses-only hosted-agent declaration:

- `CosmosAgentSessionStore` is invoked by `MapFoundryResponses()` (the Responses protocol handler, registered via `AddFoundryResponses(agent, agentSessionStore)`) and remains the separate conversation/tool-run state store.
- The frontend API uses the **Responses v2** protocol. The hosted-agent declaration advertises Responses v2 only; Azure keeps Invocations endpoint/config support with `PortfolioAgent__UseInvocations=false`, but the BFF application code no longer includes an Invocations runtime path.
- `FoundryPortfolioAgentClient.AskResponsesAsync` now sends the server-side stored `agent_session_id` and Foundry conversation reference in the Responses v2 body with `{ input, store, agent_session_id, conversation, metadata }`. Metadata only carries small tenant/user/correlation values; long user and service tokens are forwarded in the same trusted headers the hosted agent consumes (`X-User-Authorization` and `X-Service-Authorization`) plus `x-client-` prefixed copies for `ResponseContext.ClientHeaders`. The SPA receives only the opaque BFF handle in `conversationId` and can clean it up through `DELETE /api/tenants/{tenantId}/agent/sessions/{sessionHandle}`.
- **Net effect:** Foundry Responses message history and hosted-session sandbox affinity are implemented without exposing raw Foundry IDs to the SPA.

See `docs/architecture-design.md` sections 4, 5, 7 ("Portfolio-agent conversation persistence"), 9 ("Agent-memory data"), 10, 16, and 17 for the corresponding narrative updates.
