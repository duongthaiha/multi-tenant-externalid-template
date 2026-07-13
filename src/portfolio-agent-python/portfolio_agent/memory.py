# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Managed-identity Cosmos DB conversation memory for the Python portfolio agent.

Ports ``CosmosAgentSessionStore`` / ``AgentMemoryOptions`` (C#,
``src/portfolio-agent/Program.cs``) using the async ``azure-cosmos`` SDK and
``agent_framework.AgentSession.to_dict()`` / ``from_dict()`` for the state
bag, instead of the Agent Framework's ``AgentSessionStore`` /
``AgentSessionStateBag`` hosting abstraction that the C# implementation
piggybacks on -- the Python ``azure-ai-agentserver-responses`` package's own
session-store protocol (``ResponseProviderProtocol``) is scoped to the
*Responses envelope* replay/history feature, not model conversation memory,
so this module is a small, explicit adapter used directly from
``portfolio_agent.handler`` instead.

**Tenant isolation**: one Cosmos *database* per business tenant, exactly like
the C# agent and the Backend API -- never a single shared/global database.
The database name is ``f"{database_prefix}{normalized_tenant_id}"``.

**Python-specific database prefix, no global fallback**: the default prefix
is ``agent-memory-python-`` (see ``infra/modules/cosmos-agent-memory.bicep``'s
``pythonDatabasePrefix``), which is provisioned as a *separate* set of Cosmos
databases from the C# agent's ``agent-memory-`` prefix. The two hosted-agent
profiles never share a database, and there is no "default"/global database
used when a tenant is unknown -- a missing or incomplete trusted context
fails closed (returns ``None`` for reads, raises for writes) rather than
falling back to any shared or default-tenant storage.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from agent_framework import AgentSession
from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential

from .context import PortfolioToolContext
from .telemetry import PortfolioTelemetry

_DEFAULT_DATABASE_PREFIX = "agent-memory-python-"
_DEFAULT_CONTAINER_NAME = "agentSessions"
_DEFAULT_SESSION_TTL_SECONDS = 2592000  # 30 days, matching the C# agent and the Bicep container TTL.
_DOCUMENT_TYPE = "AgentSession"


@dataclass(frozen=True)
class AgentMemoryOptions:
    """Resolved Cosmos conversation-memory configuration.

    Ports ``AgentMemoryOptions`` (C#). ``endpoint`` has **no default** and
    fails closed when unset; ``database_prefix`` defaults to the
    Python-specific prefix, never the shared/C# one.
    """

    endpoint: str
    database_prefix: str
    container_name: str
    session_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "AgentMemoryOptions":
        endpoint = os.environ.get("CONTOSO_AGENT_MEMORY_ENDPOINT", "").strip()
        if not endpoint:
            raise RuntimeError("CONTOSO_AGENT_MEMORY_ENDPOINT environment variable is not set.")

        database_prefix = (
            os.environ.get("CONTOSO_AGENT_MEMORY_DATABASE_PREFIX", "").strip()
            or _DEFAULT_DATABASE_PREFIX
        )
        container_name = (
            os.environ.get("CONTOSO_AGENT_MEMORY_CONTAINER_NAME", "").strip()
            or _DEFAULT_CONTAINER_NAME
        )

        raw_ttl = os.environ.get("CONTOSO_AGENT_MEMORY_SESSION_TTL_SECONDS", "").strip()
        try:
            session_ttl_seconds = int(raw_ttl) if raw_ttl else _DEFAULT_SESSION_TTL_SECONDS
        except ValueError:
            session_ttl_seconds = _DEFAULT_SESSION_TTL_SECONDS

        return cls(
            endpoint=endpoint,
            database_prefix=database_prefix,
            container_name=container_name,
            session_ttl_seconds=session_ttl_seconds,
        )


def _normalize_tenant_id(tenant_id: str) -> str:
    return tenant_id.replace("-", "").lower()


def _document_id(agent_name: str, tenant_id: str, user_id: str, conversation_id: str) -> str:
    raw = f"{agent_name}|{tenant_id}|{user_id}|{conversation_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"agent-session-{digest}"


class CosmosAgentSessionStore:
    """Tenant-partitioned Cosmos DB store for :class:`agent_framework.AgentSession` state.

    Ports ``PortfolioToolContext.CosmosAgentSessionStore`` (C#). Uses
    managed identity (``DefaultAzureCredential``) exclusively -- never a
    connection string or account key -- matching
    ``disableLocalAuth: true`` on the Cosmos account.
    """

    def __init__(self, cosmos_client: CosmosClient, options: AgentMemoryOptions) -> None:
        self._client = cosmos_client
        self._options = options

    @classmethod
    def create_from_environment(cls) -> "CosmosAgentSessionStore":
        options = AgentMemoryOptions.from_env()
        credential = DefaultAzureCredential()
        client = CosmosClient(options.endpoint, credential=credential)
        return cls(client, options)

    async def close(self) -> None:
        await self._client.close()

    async def get_session(
        self,
        context: PortfolioToolContext,
        agent_name: str,
        conversation_id: str,
        telemetry: PortfolioTelemetry,
    ) -> Optional[AgentSession]:
        """Load a previously saved session, or ``None`` if absent/untrusted/not found.

        Fails closed (returns ``None``) when the trusted context has no
        tenant id -- conversation memory must never be read using an
        unauthenticated or cross-tenant-ambiguous context. The response handler uses
        an ephemeral session and does not call this store for context-less Foundry
        managed evaluation requests.
        """
        if not context.tenant_id.strip():
            return None

        resolved_user_id = context.user_id or ""
        document_id = _document_id(agent_name, context.tenant_id, resolved_user_id, conversation_id)
        container = self._container(context.tenant_id)

        try:
            item = await container.read_item(item=document_id, partition_key=context.tenant_id)
        except CosmosResourceNotFoundError:
            return None
        except CosmosHttpResponseError as exc:
            telemetry.log_cosmos_session_store_failure("read", context.tenant_id, exc)
            return None

        state_bag: dict[str, Any] = item.get("stateBag") or {}
        return AgentSession.from_dict({
            "session_id": conversation_id,
            "service_session_id": item.get("serviceSessionId"),
            "state": state_bag,
        })

    async def save_session(
        self,
        context: PortfolioToolContext,
        agent_name: str,
        conversation_id: str,
        session: AgentSession,
        telemetry: PortfolioTelemetry,
    ) -> None:
        """Persist ``session`` for the trusted tenant/user, or fail closed.

        Raises :class:`RuntimeError` when the trusted context has no tenant
        id -- session persistence must never write under an ambiguous or
        unauthenticated tenant scope.
        """
        if not context.tenant_id.strip():
            raise RuntimeError("Portfolio agent session persistence requires trusted tenant context.")

        resolved_user_id = context.user_id or ""
        now = datetime.now(timezone.utc).isoformat()
        serialized = session.to_dict()
        document = {
            "id": _document_id(agent_name, context.tenant_id, resolved_user_id, conversation_id),
            "tenantId": context.tenant_id,
            "documentType": _DOCUMENT_TYPE,
            "agentName": agent_name,
            "userId": resolved_user_id,
            "conversationId": conversation_id,
            "serviceSessionId": serialized.get("service_session_id"),
            "stateBag": serialized.get("state", {}),
            "createdAt": now,
            "updatedAt": now,
            "ttl": self._options.session_ttl_seconds,
        }

        container = self._container(context.tenant_id)
        try:
            await container.upsert_item(body=document)
        except CosmosHttpResponseError as exc:
            telemetry.log_cosmos_session_store_failure("save", context.tenant_id, exc)

    def _container(self, tenant_id: str):
        database_name = f"{self._options.database_prefix}{_normalize_tenant_id(tenant_id)}"
        database = self._client.get_database_client(database_name)
        return database.get_container_client(self._options.container_name)


__all__ = ["AgentMemoryOptions", "CosmosAgentSessionStore"]
