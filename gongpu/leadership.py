"""领导班子的结构（《中国共产党工作机关条例》《地方组织法》）。

原来每个机关都是"一个正职 + 若干完全相同的副职"：

    局长
    副局长
    副局长
    副局长

现实里不是这样。同样写着"副局长"三个字，可能是：

    党组副书记、分管日常工作的副局长      ← 二把手，管全面
    党组成员、副局长                     ← 分管一摊
    副局长                               ← 党外干部，或者刚提的
    副局长（挂职）                       ← 两年就走，不占实际序列
    党组副书记、副局长（主持工作）        ← 局长空缺，他临时顶着

这几种在库里必须分得开，否则干部调动会把整个结构搞乱。
所以拆成三个维度：

    行政职务  局长 / 副局长
    党内职务  党组书记 / 党组副书记 / 党组成员
    班子角色  分管日常工作 / 普通分管 / 主持工作 / 兼任 / 挂职

**最要紧的一条：分管日常工作的副职不是每个机关都有的固定槽位。**
条例写的是：正职由上级机构领导成员兼任的，可以设分管日常工作的副职。
也就是说它出现在"一把手高配、兼任、还担着更高层职务"的机关里——
公安局长兼副县长、县委常委兼组织部长，这种地方才需要有人管日常。
普通的民政局、统计局没有，党组副书记也不自动等于常务副局长。
"""
from datetime import date

import yaml

from gongpu import paths
from gongpu.appointment import LEVEL_ORDER

_TPL = {}


def templates():
    """专属领导班子模板。按机关类型分开——组织部、公安、法院、税务
    连"副职怎么叫、谁是二把手"都不一样，不能套同一张模板。"""
    if not _TPL:
        _TPL.update(yaml.safe_load(
            (paths.DATA / "leadership_templates.yaml").read_text(encoding="utf-8")))
    return _TPL


def template_for(archetype):
    return templates()["archetypes"].get(archetype or "", {})


def daily_work_title(archetype, post):
    """分管日常工作的副职在这个机关怎么称呼。"""
    t = template_for(archetype)
    pat = t.get("daily_work_title") or templates()["defaults"]["daily_work_title"]
    return pat.format(post=post)


def operational_no2(con, org_id):
    """实际承担机关日常工作的主要副职。

    它和班子排序的第二位、党组副书记、法定代行职务的人，
    经常不是同一个：这四个"第二"必须分开。
    """
    r = con.execute(
        "SELECT h.character_id AS cid, h.party_post AS pp, d.name AS post, "
        " s.leadership_order AS lo, s.executive_deputy AS ed "
        "FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.end_date IS NULL AND s.organization_id=? AND d.is_leadership=1 "
        "AND COALESCE(s.leadership_order, 99) > 1 "      # 先把正职排除掉
        "ORDER BY s.executive_deputy DESC, s.leadership_order LIMIT 1",
        (org_id,)).fetchone()
    return dict(r) if r else None

# 分管日常工作的副职，各机关的叫法不一样。
# 党委部门和办公厅多用"常务副X"或"分管日常工作的副X"，
# 中央一级现在的公文多写"分管日常工作的副部长"。
EXECUTIVE_NAME = {
    "组织": "常务副{post}", "宣传": "常务副{post}", "统战": "常务副{post}",
    "政法": "常务副{post}", "综合": "分管日常工作的副{post}",
    "政法系统": "常务副{post}",
}
DEFAULT_EXECUTIVE = "常务副{post}"

# 党内职务序列。行政副职里排第一的那位通常是党组副书记，
# 但**党组副书记不自动等于常务副职**，这两件事要分开。
PARTY_POSTS = ["党组副书记", "党组成员", "党组成员", None]

# 挂职：跟班学习性质，一般两年，不进实际班子序列。
SECONDMENT_YEARS = 2


def executive_title(post, system, archetype=None):
    """分管日常工作的副职怎么称呼。先按机关模板，再按系统。"""
    if archetype:
        t = template_for(archetype)
        if t.get("daily_work_title"):
            return t["daily_work_title"].format(post=post)
    return EXECUTIVE_NAME.get(system, DEFAULT_EXECUTIVE).format(post=post)


def wants_daily_work_deputy(archetype):
    """这类机关默认设不设分管日常工作的副职。

    模板里写了 operational_no2 的才设。发改、财政、卫健、审计、
    国资这些明确写成 null——它们不该人人都有常务副职。
    """
    return bool(template_for(archetype).get("operational_no2"))


def head_is_concurrent(con, org_id):
    """这个机关的正职，是不是由更高层的领导兼着。

    判断依据是库里的事实：这个人同时还占着另一个层次更高的岗位。
    §20 兼任就是同一个人并存多条任职记录。
    """
    head = con.execute(
        "SELECT h.character_id, d.leadership_level AS lvl FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.end_date IS NULL AND s.organization_id=? AND d.is_leadership=1 "
        "ORDER BY d.protocol_order LIMIT 1", (org_id,)).fetchone()
    if head is None:
        return False, None
    mine = LEVEL_ORDER.get(head["lvl"], 0)
    other = con.execute(
        "SELECT max(CASE WHEN d.leadership_level IS NULL THEN 0 ELSE 1 END), "
        " d.leadership_level AS lvl, COALESCE(o.short_name,o.name) AS org, d.name AS post "
        "FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.end_date IS NULL AND h.character_id=? AND s.organization_id != ?",
        (head["character_id"], org_id)).fetchone()
    if other is None or other["lvl"] is None:
        return False, None
    if LEVEL_ORDER.get(other["lvl"], 0) > mine:
        return True, "%s%s" % (other["org"], other["post"])
    return False, None


def needs_executive_deputy(con, org_id):
    """这个机关该不该设分管日常工作的副职。

    条例：正职由上级机构领导成员兼任的，可以设。
    这正是公安局、组织部、宣传部、政法委、办公厅特别常见的原因——
    它们的一把手经常是党委常委或者政府副职兼着。
    """
    yes, why = head_is_concurrent(con, org_id)
    return yes, why


def install(con, on):
    """按上面这条规则，给该设的机关设上分管日常工作的副职，
    并给所有班子成员排序、补党内职务。

    只动编制和标记，不新建岗位——用现有副职里排第一的那个。
    """
    changed = []
    for org in con.execute(
            "SELECT id, COALESCE(short_name,name) AS n, system_type AS sys, "
            " archetype AS arch FROM organization WHERE active=1 AND simulated=1 "
            "AND parent_id IS NOT NULL ORDER BY id").fetchall():
        deputies = con.execute(
            "SELECT s.id, d.name AS post, d.protocol_order AS po "
            "FROM position_slot s "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE s.organization_id=? AND d.is_leadership=1 "
            "ORDER BY d.protocol_order, s.id", (org["id"],)).fetchall()
        if len(deputies) < 2:
            continue
        for i, r in enumerate(deputies):
            con.execute("UPDATE position_slot SET leadership_order=? WHERE id=?",
                        (i + 1, r["id"]))
        yes, why = needs_executive_deputy(con, org["id"])
        # 两个条件都要：这类机关本来就设（模板说了算），
        # 而且这一任正职确实是兼任（条例说了算）。
        if yes and not wants_daily_work_deputy(org["arch"]):
            yes = False
        if not yes:
            con.execute("UPDATE position_slot SET executive_deputy=0 "
                        "WHERE organization_id=?", (org["id"],))
            continue
        # 排在正职后面的第一个副职，设为分管日常工作
        second = deputies[1]
        con.execute("UPDATE position_slot SET executive_deputy=1 WHERE id=?",
                    (second["id"],))
        changed.append((org["n"], executive_title(second["post"].lstrip("副"),
                                                  org["sys"], org["arch"]), why))
    return changed


def assign_party_posts(con):
    """给班子成员补党内职务。

    党组副书记和常务副职是两件事：高度相关，但不是同一个概念。
    济南市发改委公开分工里就有两位"党组副书记、副主任"，
    其中一位协助主任抓全面，另一位另有分工。
    """
    n = 0
    # 政府工作部门、法院、检察院设党组。
    # 党的工作机关（组织部、宣传部、政法委、办公厅）本身就是党的机构，
    # 不设党组；四套班子自己更不套——"县委党委书记"是句病句。
    for org in con.execute(
            "SELECT id, organization_type AS t FROM organization "
            "WHERE active=1 AND simulated=1 AND parent_id IS NOT NULL "
            "AND organization_type IN ('GOVERNMENT','COURT','PROCURATORATE') "
            "AND protocol_order IS NULL").fetchall():
        kind = "党组"
        rows = con.execute(
            "SELECT h.id, s.leadership_order AS lo FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.end_date IS NULL AND s.organization_id=? AND d.is_leadership=1 "
            "AND s.leadership_order IS NOT NULL ORDER BY s.leadership_order",
            (org["id"],)).fetchall()
        for r in rows:
            lo = r["lo"] or 99
            if lo == 1:
                post = kind + "书记"
            elif lo == 2:
                post = kind + "副书记"
            elif lo <= 5:
                post = kind + ("成员" if kind == "党组" else "委员")
            else:
                post = None           # 排在后面的不一定进党组
            con.execute("UPDATE office_holding SET party_post=? WHERE id=?", (post, r["id"]))
            n += 1
    return n


def full_title(con, holding_id):
    """完整的职务称谓：党内职务、行政职务、班子角色合成一句。

        党组副书记、分管日常工作的副局长
        党组成员、副局长
        党组副书记、副局长（主持工作）
        副局长（挂职）
        副主任（正部长级）
    """
    r = con.execute(
        "SELECT h.character_id AS cid, h.party_post AS pp, h.acting_head AS ah, "
        " h.appointment_type AS at, h.personal_rank AS pr, d.name AS post, "
        " d.leadership_level AS lvl, s.executive_deputy AS ed, "
        " COALESCE(o.short_name,o.name) AS org, o.system_type AS sys, "
        " o.archetype AS arch "
        "FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.id=?", (holding_id,)).fetchone()
    if r is None:
        return None
    post = r["post"]
    if r["ed"] and post.startswith("副"):
        post = executive_title(post[1:], r["sys"], r["arch"])
    # 党内职务在前，行政职务在后：党组副书记、常务副局长
    parts = [r["org"] + (r["pp"] or "")] if r["pp"] else []
    parts.append(post if parts else r["org"] + post)
    title = "、".join(parts)
    if r["pr"] and r["pr"] != r["lvl"]:
        title += "（%s）" % r["pr"]          # 高配：副主任（正部长级）
    if r["ah"]:
        title += "（主持工作）"
    elif r["at"] == "SECONDMENT":
        title += "（挂职）"
    elif r["at"] == "CONCURRENT":
        # 兼任写全：主职在前。"副县长、公安局党组书记、局长"
        main = con.execute(
            "SELECT COALESCE(o.short_name,o.name) || d.name FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE h.character_id=? AND h.end_date IS NULL AND h.primary_position=1 "
            "LIMIT 1", (r["cid"],)).fetchone()
        if main:
            title = main[0] + "、" + title
    return title


def acting_heads(con, on):
    """正职空缺时，由排第一的副职主持工作。

    主持工作和分管日常工作完全不同：分管日常工作的时候正职还在，
    一把手仍然是正职；主持工作是正职的位子空着，副职临时顶上。
    所以这里不改他的职务，只加一个标记。
    """
    out = []
    vacancies = con.execute(
        "SELECT DISTINCT s.organization_id AS org FROM position_slot s "
        "WHERE s.status='VACANT' AND s.leadership_order=1").fetchall()
    if not vacancies:
        return out
    for org in vacancies:
        r = con.execute(
            "SELECT h.id, h.character_id FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "WHERE h.end_date IS NULL AND s.organization_id=? "
            "AND s.leadership_order=2 AND h.acting_head=0", (org["org"],)).fetchone()
        if r is None:
            continue
        con.execute("UPDATE office_holding SET acting_head=1 WHERE id=?", (r["id"],))
        out.append(r["character_id"])
    # 正职到任了，主持工作随之结束
    con.execute(
        "UPDATE office_holding SET acting_head=0 WHERE acting_head=1 AND end_date IS NULL "
        "AND position_slot_id IN (SELECT s2.id FROM position_slot s2 "
        "  WHERE EXISTS (SELECT 1 FROM position_slot s1 "
        "    WHERE s1.organization_id = s2.organization_id "
        "    AND s1.leadership_order=1 AND s1.status='OCCUPIED'))")
    return out
