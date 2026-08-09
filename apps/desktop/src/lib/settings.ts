import type { EngineSettings, SettingsPatch } from '@/lib/protocol'

/**
 * Which engine knob a control edits.
 *
 * Deliberately no `bounds` field. Ranges arrive on every `settings` message precisely so no second
 * copy exists to drift from `settings.py`, and a struct with nowhere to put one cannot acquire one
 * by accident.
 */
export interface KnobSpec {
  section: keyof EngineSettings
  knob: string
  label: string
  /**
   * What each end means, in the user's terms.
   *
   * These are engine internals — `minCutoff` rises as smoothing *falls* — so a label naming the
   * quality can point the opposite way to the slider under it. See `SettingSlider`.
   */
  lowLabel?: string
  highLabel?: string
  description?: string
  step?: number
  decimals?: number
  /** Rendered behind a disclosure. Still a real control, still covered by the reachability test. */
  advanced?: boolean
}

export interface SettingsGroup {
  title: string
  summary: string
  knobs: KnobSpec[]
}

/**
 * Knobs written by a compound control rather than by a slider each.
 *
 * These two are the hysteresis pair. The engine validates `pinch_open > pinch_close` inside
 * `GestureConfig`, which is a relationship *between* fields and therefore invisible to the
 * per-field range checks — the refusal arrives from the dataclass constructor, long after the
 * bounds on the wire said the value was fine. Two independent sliders can be dragged into refusing
 * each other for a reason nothing on screen explains; one control that always sends both cannot.
 *
 * Listed here rather than left out silently, so the reachability test still accounts for them.
 */
export const DRIVEN_AS_PAIR: ReadonlySet<string> = new Set([
  'gesture.pinchClose',
  'gesture.pinchOpen',
])

/**
 * The Settings screen, grouped by what the user wants to change.
 *
 * **Not grouped by engine section.** `gesture` / `pointer` / `cursor` are implementation names —
 * useful next to each control for matching a value against the engine log or `profile.json`, and
 * useless as an organising principle. Smoothing and active area both live under "pointing" as far
 * as anyone using this is concerned, and they sit in different sections of the wire.
 */
export const SETTINGS_GROUPS: SettingsGroup[] = [
  {
    title: 'Pointing',
    summary: 'How closely the cursor follows your hand, and how much it smooths out tremor.',
    knobs: [
      {
        section: 'pointer',
        knob: 'minCutoff',
        label: 'Follow speed at rest',
        lowLabel: 'steadier',
        highLabel: 'faster',
        description:
          'Low values hold the cursor still while your hand is; too low and it feels sluggish.',
        step: 0.1,
        decimals: 1,
      },
      {
        section: 'pointer',
        knob: 'beta',
        label: 'Follow speed when moving',
        lowLabel: 'smoother',
        highLabel: 'faster',
        description: 'How much the cursor may speed up once your hand is actually moving.',
        step: 0.5,
        decimals: 1,
      },
    ],
  },
  {
    title: 'Reach',
    summary:
      'Which part of the camera frame maps onto the whole screen — the dashed box in the preview.',
    knobs: [
      {
        section: 'cursor',
        knob: 'coverage',
        label: 'Active area',
        lowLabel: 'less hand travel',
        highLabel: 'more',
        description: 'A smaller area means less hand movement per screen pixel.',
      },
      {
        section: 'cursor',
        knob: 'centerX',
        label: 'Centre — across',
        lowLabel: 'left',
        highLabel: 'right',
        description: 'Measured by the calibration wizard. Changing it by hand replaces that measurement.',
      },
      {
        section: 'cursor',
        knob: 'centerY',
        label: 'Centre — up and down',
        lowLabel: 'up',
        highLabel: 'down',
      },
    ],
  },
  {
    title: 'Clicking',
    summary: 'How far your fingers have to close to click, and how long a hold becomes a drag.',
    knobs: [
      {
        section: 'gesture',
        knob: 'holdToDragSeconds',
        label: 'Hold before a drag starts',
        lowLabel: 'quicker',
        highLabel: 'more deliberate',
        description: 'A pinch held longer than this drags instead of clicking.',
        step: 0.05,
      },
    ],
  },
  {
    title: 'Scrolling',
    summary: 'The two-finger scroll gesture.',
    knobs: [
      {
        section: 'gesture',
        knob: 'scrollStep',
        label: 'Scroll amount',
        lowLabel: 'finer',
        highLabel: 'coarser',
        description: 'How far one notch of hand movement scrolls.',
      },
    ],
  },
  {
    title: 'Hand pose',
    summary: 'What counts as a straight finger when the engine reads your hand shape.',
    knobs: [
      {
        section: 'gesture',
        knob: 'extendedAngleDegrees',
        label: 'Finger counts as extended above',
        lowLabel: 'more curl allowed',
        highLabel: 'must be straight',
        description: 'The joint angle at which a finger is treated as pointing rather than folded.',
        step: 1,
        decimals: 0,
      },
    ],
  },
  {
    title: 'Advanced',
    summary:
      'Rarely worth touching. Each of these was measured once and is here so a bad camera or an unusual hand is not a dead end.',
    knobs: [
      {
        section: 'pointer',
        knob: 'holdOnPinch',
        label: 'Hold the cursor still while clicking',
        description:
          'Stops the cursor drifting off the target as your fingers close. Turning it off makes a pinch able to move the pointer.',
        advanced: true,
      },
      {
        section: 'pointer',
        knob: 'dCutoff',
        label: 'Speed-estimate smoothing',
        lowLabel: 'steadier',
        highLabel: 'more reactive',
        description: 'Smoothing applied to how fast the filter thinks your hand is moving.',
        step: 0.1,
        decimals: 1,
        advanced: true,
      },
      {
        section: 'pointer',
        knob: 'dropoutGraceSeconds',
        label: 'Pointer dropout grace',
        lowLabel: 'none',
        highLabel: 'longer',
        description:
          'How long filter state survives a lost frame. It never extends the cursor position — no hand still means no movement on that very frame.',
        step: 0.05,
        advanced: true,
      },
      {
        section: 'gesture',
        knob: 'dropoutGraceSeconds',
        label: 'Click dropout grace',
        lowLabel: 'none',
        highLabel: 'longer',
        description:
          'A click is defined by the release, and a pinch is exactly when the thumb disappears behind the index finger. Zero makes a single lost frame swallow the click.',
        step: 0.05,
        advanced: true,
      },
      {
        section: 'gesture',
        knob: 'clickLatchSeconds',
        label: 'Click visible in the UI for',
        lowLabel: 'shorter',
        highLabel: 'longer',
        description:
          'Display only — it does not change clicking. It holds the reported gesture long enough for a 10 Hz readout to catch it; actuation reads the event stream, not this.',
        step: 0.05,
        advanced: true,
      },
    ],
  },
]

/**
 * Four decimal places: enough to carry a calibration measurement through untouched, and short
 * enough that adding two slider values cannot leave `0.5900000000000001` in the profile on disk.
 */
function round(value: number): number {
  return Math.round(value * 10_000) / 10_000
}

/**
 * The pinch pair as one patch: a threshold and the band above it.
 *
 * Both fields, every time. `GestureConfig` refuses `pinch_open <= pinch_close`, and that check runs
 * in the dataclass constructor — after the range checks, and invisible to them. Sending only the
 * half the user moved is what lets a legal value be refused for a reason nothing on screen explains.
 */
export function pinchPatch(close: number, band: number): { gesture: { pinchClose: number; pinchOpen: number } } {
  return { gesture: { pinchClose: round(close), pinchOpen: round(close + band) } }
}

/**
 * What the band slider may offer, given where the threshold sits.
 *
 * Derived from `pinchOpen`'s wire bounds rather than declared: the band is not a knob the engine
 * knows about, so its range is simply whatever room `pinchOpen` has left above the current
 * `pinchClose`. The floor is the slider's own step — that encodes the engine's "must be greater
 * than", which is a rule, not a copy of a range.
 */
export function bandBounds(
  close: number,
  openBounds: [number, number],
  step: number,
): [number, number] {
  const low = Math.max(step, round(openBounds[0] - close))
  return [low, Math.max(low, round(openBounds[1] - close))]
}

/** Reads one knob's live value out of the settings the engine last reported. */
export function knobValue(settings: EngineSettings, spec: KnobSpec): number | boolean {
  const section = settings[spec.section] as Record<string, number | boolean>
  return section[spec.knob]
}

/**
 * A single-knob patch, shaped exactly like the `set_settings` body.
 *
 * The cast is unavoidable with a computed key, and it is why `SETTINGS_GROUPS` is checked against a
 * fully-typed settings value in the tests: that is what stops a typo here reaching the engine as an
 * unknown-knob refusal instead of a compile error.
 */
export function knobPatch(spec: KnobSpec, value: number | boolean): SettingsPatch {
  return { [spec.section]: { [spec.knob]: value } } as unknown as SettingsPatch
}
