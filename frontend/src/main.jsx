import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Landing from './Landing.jsx'
import Project from './Project.jsx'
import { applyTheme, getStoredTheme } from './ui/theme.js'
import './styles.css'

// Apply the saved theme before first paint to avoid a flash.
applyTheme(getStoredTheme())

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/project/:pid" element={<Project />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
)
