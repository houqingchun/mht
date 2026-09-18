"""audit log student attribution

Adds audit_log.student_id so the per-student access-audit tab can query rows
directly. resource_type/resource_id alone cannot attribute a row to a student:
actions on a care case write heterogeneous resource types (STUDENT_CARE_CASE,
MANUAL_REVIEW, FOLLOW_UP_RECORD, RETEST_PLAN) that carry no student reference.
Rows written before this migration keep student_id = NULL.

Revision ID: 0006_audit_student_id
Revises: 0005_care_case_closure
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_audit_student_id"
down_revision = "0005_care_case_closure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("student_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_audit_log_student_id", "audit_log", "student", ["student_id"], ["id"]
    )
    op.create_index("ix_audit_log_student_id", "audit_log", ["student_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_student_id", table_name="audit_log")
    op.drop_constraint("fk_audit_log_student_id", "audit_log", type_="foreignkey")
    op.drop_column("audit_log", "student_id")
