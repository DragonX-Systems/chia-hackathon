import pytest

from verification.ibex.qualify_formal import classify


@pytest.mark.parametrize("mode,native,code,expected", [
    ("cover", "PASS 0 0", 0, "COVER_REACHED"),
    ("prove", "PASS 0 0", 0, "PROVEN_UNREACHABLE"),
    ("cover", "FAIL 2 0", 2, "BOUNDED_UNREACHED"),
    ("prove", "FAIL 2 0", 2, "COUNTEREXAMPLE"),
    ("prove", "PASS", 16, "ERROR"),
    ("prove", "", 0, "ERROR"),
    ("prove", "UNKNOWN 4 0", 4, "UNKNOWN"),
    ("cover", "TIMEOUT", -1, "TIMEOUT"),
])
def test_formal_status_preserves_scope(mode, native, code, expected):
    assert classify(mode, native, code) == expected
