# Video Studio backend

FastAPI sidecar cho ứng dụng desktop Video Studio. Xem `../README.md` và `../docs/` để biết chi tiết.

```bash
uv sync
VIDEOSTUDIO_TOKEN=dev-token uv run python -m videostudio --port 8765 --reload
uv run pytest
```
