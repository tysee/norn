"""Doc guards: the data-model ERD doc must document storage/retention + upgrade."""

from pathlib import Path

_DOC = Path(__file__).resolve().parents[1] / "docs" / "erd" / "monorepo-and-data-model.md"


def test_erd_doc_documents_partition_retention_and_upgrade():
    text = _DOC.read_text(encoding="utf-8")
    # storage/retention model
    assert "PARTITION BY toYYYYMM(created_at)" in text
    assert "forecast.retention_months" in text
    # drop+recreate upgrade path for existing tables
    assert "drop" in text and "schema-apply" in text
    assert "ALTER TABLE ... MODIFY TTL" in text
