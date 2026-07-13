# Foundry Hosted-Session Management Design

## Purpose

This document explains how Contoso Asset Management should manage Microsoft Foundry hosted-agent sessions end to end for the `portfolio-agent` path.

The goal is to let an authenticated External ID user resume the correct Foundry hosted-agent sandbox while preventing another user from stealing or replaying a session identifier. This design covers Foundry hosted sessions only: sandbox affinity, `$HOME` / session file persistence, lifecycle operations, and user/tenant ownership. It does not design agent conversation history or model memory.

## Scope

### In scope

- Foundry hosted-session creation, resume, stop/delete, and optional file operations.
- Binding a Foundry session to a validated External ID user and business tenant.
- Preventing browser-visible session handles from being used by a different user.
- BFF-owned server-side mapping from Contoso session handle to Foundry `agent_session_id`.
- Protocol compatibility for the current `portfolio-agent` deployment, with Responses v2 as the primary path and Invocations 1.0 treated only as legacy rollback if explicitly re-enabled.
- Observability, security logging, and validation strategy.

### Out of scope

- Responses `conversation` or `previous_response_id` message history.
- Agent Framework `AgentSessionStore` conversation/tool-run memory.
- Prompt/model memory.
- Direct SPA calls to Foundry agent, session, or file endpoints.
- Changes to tenant-data authorization through APIM, MCP, or backend APIs.

## Key concepts

| Concept | Meaning in this design |
| --- | --- |
| Foundry hosted session | Foundry-managed sandbox for a hosted agent. It gives compute and persisted filesystem state such as `$HOME` and uploaded files. |
| `agent_session_id` | Foundry session identifier used to route a call to the same hosted-agent sandbox. This is sensitive server-side routing state and must not be exposed to the SPA. |
| Application conversation handle | Opaque Contoso handle returned to the SPA so the user can resume the same Foundry Responses conversation and hosted session through the BFF. It is not the Foundry conversation ID or `agent_session_id`. |
| Conversation history | Message/thread continuity. This is separate from hosted-session sandbox affinity and is not solved in this design. |
| External ID user | The authenticated customer user represented by the validated Azure External ID access token. |
| Business tenant | The SaaS tenant from the validated `extension_tenantId` token claim. |

## Foundry session facts used by this design

Microsoft Foundry hosted sessions and conversations are distinct:

- A hosted session represents sandbox compute and persisted filesystem state.
- A conversation represents message history for protocols that support platform-managed history.
- Responses protocol v2 is the implemented primary invocation path for hosted-session affinity.
- For Responses protocol v2, the BFF sends hosted-session affinity through the request body field `agent_session_id`.
- For Invocations protocol, Foundry does not manage conversation history; the container defines request/response shape and any memory behavior.
- For Invocations protocol, Foundry reads the hosted-session binding only from the `agent_session_id` query-string parameter. This is a rollback-only path for this POC, not the default implementation path.
- On the Invocations rollback path, request-body fields or headers named `agent_session_id`, `session_id`, or `x-agent-session-id` do not control sandbox routing; only the Invocations query-string parameter does.
- Hosted-session REST calls require a Foundry data-plane bearer token for `https://ai.azure.com/.default` and the preview header `Foundry-Features: HostedAgents=V1Preview`.
- Foundry supports session create, list, get, stop, delete, and file operations under the hosted-agent endpoint.
- Sessions can persist across idle periods; compute can be deprovisioned and later rehydrated. Cleanup still needs application lifecycle policy.

## Current repository state

The current implementation has the major application layers needed for this design:

- `azure.yaml` defines `portfolio-agent` and the parallel
  `portfolio-agent-python` with `host: azure.ai.agent`.
- `src/portfolio-agent/agent.yaml` declares only `responses` protocol version `2.0.0`; this is the primary hosted-agent protocol.
- The BFF runtime now uses Responses only. Azure still carries `PortfolioAgent__InvocationsEndpoint` and `PortfolioAgent__UseInvocations=false` as deployment configuration support, but the frontend API no longer includes an Invocations request path.
- The BFF creates, owns, resumes, and deletes Foundry hosted-session bindings. It stores Foundry `agent_session_id` and Foundry Responses conversation IDs server-side and sends them only in Responses v2 request bodies.
- `src/frontend-api/Agent/FoundryPortfolioAgentClient.cs` sends only Responses requests and is aligned with the current hosted-agent declaration.
- The SPA sends and stores a value named `conversationId`; that value is an opaque BFF-issued conversation handle. It maps server-side to the Foundry Responses conversation ID and hosted-session ID, neither of which is exposed to the SPA.
- The existing agent-memory Cosmos account and `CosmosAgentSessionStore` are about agent conversation/tool-run state, not Foundry hosted-session ownership. They are intentionally not part of this hosted-session design.
- `portfolio-agent-python` is Foundry-only and is not selected by the BFF. It
  uses its own `agent-memory-python-{tenant}` database namespace and does not
  participate in the SPA/BFF hosted-session binding lifecycle described below.

The protocol posture decision is that Responses protocol v2 is the primary implementation path. Invocations protocol 1.0 is not a frontend API runtime path. Azure endpoint/config plumbing remains available, but a rollback deployment would need to restore the Invocations application code and protocol declaration explicitly.

## Implemented end-to-end behavior

### New session flow

1. The user signs in through Azure External ID.
2. External ID issues an API access token containing `extension_tenantId`, roles, scopes, tenant status, issuer, audience, and stable user identity claims.
3. The SPA calls the BFF route, for example:

   ```http
   POST /tenants/AlphaCapital/agent/chat
   Authorization: <External ID bearer token>
   X-Correlation-ID: <optional-correlation-id>
   ```

4. APIM validates the token and route tenant policy.
5. The BFF validates the token again and confirms:
   - route tenant equals validated `extension_tenantId`;
   - tenant status allows access;
   - required scope/role is present;
   - user ID is derived from the validated token;
   - no browser-provided tenant, user, or session value is trusted as authority.
6. The request does not contain a valid application session handle, so the BFF creates a new Foundry hosted session server-side.
7. The BFF derives a stable Foundry user identity or isolation value from validated External ID claims.
8. The BFF calls Foundry to create the hosted session using the BFF managed identity.
9. Foundry returns an `agent_session_id`.
10. The BFF stores a server-side binding record:

    ```text
    appSessionHandle -> foundryAgentSessionId
    appSessionHandle -> validatedTenantId
    appSessionHandle -> validatedUserId
    appSessionHandle -> foundryOwnerIdentity
    ```

11. The BFF invokes the hosted agent through the Responses v2 endpoint with:

    ```text
    { "input": "...", "store": false, "metadata": { "contoso_tenant_id": "...", "contoso_user_id": "...", "contoso_correlation_id": "..." }, "agent_session_id": "<stored Foundry session id>", "conversation": { "id": "<stored Foundry conversation id>" } }
    ```

    The forwarded user token and service token are sent through the same trusted headers the hosted agent consumes (`X-User-Authorization` and `X-Service-Authorization`) because Responses metadata has size constraints and must not carry long JWTs. The BFF sends `x-client-*` copies of these trusted headers because the hosted Responses SDK exposes custom client headers through `ResponseContext.ClientHeaders` only when they use that prefix.

12. The BFF returns the agent answer and only the opaque application conversation handle to the SPA.

The SPA never sees the Foundry `agent_session_id` or raw Foundry conversation ID.

### Resume session flow

1. The SPA sends the opaque application session handle on the next chat request.
2. APIM and the BFF validate the current External ID token again.
3. The BFF derives the current validated tenant and user from the token.
4. The BFF looks up the session binding by application handle plus validated tenant plus validated user.
5. If the binding is found and active, the BFF retrieves the stored Foundry conversation ID and `agent_session_id`.
6. The BFF invokes Foundry Responses v2 with the stored `conversation` and `agent_session_id` body fields and the same server-derived identity/isolation context.
7. The BFF updates last-used metadata and returns the answer.

### Ownership mismatch flow

If a different user copies an application session handle:

1. The attacker sends the copied handle with their own valid External ID token.
2. The BFF derives the attacker's validated tenant and user from that token.
3. The BFF lookup uses `handle + tenant + user`, not just `handle`.
4. The lookup fails or returns an ownership mismatch.
5. The BFF returns 404 or 403 and logs `session-owner-mismatch`.
6. The BFF does not call Foundry with the stored `agent_session_id`.

The copied handle is therefore useless without the same validated External ID tenant and user.

If a caller sends a Foundry-looking session ID directly in a header, query string, or request body, the BFF ignores it. Foundry `agent_session_id` values are only read from the BFF-owned server-side store.

## Foundry isolation strategy

Foundry isolation should be defense in depth, not the only authorization control.

### Primary path: Responses protocol v2 delegated user identity

The primary implementation is:

1. Use the existing `responses` protocol `2.0.0` declaration in `src/portfolio-agent/agent.yaml` as the normal BFF-to-agent invocation path.
2. Grant the BFF managed identity the Foundry permission required to pass delegated end-user identity:

   ```text
   Microsoft.CognitiveServices/accounts/AIServices/agents/endpoints/UserIdentityImpersonation/action
   ```

3. Derive `x-ms-user-identity` from validated External ID claims.
4. Send that value on session create/get/stop/delete, Responses v2 invoke, and file operations.

The value should be stable for the same user and tenant, but not reveal personal data. Recommended shape:

```text
tenant-{normalizedTenantId}-user-{sha256(issuer|subjectOrOid)}
```

The implementation must verify this final value satisfies Foundry's character and length constraints for `x-ms-user-identity`.

### Azure-held rollback configuration: Invocations protocol 1.0 legacy isolation headers

The current `src/portfolio-agent/agent.yaml` does not declare Invocations protocol `1.0.0`, and the frontend API runtime no longer includes an Invocations request path. Azure still retains the Invocations endpoint setting with `PortfolioAgent__UseInvocations=false` so the deployment shape can support a future explicit rollback. To use it for rollback, first restore the frontend API Invocations code, re-add the Invocations protocol declaration, and opt in through explicit rollback configuration.

Important constraints for this fallback:

- The exact headers must match the deployed Foundry endpoint's configured isolation scheme.
- Header values must still be derived by the BFF from validated External ID tenant/user claims.
- The BFF must send the same isolation context on session create/get/stop/delete, invoke, file operations, and log/session diagnostics.
- This Azure-held support must stay disabled by default (`PortfolioAgent__UseInvocations=false`) because protocol `1.0.0` is not the target posture.

Even in fallback mode, the BFF-owned server-side binding remains required. Legacy isolation headers partition Foundry state, but they do not replace application-level authorization.

## BFF session-binding store

The BFF needs a durable store for application handles and Foundry session IDs. A deployed multi-replica BFF cannot rely on process memory.

Recommended deployed store: Cosmos DB with managed identity, private endpoint, `disableLocalAuth: true`, and TTL.

Suggested record shape:

| Field | Purpose |
| --- | --- |
| `id` | Opaque application session handle or hash of it. |
| `tenantId` | Validated `extension_tenantId` at session creation. |
| `userId` | Stable validated External ID user identifier. |
| `foundryConversationId` | Foundry Responses conversation ID used for platform-managed message history. |
| `foundryAgentSessionId` | Foundry `agent_session_id`, encrypted if a suitable app-level encryption pattern is adopted. |
| `foundryOwnerIdentity` | Delegated user identity or legacy isolation key derived server-side. |
| `agentName` | Hosted agent name, for example `portfolio-agent`. |
| `protocolMode` | `responses-2.0-delegated` for the primary path, or `invocations-1.0-rollback` only during temporary rollback. |
| `status` | `active`, `stopped`, `deleted`, `expired`, or `replaced`. |
| `createdAt` | Creation timestamp. |
| `lastUsedAt` | Last successful resume/invoke. |
| `expiresAt` / `ttl` | Cleanup boundary aligned to Foundry session lifetime. |
| `correlationId` | Last correlation ID or creation correlation ID for troubleshooting. |

Do not store:

- External ID access tokens.
- BFF service tokens.
- Foundry access tokens.
- Refresh tokens.
- Raw prompts.
- Full agent responses.
- Full sensitive claim payloads.

## BFF API behavior

The public chat API shape remains stable:

```json
{
  "message": "Which portfolios are available?",
  "conversationId": "<opaque application conversation handle>"
}
```

The contract and UI documentation define the field with these semantics:

- This field is an opaque BFF-issued session handle for hosted-session resume.
- It is not a raw Foundry conversation ID or `agent_session_id`.
- It is not a guarantee of conversation history.
- It is scoped to the validated token tenant and user.

A future cleanup can rename it to `sessionHandle` or `agentSessionHandle`. If that change is made, `conversationId` can remain temporarily as a compatibility alias.

The implemented cleanup route is:

```http
DELETE /api/tenants/{tenantId}/agent/sessions/{sessionHandle}
Authorization: <External ID bearer token>
X-Correlation-ID: <optional-correlation-id>
```

It returns 204 when the validated tenant/user owns the binding and cleanup succeeds or the underlying Foundry session is already gone. It returns 404 for missing, malformed, expired, or non-owned handles so callers cannot enumerate another user's sessions. It returns 401/403 for failed token validation, route-tenant mismatch, inactive tenant, or missing authorization.

## Lifecycle behavior

### Create

Create a Foundry hosted session when the user starts a new agent interaction and no active binding exists.

### Get/resume

Before reuse, the BFF can call Foundry `GET session` to confirm status. If Foundry returns not found, expired, or inaccessible:

- do not silently bind to another user's session;
- either create a new session for the same validated user/tenant and mark the old binding replaced, or return a clear recoverable error;
- log the decision with correlation ID.

### Stop

Use stop when compute should be terminated while preserving persistent filesystem state for later resume.

### Delete

Use delete when the user explicitly ends the agent session or cleanup policy expires the binding. Deleting a Foundry session removes the sandbox and stored files for that session.

### File operations

If the application supports file upload/download to the hosted-agent sandbox:

- the SPA uploads/downloads through the BFF, not Foundry directly;
- the BFF validates External ID tenant/user ownership before every file operation;
- the BFF uses the stored Foundry conversation ID and `agent_session_id`;
- file names, sizes, and content types must be validated;
- sensitive file content must not be logged.

## Security controls

1. Treat `extension_tenantId` from the validated External ID token as the only business-tenant authority.
2. Treat stable user ID from the validated token as the only session-user authority.
3. Never accept raw Foundry conversation IDs or `agent_session_id` values from the SPA.
4. Never expose raw Foundry conversation IDs or `agent_session_id` values in public API responses.
5. Scope session lookup by application handle plus validated tenant plus validated user.
6. Derive Foundry delegated identity or isolation headers server-side only.
7. Grant Foundry delegation/cross-user session permissions only to the BFF managed identity if required.
8. Return 403 or 404 for ownership mismatch without revealing whether the handle exists for another user.
9. Do not log JWTs, service tokens, Foundry tokens, or full sensitive claims.
10. Keep APIM header sanitization so callers cannot spoof trusted identity/session headers.

## Observability

Add structured events for:

- `foundry-session-create`
- `foundry-session-resume`
- `foundry-session-owner-mismatch`
- `foundry-session-get-failed`
- `foundry-session-invoke`
- `foundry-session-stop`
- `foundry-session-delete`
- `foundry-session-expired`
- `foundry-session-replaced`

Each event should include:

- `tenantId`
- `userId` or hashed user key
- `correlationId`
- operation name
- result or authorization decision
- HTTP status code
- hashed or truncated application session handle
- hashed or truncated Foundry session ID if needed for troubleshooting

Do not log raw tokens, full claim payloads, full prompts, raw session files, or full Foundry session IDs unless a specific secure operational need is approved.

## Validation plan

### Unit or component tests

Add focused tests for:

- new chat creates a server-side Foundry session binding;
- same validated user and tenant can resume an existing binding;
- different user with stolen handle cannot resume;
- same user with mismatched route tenant cannot resume;
- client-provided Foundry-looking session ID is ignored;
- Responses v2 body includes `conversation` and `agent_session_id` only from the server-side store;
- Invocations rollback is disabled by default and can only be enabled through explicit rollback configuration;
- delegated identity or legacy isolation headers are sent consistently;
- expired or missing Foundry session does not silently cross-bind.

### Live validation

Foundry session isolation must be validated against a deployed hosted agent because local runs do not enforce all platform isolation behavior.

Recommended live checks:

1. Sign in as AlphaCapital user A and start a new agent session.
2. Capture the application handle returned by the BFF.
3. Continue as user A and confirm the same Foundry session is reused server-side.
4. Sign in as AlphaCapital user B and try to reuse user A's handle. Expect 403 or 404 and no Foundry invoke with user A's `agent_session_id`.
5. Sign in as BetaWealth user and try to reuse the AlphaCapital handle. Expect 403 or 404.
6. Attempt to submit a raw `agent_session_id` in request body/header/query. Confirm it is ignored.
7. If protocol 2.0 delegated identity is enabled, confirm Foundry lists or gets sessions only under the expected delegated user identity.
8. If legacy isolation headers are used, confirm session/file operations fail when the isolation headers differ or are omitted.

## Implementation status

Implemented:

1. Responses protocol v2 is the primary hosted-session path.
2. Invocations protocol 1.0 is absent from the hosted-agent declaration unless an explicit rollback deployment re-enables it; retained code is disabled by default.
3. The BFF has a Foundry hosted-session client and session-binding store.
4. The BFF derives owner identity from validated External ID claims.
5. The BFF chat route creates/resumes bindings with validated tenant/user ownership checks.
6. Responses v2 calls include `agent_session_id` only from the server-side binding store.
7. The BFF exposes `DELETE /api/tenants/{tenantId}/agent/sessions/{sessionHandle}` for owned cleanup.

Remaining work is operational validation and any future public-field rename from `conversationId` to `sessionHandle`.

## Open decisions

| Decision | Recommended answer |
| --- | --- |
| Should the SPA ever see Foundry `agent_session_id`? | No. |
| Should the BFF store session bindings durably? | Yes, Cosmos-backed for deployed environments. |
| Should in-memory binding be allowed? | Only for local development or tests. |
| Should Responses v2 be the primary protocol? | Yes. |
| Should Invocations 1.0 be default? | No, disabled by default and rollback-only until removed. |
| Should legacy isolation headers be used long term? | No, only as a temporary rollback fallback if still required during migration. |
| Should this implement conversation history? | No, separate design. |
