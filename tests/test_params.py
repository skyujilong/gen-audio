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
