import { useMemo, useState } from 'react'
import { useJarvisSession, type UseJarvisSessionOptions } from './hooks/useJarvisSession'
import { AgentStateIndicator } from './components/AgentStateIndicator'
import { TranscriptView } from './components/TranscriptView'
import { Controls } from './components/Controls'
import { usePassiveWakeSession } from './state/usePassiveWakeSession'
import { createWakeDetector, isWakeDetectorConfigured } from './wake/createWakeDetector'
import type { WakeDetector } from './wake/wakeDetector'
import { useClerkAuth, type AuthState } from './auth/useClerkAuth'
import { createAuthFetchToken } from './auth/authFetchToken'
import { SignInPanel } from './auth/SignInPanel'
import './App.css'

export interface AppWakeOptions {
  /** default: the real openWakeWord-backed factory, wake/createWakeDetector */
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

  // P0 fix follow-up: once signed in, the server ignores any client-
  // supplied room entirely and always mints a token for the caller's own
  // server-derived room (backend jarvis.server.app._room_for_authenticated_
  // identity) -- so a free-text room field would be a false affordance
  // that silently does nothing. There is no such field for anyone now
  // (see anonymousRoom below -- Hank: "This is a voice assistant. Why are
  // we going and setting custom room names?"), but `isSignedIn` still
  // decides what room this tab sends (null here vs. the generated id
  // below) and whether the footer shows the informational note instead.
  const isSignedIn = auth.status === 'signed-in'

  // The room an anonymous (signed-out) caller sends -- the wire contract
  // (lib/token.ts) still takes one, and the server still needs something
  // to create a room around, but a human should never have to invent a
  // name for it. Generated once per mount so a manual Join click and a
  // later wake-triggered join (the `room` passed to usePassiveWakeSession
  // below) land in the same room. Harmless to compute even when signed
  // in: the authenticated path below never sends it.
  const [anonymousRoom] = useState(() => crypto.randomUUID())

  // R11 / T-12 acceptance 3: explicit opt-in, default OFF. Passive
  // listening -- and the local (non-LiveKit) mic capture it implies --
  // never starts unless the user turns it on themselves.
  const [passiveModeEnabled, setPassiveModeEnabled] = useState(false)
  // Set only when a start() attempt actually fails (see onStartError
  // below). Cleared on every subsequent toggle interaction so a stale
  // failure message never outlives the state that caused it.
  const [passiveError, setPassiveError] = useState<string | null>(null)

  // isWakeDetectorConfigured() (wake/createWakeDetector.ts) is a
  // synchronous capability check -- WebAssembly + getUserMedia support --
  // fixed for the lifetime of this loaded page, so whether passive mode
  // can possibly work is knowable up front, no need to let a user tick the
  // box and find out it fails. A test-injected detector
  // (wake.createDetector) is always treated as available; it's standing
  // in for a working one.
  const wakeAvailable = props?.wake?.createDetector !== undefined || isWakeDetectorConfigured()

  const createDetector = props?.wake?.createDetector ?? createWakeDetector()
  const passiveState = usePassiveWakeSession({
    enabled: passiveModeEnabled,
    createDetector,
    endpoint: session,
    status: session.status,
    room: isSignedIn ? null : anonymousRoom,
    onStartError: (message) => {
      setPassiveError(message)
      setPassiveModeEnabled(false)
    },
  })

  const isConnected = session.status === 'connected'

  const handleJoin = () => {
    // Signed-in: the server derives the room from the verified identity
    // and ignores whatever is sent (see isSignedIn's comment above) --
    // null is honest about that. Signed-out: the room this tab generated.
    void session.join(isSignedIn ? null : anonymousRoom)
  }

  // P1 fix: sign-out must tear down the live LiveKit session -- leave the
  // room, stop publishing the mic -- as part of signing out, not leave it
  // running until the connection happens to drop on its own. This matters
  // more since the P0 fix made rooms private per authenticated user: a
  // session that outlives sign-out on a shared device is a *private* room
  // still open under the identity that just logged out.
  //
  // Ordering: session.leave() first, auth.signOut() second. By the time
  // auth flips to signed-out (the security-irrelevant but user-visible
  // state), the room is already torn down -- there's no window where the
  // header shows "signed out" while the mic is still hot. Reversing the
  // order would open exactly that window during the awaited signOut() call.
  //
  // Robustness: each step is wrapped separately so a failure in either
  // can never block or skip the other. session.leave() already never
  // rejects (useJarvisSession swallows the underlying disconnect()
  // failure), but the try/catch here is defensive against that changing --
  // a stuck/rejecting teardown must not strand the user signed in. Symmetrically,
  // if auth.signOut() itself rejects, the session has already been fully
  // torn down (leave() runs to completion either way, never partially), so
  // there is no half-torn-down state left behind by a failed sign-out --
  // only a user who, per Clerk, never got signed out. This whole function
  // must never reject: it's invoked with `void` below, following the same
  // convention as toggleMic.
  const handleSignOut = async () => {
    try {
      await session.leave()
    } catch (err) {
      console.error('[jarvis] session teardown before sign-out failed', err)
    }
    try {
      await auth.signOut()
    } catch (err) {
      console.error('[jarvis] auth.signOut() failed', err)
    }
  }

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__title">Jarvis</h1>
        <p className="app__tagline">A calm voice console for your agent.</p>
        <SignInPanel auth={auth} onSignOut={() => void handleSignOut()} />
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

        <label className="app__passive-toggle">
          <input
            type="checkbox"
            checked={passiveModeEnabled}
            disabled={!wakeAvailable}
            onChange={(event) => {
              // Clear any error from a previous attempt on every toggle
              // interaction (on or off) -- it belongs to that attempt, not
              // to whatever the user does next.
              setPassiveError(null)
              setPassiveModeEnabled(event.target.checked)
            }}
          />
          Passive listening (say "hey Jarvis")
        </label>
        {!wakeAvailable && (
          <p className="app__passive-note" data-testid="passive-unavailable">
            Passive listening is unavailable in this browser — it needs WebAssembly and
            microphone access support, which weren&apos;t both detected here.
          </p>
        )}
        {passiveError && (
          <p className="app__error" role="alert" data-testid="passive-error">
            {passiveError}
          </p>
        )}
        {passiveModeEnabled && (
          <p className="app__passive-state" data-testid="passive-state">
            {passiveState === 'passive' ? 'Listening for "hey Jarvis"…' : null}
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
