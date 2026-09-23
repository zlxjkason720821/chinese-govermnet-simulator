"""汇报对象的范围：一个科员找不到总书记。"""
from gongpu import actions
from gongpu.world import new_game


def test_科员的请示对象不含中央领导():
    con, rng, clock, pid = new_game(seed="汇报范围")
    names = [t["name"] for t in actions.targets_for(con, pid, "请示")]
    blob = " ".join(names)
    for who in ("总书记", "总理", "委员长", "国家主席", "中央纪委书记"):
        assert who not in blob, "科员汇报不到%s：%s" % (who, names)
    assert names, "总得有人可以汇报"


def test_请示对象顺着隶属关系往上一两级():
    con, rng, clock, pid = new_game(seed="汇报范围")
    mine = con.execute(
        "SELECT ps.organization_id FROM office_holding h "
        "JOIN position_slot ps ON ps.id = h.position_slot_id "
        "WHERE h.character_id=? AND h.end_date IS NULL", (pid,)).fetchone()[0]
    chain = set(actions._reporting_chain(con, mine, hops=2))
    for t in actions.targets_for(con, pid, "请示"):
        org = con.execute(
            "SELECT ps.organization_id FROM office_holding h "
            "JOIN position_slot ps ON ps.id = h.position_slot_id "
            "WHERE h.character_id=? AND h.end_date IS NULL AND h.primary_position=1",
            (t["id"],)).fetchone()
        if org:
            assert org[0] in chain, "%s 不在你的隶属链上" % t["name"]


def test_起步不在人大政协():
    """人大政协是职业后段的去处，新人分到那儿等于开局就进了养老院。"""
    for seed in ("甲", "乙", "丙", "丁", "戊"):
        con, rng, clock, pid = new_game(seed=seed)
        org = con.execute(
            "SELECT o.name, o.organization_type, o.system_type FROM office_holding h "
            "JOIN position_slot ps ON ps.id = h.position_slot_id "
            "JOIN organization o ON o.id = ps.organization_id "
            "WHERE h.character_id=? AND h.end_date IS NULL", (pid,)).fetchone()
        assert org["organization_type"] not in ("PEOPLES_CONGRESS", "CPPCC"), org["name"]
        assert org["system_type"] not in ("人大", "政协"), org["name"]
