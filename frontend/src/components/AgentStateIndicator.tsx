import type { AgentState } from '../lib/types'
import type { ConnectionStatus } from '../hooks/useJarvisSession'

const STATE_LABELS: Record<AgentState, string> = {
  listening: 'Listening',
  thinking: 'Thinking',
  working: 'Working',
  speaking: 'Speaking',
}

export function AgentStateIndicator(props: { state: AgentState | null; status: ConnectionStatus }) {
  const { state, status } = props
  const label = state ? STATE_LABELS[state] : 'Not connected'
  const dataState = state ?? 'none'

  return (
    <div className={`agent-orb-wrap${status === 'connecting' ? ' is-connecting' : ''}`}>
      <div className="agent-orb" data-state={dataState} aria-hidden="true">
        <span className="agent-orb__ring agent-orb__ring--outer" />
        <span className="agent-orb__ring agent-orb__ring--inner" />
        <span className="agent-orb__core" />
      </div>
      <p
        data-testid="agent-state"
        role="status"
        aria-live="polite"
        data-state={dataState}
        className="agent-state-label"
      >
        {label}
      </p>
    </div>
  )
}
