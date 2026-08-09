import { create } from 'zustand'

import type { ErrorCode } from '@/lib/protocol'
import type { DiscoveryFailureReason } from '@/lib/discovery'

/**
 * `starting` (engine spawned, handshake not yet published) is genuinely distinct from
 * `connecting` (endpoint known, socket opening) and is worth showing during cold start.
 */
export type ConnectionPhase =
  | 'idle'
  | 'starting'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'disconnected'
  | 'error'

export interface ConnectionError {
  code: ErrorCode | DiscoveryFailureReason | 'socket-error'
  message: string
}

interface ConnectionState {
  phase: ConnectionPhase
  error: ConnectionError | null
  engineVersion: string | null
  serverProtocolVersion: string | null
  endpointUrl: string | null
  attempt: number

  setPhase: (phase: ConnectionPhase) => void
  setError: (error: ConnectionError) => void
  setHello: (engineVersion: string, protocolVersion: string) => void
  setEndpoint: (url: string | null) => void
  incrementAttempt: () => void
  reset: () => void
}

const INITIAL = {
  phase: 'idle' as ConnectionPhase,
  error: null,
  engineVersion: null,
  serverProtocolVersion: null,
  endpointUrl: null,
  attempt: 0,
}

export const useConnectionStore = create<ConnectionState>((set) => ({
  ...INITIAL,

  setPhase: (phase) => set((state) => (state.phase === phase ? state : { phase })),
  setError: (error) => set({ error, phase: 'error' }),
  setHello: (engineVersion, serverProtocolVersion) => set({ engineVersion, serverProtocolVersion }),
  setEndpoint: (endpointUrl) => set({ endpointUrl }),
  incrementAttempt: () => set((state) => ({ attempt: state.attempt + 1 })),
  reset: () => set({ ...INITIAL }),
}))

export function isLive(phase: ConnectionPhase): boolean {
  return phase === 'connected'
}
