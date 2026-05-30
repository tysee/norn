"""
packages/forecast/src/norn_forecast/timesfm_model.py

Реальный адаптер TimesFM 2.5 (torch). Грузит модель один раз и отдаёт
квантильный прогноз в формате воркера. torch импортируется лениво (в __init__),
чтобы быстрый тест-сьют не тянул torch.

Классы/методы:
- TimesFM25Model.__init__() — загрузка модели TimesFM 2.5.
- TimesFM25Model.predict(values, horizon, quantiles) -> list[dict] —
  p-квантили -> p10/p50/p90 на горизонт.
- build_app() -> FastAPI — точка входа контейнера (create_app + реальная модель).
"""
from __future__ import annotations


class TimesFM25Model:
    def __init__(
        self,
        max_context: int | None = None,
        max_horizon: int | None = None,
        torch_compile: bool = False,
    ) -> None:
        from norn_core.config import get_settings

        tfm = get_settings(refresh=True).forecast.timesfm
        max_context = max_context if max_context is not None else tfm.max_context
        max_horizon = max_horizon if max_horizon is not None else tfm.max_horizon
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
            )
        )

    def predict(
        self, values: list[float], horizon: int, quantiles: list[float]
    ) -> list[dict]:
        import numpy as np

        point_forecast, quantile_forecast = self._model.forecast(
            horizon=horizon, inputs=[np.asarray(values, dtype=float)]
        )
        # point_forecast: (1, horizon); quantile_forecast: (1, horizon, 10).
        # Quantile columns are [mean, q10, q20, ..., q90] -> p10=idx1, p50=idx5, p90=idx9.
        point = point_forecast[0]
        quant = quantile_forecast[0]
        rows: list[dict] = []
        for h in range(horizon):
            rows.append(
                {
                    "horizon_step": h + 1,
                    "y_hat": float(point[h]),
                    "p10": float(quant[h][1]),
                    "p50": float(quant[h][5]),
                    "p90": float(quant[h][9]),
                }
            )
        return rows


def build_app():
    from norn_forecast.timesfm_worker import create_app

    return create_app(TimesFM25Model())
