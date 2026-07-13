# Operational Runbook

## Purpose

This runbook describes the expected operational commands for deploying, validating, onboarding, and tearing down the Contoso Asset Management multi-tenant POC after implementation assets are added.

## Prerequisites

- Azure CLI authenticated to the target subscription.
- Azure Developer CLI installed.
- Bicep support available through Azure CLI.
- Required app registrations and Azure External ID configuration prepared.
- Required `azd env` values set as documented in `README.md`.

## External ID app registrations

Use the idempotent helper to inspect the current Azure CLI account, reuse existing
app registrations by display name, and create missing SPA, frontend API, and
Custom Claims Provider registrations in the External ID tenant:

```bash
scripts/create-external-id-app-registrations.py \
  --tenant-id 11111111-1111-4111-8111-111111111111 \
  --tenant-name contosoexternalid \
  --output docs/external-id-app-registrations.local.json
```

The script creates no client secrets. It exposes delegated frontend API scopes
`assets.read` and `assets.write`, configures the claims-provider app-role
audience, and writes non-secret client IDs, app ID URIs, audiences, scopes, and
redirect URIs to the ignored local output file. It intentionally does not create
a backend API registration in External ID; backend service authentication belongs
to the internal MngEnv Entra tenant. The checked-in
`docs/external-id-app-registrations.sample.json` shows the expected safe shape.

The SPA redirect URIs include local development at `http://localhost:5173` and
`http://127.0.0.1:5173`. Do not add a deployed Static Web Apps redirect URI until
the app has a real hostname; track it as `https://<static-web-app-default-hostname>`
until then.

If `az account get-access-token --tenant <external-id-tenant-id>
--resource-type ms-graph` fails because the signed-in Azure CLI account is not a
member or admin of the External ID tenant, invite/grant the operator access or run
`az login --tenant <external-id-tenant-id> --allow-no-subscriptions` with an
authorized account. Do not run `az logout` because app/APIM/Cosmos work stays in
the MngEnv subscription.

Current setup note: `docs/external-id-app-registrations.local.json` contains the
created External ID app registration IDs, frontend API scopes, and local SPA
redirect URIs for this POC environment. The artifact is ignored because it is
environment-specific, but it must not contain secrets or tokens.

## Internal Entra backend service authentication

Create or inspect the internal MngEnv Entra backend service-auth app registration
with:

```bash
scripts/create-internal-backend-service-auth.py --dry-run

scripts/create-internal-backend-service-auth.py \
  --tenant-id 55555555-5555-4555-8555-555555555555 \
  --output docs/internal-entra-service-auth.local.json
```

This script creates no client secrets. It creates the backend service audience
and `Backend.Read` / `Backend.Write` app roles in the internal MngEnv tenant,
not in External ID. Bicep creates the frontend API and APIM MCP gateway managed
identities; grant those service principals the appropriate backend app roles
with the script's `--assign-read-client-id` and `--assign-write-client-id`
arguments. Do not use the External ID frontend API app registration client ID as
service identity. The checked-in `docs/internal-entra-service-auth.sample.json`
shows the expected safe shape.

After both local artifacts exist, set non-secret `azd` values:

```bash
python3 scripts/set-azd-auth-env.py --dry-run
python3 scripts/set-azd-auth-env.py
```

## External ID demo users and sign-in flow

For approved users who authenticate with workforce tenant
`66666666-6666-4666-8666-666666666666`, follow the source-of-truth procedure in
`docs/workforce-federation-setup.md`. It covers the workforce app registration,
enterprise-app assignment, External ID provider and user-flow setup, federated
customer-user provisioning, entitlement seeding, secret rotation, validation,
deprovisioning, and email-first SPA routing. Keep the local email/password
provider enabled for the demo users below. The active workforce provider issuer
must be `https://login.microsoftonline.com/contosoworkforce.onmicrosoft.com/v2.0`
so the SPA's `domain_hint` can accelerate sign-in.

Create or inspect the six fictional local demo users in the External ID tenant:

```bash
scripts/create-external-id-demo-users.sh \
  --tenant-id 11111111-1111-4111-8111-111111111111 \
  --tenant-domain contosoexternalid.onmicrosoft.com \
  --output docs/external-id-demo-users.local.json \
  --password-output docs/external-id-demo-user-passwords.local.json
```

The safe mapping output contains display names, example.com sign-in emails,
fixture user IDs, and External ID object IDs. It contains no passwords and is
ignored because it is environment-specific. The optional password output is also
ignored; delete it after the demo users are configured.

Seed control-plane entitlements with those real object IDs:

```bash
scripts/seed-data.sh --user-id-map docs/external-id-demo-users.local.json
```

This replaces fixture IDs such as `alpha-admin-001` with the External ID user
object ID in membership and role-assignment documents, which matches the
`OnTokenIssuanceStart` user ID emitted by External ID. The seed script still
keeps the original fixture IDs in `fixtureUserId` fields for traceability.

Current Graph capability check from this environment:

- Microsoft Graph token acquisition for tenant `11111111-1111-4111-8111-111111111111` succeeds.
- `User.ReadWrite.All` and `Directory.AccessAsUser.All` delegated scopes are present, so local user creation through `POST /users` is scriptable.
- User-flow automation is blocked in the current CLI token: `v1.0 /identity/b2cUserFlows` is unavailable for this tenant shape, and `beta /identity/b2cUserFlows` requires `IdentityUserFlow.Read.All` or `IdentityUserFlow.ReadWrite.All`.

Manual blocked subpart: create or verify the sign-in user flow in the Azure
portal until the operator can grant the required Graph user-flow permission.
Use **Microsoft Entra admin center** > the External ID tenant
`contosoexternalid.onmicrosoft.com` > **External Identities** > **User flows**:

1. Create a sign-up/sign-in user flow for local email accounts and the configured workforce provider.
2. Enable **Sign in with Contoso Workforce** and email/password local account sign-in; collect/display only Email Address and Display Name.
   For the workforce provider, map **Sub** to `oid` so pre-provisioned
   federated identities match the immutable workforce object ID.
3. Add the SPA app registration to the flow.
   The registration automation also creates its enterprise application/service
   principal; without that application instance, the SPA does not appear in the
   user flow's **Add application** list.
4. Attach the Custom Authentication Extension for token issuance after the Function endpoint is deployed.
5. Configure the claims mapping so `extension_tenantId`, `tenant_roles`, and `tenant_status` returned by the Custom Claims Provider are included in API access tokens.
6. Test with the six demo users and confirm the API token in `jwt.ms` contains the tenant and role claims.
7. Test SPA email discovery: enabled workforce domain, disabled `partnerworkforce.onmicrosoft.com`, unknown/local domain, malformed email, and provider-picker fallback.

## External ID Custom Claims Provider registration

The Custom Claims Provider Function implements the External ID
`OnTokenIssuanceStart` contract at `/api/OnTokenIssuanceStart`. Successful
responses use the `microsoft.graph.tokenIssuanceStart.provideClaimsForToken`
action and return exactly these API response claim IDs:

- `extension_tenantId`
- `tenant_roles`
- `tenant_status`

Because custom claims/mapped claims require an application-specific token
signing key, configure the Frontend API service principal before validating SPA
sign-in:

```bash
tenant_id='11111111-1111-4111-8111-111111111111'
frontend_api_app_id='33333333-3333-4333-8333-333333333333'
frontend_api_sp_id="$(az rest \
  --method GET \
  --url "https://graph.microsoft.com/v1.0/servicePrincipals(appId='${frontend_api_app_id}')?\$select=id" \
  --query id -o tsv)"

end_date="$(date -u -d '+730 days' '+%Y-%m-%dT%H:%M:%SZ')"
az rest \
  --method POST \
  --url "https://graph.microsoft.com/v1.0/servicePrincipals/${frontend_api_sp_id}/addTokenSigningCertificate" \
  --body "{\"displayName\":\"CN=Contoso Frontend API token signing\",\"endDateTime\":\"${end_date}\"}" \
  --headers Content-Type=application/json

thumbprint='<thumbprint returned by addTokenSigningCertificate>'
az rest \
  --method PATCH \
  --url "https://graph.microsoft.com/v1.0/servicePrincipals/${frontend_api_sp_id}" \
  --body "{\"preferredTokenSigningKeyThumbprint\":\"${thumbprint}\"}" \
  --headers Content-Type=application/json
```

After the signing key is active, all API validators must use app-specific OIDC
metadata so they trust the app-specific signing key:

```text
https://contosoexternalid.ciamlogin.com/11111111-1111-4111-8111-111111111111/v2.0/.well-known/openid-configuration?appid=33333333-3333-4333-8333-333333333333
```

Current environment status:

- No `azd` environment is selected and no deployed Function App matching this
  POC was found in the active subscription, so live registration cannot be
  completed yet.
- Microsoft Graph token acquisition for tenant
  `11111111-1111-4111-8111-111111111111` succeeds.
- The current delegated Graph token is missing the permissions required for
  live registration writes: `CustomAuthenticationExtension.ReadWrite.All`,
  `EventListener.ReadWrite.All`, and
  `Policy.ReadWrite.ApplicationConfiguration`. `Application.ReadWrite.All` is
  present.

After the Function is deployed and authenticated, grant the operator these
delegated Microsoft Graph permissions and an External ID role that can manage
custom authentication extensions, such as **Authentication Extensibility
Administrator** or **Application Administrator**:

- `CustomAuthenticationExtension.ReadWrite.All`
- `EventListener.ReadWrite.All`
- `Policy.ReadWrite.ApplicationConfiguration`
- `Application.ReadWrite.All`

Then run the idempotent registration script from the repository root. The script
reads the frontend API client ID and claims provider audience from the ignored
`docs/external-id-app-registrations.local.json` artifact when present.

```bash
scripts/register-claims-extension.sh --dry-run \
  --callback-url "https://<function-app>.azurewebsites.net/api/OnTokenIssuanceStart"

scripts/register-claims-extension.sh \
  --callback-url "https://<function-app>.azurewebsites.net/api/OnTokenIssuanceStart"
```

Equivalent explicit usage:

```bash
scripts/register-claims-extension.sh \
  --tenant-id 11111111-1111-4111-8111-111111111111 \
  --tenant-name contosoexternalid \
  --frontend-api-client-id 33333333-3333-4333-8333-333333333333 \
  --claims-provider-audience api://44444444-4444-4444-8444-444444444444 \
  --callback-url "https://<function-app>.azurewebsites.net/api/OnTokenIssuanceStart"
```

The live run creates or updates:

1. an `onTokenIssuanceStartCustomExtension` with
   `azureAdTokenAuthentication.resourceId` set to the Custom Claims Provider
   audience;
2. an `onTokenIssuanceStartListener` for the frontend API resource application;
3. a claims mapping policy that maps the three custom claims from
   `CustomClaimsProvider` into API access tokens; and
4. the frontend API application's `api.acceptMappedClaims=true` and
   `api.requestedAccessTokenVersion=2` settings.

To verify issuance after user-flow attachment and sign-in, pass a short-lived
API access token without writing it to disk:

```bash
export VALIDATION_ACCESS_TOKEN='<short-lived API access token>'
scripts/register-claims-extension.sh \
  --callback-url "https://<function-app>.azurewebsites.net/api/OnTokenIssuanceStart"
```

The validation step decodes the token locally and checks that
`extension_tenantId`, `tenant_roles`, and `tenant_status` are present. Do not echo,
record, or commit the token.

## Local verification before live deployment

Run these non-destructive checks before attempting a subscription deployment:

```bash
az bicep build --file infra/main.bicep
az bicep build --file infra/tenant-onboarding.bicep
bash -n scripts/*.sh
python3 - <<'PY'
import ast, pathlib
for path in pathlib.Path("scripts").glob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY
scripts/create-external-id-demo-users.sh --dry-run --check-user-flow-support
scripts/register-claims-extension.sh --dry-run --callback-url https://func-example-claims.azurewebsites.net/api/OnTokenIssuanceStart
scripts/seed-data.sh --dry-run
scripts/new-tenant.sh --dry-run
scripts/validate-deployment.sh
(cd src/spa && npm run build)
```

Expected dry-run output includes:

- `scripts/seed-data.sh --dry-run` validates all tenant fixtures and prints planned
  Cosmos database/container creation plus deterministic document upserts; the
  initial tenants should end with `Planned 57 deterministic seed documents across
  3 tenant(s).`
- `scripts/create-external-id-demo-users.sh --dry-run` validates the six selected
  fixture users and writes an ignored safe mapping with placeholder object IDs.
- `scripts/new-tenant.sh --dry-run` validates DeltaEquity, prints the planned
  tenant-onboarding Bicep deployment, runs the seed dry run for DeltaEquity, and
  should end with `Planned 19 deterministic seed documents across 1 tenant(s).`
- `scripts/validate-deployment.sh` prints `DRY-RUN: pass --live...` and the list
  of read-only live checks without requiring Azure resources or tokens.
- The SPA build emits a Vite production bundle under `src/spa/dist/`.

Attempt .NET builds only when the .NET SDK is installed:

```bash
dotnet build src/shared/Contoso.AssetManagement.Shared.csproj
dotnet build src/backend-api/Contoso.AssetManagement.BackendApi.csproj
dotnet build src/frontend-api/Contoso.AssetManagement.FrontendApi.csproj
dotnet build src/custom-claims-provider/Contoso.AssetManagement.CustomClaimsProvider.csproj
```

Skip live `az deployment sub what-if`, `azd up`, tenant provisioning, and
`scripts/validate-deployment.sh --live` unless an `azd` environment, required
deployment parameters, Azure credentials, and a short-lived validation token are
configured for a safe target subscription.

## Deploy

```bash
azd env new <environment-name>
azd env set AZURE_LOCATION eastus
azd up
```

Container API deployments require Azure Container Registry for remote builds
when Docker/Podman is unavailable locally. The Bicep deployment provisions ACR
and emits `AZURE_CONTAINER_REGISTRY_ENDPOINT`; ensure the active `azd`
environment has this value before deploying the APIs:

```bash
azd env set AZURE_CONTAINER_REGISTRY_ENDPOINT <acr-name>.azurecr.io
azd deploy backend-api
azd deploy frontend-api
```

The Container Apps use managed identities to pull from ACR; keep AcrPull RBAC
and `configuration.registries[*].identity` aligned with the deployed identities.

Tenant Cosmos data-plane access uses one user-assigned managed identity per
business tenant. The backend Container App has those identities available and
selects the correct identity from TenantDirectory metadata
(`cosmosIdentityClientId`) after validating `extension_tenantId`. Each tenant
Cosmos account should have exactly one `DataContributor` assignment: the
matching tenant identity. The backend system-assigned identity should retain
control-plane Cosmos reader access, but not tenant data account contributor
access.

APIM includes API-level CORS handling before JWT validation so browser preflight
requests from the Static Web App and local dev origins succeed without an
Authorization header. If a new SPA origin is added, update
`allowedCorsOrigins` in `infra/main.bicep` and run `azd provision`.

The Custom Claims Provider runs on Functions Flex Consumption and its package
storage account is private. Keep blob, queue, and table private endpoints plus
matching private DNS zones (`privatelink.blob.core.windows.net`,
`privatelink.queue.core.windows.net`, and
`privatelink.table.core.windows.net`) in place before disabling storage public
network access. If External ID sign-in fails with AADSTS1100001 and the Function
endpoint returns 503, verify the Function storage private endpoints and package
container access before changing the claims provider code.

The APIM API path is `api`, while the frontend API routes are also rooted under
`/api`. Keep the APIM backend `serviceUrl` set to the frontend API base URL plus
`/api`; otherwise requests such as `/api/tenants/{tenantId}/portfolios` are
forwarded as `/tenants/{tenantId}/portfolios` and return 404.

External ID may issue access tokens with the resource app **client ID** in the
`aud` claim even when the SPA requested verified-domain scopes. For strict
single-audience validation, APIM, frontend API, and backend API validate the
frontend API client ID as the runtime audience. Keep `API_AUDIENCE` aligned with
the actual token `aud`; keep the verified-domain URI as the OAuth scope prefix.

For the Vite SPA, verify that `src/spa/public/config.json` contains the deployed
Static Web App URL as `redirectUri`/`postLogoutRedirectUri`, the APIM gateway URL
as `api.apimBaseUrl`, and verified-domain Frontend API scopes. Then deploy the
built `dist` folder to Static Web Apps:

```bash
cd src/spa
npm run build
token="$(az staticwebapp secrets list \
  -g <resource-group> \
  -n <static-web-app-name> \
  --query properties.apiKey -o tsv)"
npx -y @azure/static-web-apps-cli deploy dist \
  --deployment-token "$token" \
  --env production
```

After the first deployment, add the Static Web App URL to the SPA app
registration redirect URIs in the External ID tenant.

To generate a short-lived External ID access token for live validation without
storing passwords or secrets, use the SPA public client and PKCE helper:

```bash
python3 scripts/get-external-id-access-token.py --validate-bff
```

The helper opens browser sign-in, listens on the registered local redirect URI
(`http://127.0.0.1:5173` by default), exchanges the authorization code for an
access token, prints only safe decoded claims, and optionally validates the
same-tenant and cross-tenant BFF routes. Use `--print-token` only when you need
to pass the token to another validation tool.

Before a deployment that may affect existing resources:

```bash
az bicep build --file infra/main.bicep
az deployment sub what-if --location eastus --template-file infra/main.bicep
```

### Optional Cosmos Data Explorer jumpbox

Use the jumpbox only when an operator must inspect private Cosmos data through
Azure Portal Data Explorer. Cloud-only validation remains the default.

The first enablement is a two-stage operation because the emergency Windows
administrator password must exist before the main template can retrieve it:

```bash
export AZURE_RESOURCE_GROUP='<resource-group>'
export KEY_VAULT_NAME='<key-vault-name>'
export JUMPBOX_USER_PRINCIPAL_ID='<operator-object-id>'

python3 scripts/bootstrap-jumpbox-secret.py \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --key-vault "$KEY_VAULT_NAME"

azd env set ENABLE_JUMPBOX true
azd env set JUMPBOX_USER_PRINCIPAL_ID "$JUMPBOX_USER_PRINCIPAL_ID"
azd env set JUMPBOX_USER_PRINCIPAL_TYPE User
azd env set JUMPBOX_VM_SIZE Standard_D2als_v7
azd env set JUMPBOX_SHUTDOWN_TIME 1900
azd env set JUMPBOX_SHUTDOWN_TIME_ZONE UTC
azd provision
```

Set `ENABLE_JUMPBOX=false` explicitly in azd environments that use the main
parameter file but do not deploy this optional stack.

For a live environment with unrelated resource drift, preview and deploy only
the jumpbox slice with `infra/jumpbox-deployment.bicep` instead of applying the
full subscription template:

```bash
account_names="$(az cosmosdb list -g "$AZURE_RESOURCE_GROUP" --query '[].name' -o json)"

az deployment group what-if \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --template-file infra/jumpbox-deployment.bicep \
  --parameters environmentName="$AZURE_ENV_NAME" \
               keyVaultName="$KEY_VAULT_NAME" \
               operatorPrincipalId="$JUMPBOX_USER_PRINCIPAL_ID" \
               cosmosAccountNames="$account_names"

az deployment group create \
  --name jumpbox-access \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --template-file infra/jumpbox-deployment.bicep \
  --parameters environmentName="$AZURE_ENV_NAME" \
               keyVaultName="$KEY_VAULT_NAME" \
               operatorPrincipalId="$JUMPBOX_USER_PRINCIPAL_ID" \
               cosmosAccountNames="$account_names"
```

The bootstrap script does not print or persist the password outside Key Vault.
It leaves an existing secret unchanged unless `--rotate` is supplied. Rotating
the secret does not automatically change the password on an existing VM;
redeploy or use an approved VM password reset procedure immediately after
rotation.

To connect:

1. Start `vm-<environment-name>-jumpbox` from Azure Portal if it is deallocated.
2. Open the VM, select **Connect** > **Bastion**, and choose Microsoft Entra ID authentication.
3. Open Edge in the remote Windows session and sign in to Azure Portal as the configured operator.
4. Open each Cosmos account and use Data Explorer for read-only inspection.
5. Sign out of Azure Portal, clear browser data if another operator will use the VM, disconnect, and stop/deallocate the VM.

Do not retrieve the emergency local administrator password for routine access.
If Entra sign-in is unavailable, retrieve it from Key Vault only under the
approved break-glass process. Never add a public TCP 3389 NSG rule.

The VM auto-shuts down nightly. The VM compute charge stops after deallocation,
but Bastion and public IP resources remain billable while provisioned.

## Seed demo data

```bash
scripts/seed-data.sh
```

The seed script reads deterministic fixtures from `scripts/seed-data.fixtures.json`.
It uses Azure CLI authentication to obtain a Cosmos DB data-plane token and
upserts documents without connection strings, account keys, Cosmos keys, or user
JWTs. Set `AZURE_RESOURCE_GROUP` or pass `--resource-group` if the resource group
cannot be discovered from `AZURE_ENV_NAME`.

Useful variants:

```bash
scripts/seed-data.sh --dry-run
scripts/seed-data.sh --include-onboarding-tenant
scripts/seed-data.sh --tenant DeltaEquity --resource-group <resource-group>
```

Expected result:

- Control-plane tenant directory contains AlphaCapital, BetaWealth, and GammaFund.
- Each tenant has at least two users with tenant-scoped roles.
- Each tenant data account has at least two portfolios, five positions per portfolio, and one pending transaction approval.

## Validate deployment

```bash
scripts/validate-deployment.sh
```

The default mode is a safe dry run that validates local inputs and prints the
planned read-only checks. To run live validation, provide explicit deployment
configuration and a short-lived user API access token:

```bash
export VALIDATION_ACCESS_TOKEN='<short-lived AlphaCapital API access token>'
scripts/validate-deployment.sh --live \
  --environment-name "$AZURE_ENV_NAME" \
  --tenant AlphaCapital \
  --cross-tenant BetaWealth
```

When the optional jumpbox is enabled and running, include:

```bash
scripts/validate-deployment.sh --live \
  --jumpbox-only \
  --environment-name "$AZURE_ENV_NAME" \
  --jumpbox-user-principal-id "$JUMPBOX_USER_PRINCIPAL_ID"
```

The jumpbox checks confirm Bastion and Entra login configuration, nightly
shutdown, absence of non-Bastion inbound access, Cosmos Data Reader plus ARM
Reader on every account, and private DNS/TCP 443 connectivity plus Azure Portal
and Entra sign-in egress from the VM.

Do not echo tokens in terminal recordings, store them in files, or commit them.
Expected live checks:

- Health endpoints respond successfully.
- All test users receive expected `extension_tenantId`, `tenant_roles`, and `tenant_status`.
- Same-tenant calls succeed.
- Cross-tenant calls return 403.
- Unauthorized direct backend calls fail.
- Cosmos resolves to private IPs from deployed app hosts.
- Public-route Cosmos access fails.
- All Cosmos accounts have `disableLocalAuth: true`.
- The scheduled query alert named `alert-<env>-tenant-mismatch-403` fires when
  more than five tenant-mismatch 403 responses occur in five minutes.

The alert query reads structured application logs (`tenantId`, `userId`,
`correlationId`, `operation`, `authorizationDecision`, `result`, and
`statusCode`) and APIM gateway diagnostics when available. Application logs and
APIM must preserve `X-Correlation-ID` so demo traffic can be traced end to end.

## Add a tenant

```bash
scripts/new-tenant.sh
```

`DeltaEquity` is the default tenant and is expected to complete in under 10 minutes
from an Azure-hosted runner with private network access to Cosmos DB. The script:

- deploys `infra/tenant-onboarding.bicep`, which reuses the tenant Cosmos and
  Cosmos RBAC Bicep modules;
- creates the tenant Cosmos account with local auth disabled, public access
  disabled, private endpoint, private DNS, `assets` database, and containers;
- grants the backend API managed identity Cosmos DB SQL Data Contributor;
- runs `scripts/seed-data.py --tenant DeltaEquity` to upsert TenantDirectory,
  memberships, role assignments, onboarding state, portfolios, positions, and
  approvals through Azure CLI/AAD auth; and
- validates secure Cosmos settings, private endpoint/DNS, backend RBAC,
  containers, directory records, entitlements, and seeded tenant data.

Useful variants:

```bash
scripts/new-tenant.sh --dry-run
scripts/new-tenant.sh --what-if --resource-group <resource-group>
scripts/new-tenant.sh DeltaEquity --resource-group <resource-group>
scripts/new-tenant.sh DeltaEquity --seed-principal-id <principal-object-id>
```

Use `--seed-principal-id` only when the runner needs a Cosmos SQL data-plane
role assignment on the new tenant account to seed data. The control-plane account
must already grant the runner data-plane access; no account keys, connection
strings, JWTs, or secrets are used.

Expected result:

- DeltaEquity Cosmos account, private endpoint, DNS integration, RBAC, database, and containers are created.
- TenantDirectory and entitlement records are inserted.
- Demo data is seeded.
- Validation confirms secure data-plane configuration and seeded DeltaEquity
  directory, entitlement, and portfolio records. App/API validation should then
  confirm same-tenant access and cross-tenant rejection.

## Remove a tenant

Tenant removal is not part of the POC demo path. If added later, removal must delete or disable TenantDirectory records before data-plane resources are removed, and must preserve auditability.

## Rotate secrets

The POC should not depend on checked-in secrets or Cosmos keys. If any external app credentials are required, rotate them in Entra ID or Key Vault and update runtime configuration through secure `azd env` or Azure configuration channels.

## Teardown

```bash
azd down
```

Confirm that resource deletion is acceptable before running teardown in a shared subscription.
