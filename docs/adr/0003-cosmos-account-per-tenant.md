# ADR 0003: Use one Cosmos DB account per business tenant

## Status

Accepted

## Context

The POC must visibly prove physical tenant data isolation for AlphaCapital, BetaWealth, and GammaFund, and must onboard DeltaEquity through automation.

## Decision

Provision a separate Cosmos DB SQL API account for each business tenant and a separate control-plane Cosmos DB account for tenant directory and entitlement metadata.

## Consequences

- Tenant data has a strong physical isolation boundary.
- The backend API must resolve tenant routing server-side from the TenantDirectory using the validated `extension_tenantId` claim.
- Provisioning and onboarding automation must create Cosmos accounts, private endpoints, DNS links, RBAC, databases, containers, directory records, entitlements, and seed data.
- Every Cosmos account must disable local authentication and public network access.
