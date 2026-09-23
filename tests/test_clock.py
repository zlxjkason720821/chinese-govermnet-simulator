"""世界时钟（技术文档 §27）。真实日期，不是回合数。"""
from datetime import date
from gongpu.clock import SimulationClock, add_months


def test_月末对齐():
    assert add_months(date(1986, 1, 31), 1) == date(1986, 2, 28)
    assert add_months(date(1988, 1, 31), 1) == date(1988, 2, 29)   # 闰年
    assert add_months(date(1986, 12, 15), 1) == date(1987, 1, 15)  # 跨年
    assert add_months(date(1986, 3, 31), -1) == date(1986, 2, 28)  # 倒推


def test_推进返回跨越区间():
    """§28 调度器要靠这个区间判断哪些到期事项该触发。"""
    c = SimulationClock(date(1986, 7, 15))
    assert c.advance(days=1) == (date(1986, 7, 15), date(1986, 7, 16))
    assert c.advance(weeks=2)[1] == date(1986, 7, 30)
    assert c.advance(months=1)[1] == date(1986, 8, 30)
    assert c.advance(years=1)[1] == date(1987, 8, 30)


def test_长期推进不漂移():
    """逐月推进 40 年后必须正好落在 2026 年，不能累积误差。"""
    c = SimulationClock(date(1986, 1, 1))
    for _ in range(40 * 12):
        c.advance(months=1)
    assert c.date == date(2026, 1, 1)
