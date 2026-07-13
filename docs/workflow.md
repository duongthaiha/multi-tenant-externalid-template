# End-to-End Workflow

```mermaid
sequenceDiagram
    actor User
    participant SPA
    participant ExternalID as Azure External ID
    participant WorkforceID as Federated Workforce Entra
    participant CCP as Custom Claims Provider
    participant APIM
    participant BFF as Frontend API / BFF
    participant Backend as Backend API
    participant Directory as TenantDirectory
    participant Cosmos as Tenant Cosmos DB

    User->>SPA: Open app, select sign in, enter email
    SPA->>SPA: Match exact domain to enabled, disabled, or local route
    SPA->>ExternalID: Request API token with login_hint and optional domain_hint
    opt Pre-authorized workforce user
        ExternalID->>WorkforceID: OIDC authorization code flow
        WorkforceID-->>ExternalID: Home-tenant authentication result
    end
    ExternalID->>CCP: OnTokenIssuanceStart
    CCP->>Directory: Resolve active tenant, roles, and status
    Directory-->>CCP: Tenant context
    CCP-->>ExternalID: provideClaimsForToken
    ExternalID-->>SPA: JWT with tenant claims
    SPA->>APIM: Call frontend API route with bearer token
    APIM->>APIM: Validate JWT and route tenant binding
    APIM->>BFF: Forward sanitized request
    BFF->>BFF: Re-validate token and route tenant binding
    BFF->>Backend: Call internal API with user token and service authentication
    Backend->>Backend: Validate user token, service identity, scopes, roles, and tenant
    Backend->>Directory: Resolve Cosmos routing for validated token tenant
    Directory-->>Backend: Tenant data endpoint metadata
    Backend->>Cosmos: Query or mutate data with managed identity
    Cosmos-->>Backend: Tenant data
    Backend-->>BFF: Domain response
    BFF-->>SPA: UI-shaped response
```

Tenant switching requires a new token acquisition flow. The SPA, APIM, frontend API, and backend API must not use `X-Tenant-Id`, query string values, request body values, or other client-controlled fields as tenant authority.

For workforce-federated users, the workforce tenant authenticates the user but External ID remains the application token issuer. Email discovery only selects the authentication journey; it does not authorize access or select a business tenant. Disabled configured domains are blocked before MSAL opens, unknown domains continue to External ID local sign-in, and **Choose another sign-in method** opens the normal provider picker. See `docs/workforce-federation-setup.md`.
