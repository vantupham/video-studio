# Tesseract OCR (downloaded, not committed)

`scripts/fetch-tesseract.ps1` extracts `tesseract.exe` and its DLLs from the
UB-Mannheim Windows build and adds the "fast" `vie` / `eng` models to `tessdata/`.
Tauri installs this folder to `<install dir>/tesseract/` and the shell passes it to the
backend via `VIDEOSTUDIO_TESSERACT_DIR`; the bundled models are copied to the user's
models folder on first OCR, so scanned PDFs work offline.

During development on Linux/macOS the backend uses `tesseract` on `PATH`
(`apt install tesseract-ocr`) and downloads the models on first use.
