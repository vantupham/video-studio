"""LLM suggestions (Vietnamese) for post titles, description and hashtags."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from videostudio.providers.base import ChatMessage, ResolvedProvider
from videostudio.providers.llm_json import complete_json

SYSTEM = (
    "Bạn là biên tập viên mạng xã hội người Việt. Viết tiếng Việt có dấu chuẩn, tự nhiên, "
    "không giật tít sai sự thật, không dùng ký tự < hoặc >. Chỉ trả về JSON đúng schema.")

PLATFORM_HINTS = {
    "youtube": ("YouTube: tiêu đề ≤ 70 ký tự, chứa từ khoá chính ở đầu; mô tả 2–4 câu mở "
                "đầu hấp dẫn, sau đó tóm tắt nội dung chính dạng gạch đầu dòng; 8–12 thẻ "
                "(tags) ngắn; 3–5 hashtag."),
    "facebook": ("Facebook: tiêu đề ngắn ≤ 60 ký tự; mô tả mở đầu bằng một câu gây chú ý, "
                 "thân thiện, có lời kêu gọi tương tác; 3–6 hashtag."),
}


class MetadataSuggestion(BaseModel):
    titles: list[str] = Field(min_length=1, max_length=5,
                              description="3 phương án tiêu đề, tốt nhất trước")
    description: str
    tags: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)


def _clean(s: MetadataSuggestion, platform: str) -> dict[str, Any]:
    limit = 100 if platform == "youtube" else 255
    titles = [t.replace("<", "").replace(">", "").strip()[:limit] for t in s.titles
              if t.strip()]
    hashtags = []
    for h in s.hashtags:
        h = "#" + h.strip().lstrip("#").replace(" ", "")
        if len(h) > 1 and h not in hashtags:
            hashtags.append(h)
    tags = []
    for t in s.tags:
        t = t.strip().lstrip("#")
        if t and t not in tags:
            tags.append(t[:60])
    return {"titles": titles, "description": s.description.replace("<", "").replace(">", ""),
            "tags": tags, "hashtags": hashtags}


async def suggest_metadata(llm: ResolvedProvider, *, platform: str, title: str,
                           aspect: str, duration_s: float | None, storyboard: dict[str, Any],
                           language: str = "vi") -> dict[str, Any]:
    scenes = storyboard.get("scenes", [])
    payload = {
        "task": "metadata", "platform": platform, "title": title, "aspect": aspect,
        "duration_s": round(duration_s or 0), "language": language,
        "headings": [sc.get("visual", {}).get("title") for sc in scenes
                     if sc.get("visual", {}).get("title")][:12],
        "narrations": [sc.get("narration", "") for sc in scenes][:30],
    }
    user = (f"Đề xuất thông tin đăng video lên {platform}. {PLATFORM_HINTS.get(platform, '')}\n"
            f"Video {'dọc' if aspect == '9:16' else 'ngang'}, dài khoảng "
            f"{payload['duration_s']} giây. Nội dung (tiêu đề và lời thoại từng cảnh):\n"
            f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```")
    messages = [ChatMessage(role="system", content=SYSTEM),
                ChatMessage(role="user", content=user)]
    result = await complete_json(llm.provider, llm.ctx, messages, MetadataSuggestion,
                                 model=llm.model, temperature=0.7)
    return _clean(result, platform)
