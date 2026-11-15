"""Pre-publish checks against :class:`PublishConstraints`.

Errors block the publication; warnings are shown and stored on it. An aspect mismatch
comes with a ``fix`` the UI offers as "re-render in 9:16"."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .base import PublishConstraints


def _issue(level: str, code: str, message: str, field: str | None = None,
           fix: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"level": level, "code": code, "message": message}
    if field:
        out["field"] = field
    if fix:
        out["fix"] = fix
    return out


def _fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600} giờ {seconds % 3600 // 60} phút"
    if seconds >= 60:
        return f"{seconds // 60} phút {seconds % 60:02d} giây" if seconds % 60 else \
            f"{seconds // 60} phút"
    return f"{seconds} giây"


def validate(c: PublishConstraints, *, video: dict[str, Any], meta: dict[str, Any],
             schedule: dict[str, Any] | None = None, now: datetime | None = None,
             other_aspects: list[str] | None = None) -> list[dict[str, Any]]:
    """``video``: aspect, duration_s, size_bytes, container, codec, has_captions.
    ``meta``: title, description, tags. ``schedule``: mode (now|local|platform), at.
    ``other_aspects``: aspects of already-rendered variants of the same approved content."""
    issues: list[dict[str, Any]] = []
    now = now or datetime.now(UTC)

    aspect = video.get("aspect")
    if c.aspect_ratios and aspect not in c.aspect_ratios:
        target = c.preferred_aspect or c.aspect_ratios[0]
        have = target in (other_aspects or [])
        issues.append(_issue(
            "error", "aspect",
            f"{c.platform}{' ' + c.format if c.format != 'video' else ''} cần tỉ lệ "
            f"{' hoặc '.join(c.aspect_ratios)}, video đang là {aspect}.",
            fix={"action": "use_variant" if have else "rerender_aspect", "aspect": target}))
    elif c.preferred_aspect and aspect != c.preferred_aspect:
        issues.append(_issue("info", "aspect_preferred",
                             f"Nên dùng tỉ lệ {c.preferred_aspect} cho {c.platform}.",
                             fix={"action": "rerender_aspect", "aspect": c.preferred_aspect}))

    duration = float(video.get("duration_s") or 0)
    if c.max_duration_s and duration > c.max_duration_s + 0.05:
        issues.append(_issue("error", "duration_max",
                             f"Video dài {_fmt_duration(duration)}, tối đa "
                             f"{_fmt_duration(c.max_duration_s)}."))
    if c.min_duration_s and duration < c.min_duration_s - 0.05:
        issues.append(_issue("error", "duration_min",
                             f"Video ngắn {_fmt_duration(duration)}, tối thiểu "
                             f"{_fmt_duration(c.min_duration_s)}."))
    size_mb = float(video.get("size_bytes") or 0) / 1e6
    if c.max_file_size_mb and size_mb > c.max_file_size_mb:
        issues.append(_issue("error", "size",
                             f"Tệp {size_mb:.0f} MB vượt giới hạn {c.max_file_size_mb:.0f} MB."))
    container = (video.get("container") or "mp4").lower()
    if c.formats and container not in c.formats:
        issues.append(_issue("error", "format", f"Định dạng {container} không được hỗ trợ."))
    codec = (video.get("codec") or "h264").lower()
    if c.video_codecs and codec not in c.video_codecs:
        issues.append(_issue("error", "codec", f"Codec {codec} không được hỗ trợ."))

    title = (meta.get("title") or "").strip()
    if c.title_required and not title:
        issues.append(_issue("error", "title_required", "Cần nhập tiêu đề.", "title"))
    if c.max_title_len and len(title) > c.max_title_len:
        issues.append(_issue("error", "title_len",
                             f"Tiêu đề {len(title)} ký tự, tối đa {c.max_title_len}.", "title"))
    bad = sorted({ch for ch in title if ch in c.title_forbidden_chars})
    if bad:
        issues.append(_issue("error", "title_chars",
                             f"Tiêu đề không được chứa ký tự {' '.join(bad)}.", "title"))
    description = meta.get("description") or ""
    if c.max_description_len and len(description) > c.max_description_len:
        issues.append(_issue("error", "description_len",
                             f"Mô tả {len(description)} ký tự, tối đa {c.max_description_len}.",
                             "description"))
    if c.max_description_bytes and len(description.encode("utf-8")) > c.max_description_bytes:
        issues.append(_issue("error", "description_bytes",
                             f"Mô tả {len(description.encode('utf-8'))} byte (UTF-8), tối đa "
                             f"{c.max_description_bytes} byte. Tiếng Việt có dấu tốn 2–3 "
                             "byte mỗi chữ.", "description"))
    if any(ch in description for ch in c.title_forbidden_chars):
        issues.append(_issue("error", "description_chars",
                             "Mô tả không được chứa ký tự < hoặc >.", "description"))
    tags = [t.strip() for t in meta.get("tags") or [] if t.strip()]
    if c.max_tags_chars is not None:
        # YouTube counts a tag with spaces as if it were quoted (+2 characters).
        used = sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)
        if used > c.max_tags_chars:
            issues.append(_issue("error", "tags_len",
                                 f"Thẻ dài {used} ký tự, tối đa {c.max_tags_chars}.", "tags"))
    if c.max_hashtags is not None:
        n = sum(1 for word in description.split() if word.startswith("#"))
        if n > c.max_hashtags:
            issues.append(_issue("warning", "hashtags",
                                 f"{n} hashtag; nhiều hơn {c.max_hashtags} có thể bị bỏ qua.",
                                 "description"))
    if video.get("has_captions") and not c.supports_captions:
        issues.append(_issue("info", "captions",
                             "Nền tảng này không nhận tệp phụ đề; dùng bản có phụ đề in "
                             "sẵn nếu cần."))

    schedule = schedule or {}
    mode, at = schedule.get("mode", "now"), schedule.get("at")
    if mode != "now":
        if at is None:
            issues.append(_issue("error", "schedule_missing", "Chọn thời gian đăng.",
                                 "schedule"))
        else:
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            ahead = (at - now).total_seconds()
            if ahead < 30:
                issues.append(_issue("error", "schedule_past",
                                     "Thời gian đăng phải ở tương lai.", "schedule"))
            elif mode == "platform":
                if not c.supports_scheduling:
                    issues.append(_issue("error", "schedule_unsupported",
                                         f"{c.platform} không hỗ trợ hẹn giờ trên nền tảng; "
                                         "dùng hẹn giờ trên máy.", "schedule"))
                if c.min_schedule_ahead_s and ahead < c.min_schedule_ahead_s:
                    issues.append(_issue(
                        "error", "schedule_too_soon",
                        f"Hẹn giờ trên {c.platform} phải sau ít nhất "
                        f"{_fmt_duration(c.min_schedule_ahead_s)}.", "schedule"))
                if c.max_schedule_ahead_s and ahead > c.max_schedule_ahead_s:
                    issues.append(_issue(
                        "error", "schedule_too_far",
                        f"Hẹn giờ trên {c.platform} tối đa "
                        f"{c.max_schedule_ahead_s // 86400} ngày.", "schedule"))
            elif mode == "local" and ahead > timedelta(days=60).total_seconds():
                issues.append(_issue("warning", "schedule_far_local",
                                     "Hẹn giờ trên máy cần Video Studio đang chạy vào lúc "
                                     "đăng.", "schedule"))
    return issues


def has_errors(issues: list[dict[str, Any]]) -> bool:
    return any(i["level"] == "error" for i in issues)
