"""全国省级行政区（§72 背景人口抽象）。"""
from datetime import date

import pytest

from gongpu import regions
from gongpu.world import advance_to, new_game


@pytest.fixture(scope="module")
def world():
    return new_game(seed="区划测试")


def test_开局就有三十个省级行政区(world):
    con = world[0]
    names = {r["full"] for r in regions.listing(con)}
    assert "北京市" in names and "广东省" in names and "西藏自治区" in names
    # 1986 年这四个还不存在
    assert "海南省" not in names
    assert "重庆市" not in names
    assert "香港特别行政区" not in names
    assert "澳门特别行政区" not in names


def test_区划带年代(world):
    """拿 2026 的地图倒推 1986，和拿 2008 的党校条例倒推 1986 是一个性质的错。"""
    assert {p["name"] for p in regions.existing_on(date(1986, 1, 1))} \
        .isdisjoint({"海南省", "重庆市", "香港特别行政区", "澳门特别行政区"})
    assert "海南省" in {p["name"] for p in regions.existing_on(date(1988, 5, 1))}
    assert "重庆市" in {p["name"] for p in regions.existing_on(date(1997, 4, 1))}
    assert "香港特别行政区" in {p["name"] for p in regions.existing_on(date(1997, 8, 1))}
    assert "澳门特别行政区" in {p["name"] for p in regions.existing_on(date(2000, 1, 1))}


def test_中途设立的区划到了日子才出现():
    con, rng, clock, _ = new_game(seed="建省")
    assert "海南省" not in {r["full"] for r in regions.listing(con)}
    advance_to(con, clock, date(1988, 12, 31), rng)
    assert "海南省" in {r["full"] for r in regions.listing(con)}
    ev = con.execute("SELECT date FROM world_event WHERE event_type='region_established'"
                     " ORDER BY id LIMIT 1").fetchone()
    assert ev[0] == "1988-04-26"


def test_港澳台不设党政领导班子(world):
    con = world[0]
    for name in ("台湾省",):
        row = con.execute("SELECT id FROM organization WHERE name=?", (name,)).fetchone()
        assert row is not None, "区划表里要有它"
        n = con.execute("SELECT count(*) FROM position_slot WHERE organization_id=?",
                        (row["id"],)).fetchone()[0]
        assert n == 0, "一国两制，不设省级党委政府建制"


def test_外省是背景不逐环节模拟(world):
    """§72：没有直接参与玩家世界的干部先作为背景存在。"""
    con = world[0]
    n = con.execute("SELECT count(*) FROM organization WHERE simulated=0 "
                    "AND admin_level='PROVINCIAL'").fetchone()[0]
    assert n > 50, "外省的班子要存在"
    # 玩家那条线仍然逐环节模拟
    assert con.execute("SELECT count(*) FROM organization WHERE simulated=1 "
                       "AND admin_level='COUNTY'").fetchone()[0] > 0


def test_外省的位子上有人(world):
    con = world[0]
    empty = [r["name"] for r in con.execute(
        "SELECT o.name FROM organization o WHERE o.simulated=0 "
        "AND o.organization_type != 'REGION' "
        "AND NOT EXISTS (SELECT 1 FROM position_slot s "
        "  WHERE s.organization_id=o.id AND s.status='OCCUPIED')")]
    assert not empty, "背景省份也不能是空架子：%s" % empty[:3]
