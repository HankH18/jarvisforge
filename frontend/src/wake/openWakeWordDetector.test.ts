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

import { describe, expect, it } from 'vitest'
import {
  createOpenWakeWordDetectorWithDeps,
  HOP_SAMPLES,
  SAMPLES_NEEDED,
  type AudioCapture,
  type OpenWakeWordDeps,
  type OpenWakeWordSessions,
  type OrtSessionLike,
  type OrtTensorLike,
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
 * enough data at all. */
function makeFakeSessions(wakeScore: () => number) {
  let melspecCalls = 0
  let embeddingCalls = 0
  let wakeCalls = 0
  let lastWakeFeedDims: readonly number[] | null = null

  const melspec: OrtSessionLike = {
    inputNames: ['input'],
    outputNames: ['output'],
    async run(feeds): Promise<Record<string, OrtTensorLike>> {
      melspecCalls += 1
      const samples = feeds.input.dims[1]
      const frames = Math.floor(samples / 160) - 3
      return { output: { data: new Float32Array(frames * 32), dims: [1, 1, frames, 32] } }
    },
  }

  const embedding: OrtSessionLike = {
    inputNames: ['input_1'],
    outputNames: ['conv2d_19'],
    async run(feeds): Promise<Record<string, OrtTensorLike>> {
      embeddingCalls += 1
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
  }
}

/** Fake microphone: lets a test push raw PCM samples on demand instead of
 * needing a real getUserMedia()/AudioContext. */
function makeFakeCapture() {
  let onSamples: ((samples: Float32Array) => void) | null = null
  let closeCalls = 0

  const startCapture: OpenWakeWordDeps['startCapture'] = async (cb) => {
    onSamples = cb
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
