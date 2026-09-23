"""事项—项目—会议闭环（V3 技术设计文档）。

核心：Matter 是唯一核心对象，项目是长期事项的容器，
会议是处理事项的一种制度化机制，待办只是某个岗位对事项的视图。
"""
from datetime import date

import pytest

from gongpu import caps, meetings, projects
from gongpu.world import advance_to, new_game


@pytest.fixture(scope="module")
def played():
    con, rng, clock, pid = new_game(seed="V3闭环")
    advance_to(con, clock, date(1992, 12, 31), rng)
    return con


# ── 权限不按级别硬开关（V3 §5）──────────────────────

def test_能不能进常委会看是不是常委不看级别():
    con, _, _, _ = new_game(seed="权限")
    def find(full):
        r = con.execute(
            "SELECT h.character_id FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN organization o ON o.id = s.organization_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.end_date IS NULL AND COALESCE(o.short_name,o.name)||d.name=? "
            "LIMIT 1", (full,)).fetchone()
        return r[0] if r else None
    juzhang = find("民政局局长")
    shuji = find("中共红山县委书记")
    assert juzhang and shuji
    # 民政局长是正科级，县委书记是正处级；但决定性的不是级别，
    # 是"他是不是常委"——局长再高也不是常委会成员。
    assert not caps.has(con, juzhang, "MEETING_DELIBERATE")
    assert caps.has(con, shuji, "MEETING_DELIBERATE")


def test_页签按权限和实体显示不按级别():
    """visible = 有相关实体 or 有权限 or 有会务任务"""
    con, _, _, _ = new_game(seed="页签")
    def find(full):
        r = con.execute(
            "SELECT h.character_id FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN organization o ON o.id = s.organization_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.end_date IS NULL AND COALESCE(o.short_name,o.name)||d.name=? "
            "LIMIT 1", (full,)).fetchone()
        return r[0] if r else None
    office = find("县委办科员")
    civil = find("民政局科员")
    assert office and civil
    # 办公厅的科员要做会务，看得见会议页；民政局的科员看不见
    assert caps.tab_visible(con, office, "会议")
    assert not caps.tab_visible(con, civil, "会议")
    # 但手上真有相关事的时候就看得见
    assert caps.tab_visible(con, civil, "会议", has_entity=True)


def test_兼任的人两边权限都有():
    con, _, _, _ = new_game(seed="兼任权限")
    r = con.execute(
        "SELECT h.character_id AS cid FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.appointment_type='CONCURRENT' AND h.end_date IS NULL "
        "AND o.archetype='ORGANIZATION_DEPT' LIMIT 1").fetchone()
    assert r
    mine = caps.of(con, r["cid"])
    assert "PERSONNEL_DELIBERATE" in mine, "他是常委"
    assert "PERSONNEL_INSPECT" in mine, "他也是组织部长"


# ── 会议：六种参会身份（V3 §7）──────────────────────

def test_六种参会身份分开记(played):
    con = played
    roles = {r[0] for r in con.execute(
        "SELECT DISTINCT role FROM meeting_participant")}
    assert "CHAIR" in roles and "MEMBER" in roles
    assert "STAFF" in roles, "办公厅的人是会务，不是与会人员"
    assert "REPORTER" in roles, "议题要有人上会汇报"


def test_会务人员没有成员权利(played):
    con = played
    for r in con.execute(
            "SELECT meeting_id, character_id FROM meeting_participant "
            "WHERE role='STAFF' LIMIT 5"):
        assert meetings.player_role(con, r["character_id"], r["meeting_id"]) \
            in ("工作人员", "列席"), "会务不该变成与会"


def test_议题有汇报人和顺序(played):
    con = played
    n = con.execute(
        "SELECT count(*) FROM meeting_item WHERE sequence_no IS NOT NULL").fetchone()[0]
    assert n > 0
    assert con.execute(
        "SELECT count(*) FROM meeting_item WHERE reporter_id IS NOT NULL"
    ).fetchone()[0] > 0


# ── 闭环：决定 → 事项 → 督办（V3 §19）─────────────

def test_决定要说清楚谁去办几天内办完(played):
    con = played
    rows = con.execute(
        "SELECT decision, decision_text, responsible_org_id, deadline_date "
        "FROM meeting_item WHERE decision IS NOT NULL AND decision != '不同意' "
        "LIMIT 20").fetchall()
    assert rows
    for r in rows:
        assert r["decision_text"], "决定不能只是一个词"
        if r["responsible_org_id"]:
            assert r["deadline_date"], "定了承办单位就要有时限"


def test_会议决定派生督办事项(played):
    """这是整个设计的闭环所在：决定拆成落实事项，事项进督办。"""
    con = played
    n = con.execute(
        "SELECT count(*) FROM work_item WHERE matter_type='SUPERVISION'").fetchone()[0]
    assert n > 0, "会上定了却没人去落实，会议就只是一行字"
    r = con.execute(
        "SELECT subject, origin_org_id, target_org_id, current_stage "
        "FROM work_item WHERE matter_type='SUPERVISION' LIMIT 1").fetchone()
    assert r["origin_org_id"] and r["target_org_id"], "谁交办的、交给谁，都要有"
    assert r["current_stage"]


def test_决策留痕不静默覆盖(played):
    """项目投资 8亿 → 10亿 → 9.4亿，每一次变化都要留下。"""
    con = played
    assert con.execute("SELECT count(*) FROM decision_log").fetchone()[0] > 0
    pid = con.execute("SELECT id FROM project LIMIT 1").fetchone()[0]
    before, after = projects.change_investment(
        con, pid, date(1992, 6, 1), 99999, "财政部门认为投资过高，重新测算")
    h = projects.history(con, pid)
    assert any(x["field"] == "investment" and x["after_value"] == "99999" for x in h)
    assert any(x["before_value"] == str(before) for x in h if x["field"] == "investment")


# ── 事项不是一条字符串（V3 §3.1）───────────────────

def test_事项带完整的结构(played):
    con = played
    r = con.execute(
        "SELECT * FROM work_item WHERE matter_type IS NOT NULL LIMIT 1").fetchone()
    assert r
    for f in ("matter_type", "origin_org_id", "target_org_id",
              "current_stage", "importance", "urgency"):
        assert f in r.keys()


def test_matter_视图指向同一张表(played):
    con = played
    a = con.execute("SELECT count(*) FROM matter").fetchone()[0]
    b = con.execute("SELECT count(*) FROM work_item").fetchone()[0]
    assert a == b


# ── 项目：地域、领域、十二项指标（V3 §11、§12）──────

def test_项目必须落到具体地方和领域(played):
    con = played
    rows = con.execute(
        "SELECT name, region, policy_domain FROM project").fetchall()
    assert rows
    for r in rows:
        assert r["region"], "%s 不知道在哪儿" % r["name"]
        assert r["policy_domain"], "%s 不知道属哪个口" % r["name"]


def test_项目不能只有一个政绩值(played):
    con = played
    pid = con.execute("SELECT id FROM project LIMIT 1").fetchone()[0]
    ind = projects.indicators(con, pid)
    assert len(ind) == 12
    names = {x["指标"] for x in ind}
    assert {"进度", "成本控制", "质量", "程序合规", "审计风险",
            "廉政风险", "后续运营负担"} <= names


def test_领域按项目内容判断不是随机(played):
    con = played
    for r in con.execute("SELECT name, policy_domain FROM project").fetchall():
        if "道路" in r["name"] or "公路" in r["name"]:
            assert r["policy_domain"] == "交通", r["name"]
        if "水" in r["name"] and "排水" in r["name"]:
            assert r["policy_domain"] == "水利", r["name"]
