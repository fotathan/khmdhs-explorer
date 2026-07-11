"""/version exposes OCR capability so 'works locally, fails on prod' is
diagnosable (tesseract binary, Greek data, Anthropic key)."""


def test_version_reports_ocr(client):
    r = client.get("/version")
    assert r.status_code == 200
    ocr = r.json().get("ocr")
    assert ocr is not None
    assert set(ocr) >= {"tesseract", "greek", "anthropic"}
    assert all(isinstance(ocr[k], bool) for k in ("tesseract", "greek", "anthropic"))
