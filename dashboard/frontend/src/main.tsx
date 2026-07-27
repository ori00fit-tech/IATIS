import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { installConsoleCapture } from './lib/consoleCapture'

// Installed before the first render so a module crash's own console.error
// call (ErrorBoundary.tsx) is captured from the very first paint, even
// before the BottomPanel's Console tab is ever opened.
installConsoleCapture()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
