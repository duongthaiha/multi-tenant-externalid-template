# ADR 0004: Use shared APIM as the public API front door

## Status

Accepted

## Context

The POC needs a common gateway layer that can validate tokens, enforce route-to-tenant binding, sanitize spoofable headers, apply tenant-level rate limits, and emit gateway diagnostics.

## Decision

Use a shared Azure API Management instance as the public front door for SPA-to-frontend API calls.

## Consequences

- APIM validates External ID JWTs with OIDC discovery and required claims.
- APIM enforces route tenant equals token tenant before forwarding.
- Spoofable identity headers must be removed or overwritten.
- Per-tenant rate limiting is keyed by `extension_tenantId`.
- APIM is not the only authorization layer; frontend API and backend API must independently re-check tenant binding.
