import { createRoot } from 'react-dom/client'
import './index.css'
import './rtl-agent.css'
import App from './App.jsx'
import { TooltipProvider } from './components/ui/tooltip.jsx'
import { initObservability } from './lib/observability'

initObservability()

const storedTheme = window.localStorage.getItem('theme')
const storedPalette = window.localStorage.getItem('chipverify.ui.palette')
const paletteForcesLight = storedPalette === 'light' || storedPalette === 'solarized-light'
const isDarkTheme = paletteForcesLight ? false : (storedTheme ? storedTheme === 'dark' : true)
document.documentElement.classList.toggle('dark', isDarkTheme)
document.body.classList.toggle('dark-theme', isDarkTheme)

if (storedPalette && storedPalette !== 'default') {
  document.documentElement.setAttribute('data-theme', storedPalette)
  document.body.setAttribute('data-theme', storedPalette)
} else {
  document.documentElement.removeAttribute('data-theme')
  document.body.removeAttribute('data-theme')
}

createRoot(document.getElementById('root')).render(
  <TooltipProvider>
    <App />
  </TooltipProvider>
)



