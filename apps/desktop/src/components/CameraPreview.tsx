import { useEffect, useRef } from 'react'
import { CameraOff, Hand, PlugZap, ScanLine, TriangleAlert, VideoOff } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { subscribeToFrames } from '@/lib/telemetryPump'
import { LANDMARK_COUNT, type Landmark, type TelemetryMessage } from '@/lib/protocol'
import { useTrackingStore, type PipelineIssue } from '@/stores/trackingStore'

/** MediaPipe's 21-landmark hand topology. */
const BONES: ReadonlyArray<readonly [number, number]> = [
  [0, 1], [1, 2], [2, 3], [3, 4], // thumb
  [0, 5], [5, 6], [6, 7], [7, 8], // index
  [9, 10], [10, 11], [11, 12], // middle
  [13, 14], [14, 15], [15, 16], // ring
  [0, 17], [17, 18], [18, 19], [19, 20], // pinky
  [5, 9], [9, 13], [13, 17], // palm
]

const ISSUE_COPY: Record<PipelineIssue, { icon: LucideIcon; title: string; detail: string }> = {
  disconnected: {
    icon: PlugZap,
    title: 'Engine not connected',
    detail: 'The CV engine is unreachable, so there is nothing to preview.',
  },
  'camera-error': {
    icon: TriangleAlert,
    title: 'Camera error',
    detail: 'The engine reported a camera failure. Check that no other app is using it.',
  },
  'camera-off': {
    icon: CameraOff,
    title: 'Camera off',
    detail: 'The engine is connected but the camera is not capturing.',
  },
  'not-tracking': {
    icon: VideoOff,
    title: 'Tracking stopped',
    detail: 'Start tracking to see the hand skeleton.',
  },
  'stream-stalled': {
    icon: TriangleAlert,
    title: 'Stream stalled',
    detail: 'Connected, but no telemetry has arrived recently.',
  },
  'no-hand': {
    icon: Hand,
    title: 'No hand detected',
    detail: 'Hold your hand in view of the camera.',
  },
}

interface CameraPreviewProps {
  issue: PipelineIssue | null
  /**
   * Extra layers drawn inside the aspect-corrected box — the calibration reach overlay.
   *
   * A slot rather than a second component beside this one: the `aspect-ratio` sizing below is what
   * stops the hand stretching, and anything positioned in frame coordinates has to share exactly
   * that box or it will disagree with the skeleton by however much the layout differs.
   */
  children?: React.ReactNode
}

/**
 * Fallback shape when the engine has not reported its frame size.
 *
 * Deliberately `null`, not `4/3`: guessing an aspect is exactly what produced the stretched hand,
 * and "runs on any webcam" rules out picking a favourite. With no aspect the overlay fills its
 * container as it always did — no worse than before, and it self-corrects the moment the camera
 * reports in.
 */
const UNKNOWN_ASPECT = null

/**
 * Landmark overlay.
 *
 * Draws directly to canvas from the rAF pump — it never re-renders React, because at 60 Hz that
 * would be the single most expensive thing in the app.
 *
 * Paints no backdrop of its own: the camera image is already behind the whole app, sharp
 * landmarks over the blurred feed they were computed from. Deliberately *not* drawn over an
 * unblurred copy of the frame — the points would then have to line up to the pixel, and any
 * timing skew between the preview and the landmark stream would read as inaccurate tracking.
 *
 * ## The container takes the camera's aspect, not the layout's
 *
 * `x` is normalized by frame width and `y` by height, so drawing into a container of any other
 * shape stretches the hand along one axis — which is precisely what happened when this stopped
 * being `aspect-video` and started filling a wide column. Sizing the card from the reported frame
 * geometry fixes it at the source, and it makes the empty space beside the card read as
 * deliberate rather than as a broken layout.
 */
export function CameraPreview({ issue, children }: CameraPreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const frameWidth = useTrackingStore((state) => state.frameWidth)
  const frameHeight = useTrackingStore((state) => state.frameHeight)
  const aspect = frameWidth && frameHeight ? frameWidth / frameHeight : UNKNOWN_ASPECT

  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    const context = canvas.getContext('2d')
    if (!context) return

    const styles = getComputedStyle(canvas)
    const liveColor = styles.getPropertyValue('--status-live').trim() || '#34d399'
    const boneColor = styles.getPropertyValue('--muted-foreground').trim() || '#94a3b8'

    let width = 0
    let height = 0

    const resize = () => {
      const ratio = window.devicePixelRatio || 1
      const rect = container.getBoundingClientRect()
      width = rect.width
      height = rect.height
      canvas.width = Math.max(1, Math.round(width * ratio))
      canvas.height = Math.max(1, Math.round(height * ratio))
      context.setTransform(ratio, 0, 0, ratio, 0, 0)
    }

    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(container)

    const draw = (sample: TelemetryMessage | null) => {
      context.clearRect(0, 0, width, height)

      const landmarks = sample?.landmarks
      if (!landmarks || landmarks.length === 0) return

      // The camera image is mirrored for the user, so the overlay mirrors with it.
      const toPixels = (point: Landmark): [number, number] => [
        (1 - point[0]) * width,
        point[1] * height,
      ]

      context.lineWidth = 2
      context.strokeStyle = boneColor
      context.lineCap = 'round'
      context.beginPath()
      for (const [from, to] of BONES) {
        if (from >= landmarks.length || to >= landmarks.length) continue
        const [x1, y1] = toPixels(landmarks[from])
        const [x2, y2] = toPixels(landmarks[to])
        context.moveTo(x1, y1)
        context.lineTo(x2, y2)
      }
      context.stroke()

      context.fillStyle = liveColor
      for (let index = 0; index < landmarks.length; index += 1) {
        const [x, y] = toPixels(landmarks[index])
        // Fingertips get a larger dot — they are what the Gesture Engine actually keys on.
        const isFingertip = index === 4 || index === 8 || index === 12 || index === 16 || index === 20
        context.beginPath()
        context.arc(x, y, isFingertip ? 4 : 2.5, 0, Math.PI * 2)
        context.fill()
      }
    }

    const unsubscribe = subscribeToFrames(draw)

    return () => {
      unsubscribe()
      observer.disconnect()
    }
  }, [])

  const copy = issue ? ISSUE_COPY[issue] : null
  const Icon = copy?.icon ?? ScanLine

  return (
    <div
      ref={containerRef}
      // Unstyled on purpose — the skeleton sits directly on the camera background. The sizing
      // stays: `aspect-ratio` is the fix that stops the hand stretching, not decoration, and it
      // is now invisible, so a regression here would be easy to miss. Centred and bounded on both
      // axes because `aspect-ratio` alone would overflow a short viewport.
      className="relative mx-auto h-full max-h-full w-full"
      style={aspect ? { aspectRatio: String(aspect), width: 'auto' } : undefined}
    >
      <canvas
        ref={canvasRef}
        className="absolute inset-0 size-full"
        role="img"
        aria-label="Hand landmark overlay"
      />

      {children}

      {/*
        A compact centred panel, not a full-bleed cover. Covering the whole area would put back
        the rectangle this container just lost — and it would do it in the most common state of
        all, "no hand in frame".
      */}
      {copy ? (
        <div className="absolute inset-0 flex items-center justify-center p-6">
          <div className="surface flex max-w-sm flex-col items-center gap-2 px-5 py-4 text-center">
            <Icon className="size-6 text-muted-foreground" aria-hidden />
            <p className="text-sm font-medium text-foreground">{copy.title}</p>
            <p className="text-xs text-muted-foreground">{copy.detail}</p>
          </div>
        </div>
      ) : null}

      <p className="absolute bottom-2 left-3 text-[10px] tracking-wide text-muted-foreground uppercase">
        Landmark overlay · {LANDMARK_COUNT} points
        {frameWidth && frameHeight ? ` · ${frameWidth}×${frameHeight}` : null}
      </p>
    </div>
  )
}
