"""专业序列、高配、挂职。

共同点：都不能和行政级别合并。
"""
from datetime import date

import pytest

from gongpu import leadership, proranks
from gongpu.appointment import LEVEL_ORDER
from gongpu.world import advance_to, new_game


def test_三样序列都有设立年代():
    """1986 年开局时，警衔、法官等级、检察官等级一个都不存在。"""
    assert proranks.kind_for("PUBLIC_SECURITY", date(1986, 7, 15)) == (None, None)
    assert proranks.kind_for("COURT", date(1986, 7, 15)) == (None, None)
    assert proranks.kind_for("PUBLIC_SECURITY", date(1993, 1, 1))[0] == "警衔"
    assert proranks.kind_for("COURT", date(1993, 1, 1)) == (None, None)
    assert proranks.kind_for("COURT", date(1998, 1, 1))[0] == "法官等级"
    assert proranks.kind_for("PROCURATORATE", date(1998, 1, 1))[0] == "检察官等级"


def test_警衔五等十三级_法官检察官四等十二级():
    k = proranks.kinds()
    assert len(k["警衔"]["grades"]) == 13
    assert len(k["法官等级"]["grades"]) == 12
    assert len(k["检察官等级"]["grades"]) == 12
    assert k["法官等级"]["grades"][-1]["name"] == "首席大法官"
    assert k["检察官等级"]["grades"][-1]["name"] == "首席大检察官"
    assert k["警衔"]["grades"][-1]["name"] == "总警监"


def test_一九九二年以前没有警衔():
    con, rng, clock, _ = new_game(seed="警衔年代")
    advance_to(con, clock, date(1991, 12, 31), rng)
    assert con.execute("SELECT count(*) FROM professional_rank").fetchone()[0] == 0
    advance_to(con, clock, date(1993, 12, 31), rng)
    kinds = {r[0] for r in con.execute(
        "SELECT DISTINCT kind FROM professional_rank")}
    assert kinds == {"警衔"}, "法官检察官等级 1997 年才有：%s" % kinds


@pytest.fixture(scope="module")
def played():
    con, rng, clock, pid = new_game(seed="专业序列")
    advance_to(con, clock, date(2005, 12, 31), rng)
    return con


def test_只有政法专业机关的人有序列(played):
    con = played
    bad = con.execute(
        "SELECT count(*) FROM professional_rank p "
        "JOIN character c ON c.id = p.character_id "
        "WHERE p.end_date IS NULL AND c.retired=0 AND EXISTS ("
        "  SELECT 1 FROM office_holding h "
        "  JOIN position_slot s ON s.id = h.position_slot_id "
        "  JOIN organization o ON o.id = s.organization_id "
        "  WHERE h.character_id = c.id AND h.end_date IS NULL "
        "  AND h.primary_position=1 "
        "  AND o.archetype NOT IN ('PUBLIC_SECURITY','STATE_SECURITY',"
        "                          'COURT','PROCURATORATE'))").fetchone()[0]
    assert bad == 0, "民政局的人不该有警衔"


def test_序列和行政级别是两条线(played):
    """同一个层次的人，有的有序列有的没有；序列名不是级别名。"""
    con = played
    rows = con.execute(
        "SELECT d.leadership_level AS lvl, p.grade FROM professional_rank p "
        "JOIN office_holding h ON h.character_id = p.character_id "
        "  AND h.end_date IS NULL AND h.primary_position=1 "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE p.end_date IS NULL").fetchall()
    assert rows
    for r in rows:
        assert r["grade"] not in LEVEL_ORDER, "序列名不能是行政级别"


def test_衔级按职务等级编制授予(played):
    """警衔条例第八条：一个职务层次对应一到三个衔级。"""
    con = played
    spec = proranks.kinds()["警衔"]
    for r in con.execute(
            "SELECT p.grade, d.leadership_level AS lvl FROM professional_rank p "
            "JOIN office_holding h ON h.character_id = p.character_id "
            "  AND h.end_date IS NULL AND h.primary_position=1 "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE p.end_date IS NULL AND p.kind='警衔'").fetchall():
        g = next(x for x in spec["grades"] if x["name"] == r["grade"])
        assert r["lvl"] in g["levels"], "%s 不该授予 %s" % (r["lvl"], r["grade"])


def test_调离本系统衔级不予保留但退休保留(played):
    """条例第十九条：离休退休保留警衔；调离警察工作岗位的不予保留。"""
    con = played
    assert con.execute(
        "SELECT count(*) FROM professional_rank "
        "WHERE exit_reason LIKE '调离%'").fetchone()[0] > 0
    kept = con.execute(
        "SELECT count(*) FROM professional_rank p "
        "JOIN character c ON c.id = p.character_id "
        "WHERE p.end_date IS NULL AND c.retired=1").fetchone()[0]
    assert kept > 0, "退休的要保留警衔"


def test_高配是职务和职级不一致(played):
    con = played
    rows = con.execute(
        "SELECT h.personal_rank AS pr, d.leadership_level AS lvl "
        "FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.end_date IS NULL AND h.personal_rank IS NOT NULL").fetchall()
    assert rows, "总该有几个高配的"
    for r in rows:
        assert LEVEL_ORDER[r["pr"]] > LEVEL_ORDER[r["lvl"]], \
            "高配就是职级高于职务层次"


def test_高配只给分管日常工作的副职不给兼任(played):
    """兼任的人级别来自主职，那不是高配。"""
    con = played
    bad = con.execute(
        "SELECT count(*) FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE h.end_date IS NULL AND h.personal_rank IS NOT NULL "
        "AND h.appointment_type='CONCURRENT'").fetchone()[0]
    assert bad == 0


def test_挂职有期限到期回原单位(played):
    con = played
    assert con.execute(
        "SELECT count(*) FROM office_holding "
        "WHERE exit_reason='挂职期满'").fetchone()[0] > 0
    # 挂职岗位都带期限
    assert con.execute(
        "SELECT count(*) FROM position_slot "
        "WHERE temporary=1 AND valid_to IS NULL").fetchone()[0] == 0


def test_挂职不占实际班子序列(played):
    """挂职副局长和真正的副局长，在库里从一开始就不是一回事。"""
    con = played
    bad = con.execute(
        "SELECT count(*) FROM position_slot WHERE temporary=1 "
        "AND leadership_order IS NOT NULL").fetchone()[0]
    assert bad == 0


def test_挂职挂的是副职而且是这个单位真有的职务(played):
    con = played
    for r in con.execute(
            "SELECT h.title_at_time AS t, s.organization_id AS org, "
            " d.name AS post FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.appointment_type='SECONDMENT'").fetchall():
        assert r["post"].startswith("副"), "挂职不挂一把手：%s" % r["t"]
        n = con.execute(
            "SELECT count(*) FROM position_slot s2 "
            "JOIN position_definition d2 ON d2.id = s2.position_definition_id "
            "WHERE s2.organization_id=? AND d2.name=? AND s2.temporary=0",
            (r["org"], r["post"])).fetchone()[0]
        assert n > 0, "%s 这个单位本来就没有这个职务" % r["t"]


def test_挂职在职务称谓里写明(played):
    con = played
    h = con.execute(
        "SELECT id FROM office_holding WHERE appointment_type='SECONDMENT' "
        "AND end_date IS NULL LIMIT 1").fetchone()
    if h:
        assert "（挂职）" in leadership.full_title(con, h["id"])
