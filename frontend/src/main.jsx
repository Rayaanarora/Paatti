import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import PhoneApp from './PhoneApp.jsx'
import './styles.css'

// /?phone           -> mobile call screen (new call)
// /?phone=<session> -> mobile call screen joining a call created from the dashboard QR
const params = new URLSearchParams(window.location.search)
const isPhone = params.has('phone')
if (isPhone) document.documentElement.classList.add('phone-mode')

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {isPhone ? <PhoneApp sessionId={params.get('phone') || null} /> : <App />}
  </React.StrictMode>,
)
