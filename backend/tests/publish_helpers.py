"""Shared fixtures for publishing tests: an approved video (DB rows + a fake video file)
without rendering, plus helpers to wait for publications."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from videostudio.db import Project, Render
from videostudio.db import Storyboard as StoryboardRow
from videostudio.review import lifecycle


def make_approved_video(client: TestClient, size: int = 700_000, aspect: str = "16:9",
                        duration: float = 42.0, title: str = "Quang hợp ở thực vật",
                        thumbnail: str = "custom", extra_renders: list[str] | None = None
                        ) -> dict[str, Any]:
    svc = client.app.state.services  # type: ignore[attr-defined]
    data_dir: Path = svc.settings.data_dir
    data = {"title": title, "style": "explainer_16x9", "aspect": aspect, "language": "vi",
            "scenes": [{"id": "s1", "narration": "Xin chào các bạn.",
                        "visual": {"type": "title_card", "title": title}},
                       {"id": "s2", "narration": "Lá cây hấp thụ ánh sáng.",
                        "visual": {"type": "text_card", "title": "Ánh sáng"}}]}
    settings = {"style": "explainer_16x9", "aspect": aspect}
    content = lifecycle.content_hash(data, settings)
    with svc.session_factory.begin() as s:
        p = Project(name="Kênh Sinh học")
        s.add(p)
        s.flush()
        out_dir = data_dir / "projects" / p.id / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        sb = StoryboardRow(project_id=p.id, title=title, data=data, settings=settings,
                           assets={}, status="approved", approved_hash=content)
        s.add(sb)
        s.flush()
        renders = []
        for i, a in enumerate([aspect, *(extra_renders or [])]):
            video = out_dir / f"video tiếng việt {i}.mp4"
            video.write_bytes(bytes(range(256)) * (size // 256) + b"x" * (size % 256))
            srt = out_dir / f"video tiếng việt {i}.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nXin chào các bạn.\n",
                           encoding="utf-8")
            r = Render(project_id=p.id, storyboard_id=sb.id, status="done",
                       style="explainer_16x9", aspect=a, duration_s=duration, size_bytes=size,
                       outputs={"video": video.relative_to(data_dir).as_posix(),
                                "srt": srt.relative_to(data_dir).as_posix()},
                       settings={"content_hash": content, "variant": i > 0, "width": 1920,
                                 "height": 1080, "timeline": [
                                     {"scene_id": "s1", "kind": "scene", "start": 0,
                                      "end": 20},
                                     {"scene_id": "s2", "kind": "scene", "start": 20,
                                      "end": duration}]})
            s.add(r)
            s.flush()
            renders.append(r.id)
        sb.approved_render_id = renders[0]
        meta: dict[str, Any] = {"common": {"title": title, "description": "Mô tả tiếng Việt",
                                           "tags": ["sinh học", "quang hợp"],
                                           "hashtags": ["#SinhHoc"]}}
        if thumbnail == "custom":
            img = data_dir / "projects" / p.id / "thumb source.png"
            Image.new("RGB", (1600, 900), (30, 120, 60)).save(img)
            meta["thumbnail"] = {"mode": "custom",
                                 "custom_path": img.relative_to(data_dir).as_posix()}
        else:
            meta["thumbnail"] = {"mode": thumbnail}
        sb.publish_meta = meta
        return {"id": sb.id, "project_id": p.id, "render_id": renders[0], "renders": renders,
                "video_path": data_dir / s.get(Render, renders[0]).outputs["video"],
                "size": size}


def wait_pub(client: TestClient, auth, pub_id: str, statuses: tuple[str, ...],
             timeout: float = 20) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    pub: dict[str, Any] = {}
    while time.monotonic() < deadline:
        pub = client.get(f"/publications/{pub_id}", headers=auth).json()
        if pub["status"] in statuses:
            return pub
        time.sleep(0.05)
    raise AssertionError(f"publication {pub_id} stuck in {pub.get('status')}: {pub}")


def connect_account(client: TestClient, platform: str, publisher_id: str, token: dict,
                    external_id: str = "UC123", name: str = "Kênh thật") -> str:
    """Create a 'real' (non-mock) account with tokens in the SecretStore."""
    import json

    from videostudio.db import SocialAccount

    svc = client.app.state.services  # type: ignore[attr-defined]
    with svc.session_factory.begin() as s:
        a = SocialAccount(platform=platform, publisher_id=publisher_id, name=name,
                          external_id=external_id,
                          kind="channel" if platform == "youtube" else "page", meta={})
        s.add(a)
        s.flush()
        account_id = a.id
    svc.secrets.set(f"social:{account_id}:token", json.dumps(token))
    return account_id


def no_sleep(client: TestClient) -> list[float]:
    """Make publisher backoff sleeps instant; returns the list of requested delays."""
    delays: list[float] = []

    async def fake(seconds: float) -> None:
        delays.append(seconds)

    client.app.state.services.publish.sleep = fake  # type: ignore[attr-defined]
    return delays
