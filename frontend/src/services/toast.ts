export type ToastType = 'success' | 'error' | 'info'

export interface ToastMessage {
  id: number
  type: ToastType
  text: string
  duration?: number
}

let toasts: ToastMessage[] = []
let nextId = 1

export function getToasts() {
  return toasts
}

export function showToast(type: ToastType, text: string, duration = 4000) {
  const id = nextId++
  toasts.push({ id, type, text, duration })
  if (duration > 0) setTimeout(() => removeToast(id), duration)
}

export function removeToast(id: number) {
  const idx = toasts.findIndex((t) => t.id === id)
  if (idx !== -1) toasts.splice(idx, 1)
}
