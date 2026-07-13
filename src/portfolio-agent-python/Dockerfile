# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8088

WORKDIR /app
EXPOSE 8088

# Minimal OS packages: ca-certificates for outbound TLS to Foundry/APIM/Cosmos.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

FROM base AS build

COPY pyproject.toml requirements.lock README.md ./
COPY portfolio_agent ./portfolio_agent

RUN pip install --no-cache-dir --requirement requirements.lock \
    && pip install --no-cache-dir --no-deps .

FROM base AS final

COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY portfolio_agent ./portfolio_agent

# Runs as a non-root user in the container.
RUN useradd --no-create-home --shell /usr/sbin/nologin agent
USER agent

ENTRYPOINT ["python", "-m", "portfolio_agent.main"]
