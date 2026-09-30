import React from 'react'
import { createRoot } from 'react-dom/client'
import { ConfigProvider } from 'antd'
import App from './App'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      button={{ autoInsertSpace: false }}
      theme={{
        token: {
          colorPrimary: '#315EB5',
          colorText: '#17243B',
          colorTextSecondary: '#5B6A7D',
          colorBorder: '#DCE3EB',
          borderRadius: 8,
          fontFamily: '-apple-system, BlinkMacSystemFont, "Noto Sans SC", sans-serif'
        }
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>
)
