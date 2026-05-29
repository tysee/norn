# PRD: Norn MVP — forecasting add-on на стеке dbt → ClickHouse → TimesFM → Lightdash

PRD pattern: feature
Scale mode: solo
Maturity mode: MVP
Evidence-level: **L3 (Proxy) — DRAFT** (обоснование — dogfood автора-customer-zero + рыночные факты, без discovery-интервью; см. стратегию §4.3, §10 п.2)
Upstream artifacts consumed: strategy-review [yes], jobs-backlog [no — карта JTBD взята из стратегии §4], mechanics-shortlist [no], opportunity-map [no]

> Этот PRD — принимающий документ для деталей, вынесенных из
> `metric-intelligence-strategy.md` (пункты 1, 3, 5 переноса). Архитектура/моно-репо
> (пункт 2) живёт в `../erd/monorepo-and-data-model.md` + `../erd/erd.mermaid` +
> `../erd/architecture.mermaid` — здесь только указатель, не копия.

## Product context

> Импортировано из стратегии (`metric-intelligence-strategy.md` §4–§6). Не выводить заново.

- **Бизнес-задача:** доказать ценность вендор-нейтрального forecasting-слоя на собственных данных доставки до вложений в moat (зависимости + MCP).
- **Сегмент:** Data/Analytics-инженеры и BizOps в data-heavy вертикалях (доставка/маркетплейс/e-com); customer-zero — сам автор.
- **Core Job / critical sequence:** прогноз бизнес-KPI во множестве разрезов → actual-vs-forecast в своём BI → (позже) объяснение драйверов → MCP. Бутылочное горлышко MVP — шаги 2–3 (стратегия §4.3).
- **Current solution/problem:** Prophet/in-house скрипты + «глазами по дашборду»; нет foundation-воркера поверх warehouse и нет стандартного способа писать forecast обратно и рисовать в Lightdash.
- **Value mechanic:** «начать делать неохваченную работу» — zero-shot forecasting многосегментных рядов поверх warehouse как dbt-нативный add-on (стратегия §4.4).
- **Evidence + confidence:** L3 — proxy (dogfood + market facts). Доверие к прогнозу/калибровке не подтверждено поведением пользователей за пределами автора.

## Problem & outcome

- **Что заблокировано:** шаги 2–3 критической последовательности — «missing». Многосегментный прогноз с интервалами и actual-vs-forecast в Lightdash сейчас собираются руками/Prophet'ом, без переоценки калибровки.
- **Risk if ignored:** без доказанной ценности прогноза вложение в moat (зависимости+MCP) — преждевременно.
- **Primary metric (validation):** доля прогнозируемых метрик, где прогноз даёт actionable-сигнал на delivery-KPI (по 7 вопросам ценности ниже).
- **Guardrail metrics:** калибровка (фактическое покрытие интервалов ≈ номиналу), стоимость/latency инференса на self-host без GPU-фермы.
- **Non-goals:** свой BI/dashboard-движок, форк Lightdash, metric-registry UI, Kafka, мультимодельное сравнение, Prometheus realtime, доп. warehouse-коннекторы (всё — Reject-список стратегии §4.5).

## Scope (Фаза 0 — жёсткий скоуп)

> Перенесено из стратегии §7 «Фаза 0». В стратегии остаётся одностроком.

**In scope:**

- 1 warehouse — **ClickHouse**.
- 1 BI — **Lightdash**.
- 1 модель — **TimesFM 2.5**.
- 1–3 метрики — **delivered_orders, GMV, cancellation_rate**.
- 2 grain — **hourly / daily**.
- Узкий набор dimensions — **city, store_id, merchant_id** (начать с `city × merchant` или `city × store` ради sparse-риска, стратегия §9).
- Пайплайн: `dbt-метрика в ClickHouse → TimesFM Python-воркер по YAML-конфигу → forecast-таблица → dbt actual-vs-forecast модель → дашборды в Lightdash`.

**Out of scope:** см. Non-goals.

## Технический контекст (указатель, не копия)

- Раскладка моно-репо (`packages/integration` · `packages/forecast` · `packages/agent` + `cli`), тех-стек (Python 3.14+, FastAPI, dbt, ClickHouse, TimesFM), изоляция torch/dbt-окружений, dbt через subprocess — **в `../erd/monorepo-and-data-model.md`**.
- Логическая модель данных — `../erd/erd.mermaid` (легенда `[LD]`/`[CH]`/`[META]`); компонентная схема сайдкара — `../erd/architecture.mermaid`.

## Design constraints / NFR (урок Uber DeepETT)

> Перенесено из стратегии §2.1. Инженерные ограничения, не позиционирование.

1. **Контракт раньше модели.** Зафиксировать контракт forecast-таблицы и MCP-инструментов (вход/выход, единицы, горизонт) до написания кода воркера. Менять модель — нельзя ломать контракт.
2. **Калибровка — непрерывная и отдельная задача.** Прогноз без честных доверительных интервалов и без периодической переоценки калибровки (systematic bias) бесполезен для алертинга. Resolution и калибровка независимы.
3. **Производственная пригодность > SOTA на бенчмарке.** Предсказуемая стоимость/latency инференса, self-host без GPU-фермы. Предпочесть предагрегированные признаки фиксированного размера «красивым» архитектурам.

## Контракт forecast-таблицы и YAML forecast-job

> Перенесено из стратегии §10 п.3. Зафиксировать до масштабирования (NFR-1).

**forecast-таблица (запись обратно в ClickHouse, читается dbt actual-vs-forecast моделью):**

| Поле                        | Тип             | Назначение                                           |
| --------------------------- | --------------- | ---------------------------------------------------- |
| `metric_name`               | String          | имя метрики (delivered_orders/GMV/cancellation_rate) |
| `grain`                     | Enum(hour, day) | зерно                                                |
| `ts`                        | DateTime        | таймстемп точки прогноза                             |
| `<dim...>`                  | String          | city, store_id, merchant_id (по конфигу job)         |
| `yhat`                      | Float           | точечный прогноз                                     |
| `yhat_lower` / `yhat_upper` | Float           | границы доверительного интервала                     |
| `model` / `model_version`   | String          | TimesFM + версия (для воспроизводимости)             |
| `horizon`                   | Int             | горизонт в шагах grain                               |
| `run_id`                    | String          | идентификатор прогона                                |
| `generated_at`              | DateTime        | когда сгенерирован                                   |

**YAML forecast-job (зерно «слоя описания метрик» в MVP):**

```yaml
metric: delivered_orders
source: clickhouse.analytics.fct_delivered_orders # dbt-модель/таблица
grain: hourly # hourly | daily
dimensions: [city, merchant_id] # начать узко (sparse-риск)
horizon: 24 # шагов grain
context_length: 512 # окно истории для TimesFM
model: timesfm-2.5
schedule: "0 * * * *" # пересчёт
```

## Experiment plan

- **Archetype:** dogfood-валидация (concierge-ноутбук на собственных данных доставки) → переходит в **pre-build/discovery** для проверки доверия (стратегия §4.3).
- **Hypothesis:** Если дать data/BizOps zero-shot прогноз delivery-KPI с интервалами прямо в Lightdash, то пользователь будет действовать по нему (планировать/реагировать), потому что закрывается «missing»-шаг критической последовательности без очереди к DS.
- **Audience:** автор (customer-zero) + 5–8 интервью data/BizOps в доставке/маркетплейсе.
- **Metric:** 7 вопросов ценности (ниже) + калибровка как guardrail.
- **Pre-committed decision rule:** если на 5–8 интервью отвечают «не доверяю без ручной проверки» → объяснение драйверов помечаем experimental, оставляем только корреляции с пометкой неопределённости (kill-threshold из стратегии §4.3). Дата: до старта Фазы 1.

## Acceptance criteria — DoD = 7 вопросов ценности

> DoD НЕ «запустился ли TimesFM», а ответы на 7 вопросов (стратегия §7 Фаза 0). Каждый — наблюдаемый результат на реальных данных.

- [ ] **1. Actionability** — прогноз даёт бизнес-пользователю полезный сигнал хотя бы на одной из 3 метрик (зафиксированное решение, принятое по прогнозу).
- [ ] **2. Стабильность grain** — задокументировано, на каком grain (hourly/daily) прогноз стабилен, а где разваливается.
- [ ] **3. Sparse-сегменты** — измерено поведение на разрезах с нулями/пропусками; зафиксирован порог агрегации редких сегментов.
- [ ] **4. Место потребления** — подтверждено, хочет ли пользователь видеть actual-vs-forecast именно в Lightdash.
- [ ] **5. Калибровка** — фактическое покрытие интервалов сопоставлено с номиналом; bias переоценивается, а не фиксируется один раз.
- [ ] **6. Горизонт** — определён реально используемый в решениях горизонт.
- [ ] **7. Dimensions** — отделены значимые для решения разрезы от шума.

## Validation phase (единственная — L3 DRAFT, без Launch)

- **Validation:** собрать MVP-add-on на своих данных (1–3 метрики) → пройти 7 вопросов ценности + 5–8 discovery-интервью.
  - **success threshold:** ≥1 метрика проходит вопрос 1 (actionable) при адекватной калибровке (вопрос 5).
  - **pivot/stop trigger:** kill-threshold доверия (см. decision rule) → не строить LLM-объяснение драйверов как core, понизить до experimental.
- Launch-фаза и план платного привлечения — заблокированы до прохождения evidence-gate.

## Risks and open questions

- **Sparse-сегменты:** на полном `city × store × merchant × courier_type × customer_tier × hour` половина рядов — нули → деградация. Митигация: начать с `city × merchant`/`city × store`.
- **Доверие к интервалам/калибровке:** без честных CI прогноз бесполезен для решений (NFR-2).
- **Open:** имя/нарратив add-on'а; точная граница, что считается «полезным сигналом» по каждой метрике.

## Что снимет evidence-gate

5–8 discovery-интервью data/BizOps (стратегия §10 п.2) с фактом/не-фактом о доверии к LLM-объяснению драйверов → поднимает evidence до L2 (reported behavior) и открывает Фазу 1 (moat). Маршрут: `product-discovery-interviews`.

## DoD (этого PRD)

1. Scope Фазы 0 зафиксирован одним списком (warehouse/BI/модель/метрики/grain/dimensions) — выполнено выше.
2. Контракт forecast-таблицы и YAML forecast-job определён до кода (NFR-1) — выполнено выше.
3. DoD MVP выражен как 7 наблюдаемых вопросов ценности с kill-порогом — выполнено выше.
