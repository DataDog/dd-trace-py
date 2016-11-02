

from ddtrace import writer

def test_q():
    q = writer.Q(3)
    assert q.add(1)
    assert q.add(2)
    assert q.add(3)
    assert q.size() == 3
    assert not q.add(4)
    assert q.size() == 3

    assert len(q.pop()) == 3
    assert q.size() == 0
