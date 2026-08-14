// Acceptance 1 (T-12): "State machine unit tests: passive -> wake event ->
// active -> session end -> passive."

import { describe, expect, it } from 'vitest'
import { createPassiveActiveMachine } from './passiveActiveMachine'

describe('createPassiveActiveMachine', () => {
  it('starts passive by default', () => {
    const machine = createPassiveActiveMachine()
    expect(machine.getState()).toBe('passive')
  })

  it('passive -> WAKE -> active -> SESSION_END -> passive', () => {
    const machine = createPassiveActiveMachine()
    const seen: string[] = []
    machine.subscribe((state) => seen.push(state))

    expect(machine.getState()).toBe('passive')

    machine.send({ type: 'WAKE' })
    expect(machine.getState()).toBe('active')

    machine.send({ type: 'SESSION_END' })
    expect(machine.getState()).toBe('passive')

    expect(seen).toEqual(['active', 'passive'])
  })

  it('can cycle passive -> active -> passive repeatedly', () => {
    const machine = createPassiveActiveMachine()
    for (let i = 0; i < 3; i += 1) {
      machine.send({ type: 'WAKE' })
      expect(machine.getState()).toBe('active')
      machine.send({ type: 'SESSION_END' })
      expect(machine.getState()).toBe('passive')
    }
  })

  it('WAKE while already active is a no-op', () => {
    const machine = createPassiveActiveMachine('active')
    let notified = false
    machine.subscribe(() => {
      notified = true
    })

    machine.send({ type: 'WAKE' })

    expect(machine.getState()).toBe('active')
    expect(notified).toBe(false)
  })

  it('SESSION_END while passive is a no-op', () => {
    const machine = createPassiveActiveMachine('passive')
    let notified = false
    machine.subscribe(() => {
      notified = true
    })

    machine.send({ type: 'SESSION_END' })

    expect(machine.getState()).toBe('passive')
    expect(notified).toBe(false)
  })

  it('unsubscribe stops further notifications', () => {
    const machine = createPassiveActiveMachine()
    const seen: string[] = []
    const unsubscribe = machine.subscribe((state) => seen.push(state))

    unsubscribe()
    machine.send({ type: 'WAKE' })

    expect(seen).toEqual([])
    expect(machine.getState()).toBe('active') // the machine itself still transitioned
  })

  it('supports multiple independent subscribers', () => {
    const machine = createPassiveActiveMachine()
    const a: string[] = []
    const b: string[] = []
    machine.subscribe((state) => a.push(state))
    machine.subscribe((state) => b.push(state))

    machine.send({ type: 'WAKE' })

    expect(a).toEqual(['active'])
    expect(b).toEqual(['active'])
  })
})
