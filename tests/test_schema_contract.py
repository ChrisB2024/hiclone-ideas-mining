from __future__ import annotations

import importlib
import sys
from types import ModuleType

import pytest
from sqlalchemy import CheckConstraint, String, UniqueConstraint

from ideas_mining import config


def _stub_pgvector(monkeypatch: pytest.MonkeyPatch) -> None:
    pgvector = ModuleType("pgvector")
    pgvector_sqlalchemy = ModuleType("pgvector.sqlalchemy")
    pgvector_sqlalchemy.Vector = lambda dimension: String()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pgvector", pgvector)
    monkeypatch.setitem(sys.modules, "pgvector.sqlalchemy", pgvector_sqlalchemy)


def _import_models(
    monkeypatch: pytest.MonkeyPatch, embedding_dim: int
) -> ModuleType:
    _stub_pgvector(monkeypatch)
    monkeypatch.setattr(config.settings, "embedding_dim", embedding_dim)
    sys.modules.pop("ideas_mining.db.models", None)
    return importlib.import_module("ideas_mining.db.models")


def test_unset_embedding_dimension_fails_fast_and_names_oq1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match=r"OQ-1"):
        _import_models(monkeypatch, embedding_dim=0)


def test_declared_unique_constraints_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    models = _import_models(monkeypatch, embedding_dim=3)

    raw_unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in models.RawPost.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    signal_unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in models.EnrichedSignal.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("source", "external_id") in raw_unique_columns
    assert ("raw_post_id",) in signal_unique_columns


def test_distinct_authors_check_is_a_database_constraint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = _import_models(monkeypatch, embedding_dim=3)
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in models.Cluster.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert checks["ck_clusters_authors_lte_members"] == (
        "distinct_authors <= member_count"
    )
