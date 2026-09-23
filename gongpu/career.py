"""玩家的职务变动规则。

这一套只管玩家，NPC 仍然走任用引擎的常规流程。规则是设计决定的：

  常规晋升   只能是直升（顶上你直接领导的位置）或跨部门调动，不能满县挑岗位。
  党校通道   结业之后必定提一级，但**去哪个岗位由组织谈话定**，给一到两个选择。
  代价       调职前夕手上有两件以上的事没办好，提级降为平调，
             而且调离本部门——原单位的同事关系全部清零。

最后那一条是整套规则的重量所在。没有它，党校就成了纯加速器；
有了它，"什么时候申请、申请之前先把手头的事清干净"本身就是一局要想的事。
"""
from datetime import date

from gongpu import relations
from gongpu.appointment import LEVEL_ORDER, log_event
from gongpu.db import title_of

UNFINISHED_LIMIT = 2        # 手上积压到这个数，提级降为平调
LOOKBACK_DAYS = 365


def unfinished(con, cid, on):
    """没办好的事，逐条列出来。

    两类：一年内已经逾期作废的，和眼下还压着但已经过了时限的。
    前者早就不在待办列表里了——只给个数字，玩家无从核对，
    只会觉得"明明什么都没有，凭什么说我有九件事没办好"。
    """
    since = date.fromordinal(on.toordinal() - LOOKBACK_DAYS).isoformat()
    rows = con.execute(
        "SELECT kind, subject, due_date, resolved_date, state FROM work_item "
        "WHERE assignee_id = :me AND ("
        "  (state='OVERDUE' AND resolved_date >= :since) "
        "  OR (state='PENDING' AND due_date < :on)) "
        "ORDER BY COALESCE(resolved_date, due_date) DESC",
        {"me": cid, "since": since, "on": on.isoformat()}).fetchall()
    return [dict(r, 情形=("逾期作废" if r["state"] == "OVERDUE" else "已过时限")) 
            for r in rows]


def unfinished_count(con, cid, on):
    return len(unfinished(con, cid, on))


def own_org(con, cid):
    from gongpu import actions
    return actions.own_org(con, cid)


def current_level(con, cid):
    from gongpu import actions
    return actions.player_level(con, cid)


def is_valid_move(con, cid, slot_id):
    """玩家能不能动到这个岗位上。

    只有两种：顶上本单位比自己高一级的位置（直升），
    或者到别的单位去干同一层次的活（跨部门调动）。
    """
    row = con.execute(
        "SELECT s.organization_id AS org, d.leadership_level AS lvl FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.id = ?", (slot_id,)).fetchone()
    if row is None:
        return False, "岗位不存在"
    target = LEVEL_ORDER.get(row["lvl"])
    cur = current_level(con, cid)
    mine = own_org(con, cid)
    if target is None:
        return False, "岗位层次不明"
    if target == cur + 1 and row["org"] == mine:
        return True, "直升"
    if target == cur and row["org"] != mine:
        return True, "跨部门调动"
    if target == cur + 1 and row["org"] != mine:
        return False, "跨单位的提拔要走组织调训通道"
    return False, "这个岗位不在你现在够得着的范围内"


def lateral_options(con, cid, on, rng, limit=2):
    """找几个同层次、别的单位的空缺，用于降格处理时的去向。"""
    cur = current_level(con, cid)
    lvl = next((k for k, v in LEVEL_ORDER.items() if v == cur), "科员")
    mine = own_org(con, cid)
    rows = con.execute(
        "SELECT s.id FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.status='VACANT' AND d.leadership_level = ? AND s.organization_id != ? "
        "ORDER BY s.id", (lvl, mine)).fetchall()
    return [r["id"] for r in rows[:limit]]


def pending_entitlement(con, cid):
    """结业了但还没安排职务——"待安排"。

    "上完必定提一级"不等于"结业当天就有位子"。岗位不能凭空生成（§19），
    组织能做的是把你记在名单上，等出缺再找你谈话。
    这个状态不另存字段，从履历本身推出来：最近一次本人申请的培训已经结业，
    而此后没有过任何新的任职。
    """
    t = con.execute(
        "SELECT * FROM training_enrollment WHERE character_id=? AND requested=1 "
        "AND completed=1 AND entitlement_used=0 ORDER BY end_date DESC LIMIT 1",
        (cid,)).fetchone()
    if t is None:
        return None
    later = con.execute(
        "SELECT count(*) FROM office_holding WHERE character_id=? "
        "AND position_slot_id IS NOT NULL AND start_date > ?",
        (cid, t["end_date"])).fetchone()[0]
    return None if later else dict(t)


def _use_entitlement(con, cid):
    ent = pending_entitlement(con, cid)
    if ent:
        con.execute("UPDATE training_enrollment SET entitlement_used=1 WHERE id=?",
                    (ent["id"],))


def promotion_options(con, cid, on, rng, rules, limit=3):
    """组织谈话时摆在桌上的岗位。

    蓝图十二、十三：同样是正科级，办公厅出身的人和乡镇出身的人，
    下一步本来就不该一样。所以这里按职业路线排序并标出类别——
    本系统上行排在前面，外放其次。

    注意这只影响"有哪些摆在桌上"，不改任何任职条件、不加任何概率。
    在组织部工作不会让你升得更快（蓝图十三的原话）。
    """
    from gongpu import tracks
    cur = current_level(con, cid)
    up = next((k for k, v in LEVEL_ORDER.items() if v == cur + 1), None)
    mine = own_org(con, cid)
    if not up:
        return []
    # 同级或更高一级优先。市委办公厅的人被安排去县政府办，职务层次没变，
    # 实际是往下走了一级——这不是正常的干部流动。
    rank = {"COUNTY": 0, "MUNICIPAL": 1, "PROVINCIAL": 2, "CENTRAL": 3}
    my_admin = con.execute(
        "SELECT admin_level FROM organization WHERE id=?", (mine,)).fetchone()
    my_admin = rank.get(my_admin[0] if my_admin else "COUNTY", 0)
    rows = con.execute(
        "SELECT s.id, o.admin_level AS al FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE s.status='VACANT' AND d.leadership_level = ? ORDER BY s.id",
        (up,)).fetchall()
    out = []
    for r in rows:
        kind, why = tracks.classify(con, cid, r["id"])
        here = rank.get(r["al"], 0)
        out.append({"slot": r["id"], "title": title_of(con, r["id"]),
                    "same_unit": _org_of(con, r["id"]) == mine,
                    "track": kind, "why": why,
                    "down": here < my_admin})
    out.sort(key=lambda o: (o["down"], tracks.ORDER.get(o["track"], 9),
                            not o["same_unit"], o["slot"]))
    return out[:limit]


def _org_of(con, slot_id):
    return con.execute("SELECT organization_id FROM position_slot WHERE id=?",
                       (slot_id,)).fetchone()[0]


def transfer_out_penalty(con, cid, on):
    """调离本部门：原单位的同事关系清零。

    新单位没人认识你，老单位的人也不再天天照面。
    这是"关系要花时间经营"最直接的一次体现。
    """
    mine = own_org(con, cid)
    if mine is None:
        return 0
    peers = [r[0] for r in con.execute(
        "SELECT h.character_id FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE h.end_date IS NULL AND s.organization_id = ? AND h.character_id != ?",
        (mine, cid))]
    n = 0
    for other in peers:
        lo, hi = (cid, other) if cid < other else (other, cid)
        cur = con.execute(
            "UPDATE relationship SET familiarity=0, working_trust=0 "
            "WHERE character_a=? AND character_b=?", (lo, hi))
        n += cur.rowcount
    return n


def take_post(con, cid, slot_id, on, rng, rules, reason="APPOINTMENT"):
    """把玩家放到某个岗位上，并按规则决定是提级还是降格处理。

    返回 dict：实际去向、是不是被降格、原因。
    """
    from gongpu.appointment import AppointmentProcess, ProcedureError
    bad = unfinished_count(con, cid, on)
    demoted = False
    note = None
    target = slot_id

    if bad >= UNFINISHED_LIMIT:
        # 手上一堆事没办好还想提级，组织不会答应。
        # 提级降为平调，而且调离本部门。
        demoted = True
        _use_entitlement(con, cid)      # 这次机会就此作废，否则惩罚只是推迟
        opts = lateral_options(con, cid, on, rng, limit=1)
        target = opts[0] if opts else None
        note = ("手头有 %d 件事没有办好。这次不提级了，平调，"
                "而且调离本部门。" % bad)
        if target is None:
            return {"ok": False, "demoted": True, "note": note +
                    "　眼下别的单位也没有合适的空缺，只能原地待着。"}

    if not demoted:
        _use_entitlement(con, cid)
    moving_out = _org_of(con, target) != own_org(con, cid)
    cleared = transfer_out_penalty(con, cid, on) if (demoted and moving_out) else 0

    old = [r[0] for r in con.execute(
        "SELECT position_slot_id FROM office_holding WHERE character_id=? "
        "AND end_date IS NULL AND position_slot_id IS NOT NULL", (cid,))]
    with con:
        con.execute("UPDATE office_holding SET end_date=?,exit_reason=? "
                    "WHERE character_id=? AND end_date IS NULL",
                    (on.isoformat(), "DEMOTED_TRANSFER" if demoted else "PROMOTED", cid))
        for s in old:
            con.execute("UPDATE position_slot SET status='VACANT',holder_id=NULL "
                        "WHERE id=?", (s,))
        title = title_of(con, target)
        con.execute(
            "INSERT INTO office_holding(character_id,position_slot_id,start_date,"
            "title_at_time) VALUES(?,?,?,?)", (cid, target, on.isoformat(), title))
        con.execute("UPDATE position_slot SET status='OCCUPIED',holder_id=? WHERE id=?",
                    (cid, target))
        log_event(con, on, "appointment",
                  {"slot": target, "title": title, "freed": old,
                   "demoted": demoted, "reason": reason}, actors=[cid])
    return {"ok": True, "demoted": demoted, "slot": target, "title": title,
            "note": note, "cleared": cleared, "unfinished": bad}
