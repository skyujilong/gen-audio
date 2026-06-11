# gen-audio

本地单用户 TTS「抽卡 + 批量合成」工具，基于 2noise/ChatTTS。

## 启动

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000`。

## 测试

```bash
pytest tests/ -v
```

详见 `docs/superpowers/specs/2026-06-11-gen-audio-design.md`。
