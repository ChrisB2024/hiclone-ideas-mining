from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import ideas_mining.digest as digest_module


class PreviousDigestResult:
    def scalar_one_or_none(self) -> None:
        return None


class FakeDigestSession:
    def __init__(self, persisted: list[object], role: str) -> None:
        self.persisted = persisted
        self.role = role

    async def execute(
        self, statement: object, parameters: object | None = None
    ) -> PreviousDigestResult:
        del statement, parameters
        return PreviousDigestResult()

    def add(self, value: object) -> None:
        assert self.role == "write"
        self.persisted.append(value)

    async def flush(self) -> None:
        assert self.role == "write"
        self.persisted[-1].id = 41


def _install_digest_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    prose: str | None,
    *,
    stop_reason: str = "end_turn",
) -> list[object]:
    persisted: list[object] = []
    sessions = [
        FakeDigestSession(persisted, "read"),
        FakeDigestSession(persisted, "write"),
    ]

    @asynccontextmanager
    async def fake_get_session() -> Any:
        yield sessions.pop(0)

    async def fake_gather(period_start: object, period_end: object) -> str:
        del period_start, period_end
        return (
            "## INSURANCE\n"
            "quote candidate\n"
            "url: https://example.com/source-thread\n"
            "## REAL ESTATE\n(no clusters cleared the bar this week)"
        )

    async def fake_header(period_start: object, period_end: object) -> str:
        del period_start, period_end
        return "# Pain digest"

    async def fake_top_clusters(vertical: str, n: int) -> list[object]:
        del vertical, n
        return []

    class FakeMessages:
        async def create(self, **kwargs: object) -> SimpleNamespace:
            del kwargs
            return SimpleNamespace(
                content=(
                    []
                    if prose is None
                    else [SimpleNamespace(type="text", text=prose)]
                ),
                stop_reason=stop_reason,
            )

    class FakeClient:
        messages = FakeMessages()

        async def close(self) -> None:
            return None

    monkeypatch.setattr(digest_module, "get_session", fake_get_session)
    monkeypatch.setattr(digest_module, "gather_digest_input", fake_gather)
    monkeypatch.setattr(digest_module, "render_header", fake_header)
    monkeypatch.setattr(digest_module, "top_clusters", fake_top_clusters)
    monkeypatch.setattr(
        digest_module,
        "AsyncAnthropic",
        lambda **kwargs: FakeClient(),
    )
    monkeypatch.setattr(digest_module.settings, "digest_dir", str(tmp_path))
    monkeypatch.setattr(digest_module.settings, "smtp_host", "")
    monkeypatch.setattr(digest_module.settings, "digest_to_address", "")
    return persisted


@pytest.mark.asyncio
async def test_digest_refuses_model_output_that_drops_every_source_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    persisted = _install_digest_fakes(
        monkeypatch,
        tmp_path,
        prose='## INSURANCE\n> "A useful quote with no source link"',
    )

    with pytest.raises(ValueError, match="source URL"):
        await digest_module.send_digest()

    assert persisted == []


@pytest.mark.parametrize(
    ("prose", "stop_reason"),
    [
        (None, "end_turn"),
        ("Partial digest https://example.com/source-thread", "max_tokens"),
    ],
)
@pytest.mark.asyncio
async def test_digest_refuses_empty_or_truncated_model_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    prose: str | None,
    stop_reason: str,
) -> None:
    persisted = _install_digest_fakes(
        monkeypatch,
        tmp_path,
        prose,
        stop_reason=stop_reason,
    )

    with pytest.raises(ValueError):
        await digest_module.send_digest()

    assert persisted == []


@pytest.mark.asyncio
async def test_digest_persists_cluster_ids_from_the_same_ranking_as_its_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    persisted = _install_digest_fakes(
        monkeypatch,
        tmp_path,
        prose="Digest https://example.com/source-thread",
    )

    async def fake_gather(
        period_start: object,
        period_end: object,
    ) -> tuple[str, list[int]]:
        del period_start, period_end
        return (
            "Prompt built from clusters 11 and 12. "
            "https://example.com/source-thread",
            [11, 12],
        )

    later_rankings = iter([[{"id": 21}], [{"id": 22}]])

    async def changed_top_clusters(vertical: str, n: int) -> list[dict[str, int]]:
        del vertical, n
        return next(later_rankings)

    monkeypatch.setattr(digest_module, "gather_digest_input", fake_gather)
    monkeypatch.setattr(digest_module, "top_clusters", changed_top_clusters)

    await digest_module.send_digest()

    assert persisted[0].cluster_ids == [11, 12]


@pytest.mark.asyncio
async def test_smtp_failure_keeps_persisted_digest_and_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    persisted = _install_digest_fakes(
        monkeypatch,
        tmp_path,
        prose=(
            '## INSURANCE\n> "A useful quote" '
            "https://example.com/source-thread"
        ),
    )
    monkeypatch.setattr(digest_module.settings, "smtp_host", "smtp.invalid")
    monkeypatch.setattr(
        digest_module.settings,
        "digest_to_address",
        "founder@example.com",
    )

    async def failing_to_thread(function: object, *args: object) -> None:
        del function, args
        raise OSError("SMTP unavailable")

    monkeypatch.setattr(digest_module.asyncio, "to_thread", failing_to_thread)

    digest_id = await digest_module.send_digest()

    assert digest_id == 41
    assert len(persisted) == 1
    assert persisted[0].delivered is False
    assert list(tmp_path.glob("*.md"))
