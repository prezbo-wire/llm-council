import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

async function loadAppProps() {
  // Dev-only fixture replay: `?replay=<fixture name>` runs the real UI against an in-memory
  // backend that replays src/dev/fixtures/*.json. `import.meta.env.DEV` is false in production
  // builds, so this branch and the replay chunk are dropped from `vite build`.
  if (import.meta.env.DEV) {
    const params = new URLSearchParams(window.location.search)
    if (params.has('replay')) {
      const { createReplaySetup } = await import('./dev/replay.js')
      return createReplaySetup(params)
    }
  }
  return {}
}

loadAppProps().then((props) => {
  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <App {...props} />
    </StrictMode>,
  )
})
