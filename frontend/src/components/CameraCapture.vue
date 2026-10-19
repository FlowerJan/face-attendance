<template>
  <div class="camera">
    <video ref="video" autoplay playsinline></video>
    <canvas ref="canvas" style="display:none"></canvas>
    <div class="controls">
      <el-button type="primary" @click="capture" :disabled="!stream">拍照</el-button>
      <el-button @click="startCamera" :disabled="!!stream">打开摄像头</el-button>
      <el-button @click="stopCamera" :disabled="!stream">关闭</el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, onUnmounted } from 'vue'

const video = ref(null)
const canvas = ref(null)
const stream = ref(null)
const emit = defineEmits(['captured'])

async function startCamera() {
  try {
    stream.value = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: 640, height: 480 }
    })
    video.value.srcObject = stream.value
  } catch (e) {
    console.error('camera error:', e)
  }
}

function capture() {
  const v = video.value
  const c = canvas.value
  c.width = v.videoWidth
  c.height = v.videoHeight
  const ctx = c.getContext('2d')
  // mirror flip for front camera consistency
  ctx.translate(c.width, 0)
  ctx.scale(-1, 1)
  ctx.drawImage(v, 0, 0, c.width, c.height)
  c.toBlob(blob => {
    const file = new File([blob], 'capture.jpg', { type: 'image/jpeg' })
    emit('captured', file)
  }, 'image/jpeg', 0.9)
}

function stopCamera() {
  if (stream.value) {
    stream.value.getTracks().forEach(t => t.stop())
    stream.value = null
  }
}

onUnmounted(stopCamera)
</script>

<style scoped>
.camera { text-align: center; }
video { width: 100%; max-width: 480px; transform: scaleX(-1); border-radius: 8px; }
.controls { margin-top: 12px; display: flex; gap: 8px; justify-content: center; }
</style>
