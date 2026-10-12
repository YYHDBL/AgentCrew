import React, { useSyncExternalStore } from 'react'
import { createRoot } from 'react-dom/client'
import { ConfigProvider } from 'antd'
import App from './App'
import './styles.css'

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
const subscribeMotion = (changed: () => void): (() => void) => {
  reducedMotion.addEventListener('change', changed)
  return () => reducedMotion.removeEventListener('change', changed)
}

function Application(): React.ReactElement {
  const reduceMotion = useSyncExternalStore(subscribeMotion, () => reducedMotion.matches)
  return (
    <ConfigProvider
      button={{ autoInsertSpace: false }}
      theme={{
        token: {
          motion: !reduceMotion,
          colorPrimary: '#356FEA',
          colorText: '#252936',
          colorTextSecondary: '#626A78',
          colorBorder: '#E7EAF0',
          borderRadius: 8,
          controlHeight: 34,
          fontSize: 14,
          fontFamily: '-apple-system, BlinkMacSystemFont, "Noto Sans SC", sans-serif'
        }
      }}
    >
      <App />
    </ConfigProvider>
  )
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Application />
  </React.StrictMode>
)
