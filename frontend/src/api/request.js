import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

request.interceptors.response.use(
  response => {
    const res = response.data
    if (res.code !== 0) {
      ElMessage.error(res.message || 'error')
      return Promise.reject(res)
    }
    return res.data
  },
  error => {
    // 后端已统一为 {code, message, data}，优先展示 message
    const msg = error.response?.data?.message || error.message || 'network error'
    ElMessage.error(msg)
    return Promise.reject(error.response?.data || error)
  }
)

export default request
