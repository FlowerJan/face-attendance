import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ElNotification } from 'element-plus'
import { getNotices } from '../api/notice'

export const useNoticeStore = defineStore('notice', () => {
  const unreadCount = ref(0)
  const notices = ref([])

  function addNotice(notice) {
    notices.value.unshift(notice)
    unreadCount.value++
    ElNotification({
      title: notice.title,
      message: notice.content || '',
      type: 'info',
      duration: 5000,
    })
  }

  async function fetchNotices() {
    try {
      const data = await getNotices({ page: 1, page_size: 50 })
      notices.value = data.items || []
    } catch (e) { /* ignore */ }
  }

  function markRead() {
    unreadCount.value = 0
  }

  return { unreadCount, notices, addNotice, fetchNotices, markRead }
})
