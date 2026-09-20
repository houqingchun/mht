import type { RouteRecordRaw } from 'vue-router'
import LoginPage from '../features/auth/LoginPage.vue'
import AppLayout from './AppLayout.vue'
import StudentHomePage from '../features/student/StudentHomePage.vue'
import StudentAssessmentPage from '../features/student/StudentAssessmentPage.vue'
import CounselorWorkbenchPage from '../features/care/CounselorWorkbenchPage.vue'
import CasesPage from '../features/care/CasesPage.vue'
import CareCaseDetailPage from '../features/care/CareCaseDetailPage.vue'
import LeaderOverviewPage from '../features/leader/LeaderOverviewPage.vue'
import ProgressPage from '../features/leader/ProgressPage.vue'
import AdminSystemPage from '../features/admin/AdminSystemPage.vue'
import OrganizationPage from '../features/admin/OrganizationPage.vue'
import ScalePage from '../features/admin/ScalePage.vue'
import DataCenterPage from '../features/admin/DataCenterPage.vue'
import SettingsPage from '../features/admin/SettingsPage.vue'
import TasksPage from '../features/admin/TasksPage.vue'
import ExportCenterPage from '../features/admin/ExportCenterPage.vue'
import AuditPage from '../features/admin/AuditPage.vue'
import AnalyticsPage from '../features/analytics/AnalyticsPage.vue'
import StudentHistoryPage from '../features/student/StudentHistoryPage.vue'

export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/login' },
  { path: '/login', component: LoginPage },
  {
    path: '/',
    component: AppLayout,
    children: [
      // Student
      { path: 'student/home', component: StudentHomePage, meta: { role: 'student', title: '我的测评' } },
      { path: 'student/assessment/:taskId', component: StudentAssessmentPage, meta: { role: 'student', title: '在线答题' } },
      { path: 'student/history', component: StudentHistoryPage, meta: { role: 'student', title: '完成记录' } },

      // Counselor
      { path: 'counselor/workbench', component: CounselorWorkbenchPage, meta: { role: 'counselor', title: '工作台' } },
      { path: 'counselor/cases', component: CasesPage, meta: { role: 'counselor', title: '重点学生' } },
      { path: 'counselor/cases/:studentId', component: CareCaseDetailPage, meta: { role: 'counselor', title: '学生档案' } },
      { path: 'counselor/data', component: DataCenterPage, meta: { role: 'counselor', title: '数据中心' } },
      { path: 'counselor/analytics', component: AnalyticsPage, meta: { role: 'counselor', title: '统计分析' } },
      { path: 'counselor/audit', component: AuditPage, meta: { role: 'counselor', title: '审计日志' } },
      { path: 'counselor/exports', component: ExportCenterPage, meta: { role: 'counselor', title: '导出中心' } },
      { path: 'counselor/tasks', component: TasksPage, meta: { role: 'counselor', title: '测评任务' } },

      // Leader
      { path: 'leader/overview', component: LeaderOverviewPage, meta: { role: 'leader', title: '领导总览' } },
      { path: 'leader/progress', component: ProgressPage, meta: { role: 'leader', title: '重点进展' } },
      { path: 'leader/analytics', component: AnalyticsPage, meta: { role: 'leader', title: '学校统计' } },
      { path: 'leader/tasks', component: TasksPage, meta: { role: 'leader', title: '测评任务' } },
      { path: 'leader/audit', component: AuditPage, meta: { role: 'leader', title: '审计日志' } },

      // Admin
      // 「系统管理」改名「账号与权限」（2026-09-17）：那一页只剩账号与权限矩阵两件事，
      // 而旧名字把「和系统有关的」都吸了过去——学生导入、题库导入、审计日志都曾在
      // 这一页里各留了一份副本。路径不动，改名只改标题。
      { path: 'admin/system', component: AdminSystemPage, meta: { role: 'admin', title: '账号与权限' } },
      { path: 'admin/organization', component: OrganizationPage, meta: { role: 'admin', title: '组织学生' } },
      { path: 'admin/scale', component: ScalePage, meta: { role: 'admin', title: '量表题库' } },
      { path: 'admin/settings', component: SettingsPage, meta: { role: 'admin', title: '系统配置' } },
      // 导出中心归管理员与心理老师：前者看得到全部人的作业、能替任何人叫停，
      // **但下载不下来**（他的心理详情能力是 `NONE`，见 `list_export_jobs`）。
      // 德育领导没有这一页——他的受控导出是 `PROGRESS_SUMMARY`，那是**聚合**，
      // 而他手上没有一处会建导出作业的入口。
      { path: 'admin/exports', component: ExportCenterPage, meta: { role: 'admin', title: '导出中心' } },
      { path: 'admin/audit', component: AuditPage, meta: { role: 'admin', title: '审计日志' } },
    ]
  }
]
