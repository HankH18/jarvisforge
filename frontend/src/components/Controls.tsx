import type { ConnectionStatus } from '../hooks/useJarvisSession'

export function Controls(props: {
  status: ConnectionStatus
  micEnabled: boolean
  onJoin: () => void
  onLeave: () => void
  onToggleMic: () => void
}) {
  const { status, micEnabled, onJoin, onLeave, onToggleMic } = props
  const isConnected = status === 'connected'
  const isConnecting = status === 'connecting'

  return (
    <div className="controls">
      {!isConnected && (
        <button
          type="button"
          className="controls__join"
          onClick={onJoin}
          disabled={isConnecting}
        >
          {isConnecting ? 'Connecting…' : 'Join room'}
        </button>
      )}
      {isConnected && (
        <>
          <button type="button" className="controls__mic" onClick={onToggleMic}>
            {micEnabled ? 'Mute microphone' : 'Unmute microphone'}
          </button>
          <button type="button" className="controls__leave" onClick={onLeave}>
            Leave room
          </button>
        </>
      )}
    </div>
  )
}
