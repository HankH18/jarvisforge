// Production WakeDetector factory. Default engine is openWakeWordDetector.ts
// (the "hey jarvis" ONNX chain via onnxruntime-web) -- see that file's
// header for the model provenance/license and how it runs fully on-device.
// This replaces the previous Porcupine-backed default: Porcupine is
// blocked behind commercial licensing that could not be obtained before
// submission, and porcupineWakeDetector.ts was never actually wired to a
// real AccessKey or installed dependency in this checkout (see that
// file's header). The openWakeWord engine needs neither: no API key, no
// signup, no dependency a human has to add by hand -- the models and the
// onnxruntime-web WASM runtime are vendored into frontend/public/.
//
// App.tsx uses this as its default; tests inject wake/testDoubles.ts's
// FakeWakeDetector instead via App's `wake` prop.

import type { WakeDetector } from './wakeDetector'
import { createOpenWakeWordDetector } from './openWakeWordDetector'

/** Whether this build/browser can possibly run passive/wake-word
 * listening -- known synchronously, without ever invoking the detector
 * below (and therefore without prompting for microphone permission or
 * fetching any model). App.tsx uses this to disable the passive-mode
 * toggle and explain why up front, instead of letting a user tick a box
 * that can only ever fail.
 *
 * Unlike the Porcupine-era version of this function (which checked for a
 * build-time-inlined VITE_PORCUPINE_ACCESS_KEY -- a fact fixed at build
 * time), openWakeWordDetector.ts needs no key at all, so there is no
 * build-time fact to check. What CAN be checked synchronously, and is
 * fixed for the lifetime of a loaded page, is whether this browser has the
 * two primitives the engine structurally requires: WebAssembly (to run the
 * ONNX models) and mediaDevices.getUserMedia (to capture the microphone).
 * Every other failure mode (model fetch failing, mic permission denied,
 * ...) is a genuine runtime failure and is surfaced honestly through
 * start()'s rejection instead -- see wake/openWakeWordDetector.ts and
 * state/usePassiveWakeSession.ts's onStartError. */
export function isWakeDetectorConfigured(): boolean {
  if (typeof window === 'undefined') return false
  return typeof WebAssembly !== 'undefined' && Boolean(navigator?.mediaDevices?.getUserMedia)
}

export function createWakeDetector(): () => Promise<WakeDetector> {
  return async () => {
    if (!isWakeDetectorConfigured()) {
      throw new Error(
        '[wake] Passive mode needs WebAssembly and microphone access support in this browser. ' +
          'See frontend/src/wake/openWakeWordDetector.ts for details.',
      )
    }
    return createOpenWakeWordDetector()
  }
}
