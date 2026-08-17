// Tests for openWakeWordDetector.ts's real detection logic (buffering,
// windowing, threshold, cooldown, honest-failure propagation) via injected
// fake ONNX sessions and a fake microphone feed -- see
// createOpenWakeWordDetectorWithDeps. No real onnxruntime-web, no real
// microphone, and no real hey_jarvis model weights are involved.
//
// WHAT THIS PROVES: that this file's own orchestration code is correct --
// it accumulates the right number of raw samples before running anything,
// it builds the 16 embedding windows at the right frame offsets (asserted
// directly against the shape the fake wake session receives: exactly
// [1, 16, 96]), it turns a wake-model score crossing WAKE_THRESHOLD into
// exactly one onWake() call with a cooldown, and both a model-load failure
// and a microphone-start failure reject start() rather than resolving into
// a detector that silently never listens.
//
// WHAT THIS DOES NOT PROVE: that the real hey_jarvis_v0.1.onnx model
// actually assigns a high score to someone saying "hey Jarvis" out loud,
// or a low score to everything else. The fake wake session here returns
// whatever score the test tells it to -- it has no idea what real speech
// sounds like. Only a live browser, a real microphone, and the vendored
// model files together can prove that; see the final report for what
// would be needed to close that gap and why it wasn't done here.

import { describe, expect, it, vi } from 'vitest'
import {
  createOpenWakeWordDetectorWithDeps,
  getWakeDiagnosticsSnapshot,
  HOP_SAMPLES,
  isWakeDiagnosticsEnabled,
  SAMPLES_NEEDED,
  subscribeWakeDiagnostics,
  type AudioCapture,
  type OpenWakeWordDeps,
  type OpenWakeWordSessions,
  type OrtSessionLike,
  type OrtTensorLike,
  type WakeDiagnosticsSnapshot,
} from './openWakeWordDetector'

/** Builds fake melspectrogram/embedding sessions that compute plausible
 * output shapes from their real input shapes (using the exact same
 * frames = samples/160 - 3 relationship verified against the real .onnx
 * files -- see this module's header), plus a fake wake session whose score
 * is controlled by the test and which records the shape it was fed. This
 * is deliberately not a hollow "always resolves" stub: if
 * openWakeWordDetector.ts's windowing math regresses (wrong slice, wrong
 * stride, wrong count), the wake session either receives the wrong shape
 * (caught by the dims assertion below) or `run()` never gets reached with
 * enough data at all.
 *
 * `melspecValue` (default 0, matching every existing call site's
 * expectations) controls what raw value the fake melspectrogram.onnx fills
 * its output tensor with -- lets a test pin the MELSPEC_TRANSFORM_DIVISOR/
 * _OFFSET transform (spec/10 + 2) that openWakeWordDetector.ts must apply
 * to that raw output before it reaches embedding_model.onnx (see
 * getLastEmbeddingFeedData below). */
function makeFakeSessions(wakeScore: () => number, melspecValue: () => number = () => 0) {
  let melspecCalls = 0
  let embeddingCalls = 0
  let wakeCalls = 0
  let lastWakeFeedDims: readonly number[] | null = null
  let lastMelspecFeedData: ArrayLike<number> | null = null
  let lastEmbeddingFeedData: ArrayLike<number> | null = null

  const melspec: OrtSessionLike = {
    inputNames: ['input'],
    outputNames: ['output'],
    async run(feeds): Promise<Record<string, OrtTensorLike>> {
      melspecCalls += 1
      lastMelspecFeedData = feeds.input.data
      const samples = feeds.input.dims[1]
      const frames = Math.floor(samples / 160) - 3
      const data = new Float32Array(frames * 32).fill(melspecValue())
      return { output: { data, dims: [1, 1, frames, 32] } }
    },
  }

  const embedding: OrtSessionLike = {
    inputNames: ['input_1'],
    outputNames: ['conv2d_19'],
    async run(feeds): Promise<Record<string, OrtTensorLike>> {
      embeddingCalls += 1
      lastEmbeddingFeedData = feeds.input_1.data
      const n = feeds.input_1.dims[0]
      return { conv2d_19: { data: new Float32Array(n * 96), dims: [n, 1, 1, 96] } }
    },
  }

  const wake: OrtSessionLike = {
    inputNames: ['x.1'],
    outputNames: ['53'],
    async run(feeds): Promise<Record<string, OrtTensorLike>> {
      wakeCalls += 1
      lastWakeFeedDims = feeds['x.1'].dims
      return { '53': { data: [wakeScore()], dims: [1, 1] } }
    },
  }

  const sessions: OpenWakeWordSessions = {
    melspec,
    embedding,
    wake,
    makeTensor: (data, dims) => ({ data, dims }),
  }

  return {
    sessions,
    getCalls: () => ({ melspecCalls, embeddingCalls, wakeCalls }),
    getLastWakeFeedDims: () => lastWakeFeedDims,
    getLastMelspecFeedData: () => lastMelspecFeedData,
    getLastEmbeddingFeedData: () => lastEmbeddingFeedData,
  }
}

/** Fake microphone: lets a test push raw PCM samples on demand instead of
 * needing a real getUserMedia()/AudioContext. `loseCapture()` simulates the
 * `onCaptureLost` signal a real capture reports when its MediaStreamTrack
 * fires `ended` mid-session (see startRealCapture in openWakeWordDetector.ts
 * for what that maps to in a real browser) -- lets a test drive the fatal
 * path without a real track or permission prompt. */
function makeFakeCapture() {
  let onSamples: ((samples: Float32Array) => void) | null = null
  let onCaptureLost: (() => void) | null = null
  let closeCalls = 0

  const startCapture: OpenWakeWordDeps['startCapture'] = async (cb, lostCb) => {
    onSamples = cb
    onCaptureLost = lostCb
    const capture: AudioCapture = {
      async close() {
        closeCalls += 1
      },
    }
    return capture
  }

  return {
    startCapture,
    feed: (samples: Float32Array) => onSamples?.(samples),
    loseCapture: () => onCaptureLost?.(),
    getCloseCalls: () => closeCalls,
  }
}

/** Lets the fake sessions' already-resolved promises (and this file's
 * .then/.catch/.finally chain around them) actually settle before an
 * assertion runs. */
async function flush(ticks = 10): Promise<void> {
  for (let i = 0; i < ticks; i += 1) await Promise.resolve()
}

describe('createOpenWakeWordDetectorWithDeps', () => {
  it('start() resolves, and stop() is safe before start, after start, and called twice', async () => {
    const { sessions } = makeFakeSessions(() => 0)
    const { startCapture } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps({ loadSessions: async () => sessions, startCapture })

    await expect(detector.stop()).resolves.toBeUndefined() // before start -- must not throw
    await expect(detector.start(() => {})).resolves.toBeUndefined()
    await expect(detector.stop()).resolves.toBeUndefined()
    await expect(detector.stop()).resolves.toBeUndefined() // twice -- must not throw
  })

  it('invokes onWake when the wake model score crosses WAKE_THRESHOLD', async () => {
    const { sessions, getLastWakeFeedDims } = makeFakeSessions(() => 0.9)
    const { startCapture, feed } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps({ loadSessions: async () => sessions, startCapture })

    let wakeCount = 0
    await detector.start(() => {
      wakeCount += 1
    })

    feed(new Float32Array(SAMPLES_NEEDED))
    await flush()

    expect(wakeCount).toBe(1)
    // Proves the windowing math (not just that *some* score came back):
    // the wake model must see exactly 16 embeddings of 96 dims each,
    // batched as [1, 16, 96] -- verified against the real hey_jarvis_v0.1.onnx
    // input shape (see module header).
    expect(getLastWakeFeedDims()).toEqual([1, 16, 96])
  })

  it('scales raw PCM into openWakeWord\'s int16-scale amplitude domain before it reaches melspectrogram.onnx', async () => {
    // Pins the WEB_AUDIO_TO_INT16_SCALE conversion in openWakeWordDetector.ts
    // (see that constant's header comment for the upstream evidence this is
    // based on). Web Audio hands this file float32 samples normalized to
    // [-1, 1]; openWakeWord's melspectrogram.onnx expects int16-scale
    // magnitudes (roughly [-32768, 32767]) as float32. This must FAIL if
    // that ×32767 multiply is ever removed or its constant changed --
    // checking only that "a tensor was passed" (as the existing
    // [1, 16, 96] dims test does for the wake session) would not catch a
    // regression back to feeding the model near-silence.
    const { sessions, getLastMelspecFeedData } = makeFakeSessions(() => 0)
    const { startCapture, feed } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps({ loadSessions: async () => sessions, startCapture })

    await detector.start(() => {})

    const raw = new Float32Array(SAMPLES_NEEDED)
    raw[0] = 1
    raw[1] = -1
    raw[2] = 0.5
    // Browsers can hand back samples very slightly outside [-1, 1] (float
    // rounding in the audio pipeline) -- these must clamp to ±1 before
    // scaling, not scale directly (which would overflow int16-scale range
    // and distort).
    raw[3] = 1.0005
    raw[4] = -1.0007

    feed(raw)
    await flush()

    const data = getLastMelspecFeedData()
    expect(data).not.toBeNull()
    const fed = data as ArrayLike<number>

    expect(fed[0]).toBeCloseTo(Math.fround(1 * 32767), 5)
    expect(fed[1]).toBeCloseTo(Math.fround(-1 * 32767), 5)
    expect(fed[2]).toBeCloseTo(Math.fround(0.5 * 32767), 5)
    expect(fed[3]).toBeCloseTo(Math.fround(1 * 32767), 5) // clamped, then scaled
    expect(fed[4]).toBeCloseTo(Math.fround(-1 * 32767), 5) // clamped, then scaled

    // Guards against a regression that silently drops the multiply
    // entirely (the unscaled Web Audio value at index 0 would be 1, not
    // ~32767).
    expect(Math.abs(fed[0])).toBeGreaterThan(1000)
  })

  it("applies openWakeWord's melspectrogram transform (spec/10 + 2) before feeding it to embedding_model.onnx", async () => {
    // Pins MELSPEC_TRANSFORM_DIVISOR/_OFFSET in openWakeWordDetector.ts
    // (see that constant's header comment for the upstream evidence this is
    // based on: openwakeword/utils.py's AudioFeatures._get_melspectrogram
    // applies `spec/10 + 2` to melspectrogram.onnx's raw output -- its own
    // comment calls it "Arbitrary transform of melspectrogram" -- before
    // that output is ever windowed for embedding_model.onnx). The fake
    // melspectrogram session here fills its entire raw output with a known
    // value (5); if openWakeWordDetector.ts skips the transform, the
    // embedding model would receive that raw 5 unchanged instead of
    // 5/10 + 2 = 2.5.
    const RAW_MELSPEC_VALUE = 5
    const { sessions, getLastEmbeddingFeedData } = makeFakeSessions(() => 0, () => RAW_MELSPEC_VALUE)
    const { startCapture, feed } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps({ loadSessions: async () => sessions, startCapture })

    await detector.start(() => {})
    feed(new Float32Array(SAMPLES_NEEDED))
    await flush()

    const data = getLastEmbeddingFeedData()
    expect(data).not.toBeNull()
    const fed = data as ArrayLike<number>
    expect(fed.length).toBeGreaterThan(0)

    const expected = RAW_MELSPEC_VALUE / 10 + 2
    for (let i = 0; i < fed.length; i += 1) {
      expect(fed[i]).toBeCloseTo(expected, 5)
    }
    // Guards against a regression that silently drops the transform
    // entirely (the untransformed raw value at index 0 would be 5, not 2.5).
    expect(fed[0]).not.toBeCloseTo(RAW_MELSPEC_VALUE, 5)
  })

  it('does not invoke onWake when the score stays below WAKE_THRESHOLD', async () => {
    const { sessions } = makeFakeSessions(() => 0.1)
    const { startCapture, feed } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps({ loadSessions: async () => sessions, startCapture })

    let wakeCount = 0
    await detector.start(() => {
      wakeCount += 1
    })

    feed(new Float32Array(SAMPLES_NEEDED))
    await flush()

    expect(wakeCount).toBe(0)
  })

  it('does not run any inference until enough audio has accumulated', async () => {
    const { sessions, getCalls } = makeFakeSessions(() => 0.9)
    const { startCapture, feed } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps({ loadSessions: async () => sessions, startCapture })

    let wakeCount = 0
    await detector.start(() => {
      wakeCount += 1
    })

    // One 80 ms hop -- enough to cross the "try a pass" threshold, but far
    // short of the ~2 s of context the pipeline needs.
    feed(new Float32Array(HOP_SAMPLES))
    await flush()

    expect(wakeCount).toBe(0)
    expect(getCalls().melspecCalls).toBe(0) // the pass bailed before touching any session
  })

  it('does not fire a second time within the cooldown window for one continuous utterance', async () => {
    const { sessions } = makeFakeSessions(() => 0.95)
    const { startCapture, feed } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps({ loadSessions: async () => sessions, startCapture })

    let wakeCount = 0
    await detector.start(() => {
      wakeCount += 1
    })

    feed(new Float32Array(SAMPLES_NEEDED))
    await flush()
    expect(wakeCount).toBe(1)

    // More high-scoring audio arrives immediately after (well within
    // COOLDOWN_MS) -- must not fire again.
    feed(new Float32Array(HOP_SAMPLES))
    await flush()
    expect(wakeCount).toBe(1)
  })

  it('stop() releases the microphone capture', async () => {
    const { sessions } = makeFakeSessions(() => 0)
    const { startCapture, getCloseCalls } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps({ loadSessions: async () => sessions, startCapture })

    await detector.start(() => {})
    expect(getCloseCalls()).toBe(0)
    await detector.stop()
    expect(getCloseCalls()).toBe(1)
  })

  // The bug this batch of tests exists to catch: a MediaStreamTrack ending
  // mid-session (mic permission revoked, device unplugged, ...) used to go
  // completely unnoticed -- no more audio arrived, but nothing told the
  // caller, so the app kept claiming to be listening while actually deaf.
  // `loseCapture()` (see makeFakeCapture above) simulates the fatal
  // `onCaptureLost` signal a real capture reports for exactly that browser
  // event -- see startRealCapture in openWakeWordDetector.ts for the
  // fatal-vs-transient reasoning (track `ended`, not `mute`/`unmute`).
  it('reports a mid-session capture loss through onFailure and releases resources, without calling onWake', async () => {
    const { sessions } = makeFakeSessions(() => 0.9) // score would cross WAKE_THRESHOLD if a pass ever ran
    const { startCapture, loseCapture, getCloseCalls } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps({ loadSessions: async () => sessions, startCapture })

    let wakeCount = 0
    const failures: string[] = []
    await detector.start(
      () => {
        wakeCount += 1
      },
      (message) => failures.push(message),
    )

    expect(getCloseCalls()).toBe(0)
    loseCapture()
    await flush()

    // Resources released exactly like a real stop() would -- this must not
    // leave an AudioContext/ScriptProcessorNode (or, here, the fake
    // capture's own close()) hanging around unreleased.
    expect(getCloseCalls()).toBe(1)
    // Reported through the same onFailure channel a real detector's
    // start() takes, exactly once, with a message a caller can show the
    // user -- not silently swallowed, and not reported as a wake.
    expect(failures).toHaveLength(1)
    expect(failures[0]).toBeTruthy()
    expect(wakeCount).toBe(0)
  })

  it('does not run any further detection passes after a mid-session capture loss', async () => {
    const { sessions, getCalls } = makeFakeSessions(() => 0.9)
    const { startCapture, feed, loseCapture } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps({ loadSessions: async () => sessions, startCapture })

    let wakeCount = 0
    await detector.start(() => {
      wakeCount += 1
    })

    loseCapture()
    await flush()

    // Audio arriving after the capture is already gone (a straggling
    // callback, in a real browser) must not resurrect detection.
    feed(new Float32Array(SAMPLES_NEEDED))
    await flush()

    expect(getCalls().melspecCalls).toBe(0)
    expect(wakeCount).toBe(0)
  })

  it('a deliberate stop() does NOT report a failure -- an intentional stop is not an error', async () => {
    const { sessions } = makeFakeSessions(() => 0)
    const { startCapture } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps({ loadSessions: async () => sessions, startCapture })

    const failures: string[] = []
    await detector.start(
      () => {},
      (message) => failures.push(message),
    )

    await detector.stop()

    // This is the regression this test exists to catch: if stop() were
    // ever wired to the same failure-reporting path as a real capture
    // loss, unticking the passive-listening checkbox would show a scary
    // "microphone lost" error every single time, for a fully intentional
    // action.
    expect(failures).toEqual([])
  })

  it('rejects start() when the ONNX models fail to load, instead of resolving into a silent no-op', async () => {
    const startCapture: OpenWakeWordDeps['startCapture'] = async () => {
      throw new Error('startCapture should never be called when model loading fails')
    }
    const detector = createOpenWakeWordDetectorWithDeps({
      loadSessions: async () => {
        throw new Error('failed to fetch melspectrogram.onnx')
      },
      startCapture,
    })

    await expect(detector.start(() => {})).rejects.toThrow(/failed to fetch melspectrogram\.onnx/)
  })

  it('rejects start() when microphone capture fails to start (e.g. permission denied)', async () => {
    const { sessions } = makeFakeSessions(() => 0)
    const detector = createOpenWakeWordDetectorWithDeps({
      loadSessions: async () => sessions,
      startCapture: async () => {
        throw new Error('mic permission denied')
      },
    })

    await expect(detector.start(() => {})).rejects.toThrow(/mic permission denied/)
  })
})

// Diagnostics (opt-in observability -- see this module's diagnostics
// section for why it exists: a deployed build loaded every model but never
// fired onWake, with nothing client-side able to say why). The central
// claim these tests exist to prove is the hard requirement this shipped
// under: detection behaviour (onWake firing, or not) is identical whether
// diagnostics is on or off. Everything else here (the score/max/error
// published to subscribeWakeDiagnostics, the console output, the query-
// param gate) is the actual observability feature, tested for its own
// sake too.
describe('createOpenWakeWordDetectorWithDeps: diagnostics', () => {
  it('isWakeDiagnosticsEnabled: only ?debug=wake or ?debug=1 turn it on', () => {
    expect(isWakeDiagnosticsEnabled('')).toBe(false)
    expect(isWakeDiagnosticsEnabled('?foo=bar')).toBe(false)
    expect(isWakeDiagnosticsEnabled('?debug=verbose')).toBe(false)
    expect(isWakeDiagnosticsEnabled('?debug=wake')).toBe(true)
    expect(isWakeDiagnosticsEnabled('?debug=1')).toBe(true)
    expect(isWakeDiagnosticsEnabled('?other=1&debug=wake')).toBe(true)
  })

  it('does not alter onWake behaviour: the same score sequence fires identically whether diagnostics is on or off', async () => {
    for (const diagnosticsEnabled of [false, true]) {
      const { sessions } = makeFakeSessions(() => 0.95)
      const { startCapture, feed } = makeFakeCapture()
      const detector = createOpenWakeWordDetectorWithDeps(
        { loadSessions: async () => sessions, startCapture },
        diagnosticsEnabled,
      )

      let wakeCount = 0
      await detector.start(() => {
        wakeCount += 1
      })

      feed(new Float32Array(SAMPLES_NEEDED))
      await flush()
      expect(wakeCount).toBe(1)

      // Still within COOLDOWN_MS -- must not fire again, on or off.
      feed(new Float32Array(HOP_SAMPLES))
      await flush()
      expect(wakeCount).toBe(1)

      await detector.stop()
    }
  })

  it('does not alter onWake behaviour for a below-threshold score either, on or off', async () => {
    for (const diagnosticsEnabled of [false, true]) {
      const { sessions } = makeFakeSessions(() => 0.1)
      const { startCapture, feed } = makeFakeCapture()
      const detector = createOpenWakeWordDetectorWithDeps(
        { loadSessions: async () => sessions, startCapture },
        diagnosticsEnabled,
      )

      let wakeCount = 0
      await detector.start(() => {
        wakeCount += 1
      })

      feed(new Float32Array(SAMPLES_NEEDED))
      await flush()
      expect(wakeCount).toBe(0)

      await detector.stop()
    }
  })

  it('when enabled, publishes the live score and a NaN-safe running max to subscribeWakeDiagnostics', async () => {
    let score = 0.2
    const { sessions } = makeFakeSessions(() => score)
    const { startCapture, feed } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps(
      { loadSessions: async () => sessions, startCapture },
      true,
    )

    const snapshots: WakeDiagnosticsSnapshot[] = []
    const unsubscribe = subscribeWakeDiagnostics((s) => snapshots.push(s))

    await detector.start(() => {})
    feed(new Float32Array(SAMPLES_NEEDED))
    await flush()

    score = 0.35 // a new peak
    feed(new Float32Array(HOP_SAMPLES))
    await flush()

    score = NaN // e.g. a garbage/degenerate model output
    feed(new Float32Array(HOP_SAMPLES))
    await flush()

    unsubscribe()

    // The raw score is shown as NaN, not hidden -- that's a real failure
    // signature this exists to surface.
    expect(getWakeDiagnosticsSnapshot().score).toBeNaN()
    // But the running max ignores the NaN reading and stays at the
    // highest real score seen (0.35) rather than being permanently
    // pinned at NaN by it.
    expect(getWakeDiagnosticsSnapshot().max).toBeCloseTo(0.35)
    expect(snapshots.some((s) => s.score === 0.2 && s.max === 0.2)).toBe(true)
    expect(snapshots.some((s) => s.score === 0.35 && s.max === 0.35)).toBe(true)

    await detector.stop()
  })

  it('when disabled, never publishes to subscribeWakeDiagnostics', async () => {
    const { sessions } = makeFakeSessions(() => 0.4)
    const { startCapture, feed } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps(
      { loadSessions: async () => sessions, startCapture },
      false, // explicit, though this also matches the default
    )

    const snapshots: WakeDiagnosticsSnapshot[] = []
    const unsubscribe = subscribeWakeDiagnostics((s) => snapshots.push(s))

    await detector.start(() => {})
    feed(new Float32Array(SAMPLES_NEEDED))
    await flush()

    unsubscribe()
    expect(snapshots).toEqual([])

    await detector.stop()
  })

  it('logs an inference-pass error to the console unconditionally -- on or off -- so it can never be silently swallowed', async () => {
    for (const diagnosticsEnabled of [false, true]) {
      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const throwingRun = async (): Promise<Record<string, OrtTensorLike>> => {
        throw new Error('onnx runtime boom')
      }
      const failingSessions: OpenWakeWordSessions = {
        melspec: { inputNames: ['input'], outputNames: ['output'], run: throwingRun },
        embedding: { inputNames: ['x'], outputNames: ['y'], run: throwingRun },
        wake: { inputNames: ['x'], outputNames: ['y'], run: throwingRun },
        makeTensor: (data, dims) => ({ data, dims }),
      }
      const { startCapture, feed } = makeFakeCapture()
      const detector = createOpenWakeWordDetectorWithDeps(
        { loadSessions: async () => failingSessions, startCapture },
        diagnosticsEnabled,
      )

      await detector.start(() => {})
      feed(new Float32Array(SAMPLES_NEEDED))
      await flush()

      expect(errorSpy).toHaveBeenCalledTimes(1)
      expect(errorSpy.mock.calls[0][0]).toBe('[wake] openWakeWord detection pass failed')
      expect(errorSpy.mock.calls[0][1]).toBeInstanceOf(Error)

      errorSpy.mockRestore()
      await detector.stop()
    }
  })

  it('when enabled, an inference-pass error also surfaces in the published diagnostics snapshot', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const throwingRun = async (): Promise<Record<string, OrtTensorLike>> => {
      throw new Error('onnx runtime boom')
    }
    const failingSessions: OpenWakeWordSessions = {
      melspec: { inputNames: ['input'], outputNames: ['output'], run: throwingRun },
      embedding: { inputNames: ['x'], outputNames: ['y'], run: throwingRun },
      wake: { inputNames: ['x'], outputNames: ['y'], run: throwingRun },
      makeTensor: (data, dims) => ({ data, dims }),
    }
    const { startCapture, feed } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps(
      { loadSessions: async () => failingSessions, startCapture },
      true,
    )

    await detector.start(() => {})
    feed(new Float32Array(SAMPLES_NEEDED))
    await flush()

    expect(getWakeDiagnosticsSnapshot().error).toBe('onnx runtime boom')

    errorSpy.mockRestore()
    await detector.stop()
  })

  it('throttles the console score log to well under 12.5/s, while the running max still updates every pass', async () => {
    let score = 0.1
    const { sessions } = makeFakeSessions(() => score)
    const { startCapture, feed } = makeFakeCapture()
    const detector = createOpenWakeWordDetectorWithDeps(
      { loadSessions: async () => sessions, startCapture },
      true,
    )
    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {})

    await detector.start(() => {})
    feed(new Float32Array(SAMPLES_NEEDED)) // first pass
    await flush()

    score = 0.6 // second pass, well within 250ms of the first in test time
    feed(new Float32Array(HOP_SAMPLES))
    await flush()

    // Only the first pass logged to the console -- the second was
    // throttled -- but the running max was still updated immediately.
    expect(logSpy).toHaveBeenCalledTimes(1)
    expect(getWakeDiagnosticsSnapshot().max).toBeCloseTo(0.6)

    logSpy.mockRestore()
    await detector.stop()
  })
})
