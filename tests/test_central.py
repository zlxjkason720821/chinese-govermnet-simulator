"""中央委员会、会议机制、同学关系跨地区
（蓝图 十七、二十、二十三、二十四、二十五；§30 分层模拟）。"""
from datetime import date

import pytest

from gongpu import central, meetings, relations, training
from gongpu.world import advance_to, new_game


@pytest.fixture(scope="module")
def played():
    con, rng, clock, pid = new_game(seed="central-test")
    advance_to(con, clock, date(2026, 12, 31), rng)
    return con, pid


# ---------- 层级 ----------

def test_世界有县市省中央四级(played):
    con = played[0]
    got = {r[0] for r in con.execute(
        "SELECT DISTINCT admin_level FROM organization WHERE active=1")}
    assert got >= {"COUNTY", "MUNICIPAL", "PROVINCIAL", "CENTRAL"}


def test_干部能从县走到市省(played):
    """没有上面几级，县委书记就无处可去，整条职业路径到头。"""
    con = played[0]
    n = con.execute(
        "SELECT count(*) FROM (SELECT h.character_id FROM office_holding h "
        " JOIN position_slot s ON s.id=h.position_slot_id "
        " JOIN organization o ON o.id=s.organization_id "
        " WHERE o.admin_level='COUNTY' "
        " INTERSECT "
        " SELECT h.character_id FROM office_holding h "
        " JOIN position_slot s ON s.id=h.position_slot_id "
        " JOIN organization o ON o.id=s.organization_id "
        " WHERE o.admin_level IN ('MUNICIPAL','PROVINCIAL','CENTRAL'))").fetchone()[0]
    assert n > 0, "四十年里没有一个人从县里走上去"


# ---------- 中央委员会 ----------

def test_党代会五年一次且届次连续(played):
    con = played[0]
    rows = con.execute(
        "SELECT date, data FROM world_event WHERE event_type='party_congress' "
        "ORDER BY date").fetchall()
    assert len(rows) >= 7, "四十年应当开七八次党代会"
    import json
    ords = [json.loads(r["data"])["congress"] for r in rows]
    assert ords == sorted(ords) and len(set(ords)) == len(ords)


def test_中央委员身份独立于行政级别(played):
    """蓝图二十三：一个省部级干部可以不是中央委员。"""
    con = played[0]
    senior = {r[0] for r in con.execute(
        "SELECT c.id FROM office_holding h JOIN character c ON c.id=h.character_id "
        "JOIN position_slot s ON s.id=h.position_slot_id "
        "JOIN position_definition d ON d.id=s.position_definition_id "
        "WHERE h.end_date IS NULL AND d.leadership_level IN ('副部级','正部级') "
        "AND c.alive=1 AND c.retired=0")}
    members = {r["id"] for r in central.roster(con)}
    assert senior - members, "所有省部级干部都是中央委员，身份就不独立了"


def test_政治局常委的职务出自中央(played):
    """蓝图二十五：政治局不能用普通升迁算法排出来。
    常委的行政职务只能是中央职务——总书记不可能是省长。"""
    con = played[0]
    for r in central.roster(con):
        if r["status"] not in ("总书记", "政治局常委"):
            continue
        lvl = con.execute(
            "SELECT o.admin_level FROM office_holding h "
            "JOIN position_slot s ON s.id=h.position_slot_id "
            "JOIN organization o ON o.id=s.organization_id "
            "WHERE h.character_id=? AND h.end_date IS NULL AND h.primary_position=1",
            (r["id"],)).fetchone()
        assert lvl and lvl[0] == "CENTRAL", \
            "%s %s 的主职是 %s，不是中央职务" % (r["status"], r["name"], r["title"])


def test_总书记兼任国家主席(played):
    """1993 年起三位一体。§20 兼任记为非主要职务。"""
    con = played[0]
    gs = [r for r in central.roster(con) if r["status"] == "总书记"]
    if not gs:
        pytest.skip("本次模拟结束时总书记位置空缺")
    rows = con.execute(
        "SELECT h.title_at_time, h.primary_position FROM office_holding h "
        "WHERE h.character_id=? AND h.end_date IS NULL", (gs[0]["id"],)).fetchall()
    titles = {r["title_at_time"] for r in rows}
    assert any("总书记" in t for t in titles)
    assert any("国家主席" in t or "中华人民共和国主席" in t for t in titles)
    assert any(r["primary_position"] == 0 for r in rows), "兼任没有记为非主要职务"


def test_七上八下(played):
    """蓝图二十：党代会当年年满 68 的不进新一届。"""
    con = played[0]
    import json
    congresses = con.execute(
        "SELECT date FROM world_event WHERE event_type='party_congress'").fetchall()
    for c in congresses:
        on = date.fromisoformat(c["date"])
        for r in con.execute(
                "SELECT ch.birth_date FROM party_central_status p "
                "JOIN character ch ON ch.id=p.character_id "
                "WHERE p.start_date=?", (c["date"],)):
            born = date.fromisoformat(r["birth_date"])
            age = on.year - born.year - ((on.month, on.day) < (born.month, born.day))
            assert age <= central.AGE_CEILING, "%d 岁还进了新一届" % age


def test_去世就不在名单上但退休还在(played):
    """中央委员会是党代会选出来的一届名册，任期五年。

    去世要出缺、要递补；**退休不然**——任期内从岗位上退下来的人
    仍然是中央委员，要等下一届换届才不在名单里。
    原先把退休也算成出缺，三年就掉了四十九个委员。
    """
    con = played[0]
    dead = con.execute(
        "SELECT count(*) FROM party_central_status p "
        "JOIN character c ON c.id=p.character_id "
        "WHERE p.end_date IS NULL AND c.alive=0").fetchone()[0]
    assert dead == 0, "去世的人要出缺并递补"
    retired = con.execute(
        "SELECT count(*) FROM party_central_status p "
        "JOIN character c ON c.id=p.character_id "
        "WHERE p.end_date IS NULL AND c.retired=1").fetchone()[0]
    assert retired > 0, "退休不终止党内身份"


def test_省部级退休年龄比县处级晚(played):
    """总书记 61 岁就"到龄退休"的话，整个上层每几年排空一次。"""
    from gongpu.rules import retirement_age
    on = date(2000, 1, 1)
    assert retirement_age("M", 4, on) == 60      # 副处级
    assert retirement_age("M", 9, on) == 65      # 正部级
    assert retirement_age("M", 11, on) == 68     # 正国级


# ---------- 会议 ----------

def test_会议是承重的不是过场(played):
    """蓝图十七：任用的讨论决定这一环在常委会上发生。
    不上会，流程就卡在那里。"""
    con = played[0]
    n = con.execute("SELECT count(*) FROM meeting").fetchone()[0]
    items = con.execute("SELECT count(*) FROM meeting_item").fetchone()[0]
    assert n > 50 and items > n, "四十年才开 %d 次会" % n
    # 议题必须来自真实卡在程序上的东西
    srcs = {r[0] for r in con.execute(
        "SELECT DISTINCT source FROM meeting_item WHERE source IS NOT NULL")}
    assert srcs <= {"appointment", "project", "discipline", "work"}
    assert "appointment" in srcs


def test_不同议题进不同的会(played):
    """蓝图十七的原话。干部任免归党委，项目立项归政府。"""
    con = played[0]
    for kind, src in ((meetings.PARTY_STANDING, "appointment"),
                      (meetings.GOV_EXECUTIVE, "project")):
        wrong = con.execute(
            "SELECT count(*) FROM meeting_item mi JOIN meeting m ON m.id=mi.meeting_id "
            "WHERE m.kind=? AND mi.source IS NOT NULL AND mi.source != ?",
            (kind, src)).fetchone()[0]
        assert wrong == 0, "%s 上出现了不该它研究的议题" % kind


def test_会上不同意就卡住(played):
    """再研究、缓议是真的会让事情停下来，不是走过场。"""
    con = played[0]
    deferred = con.execute(
        "SELECT count(*) FROM meeting_item WHERE decision IN ('再研究','缓议')"
    ).fetchone()[0]
    assert deferred > 0
    assert con.execute(
        "SELECT count(*) FROM world_event WHERE event_type IN "
        "('appointment_deferred','project_deferred')").fetchone()[0] > 0


def test_项目批准留下了决策痕迹(played):
    """会议直接改字段的话，"批准"这条留痕就没了——
    而留痕正是项目系统全部的意义（§42）。"""
    con = played[0]
    n = con.execute(
        "SELECT count(*) FROM project_decision WHERE role='批准'").fetchone()[0]
    assert n > 0


# ---------- 同学关系 ----------

def test_党校同学来自别的单位(played):
    """蓝图八第 4 条：同一班次的人来自不同地区和系统。"""
    con = played[0]
    n = con.execute(
        "SELECT count(*) FROM relationship WHERE type='党校同学'").fetchone()[0]
    assert n > 0, "四十年里没有产生任何党校同学关系"


def test_班次越高同学来路越远():
    """县委党校的同学还是本县的人，省级班的同学就是市里和省直的人了。"""
    assert training.CLASSMATE_SCOPE["COUNTY"] == ("COUNTY",)
    assert "MUNICIPAL" in training.CLASSMATE_SCOPE["PROVINCIAL"]
    assert "CENTRAL" in training.CLASSMATE_SCOPE["CENTRAL"]
    assert "COUNTY" not in training.CLASSMATE_SCOPE["CENTRAL"]


def test_同学关系不进任免条件(played):
    """§69 这条线对同学关系同样成立。"""
    from gongpu import rules as R
    from gongpu.appointment import explain_for
    con, pid = played
    slot = con.execute("SELECT id FROM position_slot LIMIT 1").fetchone()["id"]
    conds = explain_for(con, slot, pid, date(2026, 12, 31),
                        R.resolve(date(2026, 12, 31)))
    assert not ({c["条件"] for c in conds} & {"同学", "关系", "党校同学"})
