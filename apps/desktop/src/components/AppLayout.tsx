import { NavLink, Outlet } from 'react-router-dom'
import { Activity, Crosshair, Gauge, Hand, Settings } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { CalibrationGate } from '@/components/CalibrationGate'
import { CameraBackground } from '@/components/CameraBackground'
import { ConnectionBadge } from '@/components/ConnectionBadge'
import { EngineControls } from '@/components/EngineControls'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { shouldCalibrate } from '@/lib/calibration'
import { cn } from '@/lib/utils'
import { useSettingsStore } from '@/stores/settingsStore'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: Gauge },
  { to: '/calibration', label: 'Calibration', icon: Crosshair },
  { to: '/settings', label: 'Settings', icon: Settings },
  { to: '/diagnostics', label: 'Diagnostics', icon: Activity },
]

/** Mirrors `CalibrationGate`: the wizard itself, and the screen that explains why it cannot run. */
const ALWAYS_REACHABLE = ['/calibration', '/diagnostics']

/**
 * The app shell: camera feed behind everything, a floating rail on the left, content on the right.
 *
 * The rail is sized to its content rather than stretched full height — the point of the layout is
 * that the camera image is the surface and the chrome floats on it. A full-height column would
 * cut the background in half and undo that.
 *
 * Everything that floats is translucent with a backdrop blur. That is legibility work, not
 * styling: the background moves, and moving video behind small text is the one way this design
 * can fail.
 */
export function AppLayout() {
  // Only to grey out what the gate would refuse anyway. A nav link that navigates and then
  // bounces straight back reads as a bug; one that is visibly unavailable reads as a rule.
  const calibrationRequired = useSettingsStore((state) => shouldCalibrate(state.profile))

  return (
    <div className="relative h-full min-h-0">
      <CameraBackground />

      <div className="flex h-full min-h-0 gap-5 p-5">
        <nav
          className="surface flex w-56 shrink-0 flex-col gap-4 self-start p-3"
          aria-label="Main"
        >
          <div className="flex items-center gap-2 px-1 pt-1">
            <Hand className="size-4 text-status-live" aria-hidden />
            <span className="text-sm font-semibold tracking-tight">AirHand Mouse</span>
          </div>

          <ConnectionBadge />

          <div className="flex flex-col gap-1">
            {NAV_ITEMS.map(({ to, label, icon: Icon }) => {
              const locked = calibrationRequired && !ALWAYS_REACHABLE.includes(to)
              return (
                <NavLink
                  key={to}
                  to={to}
                  end={to === '/'}
                  aria-disabled={locked || undefined}
                  title={locked ? 'Finish calibration first' : undefined}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors duration-150',
                      isActive
                        ? 'bg-secondary text-foreground'
                        : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
                      locked && 'pointer-events-none opacity-40',
                    )
                  }
                >
                  <Icon className="size-4" aria-hidden />
                  {label}
                </NavLink>
              )
            })}
          </div>

          <div className="border-t border-border pt-3">
            <EngineControls />
          </div>
        </nav>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
          <ErrorBoundary>
            <CalibrationGate>
              <Outlet />
            </CalibrationGate>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
