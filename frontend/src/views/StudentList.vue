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
