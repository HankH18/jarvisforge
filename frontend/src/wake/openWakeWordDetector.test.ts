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

import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createOpenWakeWordDetectorWithDeps,
  getWakeDiagnosticsSnapshot,
  HOP_SAMPLES,
  isWakeDiagnosticsEnabled,
  loadRealSessions,
  SAMPLE_RATE,
  SAMPLES_NEEDED,
  startRealCapture,
  subscribeWakeDiagnostics,
  type AudioCapture,
  type OpenWakeWordDeps,
  type OpenWakeWordSessions,
  type OrtSessionLike,
  type OrtTensorLike,
  type WakeDiagnosticsSnapshot,
} from './openWakeWordDetector'
import * as ortWasmModule from 'onnxruntime-web/wasm'

// Stubs 'onnxruntime-web/wasm' -- the real WASM runtime is far too heavy
// (and not meaningfully faster to trust) for a unit test; this fakes the
// ONNX RUNTIME LIBRARY itself, not our own logic, same rationale as the
// browser-API fakes below. Intercepts both the static import a test file
// might use and loadRealSessions' own `import('onnxruntime-web/wasm')`
// (vi.mock hoists above all imports, static or dynamic, regardless of
// where in this file it's written).
vi.mock('onnxruntime-web/wasm', () => {
  class FakeInferenceSession {
    inputNames: string[]
    outputNames: string[]
    private readonly modelPath: string
    constructor(modelPath: string) {
      this.modelPath = modelPath
      this.inputNames = [`${modelPath}#in`]
      this.outputNames = [`${modelPath}#out`]
    }
    async run(): Promise<Record<string, { data: Float32Array; dims: number[] }>> {
      return { [`${this.modelPath}#out`]: { data: new Float32Array([7]), dims: [1] } }
    }
  }
  const create = vi.fn(async (modelPath: string) => new FakeInferenceSession(modelPath))

  class FakeTensor {
    type: string
    data: unknown
    dims: unknown
    constructor(type: string, data: unknown, dims: unknown) {
      this.type = type
      this.data = data
      this.dims = dims
    }
  }

  return {
    InferenceSession: { create },
    Tensor: FakeTensor,
    env: { wasm: { numThreads: 0 } },
  }
})

/** Narrow, hand-typed view onto the stubbed 'onnxruntime-web/wasm' module
 * above -- avoids fighting the real package's full (overloaded)
 * InferenceSession.create/Tensor typings for what is, at runtime, the fake
 * from the vi.mock factory above. Same rationale as this file's own
 * OrtSessionLike/OrtTensorLike: a minimal structural type is enough to
 * drive and inspect the fake. */
const ortWasm = ortWasmModule as unknown as {
  InferenceSession: { create: ReturnType<typeof vi.fn> }
  Tensor: new (type: string, data: unknown, dims: unknown) => { type: string; data: unknown; dims: unknown }
  env: { wasm: { numThreads: number } }
}

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

// ---------------------------------------------------------------------------
// startRealCapture -- the actual browser I/O layer (getUserMedia,
// AudioContext, ScriptProcessorNode, the MediaStreamTrack 'ended' listener).
// Every test above this point drives createOpenWakeWordDetectorWithDeps
// through the injected `Deps` interface and never calls startRealCapture at
// all -- makeFakeCapture's `startCapture` stands in for it completely, so
// none of the tests above can prove startRealCapture's own wiring is
// correct. Concretely: replacing startRealCapture's
// `const handleTrackEnded = () => onCaptureLost()` with a no-op removes the
// entire mid-session mic-loss feature, and every test above still passes.
//
// These tests fake the BROWSER (getUserMedia, AudioContext,
// ScriptProcessorNode, MediaStreamTrack) rather than our own logic, and
// call startRealCapture directly -- same house pattern as
// src/test/setup.ts's MemoryStorage polyfill for window.localStorage: a
// small, real stand-in for what jsdom doesn't implement (jsdom has no
// AudioContext, no MediaStreamTrack, and no navigator.mediaDevices at all --
// confirmed directly, all three are `undefined` in this test environment),
// not a mock of anything this project owns.
describe('startRealCapture (real browser I/O, faked)', () => {
  /** Minimal fake of a browser MediaStreamTrack. Extends the real (jsdom-
   * provided) EventTarget so addEventListener('ended', ...) /
   * removeEventListener / dispatchEvent behave exactly like the real thing
   * startRealCapture wires up to. `dispatchEndedOnStop` lets one test below
   * simulate a hypothetical browser that -- unlike the current spec, which
   * says a script-initiated stop() does NOT itself fire `ended` -- fires
   * `ended` as a side effect of stop() anyway; that's the one scenario the
   * removeEventListener-before-stop ordering in close() defends against
   * (see its comment in openWakeWordDetector.ts). */
  class FakeMediaStreamTrack extends EventTarget {
    stopCalls = 0
    private readonly dispatchEndedOnStop: boolean

    constructor(dispatchEndedOnStop = false) {
      super()
      this.dispatchEndedOnStop = dispatchEndedOnStop
    }

    stop(): void {
      this.stopCalls += 1
      if (this.dispatchEndedOnStop) this.dispatchEvent(new Event('ended'))
    }
  }

  class FakeAudioNode {
    disconnectCalls = 0
    connect(dest: unknown): unknown {
      return dest
    }
    disconnect(): void {
      this.disconnectCalls += 1
    }
  }

  type FakeAudioProcessingEvent = { inputBuffer: { getChannelData(channel: number): Float32Array } }

  class FakeScriptProcessorNode extends FakeAudioNode {
    onaudioprocess: ((event: FakeAudioProcessingEvent) => void) | null = null
    /** Simulates the browser invoking this node's callback with a real
     * audio buffer -- the only way startRealCapture's onaudioprocess wiring
     * can be exercised without a real AudioContext. */
    triggerAudioProcess(channelData: Float32Array): void {
      this.onaudioprocess?.({ inputBuffer: { getChannelData: () => channelData } })
    }
  }

  class FakeGainNode extends FakeAudioNode {
    gain = { value: 1 }
  }

  interface FakeAudioContextInstance {
    sampleRate: number
    closeCalls: number
    requestedOptions: { sampleRate?: number } | undefined
  }

  /** Every stub this describe block installs is torn down here, regardless
   * of whether the test that installed it passed or threw -- otherwise a
   * failing assertion could leave a fake AudioContext/navigator.mediaDevices
   * (or a pushed-state URL search string, which isWakeDiagnosticsEnabled
   * reads live) bleeding into a later, unrelated test. */
  let pendingRestores: Array<() => void> = []
  afterEach(() => {
    while (pendingRestores.length > 0) pendingRestores.pop()!()
    window.history.pushState({}, '', '/')
  })

  /** Installs fake getUserMedia + AudioContext onto the real globals (jsdom
   * has neither), wired the way a real browser would be, and returns
   * handles the test can inspect/drive. `actualSampleRate` lets a test
   * simulate a browser that grants a different rate than requested (see
   * startRealCapture's sampleRate-mismatch diagnostics); `dispatchEndedOnStop`
   * is forwarded to FakeMediaStreamTrack above. */
  function installFakeBrowserAudio(
    options: { actualSampleRate?: number; dispatchEndedOnStop?: boolean } = {},
  ): {
    track: FakeMediaStreamTrack
    getProcessor: () => FakeScriptProcessorNode | null
    getSource: () => FakeAudioNode | null
    getSilence: () => FakeGainNode | null
    getAudioContext: () => FakeAudioContextInstance | null
  } {
    const track = new FakeMediaStreamTrack(options.dispatchEndedOnStop ?? false)
    const stream = { getTracks: () => [track] }

    const originalMediaDevicesDescriptor = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices')
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => stream,
      },
    })

    let processor: FakeScriptProcessorNode | null = null
    let source: FakeAudioNode | null = null
    let silence: FakeGainNode | null = null
    let audioContextInstance: FakeAudioContextInstance | null = null

    // A plain constructor function rather than a class: it explicitly
    // returns an object literal, which (per ordinary `new` semantics) is
    // what `new AudioContext(...)` in startRealCapture then receives --
    // sidesteps ever needing to alias `this` to register the created
    // instance for the test to inspect below.
    function FakeAudioContext(ctorOptions?: { sampleRate?: number }): FakeAudioContextInstance & {
      destination: FakeAudioNode
      createMediaStreamSource(stream: unknown): FakeAudioNode
      createScriptProcessor(bufferSize: number, numIn: number, numOut: number): FakeScriptProcessorNode
      createGain(): FakeGainNode
      close(): Promise<void>
    } {
      const instance = {
        sampleRate: options.actualSampleRate ?? ctorOptions?.sampleRate ?? SAMPLE_RATE,
        closeCalls: 0,
        requestedOptions: ctorOptions,
        destination: new FakeAudioNode(),
        createMediaStreamSource(_stream: unknown): FakeAudioNode {
          source = new FakeAudioNode()
          return source
        },
        createScriptProcessor(_bufferSize: number, _numIn: number, _numOut: number): FakeScriptProcessorNode {
          processor = new FakeScriptProcessorNode()
          return processor
        },
        createGain(): FakeGainNode {
          silence = new FakeGainNode()
          return silence
        },
        async close(): Promise<void> {
          instance.closeCalls += 1
        },
      }
      audioContextInstance = instance
      return instance
    }

    vi.stubGlobal('AudioContext', FakeAudioContext)

    pendingRestores.push(() => {
      if (originalMediaDevicesDescriptor) {
        Object.defineProperty(navigator, 'mediaDevices', originalMediaDevicesDescriptor)
      } else {
        Reflect.deleteProperty(navigator, 'mediaDevices')
      }
      vi.unstubAllGlobals()
    })

    return {
      track,
      getProcessor: () => processor,
      getSource: () => source,
      getSilence: () => silence,
      getAudioContext: () => audioContextInstance,
    }
  }

  // Item 1 -- the headline regression this whole block exists to catch: a
  // MediaStreamTrack ending mid-session (mic permission revoked, device
  // unplugged) must reach onCaptureLost. MUST FAIL if
  // `const handleTrackEnded = () => onCaptureLost()` is ever gutted to a
  // no-op -- see this file's final report for confirmation that it does.
  it("delivers the MediaStreamTrack's 'ended' event to onCaptureLost", async () => {
    const { track } = installFakeBrowserAudio()
    let lostCalls = 0

    await startRealCapture(
      () => {},
      () => {
        lostCalls += 1
      },
    )

    track.dispatchEvent(new Event('ended'))

    expect(lostCalls).toBe(1)
  })

  // Item 2 -- close() must release everything it owns: the AudioContext,
  // the ScriptProcessorNode's connections, and the microphone track.
  it('close() closes the AudioContext, disconnects the audio graph, and stops the track', async () => {
    const { track, getAudioContext, getProcessor, getSource, getSilence } = installFakeBrowserAudio()
    const capture = await startRealCapture(() => {}, () => {})

    expect(getAudioContext()?.closeCalls).toBe(0)
    expect(track.stopCalls).toBe(0)

    await capture.close()

    expect(getAudioContext()?.closeCalls).toBe(1)
    expect(getProcessor()?.disconnectCalls).toBe(1)
    expect(getSource()?.disconnectCalls).toBe(1)
    expect(getSilence()?.disconnectCalls).toBe(1)
    expect(track.stopCalls).toBe(1)
    // Also detached so a stray browser callback after close() can't fire.
    expect(getProcessor()?.onaudioprocess).toBeNull()
  })

  // Item 3 -- close() must remove the 'ended' listener BEFORE stopping the
  // track, so a deliberate close() can never be misreported as a capture
  // loss. dispatchEndedOnStop simulates a (spec-noncompliant) browser that
  // fires 'ended' as a side effect of track.stop() -- the one case where
  // getting the order backwards would actually be observable.
  it('close() removes the ended listener before stopping the track, so a deliberate stop cannot masquerade as a capture loss', async () => {
    const { track } = installFakeBrowserAudio({ dispatchEndedOnStop: true })
    let lostCalls = 0
    const capture = await startRealCapture(
      () => {},
      () => {
        lostCalls += 1
      },
    )

    await capture.close()

    expect(track.stopCalls).toBe(1) // the stop did happen...
    expect(lostCalls).toBe(0) // ...but was never reported as a capture loss
  })

  // Item 4 -- audio arriving through the ScriptProcessorNode callback must
  // reach onSamples unchanged: mono float32 in [-1, 1]. The ×32767
  // int16-domain scaling happens later, in runDetectionPass -- deliberately
  // NOT asserted here.
  it('delivers ScriptProcessorNode audio to onSamples as mono float32 in [-1, 1], copied rather than aliased', async () => {
    const { getProcessor } = installFakeBrowserAudio()
    const received: Float32Array[] = []

    await startRealCapture((samples) => {
      received.push(samples)
    }, () => {})

    const channelData = new Float32Array([0.5, -0.3, 1, -1, 0.1])
    getProcessor()?.triggerAudioProcess(channelData)

    expect(received).toHaveLength(1)
    const expectedSamples = [0.5, -0.3, 1, -1, 0.1]
    expect(received[0]).toHaveLength(expectedSamples.length)
    // Float32Array storage loses a little precision vs. the plain numbers
    // above (e.g. -0.3 -> -0.30000001192092896) -- toBeCloseTo, not toEqual.
    expectedSamples.forEach((expected, i) => expect(received[0][i]).toBeCloseTo(expected, 5))
    for (const s of received[0]) expect(Math.abs(s)).toBeLessThanOrEqual(1) // still Web Audio's native domain

    // A copy, not a live view onto the browser's reused buffer (see
    // startRealCapture's own comment on this line): mutating the source
    // after the callback must not retroactively change what was delivered.
    channelData[0] = 999
    expect(received[0][0]).toBe(0.5)
  })

  // Item 5 -- the requested rate must be exactly 16 kHz (what the whole
  // model pipeline is calibrated on -- see SAMPLE_RATE), and a browser that
  // silently grants a different rate must be reported, not ignored.
  it('requests a 16 kHz AudioContext', async () => {
    const { getAudioContext } = installFakeBrowserAudio()

    await startRealCapture(() => {}, () => {})

    expect(SAMPLE_RATE).toBe(16000)
    expect(getAudioContext()?.requestedOptions?.sampleRate).toBe(SAMPLE_RATE)
  })

  it('logs a sample-rate mismatch (diagnostics on) when the browser grants a different rate than requested', async () => {
    window.history.pushState({}, '', '/?debug=wake')
    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {})
    installFakeBrowserAudio({ actualSampleRate: 44100 })

    await startRealCapture(() => {}, () => {})

    expect(logSpy).toHaveBeenCalledWith(expect.stringContaining('MISMATCH'))
    expect(logSpy).toHaveBeenCalledWith(expect.stringContaining(`requested=${SAMPLE_RATE}`))
    expect(logSpy).toHaveBeenCalledWith(expect.stringContaining('actual=44100'))

    logSpy.mockRestore()
  })

  it('does not report a mismatch (diagnostics on) when the browser grants exactly the requested rate', async () => {
    window.history.pushState({}, '', '/?debug=wake')
    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {})
    installFakeBrowserAudio({ actualSampleRate: SAMPLE_RATE })

    await startRealCapture(() => {}, () => {})

    const loggedMismatch = logSpy.mock.calls.some(
      (call) => typeof call[0] === 'string' && call[0].includes('MISMATCH'),
    )
    expect(loggedMismatch).toBe(false)

    logSpy.mockRestore()
  })
})

// ---------------------------------------------------------------------------
// loadRealSessions -- fetches and instantiates the three real ONNX
// sessions. Like startRealCapture above, nothing in this file's other
// describe blocks ever calls it: createOpenWakeWordDetectorWithDeps only
// ever sees an injected `loadSessions`. Stubs the onnxruntime-web LIBRARY
// (see the vi.mock at the top of this file), not our own logic.
describe('loadRealSessions (stubbed onnxruntime-web)', () => {
  it('loads the three vendored ONNX models by path, forces single-threaded WASM, and wraps sessions/tensors so the rest of this file can use them', async () => {
    const sessions = await loadRealSessions()

    // The exact three vendored model paths, in the melspec/embedding/wake
    // order runDetectionPass expects (see this module's pipeline diagram).
    expect(ortWasm.InferenceSession.create).toHaveBeenNthCalledWith(1, '/wake-models/melspectrogram.onnx')
    expect(ortWasm.InferenceSession.create).toHaveBeenNthCalledWith(2, '/wake-models/embedding_model.onnx')
    expect(ortWasm.InferenceSession.create).toHaveBeenNthCalledWith(3, '/wake-models/hey_jarvis_v0.1.onnx')

    // Single-threaded WASM -- see this file's own comment on why (no
    // COOP/COEP cross-origin isolation headers set for SharedArrayBuffer).
    expect(ortWasm.env.wasm.numThreads).toBe(1)

    // wrap() must pass inputNames/outputNames through from the real
    // session untouched.
    expect(sessions.melspec.inputNames).toEqual(['/wake-models/melspectrogram.onnx#in'])
    expect(sessions.melspec.outputNames).toEqual(['/wake-models/melspectrogram.onnx#out'])

    // makeTensor must build a real ort.Tensor, not some other shape.
    const tensor = sessions.makeTensor(new Float32Array([1, 2, 3]), [1, 3])
    expect(tensor).toBeInstanceOf(ortWasm.Tensor)

    // wrap()'s run() must actually delegate to the real session's run(),
    // not swallow or reshape its result.
    const result = await sessions.melspec.run({ [sessions.melspec.inputNames[0]]: tensor })
    expect(Array.from(result[sessions.melspec.outputNames[0]].data as ArrayLike<number>)).toEqual([7])
  })
})
