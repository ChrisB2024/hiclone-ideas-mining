from __future__ import annotations

import importlib
from pathlib import Path
import tomllib

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from ideas_mining.config import settings


ROOT = Path(__file__).parents[1]


def test_async_sqlalchemy_runtime_dependency_is_declared() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    dependencies = [dependency.lower() for dependency in project["dependencies"]]

    assert any(
        dependency.startswith("greenlet")
        or dependency.startswith("sqlalchemy[asyncio]")
        for dependency in dependencies
    )


def test_initial_migration_dimension_matches_runtime_setting() -> None:
    migration = importlib.import_module(
        "migrations.versions.0001_initial_schema"
    )

    assert migration.EMBEDDING_DIM == settings.embedding_dim


def test_initial_migration_creates_vector_extension_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.0001_initial_schema"
    )
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda sql: events.append(("execute", str(sql))),
    )
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda name, *args, **kwargs: events.append(("table", name)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, *args, **kwargs: events.append(("index", name)),
    )

    migration.upgrade()

    assert events[0] == ("execute", "CREATE EXTENSION IF NOT EXISTS vector")


def test_deferred_hnsw_migration_is_not_alembic_head() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_current_head() == "0001"


def test_deferred_alembic_config_exposes_hnsw_as_its_head() -> None:
    config = Config(str(ROOT / "alembic.deferred.ini"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_current_head() == "0002"
    assert scripts.get_revision("0002").down_revision == "0001"


def test_hnsw_migration_uses_cosine_operator_class() -> None:
    source = (
        ROOT / "migrations/deferred/0002_hnsw_indexes.py"
    ).read_text()

    assert source.count("vector_cosine_ops") >= 2
    assert "vector_l2_ops" not in source


def test_deferred_hnsw_indexes_do_not_block_pipeline_writes() -> None:
    source = (
        ROOT / "migrations/deferred/0002_hnsw_indexes.py"
    ).read_text()
    normalized = " ".join(source.upper().split())

    assert normalized.count("CREATE INDEX CONCURRENTLY") >= 2
    assert "AUTOCOMMIT_BLOCK" in normalized


def test_local_compose_ports_are_bound_to_loopback() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()

    assert '"127.0.0.1:5432:5432"' in compose
    assert '"127.0.0.1:6379:6379"' in compose
