"""制度迁移与职级（技术文档 §25、§26；时间轴文档 §32-§34）。

最重要的两条红线：
  §26 历史称谓不能被覆盖——1999 年的"副主任科员"到 2020 年仍写着副主任科员。
  §33 职级不是领导职务——职级晋升不改变职位、不改变指挥关系。
"""
from datetime import date

import pytest

from gongpu import migration, ranks, rules
from gongpu.world import advance_to, new_game


@pytest.fixture(scope="module")
def played():
    con, rng, clock, pid = new_game(seed="migrate")
    advance_to(con, clock, date(2026, 12, 31), rng)
    return con


def test_四次制度迁移都在正确日期发生(played):
    got = {}
    for r in played.execute("SELECT date,data FROM world_event "
                            "WHERE event_type='institution_migration'"):
        import json
        got[json.loads(r["data"])["to"]] = r["date"]
    assert got["1993_CIVIL_SERVICE"] == "1993-10-01"
    assert got["2006_CIVIL_SERVICE_LAW"] == "2006-01-01"
    assert got["2018_SUPERVISION"] == "2018-03-20"
    assert got["2019_RANK"] == "2019-06-01"


def test_1993之前没有人有公务员职级():
    con, rng, clock, pid = new_game(seed="pre93")
    advance_to(con, clock, date(1993, 9, 30), rng)
    assert con.execute("SELECT count(*) FROM rank_holding").fetchone()[0] == 0


def test_1993之后每个在职干部都进了公务员序列():
    con, rng, clock, pid = new_game(seed="post93")
    advance_to(con, clock, date(1993, 10, 2), rng)
    serving = con.execute("SELECT count(*) FROM character "
                          "WHERE alive=1 AND retired=0").fetchone()[0]
    ranked = con.execute("SELECT count(DISTINCT character_id) FROM rank_holding "
                         "WHERE end_date IS NULL").fetchone()[0]
    assert ranked == serving


def test_历史称谓不被套转改写(played):
    """§26 的原话：不能自动改写成四级主任科员。"""
    old = played.execute(
        "SELECT count(*) FROM rank_holding "
        "WHERE rank_name='主任科员' AND start_date < '2019-06-01'").fetchone()[0]
    assert old > 0, "套转前应当存在主任科员记录"
    # 套转只封口旧记录、另起新记录，旧行原文还在
    still = played.execute(
        "SELECT count(*) FROM rank_holding WHERE rank_name='主任科员' "
        "AND end_date = '2019-06-01'").fetchone()[0]
    assert still > 0, "套转把旧的主任科员记录改写或删除了"


def test_套转后的职级都在2019的十二级序列内(played):
    after = {r[0] for r in played.execute(
        "SELECT DISTINCT rank_name FROM rank_holding WHERE start_date >= '2019-06-01'")}
    assert after, "2019 之后应当有职级记录"
    assert after <= set(ranks.LADDER_2019), f"出现了序列外的职级：{after - set(ranks.LADDER_2019)}"
    assert "三级科员" not in after, "时间轴文档 §32 的十二级里没有三级科员"


def test_套转表只映射到合法职级():
    assert set(migration.CONVERSION_2019.values()) <= set(ranks.LADDER_2019)


def test_监察委员会2018年才出现(played):
    org = played.execute("SELECT name,valid_from FROM organization "
                         "WHERE organization_type='SUPERVISION'").fetchone()
    assert org is not None, "2018 监察体制改革后必须存在监察委员会"
    assert org["valid_from"] == "2018-03-20"


def test_监察委由纪委派生且简称正确(played):
    """全称是纪律检查委员会，简称才是纪委。只按简称匹配，改个名字改革就不发生了。"""
    org = played.execute("SELECT name, short_name FROM organization "
                         "WHERE organization_type='SUPERVISION'").fetchone()
    assert "监察委员会" in org["name"]
    assert org["short_name"] and "监委" in org["short_name"]


def test_迁移不丢履历():
    """§25 迁移必须保留人物历史。"""
    con, rng, clock, pid = new_game(seed="keep")
    advance_to(con, clock, date(1993, 9, 30), rng)
    before = con.execute("SELECT count(*) FROM office_holding").fetchone()[0]
    ids_before = {r[0] for r in con.execute("SELECT id FROM office_holding")}
    advance_to(con, clock, date(1993, 10, 2), rng)
    after_ids = {r[0] for r in con.execute("SELECT id FROM office_holding")}
    assert ids_before <= after_ids, "迁移删掉了既有履历"
    assert con.execute("SELECT count(*) FROM office_holding").fetchone()[0] >= before


def test_职级晋升不改变职位(played):
    """§33 红线：一级调研员 ≠ 处长。职级晋升不动 office_holding。"""
    con, rng, clock, pid = new_game(seed="rankonly")
    advance_to(con, clock, date(2000, 1, 2), rng)
    before = [tuple(r) for r in con.execute(
        "SELECT id,character_id,position_slot_id,start_date,end_date FROM office_holding "
        "ORDER BY id")]
    slots_before = [tuple(r) for r in con.execute(
        "SELECT id,status,holder_id FROM position_slot ORDER BY id")]
    ranks.promote_ranks(con, date(2000, 1, 2), rng, rules.resolve(date(2000, 1, 2)))
    after = [tuple(r) for r in con.execute(
        "SELECT id,character_id,position_slot_id,start_date,end_date FROM office_holding "
        "ORDER BY id")]
    slots_after = [tuple(r) for r in con.execute(
        "SELECT id,status,holder_id FROM position_slot ORDER BY id")]
    assert before == after, "职级晋升动了任职记录"
    assert slots_before == slots_after, "职级晋升动了岗位"


def test_职级受职数限制(played):
    """时间轴 §34：职级职数是真实资源，职级不能无限生成。"""
    total_slots = played.execute("SELECT count(*) FROM position_slot").fetchone()[0]
    for rank_name, ratio in ranks.QUOTA_RATIO.items():
        held = played.execute(
            "SELECT count(*) FROM rank_holding r JOIN character c ON c.id=r.character_id "
            "WHERE r.end_date IS NULL AND r.rank_name=? AND c.alive=1 AND c.retired=0",
            (rank_name,)).fetchone()[0]
        cap = max(int(total_slots * ratio), 1 if ratio > 0 else 0)
        assert held <= cap, f"{rank_name} 在职 {held} 人，超过职数上限 {cap}"


def test_职级会随年份上升(played):
    """如果四十年里没有一个人晋升过职级，职级系统等于没接上。"""
    promotions = played.execute(
        "SELECT count(*) FROM rank_holding WHERE source='PROMOTION'").fetchone()[0]
    assert promotions > 10


def test_一个人同时只有一个在效职级(played):
    bad = played.execute(
        "SELECT character_id, count(*) n FROM rank_holding WHERE end_date IS NULL "
        "GROUP BY character_id HAVING n > 1").fetchall()
    assert not bad, f"有人同时挂着多个职级：{[tuple(b) for b in bad]}"


def test_监委主任的职务规格是自己的不是抄来的(played):
    """按 name='主任' 去库里捞定义会捞到人大常委会主任（正处级、要求十年资历），
    县监委主任就此八年没人够得上。职务定义必须显式建立。"""
    d = played.execute(
        "SELECT d.leadership_level, d.min_years_experience, d.min_age "
        "FROM position_slot s JOIN position_definition d ON d.id = s.position_definition_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE o.organization_type='SUPERVISION'").fetchone()
    assert d is not None, "监察委员会应当有岗位"
    assert d["leadership_level"] == "副处级"
    assert d["min_years_experience"] <= 6


def test_监委成立后岗位能补上(played):
    """2018 设立，到 2026 有八年，不该一直空着。"""
    held = played.execute(
        "SELECT count(*) FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE o.organization_type='SUPERVISION'").fetchone()[0]
    assert held > 0, "监委成立八年一任主任都没配过"
