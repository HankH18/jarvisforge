"""T-16: a LOCAL, non-deployed proxy for time-to-first-audio on no-lookup
turns, for use only while Cartesia TTS is unmeasurable in production (see
docs/latency-report.md).

This is NOT the R1 measurement. R1 is defined as turn_end -> first-audio
inside the real deployed pipeline: LiveKit room transport, streaming
Deepgram STT, the real AgentSession (preemptive generation, endpointing,
etc.), Cartesia TTS, all measured from a real multi-turn deployed session
(scripts/drive_turns.py + scripts/latency_report.py). None of that runs
here. This script does exactly two direct, unmocked HTTP round trips per
sample -- a streaming Anthropic Messages call to $CONVO_MODEL (time to the
first content delta) and a Deepgram Aura-2 TTS call (the SPEC C2 fallback
TTS, since Cartesia returns HTTP 402 on every call right now -- time to the
first response byte) -- and reports their sum as a directional lower-bound
proxy: real network + real model + real TTS, but from this machine, with no
LiveKit hop, no STT, no preemptive-generation overlap, and no Cartesia
(SPEC's actual TTS pick). Treat the number as "at least this fast in the
best case", not as R1 evidence.

Usage (needs ANTHROPIC_API_KEY, CONVO_MODEL, DEEPGRAM_API_KEY set, e.g. via
`.env`, and network egress to api.anthropic.com / api.deepgram.com):

    uv run python scripts/local_ttfb_proxy.py --samples 20

Requires anthropic and httpx (both already backend dependencies).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass

import httpx

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]

_DEEPGRAM_URL = "https://api.deepgram.com/v1/speak"
_DEEPGRAM_MODEL = "aura-2-andromeda-en"  # the installed livekit-plugins-deepgram TTS default

# A handful of short, representative no-lookup ("chat") turns -- the same
# population R1's 800ms bar is defined over (docs/SPEC.md R1).
_PROMPTS = [
    "What's a good name for a golden retriever puppy?",
    "Tell me a short knock-knock joke.",
    "What's the capital of France?",
    "Give me a one-sentence fun fact about octopuses.",
    "What's a healthy breakfast idea?",
]
# Short canned replies, standing in for whatever CONVO_MODEL would say --
# used only to drive the TTS leg with something reply-shaped and short
# (voice replies are short by design, see pipeline/agent.py's INSTRUCTIONS).
_TTS_TEXTS = [
    "How about Sunny? It's friendly and easy to call.",
    "Knock knock! Who's there? Lettuce. Lettuce who? Lettuce in, it's cold!",
    "The capital of France is Paris.",
    "Octopuses have three hearts and blue blood.",
    "Try Greek yogurt with berries and a bit of honey.",
]


@dataclass(frozen=True)
class _Sample:
    llm_ttfb_ms: float
    tts_ttfb_ms: float

    @property
    def sum_ms(self) -> float:
        return self.llm_ttfb_ms + self.tts_ttfb_ms


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = pct / 100.0 * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def _measure_llm_ttfb(client: anthropic.Anthropic, model: str, prompt: str) -> float:
    start = time.monotonic()
    with client.messages.stream(
        model=model,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for event in stream:
            if event.type == "content_block_delta":
                return (time.monotonic() - start) * 1000.0
    return (time.monotonic() - start) * 1000.0


def _measure_tts_ttfb(client: httpx.Client, api_key: str, text: str) -> float:
    start = time.monotonic()
    with client.stream(
        "POST",
        _DEEPGRAM_URL,
        params={"model": _DEEPGRAM_MODEL, "encoding": "linear16", "sample_rate": "24000"},
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        json={"text": text},
        timeout=30.0,
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            if chunk:
                return (time.monotonic() - start) * 1000.0
    return (time.monotonic() - start) * 1000.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args(argv)

    if anthropic is None:
        print("ERROR: the `anthropic` package is not installed", file=sys.stderr)
        return 1

    convo_model = os.environ.get("CONVO_MODEL")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    deepgram_key = os.environ.get("DEEPGRAM_API_KEY")
    missing = [
        name
        for name, val in (
            ("CONVO_MODEL", convo_model),
            ("ANTHROPIC_API_KEY", anthropic_key),
            ("DEEPGRAM_API_KEY", deepgram_key),
        )
        if not val
    ]
    if missing:
        print(f"ERROR: missing required env var(s): {missing}", file=sys.stderr)
        return 1

    anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
    samples: list[_Sample] = []

    with httpx.Client() as http_client:
        for i in range(args.samples):
            prompt = _PROMPTS[i % len(_PROMPTS)]
            tts_text = _TTS_TEXTS[i % len(_TTS_TEXTS)]
            llm_ms = _measure_llm_ttfb(anthropic_client, convo_model, prompt)
            tts_ms = _measure_tts_ttfb(http_client, deepgram_key, tts_text)
            samples.append(_Sample(llm_ttfb_ms=llm_ms, tts_ttfb_ms=tts_ms))
            print(
                f"sample {i + 1}/{args.samples}: llm_ttfb={llm_ms:.1f}ms "
                f"tts_ttfb={tts_ms:.1f}ms sum={llm_ms + tts_ms:.1f}ms",
                file=sys.stderr,
            )

    llm_vals = sorted(s.llm_ttfb_ms for s in samples)
    tts_vals = sorted(s.tts_ttfb_ms for s in samples)
    sum_vals = sorted(s.sum_ms for s in samples)

    print()
    print("LOCAL PROXY -- NOT the R1 deployed measurement (see this script's docstring).")
    print(f"model={convo_model} tts=deepgram-aura-2 ({_DEEPGRAM_MODEL}) samples={len(samples)}")
    print()
    print("| leg | p50 (ms) | p95 (ms) | max (ms) |")
    print("| --- | --- | --- | --- |")
    print(
        f"| LLM time-to-first-token | {_percentile(llm_vals, 50):.1f} | "
        f"{_percentile(llm_vals, 95):.1f} | {llm_vals[-1]:.1f} |"
    )
    print(
        f"| TTS (Aura-2) time-to-first-byte | {_percentile(tts_vals, 50):.1f} | "
        f"{_percentile(tts_vals, 95):.1f} | {tts_vals[-1]:.1f} |"
    )
    print(
        f"| sum (proxy for turn_end -> first-audio) | {_percentile(sum_vals, 50):.1f} | "
        f"{_percentile(sum_vals, 95):.1f} | {sum_vals[-1]:.1f} |"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
