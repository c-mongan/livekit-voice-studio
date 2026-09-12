"""Sanitized errors: never include server bodies, input text, or audio."""


class VoiceboxError(Exception):
    pass


class VoiceboxConnectionError(VoiceboxError):
    pass


class VoiceboxTimeoutError(VoiceboxError):
    pass


class VoiceboxAPIError(VoiceboxError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProfileNotFoundError(VoiceboxError):
    pass


class AmbiguousProfileError(VoiceboxError):
    pass


class InvalidAudioError(VoiceboxError):
    pass


class ConfigurationError(VoiceboxError):
    pass


class ModelNotReadyError(VoiceboxAPIError):
    pass


class BackendUncertainError(VoiceboxError):
    def __init__(self) -> None:
        super().__init__(
            "Voicebox inference completion is unknown. Admission is blocked. "
            "Confirm the old backend has stopped (for example, restart Voicebox), "
            "then recreate the TTS client. Health success is not recovery."
        )


class ClientClosedError(VoiceboxError):
    pass


class ResponseLimitError(VoiceboxError):
    pass
