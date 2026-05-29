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
    def __init__(self) -> None:
        # Lazy imports: torch/timesfm only inside the container.
        import timesfm

        self._tfm = timesfm.TimesFm(
            hparams=timesfm.TimesFmHparams(backend="cpu", horizon_len=512),
            checkpoint=timesfm.TimesFmCheckpoint(
                huggingface_repo_id="google/timesfm-2.5-200m-pytorch"
            ),
        )

    def predict(
        self, values: list[float], horizon: int, quantiles: list[float]
    ) -> list[dict]:
        point, quantile = self._tfm.forecast(
            inputs=[values], freq=[0], horizon_len=horizon
        )
        # quantile shape: [1, horizon, n_quantiles]; TimesFM default quantile order
        # is [0.1..0.9]; map requested 0.1/0.5/0.9 to columns.
        q = quantile[0]
        idx = {0.1: 1, 0.5: 5, 0.9: 9}
        rows: list[dict] = []
        for h in range(horizon):
            rows.append(
                {
                    "horizon_step": h + 1,
                    "y_hat": float(point[0][h]),
                    "p10": float(q[h][idx[0.1]]),
                    "p50": float(q[h][idx[0.5]]),
                    "p90": float(q[h][idx[0.9]]),
                }
            )
        return rows


def build_app():
    from norn_forecast.timesfm_worker import create_app

    return create_app(TimesFM25Model())
