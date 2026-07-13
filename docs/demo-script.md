# Demo Script

## Goal

Demonstrate that Contoso Asset Management securely serves multiple business tenants from a shared application platform while keeping tenant data physically isolated.

## Prerequisites

- `azd up` has completed successfully.
- Seed data has been loaded.
- AlphaCapital, BetaWealth, and GammaFund each have at least two test users.
- Optional lifecycle proof: run `scripts/new-tenant.sh` to onboard DeltaEquity
  before the demo.
- Browser session state is clean or uses separate profiles for test users.
- Validation script passes before the demo.
- `src/spa/public/config.json` has been populated with the External ID authority,
  SPA client ID, APIM gateway URL, and API scopes (`assets.read` and
  `assets.write`).
- The External ID user flow is attached to the Custom Authentication Extension,
  and an AlphaCapital API access token can be acquired without logging or storing
  the token.

## Final validation sequence

Run local validation first:

```bash
az bicep build --file infra/main.bicep
az bicep build --file infra/tenant-onboarding.bicep
scripts/validate-deployment.sh
(cd src/spa && npm run build)
dotnet build src/shared/Contoso.AssetManagement.Shared.csproj
dotnet build src/backend-api/Contoso.AssetManagement.BackendApi.csproj
dotnet build src/frontend-api/Contoso.AssetManagement.FrontendApi.csproj
dotnet build src/custom-claims-provider/Contoso.AssetManagement.CustomClaimsProvider.csproj
```

Then run live validation only after APIM, frontend API, backend API, Custom
Claims Provider, Cosmos, and the tenant-mismatch alert are deployed:

```bash
export VALIDATION_ACCESS_TOKEN='<short-lived AlphaCapital API access token>'
scripts/validate-deployment.sh --live \
  --environment-name "$AZURE_ENV_NAME" \
  --resource-group "<deployed-resource-group>" \
  --apim-url "https://<apim-name>.azure-api.net" \
  --tenant AlphaCapital \
  --cross-tenant BetaWealth
```

The token must contain `extension_tenantId=AlphaCapital`, `tenant_status=active`,
`roles`, and `assets.read`/`assets.write` scopes. Do not fake live success if
the Function endpoint, claims extension registration, APIM URL, or token is not
available.

## Primary scenario

1. Open the SPA.
2. Sign in as an AlphaCapital user.
3. Show decoded claims:
   - `extension_tenantId`: `AlphaCapital`
   - `tenant_status`: `active`
   - `roles`: one or more assigned tenant roles
4. Open the portfolio list.
5. Open a position detail from an AlphaCapital portfolio.
6. Approve a pending transaction as a user with `TenantAdmin` or `PortfolioManager`.
7. Attempt a BetaWealth route with the AlphaCapital token.
8. Show the 403 tenant-mismatch response in the SPA's "Last API result" panel,
   including the correlation ID and authorization decision header when returned.
9. Open Log Analytics or Application Insights.
10. Trace the same correlation ID through APIM, frontend API, backend API, and Cosmos dependency telemetry.
11. Show the tenant-mismatch alert rule configured for more than five tenant-mismatch 403 responses in five minutes.

## Expected outputs

| Step | Expected output |
|---|---|
| Sign-in | Access token contains `extension_tenantId`, `roles`, and `tenant_status`. |
| Portfolio list | HTTP 200 and only same-tenant portfolios. |
| Position detail | HTTP 200 and a position that belongs to the selected same-tenant portfolio. |
| Approval | HTTP 200 or 204 and transaction status changes to approved. |
| Cross-tenant route | HTTP 403 with a tenant-mismatch decision. |
| Unauthorized backend direct call | HTTP 401 or 403. |
| Logs | Structured entries include `tenantId`, `userId`, `correlationId`, operation, result, and status code. |
| Alerting | Scheduled query rule `alert-<env>-tenant-mismatch-403` targets the configured action group at >5 tenant-mismatch 403s in 5 minutes. |

## Tenant onboarding proof

Run:

```bash
scripts/new-tenant.sh --dry-run
scripts/new-tenant.sh
```

Show that DeltaEquity receives its own locked-down Cosmos account, private
endpoint/DNS integration, backend managed-identity RBAC, TenantDirectory and
entitlement records, seeded portfolios, positions, and approvals.

## Reset guidance

Run the seed script again after implementation if it supports idempotent reset:

```bash
scripts/seed-data.sh --reset-demo
```

If reset is not implemented yet, redeploy or reseed the affected tenant data account before presenting.
