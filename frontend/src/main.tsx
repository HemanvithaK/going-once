import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { session } from './api/session'
import App from './App.tsx'
import { AuthProvider } from './auth/AuthProvider'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider session={session}>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
