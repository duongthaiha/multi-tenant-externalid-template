# API Contracts

This folder is the source of truth for callable API contracts in the Contoso Asset Management multi-tenant POC.

## Files

| Contract | Audience | Source implementation |
|---|---|---|
| `frontend-api.openapi.yaml` | SPA, APIM, and UI-facing clients | `src/frontend-api/` |
| `backend-api.openapi.yaml` | Frontend API/BFF service client | `src/backend-api/` |
| `backend-mcp.openapi.yaml` | Foundry hosted agent MCP tools through APIM | APIM MCP API mapped to `src/backend-api/` |
| `custom-claims-provider.openapi.yaml` | Azure External ID Custom Authentication Extension | `src/custom-claims-provider/` |

## Contract coverage

| Surface | Contract location | Notes |
|---|---|---|
| SPA/APIM to Frontend API/BFF | `frontend-api.openapi.yaml` | Includes health, portfolio list, position detail, transaction approval, and Pattern 2 portfolio-agent chat. |
| Frontend API/BFF to Backend API | `backend-api.openapi.yaml` | Includes internal tenant data routes. Backend routes require the original user bearer token and approved service authentication. |
| Foundry hosted agent to backend MCP tools | `backend-mcp.openapi.yaml` | APIM-hosted MCP tool surface for portfolio list, position detail, and transaction approval. APIM validates the user token, route tenant, scope, role, and injects approved service proof before the backend re-validates. |
| Azure External ID to Custom Claims Provider | `custom-claims-provider.openapi.yaml` | Includes the `OnTokenIssuanceStart` callback and fail-closed empty-action behavior. |
| Tenant onboarding scripts | Runbook/script docs, not OpenAPI | `scripts/new-tenant.*` is not a hosted HTTP API unless a future implementation exposes it as one. |
| Seed and validation scripts | Runbook/script docs, not OpenAPI | Script arguments and expected outputs should be documented in the runbook. |
| Foundry hosted portfolio-agent endpoint | Frontend API contract only for Pattern 2 | The SPA must call `POST /tenants/{tenantId}/agent/chat` through APIM/BFF. Do not expose the Foundry agent endpoint directly without adding a new gateway/agent adapter contract and ADR update. |

## Shared conventions

- All tenant-sensitive API routes include `{tenantId}` as a path value to compare against the validated `extension_tenantId` token claim.
- `{tenantId}` is never routing authority by itself. Server code must use the validated token claim for authorization and Cosmos routing.
- Do not use `X-Tenant-Id`, request body values, query strings, or any other client-controlled input as tenant authority.
- `X-Correlation-ID` may be supplied by callers and is propagated across APIM, frontend API, backend API, and logs. If omitted, services generate one.
- Application-owned custom headers use canonical `X-...` casing, including `X-Service-Authorization`, `X-User-Authorization`, `X-Agent-Id`, and APIM response header `X-Authorization-Decision`; Foundry-required headers keep their platform casing such as `x-ms-user-identity` and `x-client-*`.
- JWTs, access tokens, refresh tokens, secrets, and full sensitive claim payloads must not be logged.
- User-facing operations use scopes `assets.read` and `assets.write`.
- Tenant roles are `TenantAdmin`, `PortfolioManager`, and `PortfolioViewer`.
- Pattern 2 portfolio-agent chat is a frontend API/BFF route. The request body contains a natural-language question and may include `conversationId` as an opaque BFF-issued conversation handle returned by an earlier chat response. Tenant authority still comes only from the validated token and route tenant binding.
- For portfolio-agent chat, `conversationId` is scoped by the BFF to the validated tenant and user and maps server-side to a Foundry Responses conversation ID plus hosted session. It is not a raw Foundry `conversation` or `agent_session_id`, and can be passed to `DELETE /tenants/{tenantId}/agent/sessions/{sessionHandle}` for cleanup.
- Foundry conversation history and hosted-session affinity are separate platform concepts. The BFF stores and sends both the server-side Foundry conversation ID and `agent_session_id` when needed, but the SPA must never see raw Foundry IDs.
- When the BFF invokes the hosted portfolio agent through Foundry Responses v2, long forwarded tokens use the same trusted headers the hosted agent consumes, not Responses metadata: `X-User-Authorization` for the External ID user token and `X-Service-Authorization` for the BFF service proof. The BFF also sends these as `x-client-*` headers because the hosted Responses SDK exposes only `x-client-` prefixed custom headers through `ResponseContext.ClientHeaders`. Use the American spelling `Authorization` consistently.

## Authentication summary

| Layer | Authentication and authorization |
|---|---|
| APIM/frontend API | Validates Azure External ID bearer token. Requires `extension_tenantId`, active `tenant_status`, route tenant match, operation-specific scopes, and `tenant_roles`. |
| Backend API | Re-validates the user bearer token and also requires approved service authentication from the frontend API through `X-Service-Authorization`. |
| Backend MCP tools | APIM validates the External ID user token for Foundry hosted agent MCP calls, rate-limits tool usage, removes spoofable headers, and supplies approved backend service proof. Backend API still re-validates both token planes. |
| Custom Claims Provider | Called by Azure External ID during token issuance. It resolves entitlements server-side and returns claims or an empty claims action to fail closed. |
| Portfolio Agent chat | Publicly represented by the frontend API `POST /tenants/{tenantId}/agent/chat` route and the authenticated cleanup route `DELETE /tenants/{tenantId}/agent/sessions/{sessionHandle}`. Agent/tool execution must not trust tenant IDs from prompts, request bodies, or session handles. |

## Keeping contracts aligned

Update the relevant OpenAPI file whenever any of these change:

- Routes, path parameters, or HTTP methods.
- Request or response DTOs.
- Status code behavior.
- Required scopes, roles, claims, or service authentication.
- Headers such as `Authorization`, `X-Service-Authorization`, or `X-Correlation-ID`.
- APIM policy behavior that affects callers.

Cross-check contracts against the implementation files listed in the table above and `docs/architecture-design.md`.
