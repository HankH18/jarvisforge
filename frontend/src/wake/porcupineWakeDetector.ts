// Real wake-word detector backed by the Porcupine Web SDK's built-in
// "Jarvis" keyword (R11, docs/SPEC.md). Implements WakeDetector.
//
// THIS FILE IS NOT WIRED INTO THE BUILD BY DEFAULT. The Porcupine packages
// are not in frontend/package.json -- package.json is out of this ticket's
// scope, and no Picovoice AccessKey exists in this checkout (no .env /
// .env.example entry either, also out of scope). So nothing here can
// import those packages at module load time: the import below is a
// dynamic import(), reached only when createPorcupineWakeDetector() is
// actually called, which only happens from wake/createWakeDetector.ts's
// production factory when passive mode is switched on. Every test in this
// repo drives WakeDetector through wake/testDoubles.ts's FakeWakeDetector
// instead, so this module is never executed by `npm run test`.
//
// A human must, before this can run against a real microphone:
//
//   1. Add to frontend/package.json "dependencies":
//        "@picovoice/porcupine-web": "^3.0.3",
//        "@picovoice/web-voice-processor": "^4.0.9"
//      then run `npm install` inside frontend/.
//
//   2. Get an AccessKey from the Picovoice Console
//      (https://console.picovoice.ai/) and set it as an env var named
//        VITE_PORCUPINE_ACCESS_KEY
//      (Vite only exposes VITE_-prefixed vars to client code) in
//      frontend/.env. Add a `VITE_PORCUPINE_ACCESS_KEY=changeme` line to
//      .env.example too -- both files are out of this ticket's scope, so
//      neither was touched here.
//
// Until both are done, wake/createWakeDetector.ts throws a clear error
// instead of silently pretending to listen.

import type { WakeDetector } from './wakeDetector'

/** Minimal shape of the Porcupine Web SDK surface this file needs. Typed
 * by hand (not imported from the package) since the package isn't
 * installed in this checkout; a human adding the real dependency should
 * delete this in favor of the package's own types. */
interface PorcupineWorkerLike {
  release(): Promise<void>
}

/** `options.processErrorCallback`, per the real @picovoice/porcupine-web
 * API this hand-rolled type stands in for, is how the Porcupine Web SDK
 * reports an engine-level failure after PorcupineWorker.create() has
 * already resolved -- e.g. its underlying WebVoiceProcessor mic stream
 * ending. That's this SDK's equivalent of openWakeWordDetector.ts's
 * MediaStreamTrack `ended` handling (see that file for the fuller
 * reasoning): a failure that must reach WakeDetector's `onFailure`
 * (wakeDetector.ts) rather than leave this detector silently dead. */
interface PorcupineWebModuleLike {
  PorcupineWorker: {
    create(
      accessKey: string,
      keyword: { builtin: string } | string,
      keywordCallback: () => void,
      modelParams?: unknown,
      options?: { processErrorCallback?: (error: Error) => void },
    ): Promise<PorcupineWorkerLike>
  }
}

interface WebVoiceProcessorModuleLike {
  WebVoiceProcessor: {
    subscribe(engine: PorcupineWorkerLike): Promise<void>
    unsubscribe(engine: PorcupineWorkerLike): Promise<void>
  }
}

// Bare specifiers, held as data rather than written as literal `import`
// statements, so bundlers/type-checkers never try to resolve them from a
// module that only exists once a human adds the dependency (see header).
const PORCUPINE_WEB_SPECIFIER = '@picovoice/porcupine-web'
const WEB_VOICE_PROCESSOR_SPECIFIER = '@picovoice/web-voice-processor'

export async function createPorcupineWakeDetector(accessKey: string): Promise<WakeDetector> {
  if (!accessKey) {
    throw new Error(
      '[wake] Porcupine requires an AccessKey. Set VITE_PORCUPINE_ACCESS_KEY -- see wake/porcupineWakeDetector.ts for setup.',
    )
  }

  const { PorcupineWorker } = (await import(
    /* @vite-ignore */ PORCUPINE_WEB_SPECIFIER
  )) as unknown as PorcupineWebModuleLike
  const { WebVoiceProcessor } = (await import(
    /* @vite-ignore */ WEB_VOICE_PROCESSOR_SPECIFIER
  )) as unknown as WebVoiceProcessorModuleLike

  let worker: PorcupineWorkerLike | null = null

  // Shared by stop() and the mid-session failure path below so both release
  // exactly the same way and neither can double-release: whichever runs
  // first nulls `worker` out, so the other (if it also runs) sees `worker`
  // already null and does nothing.
  const release = async (): Promise<void> => {
    if (!worker) return
    const active = worker
    worker = null
    await WebVoiceProcessor.unsubscribe(active)
    await active.release()
  }

  return {
    async start(onWake: () => void, onFailure?: (message: string) => void): Promise<void> {
      worker = await PorcupineWorker.create(accessKey, { builtin: 'Jarvis' }, onWake, undefined, {
        // See PorcupineWebModuleLike's comment above: this is how a
        // failure AFTER a successful start (e.g. the SDK's own mic stream
        // ending) reaches WakeDetector's onFailure, mirroring
        // openWakeWordDetector.ts's track-`ended` handling. Release first,
        // exactly like stop() does, then report -- never leave the engine
        // half torn-down while claiming to have failed.
        processErrorCallback: (error) => {
          void release().then(() => onFailure?.(error.message))
        },
      })
      await WebVoiceProcessor.subscribe(worker)
    },

    async stop(): Promise<void> {
      await release()
    },
  }
}
