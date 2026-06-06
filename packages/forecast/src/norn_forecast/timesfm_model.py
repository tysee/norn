"""
packages/forecast/src/norn_forecast/timesfm_model.py

Real adapter for the heavy TimesFM 2.5 model (torch) for the norn platform. Loads
and compiles the model once at startup and returns a quantile forecast in the
format the HTTP worker expects. torch and timesfm are imported lazily (inside
methods) so that the fast test suite and the other norn processes do not pull in
the heavy dependencies — they are only needed inside the worker container.

Classes/methods:
- TimesFM25Model.__init__(max_context, max_horizon, torch_compile) — loading and
  compilation of TimesFM 2.5; default limits from env NORN_TIMESFM_MAX_CONTEXT/HORIZON
  (the worker is self-contained, without norn_core.config).
- TimesFM25Model.predict(values, horizon, quantiles,
  dynamic_numerical_covariates=None) -> list[dict] — forecast and mapping of the
  model's quantile columns into p10/p50/p90 for each horizon step. With
  covariates it calls forecast_with_covariates (xreg_mode from env
  NORN_TIMESFM_XREG_MODE), without them — the plain forecast (the default path is unchanged).
- build_app() -> FastAPI — container entry point: create_app + the real model.
"""
from __future__ import annotations


class TimesFM25Model:
    def __init__(
        self,
        max_context: int | None = None,
        max_horizon: int | None = None,
        torch_compile: bool = False,
    ) -> None:
        import os

        # --- context/horizon limits: argument > env > default ---
        # The worker is self-contained (a container) and does NOT depend on norn_core.config:
        # the image has neither norn_core nor a config YAML. Parameters come from an
        # argument or from env.
        max_context = max_context if max_context is not None else int(
            os.environ.get("NORN_TIMESFM_MAX_CONTEXT", "1024"))
        max_horizon = max_horizon if max_horizon is not None else int(
            os.environ.get("NORN_TIMESFM_MAX_HORIZON", "1024"))

        # --- model loading and compilation (lazy import of torch/timesfm) ---
        # Lazy imports: torch/timesfm only inside the worker env (never the fast suite).
        import timesfm

        self._model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
            "google/timesfm-2.5-200m-pytorch", torch_compile=torch_compile
        )
        self._model.compile(
            timesfm.ForecastConfig(
                max_context=max_context,
                max_horizon=max_horizon,
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                force_flip_invariance=True,
                infer_is_positive=True,
                fix_quantile_crossing=True,
                # The XReg path (forecast_with_covariates) requires return_backcast=True.
                # Side effect: forecast() returns [backcast, forecast] along the time
                # axis — predict() slices the last horizon steps on both paths.
                return_backcast=True,
            )
        )

    def predict(
        self,
        values: list[float],
        horizon: int,
        quantiles: list[float],
        dynamic_numerical_covariates: dict[str, list[float]] | None = None,
    ) -> list[dict]:
        import numpy as np

        # --- model inference ---
        if dynamic_numerical_covariates:
            # XReg path: leaders are passed as dynamic numerical covariates.
            # Version-sensitive: arg names/shape checked against the installed TimesFM 2.5 (git);
            # on a mismatch adapt here — the stable worker contract lives in FakeModel.
            import os

            mode = os.environ.get("NORN_TIMESFM_XREG_MODE", "xreg + timesfm")
            # TimesFM 2.5 expects modes with spaces ("xreg + timesfm"); we normalize the
            # compact legacy values from old configs so we do not crash.
            mode = {"xreg+timesfm": "xreg + timesfm", "timesfm+xreg": "timesfm + xreg"}.get(mode, mode)
            # forecast_with_covariates does not take horizon: the depth is derived from
            # the covariate length (len(covariate) - len(values)); the runner sends context+horizon.
            point_forecast, quantile_forecast = self._model.forecast_with_covariates(
                inputs=[np.asarray(values, dtype=float)],
                dynamic_numerical_covariates={
                    k: [np.asarray(v, dtype=float)]
                    for k, v in dynamic_numerical_covariates.items()
                },
                xreg_mode=mode,
            )
        else:
            point_forecast, quantile_forecast = self._model.forecast(
                horizon=horizon, inputs=[np.asarray(values, dtype=float)]
            )
        # point_forecast: (1, T); quantile_forecast: (1, T, 10), where T >= horizon
        # (with return_backcast=True forecast() returns [backcast, forecast]) — we take
        # the last horizon steps; the XReg path already returns exactly horizon (slice is a no-op).
        # Quantile columns are [mean, q10, q20, ..., q90] -> column = round(q*10).
        point = point_forecast[0][-horizon:]
        quant = quantile_forecast[0][-horizon:]

        def _col(q: float) -> int:
            return int(round(q * 10))

        lo, mid, hi = quantiles[0], quantiles[1], quantiles[2]

        # --- mapping the quantile columns into p10/p50/p90 per horizon step ---
        rows: list[dict] = []
        for h in range(horizon):
            rows.append(
                {
                    "horizon_step": h + 1,
                    "y_hat": float(point[h]),
                    "p10": float(quant[h][_col(lo)]),
                    "p50": float(quant[h][_col(mid)]),
                    "p90": float(quant[h][_col(hi)]),
                }
            )
        return rows


def build_app():
    from norn_forecast.timesfm_worker import create_app

    return create_app(TimesFM25Model())
