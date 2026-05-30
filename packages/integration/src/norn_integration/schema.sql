-- forecast_run: реестр запусков прогнозного пайплайна. Одна строка на прогон
-- (статус, модель/версия, тайминги, сводка по сегментам). Корневой ключ
-- forecast_run_id, к которому привязаны точки и метрики качества ниже.
CREATE TABLE IF NOT EXISTS forecast_run (
    forecast_run_id String,
    forecast_job    String,
    status          String,
    model_name      String,
    model_version   String,
    started_at      DateTime,
    finished_at     Nullable(DateTime),
    segments_total  UInt32,
    segments_skipped UInt32,
    error           Nullable(String)
) ENGINE = MergeTree ORDER BY (forecast_run_id, started_at);

-- forecast_point: сами прогнозные значения — по точке на (метрика, сегмент,
-- шаг горизонта). Хранит центральную оценку y_hat, перцентили p10/p50/p90 и
-- фактическое значение y_actual (заполняется позже для оценки качества).
CREATE TABLE IF NOT EXISTS forecast_point (
    forecast_run_id String,
    metric_name     String,
    segment_key     String,
    forecast_ts     DateTime,
    horizon_step    UInt16,
    y_hat           Float64,
    p10             Float64,
    p50             Float64,
    p90             Float64,
    y_actual        Nullable(Float64),
    model_name      String,
    created_at      DateTime DEFAULT now()
) ENGINE = MergeTree ORDER BY (metric_name, segment_key, forecast_ts);

-- forecast_segment: агрегированное качество прогноза по каждому сегменту в
-- рамках прогона — метрики ошибки (wape, mape, bias), покрытие интервалов
-- (coverage), объём ряда (n_points) и признак разреженности (is_sparse).
CREATE TABLE IF NOT EXISTS forecast_segment (
    forecast_run_id String,
    metric_name     String,
    segment_key     String,
    n_points        UInt32,
    is_sparse       UInt8,
    wape            Float64,
    mape            Float64,
    coverage        Float64,
    bias            Float64,
    created_at      DateTime DEFAULT now()
) ENGINE = MergeTree ORDER BY (metric_name, segment_key, forecast_run_id);

-- metric_dependency: обнаруженные связи между сегментами метрики — результат
-- пайплайна анализа зависимостей. Строка описывает направленную связь
-- source -> target с лагом, методом, силой (score), направлением и
-- статистикой значимости (p_value, confidence) на заданном временном окне.
CREATE TABLE IF NOT EXISTS metric_dependency (
    analysis_run_id String,
    metric_name     String,
    source_segment  String,
    target_segment  String,
    method          String,
    lag             Int16,
    score           Float64,
    direction       String,
    p_value         Nullable(Float64),
    confidence      Float64,
    window_start    DateTime,
    window_end      DateTime,
    created_at      DateTime DEFAULT now()
) ENGINE = MergeTree ORDER BY (metric_name, target_segment, source_segment, created_at);

-- dependency_explanation: интерпретация связей из metric_dependency,
-- сгенерированная LLM. Хранит вердикт о реальности связи (is_real), текстовое
-- объяснение, оговорки (caveats) и заметку об изменениях (change_note) с
-- указанием использованной модели (llm_model).
CREATE TABLE IF NOT EXISTS dependency_explanation (
    analysis_run_id String,
    metric_name     String,
    source_segment  String,
    target_segment  String,
    lag             Int16,
    direction       String,
    is_real         UInt8,
    confidence      Float64,
    explanation     String,
    caveats         String,
    change_note     String,
    llm_model       String,
    created_at      DateTime DEFAULT now()
) ENGINE = MergeTree ORDER BY (metric_name, target_segment, source_segment, created_at);
