# Workforce Tenant Federation Setup

This guide configures workforce tenant `66666666-6666-4666-8666-666666666666` as an upstream identity provider for External ID tenant `11111111-1111-4111-8111-111111111111`.

Users authenticate in their workforce home tenant, but External ID remains the issuer of the SPA and API tokens. APIM, the frontend API, and the backend API must continue to reject workforce-issued user tokens.

## Admission model

Access requires all three controls:

1. The workforce user is assigned to the federation enterprise application.
2. A federated customer identity for that workforce object ID exists in External ID.
3. An active business-tenant membership and role assignment exists in the control-plane store.

This is controlled customer federation, not classic B2B guest collaboration. Never authorize a user from their email domain alone.

## Prerequisites

- An operator who can manage app registrations, enterprise applications, consent, and user assignments in the workforce tenant.
- An External Identity Provider Administrator (or equivalent privileged administrator) in the External ID tenant.
- Microsoft Graph access for the automation scripts:
  - Workforce tenant: application and service-principal read/write, user read, and app-role assignment permissions.
  - External ID tenant: user read/write for pre-provisioning federated customer users.
- Azure CLI authenticated to both directories. Subscription access isn't required for directory-only operations.
- The existing External ID SPA, frontend API, user flow, and Custom Claims Provider configured as described in `docs/runbook.md`.

Use the checked-in sample manifest as a template:

```bash
cp docs/workforce-federation-authorized-users.sample.json \
  docs/workforce-federation-authorized-users.local.json
```

Replace every sample source object ID, email, display name, business tenant, and role with approved values. The local file is ignored by Git.

## 1. Create the workforce federation application

**Automated**

Inspect the planned changes without authenticating or writing files:

```bash
scripts/create-workforce-federation-app.sh \
  --workforce-tenant-domain contosoworkforce.onmicrosoft.com \
  --authorized-user-manifest docs/workforce-federation-authorized-users.local.json \
  --dry-run
```

Authenticate to the workforce tenant when needed:

```bash
az login \
  --tenant 66666666-6666-4666-8666-666666666666 \
  --allow-no-subscriptions
```

Create or reconcile the single-tenant app registration, enterprise application, and allowlisted assignments:

```bash
scripts/create-workforce-federation-app.sh \
  --workforce-tenant-domain contosoworkforce.onmicrosoft.com \
  --authorized-user-manifest docs/workforce-federation-authorized-users.local.json
```

The command writes the non-secret result to `docs/workforce-entra-federation-app.local.json`. It configures:

- Supported accounts: this organizational directory only.
- Required redirect URIs:
  - `https://contosoexternalid.ciamlogin.com/11111111-1111-4111-8111-111111111111/federation/oauth2`
  - `https://contosoexternalid.ciamlogin.com/contosoexternalid.onmicrosoft.com/federation/oauth2`
- Microsoft Graph delegated permissions `openid`, `profile`, `email`, and `User.Read`.
- The optional `email` ID-token claim.
- `Assignment required` on the enterprise application.
- Default app assignment for each active, allowlisted workforce object ID.

**Manual**

In the workforce tenant, grant admin consent for the configured Microsoft Graph delegated permissions. Confirm that **Enterprise applications > Contoso Asset Management - External ID Federation > Properties > Assignment required?** is **Yes**, and that only approved users are assigned.

## 2. Create the federation client secret

**Manual**

In the workforce app registration:

1. Open **Certificates & secrets > Client secrets**.
2. Create a time-limited secret with an owner and tracked expiry.
3. Copy the secret **value** once. Do not put it in this repository, `.azure/`, shell history, tickets, or command output.
4. Keep the value only long enough to enter it into the External ID provider configuration. If policy requires escrow, use the approved secret store.

The automation intentionally never creates, accepts, prints, or stores this secret.

## 3. Add the provider to External ID

**Manual**

Switch to External ID tenant `11111111-1111-4111-8111-111111111111` in the Microsoft Entra admin center:

1. Open **Entra ID > External Identities > All identity providers**.
2. Select **Custom > Add new > Open ID Connect**.
3. Enter:

| Setting | Value |
|---|---|
| Display name | `Sign in with Contoso Workforce` |
| Well-known endpoint | `https://login.microsoftonline.com/organizations/v2.0/.well-known/openid-configuration` |
| OpenID Issuer URI | `https://login.microsoftonline.com/contosoworkforce.onmicrosoft.com/v2.0` |
| Client ID | `application.clientId` from `docs/workforce-entra-federation-app.local.json` |
| Client authentication | `client_secret_post` / client secret |
| Client secret | The secret value created in step 2 |
| Scope | `openid profile` |
| Response type | `code` |

4. Configure claim mappings:
   - Map **Sub** to the workforce token's `oid` claim. This immutable object ID
     must match `sourceObjectId`/`issuerAssignedId` for controlled
     pre-provisioning; leaving it mapped to the pairwise `sub` claim causes
     **Your account already exists** when the same email is pre-created.
   - Keep the standard mappings for `name`, `given_name`, `family_name`, and
     `email`.
5. Save the provider.

Keep email required in the user flow. The workforce app is configured to emit it.

The issuer must use the verified workforce tenant domain, not the tenant-ID form, when the SPA uses `domain_hint` for issuer acceleration. The script emits this value as `externalIdPortalProviderValues.issuer`.

The generic Microsoft Graph beta `oidcIdentityProvider` resource is not used for this step. Its current reference rejects `microsoftonline.com` issuers, while Microsoft's dedicated Entra federation procedure explicitly supports this admin-center configuration.

## 4. Create or update the SPA user flow

**Manual**

1. Open **Entra ID > External Identities > User flows** in the External ID tenant.
2. If no flow exists, select **New user flow**:
   - Name: `ContosoAssetsSignUpSignIn`.
   - Under **Identity providers**, select both:
     - **Sign in with Contoso Workforce**.
     - **Email Accounts > Email with password** so the existing local demo accounts continue to work.
   - Do not select **Email one-time passcode** for this POC.
   - Under **User attributes**, keep the automatically selected **Email Address** and select **Display Name**.
   - Do not select **Given Name**, **Surname**, **City**, **Country/Region**, or other attributes; the application doesn't require them.
   - Select **Create**.
3. Open the new or existing flow and select **Applications > Add application**.
4. Add **Contoso Asset Management POC SPA** (client ID `22222222-2222-4222-8222-222222222222`).
   If it is missing from the list, rerun `scripts/create-external-id-app-registrations.py`;
   the SPA enterprise application/service principal must exist before the
   registration is eligible for user-flow association.
5. Open **Identity providers** on the flow.
6. Verify **Sign in with Contoso Workforce** and **Email with password** are both enabled.
7. If either provider wasn't available during flow creation, enable it here.
8. Save, then use **Run user flow** with the SPA application to confirm that both sign-in options appear.

Do not create a second SPA authority. The SPA continues to start authentication at the existing `ciamlogin.com` authority.

## 5. Configure email-first SPA routing

The SPA asks for an email address before starting MSAL. It uses only the normalized, exact domain to choose an authentication journey:

- `contosoworkforce.onmicrosoft.com` sends `login_hint` and `domain_hint` to External ID, which accelerates to **Sign in with Contoso Workforce**.
- `partnerworkforce.onmicrosoft.com` is represented as disabled and is blocked before opening MSAL because no provider is configured for it.
- Any unlisted domain sends only `login_hint` and continues through the normal External ID local-account experience.
- **Choose another sign-in method** sends no hints and opens the normal External ID provider picker.

Configure routes in `auth.identityRouting.providers` in `src/spa/public/config.json` and the local sample:

```json
{
  "key": "contoso-workforce",
  "displayName": "Contoso Workforce",
  "domains": ["contosoworkforce.onmicrosoft.com"],
  "domainHint": "contosoworkforce.onmicrosoft.com",
  "enabled": true
}
```

Provider keys and domains must be unique. Enabled entries require a valid `domainHint`. To onboard another workforce organization, first configure and attach its own External ID OIDC provider, then add and enable its independent runtime entry. A disabled entry can reserve a planned domain without routing users to an unavailable provider.

The typed email is held only in memory during discovery. The SPA does not persist it, log it, send it to application APIs, infer a business tenant from it, or use it for authorization. Access still requires all three admission controls and the authoritative `extension_tenantId` claim from a validated External ID access token.

## 6. Pre-provision federated users

**Automated**

Validate the manifest and planned federated issuer without making Graph calls:

```bash
scripts/create-workforce-federated-users.sh \
  --manifest docs/workforce-federation-authorized-users.local.json \
  --dry-run
```

Run the provisioning command from a session that can acquire Microsoft Graph tokens for both tenants:

```bash
scripts/create-workforce-federated-users.sh \
  --manifest docs/workforce-federation-authorized-users.local.json
```

The safe ignored output maps each immutable workforce object ID to its External ID object ID. The federated identity issuer is:

```text
https://login.microsoftonline.com/contosoworkforce.onmicrosoft.com/v2.0<11111111-1111-4111-8111-111111111111>
```

This must exactly match the format External ID creates from the same
domain-based OpenID issuer configured on the provider. The angle brackets are
literal. The provisioning script reconciles users created with the legacy
tenant-ID/slash issuer in place, preserving their External ID object IDs and
application entitlements.

The External ID object ID, not email or workforce object ID, is the `userId` used by the Custom Claims Provider entitlement lookup.

## 7. Seed application entitlements

**Automated**

Dry-run the control-plane writes first:

```bash
scripts/seed-federated-entitlements.sh \
  --user-map docs/workforce-federated-users.local.json \
  --dry-run
```

Then apply the active membership and role assignments:

```bash
scripts/seed-federated-entitlements.sh \
  --user-map docs/workforce-federated-users.local.json
```

Only `TenantAdmin`, `PortfolioManager`, and `PortfolioViewer` are valid roles. A user without an active membership and resource-specific role assignment must fail closed during token issuance.

## 8. Validate sign-in

Use **Run user flow**, or open the SPA, select **Sign in**, enter an approved `@contosoworkforce.onmicrosoft.com` email, and continue. A domain-confirmation page can still appear during issuer acceleration. Validate the resulting access token without sharing it:

| Claim | Expected |
|---|---|
| `iss` | External ID `ciamlogin.com` issuer |
| `aud` | Frontend API client ID/audience |
| `idp` | Workforce Entra issuer, when emitted |
| `extension_tenantId` | Approved SaaS business tenant |
| `tenant_roles` | Approved tenant role |
| `tenant_status` | `active` |
| `scp` | Required `assets.read` and/or `assets.write` scope |

Also prove these negative cases:

- Mixed-case `@ContosoWorkforce.onmicrosoft.com` input follows the workforce route.
- `user@partnerworkforce.onmicrosoft.com` is blocked locally and does not open a popup.
- An unknown domain follows External ID local sign-in with the email prefilled.
- A malformed email remains in the form with a clear validation message.
- **Choose another sign-in method** opens the unaccelerated provider picker.
- An unassigned workforce user cannot use the federation enterprise application.
- An assigned user without an active entitlement receives no usable tenant access token.
- A local External ID demo user can still sign in.
- A valid user receives 403 when calling a route for another business tenant.
- APIs reject a workforce-issued access token presented directly.

Run the existing deployment checks with the federation-specific issuer and IdP assertions:

```bash
scripts/validate-deployment.sh --live \
  --tenant AlphaCapital \
  --expected-role TenantAdmin \
  --expected-issuer "https://11111111-1111-4111-8111-111111111111.ciamlogin.com/11111111-1111-4111-8111-111111111111/v2.0" \
  --expected-audience "33333333-3333-4333-8333-333333333333" \
  --expected-idp "https://login.microsoftonline.com/66666666-6666-4666-8666-666666666666/v2.0"
```

Supply the short-lived access token through `VALIDATION_ACCESS_TOKEN`; never place it in the command line or a file.

Home-tenant Conditional Access and MFA apply during workforce authentication. External ID doesn't currently trust upstream Entra MFA, so a second MFA prompt can occur when External ID also requires MFA.

## Rotation

Before the current secret expires:

1. Create a second workforce app client secret.
2. Update the External ID provider with the new secret.
3. Run the user flow and complete an end-to-end API call.
4. Remove the old secret only after successful validation.
5. Record owner, expiry, test evidence, and rollback window without recording either secret value.

If validation fails, restore the still-valid old provider secret and investigate before deleting either credential.

## Deprovisioning

Deprovision both admission layers:

1. Remove the user's assignment from the workforce federation enterprise application.
2. Disable the user's control-plane membership and roles:

   ```bash
   scripts/seed-federated-entitlements.sh \
     --user-map docs/workforce-federated-users.local.json \
     --disable-source-object-id "<workforce-user-object-id>"
   ```

3. Revoke sessions when immediate termination is required.
4. Optionally disable the External ID customer user after confirming it has no other valid application relationship.
5. Verify that a new token cannot be issued with active tenant claims.

Do not rely on deleting or disabling the workforce account alone; the External ID customer object and application entitlement have independent lifecycles.

## Troubleshooting

| Symptom | Check |
|---|---|
| Provider button is missing | Provider is saved and enabled on the SPA's existing user flow; issuer/discovery values are exact. |
| Email does not accelerate to the workforce provider | Runtime domain and `domainHint` exactly match the verified workforce domain, and the provider issuer uses the domain-based URI. |
| SPA reports invalid identity-routing configuration | Provider keys/domains are unique, domains are valid DNS names, `enabled` is Boolean, and enabled entries have a valid `domainHint`. |
| `Your account already exists` after workforce authentication | Verify **Sub** maps to `oid`, the user's `issuerAssignedId` equals the workforce object ID, and its issuer exactly matches the provider-derived `.../v2.0<external-tenant-id>` format. Rerun `scripts/create-workforce-federated-users.sh` after correcting the provider. |
| `No email address was obtained` | Workforce app emits the optional `email` claim and the source user has a usable mail/UPN. |
| `AADSTS500208` | The provider issuer uses the workforce tenant and the user belongs to the allowed account type. |
| External ID error `40015` | Issuer exactly matches the upstream token, discovery is reachable, and required claims are returned. |
| User authenticates but token issuance fails | External ID object ID has an active membership and matching frontend API role assignment. |
| Wrong tenant data | Stop: validate `extension_tenantId`, entitlement records, and route binding. Never work around it with a tenant header. |

## Microsoft references

- [Add Microsoft Entra ID for customer sign-in](https://learn.microsoft.com/entra/external-id/customers/how-to-entra-id-federation-customers)
- [Add OIDC for customer sign-in](https://learn.microsoft.com/entra/external-id/customers/how-to-custom-oidc-federation-customers)
- [Create a sign-up and sign-in user flow](https://learn.microsoft.com/entra/external-id/customers/how-to-user-flow-sign-up-sign-in-customers)
