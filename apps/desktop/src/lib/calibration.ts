import type { CalibrationStep, ProfileInfo, SettingsPatch } from '@/lib/protocol'

/**
 * What the wizard is, as data, and the one rule that decides whether it is mandatory.
 *
 * Pure — no React, no store. The gate in particular has to be testable on its own, because getting
 * it wrong in either direction is bad in a way that is hard to notice: too eager and a returning
 * user is sent through calibration every launch, too lax and the "mandatory on first run"
 * requirement quietly does not hold.
 */

export interface StepDefinition {
  step: CalibrationStep
  title: string
  /** What to physically do. Written as an instruction, because it is one. */
  instruction: string
  /** Why this step exists, in one line — the user is being asked to perform, so say what for. */
  purpose: string
  /**
   * Instruction per engine-reported phase, for steps that ask for more than one thing.
   *
   * The engine owns the timing, so it names the phase and this maps it to words. Keeping the words
   * here rather than in the engine is the same split as everywhere else: the measurement is the
   * engine's, the wording is the UI's.
   */
  phases?: Record<string, string>
}

/** Keyed by step so a lookup is total — there is no "definition not found" case to handle. */
export const STEP_DEFINITIONS: Record<CalibrationStep, StepDefinition> = {
  neutral: {
    step: 'neutral',
    title: 'Resting position',
    instruction: 'Hold your hand where it feels comfortable and keep it still.',
    purpose: 'Centres the active area on you, instead of assuming you sit in front of the lens.',
  },
  reach: {
    step: 'reach',
    title: 'Reach',
    instruction:
      'Move your hand slowly to the left, right, up and down — as far as is comfortable.',
    purpose: 'Sizes the active area so the screen edges are reachable without straining.',
  },
  pinch: {
    step: 'pinch',
    title: 'Click threshold',
    instruction:
      'Touch your thumb and index finger together firmly, three times, opening your hand fully in between. Then, when asked, close your hand into a fist.',
    purpose:
      'Measures your pinch against your closed hand — the threshold has to catch one without catching the other.',
    phases: {
      pinch: 'Pinch: thumb to index finger, firmly, opening your hand fully in between.',
      fist: 'Now close your hand into a fist a few times — this is not a click, and the engine needs to see that it is not.',
    },
  },
}

/**
 * Should the app force the user into calibration before anything else?
 *
 * - **No profile at all** — the engine runs with `--no-profile` and persists nothing. A wizard
 *   whose result evaporates on exit is worse than no wizard, so there is no gate.
 * - **Stale** — a profile exists but was refused, almost always because it was calibrated against
 *   a different MediaPipe model. Its numbers are not evidence about this one.
 * - **Never calibrated** — the first-run case the requirement is actually about.
 */
export function shouldCalibrate(profile: ProfileInfo | null): boolean {
  if (profile === null) return false
  return profile.stale || !profile.calibrated
}

/** Human-readable summary of a suggestion, for the "apply this?" line. */
export function describeSuggestion(suggestion: SettingsPatch): string[] {
  const labels: Record<string, string> = {
    centerX: 'Horizontal centre',
    centerY: 'Vertical centre',
    coverage: 'Active area',
    pinchClose: 'Pinch closes at',
    pinchOpen: 'Pinch opens at',
  }

  const lines: string[] = []
  for (const [section, values] of Object.entries(suggestion)) {
    if (section === 'reset' || typeof values !== 'object' || values === null) continue
    for (const [knob, value] of Object.entries(values)) {
      if (typeof value !== 'number') continue
      lines.push(`${labels[knob] ?? knob}: ${value.toFixed(2)}`)
    }
  }
  return lines
}
