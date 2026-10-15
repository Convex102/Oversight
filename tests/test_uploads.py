import pytest
from fastapi import HTTPException
from app.hardening import check_uploads, safe_filename


class F:
    def __init__(self, n): self.filename = n


def test_ok_and_none():
    check_uploads([F("a.xlsx"), F("b.CSV")], None, F("c.pdf"))


def test_bad_ext():
    with pytest.raises(HTTPException) as e:
        check_uploads([F("x.exe")])
    assert e.value.status_code == 415


def test_too_many():
    with pytest.raises(HTTPException) as e:
        check_uploads([F("a.csv")] * 21)
    assert e.value.status_code == 413


def test_safe_filename():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("..") == "untitled"
    assert safe_filename("a\\b.csv") == "b.csv"
