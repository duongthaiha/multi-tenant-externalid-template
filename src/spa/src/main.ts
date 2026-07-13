import {
  AccountInfo,
  AuthenticationResult,
  Configuration,
  InteractionRequiredAuthError,
  PublicClientApplication,
  SilentRequest
} from "@azure/msal-browser";
import "./styles.css";

type RuntimeConfig = {
  auth: {
    authority: string;
    clientId: string;
    redirectUri?: string;
    postLogoutRedirectUri?: string;
    knownAuthorities?: string[];
    identityRouting?: {
      providers: IdentityProviderRoute[];
    };
  };
  api: {
    apimBaseUrl: string;
    pathPrefix?: string;
    scopes: string[];
    crossTenantDemoTenant?: string;
  };
  demoTenants?: string[];
};

type IdentityProviderRoute = {
  key: string;
  displayName: string;
  domains: string[];
  domainHint: string;
  enabled: boolean;
};

type IdentityRoute =
  | { kind: "workforce-enabled"; provider: IdentityProviderRoute }
  | { kind: "workforce-disabled"; provider: IdentityProviderRoute }
  | { kind: "external-id" };

type PortfolioSummary = {
  tenantId: string;
  portfolios: Array<{
    id: string;
    name: string;
    currency: string;
    marketValue: number;
    asOfDate: string;
  }>;
};

type ApiResult = {
  status: number;
  ok: boolean;
  correlationId: string | null;
  authorizationDecision: string | null;
  body: unknown;
};

type ApiMethod = "GET" | "POST" | "DELETE";

type AgentChatResponse = {
  tenantId: string;
  answer: string;
  correlationId: string;
  // BFF compatibility field; the value is an opaque conversation handle.
  conversationId?: string | null;
  toolResults?: Array<{
    toolName: string;
    result: string;
  }>;
};

type AgentChatTurn = {
  question: string;
  response: AgentChatResponse;
};

type AppState = {
  accountId: string;
  accountName: string;
  claims: Record<string, unknown> | null;
  portfolioSummary: PortfolioSummary | null;
  agentQuestion: string;
  agentPending: boolean;
  agentSessionHandle: string;
  agentTurns: AgentChatTurn[];
  lastApiResult: ApiResult | null;
  statusMessage: string;
  statusKind: "info" | "success" | "error";
  discoveryOpen: boolean;
  discoveryEmail: string;
  discoveryMessage: string;
  busy: boolean;
};

const app = document.querySelector<HTMLDivElement>("#app");

if (!app) {
  throw new Error("Missing #app root element.");
}

const appRoot = app;

let config: RuntimeConfig;
let msal: PublicClientApplication;
let state: AppState;

void bootstrap().catch(showBootstrapError);

async function bootstrap(): Promise<void> {
  config = await loadConfig();
  msal = new PublicClientApplication(toMsalConfig(config));
  await msal.initialize();

  state = {
    accountId: "",
    accountName: "",
    claims: null,
    portfolioSummary: null,
    agentQuestion: "Which portfolios are available for my tenant?",
    agentPending: false,
    agentSessionHandle: "",
    agentTurns: [],
    lastApiResult: null,
    statusMessage: "Ready. Sign in to request an access token and call the frontend API through APIM.",
    statusKind: "info",
    discoveryOpen: false,
    discoveryEmail: "",
    discoveryMessage: "",
    busy: false
  };

  await hydrateSignedInAccount();
  render();
}

async function loadConfig(): Promise<RuntimeConfig> {
  const response = await fetch("/config.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Unable to load /config.json.");
  }

  const loaded = (await response.json()) as RuntimeConfig;
  const providers = normalizeIdentityProviders(loaded.auth.identityRouting?.providers ?? []);
  return {
    ...loaded,
    auth: {
      ...loaded.auth,
      redirectUri: loaded.auth.redirectUri || window.location.origin,
      postLogoutRedirectUri: loaded.auth.postLogoutRedirectUri || window.location.origin,
      identityRouting: { providers }
    },
    api: {
      ...loaded.api,
      pathPrefix: loaded.api.pathPrefix ?? "/api"
    }
  };
}

function normalizeIdentityProviders(providers: IdentityProviderRoute[]): IdentityProviderRoute[] {
  if (!Array.isArray(providers)) {
    throw new Error("auth.identityRouting.providers must be an array.");
  }

  const seenDomains = new Set<string>();
  const seenKeys = new Set<string>();
  return providers.map((provider, index) => {
    const context = `auth.identityRouting.providers[${index}]`;
    const key = requireConfigText(provider?.key, `${context}.key`);
    const displayName = requireConfigText(provider?.displayName, `${context}.displayName`);
    if (seenKeys.has(key)) {
      throw new Error(`Identity routing provider key "${key}" is configured more than once.`);
    }
    seenKeys.add(key);

    if (!Array.isArray(provider?.domains) || provider.domains.length === 0) {
      throw new Error(`${context}.domains must contain at least one domain.`);
    }
    if (typeof provider.enabled !== "boolean") {
      throw new Error(`${context}.enabled must be true or false.`);
    }
    const domains = provider.domains.map((domain, domainIndex) => {
      const normalized = normalizeDomain(domain, `${context}.domains[${domainIndex}]`);
      if (seenDomains.has(normalized)) {
        throw new Error(`Identity routing domain "${normalized}" is configured more than once.`);
      }
      seenDomains.add(normalized);
      return normalized;
    });
    const domainHint = provider.enabled
      ? normalizeDomain(provider.domainHint, `${context}.domainHint`)
      : typeof provider.domainHint === "string" && provider.domainHint.trim()
        ? normalizeDomain(provider.domainHint, `${context}.domainHint`)
        : "";

    return { key, displayName, domains, domainHint, enabled: provider.enabled === true };
  });
}

function requireConfigText(value: unknown, fieldName: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${fieldName} must be a nonempty string.`);
  }
  return value.trim();
}

function normalizeDomain(value: unknown, fieldName: string): string {
  const domain = requireConfigText(value, fieldName).toLowerCase();
  if (
    domain.length > 253 ||
    !domain.includes(".") ||
    domain.split(".").some((label) => !label || label.length > 63 || !/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label))
  ) {
    throw new Error(`${fieldName} must be a valid DNS domain.`);
  }
  return domain;
}

function showBootstrapError(error: unknown): void {
  const message = error instanceof Error ? error.message : "Unexpected startup error.";
  appRoot.innerHTML = `
    <main class="app-shell">
      <section class="hero">
        <div>
          <h1>Contoso Asset Management</h1>
          <p class="status error" role="alert">Unable to start the application: ${escapeHtml(message)}</p>
        </div>
      </section>
    </main>`;
}

function toMsalConfig(runtime: RuntimeConfig): Configuration {
  return {
    auth: {
      authority: runtime.auth.authority,
      clientId: runtime.auth.clientId,
      redirectUri: runtime.auth.redirectUri,
      postLogoutRedirectUri: runtime.auth.postLogoutRedirectUri,
      knownAuthorities: runtime.auth.knownAuthorities ?? [new URL(runtime.auth.authority).host]
    },
    cache: {
      cacheLocation: "sessionStorage",
      storeAuthStateInCookie: false
    },
    system: {
      loggerOptions: {
        piiLoggingEnabled: false
      }
    }
  };
}

async function hydrateSignedInAccount(): Promise<void> {
  const redirectResult = await msal.handleRedirectPromise();
  const account = redirectResult?.account ?? msal.getAllAccounts()[0];
  if (!account) {
    return;
  }

  msal.setActiveAccount(account);
  state.accountId = accountKey(account);
  state.accountName = account.username || account.name || account.homeAccountId;

  if (redirectResult?.accessToken) {
    state.claims = decodeJwtPayload(redirectResult.accessToken);
    return;
  }

  // Popup sign-in never populates handleRedirectPromise's result, so on a
  // plain page reload we must silently reacquire a token from the cached
  // account to restore tenant claims (extension_tenantId, roles, status).
  try {
    const result = await msal.acquireTokenSilent({ account, scopes: config.api.scopes });
    state.claims = decodeJwtPayload(result.accessToken);
  } catch (error) {
    if (!(error instanceof InteractionRequiredAuthError)) {
      throw error;
    }
    // Leave claims null; the user can re-authenticate via the sign-in/refresh actions.
  }
}

function render(): void {
  const tenantId = getTenantId();
  const roles = getClaimValues("tenant_roles");
  const tenantStatus = getClaimValue("tenant_status");
  const signedIn = Boolean(msal.getActiveAccount());
  const configLooksReady = !hasPlaceholder(config.auth.authority) && !hasPlaceholder(config.auth.clientId) && !hasPlaceholder(config.api.apimBaseUrl);
  const displayName = state.accountName || "Signed-in user";

  appRoot.innerHTML = `
    <main class="app-shell">
      <section class="hero">
        <div>
          <h1>Contoso Asset Management</h1>
          <p>Multi-tenant SPA demo using Azure External ID, MSAL, APIM, and the frontend API/BFF.</p>
          ${signedIn ? `<p class="signed-in-user">Signed in as <strong>${escapeHtml(displayName)}</strong></p>` : ""}
          ${configLooksReady ? "" : `<p class="status error">Update <code>/config.json</code> with External ID, API scope, and APIM values before signing in.</p>`}
        </div>
        <div class="actions hero-actions">
          ${signedIn
            ? `<button id="sign-out" class="secondary" ${state.busy ? "disabled" : ""}>Sign out</button>`
            : `<button id="sign-in" ${state.busy || !configLooksReady ? "disabled" : ""}>Sign in</button>`}
        </div>
        ${!signedIn && state.discoveryOpen ? renderIdentityDiscovery() : ""}
      </section>

      <section class="grid">
        <article class="card">
          <h2>Decoded access-token claims</h2>
          <p class="muted">Shown for demo clarity only. Tokens are not logged or persisted by the app.</p>
          <div class="claims">
            <div class="claim"><strong>Account</strong><span>${escapeHtml(state.accountName || "Not signed in")}</span></div>
            <div class="claim"><strong>extension_tenantId</strong><span>${escapeHtml(tenantId || "—")}</span></div>
            <div class="claim"><strong>tenant_status</strong><span>${escapeHtml(String(tenantStatus ?? "—"))}</span></div>
            <div class="claim"><strong>tenant_roles</strong><span>${escapeHtml(roles.length ? roles.join(", ") : "—")}</span></div>
          </div>
          <div class="actions" style="margin-top: 1rem;">
            <button id="refresh-token" class="secondary" ${!signedIn || state.busy ? "disabled" : ""}>Acquire token / refresh claims</button>
          </div>
          <pre>${escapeHtml(JSON.stringify(pickDemoClaims(state.claims), null, 2))}</pre>
        </article>

        <article class="card">
          <h2>Portfolio list</h2>
          <p class="muted">Calls <code>${escapeHtml(routeFor(":tenantId", "/portfolios"))}</code> with the signed-in user's bearer token.</p>
          <button id="load-portfolios" ${!signedIn || !tenantId || state.busy ? "disabled" : ""}>Load portfolios</button>
          ${renderPortfolioTable()}
        </article>

        <article class="card">
          <h2>Cross-tenant 403 demo</h2>
          <p class="muted">
            This intentionally calls a mismatched route tenant so APIM/frontend authorization rejects it.
            No tenant authority is sent in headers, query strings, or request bodies.
          </p>
          <button id="cross-tenant" class="danger" ${!signedIn || !tenantId || state.busy ? "disabled" : ""}>Call mismatched tenant route</button>
        </article>

        <article class="card chat-card" id="chat-card">
          <header class="chat-header">
            <div>
              <h2>Portfolio agent chat</h2>
              <p class="muted">
                Calls <code>${escapeHtml(routeFor(":tenantId", "/agent/chat"))}</code> through APIM and the BFF.
                The agent uses APIM MCP tools for backend data access.
              </p>
            </div>
            <div class="chat-header-actions">
              ${state.agentSessionHandle ? `<span class="chat-session-badge">Session active</span>` : ""}
              <button id="reset-agent-chat" class="secondary" ${state.busy || (!state.agentSessionHandle && state.agentTurns.length === 0) ? "disabled" : ""}>New conversation</button>
            </div>
          </header>
          <section id="chat-transcript" class="chat-transcript" role="log" aria-live="polite" aria-label="Agent conversation">
            ${renderAgentTranscript()}
          </section>
          <div id="chat-typing" class="chat-typing" aria-label="Agent is thinking" ${state.agentPending ? "" : "hidden"}>
            <span></span><span></span><span></span>
          </div>
          <form id="chat-form" class="chat-composer" novalidate>
            <label for="agent-question" class="sr-only">Ask a tenant-scoped portfolio question</label>
            <textarea id="agent-question" class="chat-input" rows="3" placeholder="Ask about portfolios, positions, or holdings">${escapeHtml(state.agentQuestion)}</textarea>
            <button id="ask-agent" class="chat-send-btn" type="submit" ${!signedIn || !tenantId || state.busy ? "disabled" : ""}>Send</button>
          </form>
        </article>

        <article class="card">
          <h2>Last API result</h2>
          <p class="status ${state.statusKind === "error" ? "error" : state.statusKind === "success" ? "success" : ""}" role="status">
            ${escapeHtml(state.statusMessage)}
          </p>
          ${state.lastApiResult ? `<pre>${escapeHtml(JSON.stringify(state.lastApiResult, null, 2))}</pre>` : ""}
        </article>
      </section>
    </main>
  `;

  wireEvents();
  scrollChatToBottom();
}

function wireEvents(): void {
  document.querySelector("#sign-in")?.addEventListener("click", openIdentityDiscovery);
  document.querySelector<HTMLFormElement>("#identity-discovery-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!state.busy) {
      void run(signInWithEmail);
    }
  });
  document.querySelector<HTMLInputElement>("#sign-in-email")?.addEventListener("input", (event) => {
    state.discoveryEmail = (event.target as HTMLInputElement).value;
    state.discoveryMessage = "";
  });
  document.querySelector("#cancel-identity-discovery")?.addEventListener("click", closeIdentityDiscovery);
  document.querySelector("#choose-sign-in-method")?.addEventListener("click", () => run(signInWithProviderPicker));
  document.querySelector("#sign-out")?.addEventListener("click", () => run(signOut));
  document.querySelector("#refresh-token")?.addEventListener("click", () => run(refreshClaims));
  document.querySelector("#load-portfolios")?.addEventListener("click", () => run(loadPortfolios));
  document.querySelector("#cross-tenant")?.addEventListener("click", () => run(callCrossTenantRoute));
  document.querySelector<HTMLFormElement>("#chat-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    if (state.busy) {
      return;
    }

    void run(askPortfolioAgent);
  });
  document.querySelector("#reset-agent-chat")?.addEventListener("click", () => run(resetAgentChat));

  document.querySelector<HTMLTextAreaElement>("#agent-question")?.addEventListener("input", (event) => {
    state.agentQuestion = (event.target as HTMLTextAreaElement).value;
  });
  document.querySelector<HTMLTextAreaElement>("#agent-question")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      if (state.busy) {
        return;
      }

      void run(askPortfolioAgent);
    }
  });
}

function openIdentityDiscovery(): void {
  state.discoveryOpen = true;
  state.discoveryMessage = "";
  render();
  document.querySelector<HTMLInputElement>("#sign-in-email")?.focus();
}

function closeIdentityDiscovery(): void {
  state.discoveryOpen = false;
  state.discoveryEmail = "";
  state.discoveryMessage = "";
  render();
}

function renderIdentityDiscovery(): string {
  return `
    <form id="identity-discovery-form" class="identity-discovery" novalidate>
      <div>
        <label for="sign-in-email">Work or account email</label>
        <p id="sign-in-help" class="muted identity-discovery-help">We use the domain only to select the correct sign-in method.</p>
      </div>
      <input
        id="sign-in-email"
        name="email"
        type="email"
        autocomplete="username"
        inputmode="email"
        value="${escapeHtml(state.discoveryEmail)}"
        aria-describedby="sign-in-help${state.discoveryMessage ? " sign-in-message" : ""}"
        ${state.discoveryMessage ? `aria-invalid="true"` : ""}
        ${state.busy ? "disabled" : ""}
        required
      />
      ${state.discoveryMessage ? `<p id="sign-in-message" class="field-message" role="alert">${escapeHtml(state.discoveryMessage)}</p>` : ""}
      <div class="actions">
        <button type="submit" ${state.busy ? "disabled" : ""}>Continue</button>
        <button id="cancel-identity-discovery" type="button" class="secondary" ${state.busy ? "disabled" : ""}>Cancel</button>
        <button id="choose-sign-in-method" type="button" class="link-button" ${state.busy ? "disabled" : ""}>Choose another sign-in method</button>
      </div>
    </form>`;
}

async function run(action: () => Promise<void>): Promise<void> {
  state.busy = true;
  render();
  try {
    await action();
  } catch (error) {
    state.statusKind = "error";
    state.statusMessage = error instanceof Error ? error.message : "Unexpected error.";
  } finally {
    state.busy = false;
    render();
  }
}

async function signInWithEmail(): Promise<void> {
  const email = normalizeEmail(state.discoveryEmail);
  if (!email) {
    state.discoveryMessage = "Enter a valid email address.";
    return;
  }

  const route = routeIdentity(email);
  if (route.kind === "workforce-disabled") {
    state.discoveryMessage = `${route.provider.displayName} sign-in is not configured. Contact your administrator.`;
    return;
  }

  const result = await msal.loginPopup({
    scopes: config.api.scopes,
    loginHint: email,
    ...(route.kind === "workforce-enabled" ? { domainHint: route.provider.domainHint } : {})
  });
  completeSignIn(result);
}

async function signInWithProviderPicker(): Promise<void> {
  const result = await msal.loginPopup({ scopes: config.api.scopes });
  completeSignIn(result);
}

function completeSignIn(result: AuthenticationResult): void {
  msal.setActiveAccount(result.account);
  updateSessionIdentity(result.account, result.accessToken ? decodeJwtPayload(result.accessToken) : null);
  state.accountName = result.account.username || result.account.name || result.account.homeAccountId;
  state.discoveryOpen = false;
  state.discoveryEmail = "";
  state.discoveryMessage = "";
  state.statusKind = "success";
  state.statusMessage = "Signed in and acquired an API access token.";
}

function normalizeEmail(value: string): string | null {
  const email = value.trim();
  const parts = email.split("@");
  if (parts.length !== 2 || !parts[0] || /\s/.test(parts[0])) {
    return null;
  }
  try {
    normalizeDomain(parts[1], "email domain");
  } catch {
    return null;
  }
  return `${parts[0]}@${parts[1].toLowerCase()}`;
}

function routeIdentity(email: string): IdentityRoute {
  const domain = email.slice(email.lastIndexOf("@") + 1).toLowerCase();
  const provider = config.auth.identityRouting?.providers.find((entry) => entry.domains.includes(domain));
  if (!provider) {
    return { kind: "external-id" };
  }
  return provider.enabled
    ? { kind: "workforce-enabled", provider }
    : { kind: "workforce-disabled", provider };
}

async function signOut(): Promise<void> {
  const account = msal.getActiveAccount();
  state.claims = null;
  state.portfolioSummary = null;
  state.lastApiResult = null;
  clearAgentSession();
  state.accountId = "";
  state.accountName = "";
  await msal.logoutPopup({ account: account ?? undefined, postLogoutRedirectUri: config.auth.postLogoutRedirectUri });
  state.statusKind = "info";
  state.statusMessage = "Signed out.";
}

async function refreshClaims(): Promise<void> {
  await acquireAccessToken();
  state.statusKind = "success";
  state.statusMessage = "Access token acquired and demo claims refreshed.";
}

async function loadPortfolios(): Promise<void> {
  const tenantId = requireTenantId();
  const result = await callApi(routeFor(tenantId, "/portfolios"), "GET");
  state.lastApiResult = result;
  if (result.ok) {
    state.portfolioSummary = result.body as PortfolioSummary;
    state.statusKind = "success";
    state.statusMessage = `Loaded ${state.portfolioSummary.portfolios.length} portfolio(s) for ${tenantId}.`;
  } else {
    showApiFailure(result);
  }
}

async function callCrossTenantRoute(): Promise<void> {
  const tokenTenant = requireTenantId();
  const mismatchedTenant = getMismatchedTenant(tokenTenant);
  const result = await callApi(routeFor(mismatchedTenant, "/portfolios"), "GET");
  state.lastApiResult = result;
  if (result.status === 403) {
    state.statusKind = "success";
    state.statusMessage = `Visible cross-tenant rejection: ${tokenTenant} token called ${mismatchedTenant} route and received HTTP 403.`;
  } else {
    showApiFailure(result);
  }
}

async function askPortfolioAgent(): Promise<void> {
  const tenantId = requireTenantId();
  const message = state.agentQuestion.trim();
  if (!message) {
    throw new Error("Enter a portfolio-agent question.");
  }

  state.agentPending = true;
  render();
  let result: ApiResult;
  try {
    result = await callApiJson(
      routeFor(tenantId, "/agent/chat"),
      "POST",
      {
        message,
        // Keep the wire field name for BFF compatibility; do not treat the value as a Foundry ID.
        conversationId: state.agentSessionHandle || undefined
      });
  } finally {
    state.agentPending = false;
  }
  state.lastApiResult = result;
  if (result.ok) {
    const response = result.body as AgentChatResponse;
    state.agentTurns.push({ question: message, response });
    state.agentSessionHandle = response.conversationId ?? state.agentSessionHandle;
    state.agentQuestion = "";
    state.statusKind = "success";
    state.statusMessage = state.agentTurns.length > 1
      ? "Portfolio agent answered using the existing conversation context."
      : "Portfolio agent answered using tenant-scoped backend data.";
  } else {
    showApiFailure(result);
  }
}

async function resetAgentChat(): Promise<void> {
  const sessionHandle = state.agentSessionHandle;
  if (sessionHandle) {
    const tenantId = requireTenantId();
    const result = await callApiJson(routeFor(tenantId, `/agent/sessions/${encodeURIComponent(sessionHandle)}`), "DELETE");
    state.lastApiResult = result;
    if (!result.ok) {
      showApiFailure(result);
      return;
    }
  }

  clearAgentSession();
  state.statusKind = "info";
  state.statusMessage = "Started a new portfolio-agent conversation.";
}

async function acquireAccessToken(): Promise<string> {
  const account = msal.getActiveAccount() ?? msal.getAllAccounts()[0];
  if (!account) {
    throw new Error("Sign in before acquiring an access token.");
  }

  const request: SilentRequest = { account, scopes: config.api.scopes };
  try {
    const result = await msal.acquireTokenSilent(request);
    updateAccountFromResult(result);
    return result.accessToken;
  } catch (error) {
    if (!(error instanceof InteractionRequiredAuthError)) {
      throw error;
    }

    const result = await msal.acquireTokenPopup({ account, scopes: config.api.scopes });
    updateAccountFromResult(result);
    return result.accessToken;
  }
}

async function callApi(path: string, method: Extract<ApiMethod, "GET" | "POST">): Promise<ApiResult> {
  return callApiJson(path, method);
}

async function callApiJson(path: string, method: ApiMethod, requestBody?: unknown): Promise<ApiResult> {
  const accessToken = await acquireAccessToken();
  const response = await fetch(path, {
    method,
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "X-Correlation-ID": crypto.randomUUID(),
      ...(requestBody ? { "Content-Type": "application/json" } : {})
    },
    body: requestBody ? JSON.stringify(requestBody) : undefined
  });

  const contentType = response.headers.get("content-type") ?? "";
  const responseBody = contentType.includes("application/json") ? await response.json() : await response.text();
  return {
    status: response.status,
    ok: response.ok,
    correlationId: response.headers.get("X-Correlation-ID"),
    authorizationDecision: response.headers.get("x-authorization-decision"),
    body: redactHostedAgentInternals(responseBody)
  };
}

function updateAccountFromResult(result: AuthenticationResult): void {
  const claims = decodeJwtPayload(result.accessToken);
  updateSessionIdentity(result.account, claims);
}

function updateSessionIdentity(account: AccountInfo | null, claims: Record<string, unknown> | null): void {
  const previousAccountId = state.accountId;
  const previousTenantId = getTenantIdFromClaims(state.claims);
  const previousUserId = getUserIdFromClaims(state.claims);
  const nextAccountId = account ? accountKey(account) : "";
  const nextTenantId = getTenantIdFromClaims(claims);
  const nextUserId = getUserIdFromClaims(claims);

  if (
    (previousAccountId && previousAccountId !== nextAccountId) ||
    (previousTenantId && previousTenantId !== nextTenantId) ||
    (previousUserId && previousUserId !== nextUserId)
  ) {
    clearAgentSession();
  }

  if (account) {
    msal.setActiveAccount(account);
    state.accountId = nextAccountId;
    state.accountName = account.username || account.name || account.homeAccountId;
  }

  state.claims = claims;
}

function clearAgentSession(): void {
  state.agentPending = false;
  state.agentSessionHandle = "";
  state.agentTurns = [];
}

function accountKey(account: AccountInfo): string {
  return account.homeAccountId || account.localAccountId || account.username;
}

function routeFor(tenantId: string, route: string): string {
  const baseUrl = trimTrailingSlash(config.api.apimBaseUrl);
  const pathPrefix = `/${trimSlashes(config.api.pathPrefix ?? "/api")}`;
  return `${baseUrl}${pathPrefix}/tenants/${encodeURIComponent(tenantId)}${route}`;
}

function getTenantId(): string {
  return getTenantIdFromClaims(state.claims);
}

function requireTenantId(): string {
  const tenantId = getTenantId();
  if (!tenantId) {
    throw new Error("The access token does not contain extension_tenantId.");
  }

  return tenantId;
}

function getClaimValue(name: string): unknown {
  return getClaimValueFromClaims(state.claims, name);
}

function getClaimValueFromClaims(claims: Record<string, unknown> | null, name: string): unknown {
  if (!claims) {
    return undefined;
  }

  return claims[name] ?? Object.entries(claims).find(([key]) => key.endsWith(`/${name}`) || key.endsWith(`_${name}`))?.[1];
}

function getTenantIdFromClaims(claims: Record<string, unknown> | null): string {
  return String(getClaimValueFromClaims(claims, "extension_tenantId") ?? "");
}

function getUserIdFromClaims(claims: Record<string, unknown> | null): string {
  return String(getClaimValueFromClaims(claims, "oid") ?? getClaimValueFromClaims(claims, "sub") ?? getClaimValueFromClaims(claims, "preferred_username") ?? "");
}

function getClaimValues(name: string): string[] {
  const value = getClaimValue(name);
  if (Array.isArray(value)) {
    return value.map(String);
  }

  return typeof value === "string" && value.length > 0 ? value.split(/[,\s]+/).filter(Boolean) : [];
}

function getMismatchedTenant(tokenTenant: string): string {
  const configured = config.api.crossTenantDemoTenant;
  if (configured && configured !== tokenTenant) {
    return configured;
  }

  return config.demoTenants?.find((tenant) => tenant !== tokenTenant) ?? (tokenTenant === "BetaWealth" ? "AlphaCapital" : "BetaWealth");
}

function decodeJwtPayload(token: string): Record<string, unknown> {
  const payload = token.split(".")[1];
  if (!payload) {
    return {};
  }

  const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");
  const json = decodeURIComponent(
    atob(padded)
      .split("")
      .map((char) => `%${char.charCodeAt(0).toString(16).padStart(2, "0")}`)
      .join("")
  );

  return JSON.parse(json) as Record<string, unknown>;
}

function pickDemoClaims(claims: Record<string, unknown> | null): Record<string, unknown> {
  if (!claims) {
    return {};
  }

  const keys = ["extension_tenantId", "tenant_status", "tenant_roles", "scp", "aud", "iss", "oid", "sub", "preferred_username", "name"];
  return Object.fromEntries(keys.filter((key) => claims[key] !== undefined).map((key) => [key, claims[key]]));
}

function redactHostedAgentInternals(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(redactHostedAgentInternals);
  }

  if (!value || typeof value !== "object") {
    return value;
  }

  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => key !== "agent_session_id")
      .map(([key, entry]) => [key, redactHostedAgentInternals(entry)])
  );
}

function renderPortfolioTable(): string {
  const portfolios = state.portfolioSummary?.portfolios ?? [];
  if (portfolios.length === 0) {
    return `<p class="muted">No portfolios loaded yet.</p>`;
  }

  const rows = portfolios
    .map(
      (portfolio) => `
        <tr>
          <td>${escapeHtml(portfolio.id)}</td>
          <td>${escapeHtml(portfolio.name)}</td>
          <td>${escapeHtml(portfolio.currency)}</td>
          <td>${formatMoney(portfolio.marketValue, portfolio.currency)}</td>
          <td>${escapeHtml(portfolio.asOfDate)}</td>
        </tr>`
    )
    .join("");

  return `
    <table>
      <thead>
        <tr><th>ID</th><th>Name</th><th>Currency</th><th>Market value</th><th>As of</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function showApiSuccess(result: ApiResult, message: string): void {
  state.statusKind = "success";
  state.statusMessage = `${message} HTTP ${result.status}. Correlation ID: ${result.correlationId ?? "not returned"}.`;
}

function showApiFailure(result: ApiResult): void {
  state.statusKind = "error";
  state.statusMessage = `API call failed with HTTP ${result.status}. Decision: ${result.authorizationDecision ?? "not returned"}.`;
}

function renderAgentTranscript(): string {
  if (state.agentTurns.length === 0) {
    return `
      <div class="chat-empty">
        <span class="chat-empty-icon" aria-hidden="true">Chat</span>
        <p class="chat-empty-title">No agent conversation yet.</p>
        <p class="muted">Ask about portfolios, positions, holdings, or approvals below.</p>
      </div>`;
  }

  return state.agentTurns.map((turn, index) => {
    const tools = turn.response.toolResults?.length
      ? `
        <details class="chat-tools">
          <summary>Tool activity (${turn.response.toolResults.length})</summary>
          <ul>${turn.response.toolResults.map((tool) => `<li>${escapeHtml(tool.toolName)}: ${escapeHtml(tool.result)}</li>`).join("")}</ul>
        </details>`
      : "";

    return `
      <section class="chat-turn" aria-label="Turn ${index + 1}">
        <div class="chat-bubble chat-bubble--user">
          <span class="chat-bubble-label">You</span>
          <p>${escapeHtml(turn.question)}</p>
        </div>
        <div class="chat-bubble chat-bubble--agent">
          <span class="chat-bubble-label">Agent</span>
          <p>${escapeHtml(turn.response.answer)}</p>
          <div class="turn-meta">
            <span>Turn ${index + 1}</span>
            <span>Tenant ${escapeHtml(turn.response.tenantId)}</span>
            <span>Correlation ${escapeHtml(turn.response.correlationId)}</span>
          </div>
          ${tools}
        </div>
      </section>`;
  }).join("");
}

function scrollChatToBottom(): void {
  const transcript = document.querySelector<HTMLElement>("#chat-transcript");
  if (transcript) {
    transcript.scrollTop = transcript.scrollHeight;
  }
}

function formatMoney(value: number, currency: string): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(value);
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function trimSlashes(value: string): string {
  return value.replace(/^\/+|\/+$/g, "");
}

function hasPlaceholder(value: string): boolean {
  return value.includes("<") || value.includes(">");
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => {
    const replacements: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    };
    return replacements[char];
  });
}
