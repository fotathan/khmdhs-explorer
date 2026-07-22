"""Local (Tesseract) table reconstruction: TSV word boxes → grid. Pure — no
Tesseract binary or DB needed (operates on synthetic TSV)."""
import local_ocr


def _tsv(rows):
    """Build a Tesseract-style TSV from (text, left, top) triples at height 20."""
    header = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
              "\tleft\ttop\twidth\theight\tconf\ttext")
    lines = [header]
    for i, (txt, x, y) in enumerate(rows):
        lines.append(f"5\t1\t1\t1\t1\t{i}\t{x}\t{y}\t80\t20\t90\t{txt}")
    return "\n".join(lines)


def test_reconstructs_three_by_three_grid():
    tsv = _tsv([
        ("Περιγραφή", 100, 100), ("CPV", 400, 100), ("Αξία", 700, 100),
        ("Χαρτί", 100, 150), ("30197630", 400, 150), ("1200,50", 700, 150),
        ("Μελάνι", 100, 200), ("30192113", 400, 200), ("980,00", 700, 200),
    ])
    grid = local_ocr._reconstruct_table(local_ocr._parse_tsv_words(tsv))
    assert len(grid) == 3
    assert all(len(r) == 3 for r in grid)
    assert grid[0] == ["Περιγραφή", "CPV", "Αξία"]
    assert grid[1][1] == "30197630"
    assert grid[2][2] == "980,00"


def test_multi_word_cell_is_joined():
    # two words in the same row+column should join into one cell
    tsv = _tsv([
        ("Είδος", 100, 100), ("Τιμή", 500, 100),
        ("Α4", 100, 150), ("Χαρτί", 160, 150), ("5,00", 500, 150),
    ])
    grid = local_ocr._reconstruct_table(local_ocr._parse_tsv_words(tsv))
    assert grid[1][0] == "Α4 Χαρτί"
    assert grid[1][1] == "5,00"


def test_low_confidence_and_empty_words_skipped():
    header = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
              "\tleft\ttop\twidth\theight\tconf\ttext")
    lines = [
        header,
        "5\t1\t1\t1\t1\t0\t100\t100\t80\t20\t90\tKept",
        "5\t1\t1\t1\t1\t1\t400\t100\t80\t20\t90\tAlso",
        "5\t1\t1\t1\t1\t2\t100\t100\t80\t20\t-1\t",       # non-text layout row
        "4\t1\t1\t1\t1\t0\t0\t0\t0\t0\t-1\t",             # block row, no text
    ]
    words = local_ocr._parse_tsv_words("\n".join(lines))
    assert [w["text"] for w in words] == ["Kept", "Also"]


def test_no_columns_returns_empty():
    # a single column (no horizontal gaps) is not a table
    grid = local_ocr._reconstruct_table(local_ocr._parse_tsv_words(_tsv([
        ("A", 100, 100), ("B", 100, 150), ("C", 100, 200), ("D", 100, 250),
    ])))
    assert grid == []


def test_ocr_image_table_none_when_disabled(monkeypatch):
    monkeypatch.setattr(local_ocr, "enabled", lambda: False)
    assert local_ocr.ocr_image_table(b"\x89PNG...") is None
