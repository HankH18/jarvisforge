import { useMemo, useState } from 'react'
import { useJarvisSession, type UseJarvisSessionOptions } from './hooks/useJarvisSession'
import { AgentStateIndicator } from './components/AgentStateIndicator'
import { TranscriptView } from './components/TranscriptView'
import { Controls } from './components/Controls'
import { usePassiveWakeSession } from './state/usePassiveWakeSession'
import { createWakeDetector } from './wake/createWakeDetector'
import type { WakeDetector } from './wake/wakeDetector'
import { useClerkAuth, type AuthState } from './auth/useClerkAuth'
import { createAuthFetchToken } from './auth/authFetchToken'
import { SignInPanel } from './auth/SignInPanel'
import './App.css'

export interface AppWakeOptions {
  /** default: the real Porcupine-backed factory, wake/createWakeDetector */
  createDetector?: () => Promise<WakeDetector>
}

export interface AppAuthOptions {
  /** default: the real Clerk-backed hook, auth/useClerkAuth */
  useAuth?: () => AuthState
}

export default function App(props?: {
  session?: UseJarvisSessionOptions
  wake?: AppWakeOptions
  auth?: AppAuthOptions
}) {
  // T-10: Clerk sign-in. `auth.status` drives the header's sign-in widget
  // only -- the actual security boundary is server-side (/api/token 401s
  // an unauthenticated or forged request regardless of what this renders,
  // see backend jarvis.auth.clerk). When no `session.fetchToken` override
  // is supplied (real usage, as opposed to the test double every existing
  // test injects), outgoing token requests are wrapped to carry
  // `Authorization: Bearer <Clerk session token>` once signed in.
  const useAuth = props?.auth?.useAuth ?? useClerkAuth
  const auth = useAuth()
  const authFetchToken = useMemo(() => createAuthFetchToken(auth), [auth])
  const sessionOptions: UseJarvisSessionOptions = {
    ...props?.session,
    fetchToken: props?.session?.fetchToken ?? authFetchToken,
  }
  const session = useJarvisSession(sessionOptions)
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
  // P0 fix follow-up: once signed in, the server ignores any client-
  // supplied room entirely and always mints a token for the caller's own
  // server-derived room (backend jarvis.server.app._room_for_authenticated_
  // identity) -- so a free-text room field would be a false affordance
  // that silently does nothing. Hide it rather than let a signed-in user
  // believe typing a room name controls where they land.
  const isSignedIn = auth.status === 'signed-in'

  const handleJoin = () => {
    const trimmed = roomName.trim()
    void session.join(trimmed === '' ? null : trimmed)
  }

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__title">Jarvis</h1>
        <p className="app__tagline">A calm voice console for your agent.</p>
        <SignInPanel auth={auth} />
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

        {!isConnected && isSignedIn && (
          <p className="app__room" data-testid="room-note-signed-in">
            Signed in — you&apos;ll join your own private room.
          </p>
        )}

        {!isConnected && !isSignedIn && (
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
