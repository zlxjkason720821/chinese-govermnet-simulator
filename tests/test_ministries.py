"""中央部委与中央委员会（§25 制度迁移，蓝图二十三、二十四）。"""
from datetime import date

import pytest

from gongpu import central, ministries
from gongpu.appointment import LEVEL_ORDER
from gongpu.world import advance_to, new_game


@pytest.fixture(scope="module")
def world():
    con, rng, clock, pid = new_game(seed="中央制度")
    advance_to(con, clock, date(2005, 12, 31), rng)
    return con


def test_一九八六年是那一年的部委():
    """拿 2026 的部委表倒推 1986，和拿 2026 的区划倒推 1986 是一个性质的错。"""
    con, _, _, _ = new_game(seed="八六")
    names = {r["name"] for r in ministries.listing(con, date(1986, 7, 15))}
    assert "国家教委" in names and "教育部" not in names
    assert "外经贸部" in names and "商务部" not in names
    assert "国家计委" in names and "国家发改委" not in names
    assert "冶金工业部" in names and "工信部" not in names
    assert "应急管理部" not in names


def test_一九九八年那次机构改革真的发生():
    con, rng, clock, _ = new_game(seed="九八改革")
    before = {r["name"] for r in ministries.listing(con, date(1998, 1, 1))}
    advance_to(con, clock, date(1998, 12, 31), rng)
    after = {r["name"] for r in ministries.listing(con, clock.date)}
    gone = before - after
    assert len(gone) >= 10, "1998 年那次是力度最大的一次：%s" % gone
    assert {"冶金工业部", "煤炭工业部", "邮电部", "国家计委"} <= gone
    assert "教育部" in after and "科技部" in after
    ev = con.execute(
        "SELECT count(*) FROM world_event WHERE event_type='institution_abolished'"
    ).fetchone()[0]
    assert ev >= 10


def test_撤销机构的人要有去处():
    """撤销一个部不是删一行数据：部长的位子没了，人还在。"""
    con, rng, clock, _ = new_game(seed="撤并")
    advance_to(con, clock, date(1999, 6, 30), rng)
    stuck = con.execute(
        "SELECT count(*) FROM office_holding "
        "WHERE exit_reason='INSTITUTION_ABOLISHED' AND end_date IS NULL").fetchone()[0]
    assert stuck == 0
    # 人没有跟着机构消失
    assert con.execute(
        "SELECT count(*) FROM character c WHERE c.alive=1 AND EXISTS "
        "(SELECT 1 FROM office_holding h WHERE h.character_id=c.id "
        " AND h.exit_reason='INSTITUTION_ABOLISHED')").fetchone()[0] > 0


def test_省委书记省长默认是中央委员(world):
    """公开惯例：各省、自治区、直辖市党委书记及政府首长。"""
    con = world
    top = con.execute(
        "SELECT c.id, h.title_at_time t, h.start_date FROM office_holding h "
        "JOIN character c ON c.id = h.character_id "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.end_date IS NULL AND h.primary_position=1 "
        "AND o.admin_level='PROVINCIAL' AND d.leadership_level='正部级' "
        "AND d.name IN ('书记','省长','市长','主席')").fetchall()
    assert top
    congress = con.execute(
        "SELECT max(date) FROM world_event WHERE event_type='party_congress'").fetchone()[0]
    # 只看党代会之前就在任的：中央委员只在党代会上产生
    sitting = [r for r in top if r["start_date"] < congress]
    inside = [r for r in sitting
              if central.current_status(con, r["id"]) is not None]
    assert len(inside) >= len(sitting) * 0.8, \
        "党代会时在任的省级正职基本都该在中央委员会里：%d/%d" % (len(inside), len(sitting))


def test_直辖市和广东新疆的书记是政治局委员(world):
    con = world
    for name in central.politburo_regions():
        r = con.execute(
            "SELECT c.id, c.name FROM office_holding h "
            "JOIN character c ON c.id = h.character_id "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE h.end_date IS NULL AND d.name='书记' "
            "AND o.admin_level='PROVINCIAL' AND o.name LIKE ?",
            ("%" + name + "%",)).fetchone()
        if r is None:
            continue           # 那一年这个区划还不存在（重庆 1997）
        st = central.current_status(con, r["id"])
        assert st in (central.POLITBURO, central.STANDING,
                      central.GENERAL_SECRETARY, central.MEMBER, None), st


def test_中央委员要有五年以上党龄(world):
    """党章第二十二条。"""
    con = world
    congress = con.execute(
        "SELECT max(date) FROM world_event WHERE event_type='party_congress'").fetchone()[0]
    on = date.fromisoformat(congress)
    bad = []
    for r in con.execute(
            "SELECT character_id FROM party_central_status WHERE end_date IS NULL "
            "AND start_date = ?", (congress,)):
        if central.party_years(con, r["character_id"], on) < 5:
            bad.append(r["character_id"])
    assert not bad, "党龄不足五年的不能进中央委员会：%s" % bad[:5]


def test_上海经历不是总书记的必要条件():
    """江泽民、习近平曾任上海市委书记，胡锦涛没有。
    重要地区任职是履历加分项，不能设成必经条件——
    这一条在代码里的表现是：根本没有这样一条规则。
    """
    import io
    from gongpu import paths
    for f in ("central.yaml", "tracks.yaml"):
        text = (paths.DATA / f).read_text(encoding="utf-8")
        assert "上海" not in text or "必" not in text.split("上海")[1][:40]
    # 政治局兼任地区是一组，不是单独把上海挑出来
    assert len(central.politburo_regions()) >= 5


def test_政治局委员年龄线比中央委员紧(world):
    """政治局委员的预备人选一般是 64 周岁以下的正省部级以上干部。"""
    assert ministries.committee_rules()["politburo_max_age"] < central.AGE_CEILING


def test_政治局委员都是正部级以上(world):
    con = world
    for r in con.execute(
            "SELECT p.character_id FROM party_central_status p "
            "WHERE p.end_date IS NULL AND p.status=?", (central.POLITBURO,)):
        lvl = con.execute(
            "SELECT d.leadership_level FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.character_id=? AND h.end_date IS NULL AND h.primary_position=1",
            (r["character_id"],)).fetchone()
        if lvl:
            assert LEVEL_ORDER[lvl[0]] >= LEVEL_ORDER["正部级"], lvl[0]


def test_名额按届次走不是常量():
    """名额由全国代表大会决定，一届一届在变，而且是查得到的。"""
    assert central.quota(13)[central.MEMBER] == 175      # 十三大 1987
    assert central.quota(13)[central.ALTERNATE] == 110
    assert central.quota(20)[central.MEMBER] == 205      # 二十大 2022
    assert central.quota(20)[central.ALTERNATE] == 171
    assert central.quota(13) != central.quota(20)


def test_选出来的人数对得上名额():
    """政治局委员同时也是中央委员，只是挂着更高的身份标签。
    只数"中央委员"那一档会少二十几个人。"""
    con, rng, clock, _ = new_game(seed="名额")
    advance_to(con, clock, date(1988, 12, 31), rng)
    # 查换届当天选出来的人数。之后会有出缺：中央委员出缺由候补委员递补，
    # 政治局出缺则不递补，所以过一段时间总人数会略少于名额。
    elected = con.execute(
        "SELECT count(*) FROM party_central_status WHERE congress=13 "
        "AND start_date='1987-10-20' AND status IN (?,?,?,?)",
        (central.MEMBER, central.POLITBURO, central.STANDING,
         central.GENERAL_SECRETARY)).fetchone()[0]
    assert elected == central.quota(13)[central.MEMBER]
    # 眼下在册的不会比名额多，也不该掉太多
    now = central.committee_size(con)
    assert elected - 10 <= now <= elected


def test_退休不终止中央委员身份():
    """中央委员会是党代会选出来的一届名册，任期五年。
    任期内从岗位上退下来的人仍然是中央委员，要等下一届换届。

    把退休也算成出缺，是上一版最大的错：三年掉了四十九个委员，
    候补委员被抽干去填缺。
    """
    con, rng, clock, _ = new_game(seed="退休不掉")
    advance_to(con, clock, date(2005, 12, 31), rng)
    n = con.execute(
        "SELECT count(*) FROM party_central_status p "
        "JOIN character c ON c.id = p.character_id "
        "WHERE p.end_date IS NULL AND c.retired=1").fetchone()[0]
    assert n > 0, "五年一届，总有人任期内到龄"


def test_递补是个位数量级不是每月常规():
    """真实的二十届，四年递补十四名。

    上一版二十四年递补了二百九十三次，候补委员从一百五十个剩五十五个——
    那说明递补逻辑被当成了"把名册补齐"的工具，而不是出缺事件。
    """
    con, rng, clock, _ = new_game(seed="递补频度")
    advance_to(con, clock, date(1992, 9, 30), rng)     # 十三届一整届
    n = con.execute(
        "SELECT count(*) FROM world_event "
        "WHERE event_type='central_alternate_promoted'").fetchone()[0]
    assert 0 < n < 40, "一届五年递补 %d 次，对照真实的十几次" % n


def test_岗位空缺不触发中央委员递补():
    """地方或部委产生职位空缺，是另一本名册上的事。"""
    con, rng, clock, _ = new_game(seed="两本名册")
    advance_to(con, clock, date(1990, 6, 30), rng)
    before = con.execute(
        "SELECT count(*) FROM world_event "
        "WHERE event_type='central_alternate_promoted'").fetchone()[0]
    # 腾出一个正部级岗位，但人还活着、没被开除
    row = con.execute(
        "SELECT s.id, s.holder_id FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE d.leadership_level='正部级' AND s.status='OCCUPIED' LIMIT 1").fetchone()
    con.execute("UPDATE position_slot SET status='VACANT',holder_id=NULL WHERE id=?",
                (row["id"],))
    con.execute("UPDATE office_holding SET end_date=? WHERE position_slot_id=? "
                "AND end_date IS NULL", (clock.date.isoformat(), row["id"]))
    assert central.fill_vacancies(con, clock.date) == []
    after = con.execute(
        "SELECT count(*) FROM world_event "
        "WHERE event_type='central_alternate_promoted'").fetchone()[0]
    assert after == before


def test_中央任用面向全国():
    """部长本来就多是从各省省委书记、省长里出的。"""
    con, rng, clock, _ = new_game(seed="全国选人")
    advance_to(con, clock, date(2000, 12, 31), rng)
    vacant = con.execute(
        "SELECT count(*) FROM position_slot s "
        "JOIN organization o ON o.id = s.organization_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE o.admin_level='CENTRAL' AND d.leadership_level='正部级' "
        "AND s.status='VACANT'").fetchone()[0]
    total = con.execute(
        "SELECT count(*) FROM position_slot s "
        "JOIN organization o ON o.id = s.organization_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE o.admin_level='CENTRAL' AND d.leadership_level='正部级'").fetchone()[0]
    assert vacant < total * 0.2, "中央的位子不该大面积空着：%d/%d" % (vacant, total)


def test_到中央去这条路写在明面上():
    from gongpu import tracks
    cp = tracks.cfg()["central_path"]
    levels = [r["level"] for r in cp["rungs"]]
    assert levels == ["正厅级", "副部级", "正部级", "副国级", "正国级"]
    ids = [x["status"] for x in cp["identity"]]
    assert ids == [central.ALTERNATE, central.MEMBER,
                   central.POLITBURO, central.STANDING]
