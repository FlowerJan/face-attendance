import request from './request'

export function auditDuplicates() {
  return request.get('/audit/duplicates')
}
