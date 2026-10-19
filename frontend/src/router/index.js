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
