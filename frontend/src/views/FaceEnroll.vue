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

    <el-card v-if="quality" style="margin-top:16px">
      <template #header>画质诊断</template>
      <el-alert
        :type="quality.passed ? 'success' : 'error'"
        :closable="false"
        :title="quality.passed ? '本次采集通过画质检查' : (quality.reason || '未通过画质检查')"
        :description="quality.metrics"
        show-icon
      />
      <el-descriptions v-if="quality.detail" :column="4" size="small" border style="margin-top:12px">
        <el-descriptions-item label="人脸尺寸">{{ quality.detail.face_width }}×{{ quality.detail.face_height }} px</el-descriptions-item>
        <el-descriptions-item label="亮度">{{ quality.detail.brightness }}</el-descriptions-item>
        <el-descriptions-item label="清晰度">{{ quality.detail.sharpness }}</el-descriptions-item>
        <el-descriptions-item label="倾角">{{ quality.detail.roll_angle }}°</el-descriptions-item>
        <el-descriptions-item label="鼻偏">{{ quality.detail.nose_offset }} px</el-descriptions-item>
        <el-descriptions-item label="检测置信度">{{ quality.detail.det_score }}</el-descriptions-item>
        <el-descriptions-item label="过曝占比">{{ (quality.detail.saturation * 100).toFixed(1) }}%</el-descriptions-item>
        <el-descriptions-item label="触发规则">{{ quality.rule || '—' }}</el-descriptions-item>
        <el-descriptions-item label="画面人脸数">{{ quality.detail.face_count ?? 1 }}</el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CameraCapture from '../components/CameraCapture.vue'
import { getStudents } from '../api/student'
import { enrollFace, getFaces, deleteFace } from '../api/face'

const selectedStudent = ref(null)
const studentOptions = ref([])
const loading = ref(false)
const faces = ref([])
const studentMap = ref({})
const quality = ref(null)

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
  if (!result.detected) {
    quality.value = null
    ElMessage.warning(result.reason || '未检测到人脸')
    return
  }
  // 后端质量门控拒绝：detected=true 但 enrolled=false
  if (result.enrolled === false) {
    quality.value = {
      passed: false,
      reason: result.reason,
      rule: result.rule,
      metrics: result.quality?.metrics || '',
      detail: result.quality,
    }
    ElMessage.warning(result.reason || '未通过画质检查')
    return
  }
  quality.value = {
    passed: true,
    reason: '',
    rule: '',
    metrics: result.quality?.metrics || '',
    detail: result.quality,
  }
  ElMessage.success('录入成功')
  if (result.duplicate_suspect && result.similar_existing?.length) {
    const top = result.similar_existing[0]
    ElMessageBox.alert(
      `库中已存在高度相似的人脸：${top.name}（${top.student_no}），相似度 ${(top.similarity * 100).toFixed(1)}%。` +
      `\n疑似同一人重复注册，请核实。`,
      '重复注册提醒',
      { type: 'warning', confirmButtonText: '知道了' }
    )
  }
  loadFaces()
}

async function handleDeleteFace(faceId) {
  await deleteFace(faceId)
  ElMessage.success('删除成功')
  loadFaces()
}

watch(selectedStudent, () => {
  // 切换学生时清空上一位的画质诊断，避免面板与当前学生不对应
  quality.value = null
  loadFaces()
})

</script>
