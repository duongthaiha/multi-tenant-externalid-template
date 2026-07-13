# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Contoso Portfolio Agent -- Python Microsoft Agent Framework hosted agent.

Parallel Python implementation of ``src/portfolio-agent`` (C#), hosted on the
same Foundry Responses protocol contract, Backend Asset MCP tool boundary,
and trusted-header/metadata context model.
"""

from importlib import metadata as _metadata

try:
    __version__ = _metadata.version("portfolio-agent-python")
except _metadata.PackageNotFoundError:  # pragma: no cover - local/dev checkout without an installed dist
    __version__ = "0.0.0"

__all__ = ["__version__"]
