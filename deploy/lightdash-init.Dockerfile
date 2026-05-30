# One-shot bootstrapper: registers the admin, mints a token, runs dbt, and
# creates the Lightdash project — so the warehouse/dbt UI forms are never
# touched by hand. Needs node (lightdash CLI) + python (dbt-clickhouse) + jq.
FROM node:20-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip python3-venv curl jq ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# dbt + ClickHouse adapter (the deploy step compiles & runs the project)
RUN pip install --no-cache-dir --break-system-packages dbt-clickhouse

# Lightdash CLI (deploy --create). `latest` tracks the latest server image.
RUN npm install -g @lightdash/cli

COPY bootstrap-lightdash.sh /usr/local/bin/bootstrap-lightdash.sh
RUN chmod +x /usr/local/bin/bootstrap-lightdash.sh

ENTRYPOINT ["/usr/local/bin/bootstrap-lightdash.sh"]
