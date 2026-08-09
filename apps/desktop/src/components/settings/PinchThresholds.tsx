import { SettingSlider } from '@/components/settings/SettingSlider'
import { bandBounds, pinchPatch } from '@/lib/settings'
import type { EngineSettings, SettingsBounds, SettingsPatch } from '@/lib/protocol'

/** One slider step. Small enough to be fine-grained, large enough that the gap is never zero. */
const STEP = 0.01

interface PinchThresholdsProps {
  settings: EngineSettings
  bounds: SettingsBounds
  disabled?: boolean
  onCommit: (patch: SettingsPatch) => void
}

/**
 * The pinch hysteresis pair, as one control.
 *
 * ## Why this is not two sliders
 *
 * `GestureConfig.__post_init__` refuses `pinch_open <= pinch_close`. That is a relationship
 * *between* fields, so the per-field range checks in `settings.py` cannot see it and the bounds on
 * the wire cannot express it — the refusal comes out of the dataclass constructor at the end of
 * `merge()`. With independent sliders the user drags the threshold up through a value the range
 * said was legal, the engine refuses, the slider snaps back, and nothing on screen explains why.
 *
 * Editing the threshold and the *band above it* makes the invariant hold by construction: every
 * patch carries both fields, and a positive band cannot produce an illegal pair. It is also the
 * same model the engine's own calibration uses — `calibration._pinch` measures a threshold and
 * carries the existing band across untouched.
 *
 * The band slider's range comes from `pinchOpen`'s wire bounds and moves as the threshold moves,
 * so it can never offer a value that would be refused for being out of range either.
 */
export function PinchThresholds({ settings, bounds, disabled, onCommit }: PinchThresholdsProps) {
  const { pinchClose, pinchOpen } = settings.gesture
  const closeBounds = bounds.gesture.pinchClose
  const openBounds = bounds.gesture.pinchOpen
  const band = pinchOpen - pinchClose

  if (closeBounds === null || openBounds === null) {
    return (
      <p className="text-sm text-muted-foreground">
        The engine reported no range for the pinch thresholds, so there is nothing safe to draw.
      </p>
    )
  }

  return (
    <>
      <SettingSlider
        label="Click threshold"
        lowLabel="fingers must nearly touch"
        highLabel="easier to trigger"
        description="How close your thumb and finger must come for the engine to call it a pinch."
        value={pinchClose}
        bounds={closeBounds}
        step={STEP}
        disabled={disabled}
        // The band is preserved rather than the release point, so moving the threshold does not
        // silently squeeze the hysteresis gap shut.
        onCommit={(close) => onCommit(pinchPatch(close, band))}
      />
      <SettingSlider
        label="Release gap"
        lowLabel="tighter"
        highLabel="wider"
        description="How far your fingers must reopen before the click completes. A gap that is too small produces repeated phantom clicks from a hand resting near the threshold."
        value={band}
        bounds={bandBounds(pinchClose, openBounds, STEP)}
        step={STEP}
        disabled={disabled}
        onCommit={(next) => onCommit(pinchPatch(pinchClose, next))}
      />
      <p className="text-[10px] text-muted-foreground/80">
        Together: closes at{' '}
        <span className="tabular text-foreground/70">{pinchClose.toFixed(2)}</span>, opens at{' '}
        <span className="tabular text-foreground/70">{pinchOpen.toFixed(2)}</span>. Both are
        multiples of your hand's size, so they mean the same thing at any distance from the camera.
      </p>
    </>
  )
}
