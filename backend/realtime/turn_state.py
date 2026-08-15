from enum import StrEnum


class VoiceTurnState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    RECOGNIZING = "recognizing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    DRAINING = "draining"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: dict[VoiceTurnState, frozenset[VoiceTurnState]] = {
    VoiceTurnState.IDLE: frozenset({VoiceTurnState.LISTENING}),
    VoiceTurnState.LISTENING: frozenset(
        {VoiceTurnState.RECOGNIZING, VoiceTurnState.CANCELLED, VoiceTurnState.IDLE}
    ),
    VoiceTurnState.RECOGNIZING: frozenset(
        {VoiceTurnState.THINKING, VoiceTurnState.CANCELLED, VoiceTurnState.IDLE}
    ),
    VoiceTurnState.THINKING: frozenset(
        {VoiceTurnState.SPEAKING, VoiceTurnState.CANCELLED, VoiceTurnState.IDLE}
    ),
    VoiceTurnState.SPEAKING: frozenset(
        {VoiceTurnState.DRAINING, VoiceTurnState.CANCELLED, VoiceTurnState.IDLE}
    ),
    VoiceTurnState.DRAINING: frozenset(
        {VoiceTurnState.IDLE, VoiceTurnState.CANCELLED}
    ),
    VoiceTurnState.CANCELLED: frozenset({VoiceTurnState.IDLE}),
}


class InvalidVoiceTurnTransition(RuntimeError):
    pass


class VoiceTurnStateMachine:
    def __init__(self) -> None:
        self._state = VoiceTurnState.IDLE

    @property
    def state(self) -> VoiceTurnState:
        return self._state

    def transition(self, target: VoiceTurnState) -> None:
        if target == self._state:
            return
        if target not in _ALLOWED_TRANSITIONS[self._state]:
            raise InvalidVoiceTurnTransition(f"{self._state.value} -> {target.value}")
        self._state = target

    def cancel(self) -> None:
        if self._state != VoiceTurnState.IDLE:
            self.transition(VoiceTurnState.CANCELLED)

    def reset(self) -> None:
        if self._state == VoiceTurnState.IDLE:
            return
        if self._state != VoiceTurnState.CANCELLED:
            self._state = VoiceTurnState.CANCELLED
        self.transition(VoiceTurnState.IDLE)
