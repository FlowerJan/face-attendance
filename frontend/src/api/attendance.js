import request from './request'

export function checkin(file) {
  const form = new FormData()
  form.append('file', file)
  return request.post('/attendance/checkin', form)
}

export function listRecords(params) {
  return request.get('/attendance/records', { params })
}

export function daySummary(params) {
  return request.get('/attendance/summary', { params })
}
