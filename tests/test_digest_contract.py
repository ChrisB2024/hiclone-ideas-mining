from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
    monkeypatch.setattr(digest_module.settings, "smtp_host", "smtp.invalid")
    monkeypatch.setattr(
        digest_module.settings,
        "digest_to_address",
        "founder@example.com",
    )

    async def should_not_send(function: object, *args: object) -> None:
        del function, args
        pytest.fail("delivery was attempted for a digest with no source URL")

    monkeypatch.setattr(digest_module.asyncio, "to_thread", should_not_send)

    with pytest.raises(ValueError, match="source URL"):
        await digest_module.send_digest()

    assert persisted == []
    assert list(tmp_path.iterdir()) == []


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
    monkeypatch.setattr(digest_module.settings, "smtp_host", "smtp.invalid")
    monkeypatch.setattr(
        digest_module.settings,
        "digest_to_address",
        "founder@example.com",
    )

    async def should_not_send(function: object, *args: object) -> None:
        del function, args
        pytest.fail("delivery was attempted for rejected model output")

    monkeypatch.setattr(digest_module.asyncio, "to_thread", should_not_send)

    with pytest.raises(ValueError):
        await digest_module.send_digest()

    assert persisted == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("prose", ["", "   \n\t"])
def test_validate_model_output_rejects_blank_text(prose: str) -> None:
    with pytest.raises(ValueError, match="no text"):
        digest_module.validate_model_output(prose, "end_turn")


def test_validate_model_output_accepts_complete_linked_text() -> None:
    digest_module.validate_model_output(
        '> "A useful quote" https://example.com/source-thread',
        "end_turn",
    )


def test_validate_model_output_requires_a_link_for_each_markdown_quote() -> None:
    prose = (
        '## INSURANCE\n> "Linked quote" https://example.com/one\n\n'
        '## REAL ESTATE\n> "Unlinked quote"'
    )

    with pytest.raises(ValueError, match="source URL"):
        digest_module.validate_model_output(prose, "end_turn")


@pytest.mark.parametrize(
    ("prose", "expected"),
    [
        (
            '> "First line\n> second line" https://example.com/multiline',
            ['> "First line\n> second line" https://example.com/multiline'],
        ),
        (
            '> "Quoted pain"\n\n— source https://example.com/attribution',
            ['> "Quoted pain"\n— source https://example.com/attribution'],
        ),
        (
            '>> "Nested quote" https://example.com/nested',
            ['>> "Nested quote" https://example.com/nested'],
        ),
        ("Plain prose https://example.com/source", []),
    ],
)
def test_quote_blocks_supports_documented_markdown_shapes(
    prose: str,
    expected: list[str],
) -> None:
    assert digest_module._quote_blocks(prose) == expected
    digest_module.validate_model_output(prose, "end_turn")


def test_validate_model_output_rejects_one_unlinked_quote_among_ten() -> None:
    prose = "\n\n".join(
        (
            f'> "Quote {number}" https://example.com/{number}'
            if number != 7
            else '> "Quote 7 is missing its link"'
        )
        for number in range(1, 11)
    )

    with pytest.raises(ValueError, match="Quote 7 is missing its link"):
        digest_module.validate_model_output(prose, "end_turn")


def test_validate_model_output_enforces_links_when_quotes_are_not_blockquotes() -> None:
    prose = (
        "## INSURANCE\n"
        'Strongest quote: "Linked pain" https://example.com/linked\n\n'
        "## REAL ESTATE\n"
        'Strongest quote: "Unlinked pain"'
    )

    with pytest.raises(ValueError, match="source URL"):
        digest_module.validate_model_output(prose, "end_turn")


@pytest.mark.asyncio
async def test_gather_digest_input_returns_ids_for_the_rendered_clusters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clusters = {
        "insurance": [{
            "id": 11,
            "label": "Quote re-keying",
            "score": 4.2,
            "distinct_authors": 3,
            "member_count": 4,
            "last_seen_at": datetime(2026, 7, 30, tzinfo=UTC),
        }],
        "real_estate": [{
            "id": 12,
            "label": "Listing re-keying",
            "score": 3.8,
            "distinct_authors": 2,
            "member_count": 2,
            "last_seen_at": datetime(2026, 7, 29, tzinfo=UTC),
        }],
    }

    async def fake_top_clusters(vertical: str, n: int) -> list[dict[str, object]]:
        del n
        return clusters[vertical]

    class MemberResult:
        def mappings(self) -> MemberResult:
            return self

        def all(self) -> list[dict[str, object]]:
            return [{
                "pain_point": "Operators manually re-key records.",
                "who_has_it": "operator",
                "current_workaround": "copy and paste",
                "willingness_to_pay_signal": "implied",
                "excerpt": "This takes hours.",
                "url": "https://example.com/thread",
            }]

    class MemberSession:
        async def execute(
            self,
            statement: object,
            parameters: object | None = None,
        ) -> MemberResult:
            del statement, parameters
            return MemberResult()

    @asynccontextmanager
    async def fake_get_session() -> Any:
        yield MemberSession()

    monkeypatch.setattr(digest_module, "top_clusters", fake_top_clusters)
    monkeypatch.setattr(digest_module, "get_session", fake_get_session)

    prompt, cluster_ids = await digest_module.gather_digest_input(
        datetime(2026, 7, 20, tzinfo=UTC),
        datetime(2026, 7, 31, tzinfo=UTC),
    )

    assert cluster_ids == [11, 12]
    assert prompt.index("Quote re-keying") < prompt.index("Listing re-keying")
    assert prompt.count("https://example.com/thread") == 2


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
