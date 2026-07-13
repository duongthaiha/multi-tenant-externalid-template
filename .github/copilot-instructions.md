# Copilot instructions for this repository

## Repository state and commands

Prefer the commands defined by the checked-in ecosystem files:

- Provision/deploy: `azd up`
- Provision only: `azd provision`
- Deploy only: `azd deploy`
- Bicep validation: `az bicep build --file infra/main.bicep`
- Preflight review: `az deployment sub what-if`

## High-level architecture

The product is the Contoso Asset Management multi-tenant POC. Use `docs/product-backlog.md` as the product scope, `docs/architecture-design.md` as the current target architecture, and `docs/threat-model.md` for security rationale.

The target runtime architecture is:

- Azure External ID authenticates customer users.
- An Azure Functions Custom Claims Provider handles `OnTokenIssuanceStart` and adds `extension_tenantId`, `roles`, and `tenant_status` to API access tokens.
- A shared APIM instance is the public API enforcement front door.
- The SPA calls the frontend API/BFF through APIM.
- The frontend API/BFF runs on Azure Container Apps, re-validates tokens, shapes UI responses, and never accesses Cosmos DB directly.
- The backend API runs on Azure Container Apps and is the final authorization and data-access boundary.
- The SPA only requests External ID tokens for the frontend API/APIM audience; it must not know or request backend API scopes.
- Frontend-to-backend service authentication uses the internal MngEnv Entra tenant and managed identity/service credentials, not the External ID customer tenant.
- Tenant data is physically isolated with one Cosmos DB account per business tenant.
- A separate control-plane Cosmos DB account stores tenant directory, user memberships, role assignments, and tenant-to-Cosmos routing metadata.

The initial business tenants are `AlphaCapital`, `BetaWealth`, and `GammaFund`; onboarding automation must also prove a fourth tenant, `DeltaEquity`.

There are five intended enforcement layers:

1. Token issuance: External ID calls the Custom Claims Provider, which resolves the active business tenant and tenant roles from the control-plane store.
2. Gateway: APIM validates the JWT, required claims, tenant status, route tenant binding, spoofable headers, and per-tenant rate limits.
3. Frontend API/BFF: re-validates the user token, handles SPA-specific orchestration, and forwards only trusted context to the backend API.
4. Backend API: re-validates the user token and service authentication, enforces scopes/roles/resource checks, and resolves tenant routing server-side.
5. Data plane: Cosmos DB authorizes the backend managed identity against the tenant-specific account.

The identity tenant and the business tenant are different concepts in this design. Azure External ID is the customer identity plane; `extension_tenantId` is the SaaS business-tenant binding used for authorization and data routing.

Expected code layout once implementation begins:

- `src/spa/` for the demo SPA.
- `src/frontend-api/` for the UI-facing BFF.
- `src/backend-api/` for authorization-sensitive tenant data access.
- `src/custom-claims-provider/` for the token issuance callback.
- `contracts/` for OpenAPI API contracts and shared API contract conventions.
- `scripts/seed-data.*`, `scripts/validate-deployment.*`, and `scripts/new-tenant.*` for demo data, validation, and tenant onboarding.

## Security and tenancy conventions

- Treat `extension_tenantId` from a validated API token as the authoritative business-tenant selector.
- Never use `X-Tenant-Id`, request body values, query strings, or other client-controlled input as the source of tenant authority.
- Tenant switching requires a new token issued for the selected tenant; do not implement header-based tenant switching.
- APIM must validate External ID JWTs with `validate-jwt` and OIDC discovery, require tenant/status/scope claims, sanitize spoofable identity headers, and enforce route tenant equals token tenant.
- The frontend API and backend API must both re-check tenant binding; do not rely on APIM as the only authorization layer.
- The backend API must validate two token planes independently: the forwarded External ID user token for delegated tenant/scopes/roles and the internal Entra service token proving the frontend API caller.
- The backend API must resolve tenant Cosmos endpoints from the server-side TenantDirectory using the validated token tenant.
- User JWTs must never be used as Cosmos DB credentials or forwarded to Cosmos DB.
- Cosmos DB access uses managed identity and Entra RBAC; all Cosmos accounts must use `disableLocalAuth: true`.
- Cosmos DB accounts must use `publicNetworkAccess: 'Disabled'`, private endpoints, and the `privatelink.documents.azure.com` private DNS zone.
- Keep the Custom Claims Provider fail-closed: missing tenant, unresolved roles, inactive tenant, or timeout must not produce an API token with usable tenant access.
- Do not log JWTs, access tokens, refresh tokens, secrets, or full sensitive claim payloads.

## API and authorization conventions

The POC operations are portfolio list, position detail, and transaction approval.

- Use scopes `assets.read` and `assets.write`.
- Use roles `TenantAdmin`, `PortfolioManager`, and `PortfolioViewer`.
- Frontend API routes are UI-facing and can differ from backend API routes.
- Backend API routes should treat route tenant IDs only as values to compare against the token claim, not as routing authority.
- Transaction approval requires write scope plus `TenantAdmin` or `PortfolioManager`.
- Portfolio and position reads require read scope plus one of the three tenant roles.
- Backend endpoints must reject direct calls that lack the approved frontend/service authentication.

## Infrastructure conventions

The backlog expects Azure resources to be authored as Bicep modules under `infra/` and deployed through `azd`.

Expected module boundaries:

- `network.bicep`
- `apim.bicep`
- `container-apps.bicep`
- `functions.bicep`
- `cosmos-control-plane.bicep`
- `cosmos-tenant.bicep`
- `app-configuration.bicep`
- `key-vault.bicep`
- `monitoring.bicep`

Parameterize environment name, location, tenant names, External ID settings, SKU choices, and resource names. Do not hard-code subscription IDs, tenant IDs, secrets, or environment-specific resource names.

Use dedicated subnets named `snet-apps`, `snet-func`, and `snet-pe` unless an ADR changes the network design.

## Observability conventions

Structured logs should consistently include:

- `tenantId`
- `userId`
- `correlationId`
- operation name
- authorization decision or operation result
- HTTP status code

APIM, frontend API, backend API, Custom Claims Provider, and validation scripts should preserve or emit correlation IDs so a demo flow can be traced end to end in Application Insights and Log Analytics.

Create alerting for tenant-mismatch 403 spikes: more than 5 tenant-mismatch 403 responses in 5 minutes should trigger the configured action group.

## Documentation conventions

- Keep `docs/product-backlog.md` as the backlog/source of planned work.
- Keep `docs/architecture-design.md` aligned with implementation decisions as the architecture changes.
- Keep API contracts under `contracts/` aligned with the SPA, APIM policy, frontend API, backend API, and Custom Claims Provider.
- Update the relevant OpenAPI file in `contracts/` whenever routes, DTOs, status codes, auth requirements, scopes, roles, or headers change.
- Treat `contracts/README.md` as the shared API contract convention document for tenant authority, correlation IDs, and service-to-service authentication.
- Do not document `X-Tenant-Id` as an authority header; route tenant IDs are values to compare against validated token claims only.
- Add ADRs under `docs/adr/` for locked decisions called out in the backlog: External ID, Custom Claims Provider, Cosmos account-per-tenant, shared APIM, and developer access.
- Do not introduce production behavior that contradicts the documented POC guardrails without updating the architecture docs or adding an ADR.

## MCP server guidance

The repository configures Context7 in `.github/mcp.json`. Use its
`resolve-library-id` and `query-docs` tools for current, version-specific
documentation about libraries, SDKs, APIs, CLI tools, and Azure services before
implementing or recommending integration details. Prefer authoritative
Microsoft documentation when Context7 results are incomplete or conflict with
Microsoft guidance. Context7 supplements external technical research; it does
not replace this repository's `docs/` files as the source of truth for product
scope and architecture decisions.

Playwright is also relevant once the SPA exists. Use it to inspect and validate
the demo UI flows that matter to this POC: MSAL sign-in, decoded claim display,
portfolio list, position detail, transaction approval, and visible cross-tenant
403 behavior.
## Repo commit
Do commit messages in the style of "docs: update architecture design with new tenant onboarding flow" or "feat: implement Custom Claims Provider token enrichment". Use `docs:` for documentation changes, `feat:` for new features, `fix:` for bug fixes, and `refactor:` for code restructuring without changing behavior.

Commit often so we can reverse the change if needed.