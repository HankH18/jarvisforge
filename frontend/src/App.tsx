import { useState } from 'react'
import { useJarvisSession, type UseJarvisSessionOptions } from './hooks/useJarvisSession'
import { AgentStateIndicator } from './components/AgentStateIndicator'
import { TranscriptView } from './components/TranscriptView'
import { Controls } from './components/Controls'
import { usePassiveWakeSession } from './state/usePassiveWakeSession'
import { createWakeDetector } from './wake/createWakeDetector'
import type { WakeDetector } from './wake/wakeDetector'
import './App.css'

export interface AppWakeOptions {
  /** default: the real Porcupine-backed factory, wake/createWakeDetector */
  createDetector?: () => Promise<WakeDetector>
}

export default function App(props?: { session?: UseJarvisSessionOptions; wake?: AppWakeOptions }) {
  const session = useJarvisSession(props?.session)
  const [roomName, setRoomName] = useState('')
  // R11 / T-12 acceptance 3: explicit opt-in, default OFF. Passive
  // listening -- and the local (non-LiveKit) mic capture it implies --
  // never starts unless the user turns it on themselves.
  const [passiveModeEnabled, setPassiveModeEnabled] = useState(false)

  const createDetector = props?.wake?.createDetector ?? createWakeDetector()
  const passiveState = usePassiveWakeSession({
    enabled: passiveModeEnabled,
    createDetector,
    endpoint: session,
    status: session.status,
    room: roomName.trim() === '' ? null : roomName.trim(),
  })

  const isConnected = session.status === 'connected'
  const isConnecting = session.status === 'connecting'

  const handleJoin = () => {
    const trimmed = roomName.trim()
    void session.join(trimmed === '' ? null : trimmed)
  }

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__title">Jarvis</h1>
        <p className="app__tagline">A calm voice console for your agent.</p>
      </header>

      <main className="app__main">
        <AgentStateIndicator state={session.agentState} status={session.status} />
        <TranscriptView lines={session.transcript} />
      </main>

      <footer className="app__footer">
        {session.error && (
          <p className="app__error" role="alert">
            {session.error}
          </p>
        )}

        {!isConnected && (
          <div className="app__room">
            <label htmlFor="room-name" className="app__room-label">
              Room name (optional)
            </label>
            <input
              id="room-name"
              type="text"
              className="app__room-input"
              placeholder="Leave blank to start a new room"
              value={roomName}
              onChange={(event) => setRoomName(event.target.value)}
              disabled={isConnecting}
              autoComplete="off"
            />
          </div>
        )}

        <label className="app__passive-toggle">
          <input
            type="checkbox"
            checked={passiveModeEnabled}
            onChange={(event) => setPassiveModeEnabled(event.target.checked)}
          />
          Passive listening (say "Jarvis")
        </label>
        {passiveModeEnabled && (
          <p className="app__passive-state" data-testid="passive-state">
            {passiveState === 'passive' ? 'Listening for "Jarvis"…' : null}
          </p>
        )}

        <Controls
          status={session.status}
          micEnabled={session.micEnabled}
          onJoin={handleJoin}
          onLeave={() => void session.leave()}
          onToggleMic={() => void session.toggleMic()}
        />
      </footer>
    </div>
  )
}
