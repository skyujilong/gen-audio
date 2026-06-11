import pytest
from app.core.exceptions import (
    AppError,
    InvalidParamsError,
    CardNotFoundError,
    JobNotFoundError,
    TtsError,
    ModelNotReadyError,
    ImportFormatError,
    AudioFileNotFoundError,
    JobNotCancellableError,
    JobNotReadyError,
)


def test_app_error_default_code():
    err = AppError("出错了")
    assert err.detail == "出错了"
    assert err.code == "INTERNAL_ERROR"
    assert err.status == 500


def test_subclass_overrides_code_and_status():
    err = CardNotFoundError("卡 42 不存在")
    assert err.code == "CARD_NOT_FOUND"
    assert err.status == 404
    assert err.detail == "卡 42 不存在"


@pytest.mark.parametrize(
    "cls,code,status",
    [
        (InvalidParamsError, "INVALID_PARAMS", 400),
        (CardNotFoundError, "CARD_NOT_FOUND", 404),
        (JobNotFoundError, "JOB_NOT_FOUND", 404),
        (TtsError, "TTS_FAILED", 500),
        (ModelNotReadyError, "MODEL_NOT_LOADED", 503),
        (ImportFormatError, "IMPORT_INVALID_FORMAT", 400),
        (AudioFileNotFoundError, "AUDIO_FILE_NOT_FOUND", 404),
        (JobNotCancellableError, "JOB_NOT_CANCELLABLE", 409),
        (JobNotReadyError, "JOB_NOT_READY", 409),
    ],
)
def test_each_subclass_has_correct_code_and_status(cls, code, status):
    err = cls("detail-msg")
    assert err.code == code
    assert err.status == status


def test_is_a_normal_exception():
    with pytest.raises(AppError):
        raise CardNotFoundError("boom")
