#!/usr/bin/env python3
"""Generate the complete Vue 3 frontend scaffold for face-attendance system."""
import os

BASE = os.path.dirname(os.path.abspath(__file__))

DIRS = ["src/api", "src/views", "src/components", "src/stores", "src/utils", "src/router"]
for d in DIRS:
    os.makedirs(os.path.join(BASE, d.replace("/", os.sep)), exist_ok=True)

FILES = {}

# ============================================================
# package.json
# ============================================================
FILES["package.json"] = '''\
{
  "name": "face-attendance-frontend",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "vue": "^3.5.13",
    "vue-router": "^4.5.0",
    "pinia": "^2.3.0",
    "element-plus": "^2.14.0",
    "echarts": "^5.5.1",
    "axios": "^1.7.9"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.2.1",
    "vite": "^6.0.5"
  }
}
'''

# ============================================================
# vite.config.js
# ============================================================
FILES["vite.config.js"] = '''\
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/uploads': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true }
    }
  }
})
'''

# ============================================================
# index.html
# ============================================================
FILES["index.html"] = '''\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>学生刷脸考勤系统</title>
</head>
<body>
  <div id="app"></div>
  <script type="module" src="/src/main.js"></script>
</body>
</html>
'''

# ============================================================
# src/main.js
# ============================================================
FILES["src/main.js"] = '''\
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus)
app.mount('#app')
'''

# ============================================================
# src/App.vue
# ============================================================
FILES["src/App.vue"] = '''\
<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="header-left">学生刷脸考勤系统</div>
      <div class="header-right">
        <el-badge :value="noticeStore.unreadCount" :hidden="noticeStore.unreadCount === 0">
          <el-button :icon="Bell" circle @click="router.push('/notices')" />
        </el-badge>
        <span class="datetime">{{ currentTime }}</span>
      </div>
    </el-header>
    <el-container>
      <el-aside width="200px" class="aside">
        <el-menu :default-active="route.path" router>
          <el-menu-item index="/students">学生管理</el-menu-item>
          <el-menu-item index="/faces">人脸录入</el-menu-item>
          <el-menu-item index="/stats">信息汇总</el-menu-item>
          <el-menu-item index="/audit">脸库审计</el-menu-item>
          <el-menu-item index="/notices">公告管理</el-menu-item>
        </el-menu>
      </el-aside>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Bell } from '@element-plus/icons-vue'
import { useNoticeStore } from './stores/notice'
import { connectWS } from './utils/websocket'

const route = useRoute()
const router = useRouter()
const noticeStore = useNoticeStore()
const currentTime = ref('')

let timer = null
function updateTime() {
  const d = new Date()
  currentTime.value = d.toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  updateTime()
  timer = setInterval(updateTime, 1000)
  connectWS()
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.layout { height: 100vh; }
.header {
  display: flex; align-items: center; justify-content: space-between;
  background: #409eff; color: #fff;
}
.header-left { font-size: 18px; font-weight: bold; }
.header-right { display: flex; align-items: center; gap: 16px; }
.datetime { font-size: 14px; }
.aside { background: #f5f7fa; }
.main { background: #fff; }
</style>
'''

# ============================================================
# src/router/index.js
# ============================================================
FILES["src/router/index.js"] = '''\
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/students' },
  { path: '/students', name: 'students', component: () => import('../views/StudentList.vue') },
  { path: '/faces', name: 'faces', component: () => import('../views/FaceEnroll.vue') },
  { path: '/stats', name: 'stats', component: () => import('../views/StatsDashboard.vue') },
  { path: '/audit', name: 'audit', component: () => import('../views/FaceAudit.vue') },
  { path: '/notices', name: 'notices', component: () => import('../views/NoticeBoard.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
'''

# ============================================================
# src/api/request.js
# ============================================================
FILES["src/api/request.js"] = '''\
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
    ElMessage.error(error.message || 'network error')
    return Promise.reject(error)
  }
)

export default request
'''

# ============================================================
# src/api/student.js
# ============================================================
FILES["src/api/student.js"] = '''\
import request from './request'

export function getStudents(params) {
  return request.get('/students', { params })
}
export function getStudent(id) {
  return request.get(`/students/${id}`)
}
export function createStudent(data) {
  return request.post('/students', data)
}
export function updateStudent(id, data) {
  return request.put(`/students/${id}`, data)
}
export function deleteStudent(id) {
  return request.delete(`/students/${id}`)
}
export function importStudents(file) {
  const form = new FormData()
  form.append('file', file)
  return request.post('/students/import', form)
}
export function getEnums() {
  return request.get('/enums')
}
'''

# ============================================================
# src/api/face.js
# ============================================================
FILES["src/api/face.js"] = '''\
import request from './request'

export function enrollFace(studentId, file) {
  const form = new FormData()
  form.append('file', file)
  return request.post(`/students/${studentId}/faces`, form)
}
export function getFaces(studentId) {
  return request.get(`/students/${studentId}/faces`)
}
export function deleteFace(faceId) {
  return request.delete(`/faces/${faceId}`)
}
'''

# ============================================================
# src/api/stats.js
# ============================================================
FILES["src/api/stats.js"] = '''\
import request from './request'

export function getSummary() {
  return request.get('/stats/summary')
}
export function getFaceCoverage() {
  return request.get('/stats/face-coverage')
}
export function getFaceCount() {
  return request.get('/stats/face-count')
}
'''

# ============================================================
# src/api/audit.js
# ============================================================
FILES["src/api/audit.js"] = '''\
import request from './request'

export function auditDuplicates() {
  return request.get('/audit/duplicates')
}
'''

# ============================================================
# src/api/notice.js
# ============================================================
FILES["src/api/notice.js"] = '''\
import request from './request'

export function getNotices(params) {
  return request.get('/notices', { params })
}
export function getNotice(id) {
  return request.get(`/notices/${id}`)
}
export function createNotice(data) {
  return request.post('/notices', data)
}
export function updateNotice(id, data) {
  return request.put(`/notices/${id}`, data)
}
export function deleteNotice(id) {
  return request.delete(`/notices/${id}`)
}
'''

# ============================================================
# src/stores/notice.js
# ============================================================
FILES["src/stores/notice.js"] = '''\
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
'''

# ============================================================
# src/utils/websocket.js
# ============================================================
FILES["src/utils/websocket.js"] = '''\
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
'''

# ============================================================
# src/components/CameraCapture.vue
# ============================================================
FILES["src/components/CameraCapture.vue"] = '''\
<template>
  <div class="camera">
    <video ref="video" autoplay playsinline></video>
    <canvas ref="canvas" style="display:none"></canvas>
    <div class="controls">
      <el-button type="primary" @click="capture" :disabled="!stream">拍照</el-button>
      <el-button @click="startCamera" :disabled="!!stream">打开摄像头</el-button>
      <el-button @click="stopCamera" :disabled="!stream">关闭</el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, onUnmounted } from 'vue'

const video = ref(null)
const canvas = ref(null)
const stream = ref(null)
const emit = defineEmits(['captured'])

async function startCamera() {
  try {
    stream.value = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: 640, height: 480 }
    })
    video.value.srcObject = stream.value
  } catch (e) {
    console.error('camera error:', e)
  }
}

function capture() {
  const v = video.value
  const c = canvas.value
  c.width = v.videoWidth
  c.height = v.videoHeight
  const ctx = c.getContext('2d')
  // mirror flip for front camera consistency
  ctx.translate(c.width, 0)
  ctx.scale(-1, 1)
  ctx.drawImage(v, 0, 0, c.width, c.height)
  c.toBlob(blob => {
    const file = new File([blob], 'capture.jpg', { type: 'image/jpeg' })
    emit('captured', file)
  }, 'image/jpeg', 0.9)
}

function stopCamera() {
  if (stream.value) {
    stream.value.getTracks().forEach(t => t.stop())
    stream.value = null
  }
}

onUnmounted(stopCamera)
</script>

<style scoped>
.camera { text-align: center; }
video { width: 100%; max-width: 480px; transform: scaleX(-1); border-radius: 8px; }
.controls { margin-top: 12px; display: flex; gap: 8px; justify-content: center; }
</style>
'''

# ============================================================
# src/views/StudentList.vue
# ============================================================
FILES["src/views/StudentList.vue"] = '''\
<template>
  <div>
    <div class="toolbar">
      <el-input v-model="keyword" placeholder="搜索姓名/学号" style="width:200px" clearable @clear="loadData" @keyup.enter="loadData" />
      <el-button @click="loadData">搜索</el-button>
      <el-button type="primary" @click="showAdd = true">新增学生</el-button>
      <el-upload :show-file-list="false" :before-upload="handleImport" accept=".csv">
        <el-button>批量导入</el-button>
      </el-upload>
    </div>
    <el-table :data="students" border>
      <el-table-column prop="student_no" label="学号" width="120" />
      <el-table-column prop="name" label="姓名" width="80" />
      <el-table-column prop="gender" label="性别" width="60" />
      <el-table-column prop="class_name" label="班级" width="150" />
      <el-table-column prop="major" label="专业" width="150" />
      <el-table-column prop="enroll_year" label="入学年份" width="80" />
      <el-table-column prop="phone" label="联系方式" width="120" />
      <el-table-column label="人脸状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.face_status ? 'success' : 'info'">{{ row.face_status ? '已录' : '未录' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button size="small" @click="editStudent(row)">编辑</el-button>
          <el-popconfirm title="删除该学生?" @confirm="handleDelete(row.id)">
            <template #reference><el-button size="small" type="danger">删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="prev, pager, next" @current-change="loadData" />

    <el-dialog v-model="showAdd" title="新增学生" width="500px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="学号"><el-input v-model="form.student_no" /></el-form-item>
        <el-form-item label="姓名"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="性别"><el-radio-group v-model="form.gender"><el-radio value="男">男</el-radio><el-radio value="女">女</el-radio></el-radio-group></el-form-item>
        <el-form-item label="班级"><el-select v-model="form.class_name" filterable><el-option v-for="c in enums.classes" :key="c" :label="c" :value="c" /></el-select></el-form-item>
        <el-form-item label="专业"><el-select v-model="form.major" filterable><el-option v-for="m in enums.majors" :key="m" :label="m" :value="m" /></el-select></el-form-item>
        <el-form-item label="入学年份"><el-input-number v-model="form.enroll_year" :min="2000" :max="2030" /></el-form-item>
        <el-form-item label="联系方式"><el-input v-model="form.phone" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAdd = false">取消</el-button>
        <el-button type="primary" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getStudents, createStudent, updateStudent, deleteStudent, importStudents, getEnums } from '../api/student'

const students = ref([])
const total = ref(0)
const page = ref(1)
const keyword = ref('')
const showAdd = ref(false)
const form = ref({ student_no: '', name: '', gender: '男', class_name: '', major: '', enroll_year: 2024, phone: '' })
const editingId = ref(null)
const enums = ref({ classes: [], majors: [] })

async function loadData() {
  const data = await getStudents({ page: page.value, page_size: 20, keyword: keyword.value })
  students.value = data.items
  total.value = data.total
}

async function loadEnums() {
  enums.value = await getEnums()
}

function editStudent(row) {
  form.value = { ...row }
  editingId.value = row.id
  showAdd.value = true
}

async function handleSave() {
  if (editingId.value) {
    await updateStudent(editingId.value, form.value)
    ElMessage.success('更新成功')
  } else {
    await createStudent(form.value)
    ElMessage.success('新增成功')
  }
  showAdd.value = false
  editingId.value = null
  form.value = { student_no: '', name: '', gender: '男', class_name: '', major: '', enroll_year: 2024, phone: '' }
  loadData()
}

async function handleDelete(id) {
  await deleteStudent(id)
  ElMessage.success('删除成功')
  loadData()
}

async function handleImport(file) {
  await importStudents(file)
  ElMessage.success('导入成功')
  loadData()
  return false
}

onMounted(() => { loadData(); loadEnums() })
</script>

<style scoped>
.toolbar { margin-bottom: 16px; display: flex; gap: 8px; }
</style>
'''

# ============================================================
# src/views/FaceEnroll.vue
# ============================================================
FILES["src/views/FaceEnroll.vue"] = '''\
<template>
  <div>
    <el-card>
      <template #header>选择学生</template>
      <el-select v-model="selectedStudent" filterable placeholder="搜索学号或姓名" :loading="loading" remote :remote-method="searchStudents" style="width:300px">
        <el-option v-for="s in studentOptions" :key="s.id" :label="`${s.student_no} - ${s.name}`" :value="s.id" />
      </el-select>
    </el-card>

    <el-card v-if="selectedStudent" style="margin-top:16px">
      <template #header>人脸采集</template>
      <div style="display:flex;gap:24px">
        <CameraCapture @captured="onCaptured" />
        <div v-if="faces.length" style="flex:1">
          <h4>已录入脸照</h4>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <div v-for="f in faces" :key="f.face_id" style="text-align:center">
              <img :src="`/uploads/faces/${getStudentNo(f)}/${f.image_path.split('/').pop()}`" style="width:120px;height:120px;object-fit:cover;border-radius:8px" />
              <div><el-button size="small" type="danger" @click="handleDeleteFace(f.face_id)">删除</el-button></div>
            </div>
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import CameraCapture from '../components/CameraCapture.vue'
import { getStudents } from '../api/student'
import { enrollFace, getFaces, deleteFace } from '../api/face'

const selectedStudent = ref(null)
const studentOptions = ref([])
const loading = ref(false)
const faces = ref([])
const studentMap = ref({})

function getStudentNo(face) {
  const s = studentMap.value[face.student_id]
  return s ? s.student_no : ''
}

async function searchStudents(query) {
  loading.value = true
  try {
    const data = await getStudents({ page: 1, page_size: 50, keyword: query })
    studentOptions.value = data.items
    data.items.forEach(s => { studentMap.value[s.id] = s })
  } finally {
    loading.value = false
  }
}

async function loadFaces() {
  if (!selectedStudent.value) return
  faces.value = await getFaces(selectedStudent.value)
}

async function onCaptured(file) {
  if (!selectedStudent.value) {
    ElMessage.warning('请先选择学生')
    return
  }
  const result = await enrollFace(selectedStudent.value, file)
  if (result.detected) {
    ElMessage.success('录入成功')
    loadFaces()
  } else {
    ElMessage.warning(result.reason || '未检测到人脸')
  }
}

async function handleDeleteFace(faceId) {
  await deleteFace(faceId)
  ElMessage.success('删除成功')
  loadFaces()
}

import { watch } from 'vue'
watch(selectedStudent, loadFaces)
</script>
'''

# ============================================================
# src/views/StatsDashboard.vue
# ============================================================
FILES["src/views/StatsDashboard.vue"] = '''\
<template>
  <div>
    <el-row :gutter="16" style="margin-bottom:16px">
      <el-col :span="8">
        <el-card><div class="metric"><span class="label">总人数</span><span class="value">{{ summary.total_students }}</span></div></el-card>
      </el-col>
      <el-col :span="8">
        <el-card><div class="metric"><span class="label">已录入</span><span class="value">{{ coverage.enrolled }}</span></div></el-card>
      </el-col>
      <el-col :span="8">
        <el-card><div class="metric"><span class="label">覆盖率</span><span class="value">{{ (coverage.coverage_rate * 100).toFixed(1) }}%</span></div></el-card>
      </el-col>
    </el-row>
    <el-row :gutter="16">
      <el-col :span="12"><el-card><div ref="classChart" style="height:300px"></div></el-card></el-col>
      <el-col :span="12"><el-card><div ref="genderChart" style="height:300px"></div></el-card></el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import { getSummary, getFaceCoverage, getFaceCount } from '../api/stats'

const summary = ref({ total_students: 0, by_class: {}, by_gender: {}, by_major: {} })
const coverage = ref({ enrolled: 0, not_enrolled: 0, coverage_rate: 0 })
const classChart = ref(null)
const genderChart = ref(null)
let chart1 = null, chart2 = null

async function loadData() {
  summary.value = await getSummary()
  coverage.value = await getFaceCoverage()
  renderCharts()
}

function renderCharts() {
  chart1 = echarts.init(classChart.value)
  chart1.setOption({
    title: { text: '班级分布', left: 'center' },
    tooltip: { trigger: 'item' },
    series: [{
      type: 'pie', radius: '60%',
      data: Object.entries(summary.value.by_class).map(([name, value]) => ({ name, value }))
    }]
  })
  chart2 = echarts.init(genderChart.value)
  chart2.setOption({
    title: { text: '性别分布', left: 'center' },
    tooltip: { trigger: 'item' },
    series: [{
      type: 'pie', radius: '60%',
      data: Object.entries(summary.value.by_gender).map(([name, value]) => ({ name, value }))
    }]
  })
}

onMounted(loadData)
onUnmounted(() => { chart1?.dispose(); chart2?.dispose() })
</script>

<style scoped>
.metric { text-align: center; }
.metric .label { display: block; color: #909399; font-size: 14px; }
.metric .value { display: block; font-size: 36px; font-weight: bold; color: #303133; }
</style>
'''

# ============================================================
# src/views/FaceAudit.vue
# ============================================================
FILES["src/views/FaceAudit.vue"] = '''\
<template>
  <div>
    <el-card>
      <template #header>
        <span>脸库审计 (ML聚类分析)</span>
        <el-button type="primary" @click="runAudit" :loading="loading" style="float:right">运行审计</el-button>
      </template>

      <el-alert v-if="!loading && !result" title="点击运行审计检查脸库质量" type="info" :closable="false" />
      <div v-if="result">
        <p>总向量数: {{ result.total_vectors }}</p>

        <h4 style="color:#e6a23c">疑似重复注册 (不同学号, 相似度 > 0.85)</h4>
        <el-table :data="result.duplicates" border size="small">
          <el-table-column prop="student_a.name" label="学生A" width="100" />
          <el-table-column prop="student_a.student_no" label="学号A" width="120" />
          <el-table-column prop="student_b.name" label="学生B" width="100" />
          <el-table-column prop="student_b.student_no" label="学号B" width="120" />
          <el-table-column prop="similarity" label="相似度" width="80">
            <template #default="{ row }">{{ (row.similarity * 100).toFixed(1) }}%</template>
          </el-table-column>
        </el-table>

        <h4 style="color:#f56c6c">脏数据 (同学号, 相似度 < 0.6)</h4>
        <el-table :data="result.dirty_data" border size="small">
          <el-table-column prop="student.name" label="学生" width="100" />
          <el-table-column prop="student.student_no" label="学号" width="120" />
          <el-table-column prop="face_a_id" label="脸照A" width="80" />
          <el-table-column prop="face_b_id" label="脸照B" width="80" />
          <el-table-column prop="similarity" label="相似度" width="80">
            <template #default="{ row }">{{ (row.similarity * 100).toFixed(1) }}%</template>
          </el-table-column>
          <el-table-column prop="issue" label="问题" />
        </el-table>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { auditDuplicates } from '../api/audit'

const loading = ref(false)
const result = ref(null)

async function runAudit() {
  loading.value = true
  try {
    result.value = await auditDuplicates()
    ElMessage.success('审计完成')
  } finally {
    loading.value = false
  }
}
</script>
'''

# ============================================================
# src/views/NoticeBoard.vue
# ============================================================
FILES["src/views/NoticeBoard.vue"] = '''\
<template>
  <div>
    <el-card>
      <template #header>
        <span>公告管理</span>
        <el-button type="primary" @click="showForm = true" style="float:right">发布公告</el-button>
      </template>
      <el-table :data="notices" border>
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="publisher" label="发布人" width="100" />
        <el-table-column prop="created_at" label="时间" width="180" />
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button size="small" @click="editNotice(row)">编辑</el-button>
            <el-popconfirm title="删除?" @confirm="handleDelete(row.id)">
              <template #reference><el-button size="small" type="danger">删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="showForm" :title="editingId ? '编辑公告' : '发布公告'" width="500px">
      <el-form :model="form" label-width="60px">
        <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="内容"><el-input v-model="form.content" type="textarea" :rows="4" /></el-form-item>
        <el-form-item label="发布人"><el-input v-model="form.publisher" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useNoticeStore } from '../stores/notice'
import { getNotices, createNotice, updateNotice, deleteNotice } from '../api/notice'

const noticeStore = useNoticeStore()
const notices = ref([])
const showForm = ref(false)
const form = ref({ title: '', content: '', publisher: '' })
const editingId = ref(null)

async function loadData() {
  const data = await getNotices({ page: 1, page_size: 50 })
  notices.value = data.items
  noticeStore.markRead()
}

function editNotice(row) {
  form.value = { title: row.title, content: row.content, publisher: row.publisher }
  editingId.value = row.id
  showForm.value = true
}

async function handleSave() {
  if (editingId.value) {
    await updateNotice(editingId.value, form.value)
  } else {
    await createNotice(form.value)
  }
  ElMessage.success('保存成功')
  showForm.value = false
  editingId.value = null
  form.value = { title: '', content: '', publisher: '' }
  loadData()
}

async function handleDelete(id) {
  await deleteNotice(id)
  ElMessage.success('删除成功')
  loadData()
}

onMounted(loadData)
</script>
'''

# ---- write all files ----
for rel_path, content in FILES.items():
    full_path = os.path.join(BASE, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  wrote {rel_path}")

print(f"\nDone! {len(FILES)} files generated.")
