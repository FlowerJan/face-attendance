import { useNoticeStore } from '../stores/notice'

let ws = null
let reconnectTimer = null

export function connectWS() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${protocol}//${location.host}/ws/notices`
  ws = new WebSocket(url)

  ws.onopen = () => {
    console.log('[WS] connected')
  }

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data)
    if (msg.type === 'notice') {
      const store = useNoticeStore()
      store.addNotice(msg.data)
    }
  }

  ws.onclose = () => {
    console.log('[WS] disconnected, reconnecting in 3s...')
    reconnectTimer = setTimeout(() => {
      connectWS()
      // pull missed notices via HTTP after reconnect
      const store = useNoticeStore()
      store.fetchNotices()
    }, 3000)
  }

  ws.onerror = () => {
    ws.close()
  }
}

export function disconnectWS() {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (ws) ws.close()
}
