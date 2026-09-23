"""人脉：认识了人，行动里就要找得到他。

关系原来只会衰减，既没有动作去维系，也没有动作去动用——
关系页是一张只读的摆设。这组测试盯住两头。
"""
import pytest

from gongpu import actions, relations
from gongpu.game import Game


@pytest.fixture(scope="module")
def played():
    g = Game.new("试玩", seed="人脉测试")
    for _ in range(40):
        g.advance(30)
        if g.tasks():
            break
    return g


def _contact(g, rank=("正科级", "副科级", "正处级")):
    return next(x for x in g.targets("托人") if x["rank"] in rank)


def test_关系里的人出现在行动对象里(played):
    known = {c["id"] for c in relations.contacts(played.con, played.player_id)}
    assert known, "总该认识几个人"
    for key in ("走动", "托人"):
        ids = {t["id"] for t in played.targets(key)}
        assert ids == known - {played.player_id}


def test_对象的级别和官职都写明了(played):
    """托谁办事、向谁请示，先得知道他说了算不算。"""
    for t in played.targets("托人"):
        assert t["rank"], t
        assert "·" in t["tag"], "要写清楚怎么认识的、在不在够得着的范围里"


def test_够不着的人也在人脉里(played):
    """党校同学调到别的县去了，组织关系上够不着，人还是那个人。"""
    reach = {t["tag"].split(" · ")[1] for t in played.targets("走动")}
    assert reach & {"本单位", "上级机关", "兄弟单位", "系统外"}


def test_走动一趟抵一年的衰减():
    g = Game.new("试玩", seed="走动")
    g.advance(400)
    who = g.targets("走动")[0]
    before = relations.get(g.con, g.player_id, who["id"])["familiarity"]
    g.act("走动", who["id"])
    after = relations.get(g.con, g.player_id, who["id"])["familiarity"]
    assert after - before == relations.VISIT_FAMILIARITY
    assert relations.VISIT_FAMILIARITY >= relations.DECAY_PER_YEAR


def test_交情不到开不了口(played):
    g = Game.new("试玩", seed="开不了口")
    for _ in range(40):
        g.advance(30)
        if g.tasks():
            break
    who = _contact(g)
    r = g.act("托人", who["id"], g.tasks()[0]["id"])
    assert r["effect"] == "走过场"
    assert str(actions.FAVOR_TRUST_FLOOR) in r["note"]


def test_托人折交情而且托不结事():
    g = Game.new("试玩", seed="托人")
    for _ in range(40):
        g.advance(30)
        if g.tasks():
            break
    who = _contact(g)
    for _ in range(9):
        g.act("走动", who["id"])
    trust = lambda: relations.get(g.con, g.player_id, who["id"])["working_trust"]
    assert trust() >= actions.FAVOR_TRUST_FLOOR
    used = 0
    for _ in range(30):
        g.advance(20)
        for it in g.tasks():
            before = trust()
            r = g.act("托人", who["id"], it["id"])
            assert r["effect"] != "办结", "托人是敲门，不是收口"
            assert trust() < before, "人情不是白使的"
            used += 1
        if used >= 2:
            break
    assert used, "总得托成一次"


def test_空着手托人也要折交情():
    g = Game.new("试玩", seed="空手")
    g.advance(400)
    who = g.targets("托人")[0]
    r = g.act("托人", who["id"])
    assert r["effect"] == "走过场"


def test_托人一处办结也没有():
    """找交情能把门敲开，活还是得自己干。"""
    from gongpu import tasks
    for name, spec in tasks.kinds().items():
        assert spec["actions"].get("托人") != "办结", name
        assert "托人" in spec["actions"], "%s 没写托人管不管用" % name
