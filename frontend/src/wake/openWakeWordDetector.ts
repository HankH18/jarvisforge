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
//   melspectrogram.onnx   [1, samples] float32 (int16-scale amplitude, NOT
//                          Web Audio's native [-1,1] -- see
//                          WEB_AUDIO_TO_INT16_SCALE below for why)
//                       -> [1, 1, frames, 32]   (frames = samples/160 - 3;
//                          160-sample/10ms hop, 3 border frames the model
//                          itself trims)
//                          -- raw output is NOT what embedding_model.onnx
//                          consumes: upstream applies spec/10 + 2 to it
//                          first (see MELSPEC_TRANSFORM_DIVISOR/_OFFSET
//                          below), applied here before windowing
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
   * surfaces as a rejected start(), same as loadSessions above.
   *
   * `onCaptureLost` is this capture's own way to report that it has
   * stopped supplying audio ON ITS OWN, sometime after this promise has
   * already resolved -- see startRealCapture below for exactly which
   * browser signal that maps to (the MediaStreamTrack `ended` event) and
   * why. Never called as a result of this capture's own `close()`. This is
   * the source, one layer down, of WakeDetector's `onFailure` (see
   * wakeDetector.ts) -- createOpenWakeWordDetectorWithDeps below is what
   * turns this into that. */
  startCapture(
    onSamples: (samples: Float32Array) => void,
    onCaptureLost: () => void,
  ): Promise<AudioCapture>
}

function squeeze(dims: readonly number[]): number[] {
  return dims.filter((d) => d !== 1)
}

// ---------------------------------------------------------------------------
// Diagnostics (opt-in, observability only). This exists because a deployed
// build once loaded every model successfully -- server logs proved
// ort-wasm-simd-threaded.wasm and all three /wake-models/*.onnx were
// fetched -- yet no room join ever followed a spoken wake word, and this
// file logged nothing that could tell apart three very different causes:
// (a) audio never reaching inference at all, (b) inference running but
// scoring under WAKE_THRESHOLD, or (c) inference throwing every cycle with
// the error swallowed. Everything below is additive: it only *reads*
// values the detection call site already has (the resolved score, a
// thrown error) and publishes them somewhere a caller can look. Nothing
// here ever feeds back into WAKE_THRESHOLD, COOLDOWN_MS, or onWake --
// detection behaviour is identical whether this is enabled or not (see
// openWakeWordDetector.test.ts's diagnostics-parity test).
//
// Gated by a URL query parameter, not a build flag, so it can be toggled
// on an already-deployed build without a rebuild.

/** `?debug=wake` or `?debug=1` in the page's query string turns diagnostics
 * on; anything else (including no `debug` param at all) leaves it off.
 * Exported so App.tsx's on-screen readout uses this exact gate rather than
 * a second flag that could drift out of sync with the one below. A pure
 * function of the search string, not a `window.location` read, so it stays
 * testable without mocking global state -- createOpenWakeWordDetector()
 * and startRealCapture() below are the only real call sites that read
 * `window.location.search` itself. */
export function isWakeDiagnosticsEnabled(search: string): boolean {
  const debug = new URLSearchParams(search).get('debug')
  return debug === 'wake' || debug === '1'
}

/** What the on-screen readout and the throttled console log both show.
 * `score`/`max` are `null` only before the first detection pass of the
 * current listening session has completed (the first ~2s while the
 * trailing-audio buffer fills) -- not to be confused with a real score of
 * 0. A `NaN` score is preserved as-is rather than coerced into something
 * friendlier: that IS one of the failure signatures this exists to
 * surface, so hiding it would defeat the point. `max` deliberately ignores
 * NaN readings when updating (see the `.then` handler below) so one NaN
 * frame can't permanently pin the running max at NaN and hide a real peak
 * that follows it. */
export interface WakeDiagnosticsSnapshot {
  score: number | null
  max: number | null
  error: string | null
}

const INITIAL_WAKE_DIAGNOSTICS: WakeDiagnosticsSnapshot = { score: null, max: null, error: null }

let wakeDiagnosticsSnapshot: WakeDiagnosticsSnapshot = INITIAL_WAKE_DIAGNOSTICS
const wakeDiagnosticsListeners = new Set<(snapshot: WakeDiagnosticsSnapshot) => void>()

/** The current snapshot, for a subscriber to seed its own state with
 * before the first update arrives -- mirrors how usePassiveWakeSession.ts
 * seeds its React state from controller.getState() before subscribing. */
export function getWakeDiagnosticsSnapshot(): WakeDiagnosticsSnapshot {
  return wakeDiagnosticsSnapshot
}

/** Notified after every diagnostics update. Not replayed on subscribe --
 * same convention as passiveActiveMachine.ts's subscribe. Returns an
 * unsubscribe function. */
export function subscribeWakeDiagnostics(listener: (snapshot: WakeDiagnosticsSnapshot) => void): () => void {
  wakeDiagnosticsListeners.add(listener)
  return () => {
    wakeDiagnosticsListeners.delete(listener)
  }
}

function publishWakeDiagnostics(snapshot: WakeDiagnosticsSnapshot): void {
  wakeDiagnosticsSnapshot = snapshot
  for (const listener of Array.from(wakeDiagnosticsListeners)) listener(snapshot)
}

function formatWakeDiagnosticValue(value: number | null): string {
  if (value === null) return 'n/a'
  if (Number.isNaN(value)) return 'NaN'
  return value.toFixed(3)
}

/** Minimum gap between throttled console lines. A detection pass runs
 * roughly every 80ms (~12.5/s -- HOP_SAMPLES at SAMPLE_RATE); logging every
 * one of those would bury the signal in noise (and, per the task this
 * shipped for, still not show a peak that happened between two scrollback
 * lines). Capping at ~4/s keeps it readable while every pass still updates
 * the on-screen readout and the running max immediately, so a peak while
 * speaking is visible even if the exact frame that produced it scrolled
 * past in the console. */
const DIAG_LOG_INTERVAL_MS = 250

/** Web Audio's `AudioBuffer.getChannelData()` / `ScriptProcessorNode`
 * samples (what fills `buffer` below -- see startRealCapture) are float32
 * normalized to [-1, 1] per the Web Audio spec. openWakeWord's
 * melspectrogram.onnx does NOT consume that domain: it expects raw
 * 16-bit-PCM-scale values (roughly [-32768, 32767]) represented as
 * float32, not [-1, 1] floats. Independently re-derived against the exact
 * vendored release (v0.5.1 -- see frontend/public/wake-models/NOTICE.md),
 * not assumed:
 *   - openwakeword/utils.py's `AudioFeatures._get_melspectrogram` raises
 *     `ValueError("Input data must be 16-bit integers (i.e., 16-bit PCM
 *     audio). You provided {x.dtype} data.")` whenever the array it's
 *     handed isn't `np.int16` -- only *after* that check does it cast to
 *     float32 to feed the ONNX model, so the model's real input domain is
 *     int16-scale magnitudes carried in a float32 array, not [-1, 1].
 *   - openwakeword/model.py's `predict_clip()` documents its input as
 *     "a 16-bit PCM, 16 khz, single-channel WAV file, or an 1D array
 *     containing the same type of data", and loads WAV files via
 *     `np.frombuffer(f.readframes(...), dtype=np.int16)`.
 *   - utils.py's own `AudioFeatures.get_embedding_shape()` synthesizes
 *     test audio the same way this file now does:
 *     `(np.random.uniform(-1, 1, n) * 32767).astype(np.int16)` -- a
 *     [-1, 1] float scaled by exactly 32767 before being treated as
 *     int16-domain audio, confirming both the expected domain and the
 *     scale factor upstream itself uses.
 * Without this scaling, melspectrogram.onnx is fed audio ~32768x quieter
 * than what it was calibrated on -- effectively silence -- regardless of
 * what was said. This conversion belongs ONLY here, on the raw PCM going
 * into melspectrogram.onnx: embedding_model.onnx and hey_jarvis_v0.1.onnx
 * consume melspectrogram.onnx's *output*, already in whatever units that
 * model emits, so rescaling anything past this point would be wrong (and
 * would double-apply this correction).
 *
 * Multiplying by 32767, not 32768, to match upstream's own literal above;
 * these values are never actually cast to an int16 typed array (the
 * model's own float32 cast handles that), so 32767 vs. 32768 is a
 * fraction-of-an-LSB difference either way, and matching upstream's exact
 * constant is the more defensible choice when nothing else forces a pick.
 * Clamped to [-1, 1] first because a browser's audio pipeline can hand
 * back samples very slightly outside that range (float rounding) --
 * without the clamp, those would scale past int16 range and distort;
 * normal in-range samples pass through the clamp untouched. */
const WEB_AUDIO_TO_INT16_SCALE = 32767

/** openWakeWord applies an explicit post-processing transform to
 * melspectrogram.onnx's raw output before that output is ever used --
 * whether by the streaming path or the non-streaming predict_clip/
 * embed_clips path this file mirrors (see the buffering-strategy note in
 * this file's header). Independently re-derived against the exact
 * vendored release (v0.5.1), not assumed:
 *   - openwakeword/utils.py's `AudioFeatures._get_melspectrogram` takes a
 *     `melspec_transform: Callable` parameter defaulting to exactly
 *     `lambda x: x/10 + 2`, applied via `spec = melspec_transform(spec)`
 *     immediately after `spec = np.squeeze(outputs[0])` (the ONNX model's
 *     raw output) and before `_get_melspectrogram` returns -- upstream's
 *     own comment on that line reads "Arbitrary transform of
 *     melspectrogram".
 *   - Nothing overrides that default anywhere in the streaming
 *     (`_streaming_melspectrogram`) or batch (`_get_melspectrogram_batch`,
 *     `embed_clips`) call paths, so every consumer of a melspectrogram --
 *     including the 76-frame windows handed to embedding_model.onnx --
 *     sees the transformed values, never the raw ONNX output.
 * Without this transform, embedding_model.onnx (itself trained exclusively
 * on transformed input) receives melspectrogram frames on the wrong scale
 * and offset -- not silence like the int16 bug above, but a systematically
 * wrong domain, which depresses the wake score without eliminating
 * detection: exactly the "recognizes the phrase, badly under-confident"
 * signature this constant was added to fix. Applied ONLY to
 * melspectrogram.onnx's output, once, right before it's windowed for
 * embedding_model.onnx -- never to raw audio or to embeddings themselves,
 * matching upstream's single application point. */
const MELSPEC_TRANSFORM_DIVISOR = 10
const MELSPEC_TRANSFORM_OFFSET = 2

/** Runs the melspectrogram -> embedding -> wake chain over the trailing
 * SAMPLES_NEEDED samples of `buffer`. Returns the wake score, or `null` if
 * there isn't enough audio buffered yet to run a full pass (only happens
 * in the first ~2 seconds after start()). */
async function runDetectionPass(
  sessions: OpenWakeWordSessions,
  buffer: number[],
): Promise<number | null> {
  if (buffer.length < SAMPLES_NEEDED) return null
  const samples = Float32Array.from(
    buffer.slice(buffer.length - SAMPLES_NEEDED),
    (s) => Math.max(-1, Math.min(1, s)) * WEB_AUDIO_TO_INT16_SCALE,
  )

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
        // See MELSPEC_TRANSFORM_DIVISOR/_OFFSET above -- upstream's
        // `spec/10 + 2`, applied here to match _get_melspectrogram doing it
        // before anything downstream (windowing included) sees the value.
        windows[(w * MEL_WINDOW_FRAMES + f) * MEL_BINS + b] =
          melData[srcFrame * bins + b] / MELSPEC_TRANSFORM_DIVISOR + MELSPEC_TRANSFORM_OFFSET
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
 * in through `deps`. See createOpenWakeWordDetector() for the real thing.
 *
 * `diagnosticsEnabled` (default off, backward compatible with every
 * existing call site including this file's own tests) turns on the
 * opt-in observability described in this file's diagnostics section
 * above: a throttled console log of the live score/running-max, and
 * publishing the same numbers to subscribeWakeDiagnostics() for an
 * on-screen readout. It never changes anything the detection logic below
 * reads or writes. */
export function createOpenWakeWordDetectorWithDeps(
  deps: OpenWakeWordDeps,
  diagnosticsEnabled = false,
): WakeDetector {
  let capture: AudioCapture | null = null
  let buffer: number[] = []
  let newSinceLastRun = 0
  let passInFlight = false
  let lastFireAt = 0
  let stopped = true
  // Diagnostics-only state below -- read and written only inside the
  // `if (diagnosticsEnabled ...)` blocks further down, never by the
  // detection logic above them.
  let diagMaxScore: number | null = null
  let diagLastLogAt = 0

  return {
    async start(onWake: () => void, onFailure?: (message: string) => void): Promise<void> {
      // Models before microphone: if the pipeline can't run at all, no
      // point prompting for mic permission first.
      const sessions = await deps.loadSessions()

      buffer = []
      newSinceLastRun = 0
      lastFireAt = 0
      stopped = false
      // Running max is per listening session, not since the page loaded:
      // reset it (and the on-screen readout) every time start() runs.
      diagMaxScore = null
      diagLastLogAt = 0
      if (diagnosticsEnabled) publishWakeDiagnostics(INITIAL_WAKE_DIAGNOSTICS)

      // Reports a mid-session capture loss the same way stop() releases
      // resources -- see AudioCapture's onCaptureLost / wakeDetector.ts's
      // onFailure for why this exists and what it must guarantee. Guarded
      // by `stopped` so this can never fire twice, and never fires after a
      // deliberate stop() has already torn things down (stop() sets
      // `stopped = true` too, so a capture-lost signal that arrives after
      // -- or racing with -- a real stop() is a no-op here, same as this
      // file's onaudioprocess callback already guards itself above).
      const reportCaptureLost = async (): Promise<void> => {
        if (stopped) return
        stopped = true
        const c = capture
        capture = null
        buffer = []
        const message =
          'Passive listening lost microphone input -- permission may have been revoked or the device disconnected.'
        if (diagnosticsEnabled) {
          publishWakeDiagnostics({ score: wakeDiagnosticsSnapshot.score, max: diagMaxScore, error: message })
        }
        await c?.close()
        onFailure?.(message)
      }

      capture = await deps.startCapture(
        (samples) => {
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
              // Diagnostics only -- reads `score`/`stopped` but never writes
              // anything the detection logic below (unchanged, same two
              // lines as before this diagnostics feature existed) reads.
              if (diagnosticsEnabled && !stopped && score !== null) {
                if (!Number.isNaN(score)) {
                  diagMaxScore = diagMaxScore === null ? score : Math.max(diagMaxScore, score)
                }
                publishWakeDiagnostics({ score, max: diagMaxScore, error: null })
                const now = Date.now()
                if (now - diagLastLogAt >= DIAG_LOG_INTERVAL_MS) {
                  diagLastLogAt = now
                  console.log(
                    `[wake] score=${formatWakeDiagnosticValue(score)} max=${formatWakeDiagnosticValue(diagMaxScore)}`,
                  )
                }
              }

              if (score === null || stopped) return
              const now = Date.now()
              if (score >= WAKE_THRESHOLD && now - lastFireAt >= COOLDOWN_MS) {
                lastFireAt = now
                onWake()
              }
            })
            .catch((err: unknown) => {
              // Unconditional, regardless of diagnosticsEnabled -- an
              // inference-pass error must never be silently swallowed. This
              // line predates the diagnostics feature; it's why failure
              // mode (c) ("inference throws and the error is swallowed")
              // was never actually possible in this file, even before this
              // change -- see the final report for the audit that confirmed
              // that.
              console.error('[wake] openWakeWord detection pass failed', err)
              if (diagnosticsEnabled && !stopped) {
                publishWakeDiagnostics({
                  score: wakeDiagnosticsSnapshot.score,
                  max: diagMaxScore,
                  error: err instanceof Error ? err.message : String(err),
                })
              }
            })
            .finally(() => {
              passInFlight = false
            })
        },
        () => {
          // The fatal signal itself (see startRealCapture's `track.onended`
          // wiring, and this function's own `stopped` guard above for why
          // this can never double-fire or fire after a deliberate stop()).
          void reportCaptureLost()
        },
      )
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

async function startRealCapture(
  onSamples: (samples: Float32Array) => void,
  onCaptureLost: () => void,
): Promise<AudioCapture> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  })
  const audioContext = new AudioContext({ sampleRate: SAMPLE_RATE })
  // Diagnostics only: reports what the browser actually did with the
  // requested sampleRate above. Some browsers silently keep the hardware's
  // native rate (commonly 44100/48000) instead of resampling to what was
  // requested -- see this file's final report for why that specific
  // mismatch was investigated (but deliberately not fixed) as part of this
  // diagnostics change, and how to read this line if it ever fires.
  if (isWakeDiagnosticsEnabled(window.location.search)) {
    const actual = audioContext.sampleRate
    console.log(
      `[wake] AudioContext sampleRate requested=${SAMPLE_RATE} actual=${actual}` +
        (actual !== SAMPLE_RATE ? ' -- MISMATCH: audio is not actually at 16 kHz, see final report' : ''),
    )
  }
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

  // Fatal-vs-transient call for this file's one raw MediaStreamTrack (mono
  // capture above -- see the getUserMedia call, `channelCount: 1`):
  //
  //   - `ended` (and readyState flipping to 'ended' at the same moment) is
  //     the browser's own signal that this track will NEVER supply audio
  //     again -- fired when the user revokes microphone permission, the
  //     input device is unplugged/removed, or the browser otherwise stops
  //     the track outright. Per the MediaStreamTrack spec this transition
  //     is one-way: a track that has ended cannot un-end. That
  //     irreversibility is exactly the "we are no longer receiving audio,
  //     and never will be again on this track" signal this bug fix needs,
  //     so it -- and only it -- is treated as fatal here.
  //   - `mute`/`unmute` are deliberately NOT listened to. Per the same
  //     spec, `mute` fires whenever the track is merely, temporarily unable
  //     to supply data (e.g. a transient OS/driver hiccup, a Bluetooth
  //     dropout) and is routinely followed by `unmute` moments later on
  //     that very same still-live track -- readyState stays 'live' the
  //     whole time. Treating a mute blip as fatal would flash a scary
  //     "microphone lost" error, reset passive mode, and force the user to
  //     re-enable it for a hiccup that would have recovered on its own.
  //
  // One listener for however many tracks the stream has (one, given
  // `channelCount: 1` above) -- iterating rather than assuming exactly one
  // so this still behaves if that ever changes.
  const tracks = stream.getTracks()
  const handleTrackEnded = () => onCaptureLost()
  for (const track of tracks) track.addEventListener('ended', handleTrackEnded)

  return {
    async close() {
      // Unregister before track.stop() below. Per spec, a script-initiated
      // stop() does NOT itself fire `ended` (only an externally-caused end
      // does) -- so this isn't strictly required to stop today's browsers
      // from looping a deliberate close() back around into a false
      // capture-loss report. It's kept anyway as a deliberate belt-and-
      // braces guard for the "stop() must never report an error" behavior
      // this bug fix is required to preserve: it costs nothing, and it
      // means that guarantee doesn't quietly depend on every current and
      // future browser continuing to honor a spec nuance this file cannot
      // verify at runtime.
      for (const track of tracks) track.removeEventListener('ended', handleTrackEnded)
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
 * implementation. createWakeDetector.ts uses this as the default.
 * Diagnostics are read from the page URL exactly once here, at the moment
 * a real detector is constructed -- see isWakeDiagnosticsEnabled's header
 * for why that's a plain query-string check rather than a build flag. */
export function createOpenWakeWordDetector(): WakeDetector {
  const diagnosticsEnabled =
    typeof window !== 'undefined' ? isWakeDiagnosticsEnabled(window.location.search) : false
  return createOpenWakeWordDetectorWithDeps(
    {
      loadSessions: loadRealSessions,
      startCapture: startRealCapture,
    },
    diagnosticsEnabled,
  )
}
