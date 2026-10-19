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
    <el-row :gutter="16" style="margin-bottom:16px">
      <el-col :span="6">
        <el-card><div class="metric"><span class="label">未录入脸照</span><span class="value warn">{{ faceCount.zero_face }}</span></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card><div class="metric"><span class="label">仅 1 张脸照</span><span class="value">{{ faceCount.one_face }}</span></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card><div class="metric"><span class="label">多张脸照</span><span class="value">{{ faceCount.multiple_faces }}</span></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card><div class="metric"><span class="label">孤儿脸照</span><span class="value ok">{{ faceCount.orphan_faces }}</span></div></el-card>
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
const faceCount = ref({ zero_face: 0, one_face: 0, multiple_faces: 0, orphan_faces: 0 })
const classChart = ref(null)
const genderChart = ref(null)
let chart1 = null, chart2 = null

async function loadData() {
  const [s, c, f] = await Promise.all([getSummary(), getFaceCoverage(), getFaceCount()])
  summary.value = s
  coverage.value = c
  faceCount.value = f
  renderCharts()
}

function renderCharts() {
  chart1?.dispose()
  chart2?.dispose()
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

function onResize() {
  chart1?.resize()
  chart2?.resize()
}

onMounted(() => {
  loadData()
  window.addEventListener('resize', onResize)
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  chart1?.dispose()
  chart2?.dispose()
})
</script>

<style scoped>
.metric { text-align: center; }
.metric .label { display: block; color: #909399; font-size: 14px; }
.metric .value { display: block; font-size: 36px; font-weight: bold; color: #303133; }
.metric .value.warn { color: #e6a23c; }
.metric .value.ok { color: #67c23a; }
</style>
