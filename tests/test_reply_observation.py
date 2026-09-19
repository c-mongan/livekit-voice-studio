from benchmarks.reply_observation import ReplyObservation


def test_old_transcript_and_draining_audio_cannot_prove_followup():
    reply = ReplyObservation()
    old_stream = reply.token
    reply.arm()
    reply.audio(48000)
    reply.transcript(old_stream, "Recovery works")
    assert reply.samples == 0
    assert not reply.text_matched
    reply.state_changed("speaking")
    reply.audio(2400)
    assert not reply.complete
    reply.transcript(reply.token, "Recovery works")
    assert reply.complete


def test_new_reply_audio_is_retained_even_when_fixture_finishes_later():
    reply = ReplyObservation()
    reply.arm()
    stream = reply.token
    reply.state_changed("speaking")
    reply.audio(2400)
    reply.state_changed("listening")
    reply.audio(48000)
    reply.transcript(stream, "Recovery works")
    assert reply.samples == 2400
    assert reply.complete


def test_rearming_rejects_previous_response_streams_and_samples():
    reply = ReplyObservation()
    reply.arm()
    previous = reply.token
    reply.state_changed("speaking")
    reply.audio(2400)
    reply.arm()
    reply.transcript(previous, "Recovery works")
    assert not reply.complete
    assert reply.samples == 0
    reply.transcript(reply.token, "Something else")
    assert not reply.text_matched
