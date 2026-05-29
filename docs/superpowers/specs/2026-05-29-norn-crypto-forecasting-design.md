# Design: norn — крипто-forecasting как первый dogfood-инстанс

*Дата: 2026-05-29. Дизайн-документ (brainstorming → spec). Сопровождает*
*`docs/prd/mvp-prd-backlog.md`, `docs/erd/*`, `docs/prd/metric-intelligence-strategy.md`.*

## Резюме

norn остаётся **вендор-нейтральным forecasting-слоем** (стек и архитектура из PRD —
`dbt → ClickHouse → TimesFM 2.5 → Lightdash` — **не меняются технически**). Меняется
только **первый dogfood-потребитель**: вместо delivery-KPI прогнозируем **курс BTC и TON**
для дисциплины торговли в боте `pibitagent` (отдельный репозиторий
`~/Documents/pibitagent`). Источник данных —
**Bybit**; разрез — **symbol (BTC, TON)**; метрики — **close / log_return / realized_vol**.

Добавляется второй интерфейс чтения поверх той же forecast-таблицы:
**Lightdash — для людей, MCP — для агентов** (бот `pibitagent` ходит через MCP).

## Принцип границы (жёсткий)

**Платформа `norn` остаётся generic.** Крипто-специфика — это *инстанс/потребитель*,
а не часть платформы. Поэтому:

- **Ingestion и крипто-конфиги пишем отдельно**, в репозитории `norn-crypto-instance`,
  подключённом к моно-репо как **git submodule**. Версионируется независимо.
- **Никаких кусков Lightdash** в `norn`: только наш интеграционный адаптер
  (`packages/integration`) + инструкции «как интегрироваться с Lightdash». Крипто-дашборды
  и project-конфиг Lightdash живут в инстанс-репо.
- Любой спецкод модели/тестового проекта, случайно оказавшийся в моно-репо, —
  в `.gitignore`; каноническое место — submodule.

| | `norn` (платформа — коммитим) | `norn-crypto-instance` (submodule) |
|---|---|---|
| Код | `packages/{integration,forecast,agent}`, `cli`, `deploy`, generic forecast-job схема | Bybit-ingestion, BTC/TON forecast-job YAML, тюнинг модели, тест-данные |
| Lightdash | адаптер + инструкции интеграции | крипто-дашборды / project-конфиг |
| Назначение | вендор-нейтральный слой | dogfood-инстанс |

## Развёртывание и базы данных

**Всё локально, в Docker, поднимается одной командой — с первого этапа.** Это требование
к самому раннему MVP, а не «потом»: `cli/ norn up` → `deploy/docker-compose.yml` поднимает
весь сайдкар в контейнерах (ClickHouse + forecast-воркер + agent + MCP-сервер; Lightdash —
внешний, подключаем по env, не контейнеризуем его сами). Локальный one-command путь
остаётся **всегда** — это основной режим для дальнейшей локальной разработки.

Облачные деплои (**Google Cloud, AWS, k8s**) — следующий этап, вне MVP. Контейнеризация
с самого начала делает этот переход естественным; архитектура не должна его блокировать,
но и не реализует сейчас.

**Какие БД нужны:**

- **ClickHouse — нужен** (сердце стека). В MVP — **локальный контейнер** на capable-хосте
  (off-Pi). Данные регенерируемы (свечи перекачиваются из Bybit, прогнозы пересчитываются) —
  это аналитический кэш, не source-of-truth, поэтому локальный инстанс допустим, и правило
  `database.md` («никакой локальной БД») на него не распространяется.
- **`[META]` Postgres — в MVP НЕ нужен.** Метаданные = `forecasts/*.yml` + таблицы ClickHouse
  (`monorepo-and-data-model.md` §3). Отдельный Postgres — Фаза 1+ (UI/зависимости).
- **Supabase пибит-агента — не трогаем.** Хендофф к боту идёт через MCP, не через общую
  таблицу, так что norn в Supabase ничего не пишет.

## Что меняется vs текущий PRD — только предметная область

| Слой | Было (delivery) | Стало (crypto) |
|---|---|---|
| Источник | заказы доставки | **Bybit klines** (CoinGecko — fallback для длинной истории) |
| Warehouse | ClickHouse | ClickHouse *(тот же)* |
| Transform | dbt | dbt *(тот же)* |
| Модель | TimesFM 2.5 | TimesFM 2.5 *(та же)* |
| BI / люди | Lightdash | Lightdash *(тот же)* |
| Агенты | — | **MCP** (новый интерфейс чтения той же forecast-таблицы) |
| Метрики (`metric_name`) | delivered_orders, GMV, cancellation_rate | **close, log_return, realized_vol** |
| Grain | hourly / daily | **daily** (primary) |
| Dimensions | city, store_id, merchant_id | **symbol** (BTC, TON) |

Пайплайн — буквально из PRD §Scope, без правок:

```
Bybit → ingestion (в инстанс-репо) → ClickHouse (mart candles)
  → dbt-метрика → TimesFM Python-воркер по YAML-конфигу → forecast-таблица (FORECAST_POINT)
  → dbt actual_vs_forecast модель → Lightdash (люди)
                                  ↘ MCP-сервер (агенты → pibitagent)
```

## Маппинг крипты на существующий ERD (`docs/erd/erd.mermaid`)

Схема концептуально не меняется — переопределяем значения, не структуру:

| ERD-сущность | Было (delivery) | Стало (crypto) |
|---|---|---|
| `METRIC_DIMENSION` | city / store_id / merchant_id | **symbol** (`LowCardinality(String)`: BTC, TON) |
| `MART_METRIC.value` | delivered_orders / gmv | **close / log_return / realized_vol** |
| `METRIC_DEFINITION.name` | delivered_orders… | `close`, `realized_vol` (measure) |
| `FORECAST_POINT.p10/p50/p90` | интервалы | **те же** → price-path CI **и** range-продукт |
| долгосрочный сценарий | — | просто `FORECAST_JOB` с большим `horizon` |

forecast-контракт PRD (`metric_name, grain, ts, <dim=symbol>, yhat, yhat_lower/upper,
model, horizon, run_id, generated_at`) сохраняется 1-в-1.

## Три forecast-продукта (все — TimesFM 2.5, daily)

1. **Daily close 1–30d** — точечный прогноз close + калиброванные `p10/p90`.
   Назначение: sanity-check ступеней лестницы, ранний сигнал выхода факта за бэнд.
2. **Волатильность / ожидаемый диапазон** — из спреда квантилей; опционально
   отдельный `FORECAST_JOB` на ряде realized-vol/ATR. Направления нет — для сайзинга/спейсинга.
3. **Долгосрочный сценарий (≈90–540d)** — та же модель/контракт, широкие интервалы,
   явный флаг «low-confidence сценарий, не таргет» (тезис BTC 3–12мес, TON 6–18мес).

## Ingestion (в `norn-crypto-instance`, НЕ в платформе)

Единственный реально новый код домена. Тянет дневные свечи Bybit (CoinGecko fallback для
длинной истории) → raw candles в ClickHouse → dbt строит `mart_metric` (close, log-returns,
realized vol). Запускается **off-Pi** (capable-хост), пишет в ClickHouse по HTTP. Может
переиспользовать существующие Bybit-хелперы пибит-агента как референс, но код инстанса
независим от платформы.

## Два интерфейса чтения (одна forecast-таблица)

- **Lightdash (люди):** dbt `actual_vs_forecast` → дашборды. Без изменений из PRD.
  В платформе — только адаптер интеграции, не сам Lightdash.
- **MCP (агенты):** тонкий MCP-сервер в `packages/forecast` (там уже есть FastAPI),
  читает ClickHouse. Инструменты:
  - `get_forecast(symbol, metric, horizon)` → yhat + p10/p90
  - `get_expected_range(symbol, horizon)` → vol / диапазон
  - `check_ladder_rungs(symbol, rungs[])` → sanity-check предложенных ступеней vs бэнды
  - `get_divergence(symbol)` → факт вышел за интервал / смена vol-режима
  - `get_calibration(symbol, metric)` → уровень доверия прогнозу
  - `get_dependencies(target="TON")` → «BTC опережает TON на N дней» (из `packages/agent`)

  Pi регистрирует MCP-сервер у себя. При недоступности хоста — **graceful degradation**:
  агент помечает как `data_freshness`-проблему и не выдумывает прогноз.

## Потребление в pibitagent

- Оркестратор (Context A, data-prep) или сам pi (Context B) зовёт MCP → forecast +
  калибровка + зависимости попадают в `analysis_context.json`. Advisory, не хард-гейт.
- **Ladder sanity-check** через `check_ladder_rungs` при (пере)расстановке ступеней.
- **Telegram-алерты на дивергенцию:** scheduled-проверка оркестратора зовёт
  `get_divergence` → существующий alert-path пибит-агента.

## Калибровка (NFR-2, критично для крипты)

Rolling-origin бэктест; фактическое покрытие `p10/p90` vs номинал; bias переоценивается
периодически, а не фиксируется один раз. `FORECAST_SEGMENT` уже несёт `wape/mape`.
Калибровка видна **и людям** (Lightdash-панель), **и агентам** (`get_calibration`) —
дисциплинированный агент знает уровень доверия, а не верит слепо.

## Dependency-agent для крипты (`packages/agent`, бонус — структура уже есть)

Lagged-corr / Granger / mutual-info между рядами BTC и TON → «BTC опережает TON на N дней /
co-moves» с caveats (`correlation != causation`). Прямой торговый сигнал для бота.
В MVP — лёгкая версия; расширение — Фаза 1.

## Скоуп MVP

**In scope:** ingestion(Bybit, в инстанс-репо) · forecast(3 продукта на BTC/TON) ·
Lightdash-дашборды · MCP-serve · ladder-check · divergence-алерты · базовая калибровка ·
lead/lag BTC↔TON (лёгкая версия) · **one-command Docker bring-up** (`norn up` →
`deploy/docker-compose.yml`, локальный ClickHouse в контейнере).

**Отложено:** полноценный `[META]` Postgres (на старте — YAML + ClickHouse, как в
`monorepo-and-data-model.md` §3) · UI реестра метрик · мульти-warehouse · LLM-объяснение
драйверов как core (остаётся experimental до evidence-gate) · **облачные деплои
(GCP / AWS / k8s)** — архитектура контейнеризована под них с первого дня, но сам деплой вне MVP.

**Out of scope (наследуем Reject-список PRD):** свой BI/форк Lightdash, metric-registry UI,
Kafka, Prometheus realtime, доп. warehouse-коннекторы.

## NFR (наследуем 3 из PRD §Design constraints)

1. **Контракт раньше модели** — forecast-таблица и MCP-инструменты (вход/выход, единицы,
   горизонт) зафиксированы до кода воркера. Смена модели не ломает контракт.
2. **Калибровка — непрерывная отдельная задача** — без честных CI и переоценки bias прогноз
   бесполезен для алертинга/дисциплины.
3. **Производственная пригодность > SOTA** — предсказуемые стоимость/latency инференса,
   self-host без GPU-фермы. 2 символа × daily × context 512 — дёшево.

## Open questions

- Точный набор `metric_name` и нужен ли отдельный `FORECAST_JOB` под realized-vol vs вывод
  диапазона из квантилей price-path.
- Как Pi резолвит MCP-сервер при локальном Docker-развёртывании (URL/порт, сеть Pi↔хост);
  и тот же вопрос при будущем облачном деплое.
- Имя/нарратив MCP-инструментов под disciplined-агента (формулировки caveats).
- Транспорт ingestion → ClickHouse (нативный HTTP vs `clickhouse-connect` в инстанс-репо).

## DoD (этого дизайна)

1. Граница «платформа generic vs крипто-инстанс (submodule)» зафиксирована — выше.
2. Маппинг крипты на существующий ERD без структурных правок — выше.
3. Два интерфейса (Lightdash/люди, MCP/агенты) и список MCP-инструментов — выше.
4. Скоуп MVP и наследование NFR/Reject-списка из PRD — выше.
