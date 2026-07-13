# Contoso Asset Management Multi-Tenant POC Backlog

## Assumptions

| Assumption | Why It Matters | Validation Needed |
|---|---|---|
| Greenfield POC; no existing token-exchange service to migrate | No legacy dependency stories needed | Confirm no live customers on any current system |
| Domain: Contoso FSI asset management SaaS with 3 fictional customer tenants: AlphaCapital, BetaWealth, GammaFund | Scopes the demo data and API surface | Confirm tenant names and sample data shape |
| Frontend API, backend API, and Custom Claims Provider implemented in C# (.NET 8) | Matches the architecture while separating UI-facing orchestration from data-plane access | Confirm language stack with engineering team |
| Hosting: Azure Container Apps for frontend API and backend API; Azure Functions for Custom Claims Provider | Best fit for this pattern | Architecture decision |
| Deployment region: East US, or another region with capacity for all required services | All required services must be available | Confirm no data-residency constraint |
| Private endpoints and `disableLocalAuth` required from day one | Security baseline for the POC | Confirmed |
| 3 tenants prove the isolation and onboarding automation pattern | Sufficient for POC validation | Confirmed |
| `azd` and Bicep from the first sprint | Makes the POC repeatable | Required guardrail |

## POC Goal

Prove that Contoso Asset Management, a fictional FSI SaaS, can securely serve three independent customer tenants from a shared platform using Azure External ID for sign-in, a Custom Claims Provider to stamp tenant context and roles into access tokens at issuance time, APIM as the front-door enforcement gateway, a frontend API/BFF for UI-facing orchestration, a backend API for tenant authorization and data access, and one physically isolated Cosmos DB account per tenant. The full POC must be provisioned through Bicep via `azd up`, with private endpoints and no public database access from day one.

## Backlog

### Epic 1: Product Discovery and Scope

**Goal:** Finalize the POC boundary, demo scenario, and success criteria so the team builds the right thin slice.

| Story | Owner Type | Acceptance Criteria | Notes |
|---|---|---|---|
| As the product owner, I want documented POC success criteria so the team knows what done looks like | Product | Given the backlog, when reviewed, then each epic has a measurable DoD item tied to the demo scenario | Block sprint planning until complete |
| As the product owner, I want the three demo tenants and their test users defined so data setup is unambiguous | Product | Given the tenant list, when reviewed, then AlphaCapital, BetaWealth, and GammaFund each have at least 2 test users with different roles documented | Roles: TenantAdmin, PortfolioManager, PortfolioViewer |
| As the product owner, I want the frontend API and backend API surfaces defined so APIM policy, UI orchestration, and data access code can be built consistently | Architecture | Given the API spec, when reviewed, then frontend API routes and backend API routes are documented separately; at minimum the user-facing flow supports portfolio list, position detail, and transaction approval with required scopes and roles | Use scopes `assets.read`, `assets.write` |
| As the product owner, I want non-goals stated so the team does not over-build | Product | Given the non-goals list, when reviewed, then real financial data, production SLA, multi-region geo-replication, and general-purpose end-user registration UX are explicitly out of scope | Controlled, pre-authorized workforce federation remains in scope |

### Epic 2: Azure Infrastructure and Deployment

**Goal:** Stand up the full environment through `azd up` so every subsequent epic deploys and validates incrementally.

| Story | Owner Type | Acceptance Criteria | Notes |
|---|---|---|---|
| As a DevOps engineer, I want an `azure.yaml` at the repo root mapping each service to its Azure host so `azd` can provision and deploy | DevOps | Given the repo, when `azd up` runs against a clean subscription, then all Azure resources are created and all services are deployed without manual steps | Required before other epics deploy |
| As a DevOps engineer, I want an `infra/` folder with Bicep modules for each resource so IaC is the source of truth | DevOps | Given the `infra/` folder, when `azd provision` runs, then Azure Function, APIM, Container App Environment, frontend API, backend API, App Configuration, Key Vault, Log Analytics, and Application Insights are deployed | Use Bicep modules per service |
| As a DevOps engineer, I want environment-specific parameters so different environments can be provisioned from the same Bicep | DevOps | Given `infra/main.bicep`, when reviewed, then no subscription IDs, tenant IDs, or resource names are hard-coded | Parameterize environment, location, and tenant names |
| As a DevOps engineer, I want documented `azd env` variables so the team knows what must be set before `azd up` | DevOps | Given the README, when reviewed, then every required `azd env set` key is listed with description and secret classification | No secrets in `.azure/` or source control |
| As a DevOps engineer, I want Bicep linting and what-if checks in the dev workflow so broken IaC fails before provision | DevOps | Given `az bicep build` and `az deployment sub what-if`, when run, then linting errors and destructive change warnings are visible before apply | Optional CI step for POC |

### Epic 3: Private Networking and Security

**Goal:** Wire networking and identity before data flows through the system. Every service reaches the database privately; public access is disabled.

| Story | Owner Type | Acceptance Criteria | Notes |
|---|---|---|---|
| As a security engineer, I want a VNet with dedicated subnets defined in Bicep so private connectivity is the foundation | Security/DevOps | Given `infra/modules/network.bicep`, when deployed, then a VNet exists with `snet-apps`, `snet-pe`, and `snet-func` subnets | Document CIDR choices |
| As a security engineer, I want a private endpoint for each Cosmos DB tenant account so database traffic stays private | Security/DevOps | Given 3 Cosmos accounts, when deployed, then each account has a private endpoint in `snet-pe`, a private DNS zone `privatelink.documents.azure.com`, and VNet-linked DNS resolution to private IPs | One private endpoint per tenant account |
| As a security engineer, I want `publicNetworkAccess` disabled on every Cosmos account so public-route access is blocked | Security | Given each Cosmos Bicep module, when deployed, then `publicNetworkAccess: 'Disabled'` and `disableLocalAuth: true` are set; outside-VNet access is rejected | Aligns with `docs/architecture-design.md` |
| As a security engineer, I want the frontend API and backend API Container Apps integrated with the VNet so service-to-service and data access traffic is private | Security/DevOps | Given the Container App Bicep, when deployed, then the frontend API can reach the backend API privately, the backend API can reach Cosmos private endpoints, and connectivity tests succeed from the deployed apps | |
| As a security engineer, I want the Custom Claims Provider Function integrated with the VNet so it can reach the control-plane Cosmos account privately | Security/DevOps | Given the Function Bicep, when deployed, then VNet integration is configured and the Function reaches the tenant-mapping store privately | |
| As a security engineer, I want a developer-access decision recorded so local development is unblocked without weakening the model | Architecture | Given the ADR, when reviewed, then cloud-only remains the baseline and an optional Bastion jumpbox provides read-only Data Explorer access without enabling public Cosmos access | ADR 0008 gates the jumpbox behind `enableJumpbox` |
| As a security engineer, I want all Key Vault secrets accessed via managed identity so no connection strings appear in app settings | Security | Given app configuration, when reviewed, then no secret value appears in `azure.yaml`, Bicep parameters, or `azd` environment files | |
| As a security engineer, I want managed identities with least-privilege RBAC so no shared credentials exist | Security/DevOps | Given Bicep role assignments, when deployed, then frontend API MI can call only approved backend API/service dependencies, backend API MI has Cosmos data-plane access to each tenant account, Function MI can read the control-plane store, and app services have only required Key Vault/App Configuration roles | One role assignment resource per dependency |

### Epic 4: Data and Integration

**Goal:** Connect the POC to realistic FSI data. Provision per-tenant Cosmos accounts with seeded asset portfolios.

| Story | Owner Type | Acceptance Criteria | Notes |
|---|---|---|---|
| As a DevOps engineer, I want a Bicep module for the control-plane tenant-mapping store so tenant resolution is available at runtime | DevOps | Given the module, when deployed, then a separate Cosmos account exists for tenant directory data with `disableLocalAuth: true` and private endpoint configured | Separate from tenant data accounts |
| As a DevOps engineer, I want Bicep modules and automation for per-tenant Cosmos accounts so adding a tenant is repeatable | DevOps | Given the automation, when run with a tenant name, then a new Cosmos account, private endpoint, RBAC assignment, and TenantDirectory record are created | Proves onboarding automation |
| As a data engineer, I want seed data scripts for each tenant so the demo has realistic FSI content | Data | Given the seed scripts, when run post-provision, then each tenant database has at least 2 portfolios, 5 positions per portfolio, and 1 pending transaction approval | Use visibly different data per tenant |
| As a data engineer, I want the entitlement store seeded so the Custom Claims Provider resolves claims for all test users | Data | Given the seeded store, when a test user signs in, then `extension_tenantId` and correct `roles` appear in the issued JWT | 2 users per tenant minimum |

### Epic 5: User Experience and Workflow

**Goal:** Define what the user sees and does so application implementation has a clear target.

| Story | Owner Type | Acceptance Criteria | Notes |
|---|---|---|---|
| As a product owner, I want the end-to-end workflow documented so the demo scenario is unambiguous | Product | Given the workflow diagram, when reviewed, then the happy path is shown: sign-in, enriched token, portfolio list, position detail, transaction approval, and tenant switch by new token | Commit diagram to `docs/` |
| As a UX designer, I want the multi-tenant user flow defined so tenant selection is handled securely | Product/Architecture | Given the UX spec, when reviewed, then multi-tenant users select a tenant during sign-in and receive a new token per tenant selection | No `X-Tenant-Id` switching |
| As a developer, I want a simple SPA that calls the frontend API through APIM with an MSAL token so the POC has an interactive front end | Engineering | Given the SPA, when loaded, then the user signs in via MSAL, sees decoded token claims, lists portfolios, and approves a transaction through frontend API routes | Demo clarity over production UX |
| As a developer, I want the SPA to display `extension_tenantId` and `roles` so isolation is visually proven | Engineering | Given the SPA, when signed in, then the decoded claims section shows `extension_tenantId`, `roles`, and `tenant_status` | Key demo moment |
| As a workforce customer, I want to authenticate with my home Entra tenant so I can use the SPA without a separate local password | Engineering/Identity | Given a pre-authorized workforce user, when the user selects the workforce identity provider, then home-tenant authentication returns to External ID and the SPA receives an External ID-issued access token with the approved business tenant and roles | Require enterprise-app assignment, federated External ID user, and pre-seeded entitlement |
| As a user, I want to enter my email before sign-in so the SPA can select the appropriate External ID journey | Engineering/Identity | Given an exact enabled workforce-domain match, when the user continues, then MSAL sends `login_hint` and `domain_hint`; disabled domains are blocked, unknown domains use local External ID sign-in, and a provider-picker fallback remains available | Discovery only; email/domain is never tenant authority |

### Epic 6: Frontend API and Backend API Implementation

**Goal:** Implement the UI-facing frontend API/BFF and the tenant data backend API while preserving all five security layers from `docs/architecture-design.md`.

| Story | Owner Type | Acceptance Criteria | Notes |
|---|---|---|---|
| As a developer, I want the Azure Function Custom Claims Provider implemented so External ID stamps tenant and role claims into every API token | Engineering | Given the Function, when External ID calls `OnTokenIssuanceStart`, then tenant and roles are resolved, the response matches `provideClaimsForToken`, missing tenant fails closed, and processing completes within 2 seconds | Backed by control-plane Cosmos |
| As a developer, I want the Custom Authentication Extension registered and verified so enriched tokens are available before API work continues | Engineering | Given the registered extension, when a test sign-in completes, then `jwt.ms` shows `extension_tenantId`, `roles`, and `tenant_status` | Milestone gate |
| As a developer, I want the APIM `validate-jwt` policy implemented on frontend API routes so invalid and spoofed tokens are rejected at the edge | Engineering | Given the APIM policy, when requests arrive, then unsigned tokens, wrong audience, expired tokens, missing tenant claims, tenant mismatch, and spoofed headers are rejected or sanitized before reaching the frontend API | Use OIDC discovery for External ID |
| As a developer, I want the frontend API implemented as a BFF so SPA-specific orchestration is separated from tenant data access | Engineering | Given the frontend API, when the SPA calls portfolio, position, and approval routes, then the frontend API re-validates the user token, enforces route-to-claim tenant binding, forwards only trusted context to the backend API, and never accesses Cosmos directly | Frontend API owns UI-shaped responses and request aggregation |
| As a developer, I want the backend API implemented as the final authorization and data-access boundary | Engineering | Given the backend API, when a request arrives from the frontend API, then JWT or delegated trusted identity is validated, tenant binding is re-checked, scopes and roles are enforced, and only authorized tenant data operations run | Backend API is the final authority, not APIM or frontend API |
| As a developer, I want service-to-service authentication between frontend API and backend API so backend endpoints are not callable by anonymous internal traffic | Engineering | Given the deployed APIs, when the frontend API calls the backend API, then the call uses managed identity or another approved Entra-protected service credential; direct calls without valid service authentication are rejected | Recommended: managed identity between Container Apps |
| As a developer, I want `TenantDirectory` and `CosmosClientFactory` implemented in the backend API so the backend resolves the tenant Cosmos account from the validated token claim | Engineering | Given the services, when a request is processed, then tenant is resolved from the token only, Cosmos uses `DefaultAzureCredential`, clients are cached per endpoint, and the user JWT never reaches Cosmos | Never trust headers for tenant selection |
| As a developer, I want the three user-facing operations implemented end-to-end across frontend API and backend API so the demo scenario is buildable | Engineering | Given the API layers, when called with a valid token, then portfolio list, positions, and approval operations work for the matching tenant and return 403 for cross-tenant calls | Frontend API route shape may differ from backend API route shape |
| As a developer, I want APIM rate limiting keyed by `extension_tenantId` so one tenant cannot starve others | Engineering | Given the APIM policy, when one tenant exceeds the configured request rate, then that tenant receives 429 while other tenants continue unaffected | Set POC threshold to 100 requests/minute |

### Epic 7: Observability and Validation

**Goal:** Prove the POC works and fails visibly, with structured logs across every security layer.

| Story | Owner Type | Acceptance Criteria | Notes |
|---|---|---|---|
| As an engineer, I want structured logs from the Custom Claims Provider so token issuance is auditable | Engineering | Given Function logs, when a sign-in occurs, then Application Insights shows `userId`, `tenantId`, `correlationId`, and decision reason; JWT payloads are never logged | |
| As an engineer, I want structured logs from the frontend API and backend API so cross-component traces are possible | Engineering | Given Application Insights, when a request is processed, then trace entries from APIM, frontend API, backend API, and Cosmos include `tenantId`, `userId`, `correlationId`, and operation result | |
| As an engineer, I want APIM diagnostics sent to Log Analytics so policy rejections are visible | DevOps | Given APIM diagnostics, when JWT validation fails, then Log Analytics shows the rejection with correlation ID within 60 seconds | |
| As an engineer, I want an alert for tenant-mismatch 403 spikes so spoofing attempts are surfaced | DevOps | Given the alert rule, when more than 5 tenant-mismatch 403s occur in 5 minutes, then an alert fires to the configured action group | |
| As an engineer, I want a deployment validation script so the POC can be verified after every `azd up` | Engineering | Given the validation script, when run post-deploy, then it checks health endpoints, token claims, cross-tenant 403, and Cosmos private DNS resolution | |

### Epic 8: Deployment and Success Criteria Verification

**Goal:** Prove the fully deployed POC meets the agreed success criteria against the live Azure environment.

| Story | Owner Type | Acceptance Criteria | Notes |
|---|---|---|---|
| As a DevOps engineer, I want `azd up` run end-to-end against a clean subscription so repeatability is proven | DevOps | Given a clean subscription, when `azd up` runs once, then all resources are created, all services are deployed, and no manual steps are required | Capture CLI output or CI log |
| As a DevOps engineer, I want all test users to sign in and receive correct claims | Engineering | Given the deployed environment, when each test user signs in, then `jwt.ms` or the SPA shows the correct tenant and roles | |
| As a security engineer, I want cross-tenant isolation verified across the deployed frontend API and backend API | Security | Given the deployed APIs, when a tenant A user calls tenant B routes through APIM, then the response is 403; same-tenant routes return tenant-specific data only; backend API direct calls without valid frontend API/service authentication are rejected | Test all 3 by 3 tenant combinations |
| As a DevOps engineer, I want Cosmos connectivity verified from the deployed app host | DevOps | Given the deployed environment, when validation runs from the app host, then Cosmos DNS resolves to a private IP and public-route access fails | |
| As a DevOps engineer, I want a fourth tenant onboarded through automation so the lifecycle is proven | DevOps | Given `scripts/new-tenant.sh DeltaEquity`, when run, then a new Cosmos account, private endpoint, RBAC assignment, TenantDirectory record, and seeded data are created | Target: under 10 minutes |

### Epic 9: Documentation

**Goal:** Make the POC buildable by the next team and ready for production assessment.

| Story | Owner Type | Acceptance Criteria | Notes |
|---|---|---|---|
| As an architect, I want ADRs for the locked decisions so the team can revisit them with evidence | Architecture | Given `docs/adr/`, when reviewed, then ADRs exist for External ID, Custom Claims Provider, Cosmos account-per-tenant, shared APIM, and developer access | Lightweight ADR template |
| As an architect, I want the sequence diagram updated with Contoso resource names so it reflects the deployed system | Architecture | Given `docs/architecture/sequence.mmd`, when rendered, then it matches the target flow from `docs/architecture-design.md` | |
| As a DevOps engineer, I want an operational runbook covering deploy, rotate secrets, add tenant, remove tenant, and teardown | DevOps | Given `docs/runbook.md`, when reviewed, then each operation has step-by-step commands and avoids portal-only instructions where possible | |
| As a developer, I want a production-readiness gap document so the difference between POC and production is explicit | Product/Architecture | Given the document, when reviewed, then it covers production SKUs, geo-replication, Conditional Access, CI/CD, real onboarding, CMK, backup, and OWASP review | |
| As a developer, I want a lessons-learned log so the next team benefits from deployment and testing issues | Engineering | Given the log, when reviewed, then each non-trivial issue records root cause and fix | Update throughout POC |

### Epic 10: POC Demo Script and Guide

**Goal:** Make the POC demonstrable on demand by any presenter.

| Story | Owner Type | Acceptance Criteria | Notes |
|---|---|---|---|
| As a presenter, I want a step-by-step demo script for the primary scenario so I can run it without debugging during the presentation | Product | Given `docs/demo-script.md`, when followed, then it demonstrates sign-in, claim display, portfolio view, transaction approval, cross-tenant 403, and Log Analytics trace | Include expected screenshots |
| As a presenter, I want demo prerequisites documented so the demo can be reset and rerun | DevOps | Given the prerequisites section, when reviewed, then required accounts, `azd env` values, seed data reset steps, and browser state instructions are listed | |
| As a presenter, I want expected output for each demo step so a broken demo is obvious before stakeholders see it | Engineering | Given the expected-output section, when reviewed, then each step lists exact status codes, claim values, and example data shapes | |

### Epic 11: Pattern 2 Portfolio Agent Experience

**Goal:** Provide a secure end-to-end portfolio-agent chat experience where the SPA invokes the shared Foundry hosted agent through APIM and the Frontend API/BFF, and all tenant data access continues through the Backend API.

| Story | Owner Type | Acceptance Criteria | Notes |
|---|---|---|---|
| As a product owner, I want the Pattern 2 agent journey documented so stakeholders understand how agent chat preserves tenant isolation | Product/Architecture | Given the architecture docs, when reviewed, then the SPA -> APIM -> BFF -> Portfolio Agent -> Backend API -> Cosmos flow is documented with tenant and token boundaries | Documentation-first |
| As a user, I want to ask tenant-scoped portfolio questions in the SPA so I can see the agent experience in the demo | Product/Engineering | Given a signed-in user with `assets.read`, when they ask a portfolio question, then the SPA shows a tenant-scoped answer and correlation ID | Include a basic chat UI |
| As a security engineer, I want agent chat routed through APIM and the BFF so the SPA never calls Foundry directly | Security/Engineering | Given the SPA, when agent chat is invoked, then the only browser API call is `POST /api/tenants/{tenantId}/agent/chat` through APIM | Direct APIM-to-agent is an evaluated alternative, not the default |
| As an architect, I want BFF routing compared with direct APIM-to-agent routing so the team selects the safest public boundary | Architecture | Given the ADR, when reviewed, then it compares tenant authority, service auth, token handling, networking, observability, rate limits, and operational complexity | Gate before implementation |
| As a developer, I want the BFF agent-chat route to reuse existing tenant authorization so route tenant and token tenant must match | Engineering | Given a mismatched route tenant, when agent chat is called, then the request is rejected before the agent is invoked | Use `TenantAuthorization.AuthorizeRead` |
| As a developer, I want portfolio-agent tools to call Backend API instead of static cross-tenant demo data so the Backend API remains data authority | Engineering | Given an agent tool invocation, when tenant data is needed, then the tool calls Backend API or a BFF-hosted tool adapter and receives only same-tenant data | No direct Cosmos access |
| As a security engineer, I want agent tool calls to ignore user/model-provided tenant IDs so prompt injection cannot switch tenants | Security/Engineering | Given a prompt asking to switch tenants, when tools run, then the tool uses only validated tenant context and Backend API revalidates it | Negative test required |
| As a platform engineer, I want APIM policies for the agent route so edge validation and cost controls match other routes | DevOps | Given the APIM deployment, when the agent route is called, then JWT validation, tenant binding, `assets.read`, header sanitization, correlation, and rate limiting apply | Consider agent-specific quota |
| As a developer, I want OpenAPI contracts for agent chat so SPA, APIM, and BFF stay aligned | Engineering | Given the contracts, when reviewed, then request/response DTOs, route, auth, and error behavior match implementation | Update frontend API contract |
| As an operator, I want agent telemetry to include tenant ID, user ID, correlation ID, tool name, result, and latency without raw tokens | Operations | Given an agent chat, when traces are inspected, then end-to-end correlation is possible and tokens are not logged | Disable full content capture outside POC |
| As a QA/security tester, I want validation cases for agent isolation so the demo proves cross-tenant prompts fail safely | Security/QA | Given AlphaCapital credentials, when asking for BetaWealth data via agent chat, then the response refuses or returns not found and Backend/API logs show no cross-tenant data access | Add validation script coverage |
| As a developer, I want a parallel Python Microsoft Agent Framework hosted agent so the same portfolio security, evaluation, and monitoring design is demonstrated in Foundry without changing the SPA/BFF runtime target | Engineering | Given `portfolio-agent-python` is deployed, when invoked or evaluated through Foundry, then it uses APIM MCP tools, isolated Python-prefixed memory, shared canonical eval assets, and shared App Insights monitoring while the C# agent remains the BFF default | Foundry-only parallel implementation |

## Architecture Decisions

| Decision | Recommended Default | Status |
|---|---|---|
| Frontend API host | Azure Container Apps with VNet integration | Open |
| Backend API host | Azure Container Apps with VNet integration and private data access | Open |
| Custom Claims Provider host | Azure Functions with VNet integration | Open |
| Control-plane tenant mapping store | Cosmos DB SQL API, separate account | Open |
| Developer database access | Cloud-only baseline; optional Entra-authenticated Bastion jumpbox with read-only Cosmos RBAC | Accepted |
| Region | East US, or another region with confirmed service capacity | Open |
| SPA hosting | Azure Static Web Apps | Open |
| Pattern 2 portfolio-agent public boundary | APIM to Frontend API/BFF route; direct APIM-to-agent remains an evaluated alternative | Proposed |
| CI/CD | Manual `azd up` for POC; automated pipeline deferred | Closed for POC |

## Azure Guardrails

| Guardrail | Requirement |
|---|---|
| Infrastructure | All Azure resources authored in Bicep under `infra/` |
| Deployment | `azure.yaml` supports `azd provision`, `azd deploy`, and `azd up` |
| Cosmos local authentication | `disableLocalAuth: true` on all Cosmos accounts |
| Cosmos public access | `publicNetworkAccess: 'Disabled'` on all Cosmos accounts |
| Private connectivity | Private endpoint and private DNS for each Cosmos account |
| Service credentials | Managed identity for service-to-service access |
| API layering | SPA calls frontend API through APIM; backend API owns authorization-sensitive data access |
| Secrets | No secrets in source, Bicep parameters, or `azd` env files |
| Logging | Structured logs with `tenantId`, `userId`, and `correlationId`; never log JWTs |
| APIM validation | Use `validate-jwt` with External ID OIDC discovery |
| Token lifetime | Use short access-token lifetime, recommended 15-60 minutes |

## Definition of Done

- `azd up` against a clean subscription provisions and deploys all components, including SPA, frontend API, backend API, APIM, claims provider, and data services, with no manual steps.
- All 6 test users, 2 per tenant, sign in and receive tokens with correct `extension_tenantId` and `roles` claims.
- AlphaCapital, BetaWealth, and GammaFund data are physically isolated in separate Cosmos accounts.
- All 9 cross-tenant API call combinations return 403.
- Cosmos DNS resolves to a private IP from within the deployed app host.
- Public-route Cosmos access fails.
- `disableLocalAuth: true` is verified on all Cosmos accounts.
- APIM and both API layers reject unsigned, expired, wrong-audience, missing-claim, tenant-mismatch, and unauthorized service-to-service calls with correct 401 or 403 status codes.
- A fourth tenant, DeltaEquity, is onboarded with automation in under 10 minutes.
- Architecture diagrams, ADRs, operational runbook, and demo script are committed under `docs/`.
- Open architecture decisions are documented with recommended defaults.
- A presenter can run the full demo script without engineering support.
