/**
 * WebSocket client for the AirHand engine.
 *
 * Owns the connection lifecycle: discovery, handshake, auth, version check, reconnect with
 * backoff. Telemetry goes straight into the telemetry buffer and never through React.
 *
 * ## Why every handler is guarded
 *
 * More than one socket can exist at once — React StrictMode double-invokes effects in dev, and a
 * reconnect can overlap a socket that has not finished closing. Connection state lives in a
 * single shared store, so an event from an abandoned socket would corrupt the live one's state:
 * an orphan that never authenticates gets closed by the server with `unauthorized`, and its
 * `onclose` would otherwise wipe the working connection's telemetry buffer.
 *
 * Two mechanisms prevent that:
 *  - a monotonic `generation`, bumped on every connect/disconnect, invalidates in-flight work
 *    across `await` boundaries;
 *  - every handler checks it was fired by the socket that is *currently* installed.
 *
 * Handlers are also detached on teardown, so a closing socket goes quiet immediately.
 */

import { discoverEngine, isTauriRuntime } from './discovery'
import {
  isPreviewFrame,
  isProtocolCompatible,
  parseServerMessage,
  PROTOCOL_VERSION,
  type ClientMessage,
} from './protocol'
import { clearPreview, pushPreviewFrame } from './previewBuffer'
import { clearTelemetry, pushTelemetry } from './telemetryBuffer'
import { useCalibrationStore } from '@/stores/calibrationStore'
import { useCamerasStore } from '@/stores/camerasStore'
import { useConnectionStore } from '@/stores/connectionStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useTrackingStore } from '@/stores/trackingStore'

const BASE_BACKOFF_MS = 500
const MAX_BACKOFF_MS = 10_000

export class EngineClient {
  private socket: WebSocket | null = null
  private retryTimer: number | null = null
  private closedByUser = false
  private attempt = 0
  /** Bumped on every connect/disconnect; stale work compares against it and bails out. */
  private generation = 0
  /** Whether this connection has already asked for the preview stream. Reset on teardown. */
  private previewRequested = false

  connect(): void {
    this.closedByUser = false
    this.clearRetry()
    const generation = ++this.generation
    void this.openConnection(generation)
  }

  disconnect(): void {
    this.closedByUser = true
    this.generation += 1
    this.clearRetry()
    this.teardownSocket(1000, 'client shutdown')
    clearTelemetry()
    clearPreview()
    useTrackingStore.getState().clearStream()
    useSettingsStore.getState().reset()
    useCalibrationStore.getState().reset()
    useCamerasStore.getState().reset()
    useConnectionStore.getState().setPhase('disconnected')
  }

  send(message: ClientMessage): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false
    this.socket.send(JSON.stringify(message))
    return true
  }

  /**
   * Detach handlers and close the current socket.
   *
   * Clearing the handlers first is the important half: a socket that is closing will still fire
   * `onclose` (and may still deliver a queued `unauthorized`), and we no longer want to hear it.
   */
  private teardownSocket(code: number, reason: string): void {
    const socket = this.socket
    this.socket = null
    this.previewRequested = false
    if (!socket) return

    socket.onmessage = null
    socket.onerror = null
    socket.onclose = null

    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      socket.close(code, reason)
    }
  }

  private isCurrent(generation: number, socket: WebSocket): boolean {
    return generation === this.generation && socket === this.socket
  }

  private async openConnection(generation: number): Promise<void> {
    if (generation !== this.generation) return

    const connection = useConnectionStore.getState()
    // `starting` is not a nicer word for `connecting`: inside the shell, discovery may be waiting
    // on an engine process it just launched. In a browser there is nothing to launch, so claiming
    // to start something would be a lie.
    const starting = this.attempt === 0 && isTauriRuntime()
    connection.setPhase(starting ? 'starting' : this.attempt === 0 ? 'connecting' : 'reconnecting')

    const discovery = await discoverEngine()
    // discoverEngine is async, so the world may have moved on while it resolved.
    if (generation !== this.generation) return

    if (!discovery.ok) {
      // A missing endpoint is a configuration problem, not a transient one. Retrying on a loop
      // would just hide it.
      connection.setError({ code: discovery.reason, message: discovery.message })
      return
    }

    connection.setEndpoint(discovery.url)
    // The engine exists now; what remains is an ordinary socket.
    if (starting) connection.setPhase('connecting')

    // Never run two sockets side by side.
    this.teardownSocket(1000, 'replaced by a newer connection')

    let socket: WebSocket
    try {
      socket = new WebSocket(discovery.url)
    } catch (cause) {
      connection.setError({
        code: 'socket-error',
        message: `Could not open ${discovery.url}: ${String(cause)}`,
      })
      this.scheduleRetry(generation)
      return
    }

    this.socket = socket

    socket.onmessage = (event) => {
      if (!this.isCurrent(generation, socket)) return
      this.handleMessage(socket, event.data, discovery.token)
    }

    socket.onerror = () => {
      if (!this.isCurrent(generation, socket)) return
      // The browser deliberately withholds the reason for a failed WebSocket connection, so
      // there is nothing more specific to report here than "it failed".
      useConnectionStore.getState().setError({
        code: 'socket-error',
        message: `Cannot reach the engine at ${discovery.url}. Is it running?`,
      })
    }

    socket.onclose = () => {
      if (!this.isCurrent(generation, socket)) return
      this.socket = null
      this.previewRequested = false
      clearTelemetry()
      clearPreview()
      useTrackingStore.getState().clearStream()
      // Dropped rather than kept: settings describe an engine that is no longer there, and a
      // disconnected app must not offer editable controls that go nowhere. A reconnect sends
      // them again, right after `status`.
      useSettingsStore.getState().reset()
      // A measurement cannot outlive the connection that was feeding it — the countdown would
      // freeze mid-step and the wizard would wait for a verdict that is never coming.
      useCalibrationStore.getState().reset()
      // Devices belong to the engine that enumerated them, and `scanning` would otherwise be stuck
      // true forever if the connection dropped mid-probe.
      useCamerasStore.getState().reset()
      if (this.closedByUser) return
      useConnectionStore.getState().setPhase('reconnecting')
      this.scheduleRetry(generation)
    }
  }

  private handleMessage(socket: WebSocket, raw: unknown, token: string): void {
    // Binary means a camera preview frame. Checked before parsing, not instead of it —
    // parseServerMessage only ever accepts strings.
    if (isPreviewFrame(raw)) {
      pushPreviewFrame(raw)
      return
    }

    const message = parseServerMessage(raw)
    if (message === null) return

    const connection = useConnectionStore.getState()

    switch (message.type) {
      case 'hello': {
        if (!isProtocolCompatible(message.protocolVersion)) {
          // Refuse rather than half-work: an incompatible engine may interpret commands
          // differently, and this one drives real OS input.
          connection.setError({
            code: 'protocol_mismatch',
            message:
              `Engine speaks protocol ${message.protocolVersion}, this UI speaks ` +
              `${PROTOCOL_VERSION}. Rebuild both from the same shared/protocol.`,
          })
          this.closedByUser = true
          this.teardownSocket(4400, 'protocol mismatch')
          return
        }
        connection.setHello(message.engineVersion, message.protocolVersion)
        // Reply on the socket that greeted us, not on `this.socket` — during an overlap those
        // are different objects, and authenticating the wrong one leaves this socket to be
        // dropped by the server's auth timeout.
        socket.send(JSON.stringify({ type: 'auth', token } satisfies ClientMessage))
        break
      }

      case 'status': {
        // Status is the first message after a successful auth, so it doubles as the signal
        // that the connection is fully established.
        this.attempt = 0
        connection.setPhase('connected')
        // Ask for the camera preview once per connection. The engine encodes nothing until
        // someone asks, and status arrives repeatedly — re-asking every time would be noise.
        if (!this.previewRequested) {
          this.previewRequested = true
          socket.send(JSON.stringify({ type: 'command', action: 'enable_preview' }))
        }
        useTrackingStore.getState().applyStatus({
          camera: message.camera,
          tracking: message.tracking,
          cameraName: message.cameraName,
          // Optional since protocol 1.10.0; a 1.9.x engine simply never names a device index.
          cameraIndex: message.cameraIndex ?? null,
          cpuPercent: message.cpuPercent,
          statusMessage: message.message,
          // Optional since protocol 1.5.0; a 1.4.x engine leaves the overlay to fall back.
          frameWidth: message.frameWidth ?? null,
          frameHeight: message.frameHeight ?? null,
          // Optional since protocol 1.3.0; a 1.2.x engine simply never offers actuation.
          cursorAvailable: message.cursorAvailable ?? false,
          cursorEnabled: message.cursorEnabled ?? false,
          cursorReason: message.cursorReason ?? null,
          cursorDryRun: message.cursorDryRun ?? false,
          killswitchHotkey: message.killswitchHotkey ?? null,
        })
        break
      }

      // Protocol 1.6.0. Arrives after `status` on connect and on every change, to every client —
      // so two windows cannot disagree about what the engine is running.
      case 'settings':
        useSettingsStore.getState().applySettings(message)
        break

      // Protocol 1.7.0. Broadcast, not addressed to whoever asked, for the same reason as
      // `settings`: two windows must not disagree about where the wizard is.
      case 'calibration':
        useCalibrationStore.getState().applyCalibration(message)
        break

      // Protocol 1.10.0. Broadcast for the same reason again — a scan restarts the pipeline, so
      // every window has to learn that the device list and the selection moved.
      case 'cameras':
        useCamerasStore.getState().applyCameras(message)
        break

      case 'telemetry':
        pushTelemetry(message)
        break

      case 'error': {
        // A refused settings patch is the caller's problem, not a connection failure: the engine
        // is still healthy and the previous values are still in force. Routing it into the
        // connection error state would show "Engine unavailable" for a slider set too far.
        if (message.code === 'invalid_settings') {
          useSettingsStore.getState().setRejection(message.message)
          break
        }

        connection.setError({ code: message.code, message: message.message })
        if (message.code === 'unauthorized') {
          this.closedByUser = true
          this.teardownSocket(4401, 'unauthorized')
        }
        break
      }

      case 'pong':
        break
    }
  }

  private scheduleRetry(generation: number): void {
    if (this.closedByUser || this.retryTimer !== null) return
    if (generation !== this.generation) return

    this.attempt += 1
    useConnectionStore.getState().incrementAttempt()

    // Exponential backoff, capped. Never a tight retry loop.
    const delay = Math.min(BASE_BACKOFF_MS * 2 ** (this.attempt - 1), MAX_BACKOFF_MS)
    this.retryTimer = window.setTimeout(() => {
      this.retryTimer = null
      void this.openConnection(generation)
    }, delay)
  }

  private clearRetry(): void {
    if (this.retryTimer !== null) {
      window.clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
  }
}

export const engineClient = new EngineClient()
