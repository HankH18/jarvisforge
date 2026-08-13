import { useState } from 'react'
import { useJarvisSession, type UseJarvisSessionOptions } from './hooks/useJarvisSession'
import { AgentStateIndicator } from './components/AgentStateIndicator'
import { TranscriptView } from './components/TranscriptView'
import { Controls } from './components/Controls'
import './App.css'

export default function App(props?: { session?: UseJarvisSessionOptions }) {
  const session = useJarvisSession(props?.session)
  const [roomName, setRoomName] = useState('')

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
