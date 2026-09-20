from fastapi import APIRouter

from app.api.v1.analytics import router as analytics_router
from app.api.v1.assessment import router as assessment_router
from app.api.v1.assessment_import import router as assessment_import_router
from app.api.v1.audit import router as audit_router
from app.api.v1.auth import admin_router, permission_router, router as auth_router
from app.api.v1.care import router as care_router
from app.api.v1.exports import router as exports_router
from app.api.v1.health import router as health_router
from app.api.v1.scales import router as scales_router
from app.api.v1.settings import public_router as public_settings_router, router as settings_router
from app.api.v1.student_roster import router as student_roster_router
from app.api.v1.students import router as students_router
from app.api.v1.tasks import router as tasks_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(admin_router)
api_router.include_router(permission_router)
api_router.include_router(assessment_router)
api_router.include_router(assessment_import_router)
api_router.include_router(care_router)
api_router.include_router(analytics_router)
api_router.include_router(students_router)
api_router.include_router(student_roster_router)
api_router.include_router(scales_router)
api_router.include_router(settings_router)
api_router.include_router(public_settings_router)
api_router.include_router(audit_router)
api_router.include_router(exports_router)
api_router.include_router(tasks_router)
