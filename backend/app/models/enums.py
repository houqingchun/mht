from enum import StrEnum


class RoleCode(StrEnum):
    STUDENT = "student"
    COUNSELOR = "counselor"
    LEADER = "leader"
    ADMIN = "admin"


class AccountType(StrEnum):
    STUDENT_NO = "STUDENT_NO"
    MOBILE = "MOBILE"
    ADMIN_USERNAME = "ADMIN_USERNAME"


class ScopeType(StrEnum):
    SCHOOL = "SCHOOL"
    GRADE = "GRADE"
    CLASS = "CLASS"
    STUDENT = "STUDENT"

