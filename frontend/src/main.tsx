import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { AuthProvider } from './context/AuthContext'
import { LayoutModeProvider } from './context/LayoutModeContext'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider>
      <LayoutModeProvider>
        <App />
      </LayoutModeProvider>
    </AuthProvider>
  </React.StrictMode>,
)
