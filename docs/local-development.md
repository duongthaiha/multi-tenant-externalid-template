# Local development

This guide runs the Contoso Asset Management POC locally without storing secrets, tokens, client secrets, or connection strings in source control.

## Prerequisites

- .NET SDK 8.
- Node.js and npm for the Vite SPA.
- Azure Functions Core Tools v4 (`func`) only when running the Custom Claims Provider locally.
- Azure CLI login or another `DefaultAzureCredential` source if you call Cosmos-backed endpoints.
- VS Code extensions recommended by `.vscode/extensions.json`.

## Safe local configuration files

Use checked-in samples as placeholders only:

| Component | Sample | Local file to create |
|---|---|---|
| Backend API | `src/backend-api/appsettings.Development.sample.json` | `src/backend-api/appsettings.Development.json` |
| Frontend API | `src/frontend-api/appsettings.Development.sample.json` | `src/frontend-api/appsettings.Development.json` |
| Custom Claims Provider | `src/custom-claims-provider/local.settings.sample.json` | `src/custom-claims-provider/local.settings.json` |
| SPA | `src/spa/public/config.local.sample.json` | `src/spa/public/config.json` for local runs |

The current External ID setup has these non-secret values:

- External ID tenant ID: `11111111-1111-4111-8111-111111111111`.
- External ID issuer: `https://11111111-1111-4111-8111-111111111111.ciamlogin.com/11111111-1111-4111-8111-111111111111/v2.0`.
- External ID MSAL authority: `https://contosoexternalid.ciamlogin.com/11111111-1111-4111-8111-111111111111`.
- SPA client ID: `22222222-2222-4222-8222-222222222222`.
- Frontend API client ID: `33333333-3333-4333-8333-333333333333`.
- Frontend API scopes: `https://contosoexternalid.onmicrosoft.com/contoso-asset-management/frontend-api/assets.read` and `https://contosoexternalid.onmicrosoft.com/contoso-asset-management/frontend-api/assets.write`.
- Backend service authentication is not in External ID. It uses the internal MngEnv Entra tenant with `BACKEND_SERVICE_AUTHORITY`, `BACKEND_SERVICE_ISSUER`, `BACKEND_API_AUDIENCE`, and `BACKEND_API_SERVICE_TOKEN_SCOPE`. Backend callers are authorized with Backend Service Auth app roles (`Backend.Read` and `Backend.Write`) assigned to their managed identity service principals.

Workforce federation does not change these SPA authority, client, or scope values. The browser still starts at the External ID authority and returns an External ID-issued access token after upstream workforce authentication. The checked-in SPA configs also define `auth.identityRouting.providers`: `contosoworkforce.onmicrosoft.com` is enabled with the same `domainHint`, while `partnerworkforce.onmicrosoft.com` remains disabled. Use `docs/workforce-federation-setup.md` for provider setup, routing lifecycle, and approved-user provisioning.

Refresh `docs/external-id-app-registrations.local.json` with
`scripts/create-external-id-app-registrations.py` when registrations change,
create/update `docs/internal-entra-service-auth.local.json` with
`scripts/create-internal-backend-service-auth.py`, then run
`python3 scripts/set-azd-auth-env.py --dry-run` to inspect matching non-secret
`azd env set` commands or omit `--dry-run` to apply them.

After the Custom Claims Provider Function is deployed, register the External ID
token issuance hook with:

```bash
scripts/register-claims-extension.sh --dry-run \
  --callback-url "https://<function-app>.azurewebsites.net/api/OnTokenIssuanceStart"
scripts/register-claims-extension.sh \
  --callback-url "https://<function-app>.azurewebsites.net/api/OnTokenIssuanceStart"
```

The script models the `OnTokenIssuanceStart` event and expects the Function to
return a `provideClaimsForToken` action containing `extension_tenantId`, `roles`,
and `tenant_status`. It requires Graph delegated permissions
`CustomAuthenticationExtension.ReadWrite.All`, `EventListener.ReadWrite.All`,
`Policy.ReadWrite.ApplicationConfiguration`, and `Application.ReadWrite.All`.

Other local samples still use placeholders until Azure resources exist:

- `<control-plane-account>`: control-plane Cosmos DB account name.

Do not commit `local.settings.json`, access tokens, refresh tokens, client secrets, Cosmos keys, or connection strings.

For local SPA sign-in, use the ignored local login sheet generated from the demo
user outputs:

```bash
docs/demo-login.local.md
```

That file contains the demo usernames and temporary passwords and must stay
local-only.

## Local ports

| Service | URL |
|---|---|
| Backend API | `http://localhost:8080` |
| Frontend API/BFF | `http://localhost:7070` |
| SPA dev server | `http://127.0.0.1:5173` |
| Custom Claims Provider | `http://localhost:7072` |

The frontend API defaults `BackendApi:BaseAddress` to the local backend. The SPA local sample points `api.apimBaseUrl` at the local frontend API (`http://localhost:7070`; use `http://127.0.0.1:7070` if that is how you bind it) so it can bypass APIM for debugging while still using External ID access tokens and token tenant claims.

The SPA app registration output confirms local redirect URIs are registered for both `http://localhost:5173` and `http://127.0.0.1:5173`. Keep `src/spa/public/config.local.sample.json` on one of those origins when using `npm run dev -- --host 127.0.0.1 --port 5173`.

For local identity discovery, select **Sign in** and test these routes:

- an exact, case-insensitive `@contosoworkforce.onmicrosoft.com` address accelerates to the workforce provider;
- `@partnerworkforce.onmicrosoft.com` displays the disabled-provider message without opening MSAL;
- another domain continues to External ID with the email prefilled;
- **Choose another sign-in method** opens the normal provider picker.

Never add provider client secrets to SPA configuration. External ID owns upstream provider credentials.

## Command order

From the repository root:

```bash
dotnet build src/shared/Contoso.AssetManagement.Shared.csproj && \
dotnet build src/backend-api/Contoso.AssetManagement.BackendApi.csproj && \
dotnet build src/frontend-api/Contoso.AssetManagement.FrontendApi.csproj && \
dotnet build src/custom-claims-provider/Contoso.AssetManagement.CustomClaimsProvider.csproj

cd src/spa && npm install && npm run build
```

Then run services in this order:

```bash
dotnet run --project src/backend-api/Contoso.AssetManagement.BackendApi.csproj --urls http://localhost:8080
dotnet run --project src/frontend-api/Contoso.AssetManagement.FrontendApi.csproj --urls http://localhost:7070
cd src/spa && npm run dev -- --host 127.0.0.1 --port 5173
```

If Azure Functions Core Tools v4 is installed:

```bash
cd src/custom-claims-provider
func host start --port 7072
```

## VS Code tasks and debugging

`.vscode/tasks.json` includes tasks to build all .NET projects, run each local service, build/run the SPA, run the Custom Claims Provider when `func` is available, and validate contracts/scripts with the local validation script.

`.vscode/launch.json` includes debug configurations for the backend API, frontend API, SPA in Chrome, and an attach flow for the Custom Claims Provider. Start the backend before frontend API calls that proxy to backend endpoints.
