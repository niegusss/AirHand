import { beforeEach, describe, expect, it } from 'vitest'

import type { CalibrationMessage } from '@/lib/protocol'
import { useCalibrationStore } from './calibrationStore'

/**
 * Calibration store.
 *
 * Same property as the settings store: it holds what the engine reported and nothing else. A
 * countdown started locally would keep ticking through a disconnect, a refusal, or a session the
 * engine cancelled when tracking stopped — and the wizard would sit there waiting for a verdict
 * that was never coming.
 */

function sampling(overrides: Partial<CalibrationMessage> = {}): CalibrationMessage {
  return {
    type: 'calibration',
    step: 'pinch',
    state: 'sampling',
    samples: 42,
    secondsRemaining: 5.5,
    secondsTotal: 8,
    measurement: null,
    suggestion: null,
    reason: null,
    ...overrides,
  }
}

beforeEach(() => {
  useCalibrationStore.getState().reset()
})

describe('applyCalibration', () => {
  it('starts empty, because no measurement has been asked for', () => {
    expect(useCalibrationStore.getState().session).toBeNull()
  })

  it('stores the message as it arrived', () => {
    useCalibrationStore.getState().applyCalibration(sampling())
    expect(useCalibrationStore.getState().session?.secondsRemaining).toBe(5.5)
  })

  it('replaces the previous state rather than merging into it', () => {
    // A verdict carries no countdown. Merging would leave `secondsRemaining` from the last
    // progress message sitting next to a finished measurement.
    useCalibrationStore.getState().applyCalibration(sampling())
    useCalibrationStore.getState().applyCalibration(
      sampling({
        state: 'done',
        secondsRemaining: 0,
        suggestion: { gesture: { pinchClose: 0.46, pinchOpen: 0.66 } },
      }),
    )

    const session = useCalibrationStore.getState().session
    expect(session?.state).toBe('done')
    expect(session?.secondsRemaining).toBe(0)
    expect(session?.suggestion).toEqual({ gesture: { pinchClose: 0.46, pinchOpen: 0.66 } })
  })

  it('keeps the measurement on a refusal, so the user can see what was seen', () => {
    useCalibrationStore.getState().applyCalibration(
      sampling({
        state: 'failed',
        reason: 'Saw 1 clear pinch(es); 2 are needed.',
        measurement: { restingLevel: 0.94, attempts: 1, worstPinch: 0.3, bestPinch: 0.3 },
      }),
    )

    const session = useCalibrationStore.getState().session
    expect(session?.reason).toContain('2 are needed')
    expect(session?.measurement?.attempts).toBe(1)
  })
})

describe('reset', () => {
  it('drops the session, for a disconnect', () => {
    useCalibrationStore.getState().applyCalibration(sampling())
    useCalibrationStore.getState().reset()
    expect(useCalibrationStore.getState().session).toBeNull()
  })
})
