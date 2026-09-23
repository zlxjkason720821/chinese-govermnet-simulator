"""项目、决策留痕与纪律（技术文档 §41-§44）。

§44 是这里唯一真正重要的东西：违规与发现分离。
档案上干净不代表干净，只代表还没查到。
"""
from datetime import date

import pytest

from gongpu import discipline, projects
from gongpu.world import advance_to, new_game


@pytest.fixture(scope="module")
def played():
    con, rng, clock, pid = new_game(seed="gov-test")
    advance_to(con, clock, date(2026, 12, 31), rng)
    return con


def q(con, sql, args=()):
    return con.execute(sql, args).fetchone()[0]


def test_项目会跨越多年(played):
    """§41 重大项目可能跨越多年，不是当年立项当年结项。"""
    spans = played.execute(
        "SELECT CAST((julianday(closed_date)-julianday(proposed_date))/365 AS INT) y "
        "FROM project WHERE closed_date IS NOT NULL").fetchall()
    assert spans, "四十年里一个项目都没结项"
    assert max(s["y"] for s in spans) >= 3, "没有任何项目跨越三年以上"


def test_每个环节都有人经手(played):
    """§42 决策留痕：提出、批准、签批、实施、监督。"""
    p = played.execute("SELECT id FROM project WHERE state='CLOSED' LIMIT 1").fetchone()
    roles = {d["role"] for d in projects.decisions(played, p["id"])}
    assert roles >= {"提出", "批准", "签批", "实施", "监督"}, roles


def test_留痕记的是当时的职务(played):
    """人会升迁，但留痕必须记住他签字那天是什么职务。"""
    for p in played.execute("SELECT id FROM project WHERE state='CLOSED' LIMIT 5"):
        for d in projects.decisions(played, p["id"]):
            assert d["title_then"], f"{d['role']} 没记下当时的职务"


def test_违规发生时没有人知道(played):
    """§44 关键：埋下问题的那一刻不写任何公开事件。"""
    latent = played.execute(
        "SELECT count(*) FROM conduct_record WHERE status='LATENT'").fetchone()[0]
    assert latent >= 0
    # 潜伏中的记录不该出现在任何公开事件里
    for r in played.execute("SELECT id FROM conduct_record WHERE status='LATENT'"):
        pattern = '%"record": ' + str(r["id"]) + ',%'
        hit = played.execute(
            "SELECT count(*) FROM world_event WHERE visibility='PUBLIC' AND data LIKE ?",
            (pattern,)).fetchone()[0]
        assert hit == 0, f"潜伏中的问题 {r['id']} 却有公开事件"


def test_后果可以潜伏很多年(played):
    """§44 原文举的例子是 1994 的事 2003 才出线索。
    如果一两年就查出来，这个系统就没有意义了。"""
    lat = played.execute(
        "SELECT max((julianday(discovered_date)-julianday(behavior_date))/365) "
        "FROM conduct_record WHERE discovered_date IS NOT NULL").fetchone()[0]
    assert lat is not None, "四十年里一条线索都没出现"
    assert lat >= 5, "最长潜伏期只有 %.1f 年，违规与发现没有真正分离" % lat


def test_查处渠道都是真实存在的那几条(played):
    ch = {r[0] for r in played.execute(
        "SELECT DISTINCT discovered_by FROM conduct_record "
        "WHERE discovered_by IS NOT NULL")}
    assert ch <= set(discipline.CHANNELS), ch


def test_在查期间进不了候选池(played):
    """§35 纪律状态是硬门槛。这一条把整个纪律系统接进了任用系统。"""
    from gongpu import rules
    from gongpu.appointment import explain_for
    bad = played.execute(
        "SELECT id FROM character WHERE discipline_status='UNDER_REVIEW' LIMIT 1").fetchone()
    if bad is None:
        pytest.skip("本次模拟结束时没有在查人员")
    slot = played.execute(
        "SELECT id FROM position_slot LIMIT 1").fetchone()["id"]
    conds = explain_for(played, slot, bad["id"], date(2026, 12, 31),
                        rules.resolve(date(2026, 12, 31)))
    d = next(c for c in conds if c["条件"] == "纪律情况")
    assert d["ok"] is False


def test_撤职是真的从岗位上拿下来(played):
    """处分不能只是档案上一行字。

    但退休之后才被查处的没有职务可撤——退休不免责，也不存在"撤职"。
    """
    removed = played.execute(
        "SELECT character_id, closed_date, result FROM conduct_record "
        "WHERE status='CLOSED' AND result IN ('撤销党内职务','留党察看','开除党籍')"
    ).fetchall()
    if not removed:
        pytest.skip("本次模拟没有出现撤职级别的处分")
    checked = 0
    for r in removed:
        in_post = played.execute(
            "SELECT count(*) FROM office_holding WHERE character_id=? "
            "AND position_slot_id IS NOT NULL AND start_date <= ? "
            "AND (end_date IS NULL OR end_date >= ?)",
            (r["character_id"], r["closed_date"], r["closed_date"])).fetchone()[0]
        if not in_post:
            continue        # 查处时已经退休，没有职务可撤
        checked += 1
        held = played.execute(
            "SELECT count(*) FROM office_holding WHERE character_id=? "
            "AND exit_reason='DISCIPLINE'", (r["character_id"],)).fetchone()[0]
        assert held > 0, "查处时还在岗，却没有从岗位上拿下来"
    assert checked >= 0


def test_也会有人在任上被查(played):
    """查出来时他还在岗，甚至已经是县长了——这才是违规与发现分离
    最有意思的地方。如果查出来的清一色是已经退下来的人，这个机制就没了戏。

    注意要看**被查那一刻**在不在岗，不是看模拟结束时。
    1995 年被查的人到 2026 年当然已经退休了。
    """
    in_office = played.execute(
        "SELECT count(*) FROM conduct_record r "
        "WHERE r.discovered_date IS NOT NULL AND EXISTS ("
        "  SELECT 1 FROM office_holding h "
        "  WHERE h.character_id = r.character_id AND h.position_slot_id IS NOT NULL "
        "  AND h.start_date <= r.discovered_date "
        "  AND (h.end_date IS NULL OR h.end_date >= r.discovered_date))").fetchone()[0]
    assert in_office > 0, "四十年里没有一个人是在任上被查的"
