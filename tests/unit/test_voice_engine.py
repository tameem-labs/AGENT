"""Unit tests for VoiceEngine and audio processing."""

from __future__ import annotations

import base64

from zyro.voice import (
    AudioChunk,
    VoiceEngine,
    VoiceSessionState,
)


def test_voice_engine_session_and_stt() -> None:
    engine = VoiceEngine()
    session = engine.get_or_create_session("sess-1")
    assert session.state is VoiceSessionState.IDLE

    # Stream intermediate audio chunk
    interm_chunk = AudioChunk(
        pcm_base64="AAAA",
        sample_rate=16000,
        is_final=False,
    )
    res_interm = engine.process_incoming_chunk("sess-1", interm_chunk)
    assert not res_interm.is_final
    session_after = engine.get_or_create_session("sess-1")
    assert session_after.state is VoiceSessionState.LISTENING

    # Stream final audio chunk
    final_chunk = AudioChunk(
        pcm_base64="AAAA",
        sample_rate=16000,
        is_final=True,
    )
    res_final = engine.process_incoming_chunk("sess-1", final_chunk)
    assert res_final.is_final
    assert len(res_final.text) > 0


def test_voice_engine_tts_and_barge_in() -> None:
    engine = VoiceEngine()
    tts_res = engine.synthesize_speech("sess-2", "This is a response from ZYRO.")
    assert tts_res.format == "wav"
    assert tts_res.duration_ms > 0
    # Valid base64 audio payload
    raw_wav = base64.b64decode(tts_res.audio_base64)
    assert raw_wav.startswith(b"RIFF")

    session = engine.get_or_create_session("sess-2")
    assert session.state is VoiceSessionState.SPEAKING

    # User speaks while agent is speaking -> barge-in interruption triggered
    interruption_chunk = AudioChunk(pcm_base64="AAAA", sample_rate=16000, is_final=False)
    engine.process_incoming_chunk("sess-2", interruption_chunk)
    interrupted_session = engine.get_or_create_session("sess-2")
    # After interruption, it transitions back to LISTENING for user command
    assert interrupted_session.state is VoiceSessionState.LISTENING

    # Explicit interruption
    engine.synthesize_speech("sess-3", "Hello")
    assert engine.get_or_create_session("sess-3").state is VoiceSessionState.SPEAKING
    engine.interrupt("sess-3")
    assert engine.get_or_create_session("sess-3").state is VoiceSessionState.INTERRUPTED


def test_voice_engine_status() -> None:
    engine = VoiceEngine()
    status = engine.status()
    assert status["status"] == "CONFIGURED"
    assert status["supports_barge_in"] is True
