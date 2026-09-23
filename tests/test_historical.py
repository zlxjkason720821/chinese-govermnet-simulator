"""Historical Test：年代正确性（技术文档 §67、§68）。

这六条直接抄自 §68，是制度仿真的底线。
"""
from datetime import date, timedelta
import pytest
from gongpu import rules


def test_1986_无职级并行():
    """1986: 不得存在一级科员职级。"""
    r = rules.resolve(date(1986, 6, 1))
    assert r.rank_parallel is False
    assert r.civil_service is None, "1986 年公务员制度尚未建立"


def test_1990_无监察委员会():
    """1990: 不得存在监察委员会。"""
    assert rules.resolve(date(1990, 6, 1)).supervision_commission is False


def test_1994_必须是1993公务员体系():
    """1994: 必须存在 1993 公务员体系。"""
    assert rules.resolve(date(1994, 6, 1)).civil_service == "CivilServiceRules1993"


def test_2010_仍有非领导职务():
    """2010: 仍使用非领导职务。"""
    r = rules.resolve(date(2010, 6, 1))
    assert r.non_leadership_positions is True
    assert r.rank_parallel is False


def test_2018_建立监察委员会():
    """2018: 建立监察委员会。"""
    assert rules.resolve(date(2018, 6, 1)).supervision_commission is True
    # 改革前一天不能已经有
    assert rules.resolve(date(2018, 1, 1)).supervision_commission is False


def test_2020_职务职级并行():
    """2020: 必须使用职务职级并行。"""
    r = rules.resolve(date(2020, 6, 1))
    assert r.rank_parallel is True
    assert r.non_leadership_positions is False, "套转后不再使用非领导职务"


def test_era覆盖无缝且无重叠():
    """§22 每一天都必须恰好命中一个制度版本，不能有空档或双重适用。"""
    prev_hi = None
    for era_id, lo, hi, _ in rules.ERAS:
        assert lo <= hi, era_id
        if prev_hi is not None:
            assert lo == prev_hi + timedelta(days=1), f"{era_id} 与上一个 Era 之间有空档或重叠"
        prev_hi = hi
    assert rules.ERAS[0][1] == date(1986, 1, 1)


def test_开局之前没有制度():
    """世界起点是 1986，之前的日期应当明确报错而不是悄悄返回 1986 规则。"""
    with pytest.raises(ValueError):
        rules.resolve(date(1985, 12, 31))
