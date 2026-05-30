"""
packages/forecast/src/norn_forecast/__init__.py

Пакет norn_forecast — слой прогнозирования платформы norn. Извлекает временные
ряды по сегментам из ClickHouse-контракта, строит квантильные прогнозы
(baseline seasonal-naive или TimesFM 2.5), считает rolling-origin калибровку и
отдаёт результаты агенту через MCP-инструменты поверх контракт-таблиц.
"""
