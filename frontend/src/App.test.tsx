import { describe, expect, it } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "./App";
import { createFakeSession, fakeFetchToken } from "./test/fakes";
import type { AuthState } from "./auth/useClerkAuth";

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

    const roomInput = screen.getByRole("textbox", { name: "Room name (optional)" });
    fireEvent.change(roomInput, { target: { value: "control-room" } });

    fireEvent.click(screen.getByRole("button", { name: "Join room" }));

    // Transient state, asserted immediately (not via waitFor, which could
    // observe it after it has already resolved further).
    expect(screen.getByRole("button", { name: "Connecting…" })).toBeDisabled();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Leave room" })).toBeInTheDocument();
    });

    expect(tokenFn.calls).toEqual([{ identity: "web-e2e", room: "control-room" }]);
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

  it("still shows the free-form room field when signed out (anonymous/dev mode unaffected)", () => {
    const session = createFakeSession();
    const tokenFn = fakeFetchToken({ token: "tok", url: "wss://jarvis.test/rtc" });

    render(
      <App session={{ createSession: () => session, fetchToken: tokenFn, identity: "web-anon" }} />,
    );

    expect(screen.getByRole("textbox", { name: "Room name (optional)" })).toBeInTheDocument();
    expect(screen.queryByTestId("room-note-signed-in")).not.toBeInTheDocument();
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
