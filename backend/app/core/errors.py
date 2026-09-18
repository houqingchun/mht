from dataclasses import dataclass
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse


@dataclass
class AppError(Exception):
    code: str
    message: str
    status_code: int = 400


def request_id() -> str:
    return f"req_{uuid4().hex[:16]}"


def ok(data=None, request_id_value: str | None = None):
    return {
        "success": True,
        "data": data if data is not None else {},
        "request_id": request_id_value or request_id(),
        "error": None,
    }


def error_response(code: str, message: str, status_code: int, request_id_value: str | None = None):
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "request_id": request_id_value or request_id(),
            "error": {"code": code, "message": message},
        },
    )


async def app_error_handler(_: Request, exc: AppError):
    return error_response(exc.code, exc.message, exc.status_code)

