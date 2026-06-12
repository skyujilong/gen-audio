import pytest
from pydantic import ValidationError

from app.core.params import (
    TtsParams,
    DrawRequest,
    DrawnCard,
    SynthesizeRequest,
    BatchSynthesizeRequest,
    CardListItem,
    CardUpdate,
    ImportCardItem,
    ImportRequest,
    HealthResponse,
    JobStatus,
    Job,
    SpeakerBase,
    SpeakerCreate,
    SpeakerUpdate,
    SpeakerOut,
    SpeakerListItem,
)


def test_tts_params_defaults():
    p = TtsParams(seed=42, speaker="abc")
    assert p.seed == 42
    assert p.temperature == 0.3
    assert p.top_p == 0.7
    assert p.top_k == 20
    assert p.speaker == "abc"
    assert p.refiner_text is None


def test_tts_params_rejects_missing_seed():
    with pytest.raises(ValidationError):
        TtsParams(speaker="abc")  # type: ignore[call-arg]


def test_tts_params_oral_laugh_break_defaults():
    """oral/laugh/break_ 默认值应为 int 0。"""
    p = TtsParams(seed=1, speaker="x")
    assert p.oral == 0
    assert p.laugh == 0
    assert p.break_ == 0


def test_tts_params_oral_laugh_break_range():
    """oral/laugh/break_ 应支持 0-9 范围。"""
    p = TtsParams(seed=1, speaker="x", oral=3, laugh=2, break_=7)
    assert p.oral == 3
    assert p.laugh == 2
    assert p.break_ == 7


def test_tts_params_speed_default_is_int_5():
    """speed 默认应为整数 5（不再默认 [speed_5] 字符串）。"""
    p = TtsParams(seed=1, speaker="x")
    assert p.speed == 5
    assert isinstance(p.speed, int)


def test_tts_params_speed_accepts_int():
    p = TtsParams(seed=1, speaker="x", speed=3)
    assert p.speed == 3


def test_tts_params_speed_compat_old_string_format():
    """老数据形如 '[speed_5]' 的 speed 字符串，validator 应自动转 int 5。"""
    p = TtsParams(seed=1, speaker="x", speed="[speed_5]")  # type: ignore[arg-type]
    assert p.speed == 5
    p2 = TtsParams(seed=1, speaker="x", speed="[speed_3]")  # type: ignore[arg-type]
    assert p2.speed == 3
    p3 = TtsParams(seed=1, speaker="x", speed="[speed_9]")  # type: ignore[arg-type]
    assert p3.speed == 9


def test_draw_request_new_fields_defaults():
    """DrawRequest 也要有新字段，默认值与 TtsParams 一致。"""
    req = DrawRequest()
    assert req.oral == 0
    assert req.laugh == 0
    assert req.break_ == 0
    assert req.speed == 5


def test_draw_request_refiner_optional():
    req = DrawRequest()
    assert req.refiner_text is None

    req2 = DrawRequest(refiner_text="温柔")
    assert req2.refiner_text == "温柔"


def test_synthesize_request_requires_params():
    with pytest.raises(ValidationError):
        SynthesizeRequest(card_id=1, text="hi")  # type: ignore[call-arg]


def test_batch_synthesize_request_wraps_list():
    req = BatchSynthesizeRequest(
        items=[
            SynthesizeRequest(card_id=1, params=TtsParams(seed=1, speaker="x"), text="a"),
            SynthesizeRequest(card_id=1, params=TtsParams(seed=2, speaker="x"), text="b"),
        ]
    )
    assert len(req.items) == 2


def test_card_list_item_serialization():
    item = CardListItem(
        id=1,
        name="test",
        is_favorited=False,
        demo_text="demo",
        params=TtsParams(seed=1, speaker="x"),
        created_at="2026-06-11T00:00:00",
        updated_at="2026-06-11T00:00:00",
    )
    assert item.id == 1
    assert item.params.seed == 1


def test_card_update_partial():
    upd = CardUpdate(name="新名字")
    assert upd.name == "新名字"
    assert upd.is_favorited is None

    upd2 = CardUpdate(is_favorited=True)
    assert upd2.name is None
    assert upd2.is_favorited is True


def test_import_card_item_demo_text_optional():
    item = ImportCardItem(
        name="x",
        params=TtsParams(seed=1, speaker="x"),
    )
    assert item.demo_text is None
    assert item.is_favorited is False


def test_import_request_validates_cards():
    req = ImportRequest(
        cards=[
            ImportCardItem(name="a", params=TtsParams(seed=1, speaker="x")),
        ]
    )
    assert len(req.cards) == 1


def test_health_response():
    h = HealthResponse(status="ok", model_loaded=True, queue_size=2)
    assert h.queue_size == 2


def test_job_status_enum_values():
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.RUNNING.value == "running"
    assert JobStatus.DONE.value == "done"
    assert JobStatus.FAILED.value == "failed"
    assert JobStatus.CANCELED.value == "canceled"


def test_job_requires_params():
    from datetime import datetime
    job = Job(
        id="uuid-1",
        card_id=1,
        params=TtsParams(seed=1, speaker="x"),
        text="hi",
        status=JobStatus.PENDING,
        progress=0.0,
        created_at=datetime.now(),
    )
    assert job.id == "uuid-1"
    assert job.status == JobStatus.PENDING


# === Phase 1.3: Speaker Pydantic 模型 ===

def test_speaker_create_requires_name_and_tensor():
    """SpeakerCreate 必须有 name + tensor_base64。"""
    with pytest.raises(ValidationError):
        SpeakerCreate()  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        SpeakerCreate(name="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        SpeakerCreate(tensor_base64="abc")  # type: ignore[call-arg]


def test_speaker_create_valid():
    s = SpeakerCreate(name="男声A", tensor_base64="QkFTRTY0", tags=["成熟", "磁性"])
    assert s.name == "男声A"
    assert s.tensor_base64 == "QkFTRTY0"
    assert s.tags == ["成熟", "磁性"]
    assert s.is_favorited is False  # 默认 False


def test_speaker_update_partial():
    u1 = SpeakerUpdate(name="新名字")
    assert u1.name == "新名字"
    assert u1.is_favorited is None
    assert u1.tags is None

    u2 = SpeakerUpdate(is_favorited=True)
    assert u2.is_favorited is True

    u3 = SpeakerUpdate(tags=["新标签"])
    assert u3.tags == ["新标签"]


def test_speaker_out_full_fields():
    """SpeakerOut 包含所有字段（id/created_at/updated_at）。"""
    s = SpeakerOut(
        id=1,
        name="女声B",
        tensor_base64="X",
        tags=["温柔"],
        is_favorited=True,
        created_at="2026-06-11T00:00:00",
        updated_at="2026-06-11T00:00:00",
    )
    assert s.id == 1
    assert s.is_favorited is True


def test_speaker_list_item_no_tensor():
    """列表项不带 tensor（节省带宽）。"""
    s = SpeakerListItem(
        id=1,
        name="x",
        tags=[],
        is_favorited=False,
        created_at="2026-06-11T00:00:00",
        updated_at="2026-06-11T00:00:00",
    )
    assert s.id == 1


def test_speaker_base_inherits_fields():
    """SpeakerBase 共享 name/tensor_base64/tags 字段（被 Create/Out 继承）。"""
    s = SpeakerBase(name="x", tensor_base64="abc")
    assert s.name == "x"
    assert s.tensor_base64 == "abc"
    assert s.tags == []  # 默认空列表
    assert s.is_favorited is False


# === Phase 2.6.1: 增强 / 降噪字段 ===

def test_tts_params_enhance_audio_default_false():
    """enhance_audio / denoise_audio 默认 False。"""
    p = TtsParams(seed=1, speaker="x")
    assert p.enhance_audio is False
    assert p.denoise_audio is False


def test_tts_params_enhance_solver_default():
    """solver 默认 'midpoint'。"""
    p = TtsParams(seed=1, speaker="x")
    assert p.solver == "midpoint"


def test_tts_params_enhance_nfe_default():
    """nfe 默认 64，范围 1-128。"""
    p = TtsParams(seed=1, speaker="x")
    assert p.nfe == 64


def test_tts_params_enhance_tau_default():
    """tau 默认 0.5，范围 0-1。"""
    p = TtsParams(seed=1, speaker="x")
    assert p.tau == 0.5


def test_tts_params_enhance_custom_values():
    p = TtsParams(
        seed=1, speaker="x",
        enhance_audio=True, denoise_audio=True,
        solver="rk4", nfe=32, tau=0.7,
    )
    assert p.enhance_audio is True
    assert p.denoise_audio is True
    assert p.solver == "rk4"
    assert p.nfe == 32
    assert p.tau == 0.7


def test_tts_params_enhance_nfe_out_of_range():
    """nfe 必须在 1-128 范围内。"""
    with pytest.raises(ValidationError):
        TtsParams(seed=1, speaker="x", nfe=0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        TtsParams(seed=1, speaker="x", nfe=129)  # type: ignore[call-arg]


def test_tts_params_enhance_tau_out_of_range():
    """tau 必须在 0-1 范围内。"""
    with pytest.raises(ValidationError):
        TtsParams(seed=1, speaker="x", tau=-0.1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        TtsParams(seed=1, speaker="x", tau=1.5)  # type: ignore[call-arg]


def test_tts_params_enhance_solver_invalid():
    """solver 必须是 midpoint / rk4 / euler 之一。"""
    with pytest.raises(ValidationError):
        TtsParams(seed=1, speaker="x", solver="bogus")  # type: ignore[call-arg]


def test_draw_request_enhance_defaults():
    """DrawRequest 也有新字段。"""
    req = DrawRequest()
    assert req.enhance_audio is False
    assert req.denoise_audio is False
    assert req.solver == "midpoint"
    assert req.nfe == 64
    assert req.tau == 0.5
