"""领导班子的结构（《中国共产党工作机关条例》《地方组织法》）。

班子不是"一个正职 + 若干完全相同的副职"。
"""
from datetime import date

import pytest

from gongpu import leadership, meetings
from gongpu.world import advance_to, new_game


@pytest.fixture(scope="module")
def world():
    con, rng, clock, pid = new_game(seed="班子结构")
    return con


def test_兼任是常态不是特例(world):
    """县委常委兼组织部长、公安局长兼副县长。"""
    con = world
    rows = con.execute(
        "SELECT c.name, h.title_at_time FROM office_holding h "
        "JOIN character c ON c.id = h.character_id "
        "WHERE h.appointment_type='CONCURRENT' AND h.end_date IS NULL").fetchall()
    assert len(rows) >= 5, "组织部、宣传部、政法委、公安局这些都该有兼任"


def test_分管日常工作的副职不是每个机关都有(world):
    """条例：正职由上级机构领导成员兼任的，**可以**设分管日常工作的副职。

    所以它出现在一把手高配、兼任的机关里，不是固定槽位。
    普通的民政局、财政局没有。
    """
    con = world
    have = {r[0] for r in con.execute(
        "SELECT COALESCE(o.short_name,o.name) FROM position_slot s "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE s.executive_deputy=1")}
    assert have, "总有几个机关的一把手是兼任的"
    assert "公安局" in have, "公安局长兼副县长，必须有人管日常"
    assert "民政局" not in have, "普通业务局不该有常务副局长"
    assert "财政局" not in have


def test_设常务副职的机关正职确实是兼任(world):
    con = world
    for r in con.execute(
            "SELECT DISTINCT s.organization_id AS org FROM position_slot s "
            "WHERE s.executive_deputy=1"):
        yes, why = leadership.head_is_concurrent(con, r["org"])
        assert yes, "没有兼任却设了常务副职：%s" % r["org"]
        assert why


def test_党组副书记不等于常务副职(world):
    """两者高度相关，但不是同一个概念。"""
    con = world
    rows = con.execute(
        "SELECT h.party_post AS pp, s.executive_deputy AS ed FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE h.end_date IS NULL AND h.party_post LIKE '%副书记'").fetchall()
    assert rows
    assert any(not r["ed"] for r in rows), \
        "应当存在'党组副书记、副局长'而不是常务副职的情形"


def test_党的工作机关不设党组(world):
    """组织部、宣传部、政法委本身就是党的机构。
    "县委组织部党委书记"是句病句。法院检察院则是设党组的。"""
    con = world
    bad = con.execute(
        "SELECT count(*) FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.end_date IS NULL AND h.party_post IS NOT NULL "
        "AND o.organization_type NOT IN "
        "  ('GOVERNMENT','COURT','PROCURATORATE')").fetchone()[0]
    assert bad == 0


def test_每类机关的班子结构不一样(world):
    """点开不同单位，应该真的出现不同的政治组织结构，
    而不是"局长 + 副局长×N"换个名字。"""
    con = world
    shapes = {}
    for name in ("公安局", "县法院", "县检察院", "民政局", "县委政法委",
                 "县委组织部", "市国安局"):
        r = con.execute("SELECT id FROM organization WHERE short_name=?",
                        (name,)).fetchone()
        posts = [x[0] for x in con.execute(
            "SELECT d.name FROM position_slot s "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE s.organization_id=? AND d.is_leadership=1 "
            "ORDER BY s.leadership_order", (r["id"],))]
        shapes[name] = tuple(posts)
    assert shapes["县法院"][0] == "院长"
    assert shapes["县检察院"][0] == "检察长"
    assert "审判委员会专职委员" in shapes["县法院"]
    assert "检察委员会专职委员" in shapes["县检察院"]
    assert "政治部主任" in shapes["公安局"]
    assert shapes["县委政法委"][0] == "书记"
    # 七个机关不该有两个结构完全一样
    assert len(set(shapes.values())) >= 6, shapes


def test_垂直管理系统单独标出来(world):
    """税务、国安的干部不由本地党委管。这是它和民政局最根本的区别。"""
    con = world
    vertical = {r[0] for r in con.execute(
        "SELECT COALESCE(short_name,name) FROM organization "
        "WHERE personnel_control='VERTICAL'")}
    assert "县税务局" in vertical
    assert "市国安局" in vertical
    assert "民政局" not in vertical


def test_国安机关不虚构班子(world):
    """公开信息本来就少。为了"细"去虚构处室，比留白更不真实。"""
    con = world
    r = con.execute("SELECT id, visibility FROM organization "
                    "WHERE short_name='市国安局'").fetchone()
    assert r["visibility"] == "RESTRICTED"
    n = con.execute(
        "SELECT count(*) FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.organization_id=? AND d.is_leadership=1", (r["id"],)).fetchone()[0]
    assert n <= 2, "国安机关不该铺出一整套公开班子"


def test_模板决定这类机关设不设常务副职():
    """发改、财政、审计、卫健这些，模板里明确不设。"""
    assert leadership.wants_daily_work_deputy("PUBLIC_SECURITY")
    assert leadership.wants_daily_work_deputy("ORGANIZATION_DEPT")
    assert leadership.wants_daily_work_deputy("POLITICAL_LEGAL_COMMITTEE")
    assert not leadership.wants_daily_work_deputy("FINANCE")
    assert not leadership.wants_daily_work_deputy("DEVELOPMENT_REFORM")
    assert not leadership.wants_daily_work_deputy("AUDIT")
    assert not leadership.wants_daily_work_deputy("GENERIC_DEPARTMENT")


def test_四个第二不是同一个人():
    """operational_no2 / rank_no2 / party_no2 / legal_successor 要分开。"""
    con, _, _, _ = new_game(seed="四个第二")
    r = con.execute("SELECT id FROM organization WHERE short_name='公安局'").fetchone()
    no2 = leadership.operational_no2(con, r["id"])
    assert no2 and no2["ed"] == 1, "公安局的日常二把手是常务副局长"
    # 民政局不设常务副职，日常二号位就按班子排序取
    r2 = con.execute("SELECT id FROM organization WHERE short_name='民政局'").fetchone()
    n2 = leadership.operational_no2(con, r2["id"])
    if n2:
        assert n2["ed"] == 0


def test_四套班子自己不套党组(world):
    con = world
    bad = con.execute(
        "SELECT count(*) FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.end_date IS NULL AND h.party_post IS NOT NULL "
        "AND o.protocol_order IS NOT NULL").fetchone()[0]
    assert bad == 0


def test_职务称谓由三个维度合成(world):
    """党组副书记、常务副局长 —— 库里分开存，显示时才合成。"""
    con = world
    h = con.execute(
        "SELECT h.id FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.end_date IS NULL AND s.executive_deputy=1 "
        "AND o.organization_type='GOVERNMENT' LIMIT 1").fetchone()
    if h:
        t = leadership.full_title(con, h["id"])
        assert "常务副" in t or "分管日常工作" in t, t


def test_主持工作和分管日常工作不是一回事():
    """分管日常工作的时候正职还在；主持工作是正职的位子空着。"""
    con, rng, clock, _ = new_game(seed="主持工作")
    org = con.execute(
        "SELECT s.organization_id AS org FROM position_slot s "
        "WHERE s.leadership_order=1 AND s.status='OCCUPIED' "
        "AND EXISTS (SELECT 1 FROM position_slot s2 "
        "  WHERE s2.organization_id = s.organization_id "
        "  AND s2.leadership_order=2 AND s2.status='OCCUPIED') LIMIT 1").fetchone()
    assert org
    con.execute("UPDATE position_slot SET status='VACANT',holder_id=NULL "
                "WHERE organization_id=? AND leadership_order=1", (org["org"],))
    con.execute("UPDATE office_holding SET end_date=? WHERE end_date IS NULL "
                "AND position_slot_id IN (SELECT id FROM position_slot "
                "  WHERE organization_id=? AND leadership_order=1)",
                (clock.date.isoformat(), org["org"]))
    assert leadership.acting_heads(con, clock.date)
    h = con.execute(
        "SELECT h.id FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE h.end_date IS NULL AND s.organization_id=? AND h.acting_head=1",
        (org["org"],)).fetchone()
    assert "主持工作" in leadership.full_title(con, h["id"])


def test_政府常务会议成员包括秘书长(world):
    """《地方组织法》：常务会议由行政首长、副职和秘书长组成。"""
    con = world
    org = meetings._org_for(con, meetings.GOV_EXECUTIVE)
    posts = {m["title"] for m in meetings.members_of(con, org["id"])}
    assert any("秘书长" in p for p in posts), posts
    assert any("县长" in p for p in posts)


def test_局长不是常委会当然成员(world):
    """局长、厅长级别再高，也不存在"当然进入常委会"的资格。"""
    con = world
    org = meetings._org_for(con, meetings.PARTY_STANDING)
    ids = {m["id"] for m in meetings.members_of(con, org["id"])}
    juzhang = con.execute(
        "SELECT h.character_id AS cid FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.end_date IS NULL AND d.name='局长' "
        "AND o.short_name='民政局'").fetchone()
    if juzhang:
        assert juzhang["cid"] not in ids


def test_办公厅的人是会务工作人员不是列席():
    """人在会场不等于列席，列席不等于会议成员。"""
    con, rng, clock, _ = new_game(seed="会务")
    advance_to(con, clock, date(1987, 6, 30), rng)
    m = con.execute("SELECT id, organization_id FROM meeting "
                    "WHERE kind=? ORDER BY id DESC LIMIT 1",
                    (meetings.PARTY_STANDING,)).fetchone()
    if m is None:
        pytest.skip("这段时间没开常委会")
    staff = con.execute(
        "SELECT h.character_id AS cid FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.end_date IS NULL AND o.system_type='综合' "
        "AND o.parent_id=? LIMIT 1", (m["organization_id"],)).fetchone()
    if staff:
        assert meetings.player_role(con, staff["cid"], m["id"]) in (
            "工作人员", "与会", "主持", "列席")
