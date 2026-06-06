# norn platform image — role is the command: `scheduler` | `mcp` | ad-hoc `forecast …`.
# Light by design: torch/jax live in the timesfm worker image, the LLM in the agent image.
FROM python:3.13-slim

WORKDIR /app
COPY packages/ /app/packages/
COPY cli/ /app/cli/
RUN pip install --no-cache-dir \
    /app/packages/core /app/packages/integration /app/packages/forecast \
    /app/packages/agent /app/packages/scheduler /app/cli

# the default config is baked in; everything is overridable via env (env > yaml), secrets via env only
COPY config/ /app/config/
ENV NORN_CONFIG_DIR=/app/config
# unbuffered stdout/stderr: docker logs show scheduler progress immediately
# (e2e finding: buffered logs looked empty during startup health races)
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["norn"]
CMD ["--help"]
