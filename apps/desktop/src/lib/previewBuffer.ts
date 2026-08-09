/**
 * Decoded camera-preview frames.
 *
 * Sibling of `telemetryBuffer.ts`, with one extra job: the engine sends JPEG bytes, and turning
 * those into something drawable is asynchronous. Three rules keep that from becoming a leak or a
 * backlog:
 *
 * - **Decode one at a time.** `createImageBitmap` is async; frames arriving while a decode is in
 *   flight replace the pending one instead of queueing. Dropping a background frame costs
 *   nothing, and a queue would grow without bound the moment decoding fell behind.
 * - **Close every bitmap you replace.** An `ImageBitmap` holds GPU memory that garbage collection
 *   does not reclaim promptly. At 15 frames a second, forgetting this leaks steadily.
 * - **Notify on arrival, do not poll.** The frame rate *is* the draw rate, so subscribers are
 *   called when a frame is ready rather than sampled from the rAF loop like telemetry.
 */

export type PreviewSubscriber = (frame: ImageBitmap) => void

const subscribers = new Set<PreviewSubscriber>()

let latest: ImageBitmap | null = null
/** Bytes that arrived while a decode was running. Only the newest is kept. */
let queued: Blob | null = null
let decoding = false

export function subscribeToPreview(subscriber: PreviewSubscriber): () => void {
  subscribers.add(subscriber)
  // Hand over the current frame immediately, so a component mounting between frames is not
  // stuck on an empty background for up to a frame interval.
  if (latest !== null) subscriber(latest)
  return () => {
    subscribers.delete(subscriber)
  }
}

export function pushPreviewFrame(data: Blob | ArrayBuffer): void {
  const blob = data instanceof Blob ? data : new Blob([data], { type: 'image/jpeg' })

  if (decoding) {
    queued = blob
    return
  }
  void decode(blob)
}

async function decode(blob: Blob): Promise<void> {
  decoding = true
  try {
    const bitmap = await createImageBitmap(blob)
    latest?.close()
    latest = bitmap
    for (const subscriber of subscribers) subscriber(bitmap)
  } catch {
    // A truncated or malformed frame is not worth reporting: the next one is milliseconds away
    // and the previous frame stays on screen in the meantime.
  } finally {
    decoding = false
    const next = queued
    queued = null
    if (next !== null) void decode(next)
  }
}

export function readPreviewFrame(): ImageBitmap | null {
  return latest
}

/** Release the current frame. Called on disconnect, alongside `clearTelemetry`. */
export function clearPreview(): void {
  latest?.close()
  latest = null
  queued = null
}
