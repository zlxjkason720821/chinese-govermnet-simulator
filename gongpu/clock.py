"""世界时钟（技术文档 §27）。用真实日期，不用"第 N 回合"。"""
from calendar import monthrange
from datetime import date, timedelta


def add_months(d, n):
    """月末对齐：1986-01-31 + 1 月 = 1986-02-28。"""
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


class SimulationClock:
    def __init__(self, start):
        self.date = start

    def advance(self, days=0, weeks=0, months=0, years=0):
        """返回推进跨过的 [旧日期, 新日期)，调度器据此触发到期事项（§28）。"""
        old = self.date
        d = add_months(old, months + years * 12)
        self.date = d + timedelta(days=days, weeks=weeks)
        return old, self.date
