// The ONLY module allowed to import from `livekit-client`. Everything else
// depends on the RoomSession interface below, which is what makes the rest
// of the app testable without a network or a real LiveKit room.

import { Room, RoomEvent } from 'livekit-client'
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

/** Production RoomSession backed by livekit-client's Room. */
export function createLiveKitSession(): RoomSession {
  const room = new Room()

  return {
    async connect(url: string, token: string): Promise<void> {
      await room.connect(url, token)
      await room.localParticipant.setMicrophoneEnabled(true)
    },

    async disconnect(): Promise<void> {
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
