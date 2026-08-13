"""In-process STT/TTS/LLM test doubles for the pipeline tests.

These implement the real livekit.agents provider ABCs (not just duck-typed
stand-ins), so AgentSession drives them exactly like it would a real plugin:
streaming STT/TTS, no VAD, no network, fully deterministic. Patterned after
the installed livekit-plugins-deepgram/anthropic `_run()` implementations
(see backend/.venv/.../livekit/plugins/{deepgram,anthropic}) but with a
provider response synthesized in-memory instead of over the wire.
"""

from __future__ import annotations

from livekit import rtc
from livekit.agents import llm, stt, tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions


class _MockRecognizeStream(stt.RecognizeStream):
    def __init__(self, *, stt: MockSTT, conn_options: APIConnectOptions) -> None:
        super().__init__(stt=stt, conn_options=conn_options)
        self._transcript = stt.transcript

    async def _run(self) -> None:
        got_audio = False
        async for item in self._input_ch:
            if isinstance(item, rtc.AudioFrame) and not got_audio:
                got_audio = True
                self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH))
                self._event_ch.send_nowait(
                    stt.SpeechEvent(
                        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                        alternatives=[stt.SpeechData(language="en", text=self._transcript)],
                    )
                )
                self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH))
        # loop drains remaining frames until the stream is closed (turn reset
        # or session close); nothing else to do once the transcript is sent.


class MockSTT(stt.STT):
    """Streaming STT double: emits a canned final transcript on first audio."""

    def __init__(self, *, transcript: str = "hello jarvis") -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=True, interim_results=False))
        self.transcript = transcript

    async def _recognize_impl(self, buffer, *, language=NOT_GIVEN, conn_options):
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language="en", text=self.transcript)],
        )

    def stream(
        self, *, language=NOT_GIVEN, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> stt.RecognizeStream:
        return _MockRecognizeStream(stt=self, conn_options=conn_options)


class _MockSynthesizeStream(tts.SynthesizeStream):
    def __init__(self, *, tts: MockTTS, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, conn_options=conn_options)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/pcm",
            stream=True,
        )
        buf = ""
        async for chunk in self._input_ch:
            if isinstance(chunk, str):
                if not buf:
                    output_emitter.start_segment(segment_id=utils.shortuuid())
                buf += chunk
            elif isinstance(chunk, self._FlushSentinel) and buf:
                # ~100ms of silence stands in for synthesized audio.
                silence = b"\x00\x00" * int(0.1 * self._tts.sample_rate)
                output_emitter.push(silence)
                output_emitter.end_segment()
                buf = ""


class MockTTS(tts.TTS):
    """Streaming TTS double: turns each pushed text segment into silence."""

    def __init__(self, *, sample_rate: int = 16000) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=1,
        )

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return self._synthesize_with_stream(text, conn_options=conn_options)

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.SynthesizeStream:
        return _MockSynthesizeStream(tts=self, conn_options=conn_options)


class _MockLLMStream(llm.LLMStream):
    async def _run(self) -> None:
        self._event_ch.send_nowait(
            llm.ChatChunk(
                id=utils.shortuuid(),
                delta=llm.ChoiceDelta(role="assistant", content=self._llm.reply),
            )
        )


class MockLLM(llm.LLM):
    """Single-shot LLM double: always replies with a fixed canned string."""

    def __init__(self, *, reply: str = "Hi there, how can I help?") -> None:
        super().__init__()
        self.reply = reply

    @property
    def model(self) -> str:
        return "mock-llm"

    @property
    def provider(self) -> str:
        return "mock"

    def chat(
        self,
        *,
        chat_ctx,
        tools=None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls=NOT_GIVEN,
        tool_choice=NOT_GIVEN,
        extra_kwargs=NOT_GIVEN,
    ) -> llm.LLMStream:
        return _MockLLMStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)
