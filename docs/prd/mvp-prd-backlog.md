# PRD: Norn MVP — forecasting add-on on the dbt → ClickHouse → TimesFM → Lightdash stack

> **This is the MVP of the GTM beachhead instance (delivery); platform code does not hardcode the domain — the metrics/dimensions/tables here are instance examples.**

**Platform invariant.** norn is a vendor-neutral, domain-AGNOSTIC forecasting platform: multi-segment forecasting of metrics and dependency discovery on top of any warehouse via a generic contract (`forecast_point`/`forecast_segment`), with configurable model/provider/DB and an MCP contract. Platform code (`packages/*`, `cli`) carries NO domain defaults — no built-in metrics, symbols, dimensions, ingestion formats, dashboards, prompts, nor LLM-model choice. All domain specificity lives in a separate instance repo (`norn-ett-instance` at `instances/ett` — the public open-source example instance, wired in as a submodule). The GTM focus (first target vertical) is delivery/marketplace/e-commerce: that is a market strategy, NOT a platform default. Any concrete domain in this document (delivery KPIs like delivered_orders/GMV, the ETT example metric `ot`, dimensions, transformations, model choice) is a labeled EXAMPLE pointing at the instance/vertical, not a platform requirement; domain details live in the instance repo.

PRD pattern: feature
Scale mode: solo
Maturity mode: MVP
Evidence-level: **L3 (Proxy) — DRAFT** (rationale — author-as-customer-zero dogfood + market facts, without discovery interviews; see strategy §4.3, §10 item 2)
Upstream artifacts consumed: strategy-review [yes], jobs-backlog [no — the JTBD map is taken from strategy §4], mechanics-shortlist [no], opportunity-map [no]

> This PRD is the receiving document for details carried over from
> `metric-intelligence-strategy.md` (carryover items 1, 3, 5). Architecture/mono-repo
> (item 2) lives in `../erd/monorepo-and-data-model.md` + `../erd/erd.mermaid` +
> `../erd/architecture.mermaid` — only a pointer here, not a copy.

## Product context

> Imported from the strategy (`metric-intelligence-strategy.md` §4–§6). Do not re-derive.

- **Business goal:** prove the value of a vendor-neutral forecasting layer on the author's own delivery data before investing in the moat (dependencies + MCP).
- **Segment:** Data/Analytics engineers and BizOps in data-heavy verticals (delivery/marketplace/e-com); customer-zero is the author himself.
- **Core Job / critical sequence:** forecast business KPIs across many cuts → actual-vs-forecast in one's own BI → (later) explanation of drivers → MCP. The MVP bottleneck is steps 2–3 (strategy §4.3).
- **Current solution/problem:** Prophet/in-house scripts + "eyeballing the dashboard"; no foundation worker on top of the warehouse and no standard way to write the forecast back and chart it in Lightdash.
- **Value mechanic:** "start doing unaddressed work" — zero-shot forecasting of multi-segment series on top of the warehouse as a dbt-native add-on (strategy §4.4).
- **Evidence + confidence:** L3 — proxy (dogfood + market facts). Trust in the forecast/calibration is not confirmed by user behavior beyond the author.

## Problem & outcome

- **What is blocked:** steps 2–3 of the critical sequence — "missing". Multi-segment forecasts with intervals and actual-vs-forecast in Lightdash are currently assembled by hand/with Prophet, without re-evaluating calibration.
- **Risk if ignored:** without proven forecast value, investing in the moat (dependencies+MCP) is premature.
- **Primary metric (validation):** share of forecasted metrics where the forecast yields an actionable signal on a delivery KPI (per the 7 value questions below).
- **Guardrail metrics:** calibration (actual interval coverage ≈ nominal), inference cost/latency on self-host without a GPU farm.
- **Non-goals:** an own BI/dashboard engine, a Lightdash fork, a metric-registry UI, Kafka, multi-model comparison, Prometheus realtime, additional warehouse connectors (all on the strategy's Reject list §4.5).

> **No domain defaults / Platform Genericity Checkpoint.** Platform code (`packages/*`, `cli`) must not hardcode metric names, dimensions, table patterns, ingestion formats, dashboards or prompts — all of that comes from the instance configuration. The same generic contract (`forecast_point`/`forecast_segment`, MCP tools) must work unchanged for another instance; the public ETT instance (`norn-ett-instance` at `instances/ett`) exercises this with the metric `ot`, dimensions `dataset`/`feature`, and segments like `dataset=ETTh1|feature=ot`. The delivery values below are an example of the GTM beachhead vertical, not the platform scope.

## Scope (Phase 0 — hard scope)

> Carried over from strategy §7 "Phase 0". In the strategy it stays a one-liner.

**In scope** (instance/provider are chosen by config; the values below are an example of the GTM-beachhead delivery instance, not the platform scope):

- 1 warehouse — **ClickHouse** (delivery-instance example).
- 1 BI — **Lightdash** (delivery-instance example).
- 1 model — **TimesFM 2.5** (delivery-instance example).
- 1–3 metrics — **delivered_orders, GMV, cancellation_rate** (delivery-instance example).
- 2 grains — **hourly / daily** (delivery-instance example).
- A narrow set of dimensions — **city, store_id, merchant_id** (delivery-instance example; start with `city × merchant` or `city × store` for the sake of sparse risk, strategy §9).
- Pipeline: `dbt metric in ClickHouse → TimesFM Python worker driven by a YAML config → forecast table → dbt actual-vs-forecast model → dashboards in Lightdash`.

**Status:** the pipeline above is **built** and runs end-to-end on the public open-source ETT instance (`instances/ett`): metric `ot`, grain `hourly`, dimensions `dataset`/`feature`, segments `dataset=ETTh1|feature=ot` and `dataset=ETTh2|feature=ot`, jobs `ot_baseline.yml` / `ot_timesfm.yml` / `ot_timesfm_xreg.yml`, plus `forecasts/deps/*.yml` for dependency discovery. A built-in `norn scheduler` (driven by `instances/ett/deploy/jobs.yml`) runs forecast/calibrate/deps on cron. What remains is user-facing value validation (the 7 questions), not the engine.

**Out of scope:** see Non-goals.

## Technical context (pointer, not a copy)

- The mono-repo layout (`packages/core` · `packages/integration` · `packages/forecast` · `packages/agent` · `packages/scheduler` + `cli`), tech stack (Python ≥3.12, FastAPI, dbt, ClickHouse, TimesFM 2.5, APScheduler, MCP, pydantic-ai), isolation of the torch/dbt environments, dbt via subprocess — **in `../erd/monorepo-and-data-model.md`**. (The TimesFM worker runs in its own pinned-3.12 container; the light platform image stays slim.)
- The logical data model — `../erd/erd.mermaid` (legend `[LD]`/`[CH]`/`[META]`); the sidecar component diagram — `../erd/architecture.mermaid`.

## Design constraints / NFR (the Uber DeepETT lesson)

> Carried over from strategy §2.1. Engineering constraints, not positioning.

1. **Contract before model.** Freeze the contract of the forecast table and the MCP tools (input/output, units, horizon) before writing worker code. Changing the model must not break the contract.
2. **Calibration is a continuous and separate task.** A forecast without honest confidence intervals and without periodic re-evaluation of calibration (systematic bias) is useless for alerting. Resolution and calibration are independent.
3. **Production fitness > SOTA on a benchmark.** Predictable inference cost/latency, self-host without a GPU farm. Prefer pre-aggregated fixed-size features over "pretty" architectures.

## Contract of the forecast table and the YAML forecast-job

> Carried over from strategy §10 item 3. Freeze before scaling (NFR-1).

**forecast table — `forecast_point` (written back to ClickHouse, read by the dbt actual-vs-forecast model). The contract was frozen and is now implemented in `packages/integration/.../schema.sql`; the live column names are below:**

| Field                       | Type             | Purpose                                                                                 |
| --------------------------- | ---------------- | --------------------------------------------------------------------------------------- |
| `forecast_run_id`           | String           | run identifier (joins to `forecast_run`)                                                 |
| `metric_name`               | String           | metric name (example (delivery instance): delivered_orders/GMV/cancellation_rate; ETT: `ot`) |
| `segment_key`               | String           | dimensions encoded as one key (ETT: `dataset=ETTh1\|feature=ot`)                         |
| `forecast_ts`               | DateTime         | timestamp of the forecast point                                                         |
| `horizon_step`              | Int              | step index within the horizon                                                           |
| `y_hat`                     | Float            | point forecast                                                                          |
| `p10` / `p50` / `p90`       | Float            | quantile bounds (default 0.1/0.5/0.9 — the confidence interval)                          |
| `y_actual`                  | Nullable(Float)  | actual value when known (also used for backtest pairs)                                   |
| `model_name`                | String           | model tag, e.g. `timesfm-2.5` / `baseline-seasonal-naive`                                |
| `created_at`                | DateTime         | when generated                                                                          |

> Run-level metadata (`forecast_job`, `status`, `model_version`, `started_at`/`finished_at`, `horizon` etc.) lives in the companion `forecast_run` registry; per-segment quality (`wape`, `mape`, `coverage`, `bias`, `is_sparse`) in `forecast_segment`. There are exactly 5 contract tables: `forecast_run`, `forecast_point`, `forecast_segment`, `metric_dependency`, `dependency_explanation`.

**YAML forecast-job (the "metric-description layer" grain in the MVP):**

> Example (delivery instance); the contract is identical for the public ETT instance with different values (see the ETT job examples below).

```yaml
metric: delivered_orders
source: clickhouse.analytics.fct_delivered_orders # dbt model/table
grain: hourly # hourly | daily
dimensions: [city, merchant_id] # start narrow (sparse risk)
horizon: 24 # grain steps
context_length: 512 # history window for TimesFM
model: timesfm-2.5
schedule: "0 * * * *" # recompute
```

The live public ETT example (`instances/ett/forecasts/ot_timesfm.yml`) uses the same `ForecastJob` schema:

```yaml
metric: ot
source: fct_ot          # dbt model / ClickHouse table
grain: hourly           # daily | hourly
dimensions: [dataset, feature]
horizon: 24             # grain steps
context_length: 512     # history window for TimesFM
seasonality: 24
model: timesfm-2.5      # or baseline-seasonal-naive (ot_baseline.yml)
transform: none         # none | log
schedule: "0 6 * * *"   # 5-field cron; a hint — the scheduler manifest is the source of truth
```

The `ot_timesfm_xreg.yml` variant additionally sets `use_dependencies: true`, which auto-attaches confirmed `source_leads` (from `dependency_explanation`) as TimesFM XReg covariates. Dependency jobs (`forecasts/deps/*.yml`) carry `source_segment`/`target_segment`/`max_lag` instead.

## Experiment plan

- **Archetype:** dogfood validation (a concierge notebook on the author's own delivery data) → transitions into **pre-build/discovery** to test trust (strategy §4.3).
- **Hypothesis:** If we give data/BizOps a zero-shot forecast of delivery KPIs with intervals right inside Lightdash, then the user will act on it (plan/react), because it closes the "missing" step of the critical sequence without a queue to a DS.
- **Audience:** the author (customer-zero) + 5–8 interviews with data/BizOps in delivery/marketplace.
- **Metric:** 7 value questions (below) + calibration as a guardrail.
- **Pre-committed decision rule:** if in 5–8 interviews they answer "I don't trust it without a manual check" → we mark driver explanation as experimental, keeping only correlations flagged with an uncertainty note (kill-threshold from strategy §4.3). Date: before the start of Phase 1.

## Acceptance criteria — DoD = 7 value questions

> The DoD is NOT "did TimesFM run", but answers to the 7 questions (strategy §7 Phase 0). Each is an observable result on real data. The engine, calibration (rolling-origin backtest → `forecast_segment`), XReg covariates and MCP serving are now implemented, so these questions are answerable; they remain unchecked because they require validation against user decisions, not more code.

- [ ] **1. Actionability** — the forecast gives the business user a useful signal on at least one of the 3 metrics (a recorded decision made based on the forecast).
- [ ] **2. Grain stability** — documented at which grain (hourly/daily) the forecast is stable and where it falls apart.
- [ ] **3. Sparse segments** — behavior measured on cuts with zeros/gaps; an aggregation threshold for rare segments is fixed.
- [ ] **4. Place of consumption** — confirmed whether the user wants to see actual-vs-forecast specifically in Lightdash.
- [ ] **5. Calibration** — actual interval coverage compared with the nominal; bias is re-evaluated, not fixed once.
- [ ] **6. Horizon** — the horizon actually used in decisions is determined.
- [ ] **7. Dimensions** — cuts meaningful for the decision are separated from noise.

## Validation phase (the only one — L3 DRAFT, without Launch)

- **Validation:** the MVP add-on is built (and exercised end-to-end on the public ETT instance, incl. calibration, dependency discovery, XReg covariates, the MCP server and a built-in scheduler); the remaining validation step is to run it on real vertical data (1–3 metrics) → pass the 7 value questions + 5–8 discovery interviews.
  - **success threshold:** ≥1 metric passes question 1 (actionable) with adequate calibration (question 5).
  - **pivot/stop trigger:** trust kill-threshold (see decision rule) → do not build LLM-based driver explanation as core, downgrade it to experimental.
- The Launch phase and the paid-acquisition plan are blocked until the evidence-gate is passed.

## Risks and open questions

- **Sparse segments:** on the full `city × store × merchant × courier_type × customer_tier × hour`, half of the series are zeros → degradation. Mitigation: start with `city × merchant`/`city × store`.
- **Trust in intervals/calibration:** without honest CIs the forecast is useless for decisions (NFR-2).
- **Open:** the name/narrative of the add-on; the precise boundary of what counts as a "useful signal" for each metric.

## What will lift the evidence-gate

5–8 discovery interviews with data/BizOps (strategy §10 item 2) yielding a fact/non-fact about trust in LLM-based driver explanation → raises evidence to L2 (reported behavior) and opens Phase 1 (moat). Route: `product-discovery-interviews`.

## DoD (of this PRD)

1. The Phase 0 scope is fixed as a single list (warehouse/BI/model/metrics/grain/dimensions) — done above.
2. The contract of the forecast table and the YAML forecast-job is defined before code (NFR-1) — done above.
3. The MVP DoD is expressed as 7 observable value questions with a kill-threshold — done above.
