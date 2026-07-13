# Contoso Portfolio Agent (Python)

This is a Microsoft Foundry **hosted agent** sample built with the Python Microsoft Agent
Framework -- a parallel implementation of `src/portfolio-agent` (C#) on the same Responses
protocol v2 contract, trusted-header/metadata context model, and Backend Asset MCP tool
boundary. It exists to prove the same Pattern 2 architecture (see
`docs/adr/0006-pattern-2-portfolio-agent.md`) is reproducible with the Python Agent Framework
hosting stack, not to replace the C# agent.

The agent does not access Cosmos DB directly for portfolio data. Tenant data access always goes
through the Contoso Backend API's MCP gateway (`BACKEND_MCP_SERVER_URL`, fronted by APIM). The
agent's own Cosmos DB access is limited to its own conversation-memory store (see
[Conversation memory](#conversation-memory)).

## What it demonstrates

- Hosted agent code under `src/portfolio-agent-python`, deployed as azd service
  `portfolio-agent-python` (`host: azure.ai.agent`, `language: python`).
- Responses protocol v2 endpoint via `azure-ai-agentserver-responses`'s
  `ResponsesAgentServerHost` and `@app.response_handler` -- the only protocol this profile
  implements (no Invocations 1.0 rollback path).
- Python tools for portfolio Q&A that call the Backend API through the APIM native MCP server,
  named to match the C# agent's registered tool names exactly (the two profiles share Foundry
  evaluation datasets that assert on these names):
  - `ListPortfolios`
  - `GetPortfolioSummary`
  - `GetPositionDetail`
- Trusted request context resolved the same way as the C# agent's Responses v2 path: Responses
  `metadata` (identity/correlation) combined with `x-client-*` forwarded headers (tokens), with
  metadata taking priority for tenant/user/correlation identity and headers taking priority for
  bearer tokens. Tokens are used only to call APIM MCP/backend and are never logged or persisted.
- A request-scoped `contextvars` context (`portfolio_agent.context`), the async equivalent of the
  C# agent's `AsyncLocal<T>` accessor. There is deliberately no process-global fallback: if
  request context does not propagate into a tool call, the tool fails rather than reusing another
  request's tenant or credentials.
- Fail-closed tool behavior: a tool invoked without a complete trusted context (tenant, user, user
  token, service token, correlation id) raises rather than answering with cross-tenant or
  unauthenticated data.
- Foundry-hosted-agent observability:
  - A dedicated tracer/meter (`Contoso.AssetManagement.PortfolioAgent`) with the same custom tool
    invocation/miss counters as the C# agent.
  - Safe (token-free) structured logs for startup, tool invocation, missing trusted context, MCP
    tool failures, Cosmos session-store failures, and **tenant-mismatch 403** responses (logged in
    the exact shape `infra/modules/monitoring.bicep`'s alert query expects: message text containing
    `"tenant-mismatch"` and structured `authorizationDecision`/`statusCode` properties).
  - Azure Monitor trace/log/metric export via `azure-ai-agentserver-core`'s
    `configure_observability` (the `microsoft-opentelemetry` distro), gated on
    `APPLICATIONINSIGHTS_CONNECTION_STRING`.
- APIM MCP mediation (identical boundary to the C# agent):
  - portfolio-agent-python connects to `BACKEND_MCP_SERVER_URL` with a fresh MCP client session
    per tool call, carrying `Authorization: Bearer <user token>`, `X-Correlation-ID`, and
    `X-Agent-Id: portfolio-agent-python`.
  - APIM validates the user token and tenant binding.
  - APIM forwards tool calls to the Backend API with APIM MCP gateway service proof.
  - Backend API revalidates the user token plus `Backend.Read` / `Backend.Write` service roles.

## Conversation memory

Conversation memory is a managed-identity Cosmos DB store
(`portfolio_agent.memory.CosmosAgentSessionStore`), one **database per tenant**, exactly like the
C# agent -- never a single shared/global database. It is a from-scratch Python adapter around
`agent_framework.AgentSession.to_dict()` / `from_dict()`, not the `azure-ai-agentserver-responses`
package's own response-envelope storage (`ResponseProviderProtocol`), which is a different
concern (Responses-envelope replay/history, not Agent Framework conversation state).

**Python-specific database prefix, no global fallback**: the default prefix is
`agent-memory-python-` (`CONTOSO_AGENT_MEMORY_DATABASE_PREFIX`, provisioned by
`infra/modules/cosmos-agent-memory.bicep`'s `pythonDatabasePrefix` output as
`AGENT_MEMORY_PYTHON_DATABASE_PREFIX`) -- a separate set of Cosmos databases from the C# agent's
`agent-memory-` prefix. `CONTOSO_AGENT_MEMORY_ENDPOINT` has no default and fails closed if unset.
Authenticated requests require complete trusted context before reading or writing memory.
Context-less Foundry-managed evaluation requests use an ephemeral session, while any attempted
tool call still fails closed.

## Required local configuration

Copy `.env.example` to `.env` for manual local runs, or set the values in your azd environment:

```bash
azd env set FOUNDRY_PROJECT_ENDPOINT "https://<account>.services.ai.azure.com/api/projects/<project>"
azd env set AZURE_AI_PROJECT_ID "/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>"
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "<model-deployment-name>"
azd env set BACKEND_MCP_SERVER_URL "https://<apim-gateway>/backend-assets-mcp/mcp"
azd env set AGENT_MEMORY_ENDPOINT "https://<agent-memory-cosmos>.documents.azure.com:443/"
azd env set PORTFOLIO_AGENT_PYTHON_PRINCIPAL_ID "<hosted-agent-managed-identity-principal-id>"
```

For local telemetry to Application Insights, also set `APPLICATIONINSIGHTS_CONNECTION_STRING`.
Hosted Foundry containers receive this automatically.

`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` is enabled for demo trace visibility.
`AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true` enables the Python Foundry client's model spans.
Disable it for production or sensitive workloads.

## Local run

```bash
cd src/portfolio-agent-python
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
python -m portfolio_agent.main
```

The host listens on `PORT` (default `8088`). Local invocation requires the same trusted context
the BFF sends in Azure (`x-client-x-authenticated-tenant`, `x-client-x-authenticated-user`,
`x-client-x-user-authorization`, `x-client-x-service-authorization`, `x-client-x-correlation-id`
forwarded headers, or the equivalent `contoso_*` Responses `metadata` keys) -- without it, tools
fail closed instead of returning cross-tenant sample data, and the MCP server requires a valid
user token, so local tool validation needs an External ID access token for the API audience.

## Tests

```bash
pip install -e ".[test]"
pytest
```

Tests are unit-level and mock the Foundry chat client, MCP client session, and Cosmos client so
they run offline. See [API stability notes](#api-stability-notes) for why the mocked boundaries
are drawn where they are.

## Deploy

The root `azure.yaml` includes a `portfolio-agent-python` service (`host: azure.ai.agent`,
`language: python`, `docker.path: ./Dockerfile` relative to the service project), and
`infra/main.bicep` already provisions the shared Foundry project/model deployment, App Insights
connection, ACR connection, and a second (Python-prefixed) set of per-tenant Cosmos databases in
the shared agent-memory Cosmos account.

```bash
azd provision
azd deploy portfolio-agent-python
```

The hosted identity does not exist until the first deployment. Deploy once, capture its principal
ID, set `PORTFOLIO_AGENT_PYTHON_PRINCIPAL_ID`, then run `azd provision` again to grant Cosmos data
access before validating durable memory.

## Evaluation

The Python target reuses the canonical datasets and evaluator registrations under
`src/portfolio-agent/` but keeps its suite definitions and result lineage under this directory:

```bash
python3 scripts/evaluate-portfolio-agent.py --agent python --dry-run smoke
python3 scripts/evaluate-portfolio-agent.py --agent python --direct smoke
python3 scripts/evaluate-portfolio-agent.py --agent python --direct tenant-safety
```

Managed agent-target cases run without tenant context and therefore use ephemeral memory. Trace
cases invoke Responses v2 directly with per-case trusted context. The custom Responses bridge
preserves Agent Framework function calls/results as `function_call` and `function_call_output`
items so the existing deterministic tool and tenant-isolation gates remain effective.

## API stability notes

This agent depends on several packages that are pre-1.0 or explicitly marked preview/beta at the
time of writing. Direct dependencies are pinned in `pyproject.toml`, the full production graph is
locked in `requirements.lock`, and the container installs that lock before the local package.
Regenerate it with `pip-compile pyproject.toml --output-file requirements.lock --strip-extras`
after an intentional dependency update. Tests fail loudly on API drift rather than silently
changing behavior:

- `azure-ai-agentserver-core` / `azure-ai-agentserver-responses` (`2.0.0b7` / `1.0.0b8`): the
  Responses protocol hosting SDK. Its `@app.response_handler` contract is lower-level than the C#
  `Microsoft.Agents.AI.Foundry.Hosting.AddFoundryResponses(agent, store)` helper -- there is no
  equivalent "wrap an `AIAgent`/`Agent` and get a Responses server for free" API in the current
  Python package, so `portfolio_agent.handler` implements that bridge explicitly (resolve trusted
  context, load/save Cosmos session, run the Agent Framework agent, and emit a
  `ResponseEventStream`). The bridge explicitly maps retained Agent Framework function
  calls/results into Responses output items required by trace evaluation.
  `tests/test_handler.py` and `tests/test_main.py` exercise this bridge against the real
  `ResponsesAgentServerHost` (via `starlette.testclient.TestClient`), so a future SDK revision
  that changes `client_headers`/`metadata`/`platform_context` shapes fails a test here.
- `agent-framework-core` / `agent-framework-foundry` (`1.11.0` / `1.10.1`): `agent-framework-foundry`
  is pre-1.0 (no stable release yet). `FoundryChatClient` is used directly (an OpenAI Responses API
  client scoped to a Foundry project + model deployment), not
  `agent_framework.azure.AzureAIAgentsProvider.create_agent()`, because the latter creates a
  *persistent* Azure AI Agent Service resource -- a different, heavier resource model than the C#
  agent's lightweight `AIProjectClient(...).AsAIAgent(...)` chat-client wrapper this agent mirrors.
- MCP tool mediation deliberately does **not** use the Agent Framework's built-in
  `MCPStreamableHTTPTool` / `HostedMCPTool`, because both configure headers/HTTP client once at
  tool-construction time. This agent's trusted `Authorization` bearer token, `X-Correlation-ID`,
  and tenant scope change on **every** request, so `portfolio_agent.mcp_client` opens a fresh
  official MCP Python SDK (`mcp==1.28.1`) `ClientSession` per tool call with per-call headers,
  mirroring the C# agent's own manual `McpClient.CreateAsync(...)` per call (the C# agent does not
  use a framework-level MCP tool wrapper either, for the same reason).
- The MCP Python SDK does not expose a single typed exception with a guaranteed `.status_code`
  the way C#'s `HttpRequestException.StatusCode` does, so `mcp_client._extract_status_code`
  defensively checks a few common attribute shapes and falls back to `502 Bad Gateway` semantics
  (matching the C# agent's own fallback) when a status code cannot be determined.
  `tests/test_mcp_client.py` pins this behavior, including the 403 tenant-mismatch log path.
