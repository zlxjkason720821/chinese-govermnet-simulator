"""人物生成（技术文档 §74）。核心是年代正确性。"""
from datetime import date

import pytest

from gongpu.people import (EDUCATION_BY_COHORT, GIVEN_BY_COHORT, generate,
                           make_name, work_start_year)
from gongpu.rng import RandomService

ON = date(1986, 7, 15)


@pytest.fixture
def w():
    return RandomService("people")["world"]


def test_1986年的年轻人不能有2020年代学历结构(w):
    """§74 点名禁止的那一条：1986 年 22 岁的人不得有 2020 年代典型教育履历。"""
    got = {generate(w, 1964, ON)["education_level"] for _ in range(300)}
    assert "博士" not in got, "1964 年出生的人在 1986 年不可能是博士"
    later = {generate(w, 1995, date(2020, 1, 1))["education_level"] for _ in range(300)}
    assert "硕士" in later, "90 后队列应当出现硕士"


def test_学历队列覆盖所有出生年份():
    prev = None
    for lo, hi, _ in EDUCATION_BY_COHORT:
        assert lo <= hi
        if prev is not None:
            assert lo == prev + 1, "学历队列之间有空档"
        prev = hi


def test_姓名分性别且带年代(w):
    old_m = {make_name(w, 1950, "M") for _ in range(200)}
    new_f = {make_name(w, 1995, "F") for _ in range(200)}
    assert any("建" in n or "红" in n or "卫" in n for n in old_m)
    assert not any("梓" in n for n in old_m), "50 年代出生的人不该叫梓某"
    assert any(n[-1] in "涵怡琪萱诺玥" for n in new_f)


def test_参加工作年份由学历倒推():
    assert work_start_year("高中", 1960) < work_start_year("本科", 1960)
    assert work_start_year("博士", 1960) == 1988


def test_选调生学历不会低于大专(w):
    for _ in range(200):
        p = generate(w, 1963, ON, "SELECTED_GRADUATE")
        assert p["education_level"] not in ("高中", "中专")


def test_入党日期不早于成年(w):
    for _ in range(300):
        p = generate(w, 1960, ON)
        if p["party_join_date"]:
            assert date.fromisoformat(p["party_join_date"]).year >= 1960 + 18


def test_性格参数不外露为面板数值(w):
    """§32 性格只影响概率。这里只保证它是完整的一组 0-1 数值。"""
    p = generate(w, 1960, ON)
    assert len(p["personality"]) == 8
    assert all(0.0 <= v <= 1.0 for v in p["personality"].values())


def test_生成可复现():
    a = RandomService("same")["world"]
    b = RandomService("same")["world"]
    assert generate(a, 1960, ON) == generate(b, 1960, ON)
