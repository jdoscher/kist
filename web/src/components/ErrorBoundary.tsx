// Catches render errors anywhere in the app: shows a readable page instead
// of a blank one, and reports the crash (reportError.ts).

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { reportClientError, toReport } from '../reportError'

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    reportClientError(toReport('render', error, info.componentStack ?? undefined))
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="crash" role="alert">
        <h1>Something broke</h1>
        <p>
          Kist hit an error and stopped drawing this page. It has been reported. Reload to try
          again, or open the library without the item that was showing (if one item's data caused
          this, reloading the same address would show it again).
        </p>
        <pre className="crash-detail">{error.message || String(error)}</pre>
        <div className="crash-actions">
          <button className="primary" onClick={() => window.location.reload()}>
            Reload
          </button>
          <button onClick={() => window.location.assign('/')}>Open library</button>
        </div>
      </div>
    )
  }
}
