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
