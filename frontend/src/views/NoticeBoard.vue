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
import { ref, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useNoticeStore } from '../stores/notice'
import { getNotices, createNotice, updateNotice, deleteNotice } from '../api/notice'

const noticeStore = useNoticeStore()
const notices = ref([])
const showForm = ref(false)
const form = ref({ title: '', content: '', publisher: '' })
const editingId = ref(null)

// WS 推送到达时 store.notices 会更新，表格随之刷新（无需手动重拉）
watch(() => noticeStore.notices, (list) => {
  if (list?.length) notices.value = list
}, { deep: true })

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
