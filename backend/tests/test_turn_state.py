import pytest

from backend.realtime.turn_state import (
    InvalidVoiceTurnTransition,
    VoiceTurnState,
    VoiceTurnStateMachine,
)


def test_voice_turn_happy_path_has_explicit_states() -> None:
    state = VoiceTurnStateMachine()

    for target in (
        VoiceTurnState.LISTENING,
        VoiceTurnState.RECOGNIZING,
        VoiceTurnState.THINKING,
        VoiceTurnState.SPEAKING,
        VoiceTurnState.DRAINING,
        VoiceTurnState.IDLE,
    ):
        state.transition(target)

    assert state.state == VoiceTurnState.IDLE


def test_voice_turn_rejects_skipping_from_idle_to_speaking() -> None:
    state = VoiceTurnStateMachine()

    with pytest.raises(InvalidVoiceTurnTransition, match="idle -> speaking"):
        state.transition(VoiceTurnState.SPEAKING)


def test_voice_turn_cancel_always_releases_back_to_idle() -> None:
    state = VoiceTurnStateMachine()
    state.transition(VoiceTurnState.LISTENING)
    state.transition(VoiceTurnState.RECOGNIZING)

    state.cancel()
    assert state.state == VoiceTurnState.CANCELLED
    state.reset()

    assert state.state == VoiceTurnState.IDLE
