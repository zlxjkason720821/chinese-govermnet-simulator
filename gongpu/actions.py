"""玩家动作（技术文档 §53-§57）。

§54 说得很清楚：没有 NLP，自由文本无法可靠解析。
所以这里没有一个自由输入框。玩家的自由度来自组合（§55）：

    动作 + 对象 + 事项 + 方式

对象是库里真实存在的机构或人（有 id），事项是 work_item（有类型和时限）。
这样数据库才判定得了：联系的部门对不对口、召集会议够不够格、
这类事项用这个办法管不管用、有没有超过时限。

§57：权限不够时不弹"动作不可用"，而是把制度上的理由讲给玩家。
"""
from datetime import date

from gongpu import relations, tasks
from gongpu.appointment import LEVEL_ORDER, log_event

# needs：这个动作要选什么对象。ORG=机构，PERSON=人，NONE=不需要
# days：办这一趟要占掉几天。
#   这是"同时来两件事怎么办"的答案：时间是有限的，时限在走，你必须取舍。
#   没有这个代价，玩家可以在同一天把所有事都办完，选择就不成其为选择。
ACTIONS = {
    "请示": dict(label="向领导请示", needs="PERSON", scope="SUPERIOR", min_level=0, days=1,
                 note="把事情摆到上级那里，责任也就分担出去了"),
    "汇报": dict(label="汇报工作", needs="PERSON", scope="SUPERIOR", min_level=0, days=1,
                 note="让组织知道你在做什么"),
    "联系": dict(label="联系有关部门", needs="ORG", scope="DEPARTMENT", min_level=0, days=2,
                 note="横向协调，看对方买不买账"),
    "走访": dict(label="下去走访", needs="ORG", scope="GRASSROOTS", min_level=0, days=3,
                 note="下到乡镇去看，坐在办公室看不出问题"),
    "调阅": dict(label="调阅材料", needs="NONE", scope=None, min_level=0, days=2,
                 note="先把情况摸清楚"),
    "召开会议": dict(label="召开会议", needs="ORG", scope="OWN", min_level=2, days=2,
                     note="召集会议本身就是一种权力"),
    "提出建议": dict(label="提出工作建议", needs="NONE", scope=None, min_level=1, days=1,
                     note="有人会记住是谁提的"),
    "延期": dict(label="申请延期", needs="NONE", scope=None, min_level=0, days=1,
                 note="拖得过初一，拖不过十五"),
    "任免": dict(label="决定干部任免", needs="PERSON", scope="SUBORDINATE", min_level=5, days=2,
                 note="这一项需要相应的干部管理权限"),
    # 认识了人，行动里就得找得到他，否则关系页只是一张摆设。
    # 但人脉有两面：维系要花时间，动用要折交情。
    "走动": dict(label="走动走动", needs="PERSON", scope="CONTACT", min_level=0, days=1,
                 note="一年不走动，关系就淡了。维系是要占掉时间的"),
    "托人": dict(label="托人办事", needs="PERSON", scope="CONTACT", min_level=0, days=2,
                 note="找交情把门敲开。门能敲开，活还是得自己干"),
}

METHODS = ("当面", "电话", "书面", "会上")


class AuthorityError(Exception):
    """§57 不是"动作不可用"，是"这不属于你当前岗位的权限范围"。"""


def player_level(con, character_id):
    best = -1
    for (level,) in con.execute(
            "SELECT d.leadership_level FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.character_id = ? AND h.end_date IS NULL", (character_id,)):
        best = max(best, LEVEL_ORDER.get(level, 1))
    return max(best, 0)


def own_org(con, character_id):
    row = con.execute(
        "SELECT s.organization_id FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE h.character_id=? AND h.end_date IS NULL LIMIT 1", (character_id,)).fetchone()
    return row[0] if row else None


def available(con, character_id):
    """当前岗位能做的动作。不能做的也列出来并说明原因（§57）。"""
    lvl = player_level(con, character_id)
    return [{"key": k, "label": v["label"], "needs": v["needs"], "note": v["note"],
             "allowed": lvl >= v["min_level"],
             "reason": None if lvl >= v["min_level"]
                       else "这项事务不属于你当前岗位的权限范围。"}
            for k, v in ACTIONS.items()]


# 走访是下基层，不是去县委；联系是找对口工作部门，不是找四套班子本身。
# 对象范围本身就是制度的一部分，不能一股脑把所有机构都列出来。
SCOPE_TYPES = {
    "GRASSROOTS": ("TOWNSHIP",),
    "DEPARTMENT": ("GOVERNMENT", "PARTY", "SUPERVISION"),
}


def targets_for(con, character_id, action_type, work_item_id=None):
    """这个动作可以选哪些对象。全部来自库，玩家只能在其中挑（§55）。"""
    spec = ACTIONS[action_type]
    if spec["needs"] == "NONE":
        return []
    mine = own_org(con, character_id)
    my_level = player_level(con, character_id)

    if spec["needs"] == "ORG":
        if spec["scope"] == "OWN":
            rows = con.execute(
                "SELECT id, name, short_name, organization_type AS t "
                "FROM organization WHERE id=? AND active=1", (mine,)).fetchall()
        else:
            types = SCOPE_TYPES.get(spec["scope"])
            sql = ("SELECT id, name, short_name, organization_type AS t "
                   "FROM organization WHERE active=1")
            args = ()
            if types:
                sql += " AND organization_type IN (%s)" % ",".join("?" * len(types))
                args = types
                # 四套班子本身不是"对口部门"，联系的是它们下面的工作部门
                sql += " AND parent_id IS NOT NULL"
            rows = con.execute(sql + " ORDER BY id", args).fetchall()
        out = [{"id": r["id"], "name": r["short_name"] or r["name"], "tag": None}
               for r in rows]
        relevant = _relevant_names(con, work_item_id)
        if relevant:
            # 与当前事项对口的排前面并打标。标记只给界面看，
            # name 保持机构的真实名称——决策留痕里不能存界面装饰。
            for o in out:
                if any(k in o["name"] or o["name"] in k for k in relevant):
                    o["tag"] = "对口"
            out.sort(key=lambda o: (o["tag"] is None, o["id"]))
        return out

    # CONTACT：你认识的人。这一类不看隶属关系，看交情——
    # 党校同学调到别的县去了，组织关系上够不着，人还是那个人。
    if spec["scope"] == "CONTACT":
        out = []
        for c in relations.contacts(con, character_id):
            if c["id"] == character_id:
                continue
            where = _reach_tag(con, mine, c["org_id"])
            out.append({
                "id": c["id"],
                "name": "%s%s %s" % (c["org"] or "", c["post"] or "", c["name"])
                        if c["post"] else ("%s（%s）" % (c["name"], c["rank"])),
                "rank": c["rank"],
                "tag": "%s · %s · 熟悉%d 信任%d" % (
                    c["type"], where, c["familiarity"], c["trust"])})
        return out

    # PERSON：上级只能是你够得着的人。
    #
    # 原来的做法是"全世界层级比你高的领导"，于是一个县里的科员
    # 可以直接向总书记汇报工作。现实中你的汇报对象就三类：
    #   本单位的领导
    #   本单位上级机关的领导（县委办的人能找到县委领导）
    #   再往上一级的班子成员
    # 超出这个范围的人，你根本见不着。
    if spec["scope"] == "SUPERIOR":
        # 活动半径随层次放大：科员就在本单位和它的直接上级打转，
        # 到了正科级才谈得上跟再上一级的班子说得上话。
        chain = _reporting_chain(con, mine, hops=1 if my_level < 3 else 2)
        if not chain:
            return []
        rows = con.execute(
            "SELECT DISTINCT c.id, c.name, d.name AS post, "
            "COALESCE(o.short_name, o.name) AS org, "
            "d.leadership_level AS lvl, s.organization_id AS org_id "
            "FROM office_holding h "
            "JOIN character c ON c.id = h.character_id "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE h.end_date IS NULL AND c.alive=1 AND c.retired=0 "
            "AND d.is_leadership=1 AND c.id != ? "
            "AND s.organization_id IN (%s) ORDER BY c.id"
            % ",".join("?" * len(chain)), (character_id,) + tuple(chain)).fetchall()
        out = [r for r in rows if LEVEL_ORDER.get(r["lvl"], 0) > my_level]
        # 本单位的领导排最前：请示先找直接领导
        out.sort(key=lambda r: (r["org_id"] != mine, -LEVEL_ORDER.get(r["lvl"], 0)))
        # 隶属链上第几级，就标第几级：本单位 / 上级机关 / 再上一级
        tier = {org: i for i, org in enumerate(chain)}
        tags = ["本单位", "上级机关", "再上一级"]
        return [{"id": r["id"], "name": "%s%s %s" % (r["org"], r["post"], r["name"]),
                 "rank": r["lvl"],
                 "tag": tags[min(tier.get(r["org_id"], 1), len(tags) - 1)]}
                for r in out[:20]]

    if spec["scope"] == "SUBORDINATE":
        rows = con.execute(
            "SELECT c.id, c.name, d.name AS post, "
            "COALESCE(o.short_name, o.name) AS org, d.leadership_level AS lvl "
            "FROM office_holding h JOIN character c ON c.id = h.character_id "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE h.end_date IS NULL AND c.alive=1 AND c.retired=0 ORDER BY c.id").fetchall()
        return [{"id": r["id"], "name": f"{r['org']}{r['post']} {r['name']}",
                 "rank": r["lvl"], "tag": None}
                for r in rows if LEVEL_ORDER.get(r["lvl"], 0) < my_level][:30]
    return []


def _reporting_chain(con, org_id, hops=2):
    """你能直接打交道的机关：本单位，以及往上若干级的上级机关。

    再往上就不是"请示"，是越级——那不成立。
    一个县里的科员能向总书记汇报工作，是因为原来根本没有这个范围限制。
    """
    if org_id is None:
        return []
    chain = [org_id]
    cur = org_id
    for _ in range(hops):
        r = con.execute("SELECT parent_id FROM organization WHERE id=?", (cur,)).fetchone()
        if not r or not r[0]:
            break
        cur = r[0]
        chain.append(cur)
    return chain


def _relevant_names(con, work_item_id):
    """当前事项对口哪些单位。没有选事项就不排序。"""
    if not work_item_id:
        return []
    row = con.execute("SELECT kind FROM work_item WHERE id=?", (work_item_id,)).fetchone()
    if row is None:
        return []
    names = tasks.kinds()[row["kind"]].get("relevant", [])
    # "乡镇"是一类而不是一个单位名，展开成所有乡镇
    out = []
    for n in names:
        if n == "乡镇":
            out += [r[0] for r in con.execute(
                "SELECT COALESCE(short_name,name) FROM organization "
                "WHERE organization_type='TOWNSHIP' AND active=1")]
        else:
            out.append(n)
    return out


def _reach_tag(con, mine, other_org):
    """这个人在不在你平时够得着的范围里。

    够不着的那些才是人脉真正值钱的地方：组织关系上你和他没有任何往来，
    但你认识他。
    """
    if other_org is None:
        return "已不在岗"
    if other_org == mine:
        return "本单位"
    if other_org in _reporting_chain(con, mine, hops=2):
        return "上级机关"
    r = con.execute("SELECT parent_id FROM organization WHERE id=?", (other_org,)).fetchone()
    if r and r[0] and r[0] in _reporting_chain(con, mine, hops=2):
        return "兄弟单位"
    return "系统外"


def _org_name(con, org_id):
    row = con.execute("SELECT COALESCE(short_name, name) FROM organization WHERE id=?",
                      (org_id,)).fetchone()
    return row[0] if row else None


class ActionIntent:
    """§56 动作意图。由菜单组合而成，字段全是 id，不是自由文本。"""

    def __init__(self, action_type, target_id=None, work_item_id=None,
                 method="当面", priority="一般"):
        if action_type not in ACTIONS:
            raise ValueError(f"未知动作：{action_type}")
        self.action_type = action_type
        self.target_id = target_id
        self.work_item_id = work_item_id
        self.method = method
        self.priority = priority


def resolve(con, character_id, intent, on, rng, rules):
    """§57 先查权限，再核对对象是否真实存在，再判定事项，最后才落库。"""
    spec = ACTIONS[intent.action_type]
    if player_level(con, character_id) < spec["min_level"]:
        raise AuthorityError("这项事务不属于你当前岗位的权限范围。")

    target_name = None
    if spec["needs"] != "NONE":
        if intent.target_id is None:
            raise AuthorityError(f"{spec['label']}需要指明对象。")
        allowed = {t["id"]: t["name"] for t in targets_for(
            con, character_id, intent.action_type, intent.work_item_id)}
        if intent.target_id not in allowed:
            # 对象不在可选范围内——不是"没这个人"，是够不着或不归你管
            raise AuthorityError("这个对象不在你当前岗位能直接打交道的范围内。")
        target_name = allowed[intent.target_id]

    # 认识对口部门的人，一趟顶两趟。关系帮你把事办动，
    # 但它一条也进不了任免的条件（§69 关系好不能绕过程序）。
    bonus = 0
    if spec["needs"] == "ORG" and intent.target_id:
        trust, leader = relations.trust_with_leader_of(con, character_id, intent.target_id)
        if leader:
            relations.touch(con, character_id, leader, on, familiarity=3, trust=2,
                            kind="合作关系")
        if trust >= relations.SMOOTH_TRUST:
            bonus = 1
    if spec["needs"] == "PERSON" and spec["scope"] != "CONTACT" and intent.target_id:
        kind = "上下级" if spec["scope"] == "SUPERIOR" else "合作关系"
        relations.touch(con, character_id, intent.target_id, on,
                        familiarity=4, trust=3, kind=kind)

    # 走动不办事，它只维系关系。一次走动正好抵一年的自然衰减——
    # 你的时间就那么多，维护了这头就顾不上那头。
    if intent.action_type == "走动":
        return _visit(con, character_id, intent, on, spec, target_name)
    if intent.action_type == "托人":
        effect, note, bonus = _favor(con, character_id, intent, on)
        if effect is not None:
            return _finish(con, character_id, intent, on, spec, target_name, effect, note)

    effect, note = ("无关", "")
    if intent.work_item_id:
        effect, note = tasks.apply(con, intent.work_item_id, intent.action_type, on, rng,
                                   bonus=bonus)
        if bonus and effect in ("推进", "办结"):
            note += "　那边有熟人，事情比想象中顺。"
        if intent.action_type == "托人":
            # 人情不是白使的。托对了折一截，托错了白折。
            note += "　这一趟人情折进去了（工作信任 -%d）。" % relations.FAVOR_TRUST_COST
            if effect == "走过场":
                note += "他答应得痛快，可这类事不是靠交情能动的。"
            elif effect == "失当":
                note += "这种时候不该先想着找人。"

    return _finish(con, character_id, intent, on, spec, target_name, effect, note)


def _finish(con, character_id, intent, on, spec, target_name, effect, note):
    """判定完了才落库：注意度、动作流水、世界事件。"""
    delta = tasks.attention_delta(effect)
    if delta:
        con.execute(
            "INSERT INTO organization_attention(character_id,visibility,last_review) "
            "VALUES(?,?,?) ON CONFLICT(character_id) DO UPDATE SET "
            "visibility = max(min(organization_attention.visibility + ?, 10), 0), "
            "last_review = ?",
            (character_id, max(delta, 0), on.isoformat(), delta, on.isoformat()))

    con.execute(
        "INSERT INTO action_log(date,character_id,action_type,target,subject,method,"
        "outcome,work_item_id) VALUES(?,?,?,?,?,?,?,?)",
        (on.isoformat(), character_id, intent.action_type, target_name,
         None, intent.method, effect, intent.work_item_id))
    log_event(con, on, "player_action",
              {"action": intent.action_type, "target": target_name,
               "item": intent.work_item_id, "effect": effect},
              actors=[character_id], visibility="DEV")
    return {"action": intent.action_type, "label": spec["label"],
            "target": target_name, "method": intent.method,
            "effect": effect, "note": note, "days": spec["days"]}


def _visit(con, character_id, intent, on, spec, target_name):
    """走动：只维系关系，不办事。"""
    before = relations.get(con, character_id, intent.target_id)
    relations.touch(con, character_id, intent.target_id, on,
                    familiarity=relations.VISIT_FAMILIARITY,
                    trust=relations.VISIT_TRUST,
                    kind=(before["type"] if before else "合作关系"))
    after = relations.get(con, character_id, intent.target_id)
    note = "走了一趟。熟悉 %d→%d，工作信任 %d→%d。" % (
        (before["familiarity"] if before else 0), after["familiarity"],
        (before["working_trust"] if before else 0), after["working_trust"])
    if intent.work_item_id:
        note += "　手头那件事没动——走动不办事。"
    return _finish(con, character_id, intent, on, spec, target_name, "无关", note)


# 交情到这个数，托人才真的托得动。够不着就是开不了口。
FAVOR_TRUST_FLOOR = 35


def _favor(con, character_id, intent, on):
    """托人：先看托不托得动，再看这个人帮不帮得上。

    返回 (效果, 说明, bonus)。效果为 None 表示继续走正常判定。
    """
    who = next((c for c in relations.contacts(con, character_id)
                if c["id"] == intent.target_id), None)
    if who is None:
        return "走过场", "你和这个人还谈不上交情。", 0
    if not intent.work_item_id:
        return "走过场", "手头没有要托的事。空着手上门，交情也是要折的。", 0

    if who["trust"] < FAVOR_TRUST_FLOOR:
        relations.spend(con, character_id, intent.target_id, on, 2)
        return ("走过场",
                "%s是认识，交情还不到开口的份上（工作信任 %d，得 %d 以上）。"
                % (who["name"], who["trust"], FAVOR_TRUST_FLOOR), 0)
    if who["org_id"] is None:
        relations.spend(con, character_id, intent.target_id, on,
                        relations.FAVOR_WASTED_COST)
        return "走过场", "%s已经不在位子上了，说得上话，说了不算。" % who["name"], 0

    # 对不对口：他那个单位是不是管这件事的
    relevant = _relevant_names(con, intent.work_item_id)
    fit = any(k in (who["org"] or "") or (who["org"] or "") in k for k in relevant)
    if intent.work_item_id and relevant and not fit and not who["is_leadership"]:
        relations.spend(con, character_id, intent.target_id, on,
                        relations.FAVOR_WASTED_COST)
        return ("走过场",
                "这件事不归%s管，%s也只能替你问一句。人情倒是折进去了。"
                % (who["org"], who["name"]), 0)

    # 托人是敲门，不是收口。最后这一步不能靠人情，得自己去办。
    row = con.execute("SELECT progress FROM work_item WHERE id=?",
                      (intent.work_item_id,)).fetchone()
    if row and row["progress"] + 1 >= tasks.PROGRESS_TO_CLOSE:
        return ("走过场",
                "%s愿意帮忙，可事情到了这一步，最后这一趟得你自己跑。"
                % who["name"], 0)

    # 托得动。交情支掉一截，事情往前挪一步。
    relations.spend(con, character_id, intent.target_id, on, relations.FAVOR_TRUST_COST)
    return None, "", 1 if fit else 0


# 年度考核（§15 独立记录，不是隐藏实绩值）
ASSESSMENT_RESULTS = ("优秀", "称职", "基本称职", "不称职")


def annual_assessment(con, on, rng, rules):
    """年末考核。依据是办结了多少、逾期了多少——都是库里的客观记录。"""
    serving = [r[0] for r in con.execute(
        "SELECT id FROM character WHERE alive=1 AND retired=0 ORDER BY id")]
    if not serving:
        return 0
    quota = max(int(len(serving) * 0.15), 1)       # 优秀比例一般不超过 15%
    year_from = date(on.year, 1, 1).isoformat()
    scored = []
    for cid in serving:
        rec = tasks.record(con, cid, on, year_from)
        vis = con.execute("SELECT visibility FROM organization_attention WHERE character_id=?",
                          (cid,)).fetchone()
        score = (rec.get("办结", 0) * 2 - rec.get("逾期", 0) * 3
                 + (vis[0] if vis else 0) + rng["governance"].random() * 3)
        scored.append((score, cid, rec))
    scored.sort(key=lambda t: (-t[0], t[1]))
    for i, (score, cid, rec) in enumerate(scored):
        if rec.get("逾期", 0) >= 3:
            result = "基本称职"
        elif i < quota:
            result = "优秀"
        else:
            result = "称职"
        con.execute("INSERT OR REPLACE INTO assessment(character_id,year,result) "
                    "VALUES(?,?,?)", (cid, on.year, result))
    return len(scored)
