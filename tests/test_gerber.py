import math

import numpy as np

from flatdeck import geom
from flatdeck.gerber import outline_loops, parse_gerber

HEAD = "%FSLAX24Y24*%\n%MOMM*%\n"


def area(layer):
    return geom.total_area(layer.polys)


def test_circle_and_rect_flash():
    g = parse_gerber(HEAD + "%ADD10C,1.0*%\n%ADD11R,2.0X1.0*%\nD10*\nX00000Y00000D03*\nD11*\nX100000Y00000D03*\nM02*\n")
    assert len(g.polys) == 2
    assert abs(area(g) - (math.pi * 0.25 + 2.0)) < 0.01
    x0, y0, x1, y1 = g.bounds()
    assert abs(x0 + 0.5) < 1e-3 and abs(x1 - 11.0) < 1e-3


def test_leading_zero_omission_and_inch():
    g = parse_gerber("%FSLAX24Y24*%\n%MOIN*%\n%ADD10C,0.1*%\nD10*\nX10000Y0D02*\nX30000Y0D01*\nM02*\n")
    x0, _, x1, _ = g.bounds()
    assert abs(x0 - (1.0 * 25.4 - 0.05 * 25.4)) < 1e-3
    assert abs(x1 - (3.0 * 25.4 + 0.05 * 25.4)) < 1e-3


def test_trailing_zero_omission():
    g = parse_gerber("%FSTAX24Y24*%\n%MOMM*%\n%ADD10C,0.1*%\nD10*\nX01Y0D02*\nX02Y0D01*\nM02*\n")   # X01 -> 01.0000, X02 -> 02.0000
    x0, _, x1, _ = g.bounds()
    assert abs(x0 - 0.95) < 1e-3 and abs(x1 - 2.05) < 1e-3


def test_trace_is_stadium():
    g = parse_gerber(HEAD + "%ADD10C,0.5*%\nD10*\nX00000Y00000D02*\nX100000Y00000D01*\nM02*\n")
    assert abs(area(g) - (10 * 0.5 + math.pi * 0.25 ** 2)) < 0.01
    assert len(g.strokes) == 1


def test_region_with_hole_by_clear_polarity():
    txt = HEAD + ("G36*\nX00000Y00000D02*\nG01X100000Y00000D01*\nX100000Y100000D01*\nX00000Y100000D01*\nX00000Y00000D01*\nG37*\n"
                  "%LPC*%\n%ADD10C,2.0*%\nD10*\nX50000Y50000D03*\n%LPD*%\nM02*\n")
    g = parse_gerber(txt)
    assert abs(area(g) - (100 - math.pi)) < 0.05
    assert len(g.polys) == 2          # outer + hole


def test_full_circle_arc():
    txt = HEAD + "%ADD10C,0.2*%\nD10*\nG75*\nX50000Y00000D02*\nG03X50000Y00000I-50000J0D01*\nM02*\n"
    g = parse_gerber(txt)
    # ring of mean radius 5 and width 0.2
    assert abs(area(g) - math.pi * (5.1 ** 2 - 4.9 ** 2)) < 0.1


def test_quarter_arc_single_quadrant():
    txt = HEAD + "%ADD10C,0.1*%\nD10*\nG74*\nX50000Y00000D02*\nG03X00000Y50000I50000J00000D01*\nM02*\n"
    g = parse_gerber(txt)
    x0, y0, x1, y1 = g.bounds()
    assert abs(x0 + 0.05) < 0.01 and abs(y1 - 5.05) < 0.01 and abs(x1 - 5.05) < 0.01


def test_macro_rounded_rect():
    txt = HEAD + ("%AMRR*21,1,$1-$3-$3,$2,0,0,0*21,1,$1,$2-$3-$3,0,0,0*1,1,$3+$3,$1/2-$3,$2/2-$3*1,1,$3+$3,-$1/2+$3,$2/2-$3*"
                  "1,1,$3+$3,-$1/2+$3,-$2/2+$3*1,1,$3+$3,$1/2-$3,-$2/2+$3*%\n%ADD10RR,4X2X0.5*%\nD10*\nX00000Y00000D03*\nM02*\n")
    g = parse_gerber(txt)
    assert not g.warnings
    assert abs(area(g) - (8 - (4 - math.pi) * 0.25)) < 0.02


def test_step_repeat():
    txt = HEAD + "%ADD10C,1.0*%\nD10*\n%SRX3Y2I5.0J4.0*%\nX00000Y00000D03*\n%SR*%\nM02*\n"
    g = parse_gerber(txt)
    assert len(g.polys) == 6
    x0, y0, x1, y1 = g.bounds()
    assert abs(x1 - 10.5) < 1e-3 and abs(y1 - 4.5) < 1e-3


def test_obround_and_polygon_apertures():
    g = parse_gerber(HEAD + "%ADD10O,3X1*%\n%ADD11P,2X6*%\nD10*\nX00000Y00000D03*\nD11*\nX100000Y00000D03*\nM02*\n")
    assert abs(area(g) - ((2 * 1 + math.pi * 0.25) + 3 * math.sqrt(3) / 2)) < 0.05
    assert len(g.polys) == 2


def test_aperture_hole():
    g = parse_gerber(HEAD + "%ADD10C,2X1*%\nD10*\nX00000Y00000D03*\nM02*\n")
    assert abs(area(g) - math.pi * (1 - 0.25)) < 0.02 and len(g.polys) == 2


def test_missing_aperture_warns():
    g = parse_gerber(HEAD + "D12*\nX0Y0D03*\nM02*\n")
    assert any("D12" in w for w in g.warnings)


def test_outline_loops_chain_and_open():
    txt = HEAD + "%ADD10C,0.1*%\nD10*\nX00000Y00000D02*\nX100000Y00000D01*\nX100000Y50000D01*\nX00000Y50000D01*\nX00000Y00000D01*\nM02*\n"
    closed, opened = outline_loops(parse_gerber(txt).strokes)
    assert len(closed) == 1 and not opened
    assert abs(abs(geom.area(closed[0])) - 50) < 1e-6
    # same rectangle as four separate lines in shuffled direction
    segs = [(0.1, np.array([[0, 0], [10, 0.0]])), (0.1, np.array([[10, 5], [10, 0.0]])),
            (0.1, np.array([[10, 5], [0, 5.0]])), (0.1, np.array([[0, 5], [0, 0.0]]))]
    closed, opened = outline_loops(segs)
    assert len(closed) == 1 and not opened


def test_contiguous_draws_merge_into_one_stroke():
    lines = "".join(f"X{i * 10000:06d}Y00000D01*\n" for i in range(1, 200))
    g = parse_gerber(HEAD + "%ADD10C,0.2*%\nD10*\nX00000Y00000D02*\n" + lines + "M02*\n")
    assert len(g.strokes) == 1 and len(g.strokes[0][1]) == 200
    assert abs(area(g) - (199 * 0.2 * 1.0 + math.pi * 0.01)) < 0.05
    # a move (D02) or a new aperture breaks the polyline
    g = parse_gerber(HEAD + "%ADD10C,0.2*%\nD10*\nX00000Y00000D02*\nX10000Y00000D01*\nX30000Y00000D02*\nX40000Y00000D01*\nM02*\n")
    assert len(g.strokes) == 2
