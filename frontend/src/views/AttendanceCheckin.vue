<template>
  <div>
    <el-card>
      <template #header>人脸签到</template>
      <div style="display:flex;gap:24px;flex-wrap:wrap">
        <CameraCapture @captured="onCaptured" />
        <div style="flex:1;min-width:280px">
          <el-alert
            v-if="result"
            :type="resultType"
            :closable="false"
            :title="resultTitle"
            :description="resultDesc"
            show-icon
          />
          <el-empty v-else description="拍照或上传照片后自动识别签到" />
        </div>
      </div>
    </el-card>

    <el-card style="margin-top:16px">
      <template #header>
        <div style="display:flex;align-items:center;justify-content:space-between">
          <span>当日签到记录</span>
          <div style="display:flex;gap:8px;align-items:center">
            <el-date-picker v-model="dateStr" type="date" value-format="YYYY-MM-DD" :clearable="false" style="width:150px" @change="loadRecords" />
            <el-tag>{{ summary.checked_count }} / {{ summary.total_students }} 人（{{ (summary.rate * 100).toFixed(1) }}%）</el-tag>
          </div>
        </div>
      </template>
      <el-table :data="records" border stripe>
        <el-table-column prop="student_no" label="学号" width="130" />
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="class_name" label="班级" width="140" />
        <el-table-column prop="checkin_at" label="签到时间" min-width="170" />
        <el-table-column prop="similarity" label="相似度" width="100" />
      </el-table>
      <el-pagination
        v-if="total > pageSize"
        style="margin-top:12px;justify-content:flex-end"
        layout="prev, pager, next"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        @current-change="p => { page = p; loadRecords() }"
      />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import CameraCapture from '../components/CameraCapture.vue'
import { checkin, daySummary, listRecords } from '../api/attendance'

const result = ref(null)
const submitting = ref(false)
const dateStr = ref(new Date().toISOString().slice(0, 10))
const records = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const summary = ref({ checked_count: 0, total_students: 0, rate: 0 })

const resultType = computed(() => {
  if (!result.value) return 'info'
  if (!result.value.recognized) return 'warning'
  return result.value.duplicate ? 'info' : 'success'
})
const resultTitle = computed(() => {
  const r = result.value
  if (!r) return ''
  if (!r.recognized) return r.reason === 'no face detected' ? '未检测到人脸' : '识别失败'
  if (r.duplicate) return `${r.record.name} 今日已签到（无需重复签到）`
  return `签到成功：${r.record.name}`
})
const resultDesc = computed(() => {
  const r = result.value
  if (!r) return ''
  if (!r.recognized) {
    if (r.best_similarity !== undefined) {
      return `最高相似度 ${r.best_similarity}，未达阈值 ${r.threshold}`
    }
    return r.rule ? `未通过质量门控（${r.rule}）：${r.reason}` : (r.reason || '')
  }
  return `相似度 ${r.record.similarity}，签到时间 ${r.record.checkin_at}`
})

async function onCaptured(file) {
  if (submitting.value) return
  submitting.value = true
  try {
    result.value = await checkin(file)
    loadRecords()
  } finally {
    submitting.value = false
  }
}

async function loadRecords() {
  const data = await listRecords({ date: dateStr.value, page: page.value, page_size: pageSize })
  records.value = data.items
  total.value = data.total
  summary.value = await daySummary({ date: dateStr.value })
}

onMounted(loadRecords)
</script>
