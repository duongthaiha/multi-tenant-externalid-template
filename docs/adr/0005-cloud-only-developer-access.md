# ADR 0005: Use cloud-only developer database access for the POC

## Status

Superseded by ADR 0008 for environments that enable operator Data Explorer access

## Context

Tenant and control-plane Cosmos accounts must be private from day one. Local developer access options such as VPN or jumpboxes add operational complexity and can weaken the demonstration if they become required for validation.

## Decision

Use cloud-only database access for the POC. Development and validation scripts should run from deployed app hosts or other approved Azure-hosted execution contexts with private network access.

## Consequences

- No VPN gateway, jumpbox, or Bastion dependency is required for the POC baseline.
- Validation must include checks that Cosmos resolves privately and public-route access fails.
- Local development should use mocks, emulators, or app-layer tests where direct private Cosmos access is unavailable.
- Any future production developer-access model requires a separate decision.
