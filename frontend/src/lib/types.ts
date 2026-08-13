// Pinned wire contracts (docs/DESIGN.md §Interfaces) -- unchangeable.
//
//   {"type":"agent_state","state":"listening|thinking|working|speaking","ts":0}
//   {"type":"transcript","role":"user|assistant","text":"...","final":true}

export type AgentState = 'listening' | 'thinking' | 'working' | 'speaking'
export type TranscriptRole = 'user' | 'assistant'

export interface AgentStateEvent {
  type: 'agent_state'
  state: AgentState
  ts: number
}

export interface TranscriptEvent {
  type: 'transcript'
  role: TranscriptRole
  text: string
  final: boolean
}

export type JarvisEvent = AgentStateEvent | TranscriptEvent

export const AGENT_STATES: readonly AgentState[] = [
  'listening',
  'thinking',
  'working',
  'speaking',
]

const AGENT_STATE_SET: ReadonlySet<string> = new Set(AGENT_STATES)
const TRANSCRIPT_ROLE_SET: ReadonlySet<string> = new Set<TranscriptRole>(['user', 'assistant'])

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseAgentStateEvent(raw: Record<string, unknown>): AgentStateEvent | null {
  const { state, ts } = raw
  if (typeof state !== 'string' || !AGENT_STATE_SET.has(state)) return null
  if (ts !== undefined && typeof ts !== 'number') return null
  return { type: 'agent_state', state: state as AgentState, ts: typeof ts === 'number' ? ts : 0 }
}

function parseTranscriptEvent(raw: Record<string, unknown>): TranscriptEvent | null {
  const { role, text, final } = raw
  if (typeof role !== 'string' || !TRANSCRIPT_ROLE_SET.has(role)) return null
  if (typeof text !== 'string') return null
  if (typeof final !== 'boolean') return null
  return { type: 'transcript', role: role as TranscriptRole, text, final }
}

/** Parse one data-channel payload. Returns null for anything that is not a
 *  well-formed Jarvis event — unknown type, bad state, missing/mistyped
 *  field. Never throws. */
export function parseJarvisEvent(raw: unknown): JarvisEvent | null {
  if (!isPlainObject(raw)) return null
  switch (raw.type) {
    case 'agent_state':
      return parseAgentStateEvent(raw)
    case 'transcript':
      return parseTranscriptEvent(raw)
    default:
      return null
  }
}
