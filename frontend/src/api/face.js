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
