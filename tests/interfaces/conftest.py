import pytest

from toffee import MemoryBackend, XCommClockBackend


@pytest.fixture(params=["memory", "native"])
def simulation(request):
    if request.param == "native":
        xspcomm = pytest.importorskip("xspcomm")
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)
    else:
        clock = object()
        backend = MemoryBackend(clock)
    try:
        yield clock, backend
    finally:
        backend.close()
