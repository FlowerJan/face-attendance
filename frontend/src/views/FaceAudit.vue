<template>
  <div>
    <el-card>
      <template #header>
        <span>脸库审计 (ML 聚类分析)</span>
        <el-button type="primary" @click="runAudit" :loading="loading" style="float:right">运行审计</el-button>
      </template>

      <el-alert v-if="!loading && !result" title="点击运行审计，检查脸库质量" type="info" :closable="false" />

      <div v-if="result">
        <el-alert v-if="result.note" :title="result.note" type="warning" :closable="false" style="margin-bottom:12px" />

        <el-descriptions :column="4" border size="small" style="margin-bottom:16px">
          <el-descriptions-item label="向量后端">{{ result.backend }}</el-descriptions-item>
          <el-descriptions-item label="向量总数">{{ result.face_count }}</el-descriptions-item>
          <el-descriptions-item label="聚类簇数">{{ summary.cluster_count }}</el-descriptions-item>
          <el-descriptions-item label="涉及学生">{{ summary.students_involved }}</el-descriptions-item>
          <el-descriptions-item label="重复注册对">
            <el-tag :type="summary.duplicate_pairs ? 'warning' : 'success'">{{ summary.duplicate_pairs }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="脏数据对">
            <el-tag :type="summary.dirty_pairs ? 'danger' : 'success'">{{ summary.dirty_pairs }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="可疑跨人簇">
            <el-tag :type="summary.suspicious_cluster_count ? 'danger' : 'success'">{{ summary.suspicious_cluster_count }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="阈值">
            同人 &lt; {{ result.thresholds?.same_student }} / 跨人 &gt; {{ result.thresholds?.cross_student }}
          </el-descriptions-item>
        </el-descriptions>

        <h4 style="color:#e6a23c">疑似重复注册 (不同学号, 相似度 &gt; 阈值)</h4>
        <el-table v-if="result.duplicates?.length" :data="result.duplicates" border size="small" style="margin-bottom:16px">
          <el-table-column prop="student_a.name" label="学生A" width="110" />
          <el-table-column prop="student_a.student_no" label="学号A" width="130" />
          <el-table-column prop="student_b.name" label="学生B" width="110" />
          <el-table-column prop="student_b.student_no" label="学号B" width="130" />
          <el-table-column label="相似度" width="100">
            <template #default="{ row }">{{ (row.similarity * 100).toFixed(1) }}%</template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="未发现重复注册" :image-size="60" />

        <h4 style="color:#f56c6c">脏数据 (同一学号内, 相似度 &lt; 阈值)</h4>
        <el-table v-if="result.dirty_data?.length" :data="result.dirty_data" border size="small" style="margin-bottom:16px">
          <el-table-column prop="student.name" label="学生" width="110" />
          <el-table-column prop="student.student_no" label="学号" width="130" />
          <el-table-column prop="face_a_id" label="脸照A" width="90" />
          <el-table-column prop="face_b_id" label="脸照B" width="90" />
          <el-table-column label="相似度" width="100">
            <template #default="{ row }">{{ (row.similarity * 100).toFixed(1) }}%</template>
          </el-table-column>
          <el-table-column prop="issue" label="问题" />
        </el-table>
        <el-empty v-else description="未发现脏数据" :image-size="60" />

        <h4 style="color:#f56c6c">可疑聚类簇 (同一簇内含多个学号)</h4>
        <el-table v-if="result.suspicious_clusters?.length" :data="result.suspicious_clusters" border size="small" style="margin-bottom:16px">
          <el-table-column prop="cluster_id" label="簇ID" width="80" />
          <el-table-column prop="size" label="照片数" width="90" />
          <el-table-column label="涉及学生">
            <template #default="{ row }">
              <el-tag v-for="s in row.students" :key="s.id" style="margin-right:6px">
                {{ s.name }} ({{ s.student_no }})
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="脸照ID">
            <template #default="{ row }">{{ row.face_ids.join(', ') }}</template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="未发现跨学号聚集" :image-size="60" />

        <el-collapse style="margin-top:12px">
          <el-collapse-item :title="`全部聚类簇 (${result.clusters?.length || 0})`">
            <el-table :data="result.clusters" border size="small">
              <el-table-column prop="cluster_id" label="簇ID" width="80" />
              <el-table-column prop="size" label="照片数" width="90" />
              <el-table-column label="学号" width="140">
                <template #default="{ row }">{{ row.student_ids.join(', ') }}</template>
              </el-table-column>
              <el-table-column label="脸照ID">
                <template #default="{ row }">{{ row.face_ids.join(', ') }}</template>
              </el-table-column>
            </el-table>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { auditDuplicates } from '../api/audit'

const loading = ref(false)
const result = ref(null)
const summary = computed(() => result.value?.summary || {})

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
