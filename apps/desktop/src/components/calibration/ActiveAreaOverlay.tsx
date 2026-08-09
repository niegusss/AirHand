import { useEffect, useRef } from 'react'

import type { ActiveArea, TelemetryMessage } from '@/lib/protocol'
import { subscribeToFrames } from '@/lib/telemetryPump'

interface ActiveAreaOverlayProps {
  /** The rectangle the engine computed. Null until it knows both the screen and the camera. */
  area: ActiveArea | null
}

/**
 * The reach box and the hand's position inside it, drawn over the camera image.
 *
 * This is the wizard's "test it" affordance, and it deliberately **does not move the real
 * cursor**. Telemetry carries the anchor whether or not actuation is on, so the effect of every
 * setting is visible with the pointer left alone — which matters because the user has no working
 * gesture cursor until calibration finishes, and a preview that hijacked the real one would take
 * away the mouse they are using to run the wizard.
 *
 * The rectangle arrives computed from the engine; nothing here derives it. The dot is positioned
 * straight from the rAF pump without re-rendering React, the same as the landmark overlay.
 *
 * Both are mirrored, because the camera image is: your hand moving right has to move the marker
 * right.
 */
export function ActiveAreaOverlay({ area }: ActiveAreaOverlayProps) {
  const dotRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const move = (sample: TelemetryMessage | null) => {
      const dot = dotRef.current
      if (!dot) return

      const cursor = sample?.cursor ?? null
      if (!cursor) {
        dot.style.opacity = '0'
        return
      }
      dot.style.opacity = '1'
      dot.style.left = `${(1 - cursor.x) * 100}%`
      dot.style.top = `${cursor.y * 100}%`
    }

    return subscribeToFrames(move)
  }, [])

  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden>
      {area ? (
        <div
          className="absolute rounded-md border-2 border-dashed border-status-live/70 bg-status-live/5"
          style={{
            left: `${(1 - area.left - area.width) * 100}%`,
            top: `${area.top * 100}%`,
            width: `${area.width * 100}%`,
            height: `${area.height * 100}%`,
          }}
        />
      ) : null}

      <div
        ref={dotRef}
        className="absolute size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-status-live opacity-0 shadow-[0_0_12px_var(--status-live)]"
      />
    </div>
  )
}
