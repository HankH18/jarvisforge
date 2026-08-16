// The ONLY module allowed to import from `livekit-client`. Everything else
// depends on the RoomSession interface below, which is what makes the rest
// of the app testable without a network or a real LiveKit room.

import { Room, RoomEvent, Track, type RemoteTrack, type RemoteTrackPublication } from 'livekit-client'
import { parseJarvisEvent, type JarvisEvent } from './types'

const JARVIS_TOPIC = 'jarvis'

export interface RoomSession {
  connect(url: string, token: string): Promise<void>
  disconnect(): Promise<void>
  setMicrophoneEnabled(enabled: boolean): Promise<void>
  /** Subscribe to decoded Jarvis events. Returns an unsubscribe function. */
  onEvent(cb: (event: JarvisEvent) => void): () => void
  /** Fires when the room drops for any reason. Returns an unsubscribe fn. */
  onDisconnected(cb: () => void): () => void
}

const utf8Decoder = new TextDecoder('utf-8')

/**
 * Play the agent's speech.
 *
 * THIS IS NOT OPTIONAL PLUMBING -- without it the app is silent, and silently
 * so. Raw `livekit-client` does NOT auto-play remote audio; it hands you a
 * subscribed track and expects you to attach it to a media element in the DOM.
 * (`@livekit/components-react`'s `<RoomAudioRenderer />` is what normally does
 * this; this app deliberately depends on `livekit-client` alone, so it has to
 * do the attaching itself.)
 *
 * Until 2026-08-15 this file handled `DataReceived` and `Disconnected` only.
 * Everything else worked -- the worker joined, the LLM replied, Cartesia
 * synthesized, the track arrived -- and the browser played none of it. The bug
 * survived because every latency and smoke measurement to date was taken
 * worker-side (`/app/var/latency.jsonl`) or through the Python driver in
 * `scripts/drive_turns.py`, both of which bypass this module entirely. The
 * transcript still rendered, because transcripts travel over the data channel,
 * so the UI looked alive while being mute. Hank found it by using the app.
 *
 * Returns a teardown function that detaches every element it created.
 */
function attachRemoteAudio(room: Room): () => void {
  const elements = new Map<string, HTMLAudioElement>()

  const onSubscribed = (track: RemoteTrack, publication: RemoteTrackPublication) => {
    if (track.kind !== Track.Kind.Audio) return
    const el = track.attach() as HTMLAudioElement
    // Off-screen but IN the document: a detached element is not guaranteed to
    // play, and some browsers suspend audio for elements outside the DOM.
    el.style.display = 'none'
    el.setAttribute('data-testid', 'agent-audio')
    document.body.appendChild(el)
    elements.set(publication.trackSid, el)
  }

  const onUnsubscribed = (track: RemoteTrack, publication: RemoteTrackPublication) => {
    if (track.kind !== Track.Kind.Audio) return
    track.detach().forEach((el) => el.remove())
    elements.delete(publication.trackSid)
  }

  room.on(RoomEvent.TrackSubscribed, onSubscribed)
  room.on(RoomEvent.TrackUnsubscribed, onUnsubscribed)

  return () => {
    room.off(RoomEvent.TrackSubscribed, onSubscribed)
    room.off(RoomEvent.TrackUnsubscribed, onUnsubscribed)
    elements.forEach((el) => el.remove())
    elements.clear()
  }
}

/** Production RoomSession backed by livekit-client's Room. */
export function createLiveKitSession(): RoomSession {
  const room = new Room()
  let detachAudio: (() => void) | null = null

  return {
    async connect(url: string, token: string): Promise<void> {
      // Attach BEFORE connecting. TrackSubscribed can fire during connect for
      // a participant already publishing -- an agent that beat us into the
      // room -- and a listener registered afterwards would miss that track and
      // stay silent for the whole session.
      detachAudio = attachRemoteAudio(room)
      await room.connect(url, token)
      await room.localParticipant.setMicrophoneEnabled(true)
      // Browser autoplay policy blocks audio until a user gesture. `connect`
      // runs from the Join click, so the gesture is in scope here and this
      // resolves; it is a no-op when audio is already permitted. Failure to
      // start audio must not fail the join -- the session is still usable, so
      // this is reported rather than thrown.
      try {
        await room.startAudio()
      } catch (err) {
        console.warn('[room] could not start audio playback automatically', err)
      }
    },

    async disconnect(): Promise<void> {
      // Tear the audio elements down first. Disconnecting leaves them orphaned
      // in document.body otherwise, and they accumulate across reconnects.
      detachAudio?.()
      detachAudio = null
      await room.disconnect()
    },

    async setMicrophoneEnabled(enabled: boolean): Promise<void> {
      await room.localParticipant.setMicrophoneEnabled(enabled)
    },

    onEvent(cb: (event: JarvisEvent) => void): () => void {
      const handler = (payload: Uint8Array, _participant?: unknown, _kind?: unknown, topic?: string) => {
        if (topic !== JARVIS_TOPIC) return
        let parsed: unknown
        try {
          parsed = JSON.parse(utf8Decoder.decode(payload))
        } catch {
          return
        }
        const event = parseJarvisEvent(parsed)
        if (event) cb(event)
      }
      room.on(RoomEvent.DataReceived, handler)
      return () => room.off(RoomEvent.DataReceived, handler)
    },

    onDisconnected(cb: () => void): () => void {
      const handler = () => cb()
      room.on(RoomEvent.Disconnected, handler)
      return () => room.off(RoomEvent.Disconnected, handler)
    },
  }
}
