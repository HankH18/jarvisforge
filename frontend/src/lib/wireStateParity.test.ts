// T-27: assert the pinned agent-state wire vocabulary cannot drift between
// the backend (WireAgentState / _WIRE_STATES in
// backend/src/jarvis/pipeline/events.py) and the frontend (AgentState /
// AGENT_STATES in ./types.ts). Both sides are read from their own source of
// truth -- AGENT_STATES via normal import, _WIRE_STATES via
// backendWireStates.ts parsing the backend file's text -- so there is no
// third, hand-maintained copy of the list to drift out of step with either.

import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { AGENT_STATES } from "./types";
import { extractWireStates, readBackendWireStates } from "./backendWireStates";

const here = path.dirname(fileURLToPath(import.meta.url));
const BACKEND_EVENTS_PATH = path.resolve(
  here,
  "../../../backend/src/jarvis/pipeline/events.py",
);

describe("wire-state vocabulary parity between frontend and backend (T-27)", () => {
  it("sanity check: the backend events.py file exists at the expected path", () => {
    // Guards against this whole suite silently passing because the path is
    // stale after a repo reorg -- readBackendWireStates would already throw
    // in that case, but this pinpoints the failure mode explicitly.
    expect(fs.existsSync(BACKEND_EVENTS_PATH)).toBe(true);
  });

  it("frontend AGENT_STATES and backend _WIRE_STATES contain exactly the same members", () => {
    const backendStates = readBackendWireStates(BACKEND_EVENTS_PATH);

    expect(new Set(backendStates)).toEqual(new Set(AGENT_STATES));
    // Belt-and-suspenders against a set-equality false positive from
    // duplicate entries on either side.
    expect(backendStates.length).toBe(new Set(backendStates).size);
    expect(AGENT_STATES.length).toBe(new Set(AGENT_STATES).size);
  });

  it("readBackendWireStates throws loudly rather than passing silently on a missing/renamed file", () => {
    const bogusPath = path.resolve(
      here,
      "../../../backend/src/jarvis/pipeline/does-not-exist.py",
    );
    expect(() => readBackendWireStates(bogusPath)).toThrow();
  });

  it("extractWireStates throws loudly when the _WIRE_STATES literal is missing (shape drift)", () => {
    expect(() => extractWireStates("# _WIRE_STATES was renamed or removed\n")).toThrow(
      /no `_WIRE_STATES = frozenset\(\(\.\.\.\)\)` literal found/,
    );
  });

  it("extractWireStates throws loudly on an empty frozenset", () => {
    expect(() => extractWireStates("_WIRE_STATES = frozenset(())")).toThrow(/is empty/);
  });

  it("extractWireStates throws loudly when an entry isn't a plain quoted identifier", () => {
    expect(() =>
      extractWireStates('_WIRE_STATES = frozenset(("listening", some_dynamic_expr()))'),
    ).toThrow(/not a simple quoted/);
  });
});
