"""Token tracker tests."""

from elsa_runtime.cost.tracker import TokenTracker


def test_token_tracker_instantiation():
    tracker = TokenTracker()
    assert tracker is not None
