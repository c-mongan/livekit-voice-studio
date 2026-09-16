from __future__ import annotations

import pytest

from tests.integration.test_hermes_studio_live import (
    _assert_interruption_evidence,
    _completed_tool_status_observed,
    _ObservationLog,
    _retired_text_absent_after,
    _streamed_assistant_text_observed,
)


def test_streamed_text_rejects_expected_text_from_final_event() -> None:
    observations = _ObservationLog()
    start = observations.transcript_count
    observations.record_transcription(1.0, "agent", "READY", final=True)
    observations.record_transcription(1.1, "agent", "REA", final=False)

    assert not _streamed_assistant_text_observed(observations, start, "agent", "READY")


def test_streamed_text_accepts_expected_text_accumulated_from_non_final_events() -> None:
    observations = _ObservationLog()
    start = observations.transcript_count
    observations.record_transcription(1.0, "agent", "REA", final=False)
    observations.record_transcription(1.1, "agent", "DY", final=False)

    assert _streamed_assistant_text_observed(observations, start, "agent", "READY")


def test_action_truth_requires_observed_successful_completed_tool_status() -> None:
    observations = _ObservationLog()
    observations.record_tool_status(
        {"phase": "started", "tool": "read_file", "preview": "HERMES-LIVE-FIXTURE-7F31"}
    )
    assert not _completed_tool_status_observed(observations, "HERMES-LIVE-FIXTURE-7F31")

    observations.record_tool_status(
        {
            "phase": "completed",
            "tool": "read_file",
            "preview": "HERMES-LIVE-FIXTURE-7F31",
            "error": False,
        }
    )
    assert _completed_tool_status_observed(observations, "HERMES-LIVE-FIXTURE-7F31")


def test_stale_truth_is_derived_from_events_after_exact_boundary() -> None:
    observations = _ObservationLog()
    observations.record_transcription(1.0, "agent", "RETIRE-ME", final=False)
    boundary = observations.boundary(1.1)
    assert _retired_text_absent_after(observations, boundary, "agent", "RETIRE-ME")

    observations.record_transcription(1.2, "agent", "RETIRE-ME", final=True)
    assert not _retired_text_absent_after(observations, boundary, "agent", "RETIRE-ME")


def test_interruption_evidence_rejects_final_only_marker_before_stop() -> None:
    observations = _ObservationLog()
    retired_start = observations.boundary(0.9)
    observations.record_transcription(1.0, "agent", "RETIRE-ME", final=True)
    observations.record_nonzero_audio(1.01)
    boundary = observations.boundary(1.02)

    with pytest.raises(AssertionError, match="non-final output was not observed"):
        _assert_interruption_evidence(
            observations,
            retired_start,
            boundary,
            agent_identity="agent",
            marker="RETIRE-ME",
            silence_limit_seconds=0.2,
        )


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


def test_interruption_evidence_ignores_continuity_audio_for_silence_gate() -> None:
    observations = _ObservationLog()
    retired_start = observations.boundary(0.9)
    observations.record_transcription(1.0, "agent", "RETIRE-ME", final=False)
    observations.record_nonzero_audio(1.01)
    stop_boundary = observations.boundary(1.02)
    observations.record_nonzero_audio(1.08)
    continuity_start = observations.boundary(1.25)
    observations.record_transcription(1.3, "agent", "violet", final=False)
    observations.record_nonzero_audio(1.31)

    assert _assert_interruption_evidence(
        observations,
        retired_start,
        stop_boundary,
        audio_end=continuity_start,
        agent_identity="agent",
        marker="RETIRE-ME",
        silence_limit_seconds=0.2,
    ) == pytest.approx(0.06)


def test_interruption_evidence_rejects_marker_arriving_during_continuity() -> None:
    observations = _ObservationLog()
    retired_start = observations.boundary(0.9)
    observations.record_transcription(1.0, "agent", "RETIRE-ME", final=False)
    observations.record_nonzero_audio(1.01)
    stop_boundary = observations.boundary(1.02)
    observations.record_nonzero_audio(1.08)
    continuity_start = observations.boundary(1.25)
    observations.record_transcription(1.3, "agent", "violet", final=False)
    observations.record_nonzero_audio(1.31)
    observations.record_transcription(1.4, "agent", "RETIRE-ME", final=True)

    with pytest.raises(AssertionError, match="retired Hermes text"):
        _assert_interruption_evidence(
            observations,
            retired_start,
            stop_boundary,
            audio_end=continuity_start,
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
