# Contoso Asset Management Multi-Tenant Template

This public template contains the Contoso Asset Management multi-tenant SaaS proof of concept. It demonstrates Azure External ID authentication, token-time tenant claim enrichment, shared APIM enforcement, a frontend API/BFF, a backend API authorization boundary, and physically isolated tenant data in one Cosmos DB account per business tenant.

The target architecture and backlog live in:

- `docs/architecture-design.md`
- `docs/local-development.md`
- `docs/workforce-federation-setup.md`

All checked-in tenant IDs, client IDs, domains, and public endpoints are
synthetic examples. Replace them through the documented `azd` environment
variables and local configuration files before deployment. Never commit local
credentials, generated identity output, or Foundry evaluation results.

## Locked implementation choices

| Decision | Choice |
|---|---|
| API/runtime stack | C# / .NET 8 |
| Frontend API host | Azure Container Apps |
| Backend API host | Azure Container Apps |
| Custom Claims Provider host | Azure Functions |
| SPA host | Azure Static Web Apps |
| Infrastructure | Bicep deployed with Azure Developer CLI |
| Initial region | East US |
| Developer database access | Cloud-only for the POC |
| CI/CD | Deferred; manual `azd up` is the POC path |

## Expected repository layout

Implementation should follow this structure:

```text
azure.yaml
infra/
  main.bicep
  main.parameters.json
  modules/
src/
  spa/
  frontend-api/
  backend-api/
  custom-claims-provider/
scripts/
  seed-data.*
  validate-deployment.*
  new-tenant.*
docs/
  adr/
```

## Required `azd` environment values

These values are expected once `azure.yaml` and `infra/` are added.

| Key | Description | Secret |
|---|---|---|
| `AZURE_LOCATION` | Azure region for the POC. Default: `eastus`. | No |
| `AZURE_ENV_NAME` | Short environment name used for resource naming. | No |
| `EXTERNAL_ID_TENANT_ID` | Azure External ID tenant ID. | No |
| `EXTERNAL_ID_ISSUER` | Expected token issuer URL. | No |
| `EXTERNAL_ID_AUTHORITY` | MSAL authority for the SPA. | No |
| `API_AUDIENCE` | Exact Frontend API token audience (`aud`) accepted by APIM, the frontend API, and forwarded user-token validation in the backend API. For this External ID tenant, use the frontend API client ID. | No |
| `SPA_CLIENT_ID` | SPA app registration client ID. | No |
| `FRONTEND_API_CLIENT_ID` | Frontend API app registration/client ID. | No |
| `BACKEND_SERVICE_AUTHORITY` | Internal MngEnv Entra authority used for frontend-to-backend service tokens. | No |
| `BACKEND_SERVICE_ISSUER` | Internal MngEnv Entra issuer used for frontend-to-backend service tokens. | No |
| `BACKEND_API_AUDIENCE` | Internal Entra backend service audience/application ID URI. | No |
| `BACKEND_API_SERVICE_TOKEN_SCOPE` | Internal Entra backend service `/.default` scope requested by the frontend API managed identity. | No |
| Backend service app roles | Grant backend callers `Backend.Read` and/or `Backend.Write` on the internal Backend Service Auth enterprise app. Adding new callers should be an Entra app-role assignment, not an app deployment. | No |
| `CLAIMS_PROVIDER_APP_ID` | Custom Claims Provider app registration/client ID, if required by registration automation. | No |
| `CLAIMS_PROVIDER_AUDIENCE` | Custom Claims Provider app ID URI, if required by registration automation. | No |
| `CLAIMS_PROVIDER_CALLBACK_URL` | Deployed Custom Claims Provider Function URL used by `scripts/register-claims-extension.sh`. Do not commit URLs that include function keys. | Yes if it contains a key |
| `APIM_PUBLISHER_EMAIL` | Required APIM publisher email. | No |
| `APIM_PUBLISHER_NAME` | Required APIM publisher name. | No |
| `ALERT_ACTION_GROUP_EMAIL` | Email target for the tenant-mismatch alert action group. | No |
| `VALIDATION_ACCESS_TOKEN` | Short-lived user access token used only by `scripts/validate-deployment.sh --live` to prove tenant claims and API isolation. Never echo, store, or commit it. | Yes |

## Optional Foundry hosted-agent configuration

The deployment now provisions an AI Foundry account, Foundry project, model deployment, App Insights connection, and ACR connection for `src/portfolio-agent`. Defaults target a hosted-agent-supported Foundry region and a small chat model deployment:

| Key | Default | Description |
|---|---:|---|
| `FOUNDRY_LOCATION` | `eastus2` | Region for Foundry account/project/model resources. Use a hosted-agent-supported region with available model quota. |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | `gpt-4.1-mini` | Deployment name injected into the hosted agent. |
| `FOUNDRY_MODEL_NAME` | `gpt-4.1-mini` | Model catalog name for the deployment. |
| `FOUNDRY_MODEL_VERSION` | `2025-04-14` | Model version for the deployment. |
| `FOUNDRY_MODEL_SKU` | `GlobalStandard` | Model deployment SKU; change if your region requires `Standard` or `DataZoneStandard`. |
| `FOUNDRY_MODEL_CAPACITY` | `10` | Model deployment capacity. |

`azd provision` outputs `FOUNDRY_PROJECT_ENDPOINT`, `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_AI_PROJECT_ID`, `AZURE_AI_MODEL_DEPLOYMENT_NAME`, `APPLICATIONINSIGHTS_CONNECTION_STRING`, and `AZURE_AI_PROJECT_ACR_CONNECTION_NAME` for the hosted-agent extension. To grant your signed-in user project access through IaC, set `AZURE_PRINCIPAL_ID` and optionally `AZURE_PRINCIPAL_TYPE` before provisioning.

Do not store secrets, client secrets, connection strings, Cosmos keys, JWTs, or refresh tokens in source control, Bicep parameters, or checked-in `.azure/` files.

## SPA runtime configuration

The Static Web App in `src/spa/` reads `/config.json` at startup. The checked-in config uses the current non-secret External ID tenant and app registration values from `docs/external-id-app-registrations.local.json`:

- `auth.authority`: `https://contosoexternalid.ciamlogin.com/11111111-1111-4111-8111-111111111111`.
- `auth.clientId`: `22222222-2222-4222-8222-222222222222`.
- `auth.identityRouting.providers`: exact-domain routes for enabled, disabled, and future workforce providers. The current active route uses `contosoworkforce.onmicrosoft.com` as both the email domain and `domainHint`.
- `api.apimBaseUrl`: APIM gateway URL, for example the `apimGatewayUrl` deployment output.
- `api.scopes`: `https://contosoexternalid.onmicrosoft.com/contoso-asset-management/frontend-api/assets.read` and `https://contosoexternalid.onmicrosoft.com/contoso-asset-management/frontend-api/assets.write`.

Keep the APIM gateway placeholder until deployment returns a real gateway URL. For local debugging, copy `src/spa/public/config.local.sample.json` to `src/spa/public/config.json`; it points `api.apimBaseUrl` at the local frontend API on `http://localhost:7070`.

The SPA derives tenant authority only from the decoded `extension_tenantId` access-token claim. The cross-tenant demo intentionally calls a mismatched route tenant to show a 403 response; it does not send tenant IDs in headers, query strings, or request bodies.

Approved users from the configured workforce tenant can authenticate through External ID while continuing to receive External ID-issued application tokens. The SPA asks for email first and uses an exact domain match only to accelerate the configured provider; unknown domains use normal External ID local sign-in and users can open the provider picker. Follow `docs/workforce-federation-setup.md`; do not point the SPA or APIs at the workforce issuer or treat email routing as authorization.

Portfolio-agent chat uses the frontend API/BFF hosted-session flow. `POST /api/tenants/{tenantId}/agent/chat` returns `conversationId` as an opaque BFF-issued session handle; it is not a Foundry `agent_session_id` and not conversation memory. The SPA can echo the handle on later chat calls or call `DELETE /api/tenants/{tenantId}/agent/sessions/{sessionHandle}` to clean up an owned hosted session.

## Deployment workflow

Once implementation assets exist, use the checked-in ecosystem commands:

```bash
azd env new <environment-name>
azd env set AZURE_LOCATION eastus
python3 scripts/set-azd-auth-env.py
azd up
```

`scripts/set-azd-auth-env.py` reads the ignored
`docs/external-id-app-registrations.local.json` and optional
`docs/internal-entra-service-auth.local.json` artifacts and sets only
non-secret `azd env` values. The SPA and user-token validation use External ID
frontend API scopes. Frontend-to-backend service authentication uses the
internal MngEnv Entra tenant.

```bash
azd env set EXTERNAL_ID_TENANT_ID 11111111-1111-4111-8111-111111111111
azd env set EXTERNAL_ID_ISSUER https://11111111-1111-4111-8111-111111111111.ciamlogin.com/11111111-1111-4111-8111-111111111111/v2.0
azd env set EXTERNAL_ID_AUTHORITY https://contosoexternalid.ciamlogin.com/11111111-1111-4111-8111-111111111111
azd env set API_AUDIENCE 33333333-3333-4333-8333-333333333333
azd env set SPA_CLIENT_ID 22222222-2222-4222-8222-222222222222
azd env set FRONTEND_API_CLIENT_ID 33333333-3333-4333-8333-333333333333
azd env set BACKEND_SERVICE_AUTHORITY https://login.microsoftonline.com/55555555-5555-4555-8555-555555555555
azd env set BACKEND_SERVICE_ISSUER https://sts.windows.net/55555555-5555-4555-8555-555555555555/
azd env set BACKEND_API_AUDIENCE <internal-entra-backend-api-audience>
azd env set BACKEND_API_SERVICE_TOKEN_SCOPE <internal-entra-backend-api-audience>/.default
```

Bicep creates the frontend API and APIM MCP gateway managed identities. Grant
those managed identity service principals the internal backend API app roles
(`Backend.Read` and/or `Backend.Write`) with
`scripts/create-internal-backend-service-auth.py`; adding future backend callers
should be an Entra app-role assignment, not a backend deployment.

## Validate deployment and observability

Run the validator in dry-run mode first:

```bash
scripts/validate-deployment.sh
```

Live validation is read-only and requires explicit configuration:

```bash
export VALIDATION_ACCESS_TOKEN='<short-lived AlphaCapital API access token>'
scripts/validate-deployment.sh --live \
  --environment-name "$AZURE_ENV_NAME" \
  --tenant AlphaCapital \
  --cross-tenant BetaWealth
```

To generate a short-lived token through the registered SPA public client and
immediately validate the BFF-to-backend route:

```bash
python3 scripts/get-external-id-access-token.py --validate-bff
```

The helper opens an External ID browser sign-in, captures the localhost
authorization-code redirect with PKCE, and never stores the token. Use
`--print-token` only when you need to pass the token to another validation tool.

The script checks service health, required token claims, same-tenant success,
cross-tenant 403 behavior, malformed-token rejection, direct backend rejection,
Cosmos private DNS from the app host, public Cosmos access failure, Cosmos
`disableLocalAuth: true`, and the tenant-mismatch alert. It never prints or
stores tokens.

Before provisioning, validate Bicep with:

```bash
az bicep build --file infra/main.bicep
az deployment sub what-if --location eastus --template-file infra/main.bicep
```

## Security guardrails

- `extension_tenantId` from a validated access token is the only trusted business-tenant selector.
- Tenant switching requires acquiring a new token; do not implement `X-Tenant-Id` switching.
- APIM, the frontend API, and the backend API must independently validate tenant binding.
- The backend API is the only application component allowed to access tenant Cosmos accounts.
- Cosmos access uses managed identity and Entra RBAC; user JWTs must never be sent to Cosmos DB.
- Every Cosmos account must use `disableLocalAuth: true`, `publicNetworkAccess: 'Disabled'`, private endpoints, and private DNS.
- Do not log JWTs, access tokens, refresh tokens, secrets, or full sensitive claim payloads.
