# TimesFM worker — pinned to Python 3.12 for torch/timesfm wheels (monorepo-doc §5).
FROM python:3.12-slim

WORKDIR /app
# git is needed to install TimesFM 2.5 from the upstream repo.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/timesfm-requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Only the worker + model code is needed; install the forecast package source.
COPY packages/forecast/src/norn_forecast/timesfm_worker.py /app/norn_forecast/timesfm_worker.py
COPY packages/forecast/src/norn_forecast/timesfm_model.py /app/norn_forecast/timesfm_model.py
RUN touch /app/norn_forecast/__init__.py
ENV PYTHONPATH=/app

EXPOSE 9100
CMD ["uvicorn", "norn_forecast.timesfm_model:build_app", "--factory", "--host", "0.0.0.0", "--port", "9100"]
