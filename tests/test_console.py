"""A6 (ADR-043): un log con caracteres no ASCII no puede abortar un análisis en una consola cp1252."""
import io

from due_diligence.utils.console import make_console_safe


def test_cp1252_stream_no_longer_raises_on_non_ascii_log():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    make_console_safe(stream)
    stream.write("no interest expense (weight ≈ 0 — irrelevant)\n")   # antes: UnicodeEncodeError
    stream.flush()
    assert b"weight ? 0" in raw.getvalue()


def test_utf8_stream_is_left_untouched():
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    make_console_safe(stream)
    assert stream.errors == "strict"
