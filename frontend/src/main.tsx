import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { applyTheme, getTheme } from './theme.ts'

// Applied before the first render, not inside a component effect - doing
// it in an effect would paint one dark frame first and then flash to
// light for anyone who'd chosen the light theme.
applyTheme(getTheme())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
