# Norn — моно-репо, ERD и тех-стек

*Дата: 29 мая 2026. Сопровождает `erd.mermaid` и `architecture.mermaid`.*

Плаг-ин-плей сайдкар к Lightdash: аналитик одной командой поднимает прогнозы и анализ зависимостей поверх существующего стека `dbt + ClickHouse + Lightdash`. Мы **не форкаем Lightdash и не пишем свой BI** — добавляем три слоя сбоку.

---

## 1. Состав моно-репо (3 части + CLI)

```text
norn/                    # репозиторий tysee/norn
├── packages/
│   ├── agent/          # 1) Агент анализа зависимостей (pi.dev / PydanticAI)
│   ├── forecast/       # 2) Сервис прогнозов (TimesFM 2.5 worker, FastAPI)
│   └── integration/    # 3) Обвязка: dbt + Lightdash + ClickHouse
├── cli/                # one-command orchestrator: `norn up`
├── deploy/             # docker-compose: сайдкар рядом с Lightdash
├── forecasts/          # YAML forecast-job registry (без UI на старте)
└── pyproject.toml      # workspace (uv / hatch), общий линт/типы
```

Три части — это ровно три слоя фокуса из стратегии: **описание метрик** (integration), **прогнозирование** (forecast), **поиск зависимостей** (agent).

---

## 2. Тех-стек и принцип «меньше зависимостей»

Базовое правило: берём готовое, если оно не навязывает лишних ограничений; свой код — только клей. Каждая зависимость должна «оправдать своё место».

| Часть | Язык / рантайм | Готовое (переиспользуем) | Свой код (клей) |
|-------|----------------|---------------------------|-----------------|
| integration | Python 3.14+ | `dbt-core` + `dbt-clickhouse`; чтение метрик из dbt `manifest.json` (или Lightdash API); `clickhouse-connect` | маппинг dbt-метрик → `metric_definition`, генерация `actual_vs_forecast` dbt-модели |
| forecast | Python (env воркера, см. §5) | `timesfm` (2.5) + `torch`; `clickhouse-connect`; `FastAPI` для API; `pydantic` для конфигов | extract→group→inference→write-back; sparse-политика |
| agent | Python 3.14+ | агент-фреймворк (pi.dev / PydanticAI); `numpy`/`scipy`/`statsmodels` (lagged corr, Granger, MI); LLM-SDK провайдера | оркестрация анализа, формирование объяснений с caveats |
| cli | Python 3.14+ | `typer` (или stdlib `argparse`, если хотим 0 доп. зависимостей) | `norn init` / `norn up` |
| metadata | — | Postgres (позже); на старте — `forecasts/*.yml` + таблицы в ClickHouse | — |

Намеренно НЕ тащим: свой scheduler (берём системный `cron` в `deploy/`), свой ORM на старте (конфиги — YAML, ран-лог — в ClickHouse), свой dashboard-движок (Lightdash), свой transform (dbt).

---

## 3. Хранилища и где живут сущности ERD

Легенда `erd.mermaid`:

- **`[LD]` Lightdash Postgres** — проекты и dbt-метрики. **Только читаем** (через `manifest.json` или Lightdash API), не владеем.
- **`[CH]` ClickHouse** — `mart_metric` (факты, строит dbt), `forecast_point` (выход прогноза), `actual_vs_forecast` (dbt-вью). Аналитический слой.
- **`[META]` addon Postgres** — `project`, `connection`, `metric_definition`, `forecast_job/run/segment`, `dependency_*`.

**Важная оговорка про MVP.** Полноценный `[META]` Postgres нужен только когда появятся UI/много пользователей. На старте (как в MVP-спеке) персистентность проще:

- `forecast_job` / `metric_definition` → `forecasts/*.yml` в репозитории;
- `forecast_run` / `forecast_segment` / `forecast_point` → таблицы ClickHouse;
- `dependency_*` → ClickHouse или JSON-артефакты.

То есть ERD описывает **логическую** модель; реляционный `[META]`-стор вводим на Фазе 1+, когда добавляем зависимости/MCP и UI. Это держит число инфра-зависимостей минимальным на старте (только ClickHouse, который и так есть).

---

## 4. One-command UX (plug-and-play)

```text
norn init       # обнаружить dbt-метрики (manifest.json/Lightdash),
                # предложить forecast-jobs -> forecasts/*.yml
norn up         # поднять сайдкар: forecast worker + agent (FastAPI),
                # прогнать прогноз, записать forecast_point в ClickHouse,
                # сгенерировать dbt actual_vs_forecast и обновить Lightdash
```

`deploy/docker-compose.yml` запускает сайдкар, указывающий на **существующие** ClickHouse и Lightdash (через env). Аналитику не нужно ничего конфигурировать вручную сверх DSN.

### Локальный BI-стек (отладка)

`deploy/docker-compose.yml` поднимает локально: ClickHouse + Lightdash (+ его
Postgres + headless-browser) + generic dbt-проект `deploy/dbt/` (profiles → ClickHouse,
модели `mart_metric`, `actual_vs_forecast`). TimesFM-воркер — отдельный torch-pinned
контейнер (`deploy/timesfm.Dockerfile`), forecast-слой ходит в него по HTTP за
`Forecaster`-интерфейсом (baseline остаётся фолбэком). Наполнение данными
(`raw_candles`) — отдельно, вне платформы.

**MCP-слой (агенты):** `norn mcp` поднимает FastMCP-сервер (streamable-http) с
инструментами get_forecast / get_expected_range / check_ladder_rungs /
get_divergence / get_calibration поверх таблиц `forecast_point` / `forecast_segment`.
«Lightdash для людей, MCP для агентов». `get_dependencies` (BTC↔TON) — Plan 5.

**Dependency-агент (`packages/agent`):** PydanticAI-агент анализа зависимостей. Методы
(lagged cross-correlation + Granger на log-returns) дают улики → агент судит реальность и
объясняет → `metric_dependency` (числа) + `dependency_explanation` (решение). `norn deps
<job.yml>`; MCP `get_dependencies` отдаёт и числа, и решение агента. Тесты — на PydanticAI
`TestModel` (без реального LLM). Лаг — будущая ковариата TimesFM (XReg).

---

## 5. Совместимость Python 3.14+ (честный риск)

Наш код целимся на **Python 3.14+**, но две зависимости исторически отстают от свежих релизов Python:

- **`torch` / `timesfm`** — колёса под 3.14 могут появиться с задержкой.
- **`dbt-core`** — поддержка новых минорных версий Python обычно догоняет не сразу.

Митигация — моно-репо это позволяет без боли:

1. **forecast** запускаем в своём контейнере с зафиксированным интерпретатором под torch (напр. 3.12/3.13), общается по FastAPI/HTTP — наш остальной код остаётся на 3.14+.
2. **dbt** вызываем как **subprocess** (CLI), а не импортируем в наш процесс → версия Python dbt развязана с нашей.
3. `integration` и `agent` (чистый Python + numpy/scipy) — на 3.14+ без проблем.

Проверить перед стартом: наличие колёс `torch`/`timesfm` и поддержку Python в `dbt-clickhouse` на момент сборки (открытый вопрос в spike).

---

## 6. Конфигурация (YAML-native)

Все generic-настройки платформы — в центральной `config/` (разбито по логике:
`database.yml`/`forecast.yml`/`agent.yml`/`mcp.yml`), читаются типизированным слоем
`norn_core.config` (pydantic-settings). Приоритет: **env > YAML > дефолт**. Секреты
(пароль БД, API-ключи) — только в env (`NORN_DB_PASSWORD`, `NORN_CLICKHOUSE_URL`).
`NORN_CONFIG_DIR` переопределяет путь. Доменные значения (метрики/символы) в
платформенный config НЕ попадают — это инстанс.

Магические константы устранены: интервалы baseline выводятся из `forecast.quantiles`
(нормальная аппроксимация), значимость/порог Granger — из `agent.*`, колонки квантилей
TimesFM выводятся из запрошенных квантилей. Числовые допуски (eps) — именованные константы.

**Ковариаты (XReg):** forecast-job может объявить covariates (metric/segment/lag) или
use_dependencies (взять подтверждённые зависимости из metric_dependency). Раннер строит
выровненный по таймстемпам ряд лидера на контекст+горизонт (policy strict|ffill из config) и
передаёт TimesFM как dynamic_numerical_covariates (forecast_with_covariates). Без ковариат —
обычный прогноз (дефолт, без изменений). Baseline ковариаты игнорирует.

---

## 7. Связь с диаграммами

- `erd.mermaid` — сущности и связи (логическая модель данных, легенда хранилищ).
- `architecture.mermaid` — компонентная схема сайдкара: CLI → 3 пакета → ClickHouse / Lightdash / dbt / LLM.

Оба файла рендерятся в Cowork; открой их карточки, чтобы увидеть диаграммы.
