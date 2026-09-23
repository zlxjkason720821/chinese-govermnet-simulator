"""重大项目（技术文档 §41、§42）。

项目是这个世界里唯一会自己跨越十几年的东西。
它最重要的作用不是"建成了什么"，而是**在每个环节留下是谁经的手**（§42）。

十年后审计查到这个项目有问题，能一路追回去：谁提的、谁批的、谁签的、
谁实施的、谁监督的。那时候当年签字的人可能已经是县长了。
"""
import json
from datetime import date, timedelta

from gongpu.appointment import log_event

# §41 重大项目至少要走这几个状态，可能跨越多年
STATES = ["PROPOSED", "RESEARCH", "APPROVAL", "FUNDED", "IMPLEMENTATION",
          "INSPECTION", "OPERATING", "CLOSED"]
STATE_CN = {
    "PROPOSED": "动议", "RESEARCH": "论证", "APPROVAL": "报批", "FUNDED": "落实资金",
    "IMPLEMENTATION": "施工", "INSPECTION": "验收", "OPERATING": "运行", "CLOSED": "结项",
}
# 各环节大致耗时（天）。项目本来就慢。
STATE_DAYS = {
    "PROPOSED": 90, "RESEARCH": 180, "APPROVAL": 150, "FUNDED": 120,
    "IMPLEMENTATION": 540, "INSPECTION": 90, "OPERATING": 730,
}
ROLES = ("提出", "批准", "签批", "实施", "监督")

# 项目要落在对口的单位上。公安局承办供热管网、民政局盖小学，
# 决策链读起来就是假的——而决策链正是这个系统全部的价值所在。
NAMES_BY_ORG = {
    "教育局": ["青阳镇中心小学新建", "县第二中学教学楼改造", "农村学校危房改造"],
    "农业局": ["农田水利灌溉配套", "良种繁育基地建设", "农村电网配套改造"],
    "民政局": ["县社会福利院改扩建", "农村敬老院建设", "殡仪馆迁建"],
    "财政局": ["县级财政信息化系统", "政府投资项目审核中心"],
    "公安局": ["县公安局业务技术用房", "城区治安监控系统"],
    "审计局": ["审计业务用房改造"],
    "县政府办": ["县城自来水管网改造", "县城供热管网", "城区防洪堤加固",
                 "县城垃圾处理场", "工业园区标准厂房"],
}
# 乡镇项目名里不带具体乡镇名，否则会出现"柳林乡移民搬迁安置"由青阳镇承办
TOWNSHIP_NAMES = [
    "过境公路拓宽", "卫生院改造", "移民搬迁安置",
    "农村饮水安全工程", "集贸市场改建", "小型农田水利配套",
]
FALLBACK = ["县城道路改造", "供排水管网配套", "公共服务设施建设"]


def _names_for(org_name, org_type):
    if org_type == "TOWNSHIP":
        return TOWNSHIP_NAMES
    for key, names in NAMES_BY_ORG.items():
        if key in org_name:
            return names
    return FALLBACK


def _leader_of(con, org_id, nth=0):
    rows = con.execute(
        "SELECT h.character_id FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.end_date IS NULL AND s.organization_id = ? AND d.is_leadership = 1 "
        "ORDER BY d.protocol_order, h.id", (org_id,)).fetchall()
    return rows[nth]["character_id"] if len(rows) > nth else None


def record_decision(con, project_id, role, character_id, on, note=None):
    """§42 留痕。这一条写下去，十年后还能查到。"""
    if character_id is None:
        return
    con.execute(
        "INSERT INTO project_decision(project_id,role,character_id,date,note) "
        "VALUES(?,?,?,?,?)", (project_id, role, character_id, on.isoformat(), note))


def propose(con, on, rng, organization_id=None):
    """起一个新项目。提出人是本单位负责人。"""
    w = rng["governance"]
    if organization_id is None:
        orgs = [r[0] for r in con.execute(
            "SELECT id FROM organization WHERE active=1 AND admin_level='COUNTY' "
            "AND organization_type IN ('GOVERNMENT','TOWNSHIP') "
            "AND parent_id IS NOT NULL ORDER BY id")]
        if not orgs:
            return None
        organization_id = w.choice(orgs)
    org = con.execute(
        "SELECT COALESCE(short_name,name) AS n, organization_type AS t "
        "FROM organization WHERE id=?", (organization_id,)).fetchone()
    name = w.choice(_names_for(org["n"], org["t"]))
    scale = 1 + int(w.random() * 3)
    # 体量越大，埋下问题的概率越高。这不是设定，是留痕越多越查得出来。
    risk = int(w.random() * 30) + scale * 8
    cur = con.execute(
        "INSERT INTO project(name,organization_id,state,scale,risk,proposed_date) "
        "VALUES(?,?,'PROPOSED',?,?,?)",
        (name, organization_id, scale, risk, on.isoformat()))
    pid = cur.lastrowid
    record_decision(con, pid, "提出", _leader_of(con, organization_id), on, "动议")
    log_event(con, on, "project_proposed", {"project": pid, "name": name, "scale": scale})
    return pid


def advance(con, project_id, on, rng, rules, via_meeting=False):
    """推一个环节。每个环节都要有人经手，都留痕。

    via_meeting=True 表示这一步是政府常务会议研究通过的，可以进报批。
    """
    p = con.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
    if p is None or p["state"] == "CLOSED":
        return None
    i = STATES.index(p["state"])
    nxt = STATES[i + 1]
    if nxt == "APPROVAL" and not via_meeting:
        # 立项要上政府常务会议研究（蓝图十七）。引擎在这里停住等会。
        return None
    org = p["organization_id"]
    county_gov = con.execute(
        "SELECT id FROM organization WHERE organization_type='GOVERNMENT' "
        "AND admin_level='COUNTY' AND protocol_order IS NOT NULL "
        "ORDER BY id LIMIT 1").fetchone()

    if nxt == "APPROVAL":
        record_decision(con, project_id, "批准",
                        _leader_of(con, county_gov["id"] if county_gov else org), on,
                        "县政府批准立项")
    elif nxt == "FUNDED":
        czj = con.execute("SELECT id FROM organization WHERE name LIKE '%财政局' "
                          "AND admin_level='COUNTY' LIMIT 1").fetchone()
        record_decision(con, project_id, "签批",
                        _leader_of(con, czj["id"] if czj else org), on, "资金签批")
    elif nxt == "IMPLEMENTATION":
        record_decision(con, project_id, "实施", _leader_of(con, org), on, "组织施工")
    elif nxt == "INSPECTION":
        record_decision(con, project_id, "监督",
                        _leader_of(con, county_gov["id"] if county_gov else org, nth=1),
                        on, "组织验收")
        # §44 问题就是在这里埋下的：这时候没人发现，账要很多年后才算
        from gongpu import discipline
        discipline.maybe_plant(con, project_id, on, rng)

    con.execute("UPDATE project SET state=? WHERE id=?", (nxt, project_id))
    if nxt == "CLOSED":
        con.execute("UPDATE project SET closed_date=?,outcome='建成' WHERE id=?",
                    (on.isoformat(), project_id))
    log_event(con, on, "project_" + nxt.lower(),
              {"project": project_id, "name": p["name"], "state": STATE_CN[nxt]})
    return nxt


def decisions(con, project_id):
    return [dict(r) for r in con.execute(
        "SELECT pd.role, pd.date, pd.note, c.name, "
        " (SELECT h.title_at_time FROM office_holding h "
        "  WHERE h.character_id = pd.character_id AND h.start_date <= pd.date "
        "  ORDER BY h.start_date DESC LIMIT 1) AS title_then "
        "FROM project_decision pd JOIN character c ON c.id = pd.character_id "
        "WHERE pd.project_id = ? ORDER BY pd.id", (project_id,))]


def listing(con, limit=60):
    return [dict(r) for r in con.execute(
        "SELECT p.*, COALESCE(o.short_name, o.name) AS org FROM project p "
        "LEFT JOIN organization o ON o.id = p.organization_id "
        "ORDER BY p.id DESC LIMIT ?", (limit,))]
