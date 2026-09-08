import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid.uuid4()))


def _http_error(status_code: int, detail: object) -> tuple[str, str]:
    text = str(detail)
    if text == "doubao route has not passed release validation":
        return "VOICE_ROUTE_NOT_VALIDATED", "豆包语音方案尚未完成启用配置或发布验证"
    if "voice preset is unavailable or incompatible" in text:
        return "VOICE_PRESET_INCOMPATIBLE", "此声音不适用于当前语音方案，请重新选择"
    if "selected route does not support" in text:
        return "VOICE_PARAMETER_UNSUPPORTED", "当前语音方案不支持此设置，请刷新后重新选择"
    if text == "email already registered":
        return "EMAIL_REGISTERED", "该邮箱已有账户，请使用邮箱登录；不会自动合并账户"
    if text == "email not registered":
        return "EMAIL_NOT_REGISTERED", "该邮箱尚未注册，请选择注册账号"
    if text == "email already bound":
        return "EMAIL_BOUND", "当前账户已绑定邮箱"
    if "invalid email code" in text:
        return "INVALID_CODE", "验证码不正确，请重新输入"
    if status_code == 429:
        return "TOO_MANY_REQUESTS", "请求过于频繁，请稍后再试"
    if status_code == 401:
        return "AUTH_REQUIRED", "登录状态已失效，请重新登录"
    if status_code == 403 and "agreement" in text.lower():
        return "AGREEMENTS_REQUIRED", "请先完成年龄与服务协议确认"
    if status_code == 403:
        return "ACCESS_DENIED", "当前账户暂时无法使用此功能"
    if status_code == 404:
        return "NOT_FOUND", "没有找到对应内容"
    if status_code == 409:
        return "STATE_CONFLICT", "当前状态无法完成此操作，请刷新后重试"
    if status_code == 410:
        return "EXPIRED", "操作已过期，请重新开始"
    if status_code == 423:
        return "DEVICE_LOCKED", "设备当前不可用，请联系售后"
    if status_code == 503:
        return "SERVICE_UNAVAILABLE", "服务暂时不可用，请稍后重试"
    return "REQUEST_FAILED", "请求未完成，请稍后重试"


def install_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        code, message = _http_error(exc.status_code, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": code, "message": message, "request_id": _request_id(request)},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "VALIDATION_ERROR",
                "message": "请检查填写内容后重试",
                "request_id": _request_id(request),
            },
        )
