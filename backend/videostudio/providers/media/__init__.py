"""Stock media providers (images for scenes with ``visual.type == "stock"``)."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import ClassVar

from ..base import (
    MediaItem,
    MediaProvider,
    TestResult,
    secret_field,
    timeout_field,
)
from ..errors import ProviderConfigError, client, send


class PexelsMedia(MediaProvider):
    id: ClassVar[str] = "pexels"
    name: ClassVar[str] = "Pexels (ảnh miễn phí)"
    description: ClassVar[str] = (
        "Kho ảnh/video miễn phí, giấy phép Pexels (dùng thương mại được, không bắt buộc ghi "
        "nguồn). API key miễn phí.")
    docs_url: ClassVar[str] = "https://www.pexels.com/api/"

    def config_schema(self):
        return {
            "type": "object",
            "required": ["api_key"],
            "properties": {
                "api_key": secret_field("API key"),
                "locale": {"type": "string", "title": "Ngôn ngữ tìm kiếm", "default": "vi-VN"},
                "timeout_s": timeout_field(30),
            },
        }

    def _headers(self, ctx):
        key = ctx.get("api_key")
        if not key:
            raise ProviderConfigError(f"{self.name}: chưa cấu hình API key", provider=self.id)
        return {"Authorization": str(key)}

    async def search(self, ctx, query, *, kind="image", orientation="landscape", limit=10):
        params = {"query": query, "orientation": orientation, "per_page": limit,
                  "locale": ctx.get("locale", "vi-VN")}
        async with client(ctx.timeout) as http:
            if kind == "video":
                r = await send(http, "GET", "https://api.pexels.com/videos/search",
                               provider=self.name, headers=self._headers(ctx), params=params)
                return [
                    MediaItem(id=str(v["id"]), kind="video",
                              url=max(v.get("video_files", []),
                                      key=lambda f: f.get("width") or 0)["link"],
                              preview_url=v.get("image"), width=v.get("width"),
                              height=v.get("height"), author=(v.get("user") or {}).get("name"),
                              source=v.get("url"))
                    for v in r.json().get("videos", []) if v.get("video_files")
                ]
            r = await send(http, "GET", "https://api.pexels.com/v1/search", provider=self.name,
                           headers=self._headers(ctx), params=params)
        return [
            MediaItem(id=str(p["id"]), url=p["src"].get("large2x") or p["src"]["original"],
                      preview_url=p["src"].get("medium"), width=p.get("width"),
                      height=p.get("height"), author=p.get("photographer"), source=p.get("url"))
            for p in r.json().get("photos", [])
        ]

    async def download(self, ctx, item, dest):
        async with client(max(ctx.timeout, 60)) as http:
            r = await send(http, "GET", item.url, provider=self.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest

    async def test_connection(self, ctx):
        items = await self.search(ctx, "nature", limit=1)
        return TestResult(ok=True, message=f"Kết nối thành công ({len(items)} kết quả thử)")


class MockMedia(MediaProvider):
    id: ClassVar[str] = "mock_media"
    name: ClassVar[str] = "Ảnh giả lập (gradient, offline)"
    description: ClassVar[str] = "Tạo ảnh nền gradient có chữ thay cho ảnh stock."
    requires_network: ClassVar[bool] = False
    is_mock: ClassVar[bool] = True

    def config_schema(self):
        return {"type": "object", "properties": {}}

    async def search(self, ctx, query, *, kind="image", orientation="landscape", limit=10):
        w, h = (1920, 1080) if orientation == "landscape" else (1080, 1920)
        digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:8]
        return [MediaItem(id=f"{digest}-{i}", url=f"mock://{digest}/{i}?q={query}", width=w,
                          height=h, author="Video Studio", source="mock")
                for i in range(min(limit, 3))]

    async def download(self, ctx, item, dest):
        from PIL import Image, ImageDraw

        def make() -> Path:
            seed = int(hashlib.sha1(item.id.encode()).hexdigest()[:6], 16)
            c1 = ((seed >> 16) & 0xFF, (seed >> 8) & 0xFF, seed & 0xFF)
            c2 = tuple(255 - c for c in c1)
            w, h = item.width or 1920, item.height or 1080
            img = Image.new("RGB", (w, h), c1)
            draw = ImageDraw.Draw(img)
            for y in range(0, h, 4):
                t = y / h
                color = tuple(int(a + (b - a) * t) for a, b in zip(c1, c2, strict=True))
                draw.rectangle([0, y, w, y + 4], fill=color)
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, "JPEG", quality=88)
            return dest

        return await asyncio.to_thread(make)

    async def test_connection(self, ctx):
        return TestResult(ok=True, message="Luôn sẵn sàng")


PROVIDERS = [PexelsMedia, MockMedia]
