"""业务异常体系。

所有业务异常应继承 `AppError` 并通过 FastAPI 异常处理器统一转为 JSON 响应。
**严禁**在业务代码里用 try/except 吞错 —— 任何错误情况必须显式抛 `AppError` 子类。
"""


class AppError(Exception):
    """业务异常基类。

    Attributes:
        detail: 人类可读的错误描述，会原样返回给前端用于 toast 提示。
        code: 机器可读的错误码，前端可用于 if/else 分支判断。
        status: HTTP 状态码。
    """

    code: str = "INTERNAL_ERROR"
    status: int = 500

    def __init__(self, detail: str, code: str | None = None, status: int | None = None):
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status


class InvalidParamsError(AppError):
    """请求参数不合法。"""
    code = "INVALID_PARAMS"
    status = 400


class CardNotFoundError(AppError):
    """参数卡不存在。"""
    code = "CARD_NOT_FOUND"
    status = 404


class JobNotFoundError(AppError):
    """合成任务不存在。"""
    code = "JOB_NOT_FOUND"
    status = 404


class TtsError(AppError):
    """ChatTTS 合成失败。"""
    code = "TTS_FAILED"
    status = 500


class ModelNotReadyError(AppError):
    """ChatTTS 模型尚未加载完成。"""
    code = "MODEL_NOT_LOADED"
    status = 503


class ImportFormatError(AppError):
    """导入 JSON 格式不合法。"""
    code = "IMPORT_INVALID_FORMAT"
    status = 400


class AudioFileNotFoundError(AppError):
    """DB 中记录了文件路径但磁盘上找不到（孤儿记录 / 误删）。"""
    code = "AUDIO_FILE_NOT_FOUND"
    status = 404


class JobNotCancellableError(AppError):
    """任务不在 pending 状态，不能取消。"""
    code = "JOB_NOT_CANCELLABLE"
    status = 409


class JobNotReadyError(AppError):
    """任务尚未完成，不能取结果。"""
    code = "JOB_NOT_READY"
    status = 409
