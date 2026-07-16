import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  /** Human label of the module being guarded — shown in the fallback. */
  moduleName: string
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * Per-module guard. A command center is only as trustworthy as its worst
 * panel — before this, a single render-time throw in any of the 15 modules
 * white-screened the whole console (and with it every other live readout an
 * operator might need in that moment). This isolates the blast radius: the
 * failing tab shows a recoverable fallback, everything else keeps polling.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surfaces in the browser console / Live Logs pipeline for triage.
    console.error(`[${this.props.moduleName}] render failure:`, error, info.componentStack)
  }

  reset = () => this.setState({ error: null })

  render() {
    if (this.state.error) {
      return (
        <div className="bg-card border border-red/40 rounded-[10px] p-8 text-center flex flex-col items-center gap-3">
          <div className="text-red text-[1.4em]">⚠</div>
          <div className="text-[0.95em] font-bold text-red">{this.props.moduleName} failed to render</div>
          <div className="text-muted text-[0.8em] max-w-[560px] break-words font-mono">
            {this.state.error.message}
          </div>
          <p className="text-muted text-[0.78em] max-w-[520px]">
            This panel crashed but the rest of the command center is unaffected. Retry, or switch tabs and come back.
          </p>
          <button
            onClick={this.reset}
            className="mt-1 px-4 py-1.5 text-[0.8em] rounded border border-accent/50 text-accent hover:bg-accent/10"
          >
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
