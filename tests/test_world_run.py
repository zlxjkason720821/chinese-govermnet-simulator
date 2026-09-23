"""世界自转（技术文档 §19、§29、§30-33、§82）。

§82 给整个第一版定的目标只有一句：验证这个世界是否真的会自己运行。
这个文件就是那句话的测试。
"""
from datetime import date

import pytest

from gongpu import rules
from gongpu.npc import annual_intake, die, retire, tick
from gongpu.rng import RandomService
from gongpu.world import advance_to, new_game

START = date(1986, 7, 15)


@pytest.fixture(scope="module")
def played():
    """跑满 1986—2026，模块内共用。跑一次约零点几秒。"""
    con, rng, clock, pid = new_game(seed="world-run")
    advance_to(con, clock, date(2026, 12, 31), rng)
    return con, rng, clock, pid


def count(con, sql):
    return con.execute(sql).fetchone()[0]


def test_开局的县是有人在岗的():
    con, rng, clock, pid = new_game(seed="boot")
    assert count(con, "SELECT count(*) FROM character") > 50
    assert count(con, "SELECT count(*) FROM position_slot WHERE status='OCCUPIED'") > 50


def test_开局没有人不满足自己在任岗位的资格():
    """开局就违反制度的世界，后面所有推演都不可信。"""
    con, rng, clock, pid = new_game(seed="boot")
    bad = con.execute(
        "SELECT c.name, d.name AS post FROM office_holding h "
        "JOIN character c ON c.id = h.character_id "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.end_date IS NULL AND d.party_requirement = 1 "
        "AND c.party_status != 'MEMBER'").fetchall()
    assert not bad, "有非党员占着要求党籍的岗位：%s" % [tuple(b) for b in bad]


def test_退休产生真实空缺(world_fixtureless=None):
    """§19 空缺只能由调走/退休/免职/去世/机构调整产生。"""
    con, rng, clock, pid = new_game(seed="retire")
    holder = con.execute(
        "SELECT holder_id, id FROM position_slot WHERE status='OCCUPIED' "
        "AND holder_id IS NOT NULL LIMIT 1").fetchone()
    freed = retire(con, holder["holder_id"], date(1990, 1, 1))
    assert holder["id"] in freed
    slot = con.execute("SELECT status, holder_id FROM position_slot WHERE id=?",
                       (holder["id"],)).fetchone()
    assert (slot["status"], slot["holder_id"]) == ("VACANT", None)
    assert con.execute("SELECT retired FROM character WHERE id=?",
                       (holder["holder_id"],)).fetchone()[0] == 1


def test_去世同样腾出岗位并留下记录():
    con, rng, clock, pid = new_game(seed="die")
    holder = con.execute("SELECT holder_id FROM position_slot WHERE status='OCCUPIED' "
                         "AND holder_id IS NOT NULL LIMIT 1").fetchone()[0]
    die(con, holder, date(1991, 5, 1))
    c = con.execute("SELECT alive, death_date FROM character WHERE id=?", (holder,)).fetchone()
    assert c["alive"] == 0 and c["death_date"] == "1991-05-01"
    assert con.execute("SELECT count(*) FROM office_holding WHERE character_id=? "
                       "AND exit_reason='DEATH'", (holder,)).fetchone()[0] >= 1


def test_世界会自己运行(played):
    """没有玩家操作，四十年里必须自己发生退休、录用、任免。"""
    con = played[0]
    kinds = dict(con.execute("SELECT event_type, count(*) FROM world_event "
                             "GROUP BY event_type"))
    for k in ("retirement", "recruitment", "appointment", "institution_migration"):
        assert kinds.get(k, 0) > 0, f"四十年里一次 {k} 都没有发生"
    assert kinds["appointment"] >= 10


def test_在岗人数不超过在职干部数(played):
    """一个人不能同时占两个坑：升迁必须腾出原岗位。"""
    con = played[0]
    occupied = count(con, "SELECT count(*) FROM position_slot WHERE status='OCCUPIED'")
    serving = count(con, "SELECT count(*) FROM character WHERE alive=1 AND retired=0")
    # 兼任的人占两个坑，所以在岗数可以略高于在职人数
    concurrent = count(con, "SELECT count(*) FROM office_holding WHERE end_date IS NULL "
                            "AND primary_position = 0")
    assert occupied <= serving + concurrent, f"在岗 {occupied} 超过在职 {serving}"


def test_没有退休或去世的人还占着岗位(played):
    con = played[0]
    bad = con.execute(
        "SELECT count(*) FROM position_slot s JOIN character c ON c.id = s.holder_id "
        "WHERE s.status='OCCUPIED' AND (c.alive=0 OR c.retired=1)").fetchone()[0]
    assert bad == 0


def test_县不会空掉(played):
    """四十年后如果大半岗位空着，说明干部来源或任用链断了。"""
    con = played[0]
    total = count(con, "SELECT count(*) FROM position_slot")
    vacant = count(con, "SELECT count(*) FROM position_slot WHERE status='VACANT'")
    assert vacant / total < 0.20, f"空缺率 {vacant}/{total} 过高"


def test_没有人在一个岗位上任职为负数天(played):
    con = played[0]
    bad = con.execute("SELECT count(*) FROM office_holding "
                      "WHERE end_date IS NOT NULL AND end_date < start_date").fetchone()[0]
    assert bad == 0


def test_干部不会一年换三个岗位(played):
    """现职任职年限是制度条文（时间轴 2014 条例一节），不是手感调参。"""
    con = played[0]
    short = con.execute(
        "SELECT count(*) FROM office_holding "
        "WHERE end_date IS NOT NULL AND exit_reason='PROMOTED' "
        "AND julianday(end_date) - julianday(start_date) < 365 * 1.5").fetchone()[0]
    total = con.execute("SELECT count(*) FROM office_holding "
                        "WHERE exit_reason='PROMOTED'").fetchone()[0]
    assert total > 0
    assert short / total < 0.15, f"{short}/{total} 次调动的任职不足一年半"


def test_党籍会随职业发展变化(played):
    """干部进来时多数不是党员。如果此后永不入党，几年后就没人够格当局长。"""
    con = played[0]
    members = count(con, "SELECT count(*) FROM character WHERE alive=1 AND retired=0 "
                         "AND party_status='MEMBER'")
    serving = count(con, "SELECT count(*) FROM character WHERE alive=1 AND retired=0")
    assert members / serving > 0.5


def test_四十年模拟可复现():
    """§70 同种子跑两遍，世界必须一模一样。"""
    def run():
        con, rng, clock, pid = new_game(seed="determinism")
        advance_to(con, clock, date(2010, 1, 1), rng)
        return [tuple(r) for r in con.execute(
            "SELECT date,event_type,actors,data FROM world_event ORDER BY id")]
    a, b = run(), run()
    assert a == b
    assert len(a) > 20


def test_不同种子产生不同的世界():
    def run(seed):
        con, rng, clock, pid = new_game(seed=seed)
        advance_to(con, clock, date(2000, 1, 1), rng)
        return con.execute("SELECT count(*) FROM world_event").fetchone()[0], \
            con.execute("SELECT group_concat(name) FROM character LIMIT 20").fetchone()[0]
    assert run("seed-1") != run("seed-2")


def test_开局四套班子的正职必须有人():
    """县长、书记、人大主任、政协主席开局不能空着。

    正职空缺要从副职提拔，而提拔要求现职满三年——开局所有人都刚到任，
    这个坑会连着空好几年，整个县从一开始就是残的。
    """
    for seed in ("1986", "111111", "222222", "333333", "444444", "555555"):
        con, rng, clock, pid = new_game(seed=seed)
        empty = con.execute(
            "SELECT o.short_name || d.name FROM position_slot s "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE s.status='VACANT' AND d.leadership_level='正处级' "
            "AND d.protocol_order = 1").fetchall()
        assert not empty, f"seed={seed} 开局就缺正职：{[e[0] for e in empty]}"


def test_没有人身兼两个主要领导职务(played):
    """升迁必须腾出原岗位。

    §20 允许兼任，但兼任必须显式记为非主要职务（primary_position=0）——
    比如总书记兼任国家主席。主职只能有一个。
    """
    con = played[0]
    dup = con.execute(
        "SELECT c.name, count(*) n FROM office_holding h "
        "JOIN character c ON c.id = h.character_id "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.end_date IS NULL AND d.is_leadership = 1 "
        "AND h.primary_position = 1 "
        "GROUP BY h.character_id HAVING n > 1").fetchall()
    assert not dup, f"有人身兼两个主要领导职务：{[tuple(d) for d in dup]}"


def test_外单位经历不挂在本县岗位上():
    """入职前和调入前的经历属于世界之外，不该指向本县某个具体岗位。

    挂上去的话，这个岗位的历任名单会从那人参加工作那年算起——
    出现"1952 年就当县长"这种记录。
    """
    con, rng, clock, pid = new_game(seed="outside")
    bad = con.execute(
        "SELECT count(*) FROM office_holding "
        "WHERE position_slot_id IS NOT NULL AND title_at_time IN ('干部','市直机关及外县任职')"
    ).fetchone()[0]
    assert bad == 0
    assert count(con, "SELECT count(*) FROM office_holding WHERE position_slot_id IS NULL") > 0


def test_一个岗位同一时间只有一个人在任(played):
    """任职区间不能重叠，否则"历任某职"读起来是错的。"""
    con = played[0]
    overlap = con.execute(
        "SELECT a.position_slot_id, count(*) FROM office_holding a "
        "JOIN office_holding b ON b.position_slot_id = a.position_slot_id AND b.id > a.id "
        "WHERE a.position_slot_id IS NOT NULL "
        "AND a.start_date < COALESCE(b.end_date, '9999-12-31') "
        "AND b.start_date < COALESCE(a.end_date, '9999-12-31') "
        "GROUP BY a.position_slot_id").fetchall()
    assert not overlap, f"这些岗位出现了任职区间重叠：{[tuple(o) for o in overlap]}"


def test_正职不会长期空缺(played):
    """县内选不出人时，上级会调人来（§72 背景干部池）。

    没有这条通道，一个县四十年里会有五分之一的时间没有县长——那才是失真。
    """
    from datetime import date as _d
    con = played[0]
    total = (_d(2026, 12, 31) - _d(1986, 7, 15)).days
    for slot in con.execute(
            "SELECT s.id, o.short_name || d.name AS title FROM position_slot s "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE d.leadership_level='正处级' AND d.protocol_order=1"):
        filled = 0
        for h in con.execute(
                "SELECT start_date, end_date FROM office_holding "
                "WHERE position_slot_id=?", (slot["id"],)):
            a = max(_d.fromisoformat(h["start_date"]), _d(1986, 7, 15))
            b = _d.fromisoformat(h["end_date"]) if h["end_date"] else _d(2026, 12, 31)
            filled += max((b - a).days, 0)
        assert filled / total > 0.85, \
            "%s 四十年里有 %.0f%% 的时间没人" % (slot["title"], 100 - filled * 100.0 / total)
