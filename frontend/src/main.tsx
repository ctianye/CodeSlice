import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { AppProvider } from './state/AppState'

import './styles/base.css'
import './styles/layout.css'
import './styles/welcome.css'
import './styles/sidebar.css'
import './styles/editor.css'
import './styles/doc.css'
import './styles/tabs.css'
import './styles/chat.css'
import './styles/note.css'
/* vis-network ships its own tooltip/navigation CSS; the graph view renders no
 * usable canvas chrome without it. */
import 'vis-network/styles/vis-network.min.css'
import './styles/graph.css'
import './styles/feedback.css'
import './styles/settings.css'

const container = document.getElementById('root')
if (!container) throw new Error('未找到 #root 挂载点')

createRoot(container).render(
  <StrictMode>
    <AppProvider>
      <App />
    </AppProvider>
  </StrictMode>,
)
