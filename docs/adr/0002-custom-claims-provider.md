# ADR 0002: Use a Custom Claims Provider for tenant claims

## Status

Accepted

## Context

The SPA and APIs need a reliable business-tenant binding at token issuance time. Tenant and role data lives in the control-plane store and must be resolved before an API access token is accepted.

## Decision

Implement an Azure Functions Custom Claims Provider for the `OnTokenIssuanceStart` event. The function resolves tenant status and tenant-scoped roles from the control-plane Cosmos DB account and returns `extension_tenantId`, `roles`, and `tenant_status`.

## Consequences

- Missing tenant, inactive tenant, unresolved roles, multiple active tenants without explicit selection, dependency errors, and timeout must fail closed.
- Token issuance depends on control-plane availability and latency.
- The function must use managed identity and private connectivity to the control-plane store.
- Logs must record decisions and correlation IDs without logging JWTs or sensitive claim payloads.
