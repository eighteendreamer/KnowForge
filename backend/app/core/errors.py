from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException


class AppError(Exception):
    def __init__(self, status: int, code: int, message: str, headers: dict[str, str] | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


def success(data: Any) -> dict[str, Any]:
    return {"code": 0, "message": "success", "data": data}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            {"code": exc.code, "message": exc.message}, status_code=exc.status, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        query_error = any(error["loc"] == ("body", "query") for error in exc.errors())
        return JSONResponse(
            {
                "code": 1002 if query_error else 1001,
                "message": "Query 为空或超过 500 字" if query_error else "参数缺失或格式错误",
            },
            status_code=400,
        )

    @app.exception_handler(IntegrityError)
    async def conflict_handler(request: Request, exc: IntegrityError):
        return JSONResponse({"code": 1001, "message": "数据冲突，请刷新后重试"}, status_code=409)

    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException):
        return JSONResponse({"code": 1001, "message": str(exc.detail)}, status_code=exc.status_code)
