// Real wake-word detector backed by openWakeWord's pretrained "hey jarvis"
// ONNX model chain, run entirely on-device via onnxruntime-web's WASM
// execution provider. Implements WakeDetector. Replaces
// porcupineWakeDetector.ts as the default (see createWakeDetector.ts) --
// Porcupine is blocked behind commercial licensing Hank cannot obtain
// before submission. This engine needs no API key and no signup: the
// three ONNX models are vendored into frontend/public/wake-models/, and
// the onnxruntime-web WASM runtime is bundled by Vite as a same-origin
// build asset (see loadRealSessions below for why that needed no manual
// vendoring), so detection runs fully offline in the browser -- no
// network call happens once the page and its assets have loaded. See R11
// / docs/SPEC.md and wakeDetector.ts's header for why "on-device,
// publishes nothing upstream until woken" is a privacy guarantee, not a
// UX detail.
//
// Model provenance and license -- READ BEFORE REDISTRIBUTING:
// https://github.com/dscripka/openWakeWord (release v0.5.1). The
// openWakeWord framework code is Apache-2.0, but these three pretrained
// model WEIGHT files are Creative Commons Attribution-NonCommercial-
// ShareAlike 4.0 International (CC BY-NC-SA 4.0), NOT Apache-2.0 -- full
// detail and exact upstream wording in frontend/public/wake-models/NOTICE.md.
//
// Pipeline -- tensor shapes below were verified directly against the
// vendored .onnx files by loading them with onnxruntime-web and inspecting
// real input/output tensors, not assumed from documentation:
//
//   melspectrogram.onnx   [1, samples] float32
//                       -> [1, 1, frames, 32]   (frames = samples/160 - 3;
//                          160-sample/10ms hop, 3 border frames the model
//                          itself trims)
//   embedding_model.onnx  [N, 76, 32, 1] float32
//                       -> [N, 1, 1, 96]   (N windows of 76 consecutive mel
//                          frames -> one 96-dim embedding per window)
//   hey_jarvis_v0.1.onnx  [1, 16, 96] float32
//                       -> [1, 1]   (16 consecutive embeddings -> one 0..1
//                          score; already a probability -- confirmed by
//                          probing the model with an all-zero input and
//                          getting ~0.0002, not something needing a
//                          separate sigmoid)
//
// Buffering strategy: rather than replicate openWakeWord's internal
// incremental streaming buffer byte-for-byte (its own source code notes
// "slight numerical issues" in that path vs. computing over a full clip),
// this recomputes the melspectrogram and all 16 embedding windows from a
// trailing raw-audio buffer every time 1280 new samples (80 ms) have
// arrived. That is behaviorally equivalent to running openWakeWord's
// non-streaming reference pipeline (predict_clip / embed_clips) over the
// last ~2 seconds of audio each hop -- more CPU than the incremental
// approach, trivially affordable for one stream on modern hardware, and
// avoids reproducing streaming-buffer bookkeeping this file's author could
// not verify against upstream beyond reading its source.
//
// This module owns microphone capture end-to-end (getUserMedia + a 16 kHz
// AudioContext), exactly like porcupineWakeDetector.ts's contract requires
// -- it must never touch livekit-client or the RoomSession boundary
// (lib/room.ts). See state/wakeSessionController.ts for the one place the
// two worlds are wired together.
//
// Testability: model-session creation and audio-capture start-up are
// injected via `Deps` (see createOpenWakeWordDetectorWithDeps below),
// defaulting to the real onnxruntime-web / getUserMedia implementations in
// `createOpenWakeWordDetector()`. Tests inject fake ONNX sessions whose
// `run()` returns controlled tensors, so the real buffering / windowing /
// threshold / cooldown logic in this file is exercised for real -- see
// openWakeWordDetector.test.ts's header for exactly what that does and
// does not prove about real-world wake-word detection.

import type { WakeDetector } from './wakeDetector'

const SAMPLE_RATE = 16000
/** 80 ms of audio -- openWakeWord's own recommended step size. A new
 * detection pass is attempted every time this many new samples arrive.
 * Exported (with the other shape constants below) so tests can feed
 * exactly enough audio to trigger a pass without duplicating this math. */
export const HOP_SAMPLES = 1280
const MEL_BINS = 32
const MEL_WINDOW_FRAMES = 76
const EMBED_STEP_FRAMES = 8
const EMBED_DIM = 96
export const NUM_EMBEDDINGS = 16
/** How many trailing mel frames a detection pass needs: one 76-frame
 * window, plus 15 more windows each starting 8 frames later. */
const FRAMES_NEEDED = MEL_WINDOW_FRAMES + (NUM_EMBEDDINGS - 1) * EMBED_STEP_FRAMES // 196
/** Inverse of melspectrogram.onnx's verified frames = samples/160 - 3. */
export const SAMPLES_NEEDED = (FRAMES_NEEDED + 3) * 160 // 31840
/** Raw-audio ring capacity: enough for one detection pass plus a little
 * slack, trimmed back down after every pass so memory doesn't grow. */
const BUFFER_CAP_SAMPLES = SAMPLES_NEEDED + HOP_SAMPLES
export const WAKE_THRESHOLD = 0.5
/** Minimum time between two wake events, so one spoken "hey Jarvis" can't
 * fire onWake more than once while its audio is still in the buffer. */
const COOLDOWN_MS = 1500

/** Structural subset of onnxruntime-web's Tensor this file needs. Typed by
 * hand so tests can build one without importing onnxruntime-web at all. */
export interface OrtTensorLike {
  readonly data: ArrayLike<number>
  readonly dims: readonly number[]
}

/** Structural subset of onnxruntime-web's InferenceSession this file
 * needs. */
export interface OrtSessionLike {
  readonly inputNames: readonly string[]
  readonly outputNames: readonly string[]
  run(feeds: Record<string, OrtTensorLike>): Promise<Record<string, OrtTensorLike>>
}

export interface OpenWakeWordSessions {
  melspec: OrtSessionLike
  embedding: OrtSessionLike
  wake: OrtSessionLike
  makeTensor(data: Float32Array, dims: readonly number[]): OrtTensorLike
}

export interface AudioCapture {
  close(): Promise<void>
}

/** Everything this file needs from the outside world, injected so tests
 * can drive the real detection/buffering logic without a browser, a
 * microphone, or a WASM runtime. `createOpenWakeWordDetector()` below
 * supplies the real implementations. */
export interface OpenWakeWordDeps {
  /** Load all three ONNX sessions. Rejecting here is how "models failed to
   * load" surfaces as a rejected start() (wakeDetector.ts's contract) --
   * this must never be swallowed into a silently-not-listening detector. */
  loadSessions(): Promise<OpenWakeWordSessions>
  /** Start 16 kHz mono microphone capture. `onSamples` is called with each
   * new batch of raw PCM. Rejecting here is how "mic permission denied"
   * surfaces as a rejected start(), same as loadSessions above. */
  startCapture(onSamples: (samples: Float32Array) => void): Promise<AudioCapture>
}

function squeeze(dims: readonly number[]): number[] {
  return dims.filter((d) => d !== 1)
}

/** Runs the melspectrogram -> embedding -> wake chain over the trailing
 * SAMPLES_NEEDED samples of `buffer`. Returns the wake score, or `null` if
 * there isn't enough audio buffered yet to run a full pass (only happens
 * in the first ~2 seconds after start()). */
async function runDetectionPass(
  sessions: OpenWakeWordSessions,
  buffer: number[],
): Promise<number | null> {
  if (buffer.length < SAMPLES_NEEDED) return null
  const samples = Float32Array.from(buffer.slice(buffer.length - SAMPLES_NEEDED))

  const melInput = sessions.makeTensor(samples, [1, samples.length])
  const melFeeds = { [sessions.melspec.inputNames[0]]: melInput }
  const melOut = await sessions.melspec.run(melFeeds)
  const melTensor = melOut[sessions.melspec.outputNames[0]]
  const melDims = squeeze(melTensor.dims) // [frames, 32]
  const frames = melDims[0]
  const bins = melDims[1] ?? MEL_BINS
  if (frames < FRAMES_NEEDED) return null
  const melData = melTensor.data
  const startFrame = frames - FRAMES_NEEDED

  const windows = new Float32Array(NUM_EMBEDDINGS * MEL_WINDOW_FRAMES * MEL_BINS)
  for (let w = 0; w < NUM_EMBEDDINGS; w += 1) {
    const frameOffset = startFrame + w * EMBED_STEP_FRAMES
    for (let f = 0; f < MEL_WINDOW_FRAMES; f += 1) {
      const srcFrame = frameOffset + f
      for (let b = 0; b < MEL_BINS; b += 1) {
        windows[(w * MEL_WINDOW_FRAMES + f) * MEL_BINS + b] = melData[srcFrame * bins + b]
      }
    }
  }

  const embInput = sessions.makeTensor(windows, [NUM_EMBEDDINGS, MEL_WINDOW_FRAMES, MEL_BINS, 1])
  const embFeeds = { [sessions.embedding.inputNames[0]]: embInput }
  const embOut = await sessions.embedding.run(embFeeds)
  const embTensor = embOut[sessions.embedding.outputNames[0]]
  const embData = Float32Array.from(embTensor.data as ArrayLike<number>)

  const wakeInput = sessions.makeTensor(embData, [1, NUM_EMBEDDINGS, EMBED_DIM])
  const wakeFeeds = { [sessions.wake.inputNames[0]]: wakeInput }
  const wakeOut = await sessions.wake.run(wakeFeeds)
  const wakeTensor = wakeOut[sessions.wake.outputNames[0]]
  return Number(wakeTensor.data[0])
}

/** The testable core: all real-world I/O (ONNX runtime, microphone) comes
 * in through `deps`. See createOpenWakeWordDetector() for the real thing. */
export function createOpenWakeWordDetectorWithDeps(deps: OpenWakeWordDeps): WakeDetector {
  let capture: AudioCapture | null = null
  let buffer: number[] = []
  let newSinceLastRun = 0
  let passInFlight = false
  let lastFireAt = 0
  let stopped = true

  return {
    async start(onWake: () => void): Promise<void> {
      // Models before microphone: if the pipeline can't run at all, no
      // point prompting for mic permission first.
      const sessions = await deps.loadSessions()

      buffer = []
      newSinceLastRun = 0
      lastFireAt = 0
      stopped = false

      capture = await deps.startCapture((samples) => {
        if (stopped) return
        for (let i = 0; i < samples.length; i += 1) buffer.push(samples[i])
        if (buffer.length > BUFFER_CAP_SAMPLES) {
          buffer.splice(0, buffer.length - BUFFER_CAP_SAMPLES)
        }
        newSinceLastRun += samples.length
        if (newSinceLastRun < HOP_SAMPLES || passInFlight) return
        newSinceLastRun = 0
        passInFlight = true
        runDetectionPass(sessions, buffer)
          .then((score) => {
            if (score === null || stopped) return
            const now = Date.now()
            if (score >= WAKE_THRESHOLD && now - lastFireAt >= COOLDOWN_MS) {
              lastFireAt = now
              onWake()
            }
          })
          .catch((err: unknown) => {
            console.error('[wake] openWakeWord detection pass failed', err)
          })
          .finally(() => {
            passInFlight = false
          })
      })
    },

    async stop(): Promise<void> {
      stopped = true
      const c = capture
      capture = null
      buffer = []
      await c?.close()
    },
  }
}

let realOrtModulePromise: Promise<typeof import('onnxruntime-web/wasm')> | null = null

async function loadRealSessions(): Promise<OpenWakeWordSessions> {
  if (!realOrtModulePromise) realOrtModulePromise = import('onnxruntime-web/wasm')
  const ort = await realOrtModulePromise

  // Deliberately no explicit ort.env.wasm.wasmPaths override here. The
  // 'onnxruntime-web/wasm' subpath import resolves (see package.json's
  // "exports"."./wasm") to its self-contained "bundle" build, which
  // references its own .wasm binary via `new URL(..., import.meta.url)` --
  // Vite's standard asset-bundling pattern. Confirmed by inspecting a real
  // `npm run build` output: the wasm binary lands as a content-hashed file
  // under dist/assets/, served from this app's own origin, not any CDN --
  // satisfying R11's "publishes no audio upstream" boundary (no network
  // dependency beyond this app's own origin) without hand-vendoring a
  // second copy into public/ (an earlier version of this file did that,
  // and Vite silently duplicated the ~13 MB binary into the build output
  // rather than using it -- this file's header/final report note that).
  //
  // Single-threaded: the alternative needs SharedArrayBuffer and
  // cross-origin-isolation response headers (COOP/COEP) this app doesn't
  // set. A wake-word pass every 80 ms is cheap enough single-threaded.
  ort.env.wasm.numThreads = 1

  const [melspec, embedding, wake] = await Promise.all([
    ort.InferenceSession.create('/wake-models/melspectrogram.onnx'),
    ort.InferenceSession.create('/wake-models/embedding_model.onnx'),
    ort.InferenceSession.create('/wake-models/hey_jarvis_v0.1.onnx'),
  ])

  // onnxruntime-web's real InferenceSession.run() wants full ort.Tensor
  // objects (type, location, gpu buffer, ...), a much richer shape than
  // OrtTensorLike's minimal {data, dims} -- that's deliberate: it's what
  // lets tests build fake sessions without importing onnxruntime-web at
  // all (see openWakeWordDetector.test.ts). The values actually flowing
  // through this cast at runtime are always real ort.Tensor instances
  // (built by makeTensor below), so the cast is sound in practice even
  // though OrtSessionLike's narrower type can't prove that to TS.
  const wrap = (session: Awaited<ReturnType<typeof ort.InferenceSession.create>>): OrtSessionLike => ({
    inputNames: session.inputNames,
    outputNames: session.outputNames,
    async run(feeds) {
      const result = await session.run(feeds as unknown as Parameters<typeof session.run>[0])
      return result as unknown as Record<string, OrtTensorLike>
    },
  })

  return {
    melspec: wrap(melspec),
    embedding: wrap(embedding),
    wake: wrap(wake),
    makeTensor: (data, dims) => new ort.Tensor('float32', data, dims),
  }
}

async function startRealCapture(onSamples: (samples: Float32Array) => void): Promise<AudioCapture> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  })
  const audioContext = new AudioContext({ sampleRate: SAMPLE_RATE })
  const source = audioContext.createMediaStreamSource(stream)
  // ScriptProcessorNode is deprecated but still implemented everywhere
  // that matters (Chrome, Firefox, Safari); chosen over AudioWorklet to
  // avoid depending on Vite's handling of a separately-bundled worklet
  // module asset, which this file's author could not verify against a
  // live browser build (see module header / final report on test scope).
  // The work done per callback here is trivial (copy samples into a plain
  // array); the actual ONNX inference is scheduled asynchronously outside
  // this callback, so main-thread jank from ScriptProcessorNode is not a
  // concern for a background listening feature like this one.
  const processor = audioContext.createScriptProcessor(4096, 1, 1)
  processor.onaudioprocess = (event: AudioProcessingEvent) => {
    // Copy: the browser reuses/invalidates the underlying buffer for the
    // next callback.
    onSamples(Float32Array.from(event.inputBuffer.getChannelData(0)))
  }
  source.connect(processor)
  // ScriptProcessorNode only fires while connected into the graph; route
  // through a muted gain node so nothing is audibly echoed back to the user.
  const silence = audioContext.createGain()
  silence.gain.value = 0
  processor.connect(silence)
  silence.connect(audioContext.destination)

  return {
    async close() {
      processor.onaudioprocess = null
      source.disconnect()
      processor.disconnect()
      silence.disconnect()
      for (const track of stream.getTracks()) track.stop()
      await audioContext.close()
    },
  }
}

/** Production factory: the real onnxruntime-web + getUserMedia
 * implementation. createWakeDetector.ts uses this as the default. */
export function createOpenWakeWordDetector(): WakeDetector {
  return createOpenWakeWordDetectorWithDeps({
    loadSessions: loadRealSessions,
    startCapture: startRealCapture,
  })
}
