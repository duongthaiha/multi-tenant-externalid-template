# ADR 0001: Use Azure External ID for customer identity

## Status

Accepted

## Context

The POC needs to authenticate fictional customer users while keeping the identity tenant separate from the SaaS business tenant. The application must receive access tokens that can be enriched with business-tenant authorization context.

## Decision

Use Azure External ID as the customer identity plane and sole issuer of user access tokens accepted by the application. Business tenancy is represented by application claims, especially `extension_tenantId`, and not by the identity tenant itself.

Approved users from Microsoft Entra workforce tenant `66666666-6666-4666-8666-666666666666` authenticate through External ID's dedicated Microsoft Entra OIDC federation pattern. This is customer federation, not classic B2B guest collaboration. Admission requires workforce enterprise-app assignment, a federated External ID customer identity, and a pre-provisioned application entitlement.

## Consequences

- Customer authentication is delegated to Azure External ID.
- API authorization uses validated token claims plus server-side tenant metadata.
- The implementation must not treat the External ID tenant as the business tenant.
- APIs continue to reject workforce-issued user tokens; the upstream tenant authenticates but does not issue application tokens.
- Source tenant ID plus source object ID is the durable upstream identity key. Email and email domain are not authorization boundaries.
- Workforce-user deprovisioning must remove both enterprise-app assignment and application entitlement.
- Local External ID accounts and the workforce provider can coexist in the same user flow.
- Test user setup and Custom Authentication Extension registration remain required deployment prerequisites.
