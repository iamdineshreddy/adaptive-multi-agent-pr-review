"""Model metadata and offline DDL tests (no database required).

These verify that the ORM schema matches docs/DATABASE.md and that every table
compiles to PostgreSQL DDL -- a fast, dependency-free proxy for migration
validation.
"""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import Enum as SAEnum
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import dialect as pg_dialect
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql.schema import Table

from app.models import Base

EXPECTED_TABLES: set[str] = {
    "users",
    "repositories",
    "coding_standards",
    "pull_requests",
    "reviews",
    "review_iterations",
    "agents",
    "review_tasks",
    "agent_metrics",
    "findings",
    "finding_groups",
    "developer_feedback",
    "repository_memory",
    "embeddings",
    "system_metrics",
    "dead_letters",
}


def tables() -> dict[str, Table]:
    return Base.metadata.tables


def _table(name: str) -> Table:
    table = Base.metadata.tables.get(name)
    assert table is not None, f"missing table {name}"
    return table


def test_all_documented_tables_registered() -> None:
    assert set(Base.metadata.tables) >= EXPECTED_TABLES


def test_mappers_configure_cleanly() -> None:
    configure_mappers()


def test_every_table_compiles_to_postgres_ddl() -> None:
    for table_name in EXPECTED_TABLES:
        ddl = CreateTable(_table(table_name)).compile(dialect=pg_dialect())  # type: ignore[no-untyped-call]
        assert f"CREATE TABLE {table_name}" in str(ddl)


def _enum_column(table_name: str, column_name: str) -> SAEnum:
    column = _table(table_name).c[column_name]
    assert isinstance(column.type, SAEnum)
    return column.type


def test_enum_types_use_stable_postgres_names() -> None:
    enum_locations = [
        ("users", "role"),
        ("pull_requests", "state"),
        ("pull_requests", "risk_class"),
        ("reviews", "status"),
        ("reviews", "mode"),
        ("reviews", "risk_class"),
        ("review_tasks", "status"),
        ("findings", "severity"),
        ("findings", "publication_status"),
        ("findings", "feedback_result"),
        ("developer_feedback", "outcome"),
        ("developer_feedback", "source"),
    ]
    for table_name, column_name in enum_locations:
        column = _table(table_name).c[column_name]
        assert isinstance(column.type, SAEnum), (table_name, column_name)
        assert column.type.name, (table_name, column_name)


def test_enum_member_values_match_documentation() -> None:
    severity = _enum_column("findings", "severity")
    assert severity.enums == ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    review_status = _enum_column("reviews", "status")
    assert review_status.enums == [
        "RECEIVED",
        "QUEUED",
        "PROCESSING",
        "AGENTS_RUNNING",
        "CONSOLIDATING",
        "DECIDING",
        "PUBLISHED",
        "WAITING_FOR_FEEDBACK",
        "ITERATING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    ]


def test_primary_keys_are_uuid_id() -> None:
    for table_name in EXPECTED_TABLES:
        pk = list(_table(table_name).primary_key.columns)
        assert len(pk) == 1, table_name
        assert pk[0].name == "id", table_name
        assert str(pk[0].type).lower() == "uuid", table_name


def test_unique_constraints_present() -> None:
    expected_uc = {
        "pull_requests": "uq_pr_repo_number",
        "coding_standards": "uq_coding_standards_repository_id",
        "embeddings": "uq_embedding_resource_hash",
        "system_metrics": "uq_metric_sample",
        "review_iterations": "uq_iteration_review_round",
    }
    for table_name, uc_name in expected_uc.items():
        constraint_names = {c.name for c in _table(table_name).constraints}
        assert uc_name in constraint_names, table_name


def test_foreign_keys_enforce_table_relationships() -> None:
    fk_map = {
        "coding_standards": {"repositories"},
        "pull_requests": {"repositories"},
        "reviews": {"pull_requests", "repositories"},
        "review_iterations": {"reviews"},
        "review_tasks": {"reviews", "agents"},
        "agent_metrics": {"reviews", "agents", "review_tasks"},
        "findings": {"reviews", "repositories", "agents", "finding_groups"},
        "finding_groups": {"reviews", "findings"},
        "developer_feedback": {"findings", "reviews", "repositories"},
        "repository_memory": {"repositories"},
        "embeddings": {"repositories", "findings"},
        "system_metrics": {"repositories"},
        "dead_letters": {"reviews"},
    }
    for table_name, referred in fk_map.items():
        actual = {fk.column.table.name for fk in _table(table_name).foreign_keys}
        assert referred <= actual, table_name


def test_indexes_on_high_frequency_columns() -> None:
    expected_index_cols = [
        ("reviews", "status"),
        ("reviews", "repository_id"),
        ("review_tasks", "review_id"),
        ("review_tasks", "status"),
        ("findings", "review_id"),
        ("findings", "repository_id"),
        ("findings", "category"),
        ("findings", "file_path"),
    ]
    for table_name, column_name in expected_index_cols:
        table = _table(table_name)
        assert any(column_name in set(idx.columns.keys()) for idx in table.indexes), (
            table_name,
            column_name,
        )


def test_embeddings_uses_pgvector() -> None:
    vec = _table("embeddings").c.vector.type
    assert isinstance(vec, Vector)
    assert vec.dim == 1536


def test_repository_memory_singleton_per_repository() -> None:
    repo_memory = _table("repository_memory")
    unique_cols = {
        c.name
        for c in repo_memory.constraints
        if isinstance(c, UniqueConstraint) or getattr(c, "unique", False)
    }
    assert "uq_repository_memory_repository_id" in unique_cols


def test_cascade_delete_relationships_configured() -> None:
    review_mapper = next(
        m for m in Base.registry.mappers if m.class_.__tablename__ == "reviews"
    )
    for rel_name in ("iterations", "findings"):
        assert "delete-orphan" in review_mapper.relationships[rel_name].cascade


def test_reviews_track_arum_budget_and_decisions() -> None:
    reviews = _table("reviews")
    for column_name in (
        "priority_score",
        "budget_cap",
        "arum_version",
        "root_cause",
        "feedback_aggregate",
        "failure_reason",
        "started_at",
        "completed_at",
    ):
        assert column_name in reviews.c, column_name


def test_findings_carry_decision_features() -> None:
    findings = _table("findings")
    for column_name in (
        "arum_features",
        "arum_utility",
        "arum_version",
        "publication_status",
        "feedback_result",
        "feedback_labelled",
        "temporal_weight",
    ):
        assert column_name in findings.c, column_name
