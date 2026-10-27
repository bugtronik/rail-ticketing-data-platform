"""`rail-generator history` on a source that already holds data.

`docker compose up` runs the seed on every start, so `--if-empty` must turn
"already seeded" into a success. Without the flag it stays an error: a
second history would collide with the ids of the first one.
No database here: the connection and the emptiness check are replaced.
"""

import pytest

from rail_ticketing.generator import cli


@pytest.fixture
def seeded_source(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """A source that is not empty; records whether history generation ran."""
    calls: list[int] = []
    monkeypatch.setattr(cli.db, "connect", lambda: object())
    monkeypatch.setattr(cli.db, "source_is_empty", lambda conn: False)
    monkeypatch.setattr(cli, "generate_history", lambda *args, **kwargs: calls.append(1))
    return calls


def test_history_refuses_a_source_that_already_holds_data(seeded_source: list[int]) -> None:
    assert cli.main(["history"]) == 1
    assert seeded_source == []


def test_if_empty_turns_an_already_seeded_source_into_a_success(
    seeded_source: list[int],
) -> None:
    assert cli.main(["history", "--if-empty"]) == 0
    assert seeded_source == []
