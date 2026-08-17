import { afterEach, describe, expect, it } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "./App";
import { createFakeSession, fakeFetchToken } from "./test/fakes";
import type { AuthState } from "./auth/useClerkAuth";

/** Matches a crypto.randomUUID() string -- the shape of the room id the
 * client now generates for an anonymous caller (App.tsx's anonymousRoom),
 * since there is no human-typed room field to assert an exact value from
 * anymore. */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A signed-in AuthState double. The room-name field is only ever a UX
 * affordance -- the actual authorization boundary is server-side
 * (backend jarvis.server.app, P0 fix: authenticated-mode room is derived
 * from the verified identity, `req.room` is never read) -- but a
 * signed-in user should not be shown a free-text room field that quietly
 * does nothing, hence the assertions below.
 */
function fakeSignedInAuth(): AuthState {
  return {
    status: "signed-in",
    userLabel: "person@example.com",
    getToken: async () => "fake-clerk-session-token",
    signOut: async () => {},
    mountSignIn: () => {},
    unmountSignIn: () => {},
  };
}

describe("App", () => {
  it("joins a room, renders live agent state and transcript, then leaves", async () => {
    const session = createFakeSession();
    const tokenFn = fakeFetchToken({ token: "tok-e2e", url: "wss://jarvis.test/rtc" });

    render(
      <App
        session={{
          createSession: () => session,
          fetchToken: tokenFn,
          identity: "web-e2e",
        }}
      />,
    );

    expect(screen.getByRole("button", { name: "Join room" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Leave room" })).not.toBeInTheDocument();

    const agentState = screen.getByTestId("agent-state");
    expect(agentState).toHaveAttribute("data-state", "none");
    expect(agentState).toHaveTextContent("Not connected");
    expect(screen.getByTestId("transcript-empty")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Join room" }));

    // Transient state, asserted immediately (not via waitFor, which could
    // observe it after it has already resolved further).
    expect(screen.getByRole("button", { name: "Connecting…" })).toBeDisabled();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Leave room" })).toBeInTheDocument();
    });

    // No room field exists to type into anymore -- the client generates its
    // own room id for this anonymous session (App.tsx's anonymousRoom).
    expect(tokenFn.calls).toHaveLength(1);
    expect(tokenFn.calls[0].identity).toBe("web-e2e");
    expect(tokenFn.calls[0].room).toMatch(UUID_RE);
    expect(session.connectCalls).toEqual([{ url: "wss://jarvis.test/rtc", token: "tok-e2e" }]);
    expect(screen.getByRole("button", { name: "Mute microphone" })).toBeInTheDocument();

    act(() => {
      session.emit({ type: "agent_state", state: "thinking", ts: 1 });
    });
    expect(screen.getByTestId("agent-state")).toHaveAttribute("data-state", "thinking");
    expect(screen.getByTestId("agent-state")).toHaveTextContent("Thinking");

    act(() => {
      session.emit({ type: "transcript", role: "user", text: "What is the weather?", final: true });
    });
    act(() => {
      session.emit({ type: "transcript", role: "assistant", text: "It is sunny.", final: false });
    });
    act(() => {
      session.emit({ type: "transcript", role: "assistant", text: "It is sunny today.", final: true });
    });

    const lines = screen.getAllByTestId("transcript-line");
    expect(lines).toHaveLength(2);
    expect(lines[0]).toHaveAttribute("data-role", "user");
    expect(lines[0]).toHaveAttribute("data-final", "true");
    expect(lines[0]).toHaveTextContent("What is the weather?");
    expect(lines[1]).toHaveAttribute("data-role", "assistant");
    expect(lines[1]).toHaveAttribute("data-final", "true");
    expect(lines[1]).toHaveTextContent("It is sunny today.");

    fireEvent.click(screen.getByRole("button", { name: "Leave room" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Join room" })).toBeInTheDocument();
    });
    expect(session.disconnectCalls).toBe(1);
    // The transcript survives leaving.
    expect(screen.getAllByTestId("transcript-line")).toHaveLength(2);
  });

  it("renders the error as role=alert when join fails, and Join room reappears", async () => {
    const session = createFakeSession();
    session.connect = async () => {
      throw new Error("connect boom");
    };
    const tokenFn = fakeFetchToken({ token: "tok", url: "wss://jarvis.test/rtc" });

    render(<App session={{ createSession: () => session, fetchToken: tokenFn, identity: "web-err" }} />);

    fireEvent.click(screen.getByRole("button", { name: "Join room" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Join room" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Leave room" })).not.toBeInTheDocument();
  });

  it("hides the free-form room field when signed in and never sends a client-chosen room", async () => {
    const session = createFakeSession();
    const tokenFn = fakeFetchToken({ token: "tok-signed-in", url: "wss://jarvis.test/rtc" });

    render(
      <App
        session={{ createSession: () => session, fetchToken: tokenFn, identity: "web-signed-in" }}
        auth={{ useAuth: fakeSignedInAuth }}
      />,
    );

    // The room text field is gone entirely for a signed-in user -- typing
    // into it would be a false affordance, since the server ignores it.
    expect(
      screen.queryByRole("textbox", { name: "Room name (optional)" }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("room-note-signed-in")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Join room" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Leave room" })).toBeInTheDocument();
    });

    // No room in the request at all -- there was never a field to put one
    // in, so there is nothing for an attacker to steer client-side either.
    expect(tokenFn.calls).toEqual([{ identity: "web-signed-in", room: null }]);
  });

  it("has no free-form room field when signed out either -- the client generates its own room id", async () => {
    const session = createFakeSession();
    const tokenFn = fakeFetchToken({ token: "tok", url: "wss://jarvis.test/rtc" });

    render(
      <App session={{ createSession: () => session, fetchToken: tokenFn, identity: "web-anon" }} />,
    );

    // This is a voice assistant -- nobody should be asked to invent a room
    // name, signed in or not (Hank's report). No note either: there's
    // nothing to explain when there was never a control to begin with.
    expect(
      screen.queryByRole("textbox", { name: "Room name (optional)" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("room-note-signed-in")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Join room" }));
    await waitFor(() => {
      expect(tokenFn.calls).toHaveLength(1);
    });
    expect(tokenFn.calls[0]).toEqual({ identity: "web-anon", room: expect.stringMatching(UUID_RE) });
  });

  it("generates a fresh anonymous room id per mount, not one fixed constant", async () => {
    // Guards against a "fix" that hardcodes a single literal in place of
    // the input -- that would also make the field disappear, but every
    // anonymous tab would collide in the same room.
    const tokenFnA = fakeFetchToken({ token: "tok", url: "wss://jarvis.test/rtc" });
    const { unmount } = render(
      <App
        session={{ createSession: () => createFakeSession(), fetchToken: tokenFnA, identity: "web-anon-a" }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Join room" }));
    await waitFor(() => expect(tokenFnA.calls).toHaveLength(1));
    unmount();

    const tokenFnB = fakeFetchToken({ token: "tok", url: "wss://jarvis.test/rtc" });
    render(
      <App
        session={{ createSession: () => createFakeSession(), fetchToken: tokenFnB, identity: "web-anon-b" }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Join room" }));
    await waitFor(() => expect(tokenFnB.calls).toHaveLength(1));

    expect(tokenFnA.calls[0].room).not.toBe(tokenFnB.calls[0].room);
  });

  it("R3 fix: the authenticated path is unaffected by the real (device-id-backed) identity generator", async () => {
    // Every other signed-in test above injects an explicit `identity`
    // override, which bypasses useJarvisSession's generateIdentity()
    // entirely -- so none of them would notice if generateIdentity()
    // itself (now lib/deviceId.ts's anonymousIdentity(), persisted via
    // localStorage) broke or changed authenticated-mode behaviour. This
    // test gives no override at all: the REAL generator runs, exactly as
    // it does in production for a signed-in user. If a future change made
    // that generator auth-aware in a way that altered what's sent, or
    // made it throw, or somehow fed the generated identity into the room
    // instead of null, this is what would fail.
    window.localStorage.clear();
    const session = createFakeSession();
    const tokenFn = fakeFetchToken({ token: "tok-signed-in-real-identity", url: "wss://jarvis.test/rtc" });

    render(
      <App
        session={{ createSession: () => session, fetchToken: tokenFn }}
        auth={{ useAuth: fakeSignedInAuth }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Join room" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Leave room" })).toBeInTheDocument();
    });

    // Signed in: room is still always null (server derives it from the
    // verified Clerk identity, ignoring the body entirely) regardless of
    // what identity value the client happened to generate.
    expect(tokenFn.calls).toHaveLength(1);
    expect(tokenFn.calls[0].room).toBeNull();
    expect(typeof tokenFn.calls[0].identity).toBe("string");
    expect(tokenFn.calls[0].identity.length).toBeGreaterThan(0);
  });

  describe("sign-out tears down the live session (P1: shared-device fix)", () => {
    /** A signed-in AuthState double whose signOut records into a shared
     * `order` array (so a test can assert it happened *after* session
     * teardown), and can optionally be made to reject to prove a failed
     * sign-out still doesn't strand the app in a half-torn-down state. */
    function fakeSignedInAuthWithTracking(order: string[], opts?: { signOutError?: unknown }) {
      return function useAuth(): AuthState {
        return {
          status: "signed-in",
          userLabel: "person@example.com",
          getToken: async () => "fake-clerk-session-token",
          signOut: async () => {
            order.push("auth.signOut");
            if (opts?.signOutError !== undefined) throw opts.signOutError;
          },
          mountSignIn: () => {},
          unmountSignIn: () => {},
        };
      };
    }

    it("signing out while connected leaves the LiveKit room before completing sign-out", async () => {
      const order: string[] = [];
      const session = createFakeSession();
      const originalDisconnect = session.disconnect;
      session.disconnect = async () => {
        order.push("session.disconnect");
        await originalDisconnect();
      };
      const tokenFn = fakeFetchToken({ token: "tok-signout", url: "wss://jarvis.test/rtc" });

      render(
        <App
          session={{ createSession: () => session, fetchToken: tokenFn, identity: "web-signout" }}
          auth={{ useAuth: fakeSignedInAuthWithTracking(order) }}
        />,
      );

      fireEvent.click(screen.getByRole("button", { name: "Join room" }));
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Leave room" })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

      await waitFor(() => {
        expect(order).toEqual(["session.disconnect", "auth.signOut"]);
      });
      expect(session.disconnectCalls).toBe(1);
    });

    it("signing out while never connected is a harmless no-op: still signs out, never touches the session", async () => {
      const order: string[] = [];
      const session = createFakeSession();
      const originalDisconnect = session.disconnect;
      session.disconnect = async () => {
        order.push("session.disconnect");
        await originalDisconnect();
      };
      const tokenFn = fakeFetchToken({ token: "tok-signout-idle", url: "wss://jarvis.test/rtc" });

      render(
        <App
          session={{ createSession: () => session, fetchToken: tokenFn, identity: "web-signout-idle" }}
          auth={{ useAuth: fakeSignedInAuthWithTracking(order) }}
        />,
      );

      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

      await waitFor(() => {
        expect(order).toEqual(["auth.signOut"]);
      });
      expect(session.disconnectCalls).toBe(0);
    });

    it("a session teardown failure on sign-out still completes the sign-out (does not strand the user signed in)", async () => {
      const order: string[] = [];
      const session = createFakeSession();
      session.disconnect = async () => {
        order.push("session.disconnect");
        throw new Error("livekit disconnect boom");
      };
      const tokenFn = fakeFetchToken({ token: "tok-signout-fail", url: "wss://jarvis.test/rtc" });

      render(
        <App
          session={{ createSession: () => session, fetchToken: tokenFn, identity: "web-signout-fail" }}
          auth={{ useAuth: fakeSignedInAuthWithTracking(order) }}
        />,
      );

      fireEvent.click(screen.getByRole("button", { name: "Join room" }));
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Leave room" })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

      // auth.signOut() must still run even though teardown blew up -- a
      // rejecting/failed teardown must never strand the user signed in.
      await waitFor(() => {
        expect(order).toEqual(["session.disconnect", "auth.signOut"]);
      });
    });

    it("an auth.signOut() failure does not throw / produce an unhandled rejection", async () => {
      const order: string[] = [];
      const session = createFakeSession();
      const originalDisconnect = session.disconnect;
      session.disconnect = async () => {
        order.push("session.disconnect");
        await originalDisconnect();
      };
      const tokenFn = fakeFetchToken({ token: "tok-signout-autherr", url: "wss://jarvis.test/rtc" });

      render(
        <App
          session={{ createSession: () => session, fetchToken: tokenFn, identity: "web-signout-autherr" }}
          auth={{
            useAuth: fakeSignedInAuthWithTracking(order, { signOutError: new Error("clerk signOut boom") }),
          }}
        />,
      );

      fireEvent.click(screen.getByRole("button", { name: "Join room" }));
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Leave room" })).toBeInTheDocument();
      });

      // Clicking must not throw synchronously, and vitest's own unhandled-
      // rejection detection would fail this test if the click handler's
      // promise chain rejected without being caught somewhere.
      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

      await waitFor(() => {
        expect(order).toEqual(["session.disconnect", "auth.signOut"]);
      });
    });
  });
});

// Wake-detector diagnostics (opt-in observability, gated by a URL query
// param rather than a build flag -- see wake/openWakeWordDetector.ts's
// diagnostics section). These only prove App.tsx's side of the gate: that
// the readout appears/disappears with the URL, and reads its initial
// values from wake/openWakeWordDetector.ts's diagnostics store. Live
// score/max/error plumbing through a real detection pass is covered by
// openWakeWordDetector.test.ts's own diagnostics tests -- FakeWakeDetector
// (used everywhere else in this file) never touches that store.
describe("App: wake diagnostics readout (?debug=wake / ?debug=1)", () => {
  afterEach(() => {
    // Every test above renders with a bare "/" URL; reset it after each
    // test here so pushState doesn't leak a ?debug param into any test
    // that happens to run after this block.
    window.history.pushState({}, "", "/");
  });

  it("does not render the readout by default (no ?debug param)", () => {
    const session = createFakeSession();
    const tokenFn = fakeFetchToken({ token: "tok", url: "wss://jarvis.test/rtc" });

    render(
      <App session={{ createSession: () => session, fetchToken: tokenFn, identity: "web-no-debug" }} />,
    );

    expect(screen.queryByTestId("wake-debug")).not.toBeInTheDocument();
  });

  it("renders the readout when ?debug=wake is present in the URL", () => {
    window.history.pushState({}, "", "/?debug=wake");
    const session = createFakeSession();
    const tokenFn = fakeFetchToken({ token: "tok", url: "wss://jarvis.test/rtc" });

    render(
      <App session={{ createSession: () => session, fetchToken: tokenFn, identity: "web-debug-wake" }} />,
    );

    expect(screen.getByTestId("wake-debug")).toBeInTheDocument();
    expect(screen.getByTestId("wake-debug")).toHaveTextContent(/score/i);
  });

  it("also turns on with ?debug=1, but not for an unrelated ?debug value", () => {
    window.history.pushState({}, "", "/?debug=1");
    const session1 = createFakeSession();
    const tokenFn1 = fakeFetchToken({ token: "tok", url: "wss://jarvis.test/rtc" });
    const { unmount } = render(
      <App session={{ createSession: () => session1, fetchToken: tokenFn1, identity: "web-debug-1" }} />,
    );
    expect(screen.getByTestId("wake-debug")).toBeInTheDocument();
    unmount();

    window.history.pushState({}, "", "/?debug=verbose");
    const session2 = createFakeSession();
    const tokenFn2 = fakeFetchToken({ token: "tok", url: "wss://jarvis.test/rtc" });
    render(
      <App session={{ createSession: () => session2, fetchToken: tokenFn2, identity: "web-debug-2" }} />,
    );
    expect(screen.queryByTestId("wake-debug")).not.toBeInTheDocument();
  });
});
