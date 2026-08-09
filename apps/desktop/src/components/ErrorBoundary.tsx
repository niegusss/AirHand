import { Component, type ErrorInfo, type ReactNode } from 'react'
import { TriangleAlert } from 'lucide-react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * Route-level error boundary.
 *
 * Keeps a crash in one screen from taking down the shell — which matters here because the engine
 * connection lives at the app root. Losing it on a render error would also stop tracking.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Route error:', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="mx-auto max-w-2xl" role="alert">
        <div className="flex items-center gap-2.5">
          <TriangleAlert className="size-5 text-status-down" aria-hidden />
          <h1 className="text-xl font-semibold tracking-tight">This screen crashed</h1>
        </div>
        <p className="mt-3 text-sm text-muted-foreground">
          The rest of the app is still running and the engine connection is intact.
        </p>
        <pre className="surface mt-4 overflow-x-auto p-4 text-xs text-muted-foreground">
          {error.message}
        </pre>
      </div>
    )
  }
}
