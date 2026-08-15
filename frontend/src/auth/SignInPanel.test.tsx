import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { SignInPanel } from "./SignInPanel";
import type { AuthState } from "./useClerkAuth";

/** A signed-in AuthState double. `signOut` is tracked separately so a test
 * can assert the panel never calls it directly -- teardown coordination is
 * the caller's job (App.tsx), not this component's; see SignInPanel's
 * module docstring. */
function fakeSignedInAuth(signOutCalls: number[]): AuthState {
  return {
    status: "signed-in",
    userLabel: "person@example.com",
    getToken: async () => "fake-clerk-session-token",
    signOut: async () => {
      signOutCalls.push(1);
    },
    mountSignIn: () => {},
    unmountSignIn: () => {},
  };
}

describe("SignInPanel", () => {
  it("clicking Sign out invokes the injected onSignOut callback, not auth.signOut() directly", () => {
    const authSignOutCalls: number[] = [];
    const onSignOut = vi.fn();

    render(<SignInPanel auth={fakeSignedInAuth(authSignOutCalls)} onSignOut={onSignOut} />);

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    expect(onSignOut).toHaveBeenCalledTimes(1);
    // The panel itself never reaches into auth.signOut() -- coordinating
    // session teardown with sign-out is the caller's responsibility (this
    // component doesn't know about the LiveKit session at all).
    expect(authSignOutCalls).toHaveLength(0);
  });

  it("renders nothing sign-out-related when signed out", () => {
    const onSignOut = vi.fn();
    const auth: AuthState = {
      status: "unavailable",
      userLabel: null,
      getToken: async () => null,
      signOut: async () => {},
      mountSignIn: () => {},
      unmountSignIn: () => {},
    };

    render(<SignInPanel auth={auth} onSignOut={onSignOut} />);

    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });
});
