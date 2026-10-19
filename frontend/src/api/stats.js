import request from './request'

export function getSummary() {
  return request.get('/stats/summary')
}
export function getFaceCoverage() {
  return request.get('/stats/face-coverage')
}
export function getFaceCount() {
  return request.get('/stats/face-count')
}
