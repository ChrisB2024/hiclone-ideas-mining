from __future__ import annotations

import ast
import importlib
import inspect
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from ideas_mining import cluster, filter as filter_module, score


PACKAGE_ROOT = Path(__file__).parents[1] / "ideas_mining"
VERTICAL_LITERALS = {"insurance", "real_estate"}


def _package_sources() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def test_every_builder_python_file_compiles() -> None:
    for path in _package_sources():
        compile(path.read_text(), str(path), "exec")


@pytest.mark.parametrize(
    ("module", "pure_name", "impure_name", "parameters"),
    [
        (
            filter_module,
            "classify",
            "run_filter",
            ["title", "body", "score", "author", "vertical_hint"],
        ),
        (
            score,
            "compute_score",
            "score_clusters",
            [
                "distinct_authors",
                "days_since_last_seen",
                "explicit_ratio",
                "implied_ratio",
                "buildable_ratio",
                "avg_relevance",
            ],
        ),
    ],
)
def test_pure_impure_split_is_real_and_db_free(
    module: ModuleType,
    pure_name: str,
    impure_name: str,
    parameters: list[str],
) -> None:
    pure = getattr(module, pure_name)
    impure = getattr(module, impure_name)

    assert not inspect.iscoroutinefunction(pure)
    assert inspect.iscoroutinefunction(impure)
    assert list(inspect.signature(pure).parameters) == parameters

    tree = ast.parse(Path(module.__file__).read_text())
    db_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith("ideas_mining.db")
    ]
    assert db_imports == []


def test_config_is_the_only_possible_direct_environment_reader() -> None:
    violations: list[str] = []

    for path in _package_sources():
        if path.name == "config.py":
            continue

        tree = ast.parse(path.read_text())
        os_aliases = {"os"}
        direct_names: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                os_aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "os"
                )
            elif isinstance(node, ast.ImportFrom) and node.module == "os":
                direct_names.update(alias.asname or alias.name for alias in node.names)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in os_aliases
                and node.attr in {"environ", "getenv", "getenvb"}
            ):
                violations.append(f"{path}:{node.lineno}")
            elif (
                isinstance(node, ast.Name)
                and node.id in direct_names
                and isinstance(node.ctx, ast.Load)
            ):
                violations.append(f"{path}:{node.lineno}")

    assert violations == []


def test_no_module_outside_config_branches_on_a_vertical_literal() -> None:
    violations: list[str] = []

    for path in _package_sources():
        if path.name == "config.py":
            continue

        tree = ast.parse(path.read_text())
        branch_expressions: list[ast.AST] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.IfExp, ast.While)):
                branch_expressions.append(node.test)
            elif isinstance(node, ast.comprehension):
                branch_expressions.extend(node.ifs)
            elif isinstance(node, ast.match_case):
                branch_expressions.append(node.pattern)
                if node.guard is not None:
                    branch_expressions.append(node.guard)

        for expression in branch_expressions:
            literals = {
                child.value
                for child in ast.walk(expression)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            }
            if literals & VERTICAL_LITERALS:
                violations.append(f"{path}:{expression.lineno}")

    assert violations == []


def test_nearest_centroid_query_keeps_the_vertical_partition_clause() -> None:
    normalized = " ".join(cluster.NEAREST_CENTROID_SQL.lower().split())
    assert "where vertical = :vertical" in normalized


@pytest.fixture
def worker_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    fake_arq = ModuleType("arq")

    def fake_cron(coroutine: object, **options: object) -> SimpleNamespace:
        return SimpleNamespace(coroutine=coroutine, options=options)

    fake_arq.cron = fake_cron  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "arq", fake_arq)
    sys.modules.pop("ideas_mining.worker", None)
    module = importlib.import_module("ideas_mining.worker")
    yield module
    sys.modules.pop("ideas_mining.worker", None)


def test_worker_functions_and_cron_targets_resolve_to_callables(
    worker_module: ModuleType,
) -> None:
    functions = worker_module.WorkerSettings.functions
    cron_targets = [
        cron_job.coroutine for cron_job in worker_module.WorkerSettings.cron_jobs
    ]

    assert functions
    assert all(callable(function) for function in functions)
    assert cron_targets == functions


def test_every_arq_function_accepts_ctx_first(worker_module: ModuleType) -> None:
    missing_context = [
        f"{function.__module__}.{function.__name__}"
        for function in worker_module.WorkerSettings.functions
        if next(iter(inspect.signature(function).parameters), None) != "ctx"
    ]

    assert missing_context == []
