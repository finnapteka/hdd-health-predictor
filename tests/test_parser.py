"""Tests for SMART output parsing (offline, no disk needed)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hdd_parser import _parse_smart_attributes  # noqa: E402


SAMPLE = """
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  1 Raw_Read_Error_Rate     0x000f   118   099   006    Pre-fail  Always       -       201433
  7 Seek_Error_Rate         0x000f   076   060   030    Pre-fail  Always       -       12345678
 11 Calibration_Retry_Count 0x0012   100   100   000    Old_age   Always       -       5
"""


def test_parse_basic():
    attrs = _parse_smart_attributes(SAMPLE)
    assert attrs is not None
    assert attrs["attr1"] == 201433
    assert attrs["attr7"] == 12345678
    assert attrs["attr11"] == 5


def test_parse_seagate_48bit():
    sample = (
        "  7 Seek_Error_Rate         0x000f   076   060   030    Pre-fail  Always       -       "
        "1234567890123 (0)\n"
    )
    attrs = _parse_smart_attributes(sample)
    assert attrs is not None
    # 48-bit raw is preserved as-is; baseline normalization happens in trend_analyzer.
    assert attrs["attr7"] == 1234567890123


def test_parse_missing():
    assert _parse_smart_attributes("no attributes here") is None

def test_real_fixtures():
    """Parse real smartctl output captured from the host."""
    import pathlib
    import pytest
    base = pathlib.Path(__file__).parent / "fixtures"
    checked = 0
    for name in ("smartctl_sdc.txt", "smartctl_sdd.txt"):
        f = base / name
        if not f.exists() or f.stat().st_size < 100:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if "ATTRIBUTE_NAME" not in text:
            continue
        attrs = _parse_smart_attributes(text)
        assert attrs is not None, f"{name}: parser returned None"
        # Seek Error Rate (attr 7) must be present on Seagate HDDs
        assert attrs["attr7"] is not None, f"{name}: attr7 missing"
        checked += 1
    if checked == 0:
        pytest.skip("no usable fixtures present")

def test_real_fixtures_extended_attrs():
    """Seagate fixtures should expose attr1/attr7 and any of 187/193/197/198/240."""
    import pathlib
    import pytest
    base = pathlib.Path(__file__).parent / "fixtures"
    checked = 0
    for name in ("smartctl_sdc.txt", "smartctl_sdd.txt"):
        f = base / name
        if not f.exists() or f.stat().st_size < 100:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if "ATTRIBUTE_NAME" not in text:
            continue
        attrs = _parse_smart_attributes(text)
        assert attrs is not None
        assert attrs["attr1"] is not None, f"{name}: attr1 missing"
        assert attrs["attr7"] is not None, f"{name}: attr7 missing"
        # at least one Seagate-specific attribute should be present
        extra = [attrs[k] for k in ("attr187", "attr188", "attr193",
                                     "attr197", "attr198", "attr240")
                 if attrs.get(k) is not None]
        assert extra, f"{name}: no Seagate-specific attributes parsed"
        checked += 1
    if checked == 0:
        pytest.skip("no usable fixtures present")

def test_seagate_48bit_raw():
    """attr7 and attr188 on Seagate must be parsed as full 48-bit integers."""
    sample = """
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  7 Seek_Error_Rate         0x000f   065   060   030    Pre-fail  Always       -       34385099575
188 Command_Timeout         0x0032   100   094   000    Old_age   Always       -       4295032866
240 Head_Flying_Hours       0x0000   100   253   000    Old_age   Offline      -       23211 (103 48 0)
"""
    attrs = _parse_smart_attributes(sample)
    assert attrs is not None
    assert attrs["attr7"] == 34385099575
    assert attrs["attr188"] == 4295032866
    assert attrs["attr240"] == 23211


def test_skip_time_string_raw():
    """attr9 with '15678h+00m+...' must not crash and must be skipped."""
    sample = "  9 Power_On_Hours  0x0032  083  083  000  Old_age  Always  -  15678h+00m+00.000s\n"
    # attr9 is not in ATTR_IDS, so parser returns None
    assert _parse_smart_attributes(sample) is None

