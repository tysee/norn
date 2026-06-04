# norn agent-worker — LLM dependency judge behind HTTP (switchable: scale to 0
# and deps jobs degrade explicitly via LLMUnavailable -> explained=false).
FROM python:3.13-slim

WORKDIR /app
COPY packages/core /app/packages/core
COPY packages/integration /app/packages/integration
COPY packages/agent /app/packages/agent
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn>=0.29" \
    /app/packages/core /app/packages/integration /app/packages/agent

COPY config/ /app/config/
ENV NORN_CONFIG_DIR=/app/config

EXPOSE 9400
CMD ["uvicorn", "norn_agent.agent_worker:build_app", "--factory", "--host", "0.0.0.0", "--port", "9400"]
