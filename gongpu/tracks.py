"""职业路线（蓝图 十二、十三）。

同样是正科级，办公厅出身的人和乡镇出身的人，下一步本来就不该一样。
原来的做法是"找任何一个高一级的空缺"，结果所有人的路都长一个样：
副局长 → 局长 → 副县长。

这里定义的不是快慢，是**方向**：

  纵向　本系统内上行（办公厅的人往副主任、副秘书长走）
  外放　到了一定层次，路要往外走（办公厅待久了不外放，就一直是给人服务的）
  转系统　别的去向，比外放更远

蓝图十三划了一条线：组织部是一条特殊履历，但**绝不能变成"在组织部所以升得快"**。
所以这里只影响"有哪些去向摆在桌上"，不改任何任职条件、不加任何概率。
"""
from pathlib import Path

import yaml

from gongpu import paths

_CFG = {}


def cfg():
    if not _CFG:
        _CFG.update(yaml.safe_load(
            (paths.DATA / "tracks.yaml").read_text(encoding="utf-8")))
    return _CFG


def systems():
    return cfg()["systems"]


def label(system):
    s = systems().get(system)
    return s["label"] if s else (system or "—")


def note(system):
    s = systems().get(system)
    return s["note"] if s else ""


def system_of(con, org_id):
    if org_id is None:
        return None
    r = con.execute("SELECT system_type FROM organization WHERE id=?", (org_id,)).fetchone()
    return r[0] if r else None


def system_of_character(con, cid):
    r = con.execute(
        "SELECT o.system_type FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.character_id=? AND h.end_date IS NULL AND h.primary_position=1 "
        "ORDER BY h.id DESC LIMIT 1", (cid,)).fetchone()
    return r[0] if r else None


# 行政层级决定走哪张梯子。市委办公厅的科员和县委办的科员不是一条路——
# 这是原来最大的漏洞：两边共用一张表，于是谁的下一步都是那老三样。
TIER = {"COUNTY": "县", "MUNICIPAL": "市", "PROVINCIAL": "省",
        "CENTRAL": "中央"}
TIER_CN = {"县": "县级", "市": "市级", "省": "省级", "中央": "中央"}


def tier_of(con, org_id):
    if org_id is None:
        return "县"
    r = con.execute("SELECT admin_level FROM organization WHERE id=?", (org_id,)).fetchone()
    return TIER.get(r[0] if r else None, "县")


def _rung(system, tier):
    """取这个系统在这一层的梯子。这一层没写就落回县级。"""
    s = systems().get(system)
    if not s:
        return {}
    return s.get(tier) or s.get("县") or {}


def next_posts(system, level_name, tier="县"):
    """本系统在这个层级、这个层次上的典型下一步职务名。"""
    return list(_rung(system, tier).get("ladder", {}).get(level_name, []))


def exit_systems(system, level_name, tier="县"):
    """到了这个层次，这条路通常往哪些系统外放。"""
    return list(_rung(system, tier).get("exits", {}).get(level_name, []))


def exit_note(system, tier="县"):
    return _rung(system, tier).get("exit_note", "")


def secretary():
    return cfg().get("secretary", {})


def is_secretary_slot(con, slot_id):
    """这个岗位是不是服务某位领导的秘书岗。

    看的是服务关系，不是职务名。"书记秘书""市长秘书"不是全国统一的
    行政职务名称——人事表上写的是办公厅某个处的职务，工作上服务谁，
    是另一条信息。各地"秘书一处"服务的对象也并不一样。
    """
    r = con.execute("SELECT serves_slot_id FROM position_slot WHERE id=?",
                    (slot_id,)).fetchone()
    return bool(r and r[0])


def serves(con, slot_id):
    """这个秘书岗服务谁。返回领导的职务全称，不是秘书岗自己的名字。"""
    r = con.execute(
        "SELECT COALESCE(o.short_name,o.name) || d.name FROM position_slot s "
        "JOIN position_slot ls ON ls.id = s.serves_slot_id "
        "JOIN organization o ON o.id = ls.organization_id "
        "JOIN position_definition d ON d.id = ls.position_definition_id "
        "WHERE s.id = ?", (slot_id,)).fetchone()
    return r[0] if r else None


def classify(con, cid, slot_id):
    """这个岗位对这个人算哪一种去向：纵向、外放、还是别的。

    返回 (类别, 说明)。类别只影响界面怎么排、怎么标，不影响够不够格。
    """
    mine = system_of_character(con, cid)
    row = con.execute(
        "SELECT o.system_type AS sys, o.id AS org, d.name AS post, "
        " d.leadership_level AS lvl "
        "FROM position_slot s "
        "JOIN organization o ON o.id = s.organization_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.id = ?", (slot_id,)).fetchone()
    if row is None or mine is None:
        return "其他", ""
    cur_level = _current_level_name(con, cid)
    # 梯子按"你现在在哪一层"取：市委办公厅的人走市级那张表
    tier = tier_of(con, own_org(con, cid))
    if is_secretary_slot(con, slot_id):
        who = serves(con, slot_id)
        return "领导秘书", "服务%s。%s" % (who or "领导", secretary().get("caution", ""))
    if row["sys"] == mine:
        # 同上：职务重名很常见，系统对上了才算本系统的下一步
        if row["post"] in next_posts(mine, cur_level, tier):
            return "本系统上行", "%s%s内部的下一步" % (
                TIER_CN.get(tier_of(con, row["org"]), ""), label(mine))
        return "本系统", "还在%s" % label(mine)
    if row["sys"] in exit_systems(mine, cur_level, tier):
        return "外放", exit_note(mine, tier)
    return "转系统", "从%s转到%s" % (label(mine), label(row["sys"]))


def own_org(con, cid):
    r = con.execute(
        "SELECT s.organization_id FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE h.character_id=? AND h.end_date IS NULL AND h.primary_position=1 "
        "ORDER BY h.id DESC LIMIT 1", (cid,)).fetchone()
    return r[0] if r else None


def _current_level_name(con, cid):
    r = con.execute(
        "SELECT d.leadership_level FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.character_id=? AND h.end_date IS NULL AND h.primary_position=1 "
        "ORDER BY h.id DESC LIMIT 1", (cid,)).fetchone()
    return r[0] if r else "科员"


# 界面排序用：本系统上行排最前，其次外放，再是转系统
ORDER = {"本系统上行": 0, "领导秘书": 1, "外放": 2, "本系统": 3,
         "转系统": 4, "其他": 5}
