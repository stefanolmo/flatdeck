

def test_simplify_keeps_ends_and_shape():
    import numpy as np
    from flatdeck import geom
    a = np.array([[0, 0], [1, 0.001], [2, 0], [3, 0], [3, 2]], float)
    s = geom.simplify(a, 0.01)
    assert s.tolist() == [[0, 0], [3, 0], [3, 2]]
    assert geom.simplify(a, 0).shape == a.shape
