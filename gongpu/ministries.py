"""中央部委（§25 制度迁移）。

原来中央只有八个机构，于是"省委书记往上是什么"这个问题在库里没有答案：
正部级的位子总共两三个，中央委员会想凑人都凑不齐。

这个模块把中央那一层铺开，而且铺得带年代。国务院组成部门在 1986—2026
改过八次，每次都是全国人大一次会议上真实通过的：

    1998-03-10  一次撤掉冶金、化工、煤炭、机械、电力、林业、邮电……
                一代人的正部级岗位就此消失

机构撤销不是把一行数据删掉。位子上坐着人，撤了之后那个人要有去处——
这正是机构改革对干部意味着什么。
"""
from datetime import date

import yaml

from gongpu import paths
from gongpu.appointment import log_event

_CFG = {}

MINISTER_LEVEL = "正部级"


def cfg():
    if not _CFG:
        _CFG.update(yaml.safe_load(
            (paths.DATA / "central.yaml").read_text(encoding="utf-8")))
    return _CFG


def committee_rules():
    return cfg()["committee"]


def _d(x):
    return date.fromisoformat(str(x))


def _alive(x, on):
    return _d(x["from"]) <= on and (not x.get("to") or _d(x["to"]) > on)


def all_bodies():
    """党中央机构 + 国务院组成部门，合成一张表。"""
    out = []
    for x in cfg()["party"]:
        out.append(dict(x, kind="PARTY"))
    for x in cfg()["state_council"]:
        out.append(dict(x, kind="GOVERNMENT"))
    return out


def live_on(on):
    return [x for x in all_bodies() if _alive(x, on)]


def _head_post(body):
    return body.get("head") or "部长"


def _pdef(con, name):
    """正部级主要负责人的职务定义。同名的复用同一条。"""
    r = con.execute(
        "SELECT id FROM position_definition WHERE name=? AND leadership_level=? "
        "AND management_authority='CENTRAL' LIMIT 1",
        (name, MINISTER_LEVEL)).fetchone()
    if r:
        return r[0]
    return con.execute(
        "INSERT INTO position_definition(name,is_leadership,management_authority,"
        "min_age,max_age,party_requirement,min_years_experience,leadership_level,"
        "protocol_order,valid_from) VALUES(?,1,'CENTRAL',48,63,1,26,?,1,'1949-10-01')",
        (name, MINISTER_LEVEL)).lastrowid


def _create(con, body, on, rng, parent):
    from gongpu.world import _seat
    org = con.execute(
        "INSERT INTO organization(name,short_name,admin_level,protocol_order,"
        "organization_type,system_type,institution_grade,valid_from,parent_id,simulated) "
        "VALUES(?,?,'CENTRAL',5,?,?,?,?,?,1)",
        (body["name"], body["short"], body["kind"], body.get("system"),
         MINISTER_LEVEL, str(body["from"]), parent)).lastrowid
    con.execute("INSERT INTO cadre_management_authority(level,organization_id) "
                "VALUES('CENTRAL',?)", (org,))
    slot = con.execute(
        "INSERT INTO position_slot(position_definition_id,organization_id,"
        "valid_from,status) VALUES(?,?,?,'VACANT')",
        (_pdef(con, _head_post(body)), org, str(body["from"]))).lastrowid
    _seat(con, slot, MINISTER_LEVEL, on, rng["world"])
    return org


def _parents(con):
    def find(name):
        r = con.execute("SELECT id FROM organization WHERE name=? LIMIT 1",
                        (name,)).fetchone()
        return r[0] if r else None
    return {"PARTY": find("中国共产党中央委员会"), "GOVERNMENT": find("国务院")}


def install(con, on, rng):
    """开局时把那一天真实存在的中央机构建出来。"""
    par = _parents(con)
    n = 0
    for body in live_on(on):
        if con.execute("SELECT 1 FROM organization WHERE name=? AND active=1",
                       (body["name"],)).fetchone():
            continue
        _create(con, body, on, rng, par[body["kind"]])
        n += 1
    install_top(con, on, rng)
    return n


def install_top(con, on, rng):
    """副国级职务：国务委员、人大副委员长、政协副主席、中央书记处书记……

    这些是"省委书记往上"的答案。没有它们，正部级就是天花板。
    """
    from gongpu.world import _seat
    n = 0
    for t in cfg()["top"]:
        if _d(t["from"]) > on:
            continue
        org = con.execute("SELECT id FROM organization WHERE name=? LIMIT 1",
                          (t["org"],)).fetchone()
        if org is None:
            continue
        pdef = con.execute(
            "SELECT id FROM position_definition WHERE name=? AND leadership_level=? "
            "AND management_authority='CENTRAL' LIMIT 1",
            (t["post"], t["grade"])).fetchone()
        if pdef is None:
            pdef = (con.execute(
                "INSERT INTO position_definition(name,is_leadership,"
                "management_authority,min_age,max_age,party_requirement,"
                "min_years_experience,leadership_level,protocol_order,valid_from) "
                "VALUES(?,1,'CENTRAL',52,67,1,30,?,2,?)",
                (t["post"], t["grade"], str(t["from"]))).lastrowid,)
        have = con.execute(
            "SELECT count(*) FROM position_slot WHERE organization_id=? "
            "AND position_definition_id=?", (org[0], pdef[0])).fetchone()[0]
        for _ in range(max(t["n"] - have, 0)):
            slot = con.execute(
                "INSERT INTO position_slot(position_definition_id,organization_id,"
                "valid_from,status) VALUES(?,?,?,'VACANT')",
                (pdef[0], org[0], str(t["from"]))).lastrowid
            _seat(con, slot, t["grade"], on, rng["world"])
            n += 1
    return n


def reform(con, on, rng):
    """机构改革：这一天该设的设，该撤的撤。

    撤销一个部，不是删一行数据——部长的位子没了，人还在。
    他会回到候选池里，等着别处安排。这就是机构改革对干部的意思。
    """
    changed = []
    par = _parents(con)
    for body in all_bodies():
        if _d(body["from"]) == on:
            if not con.execute(
                    "SELECT 1 FROM organization WHERE name=? AND active=1",
                    (body["name"],)).fetchone():
                _create(con, body, on, rng, par[body["kind"]])
                log_event(con, on, "institution_established",
                          {"name": body["name"], "note": body.get("note", "")})
                changed.append(("设立", body["short"]))
        if body.get("to") and _d(body["to"]) == on:
            changed += _abolish(con, body, on)
    return changed


def _abolish(con, body, on):
    org = con.execute("SELECT id FROM organization WHERE name=? AND active=1",
                      (body["name"],)).fetchone()
    if org is None:
        return []
    displaced = [r[0] for r in con.execute(
        "SELECT h.character_id FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE s.organization_id=? AND h.end_date IS NULL", (org[0],))]
    con.execute("UPDATE office_holding SET end_date=?,exit_reason='INSTITUTION_ABOLISHED' "
                "WHERE position_slot_id IN (SELECT id FROM position_slot "
                "WHERE organization_id=?) AND end_date IS NULL", (on.isoformat(), org[0]))
    con.execute("UPDATE position_slot SET status='ABOLISHED',holder_id=NULL,valid_to=? "
                "WHERE organization_id=?", (on.isoformat(), org[0]))
    con.execute("UPDATE organization SET active=0,valid_to=? WHERE id=?",
                (on.isoformat(), org[0]))
    log_event(con, on, "institution_abolished",
              {"name": body["name"], "note": body.get("note", ""),
               "displaced": len(displaced)}, actors=displaced)
    return [("撤销", body["short"])]


def listing(con, on):
    """中央机构总览，给界面用。"""
    return [dict(r) for r in con.execute(
        "SELECT COALESCE(o.short_name,o.name) AS name, o.name AS full, "
        " o.system_type AS sys, o.organization_type AS kind, o.valid_from AS since, "
        " (SELECT COALESCE(c.name,'（空缺）') FROM position_slot s "
        "    LEFT JOIN character c ON c.id = s.holder_id "
        "    JOIN position_definition d ON d.id = s.position_definition_id "
        "    WHERE s.organization_id = o.id AND d.is_leadership=1 "
        "    ORDER BY d.protocol_order LIMIT 1) AS head, "
        " (SELECT d.name FROM position_slot s "
        "    JOIN position_definition d ON d.id = s.position_definition_id "
        "    WHERE s.organization_id = o.id AND d.is_leadership=1 "
        "    ORDER BY d.protocol_order LIMIT 1) AS post "
        "FROM organization o WHERE o.admin_level='CENTRAL' AND o.active=1 "
        "ORDER BY o.protocol_order, o.id")]
