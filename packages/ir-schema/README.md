# Content IR schema

Định dạng trung gian (Intermediate Representation) chung cho mọi nguồn nội dung.

- `content-ir.schema.json` — JSON Schema (draft 2020-12), **nguồn chuẩn**.
- `ts/content-ir.ts` — kiểu TypeScript cho UI.
- `backend/videostudio/ir/models.py` — model Pydantic tương ứng cho backend.
- `examples/` — ví dụ IR hợp lệ (được test kiểm tra).

Khi sửa schema, cập nhật cả ba nơi; `backend/tests/test_ir_schema.py` sẽ báo lỗi nếu lệch.

Cấu trúc: `ContentIR { schema_version, title, source{type,uri,lang}, metadata, sections[] }`,
mỗi `Section { id, title, level, blocks[] }`, mỗi block có `kind` thuộc:
`paragraph | heading | image | table | list | code | screen_recording`.
