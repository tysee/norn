# Norn — моно-репо, ERD и тех-стек

*Дата: 29 мая 2026. Сопровождает `erd.mermaid` и `architecture.mermaid`.*

> **Инвариант платформы.** norn — вендор-нейтральная, домен-АГНОСТИЧНАЯ forecasting-платформа: мультисегментный прогноз метрик и поиск зависимостей поверх любого warehouse через generic-контракт (`forecast_point`/`forecast_segment`), конфигурируемые модель/провайдер/БД и MCP-контракт. Платформенный код (`packages/*`, `cli`) НЕ несёт доменных дефолтов — ни встроенных метрик, символов, размерностей, форматов ingestion, дашбордов, промптов, ни выбора LLM-модели. Вся доменная специфика живёт в отдельном инстанс-репо (`norn-crypto-instance` — первый dogfood-инстанс, подключается submodule). GTM-фокус (первый целевой вертикал) — delivery/marketplace/e-commerce: это рыночная стратегия, а НЕ платформенный дефолт. Любой конкретный домен в этом документе (delivery-KPI вроде delivered_orders/GMV, крипто-символы BTC/TON, размерности, трансформации, выбор модели) — помеченный ПРИМЕР, указывающий на инстанс/вертикал, а не требование платформы; детали домена — в инстанс-репо.

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
`Forecaster`-интерфейсом (baseline остаётся фолбэком). Наполнение данными — raw
datapoints (формат ingestion — выбор инстанса; крипто-инстанс: `raw_candles`) —
отдельно, вне платформы.

**MCP-слой (агенты):** `norn mcp` поднимает FastMCP-сервер (streamable-http) с
MCP-инструментами (11): get_forecast / get_expected_range / classify_levels_vs_band /
get_divergence / get_calibration (incl. is_sparse) / get_dependencies (explained-флаг +
числовой fallback при деградации LLM) / get_dependency_history / get_run_status /
get_forecast_status / list_metrics / list_segments поверх таблиц `forecast_point` /
`forecast_segment` / `forecast_run` / `metric_dependency` / `dependency_explanation`.
Discovery (list_*) и статус/свежесть (get_*_status) позволяют агенту находить ряды и
оценивать актуальность прогноза. «Lightdash для людей, MCP для агентов».
`get_dependencies` (пример (крипто-инстанс): BTC↔TON) — Plan 5.

**Dependency-агент (`packages/agent`):** PydanticAI-агент анализа зависимостей. Методы
(lagged cross-correlation + Granger на доменной трансформации ряда — пример доменной
трансформации (крипто-инстанс): log-returns) дают улики → агент судит реальность и
объясняет → `metric_dependency` (числа) + `dependency_explanation` (решение). `norn deps
<job.yml>`; MCP `get_dependencies` отдаёт и числа, и решение агента. Тесты — на PydanticAI
`TestModel` (без реального LLM). Лаг — будущая ковариата TimesFM (XReg).

**LLM-провайдер и модель агента** конфигурируем (`config/agent.yml` → `provider` / `model`):
ollama (локальный), openai-api, openai-oauth (bearer), openrouter, anthropic-api. Конкретные
модель и провайдер — выбор инстанса в `config/agent.yml`; у платформы НЕТ дефолтной LLM-модели.
Секреты — из env (OPENAI_API_KEY / NORN_OPENAI_OAUTH_TOKEN / OPENROUTER_API_KEY /
ANTHROPIC_API_KEY). Для локального Ollama: запущенный демон на :11434 + `ollama pull <model>`
(модель из config инстанса). При недоступном/неверном провайдере `norn deps` деградирует
(пишет metric_dependency, без объяснения), не падает.

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

**Конфиг — YAML-native без скрытых дефолтов:** поля настроек не имеют Python-дефолтов; значение
берётся из `config/<section>.yml` (или env-override), отсутствие обязательного ключа → явный
`ValidationError` на старте. Секрет БД (`password`) — только из env `NORN_DB_PASSWORD`. LLM-режим
вывода (`agent.output_mode`: native|tool|prompted) и `agent.base_url` — явная конфигурация, без
фолбеков в коде. **Деградация LLM явная:** `judge_dependencies` поднимает `LLMUnavailable`,
`analyze_dependencies` ловит на границе (ERROR-лог с traceback), возвращает `AnalysisResult`
(`explained=False` + причина), CLI печатает `⚠ LLM explanation skipped: …`; статистика
(`metric_dependency`) пишется всегда.

**Владение схемой контракт-таблиц (`database.manage_schema`):** norn — warehouse-table-native,
dbt-опциональна. `true` (дефолт) — norn идемпотентно создаёт свои контракт-таблицы в своей БД
(zero-setup, greenfield/локалка). `false` — norn НЕ выполняет DDL (только INSERT); таблицы
заводит пользователь своим dbt/миграциями, каноническую DDL печатает `norn print-schema`; перед
записью norn делает pre-flight проверку и при отсутствии таблиц явно падает `ContractSchemaMissing`.
Так платформа не навязывает runtime-DDL governed-хранилищу. dbt — типичный, но не обязательный
способ построить как витрину, так и эти таблицы.

**Хранение контракт-таблиц при росте:** одна таблица на тип контракта, но с
`PARTITION BY toYYYYMM(created_at)` и настраиваемым `TTL` (`forecast.retention_months`,
дефолт 12 мес; 0 = без TTL). Это идиоматично для ClickHouse (не дробим на таблицы).
**Upgrade существующих таблиц:** ClickHouse не добавляет `PARTITION BY` через `ALTER` —
для уже созданных таблиц требуется пересоздание (drop + `norn schema-apply`); forecast-данные
воспроизводимы повторным прогоном job'ов, поэтому это безопасный штатный шаг. `TTL` отдельно
можно докинуть `ALTER TABLE ... MODIFY TTL ...`.

---

## 7. Связь с диаграммами

- `erd.mermaid` — сущности и связи (логическая модель данных, легенда хранилищ).
- `architecture.mermaid` — компонентная схема сайдкара: CLI → 3 пакета → ClickHouse / Lightdash / dbt / LLM.

Оба файла рендерятся в Cowork; открой их карточки, чтобы увидеть диаграммы.
