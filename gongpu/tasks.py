"""工作事项与判定（技术文档 §53-§56、§58）。

数据库凭什么判定玩家办得对不对？

凭三件事，全是库里已有的事实：
  1. 事项类型 —— 信访件走访有用，起草材料走访没用
  2. 时限 —— due_date 在库里，逾期是客观事实
  3. 对象与权限 —— 联系的是不是真实存在的机构，召集会议够不够格

玩家的自由度来自组合（§55），不来自自由文本（§54）。
"""
import json
from datetime import date, timedelta
from pathlib import Path

from gongpu import paths

import yaml

from gongpu.appointment import log_event

DATA = paths.DATA
_CFG = {}

PROGRESS_TO_CLOSE = 3       # 光靠反复推进也能办成，但比用对办法慢


def cfg():
    if not _CFG:
        _CFG.update(yaml.safe_load((DATA / "work_items.yaml").read_text(encoding="utf-8")))
    return _CFG


def kinds():
    return cfg()["kinds"]


def assign(con, character_id, on, rng, organization_id=None):
    """给某人派一件事。

    事项从模板库里抽（§16 通用 + §17 专业机关），用 world 流——这是世界事实。

    抽哪一条取决于两件事：他在什么机关，他在哪个层次。
    组织部的科员摊上的是干部档案审核，公安局的科员摊上的是基层治安巡查，
    办公厅的科员摊上的是会务和批示流转。同样是科员，桌面上的东西不一样。

    同一个人手上不会同时出现两件一样的事，也不会刚办完又原样再来一遍。
    """
    if organization_id is None:
        row = con.execute(
            "SELECT s.organization_id FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "WHERE h.character_id=? AND h.end_date IS NULL LIMIT 1",
            (character_id,)).fetchone()
        organization_id = row[0] if row else None
    tpl = _pick_template(con, character_id, organization_id, on, rng)
    if tpl is None:
        return None
    # 交办方。上级部署、督办、批示这类事项是上级机关交下来的，
    # 不是本单位自己冒出来的——"谁交办的"是事项的基本信息之一（§3.1）。
    origin = organization_id
    if tpl.get("type") in ("INSTRUCTION", "SUPERVISION", "INSPECTION", "AUDIT"):
        up = con.execute("SELECT parent_id FROM organization WHERE id=?",
                         (organization_id,)).fetchone()
        origin = (up[0] if up and up[0] else organization_id)
    spec = cfg()["kinds"][tpl["kind"]]
    cur = con.execute(
        "INSERT INTO work_item(kind,subject,organization_id,assignee_id,created_date,"
        "due_date,matter_type,origin_org_id,target_org_id,current_stage,secrecy,"
        "importance,urgency) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tpl["kind"], tpl["t"], organization_id, character_id, on.isoformat(),
         (on + timedelta(days=tpl.get("days", spec["days"]))).isoformat(),
         tpl.get("type", "ROUTINE"), origin, organization_id, "承办",
         tpl.get("secrecy", 0), tpl.get("importance", 50),
         tpl.get("urgency", 50)))
    mid = cur.lastrowid
    # 大部分事项是要办文的。公文是事项的主要表现载体（V3 §21）：
    # 一件事走到哪一步，看的是文走到哪一步。
    if tpl.get("type") not in ("EMERGENCY", "PETITION"):
        from gongpu import documents
        documents.create_for(con, mid, on, rng)
    return mid


_MATTERS = {}


def templates():
    if not _MATTERS:
        import yaml
        _MATTERS.update(yaml.safe_load(
            (paths.DATA / "matters.yaml").read_text(encoding="utf-8")))
    return _MATTERS


def pool_for(con, organization_id, level):
    """这个机关、这个层次的人，可能摊上哪些事。

    专业机关的事排在前面（一个组织部的人主要办干部的事），
    通用事项族作为底子（谁都要报材料、谁都要应付督查）。
    """
    m = templates()
    arch = None
    if organization_id:
        r = con.execute("SELECT archetype FROM organization WHERE id=?",
                        (organization_id,)).fetchone()
        arch = r[0] if r else None
    out = list(m.get("by_archetype", {}).get(arch or "", []))
    out += [x for fam in m.get("common", {}).values() for x in fam]
    if level:
        out = [x for x in out if not x.get("levels") or level in x["levels"]]
    return out


def _pick_template(con, cid, org_id, on, rng):
    r = con.execute(
        "SELECT d.leadership_level FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.character_id=? AND h.end_date IS NULL AND h.primary_position=1 "
        "LIMIT 1", (cid,)).fetchone()
    pool = pool_for(con, org_id, r[0] if r else None)
    if not pool:
        return None
    recent = {x[0] for x in con.execute(
        "SELECT subject FROM work_item WHERE assignee_id=? "
        "AND (state='PENDING' OR resolved_date >= ?)",
        # 不能用 on.replace(year=...)：闰年 2 月 29 日会直接抛 ValueError
        (cid, (on - timedelta(days=365)).isoformat()))}
    fresh = [x for x in pool if x["t"] not in recent] or pool
    return rng["world"].choice(sorted(fresh, key=lambda x: x["t"]))


def pending(con, character_id, on):
    """待办事项。时限近的排前面。"""
    rows = con.execute(
        "SELECT * FROM work_item WHERE assignee_id=? AND state='PENDING' "
        "ORDER BY due_date, id", (character_id,)).fetchall()
    out = []
    for r in rows:
        spec = kinds()[r["kind"]]
        left = (date.fromisoformat(r["due_date"]) - on).days
        out.append({
            "id": r["id"], "kind": r["kind"], "label": spec["label"],
            "subject": r["subject"], "due_date": r["due_date"],
            "days_left": left, "progress": r["progress"],
            "desc": spec["desc"],
            "useful_actions": sorted(
                a for a, e in spec["actions"].items() if e in ("办结", "推进")),
        })
    return out


def judge(con, item_id, action_type, on):
    """判定一次动作对这件事的效果。返回 (效果, 说明)。

    全部依据库里的事实，没有任何"看起来像"的成分。
    """
    r = con.execute("SELECT * FROM work_item WHERE id=?", (item_id,)).fetchone()
    if r is None:
        return "无关", "没有这件事。"
    if r["state"] != "PENDING":
        return "无关", "这件事已经了结了。"
    if date.fromisoformat(r["due_date"]) < on:
        return "逾期", "已经过了时限。"
    spec = kinds()[r["kind"]]
    effect = spec["actions"].get(action_type)
    if effect is None:
        return "走过场", f"{action_type}对{spec['label']}这类事项不起作用。"
    if effect == "办结" and r["progress"] < spec.get("close_needs", 0):
        # 什么实事都还没做就想收口。汇报不会让事情自己办成。
        return "推进", "情况还没摸清，这时候报上去也定不下来。"
    return effect, {
        "办结": "事情办下来了。",
        "推进": "有进展，还没完。",
        "走过场": "合规，但对这件事没用。",
        "失当": "方式不对，会留下记录。",
    }[effect]


def apply(con, item_id, action_type, on, rng, bonus=0):
    """把判定结果写进世界。bonus 来自人熟，只加快推进，不改变动作对不对路。"""
    effect, note = judge(con, item_id, action_type, on)
    if effect in ("无关",):
        return effect, note
    if effect == "逾期":
        _close(con, item_id, on, "OVERDUE", "逾期")
        return effect, note
    if effect == "办结":
        _close(con, item_id, on, "DONE", "办结")
        return effect, note
    if effect == "推进":
        con.execute("UPDATE work_item SET progress = progress + ? WHERE id=?",
                    (1 + bonus, item_id))
        p = con.execute("SELECT progress FROM work_item WHERE id=?", (item_id,)).fetchone()[0]
        if p >= PROGRESS_TO_CLOSE:
            _close(con, item_id, on, "DONE", "办结")
            return "办结", "几次跑下来，事情办完了。"
    return effect, note


def _close(con, item_id, on, state, outcome):
    con.execute("UPDATE work_item SET state=?,resolved_date=?,outcome=? WHERE id=?",
                (state, on.isoformat(), outcome, item_id))


def sweep_overdue(con, on):
    """时限到了没办的，客观上就是逾期。不需要谁来判断。"""
    overdue = [r[0] for r in con.execute(
        "SELECT id FROM work_item WHERE state='PENDING' AND due_date < ?",
        (on.isoformat(),))]
    for i in overdue:
        _close(con, i, on, "OVERDUE", "逾期")
    return overdue


def attention_delta(effect):
    return cfg()["effect_attention"].get(effect, 0)


def record(con, character_id, on, year_from=None):
    """某人的办事记录，年度考核用得上。"""
    year_from = year_from or date(on.year, 1, 1).isoformat()
    rows = con.execute(
        "SELECT outcome, count(*) FROM work_item WHERE assignee_id=? "
        "AND resolved_date >= ? GROUP BY outcome", (character_id, year_from)).fetchall()
    return {r[0]: r[1] for r in rows if r[0]}
