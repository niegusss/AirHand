import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { AppLayout } from '@/components/AppLayout'
import { useEngineLifecycle } from '@/hooks/useEngine'
import { CalibrationPage } from '@/pages/CalibrationPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { DiagnosticsPage } from '@/pages/DiagnosticsPage'
import { SettingsPage } from '@/pages/SettingsPage'

export function App() {
  // Mounted once at the root: the engine connection and telemetry pump must outlive navigation.
  useEngineLifecycle()

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<DashboardPage />} />
          <Route path="calibration" element={<CalibrationPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="diagnostics" element={<DiagnosticsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
