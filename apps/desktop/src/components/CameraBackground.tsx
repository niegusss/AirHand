import { useEffect, useRef, useState } from 'react'

import { subscribeToPreview } from '@/lib/previewBuffer'

/**
 * The camera feed, blurred, behind the whole app.
 *
 * More than decoration: it shows what the engine can actually see. Bad framing, a dark room or a
 * hand that has drifted out of shot become obvious from the background alone, without opening
 * Diagnostics.
 *
 * ## Why the canvas stays tiny
 *
 * Frames arrive at 320 px wide and the canvas keeps that as its backing size — CSS stretches it
 * to fill the viewport. The upscale does most of the blurring for free, so the CSS `blur()` on
 * top can be small. Blurring a 2560-px-wide surface fifteen times a second would cost real GPU
 * time for a result nobody can distinguish from this one.
 *
 * Mirrored to match the landmark overlay, which is mirrored because a preview that moves the
 * opposite way to your hand is unusable.
 */
export function CameraBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [hasFrame, setHasFrame] = useState(false)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const context = canvas.getContext('2d')
    if (!context) return

    // Driven by frame arrival, not by requestAnimationFrame: the engine's preview rate is the
    // draw rate, so a rAF loop would redraw the same frame three times out of four.
    return subscribeToPreview((frame) => {
      if (canvas.width !== frame.width || canvas.height !== frame.height) {
        canvas.width = frame.width
        canvas.height = frame.height
      }
      context.drawImage(frame, 0, 0)
      setHasFrame(true)
    })
  }, [])

  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-background">
      <canvas
        ref={canvasRef}
        aria-hidden
        className="size-full object-cover blur-xl transition-opacity duration-700"
        style={{
          opacity: hasFrame ? 1 : 0,
          // Negative X mirrors; the 1.1 scale pushes the blur's soft edge off-screen, which
          // otherwise shows as a pale border all the way round. Set here rather than as
          // `scale-110 -scale-x-110` so the two axes cannot fight over class order.
          transform: 'scaleX(-1.1) scaleY(1.1)',
        }}
      />

      {/*
        Fallback and scrim in one layer. With no frames it is the entire background; with frames
        it is what keeps text legible over moving video — the real risk of this design.
      */}
      <div className="absolute inset-0 bg-background/70 bg-[radial-gradient(circle_at_30%_20%,rgba(148,163,184,0.10),transparent_60%)]" />
    </div>
  )
}
