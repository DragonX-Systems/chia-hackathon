from pathlib import Path

from adapters.mediumboom.astra_writer import write_last_mile_asm, write_self_asm


def test_self_writer_is_default(tmp_path, monkeypatch):
    monkeypatch.delenv("LAST_MILE_WRITER", raising=False)
    monkeypatch.delenv("ASTRA_API_KEY", raising=False)
    monkeypatch.delenv("POOLSIDE_API_KEY", raising=False)
    path = tmp_path / "t.S"
    note = write_last_mile_asm(path, ["riscv_alu.v:line:l125c24:case"], __import__("random").Random(0))
    text = path.read_text()
    assert note.startswith("self:")
    assert "srai" in text
    assert "lbu" in text
    assert "0xF000" in text
    assert "_start" in text


def test_self_suite_file_exists():
    assert (
        Path(__file__).resolve().parents[3]
        / "adapters/mediumboom/testbenches/verilator/last_mile_self.S"
    ).exists()
    write_self_asm  # imported
