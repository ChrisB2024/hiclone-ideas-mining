from __future__ import annotations

import signal
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

import ideas_mining.ingest.reddit as reddit_module
from ideas_mining.ingest.hackernews import _hit_to_row, _parse_created_at, strip_html
from ideas_mining.ingest.reddit import _author_name, _created_at
from ideas_mining.ingest.upsert import dedupe_rows


class HtmlStripTimedOut(TimeoutError):
    pass


def _raise_timeout(signum: int, frame: object) -> None:
    del signum, frame
    raise HtmlStripTimedOut


def test_strip_html_decodes_entities_and_preserves_paragraph_breaks() -> None:
    result = strip_html(
        "<p>First &#x27;quoted&#x27; paragraph</p>"
        "<p>Second paragraph<br>next line</p>"
    )

    assert result == "First 'quoted' paragraph\nSecond paragraph\nnext line"
    assert "<p>" not in result
    assert "&#x27;" not in result


def test_strip_html_handles_pathological_unclosed_tag_quickly() -> None:
    hostile = "<" + ("a" * 500_000)
    previous_handler = signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, 1.0)
    try:
        assert strip_html(hostile) == hostile
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def test_hn_mapping_has_null_hint_and_aware_timestamp() -> None:
    row = _hit_to_row(
        {
            "objectID": "42",
            "story_id": 7,
            "author": "",
            "title": None,
            "comment_text": "<p>Manual insurance quoting is tedious.</p>",
            "points": 3,
            "created_at": "2026-07-28T12:00:00Z",
        }
    )

    assert row is not None
    assert row["vertical_hint"] is None
    assert row["author"] is None
    assert row["created_at"].tzinfo is not None
    assert "<p>" not in row["body"]


@pytest.mark.parametrize("created_at", [None, "not-a-date", 123])
def test_hn_mapping_skips_malformed_created_at(created_at: object) -> None:
    hit = {
        "objectID": "bad-time",
        "author": "founder",
        "title": "Manual insurance work",
        "comment_text": None,
        "points": 1,
        "created_at": created_at,
    }

    assert _hit_to_row(hit) is None


@pytest.mark.parametrize("created_at", [None, 123, "not-a-date"])
def test_hn_timestamp_parser_rejects_unusable_values(created_at: object) -> None:
    assert _parse_created_at(created_at) is None


def test_hn_timestamp_parser_attaches_utc_to_naive_iso_value() -> None:
    created = _parse_created_at("2026-07-30T12:34:56")

    assert created is not None
    assert created.tzinfo is reddit_module.UTC
    assert created.utcoffset().total_seconds() == 0


def test_reddit_deleted_author_never_becomes_string_none() -> None:
    assert _author_name(None) is None
    assert _author_name(SimpleNamespace(name=None)) is None
    assert _author_name(SimpleNamespace(name="agent-1")) == "agent-1"


def test_reddit_epoch_is_aware_utc() -> None:
    created = _created_at(0)

    assert created.tzinfo is not None
    assert created.utcoffset().total_seconds() == 0


@pytest.mark.asyncio
async def test_reddit_submission_failure_does_not_discard_later_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeComments(list[object]):
        def __init__(self, *, fails: bool) -> None:
            super().__init__()
            self.fails = fails

        async def replace_more(self, limit: int) -> None:
            assert limit == 0
            if self.fails:
                raise RuntimeError("comment expansion failed")

    now = reddit_module.datetime.now(reddit_module.UTC)
    submissions = [
        SimpleNamespace(
            fullname="t3-first",
            created_utc=now.timestamp(),
            subreddit="Insurance",
            permalink="/r/Insurance/comments/first",
            author=SimpleNamespace(name="first-author"),
            title="First",
            selftext="First body",
            score=1,
            comments=FakeComments(fails=True),
        ),
        SimpleNamespace(
            fullname="t3-second",
            created_utc=now.timestamp(),
            subreddit="Insurance",
            permalink="/r/Insurance/comments/second",
            author=SimpleNamespace(name="second-author"),
            title="Second",
            selftext="Second body",
            score=2,
            comments=FakeComments(fails=False),
        ),
    ]

    class AsyncSubmissions:
        def __init__(self, values: list[SimpleNamespace]) -> None:
            self._values = iter(values)

        def __aiter__(self) -> AsyncSubmissions:
            return self

        async def __anext__(self) -> SimpleNamespace:
            try:
                return next(self._values)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class FakeSubreddit:
        def new(self, limit: int) -> AsyncSubmissions:
            del limit
            return AsyncSubmissions(submissions)

    class FakeReddit:
        async def subreddit(self, name: str) -> FakeSubreddit:
            assert name == "Insurance"
            return FakeSubreddit()

    captured: list[dict[str, object]] = []

    @asynccontextmanager
    async def fake_get_session() -> Any:
        yield object()

    async def fake_upsert(
        session: object,
        rows: list[dict[str, object]],
    ) -> int:
        del session
        captured.extend(rows)
        return len(rows)

    monkeypatch.setattr(reddit_module, "get_session", fake_get_session)
    monkeypatch.setattr(reddit_module, "upsert_raw_posts", fake_upsert)

    result = await reddit_module.ingest_subreddit(
        "Insurance",
        "insurance",
        reddit=FakeReddit(),
    )

    assert [row["external_id"] for row in captured] == ["t3-first", "t3-second"]
    assert result == {"fetched": 2, "inserted": 2, "skipped": 0}


@pytest.mark.asyncio
async def test_reddit_bad_submission_preserves_prior_and_later_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Comments(list[object]):
        async def replace_more(self, limit: int) -> None:
            assert limit == 0

    now = reddit_module.datetime.now(reddit_module.UTC).timestamp()

    def valid_submission(external_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            fullname=external_id,
            created_utc=now,
            subreddit="Insurance",
            permalink=f"/r/Insurance/comments/{external_id}",
            author=SimpleNamespace(name="author"),
            title="Valid",
            selftext="Valid body",
            score=1,
            comments=Comments(),
        )

    class BrokenSubmission:
        @property
        def created_utc(self) -> float:
            raise RuntimeError("malformed submission")

    class Listing:
        def __init__(self) -> None:
            self.values = iter([
                valid_submission("t3-before"),
                BrokenSubmission(),
                valid_submission("t3-after"),
            ])

        def __aiter__(self) -> Listing:
            return self

        async def __anext__(self) -> object:
            try:
                return next(self.values)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class Subreddit:
        def new(self, limit: int) -> Listing:
            del limit
            return Listing()

    class Reddit:
        async def subreddit(self, name: str) -> Subreddit:
            del name
            return Subreddit()

    captured: list[dict[str, object]] = []

    @asynccontextmanager
    async def fake_get_session() -> Any:
        yield object()

    async def fake_upsert(session: object, rows: list[dict[str, object]]) -> int:
        del session
        captured.extend(rows)
        return len(rows)

    monkeypatch.setattr(reddit_module, "get_session", fake_get_session)
    monkeypatch.setattr(reddit_module, "upsert_raw_posts", fake_upsert)

    await reddit_module.ingest_subreddit(
        "Insurance",
        "insurance",
        reddit=Reddit(),
    )

    assert [row["external_id"] for row in captured] == ["t3-before", "t3-after"]


@pytest.mark.asyncio
async def test_reddit_listing_failure_persists_rows_already_collected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Comments(list[object]):
        async def replace_more(self, limit: int) -> None:
            assert limit == 0

    submission = SimpleNamespace(
        fullname="t3-before-listing-failure",
        created_utc=reddit_module.datetime.now(reddit_module.UTC).timestamp(),
        subreddit="Insurance",
        permalink="/r/Insurance/comments/before-failure",
        author=SimpleNamespace(name="author"),
        title="Valid",
        selftext="Valid body",
        score=1,
        comments=Comments(),
    )

    class FailingListing:
        def __init__(self) -> None:
            self.calls = 0

        def __aiter__(self) -> FailingListing:
            return self

        async def __anext__(self) -> object:
            self.calls += 1
            if self.calls == 1:
                return submission
            raise RuntimeError("listing connection dropped")

    class Subreddit:
        def new(self, limit: int) -> FailingListing:
            del limit
            return FailingListing()

    class Reddit:
        async def subreddit(self, name: str) -> Subreddit:
            del name
            return Subreddit()

    captured: list[dict[str, object]] = []

    @asynccontextmanager
    async def fake_get_session() -> Any:
        yield object()

    async def fake_upsert(session: object, rows: list[dict[str, object]]) -> int:
        del session
        captured.extend(rows)
        return len(rows)

    monkeypatch.setattr(reddit_module, "get_session", fake_get_session)
    monkeypatch.setattr(reddit_module, "upsert_raw_posts", fake_upsert)

    result = await reddit_module.ingest_subreddit(
        "Insurance",
        "insurance",
        reddit=Reddit(),
    )

    assert [row["external_id"] for row in captured] == [
        "t3-before-listing-failure"
    ]
    assert result == {"fetched": 1, "inserted": 1, "skipped": 0}


def test_dedupe_rows_keeps_first_occurrence_and_input_order() -> None:
    rows = [
        {"source": "reddit", "external_id": "1", "body": "first"},
        {"source": "reddit", "external_id": "2", "body": "second"},
        {"source": "reddit", "external_id": "1", "body": "later duplicate"},
    ]

    assert dedupe_rows(rows) == rows[:2]
