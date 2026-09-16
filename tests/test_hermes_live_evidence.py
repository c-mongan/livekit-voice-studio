from __future__ import annotations

import pytest

from tests.integration.test_hermes_studio_live import (
    _assert_interruption_evidence,
    _ObservationLog,
    _streamed_assistant_text_observed,
)


def test_streamed_text_requires_non_final_assistant_transcription() -> None:
    observations = _ObservationLog()
    start = observations.transcript_count
    observations.record_transcription(1.0, "agent", "READY", final=True)

    assert not _streamed_assistant_text_observed(observations, start, "agent", "READY")

    observations.record_transcription(1.1, "agent", "REA", final=False)
    assert _streamed_assistant_text_observed(observations, start, "agent", "READY")


def test_interruption_boundary_proves_marker_before_stop_and_rejects_late_marker() -> None:
    observations = _ObservationLog()
    retired_start = observations.boundary(0.9)
    observations.record_transcription(1.0, "agent", "RETIRE-ME", final=False)
    observations.record_nonzero_audio(1.01)
    boundary = observations.boundary(1.02)

    observations.record_transcription(1.03, "agent", "RETIRE-ME", final=False)

    with pytest.raises(AssertionError, match="retired Hermes text"):
        _assert_interruption_evidence(
            observations,
            retired_start,
            boundary,
            agent_identity="agent",
            marker="RETIRE-ME",
            silence_limit_seconds=0.2,
        )


def test_interruption_boundary_rejects_audio_after_silence_gate() -> None:
    observations = _ObservationLog()
    retired_start = observations.boundary(0.9)
    observations.record_transcription(1.0, "agent", "RETIRE-ME", final=False)
    observations.record_nonzero_audio(1.01)
    boundary = observations.boundary(1.02)
    observations.record_nonzero_audio(1.19)
    observations.record_nonzero_audio(1.23)

    with pytest.raises(AssertionError, match="retired reply audio"):
        _assert_interruption_evidence(
            observations,
            retired_start,
            boundary,
            agent_identity="agent",
            marker="RETIRE-ME",
            silence_limit_seconds=0.2,
        )


def test_interruption_evidence_returns_last_post_stop_audio_delay() -> None:
    observations = _ObservationLog()
    retired_start = observations.boundary(0.9)
    observations.record_transcription(1.0, "agent", "RETIRE-ME", final=False)
    observations.record_nonzero_audio(1.01)
    boundary = observations.boundary(1.02)
    observations.record_nonzero_audio(1.08)
    observations.record_transcription(1.09, "agent", "cancelled", final=True)

    assert _assert_interruption_evidence(
        observations,
        retired_start,
        boundary,
        agent_identity="agent",
        marker="RETIRE-ME",
        silence_limit_seconds=0.2,
    ) == pytest.approx(0.06)


def test_interruption_evidence_rejects_activity_only_from_an_older_reply() -> None:
    observations = _ObservationLog()
    observations.record_transcription(0.8, "agent", "RETIRE-ME", final=False)
    observations.record_nonzero_audio(0.81)
    retired_start = observations.boundary(0.9)
    boundary = observations.boundary(1.0)

    with pytest.raises(AssertionError, match="not observed before interruption"):
        _assert_interruption_evidence(
            observations,
            retired_start,
            boundary,
            agent_identity="agent",
            marker="RETIRE-ME",
            silence_limit_seconds=0.2,
        )
