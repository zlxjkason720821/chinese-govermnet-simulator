"""领导秘书的出路（蓝图十二）。

秘书不跟着领导走。领导动了，秘书按级别安排——
怎么安排，取决于领导是怎么走的。
"""
from datetime import date

import pytest

from gongpu import secretary
from gongpu.appointment import LEVEL_ORDER
from gongpu.world import advance_to, new_game


@pytest.fixture(scope="module")
def world():
    con, rng, clock, pid = new_game(seed="秘书制度")
    advance_to(con, clock, date(2005, 12, 31), rng)
    return con


def test_每个秘书岗都指明服务谁():
    """"书记秘书""市长秘书"不是全国统一的行政职务名称，是一种工作关系。

    人事表上写的是办公厅某个处的职务，服务谁另记一笔。各地"秘书一处"
    服务的对象也不一样，所以不能靠处室名推断。
    """
    from gongpu import tracks
    con, _, _, _ = new_game(seed="秘书编制")
    rows = con.execute(
        "SELECT s.id, d.name AS post FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.serves_slot_id IS NOT NULL").fetchall()
    assert rows, "市省中央三级都该有领导秘书"
    # 正式职务是真实的办公厅职务，不是"书记秘书"这种非正式称呼
    for r in rows:
        assert not r["post"].endswith("书记秘书"), r["post"]
        assert r["post"] not in ("市长秘书", "副市长秘书", "中央领导秘书"), r["post"]
        assert tracks.is_secretary_slot(con, r["id"])
        assert tracks.serves(con, r["id"]), "必须写明服务谁"
    served = {tracks.serves(con, r["id"]) for r in rows}
    assert any("总书记" in x for x in served)
    assert any("省委书记" in x for x in served)
    assert any("市委书记" in x for x in served)


def test_秘书级别和下放的主政岗接得上():
    """外放升一级，那一级必须真有主政岗位，否则这条路是断的。"""
    con, _, _, _ = new_game(seed="接得上")
    for r in con.execute(
            "SELECT DISTINCT d.leadership_level AS lvl FROM position_slot s "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE s.serves_slot_id IS NOT NULL"):
        up = secretary._level_up(r["lvl"])
        assert up in secretary.GOVERNING, "%s 升一级是 %s，没定主政岗" % (r["lvl"], up)


def test_领导升迁秘书下放主政并提一级(world):
    import json
    ups = [json.loads(r["data"]) for r in world.execute(
        "SELECT data FROM world_event WHERE event_type='secretary_settled'")]
    assert ups, "二十年里总该有领导动的时候"
    good = [d for d in ups if d["fate"] == "升迁" and "提一级" in d["why"]]
    assert good, "领导升迁，秘书要提一级：%s" % [d["why"] for d in ups[:5]]


def test_秘书不会被安排去当另一个秘书(world):
    """从秘书到秘书不叫出路，那是原地打转。"""
    import json
    for r in world.execute(
            "SELECT data FROM world_event WHERE event_type='secretary_settled'"):
        to = json.loads(r["data"])["to"]
        assert to is None or not to.endswith("秘书"), to


def test_升迁的领导不会把秘书发落到政协(world):
    import json
    for r in world.execute(
            "SELECT data FROM world_event WHERE event_type='secretary_settled'"):
        d = json.loads(r["data"])
        if d["fate"] == "升迁" and d["to"]:
            assert "政协" not in d["to"] and "人大" not in d["to"], d


def test_领导退休是平调不是提拔(world):
    import json
    for r in world.execute(
            "SELECT data FROM world_event WHERE event_type='secretary_settled'"):
        d = json.loads(r["data"])
        if d["fate"] in ("退休", "去世"):
            assert "提一级" not in d["why"], d


def test_牵连的去人大政协():
    """领导被查，秘书自己也有事，那就不再安排实职。"""
    con, rng, clock, pid = new_game(seed="牵连")
    row = con.execute(
        "SELECT s.id, s.holder_id, d.leadership_level AS lvl FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.serves_slot_id IS NOT NULL AND s.status='OCCUPIED' "
        "AND d.leadership_level='副处级' LIMIT 1").fetchone()
    assert row, "总有一个副处级的秘书"
    # 腾一个人大的位子出来，保证有地方安置
    free = con.execute(
        "SELECT s.id FROM position_slot s "
        "JOIN organization o ON o.id = s.organization_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE o.organization_type IN ('PEOPLES_CONGRESS','CPPCC') "
        "AND d.leadership_level='副处级' LIMIT 1").fetchone()
    con.execute("UPDATE position_slot SET status='VACANT',holder_id=NULL WHERE id=?",
                (free["id"],))
    dest, why = secretary.outlet(con, row["holder_id"], row["lvl"],
                                 "被查", hurt=True)
    assert "牵连" in why
    kind = con.execute(
        "SELECT o.organization_type FROM position_slot s "
        "JOIN organization o ON o.id = s.organization_id WHERE s.id=?",
        (dest,)).fetchone()[0]
    assert kind in secretary.RETIREMENT_HOME, kind


def test_没牵连的只是平调():
    con, rng, clock, pid = new_game(seed="没牵连")
    row = con.execute(
        "SELECT s.holder_id, d.leadership_level AS lvl FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.serves_slot_id IS NOT NULL AND s.status='OCCUPIED' LIMIT 1").fetchone()
    dest, why = secretary.outlet(con, row["holder_id"], row["lvl"], "被查", hurt=False)
    assert "平调" in why and "牵连" not in why
    lvl = con.execute(
        "SELECT d.leadership_level FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.id=?", (dest,)).fetchone()[0]
    assert LEVEL_ORDER[lvl] == LEVEL_ORDER[row["lvl"]], "平调就是级别不变"


def test_秘书经历不是台阶():
    """蓝图十二：不能把它做成'当过秘书所以升得快'。"""
    from gongpu import tracks
    con, _, _, _ = new_game(seed="非台阶")
    slot = con.execute(
        "SELECT id FROM position_slot WHERE serves_slot_id IS NOT NULL LIMIT 1"
    ).fetchone()["id"]
    assert tracks.is_secretary_slot(con, slot)
    assert "不是台阶" in tracks.secretary()["caution"]


def test_秘书身份看服务关系不看职务名():
    """不存在全国统一的"秘书一处 = 书记秘书"对应表。"""
    from gongpu import tracks
    con, _, _, _ = new_game(seed="不看名字")
    same = con.execute(
        "SELECT s.id, s.serves_slot_id FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE d.name='秘书一处副处长' ORDER BY s.id").fetchall()
    assert len(same) >= 2, "同名的秘书一处副处长应当不止一个"
    # 同一个职务名，服务对象各不相同——所以名字推不出服务谁
    served = {tracks.serves(con, r["id"]) for r in same if r["serves_slot_id"]}
    assert len(served) >= 2, served
