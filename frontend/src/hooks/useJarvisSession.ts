import { useCallback, useEffect, useRef, useState } from 'react'
import type { AgentState, JarvisEvent, TranscriptEvent, TranscriptRole } from '../lib/types'
import { fetchToken as defaultFetchToken, TokenError } from '../lib/token'
import { createLiveKitSession, type RoomSession } from '../lib/room'

/** User-facing copy for a failed join. The underlying error is logged, not shown. */
function joinErrorMessage(err: unknown): string {
  if (err instanceof TokenError) {
    return err.status === undefined
      ? "Couldn't reach Jarvis — is the server running?"
      : `Couldn't reach Jarvis — the server returned ${err.status}.`
  }
  return "Couldn't join the room. Check your connection and try again."
}

export type ConnectionStatus = 'idle' | 'connecting' | 'connected' | 'error'

export interface TranscriptLine {
  id: string
  role: TranscriptRole
  text: string
  final: boolean
}

export interface JarvisSession {
  status: ConnectionStatus
  agentState: AgentState | null
  transcript: TranscriptLine[]
  micEnabled: boolean
  error: string | null
  join(room?: string | null): Promise<void>
  leave(): Promise<void>
  toggleMic(): Promise<void>
}

export interface UseJarvisSessionOptions {
  /** default: createLiveKitSession */
  createSession?: () => RoomSession
  /** default: the real fetchToken */
  fetchToken?: typeof defaultFetchToken
  /** default: a generated 'web-xxxxxxx' */
  identity?: string
}

function generateIdentity(): string {
  return `web-${Math.random().toString(36).slice(2, 9).padEnd(7, '0')}`
}

let lineCounter = 0
function nextLineId(): string {
  lineCounter += 1
  return `line-${lineCounter}`
}

/** Transcript merge rule (pinned):
 *  - a non-final line REPLACES that role's current trailing non-final line
 *    rather than appending.
 *  - a final line replaces the trailing non-final line of the same role, or
 *    appends when there is none.
 *  - final lines are never mutated afterwards. */
function mergeTranscript(lines: TranscriptLine[], event: TranscriptEvent): TranscriptLine[] {
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i]
    if (line.role !== event.role) continue
    if (line.final) break // most recent line of this role is already final: append instead
    const next = lines.slice()
    next[i] = { ...line, text: event.text, final: event.final }
    return next
  }
  return [...lines, { id: nextLineId(), role: event.role, text: event.text, final: event.final }]
}

export function useJarvisSession(options?: UseJarvisSessionOptions): JarvisSession {
  const createSession = options?.createSession ?? createLiveKitSession
  const fetchTokenImpl = options?.fetchToken ?? defaultFetchToken
  const identityRef = useRef(options?.identity ?? generateIdentity())

  const [status, setStatus] = useState<ConnectionStatus>('idle')
  const [agentState, setAgentState] = useState<AgentState | null>(null)
  const [transcript, setTranscript] = useState<TranscriptLine[]>([])
  const [micEnabled, setMicEnabled] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const sessionRef = useRef<RoomSession | null>(null)
  const unsubscribersRef = useRef<Array<() => void>>([])
  const mountedRef = useRef(true)
  const micEnabledRef = useRef(micEnabled)
  micEnabledRef.current = micEnabled

  const teardownSubscriptions = useCallback(() => {
    for (const unsub of unsubscribersRef.current) unsub()
    unsubscribersRef.current = []
  }, [])

  const handleEvent = useCallback((event: JarvisEvent) => {
    if (!mountedRef.current) return
    if (event.type === 'agent_state') {
      setAgentState(event.state)
      return
    }
    setTranscript((prev) => mergeTranscript(prev, event))
  }, [])

  const handleUnexpectedDisconnect = useCallback(() => {
    teardownSubscriptions()
    sessionRef.current = null
    if (!mountedRef.current) return
    setStatus('idle')
    setAgentState(null)
    setMicEnabled(false)
  }, [teardownSubscriptions])

  const join = useCallback(
    async (room?: string | null) => {
      // Defensive re-entry: drop any prior session before starting a new one.
      if (sessionRef.current) {
        const stale = sessionRef.current
        teardownSubscriptions()
        sessionRef.current = null
        await stale.disconnect().catch(() => {})
      }

      setStatus('connecting')
      setError(null)

      const session = createSession()
      sessionRef.current = session
      unsubscribersRef.current = [
        session.onEvent(handleEvent),
        session.onDisconnected(handleUnexpectedDisconnect),
      ]

      try {
        const { token, url } = await fetchTokenImpl({ identity: identityRef.current, room: room ?? null })
        await session.connect(url, token)
        if (!mountedRef.current) {
          await session.disconnect().catch(() => {})
          return
        }
        setStatus('connected')
        setMicEnabled(true)
      } catch (err) {
        teardownSubscriptions()
        sessionRef.current = null
        if (!mountedRef.current) return
        setStatus('error')
        // Raw exception text ("Token response was not valid JSON") is for the
        // console, not for a user staring at a voice console. Keep the detail
        // debuggable, show them something actionable.
        console.error('[jarvis] join failed', err)
        setError(joinErrorMessage(err))
      }
    },
    [createSession, fetchTokenImpl, handleEvent, handleUnexpectedDisconnect, teardownSubscriptions],
  )

  const leave = useCallback(async () => {
    const session = sessionRef.current
    teardownSubscriptions()
    sessionRef.current = null
    if (session) {
      await session.disconnect().catch(() => {})
    }
    if (!mountedRef.current) return
    setStatus('idle')
    setAgentState(null)
    setMicEnabled(false)
  }, [teardownSubscriptions])

  const toggleMic = useCallback(async () => {
    const session = sessionRef.current
    if (!session) return
    const next = !micEnabledRef.current
    setMicEnabled(next)
    await session.setMicrophoneEnabled(next)
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      teardownSubscriptions()
      const session = sessionRef.current
      sessionRef.current = null
      if (session) {
        void session.disconnect()
      }
    }
  }, [teardownSubscriptions])

  return { status, agentState, transcript, micEnabled, error, join, leave, toggleMic }
}
