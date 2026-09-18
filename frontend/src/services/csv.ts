export function toCsv(rows: string[][]): string {
  return '﻿' + rows
    .map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
    .join('\r\n')
}

export function downloadFile(name: string, content: string, type = 'text/csv;charset=utf-8') {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 300)
}

export function downloadCsv(name: string, rows: string[][]) {
  downloadFile(name, toCsv(rows))
}

export function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false

  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    const n = text[i + 1]

    if (c === '"' && quoted && n === '"') {
      cell += '"'
      i++
    } else if (c === '"') {
      quoted = !quoted
    } else if (c === ',' && !quoted) {
      row.push(cell.trim())
      cell = ''
    } else if ((c === '\n' || c === '\r') && !quoted) {
      if (c === '\r' && n === '\n') i++
      row.push(cell.trim())
      if (row.some(Boolean)) rows.push(row)
      row = []
      cell = ''
    } else {
      cell += c
    }
  }

  row.push(cell.trim())
  if (row.some(Boolean)) rows.push(row)
  return rows
}

/**
 * 学号 / 姓名 / 年级 / 班级 / 性别 / 年龄 —— 后两项选填。
 *
 * 注意：本函数目前**没有任何调用方**。真正的学生导入走 `useDataImport`，
 * 把原始文件交给后端预览接口，校验全在服务端（见该 composable 的说明）。
 * 这里保留完整的列，是为了万一有人接上它时不会静默丢掉性别与年龄。
 */
export function parseStudentCsv(
  text: string
): Array<{
  student_no: string
  name: string
  grade: string
  class_name: string
  gender: string
  age: string
}> {
  const rows = parseCsv(text)
  const headers = rows.shift()!.map(h => h.trim())
  return rows.map(r => ({
    student_no: r[headers.indexOf('学号')] || '',
    name: r[headers.indexOf('姓名')] || '',
    grade: r[headers.indexOf('年级')] || '',
    class_name: r[headers.indexOf('班级')] || '',
    // 原样透传，不在这里翻译：后端接受 男/女 与 MALE/FEMALE 两种写法，
    // 年龄也认 `13` 与 `13岁`。
    gender: r[headers.indexOf('性别')] || '',
    age: r[headers.indexOf('年龄')] || ''
  }))
}

export function parseQuestionCsv(text: string): Array<{ no: number; text: string; dimension: string; is_validity: boolean; is_key: boolean }> {
  const rows = parseCsv(text)
  const headers = rows.shift()!.map(h => h.trim())
  return rows.map(r => ({
    no: Number(r[headers.indexOf('题号')]) || 0,
    text: r[headers.indexOf('题目文本')] || '',
    dimension: r[headers.indexOf('维度')] || '',
    is_validity: ['是', 'true', '1'].includes(r[headers.indexOf('是否效度题')]?.trim().toLowerCase()),
    is_key: ['是', 'true', '1'].includes(r[headers.indexOf('是否重点题')]?.trim().toLowerCase())
  }))
}
