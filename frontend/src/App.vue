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
