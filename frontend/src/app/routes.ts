import type { RouteRecordRaw } from 'vue-router'
import LoginPage from '../features/auth/LoginPage.vue'
import AppLayout from './AppLayout.vue'
import StudentHomePage from '../features/student/StudentHomePage.vue'
import StudentAssessmentPage from '../features/student/StudentAssessmentPage.vue'
import CounselorWorkbenchPage from '../features/care/CounselorWorkbenchPage.vue'
import CasesPage from '../features/care/CasesPage.vue'
import CareCaseDetailPage from '../features/care/CareCaseDetailPage.vue'
import StudentRecordsPage from '../features/care/StudentRecordsPage.vue'
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
import ReportOverviewPage from '../features/analytics/views/OverviewPage.vue'
import ReportDimensionsPage from '../features/analytics/views/DimensionsPage.vue'
import ReportGradesPage from '../features/analytics/views/GradesPage.vue'
import ReportClassPage from '../features/analytics/views/ClassPortraitPage.vue'
import ReportExportPage from '../features/analytics/views/ReportExportPage.vue'
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
      // 路径里**没有** `cases`，这是有意的：档案页回答「这份档案走到哪一步了」，
      // 这一页回答「这个学生考过几次、每次多少分」——而**没有档案的学生也要看得到**，
      // 那正是它存在的理由（全库唯一的开档触发点是重点题命中，所以被评成
      // 「需要关注」的学生照样可能没档案）。挂到 `cases/...` 下面会让这一页读起来
      // 像是档案的一部分，而它恰恰不是。
      { path: 'counselor/students/:studentId/records', component: StudentRecordsPage, meta: { role: 'counselor', title: '学生测评记录' } },
      { path: 'counselor/data', component: DataCenterPage, meta: { role: 'counselor', title: '数据中心' } },
      { path: 'counselor/analytics', redirect: '/counselor/analytics/overview', meta: { role: 'counselor', title: '统计分析' } },
      { path: 'counselor/analytics/overview', component: ReportOverviewPage, meta: { role: 'counselor', title: '全校预警总览' } },
      { path: 'counselor/analytics/dimensions', component: ReportDimensionsPage, meta: { role: 'counselor', title: '全校八维度分析' } },
      { path: 'counselor/analytics/grades', component: ReportGradesPage, meta: { role: 'counselor', title: '年级维度对比' } },
      { path: 'counselor/analytics/classes', component: ReportClassPage, meta: { role: 'counselor', title: '班级维度画像' } },
      { path: 'counselor/analytics/report', component: ReportExportPage, meta: { role: 'counselor', title: '专业解读与导出' } },
      { path: 'counselor/audit', component: AuditPage, meta: { role: 'counselor', title: '审计日志' } },
      { path: 'counselor/exports', component: ExportCenterPage, meta: { role: 'counselor', title: '导出中心' } },
      { path: 'counselor/tasks', component: TasksPage, meta: { role: 'counselor', title: '测评任务' } },

      // Leader
      { path: 'leader/overview', component: LeaderOverviewPage, meta: { role: 'leader', title: '领导总览' } },
      { path: 'leader/progress', component: ProgressPage, meta: { role: 'leader', title: '重点进展' } },
      { path: 'leader/analytics', redirect: '/leader/analytics/overview', meta: { role: 'leader', title: '学校统计' } },
      { path: 'leader/analytics/overview', component: ReportOverviewPage, meta: { role: 'leader', title: '全校预警总览' } },
      { path: 'leader/analytics/dimensions', component: ReportDimensionsPage, meta: { role: 'leader', title: '全校八维度分析' } },
      { path: 'leader/analytics/grades', component: ReportGradesPage, meta: { role: 'leader', title: '年级维度对比' } },
      { path: 'leader/analytics/classes', component: ReportClassPage, meta: { role: 'leader', title: '班级维度画像' } },
      { path: 'leader/analytics/report', component: ReportExportPage, meta: { role: 'leader', title: '专业解读与导出' } },
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
