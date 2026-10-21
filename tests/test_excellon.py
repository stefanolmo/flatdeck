import os

import numpy as np

from flatdeck.excellon import parse_excellon

HERE = os.path.dirname(__file__)
REAL = os.path.join(HERE, "..", "examples", "TrafficLight_drill.txt")


def test_real_file_counts_and_units():
    d = parse_excellon(open(REAL).read())
    assert {n: len(t.holes) for n, t in d.tools.items()} == {100: 2, 101: 10, 102: 32, 103: 12}
    assert abs(d.tools[101].dia - 0.031497 * 25.4) < 1e-6
    assert d.hole_count() == 56 and not d.warnings
    # first hole: X023459 Y008566 in a 2.4 inch file
    assert np.allclose(d.tools[100].holes[0], [2.3459 * 25.4, 0.8566 * 25.4])


def test_metric_with_decimal_and_trailing_zero_flag():
    txt = "M48\nMETRIC,TZ\nT1C0.8\n%\nT1\nX1.5Y2.5\nX012500Y001000\nM30\n"
    d = parse_excellon(txt)
    h = d.tools[1].holes
    assert np.allclose(h[0], [1.5, 2.5]) and np.allclose(h[1], [12.5, 1.0])


def test_leading_zero_flag_pads_right():
    d = parse_excellon("M48\nMETRIC,LZ\nT1C1\n%\nT1\nX1Y25\nM30\n")
    assert np.allclose(d.tools[1].holes[0], [100.0, 250.0])


def test_slots_g85_and_routed():
    txt = "M48\nMETRIC\nT1C1.0\nT2C0.8\n%\nT1\nX0.0Y0.0G85X5.0Y0.0\nT2\nG00X1.0Y1.0\nM15\nG01X1.0Y4.0\nM16\nM30\n"
    d = parse_excellon(txt)
    assert len(d.tools[1].slots) == 1 and np.allclose(d.tools[1].slots[0], [[0, 0], [5, 0]])
    assert np.allclose(d.tools[2].slots[0], [[1, 1], [1, 4]])


def test_undefined_tool_and_empty_file_warn():
    d = parse_excellon("M48\nMETRIC\n%\nT5\nX1Y1\nM30\n")
    assert d.warnings
