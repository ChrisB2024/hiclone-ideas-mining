from __future__ import annotations

import logging

import pytest

import ideas_mining.worker as worker


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" debug ", logging.DEBUG),
        ("wArNiNg", logging.WARNING),
        ("unknown", logging.INFO),
        ("10", logging.INFO),
    ],
)
def test_resolve_log_level_normalizes_or_falls_back(
    raw: str,
    expected: int,
) -> None:
    assert worker.resolve_log_level(raw) == expected


@pytest.mark.asyncio
async def test_worker_startup_falls_back_for_invalid_log_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_levels: list[int] = []

    def validating_basic_config(**kwargs: object) -> None:
        configured_levels.append(logging._checkLevel(kwargs["level"]))

    monkeypatch.setattr(worker.settings, "log_level", " not-a-level ")
    monkeypatch.setattr(worker.logging, "basicConfig", validating_basic_config)

    await worker.startup({})

    assert configured_levels == [logging.INFO]
