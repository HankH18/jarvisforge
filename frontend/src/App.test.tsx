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
});
