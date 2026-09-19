"""Bound synthetic follow-up evidence to a new agent speaking interval."""


class ReplyObservation:
    def __init__(self) -> None:
        self.generation = 0
        self.armed = False
        self.speaking = False
        self.samples = 0
        self.text_matched = False

    @property
    def token(self) -> int | None:
        return self.generation if self.armed else None

    def arm(self) -> None:
        self.generation += 1
        self.armed = True
        self.speaking = False
        self.samples = 0
        self.text_matched = False

    def state_changed(self, state: str) -> None:
        if self.armed:
            self.speaking = state == "speaking"

    def audio(self, nonzero_samples: int) -> None:
        if self.armed and self.speaking:
            self.samples += nonzero_samples

    def transcript(self, token: int | None, text: str) -> None:
        # The token belongs to stream creation, not the arrival of its last chunk.
        if self.armed and token == self.generation and "recovery works" in text.lower():
            self.text_matched = True

    @property
    def complete(self) -> bool:
        return self.text_matched and self.samples >= 2400
