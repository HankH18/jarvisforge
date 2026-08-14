// T-27: the pinned wire-state vocabulary is declared twice -- here as
// AGENT_STATES (./types.ts) and on the backend as _WIRE_STATES
// (backend/src/jarvis/pipeline/events.py). Nothing enforced that the two
// stayed equal, so a state added to one side would silently drift.
//
// This module does NOT restate the vocabulary a third time. It extracts the
// backend's `_WIRE_STATES = frozenset((...))` literal directly out of the
// Python source text, so the comparison always reads the backend's actual
// source of truth. The parse is deliberately strict: any shape it does not
// recognize (moved/renamed constant, non-literal entries, etc.) throws
// instead of silently returning an empty or partial list -- a test that
// stayed green after a refactor would be worse than no test at all.

import fs from 'node:fs'

const WIRE_STATES_PATTERN = /_WIRE_STATES\s*=\s*frozenset\(\(\s*([\s\S]*?)\s*\)\)/
const QUOTED_IDENTIFIER = /^(['"])([a-zA-Z_][a-zA-Z0-9_]*)\1$/

/** Extract the frozenset's string members from Python source text.
 *
 * Throws if:
 *  - no `_WIRE_STATES = frozenset((...))` literal is found at all, or
 *  - the literal is empty, or
 *  - any entry inside it is not a single quoted identifier (e.g. a variable
 *    reference, a call, a concatenation) -- anything that is not a plain
 *    string literal we can read without evaluating Python.
 */
export function extractWireStates(pythonSource: string): string[] {
  const match = WIRE_STATES_PATTERN.exec(pythonSource)
  if (!match) {
    throw new Error(
      'backendWireStates: no `_WIRE_STATES = frozenset((...))` literal found in the given source. ' +
        'Either the constant was renamed/moved (update WIRE_STATES_PATTERN in backendWireStates.ts ' +
        'to match), or this parser was pointed at the wrong file.',
    )
  }

  const body = match[1].trim()
  if (body === '') {
    throw new Error('backendWireStates: _WIRE_STATES frozenset literal is empty')
  }

  const entries = body
    .split(',')
    .map((entry) => entry.trim())
    .filter((entry) => entry !== '') // tolerate a trailing comma before the close-paren

  return entries.map((entry) => {
    const quoted = QUOTED_IDENTIFIER.exec(entry)
    if (!quoted) {
      throw new Error(
        `backendWireStates: _WIRE_STATES contains an entry that is not a simple quoted ` +
          `identifier, so it cannot be read without evaluating Python: ${JSON.stringify(entry)}`,
      )
    }
    return quoted[2]
  })
}

/** Read backend/src/jarvis/pipeline/events.py off disk and parse its pinned
 *  wire-state vocabulary. Throws (does not return an empty/partial result)
 *  if `filePath` does not exist or the file's shape is unrecognized. */
export function readBackendWireStates(filePath: string): string[] {
  const source = fs.readFileSync(filePath, 'utf-8') // throws ENOENT etc. on a bad path
  return extractWireStates(source)
}
