"""CLI: hold real conversation turns with the deployed (or local) Jarvis agent
over a live LiveKit room, using recorded utterance WAVs as the "microphone"
input (T-9).

This is the tool that produces the >=50 real deployed turns that
`scripts/latency_report.py` (T-9 acceptance item 3) needs -- unlike the
worker's own `backend/var/latency.jsonl`, which only gets written when a real
participant actually talks to the agent, this script *is* that participant.

How it works:

1. Mints (or fetches, via `--token-url`'s `/api/token`) a LiveKit access token
   and connects to the room as a normal participant using `livekit.rtc`.
2. Publishes a microphone-source audio track backed by an `rtc.AudioSource`,
   fed 20ms 48kHz mono PCM16 frames decoded from each utterance WAV (resampled
   with the stdlib `audioop` module as needed) at real-time pace.
3. Subscribes to the data channel on topic "jarvis" (the wire contract pinned
   in `backend/src/jarvis/pipeline/events.py` / docs/DESIGN.md §Interfaces:
   `{"type":"agent_state","state":...}` and
   `{"type":"transcript","role":...,"text":...,"final":...}`) to detect
   turn boundaries: after an utterance finishes playing, a turn is "complete"
   once the agent_state transitions to "speaking" (first agent audio).
4. Records, per turn: the utterance file, the user transcript the agent
   *heard* (STT output -- the proof it understood real speech), the assistant
   transcript, wall-clock time-to-first-audio, and completed/timed-out.

Never hangs forever: connecting, waiting for the agent to join, and each
turn's wait for a reply are all bounded by `--timeout` (or a small multiple of
it as a hard-timeout safety net around the whole turn). Exits non-zero if the
agent never joins the room, or if any turn times out, so a silent stall can
never be reported as success.

Usage (from repo root or backend/):

    uv run python scripts/drive_turns.py --room smoke-test --turns 1 \\
        --utterances scripts/utterances --json /tmp/drive_turns.json

Requires `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` / `LIVEKIT_URL` in the
environment when `--token-url` is not given (local minting, mirrors
`backend/src/jarvis/server/app.py`'s `/api/token`).

COST WARNING (2026-08-15, see `_cartesia_spend_guard`): every turn this
script drives can trigger a real Cartesia TTS synthesis call on whatever
worker replies -- a `--turns 55` run against the deployed worker exhausted
a 20,000-credit/month free Cartesia tier in ~13 minutes
(.claude/NEEDS_HUMAN.md). This script CANNOT change what TTS provider a
deployed worker uses (that is the worker's own `$TTS_PROVIDER`, set on Fly,
not here) -- it only refuses to run at all unless
`ALLOW_CARTESIA_SPEND=1` is set, after printing what to check/set on the
actual target first.
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import json
import os
import sys
import time
import uuid
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import livekit.rtc as rtc
from livekit.api import AccessToken, VideoGrants

# --- Cartesia spend guard (2026-08-15) ---
# A `drive_turns.py --turns 55` run against the deployed worker exhausted a
# 20,000-credit/month free Cartesia tier in ~13 minutes
# (.claude/NEEDS_HUMAN.md, "ANSWERED -- where the free Cartesia credits
# went"). Cartesia bills by SECONDS OF GENERATED AUDIO, not characters
# (~12 credits/second by their own pricing page), and every turn this
# script drives can trigger a real synthesis call on whatever the target
# worker is actually configured to use.
_ALLOW_CARTESIA_SPEND_ENV = "ALLOW_CARTESIA_SPEND"
_TRUTHY = {"1", "true", "yes", "on"}


def _cartesia_spend_guard() -> int | None:
    """Refuse to run against a live target unless the operator has
    explicitly acknowledged the real Cartesia cost model -- and be honest
    about what this guard can and cannot actually control.

    IMPORTANT, and the reason this is a warning-plus-opt-in rather than a
    provider switch: this script CANNOT control what TTS provider the
    worker it drives actually uses. drive_turns.py only joins the LiveKit
    room as an ordinary participant and plays audio; the agent that
    replies is a SEPARATE process -- locally, or (via --token-url) the
    DEPLOYED Fly worker -- and that process reads its OWN
    $TTS_PROVIDER from its OWN environment
    (backend/src/jarvis/pipeline/providers.py:build_tts). Setting
    $TTS_PROVIDER in the shell that runs *this* script has NO effect on a
    deployed worker's TTS choice. Pretending a client-side env var here
    could switch that off would be a fake guard, not a real one.

    Because docs/DESIGN.md pins "cartesia" as the documented default, the
    safe assumption -- absent direct confirmation of the target's actual
    config -- is that the target IS using Cartesia. So: always print the
    warning, and require $ALLOW_CARTESIA_SPEND=1 to proceed at all.
    """
    allowed = os.environ.get(_ALLOW_CARTESIA_SPEND_ENV, "").strip().lower() in _TRUTHY
    print(
        "\n"
        "==================== CARTESIA SPEND WARNING ====================\n"
        "This script drives a REAL agent worker over LiveKit -- either one\n"
        "running locally, or (with --token-url) the DEPLOYED Fly app. That\n"
        "worker's TTS provider is chosen by ITS OWN $TTS_PROVIDER env var\n"
        "(jarvis.pipeline.providers.build_tts), NOT by anything set in the\n"
        "shell that runs this script. Setting $TTS_PROVIDER here has NO\n"
        "effect on a deployed worker's synthesis calls.\n"
        "\n"
        "Cartesia bills by SECONDS OF GENERATED AUDIO (roughly 12\n"
        "credits/second), not characters. A single `drive_turns.py\n"
        "--turns 55` run against the deployed worker exhausted a\n"
        "20,000-credit/month free tier in ~13 minutes (2026-08-14).\n"
        "\n"
        "TO AVOID SPENDING REAL CARTESIA CREDITS ON A DEPLOYED TARGET:\n"
        "  fly secrets set TTS_PROVIDER=aura2 -a jarvisforge\n"
        "and confirm it rolled out (a fresh deploy/restart) BEFORE\n"
        "pointing this script at it via --token-url. For a LOCAL worker,\n"
        "export TTS_PROVIDER=aura2 in the shell that starts THAT process\n"
        "(python -m jarvis.pipeline.worker start) -- not this one.\n"
        "==================================================================\n",
        file=sys.stderr,
    )
    if not allowed:
        print(
            f"ERROR: refusing to run. Set {_ALLOW_CARTESIA_SPEND_ENV}=1 to proceed once "
            "you have confirmed what TTS provider the target worker actually uses (see "
            "warning above) and accept the cost if it is Cartesia.",
            file=sys.stderr,
        )
        return 1
    print(
        f"{_ALLOW_CARTESIA_SPEND_ENV}=1 is set -- proceeding. This does NOT confirm the "
        "target is using aura2; it only records that you've acknowledged the risk.\n",
        file=sys.stderr,
    )
    return None


# --- Audio contract (docs/DESIGN.md / LiveKit): 48kHz mono PCM16, 20ms frames. ---
SAMPLE_RATE = 48_000
NUM_CHANNELS = 1
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 960
FRAME_DURATION_S = FRAME_MS / 1000.0
_BYTES_PER_SAMPLE = 2  # PCM16

# Minimum floor for the room-connect and agent-join waits, independent of a
# possibly-short --timeout meant for per-turn reply latency.
_MIN_CONNECT_TIMEOUT_S = 20.0


@dataclass
class TurnResult:
    turn: int
    utterance_file: str
    user_transcript: str | None
    assistant_transcript: str | None
    ttfa_ms: float | None
    completed: bool
    timed_out: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _load_utterance_frames(path: Path) -> list[bytes]:
    """Read a WAV file and return it as a list of 20ms PCM16 mono 48kHz
    frame byte-chunks, resampling/downmixing as needed."""
    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if sampwidth != _BYTES_PER_SAMPLE:
        raw = audioop.lin2lin(raw, sampwidth, _BYTES_PER_SAMPLE)
    if n_channels == 2:
        raw = audioop.tomono(raw, _BYTES_PER_SAMPLE, 0.5, 0.5)
    elif n_channels != 1:
        raise ValueError(f"{path}: unsupported channel count {n_channels} (need mono or stereo)")

    if framerate != SAMPLE_RATE:
        raw, _ = audioop.ratecv(raw, _BYTES_PER_SAMPLE, 1, framerate, SAMPLE_RATE, None)

    chunk_bytes = SAMPLES_PER_FRAME * _BYTES_PER_SAMPLE
    frames = [raw[i : i + chunk_bytes] for i in range(0, len(raw), chunk_bytes)]
    if frames and len(frames[-1]) < chunk_bytes:
        frames[-1] = frames[-1] + b"\x00" * (chunk_bytes - len(frames[-1]))
    return frames


def _discover_utterances(path: Path) -> list[Path]:
    if not path.is_dir():
        raise SystemExit(f"ERROR: --utterances path is not a directory: {path}")
    files = sorted(p for p in path.iterdir() if p.suffix.lower() == ".wav")
    if not files:
        raise SystemExit(f"ERROR: no .wav files found in --utterances dir: {path}")
    return files


async def _mint_token_remote(token_url: str, identity: str, room: str) -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{token_url.rstrip('/')}/api/token",
            json={"identity": identity, "room": room},
        )
        resp.raise_for_status()
        data = resp.json()
    return data["token"], data["url"]


def _mint_token_local(identity: str, room: str) -> tuple[str, str]:
    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")
    url = os.environ.get("LIVEKIT_URL")
    missing = [
        name
        for name, val in (
            ("LIVEKIT_API_KEY", api_key),
            ("LIVEKIT_API_SECRET", api_secret),
            ("LIVEKIT_URL", url),
        )
        if not val
    ]
    if missing:
        raise SystemExit(
            "ERROR: no --token-url given, and missing env var(s) for local token "
            f"minting: {', '.join(missing)}"
        )
    grants = VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
    token = AccessToken(api_key, api_secret).with_identity(identity).with_grants(grants).to_jwt()
    return token, url  # type: ignore[return-value]


class TurnDriver:
    """Owns the room connection, the published audio track, and the data-
    channel event queue that turn detection reads from."""

    def __init__(self, *, room_name: str, timeout: float) -> None:
        self.room_name = room_name
        self.timeout = timeout
        self.room = rtc.Room()
        self._events: asyncio.Queue[dict] = asyncio.Queue()
        self._agent_joined = asyncio.Event()

        self.room.on("data_received", self._on_data_received)
        self.room.on("participant_connected", self._on_participant_connected)

    def _on_participant_connected(self, participant: rtc.RemoteParticipant) -> None:
        self._agent_joined.set()

    def _on_data_received(self, data_packet: rtc.DataPacket) -> None:
        if data_packet.topic != "jarvis":
            return
        try:
            payload = json.loads(data_packet.data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return
        payload["_recv_ts"] = time.monotonic()
        self._events.put_nowait(payload)

    async def connect(self, url: str, token: str) -> None:
        connect_timeout = max(self.timeout, _MIN_CONNECT_TIMEOUT_S)
        await asyncio.wait_for(self.room.connect(url, token), timeout=connect_timeout)

    async def publish_microphone_track(self) -> rtc.AudioSource:
        source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS)
        track = rtc.LocalAudioTrack.create_audio_track("drive-turns", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self.room.local_participant.publish_track(track, options)
        return source

    async def wait_for_agent(self) -> bool:
        """Waits (bounded by self.timeout) for a remote participant -- the
        agent, dispatched automatically per T-1 -- to join the room. Returns
        whether one is present."""
        if self.room.remote_participants:
            return True
        try:
            await asyncio.wait_for(self._agent_joined.wait(), timeout=self.timeout)
        except TimeoutError:
            return False
        return True

    def _drain_stale_events(self) -> None:
        while not self._events.empty():
            self._events.get_nowait()

    async def _play_utterance(self, source: rtc.AudioSource, frames: list[bytes]) -> float:
        """Pushes frames at real-time pace; returns the monotonic timestamp
        at which playback actually finished (queue fully drained)."""
        for frame_bytes in frames:
            audio_frame = rtc.AudioFrame(
                data=frame_bytes,
                sample_rate=SAMPLE_RATE,
                num_channels=NUM_CHANNELS,
                samples_per_channel=SAMPLES_PER_FRAME,
            )
            await source.capture_frame(audio_frame)
            await asyncio.sleep(FRAME_DURATION_S)
        await source.wait_for_playout()
        return time.monotonic()

    async def _detect_turn(self, end_of_utterance_ts: float) -> TurnResult:
        deadline = end_of_utterance_ts + self.timeout
        user_transcript: str | None = None
        assistant_transcript: str | None = None
        first_audio_ts: float | None = None

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(self._events.get(), timeout=remaining)
            except TimeoutError:
                break

            etype = event.get("type")
            if etype == "agent_state":
                if event.get("state") == "speaking" and first_audio_ts is None:
                    first_audio_ts = event["_recv_ts"]
            elif etype == "transcript":
                role, text, final = event.get("role"), event.get("text"), event.get("final")
                if role == "user" and final:
                    user_transcript = text
                elif role == "assistant" and final:
                    assistant_transcript = text

            if first_audio_ts is not None and assistant_transcript is not None:
                break

        completed = first_audio_ts is not None
        ttfa_ms = (first_audio_ts - end_of_utterance_ts) * 1000.0 if completed else None
        return TurnResult(
            turn=0,  # filled in by caller
            utterance_file="",  # filled in by caller
            user_transcript=user_transcript,
            assistant_transcript=assistant_transcript,
            ttfa_ms=ttfa_ms,
            completed=completed,
            timed_out=not completed,
        )

    async def run_turn(
        self, *, turn_no: int, source: rtc.AudioSource, utterance: Path, frames: list[bytes]
    ) -> TurnResult:
        self._drain_stale_events()

        # Hard-timeout safety net: playback duration + the reply-detection
        # window + a fixed buffer, so a stuck FFI call can't hang forever
        # even though each individual await above is itself bounded.
        audio_duration_s = len(frames) * FRAME_DURATION_S
        hard_timeout = audio_duration_s + self.timeout + 10.0

        async def _play_and_detect() -> TurnResult:
            end_ts = await self._play_utterance(source, frames)
            return await self._detect_turn(end_ts)

        try:
            result = await asyncio.wait_for(_play_and_detect(), timeout=hard_timeout)
        except TimeoutError:
            result = TurnResult(
                turn=0,
                utterance_file="",
                user_transcript=None,
                assistant_transcript=None,
                ttfa_ms=None,
                completed=False,
                timed_out=True,
            )

        result.turn = turn_no
        result.utterance_file = utterance.name
        return result

    async def disconnect(self) -> None:
        try:
            await self.room.disconnect()
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup, never raise on exit
            print(f"WARN: error during disconnect: {exc}", file=sys.stderr)


def _print_turn_line(result: TurnResult) -> None:
    status = "OK" if result.completed else "TIMEOUT"
    ttfa = f"{result.ttfa_ms:.0f}ms" if result.ttfa_ms is not None else "n/a"
    user = (result.user_transcript or "<no user transcript>")[:80]
    assistant = (result.assistant_transcript or "<no assistant transcript>")[:80]
    print(
        f"[turn {result.turn}] {status} ttfa={ttfa} file={result.utterance_file} "
        f"user={user!r} assistant={assistant!r}"
    )


async def _amain(args: argparse.Namespace) -> int:
    guard_exit = _cartesia_spend_guard()
    if guard_exit is not None:
        return guard_exit

    room_name = args.room or f"drive-turns-{uuid.uuid4().hex[:8]}"
    identity = f"drive-turns-{uuid.uuid4().hex[:8]}"

    utterance_paths = _discover_utterances(args.utterances)

    print(f"Minting token for identity={identity!r} room={room_name!r}...")
    if args.token_url:
        token, url = await _mint_token_remote(args.token_url, identity, room_name)
    else:
        token, url = _mint_token_local(identity, room_name)

    driver = TurnDriver(room_name=room_name, timeout=args.timeout)
    results: list[TurnResult] = []
    agent_joined = False

    try:
        print(f"Connecting to {url} room={room_name!r}...")
        await driver.connect(url, token)
        print("Connected.")

        source = await driver.publish_microphone_track()
        print("Published microphone audio track.")

        print(f"Waiting up to {args.timeout}s for the agent to join room {room_name!r}...")
        agent_joined = await driver.wait_for_agent()
        if not agent_joined:
            print(
                f"ERROR: no agent joined room {room_name!r} within {args.timeout}s. "
                "The worker is not registered/dispatching to this room -- this is not "
                "a per-turn reply timeout, no turns were attempted.",
                file=sys.stderr,
            )
        else:
            print("Agent joined. Starting turns.")
            frame_cache: dict[Path, list[bytes]] = {}
            for i in range(args.turns):
                utterance = utterance_paths[i % len(utterance_paths)]
                if utterance not in frame_cache:
                    frame_cache[utterance] = _load_utterance_frames(utterance)
                frames = frame_cache[utterance]

                result = await driver.run_turn(
                    turn_no=i + 1, source=source, utterance=utterance, frames=frames
                )
                results.append(result)
                _print_turn_line(result)
    finally:
        await driver.disconnect()
        print("Disconnected.")

    completed = sum(1 for r in results if r.completed)
    timed_out = sum(1 for r in results if r.timed_out)
    print(
        f"\nSummary: room={room_name} agent_joined={agent_joined} "
        f"turns_attempted={len(results)}/{args.turns} completed={completed} timed_out={timed_out}"
    )

    if args.json:
        payload = {
            "room": room_name,
            "identity": identity,
            "agent_joined": agent_joined,
            "turns": [r.to_dict() for r in results],
            "summary": {
                "attempted": len(results),
                "requested": args.turns,
                "completed": completed,
                "timed_out": timed_out,
            },
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.json}")

    if not agent_joined:
        return 1
    if timed_out > 0:
        return 1
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Hold real conversation turns with the Jarvis agent over a live LiveKit "
            "room, speaking recorded WAV utterances and detecting replies via the "
            "'jarvis' data-channel topic."
        )
    )
    parser.add_argument("--room", default=None, help="Room name (default: generated).")
    parser.add_argument("--turns", type=int, default=1, help="Number of turns to run.")
    parser.add_argument(
        "--token-url",
        default=None,
        help=(
            "Base URL of a running /api/token server (e.g. http://localhost:8080). "
            "If omitted, a token is minted locally from LIVEKIT_API_KEY/API_SECRET."
        ),
    )
    parser.add_argument(
        "--utterances",
        type=Path,
        required=True,
        help="Directory of .wav files to speak, round-robin.",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="Per-turn reply timeout in seconds."
    )
    parser.add_argument(
        "--json", type=Path, default=None, help="Path to write per-turn results as JSON."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
