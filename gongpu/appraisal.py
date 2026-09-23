"""人物评价（V3 §24）。

不能只有一个"政绩值"。

而且这十五项**不是存在库里的隐藏分**——它们是从记录里算出来的。
组织部门的人事研判读的是历史记录，不是

    总分 > 80 = 自动晋升

所以每一项都带着它的依据：办结了几件、逾期了几件、几次失当、
经手过几个项目、哪一年考核是什么等次。玩家看到的是事实，
不是一个凭空的数字——这也是 §15"取消隐藏实绩值"的意思。

这些维度互相之间不能相加。一个执行力强但协调能力差的人，
和一个样样中等的人，不是"总分谁高"的关系。
"""
from datetime import date

DIMENSIONS = [
    ("execution", "执行能力"),
    ("policy_ability", "政策业务能力"),
    ("coordination", "协调能力"),
    ("organization", "组织管理能力"),
    ("crisis_management", "应急能力"),
    ("legal_compliance", "合规记录"),
    ("discipline_risk", "纪律风险"),
    ("superior_evaluation", "上级评价"),
    ("peer_reputation", "班子同级评价"),
    ("subordinate_reputation", "下属评价"),
    ("public_response", "社会反馈"),
    ("project_record", "项目履历"),
    ("cadre_work_record", "干部工作履历"),
    ("error_record", "失误记录"),
    ("rectification_record", "整改记录"),
]
DIM_CN = dict(DIMENSIONS)


def _rate(good, bad, base=50, weight=30):
    """把"办成几件、办砸几件"折成一个刻度，同时保留原始计数。"""
    n = good + bad
    if n == 0:
        return None
    return int(base + weight * (good - bad) / n)


def _kind_stats(con, cid, kinds):
    """某几类事项的办结与逾期。"""
    row = con.execute(
        "SELECT sum(state='DONE') AS done, sum(state='OVERDUE') AS late, "
        " count(*) AS total FROM work_item "
        "WHERE assignee_id=? AND kind IN (%s)" % ",".join("?" * len(kinds)),
        (cid,) + tuple(kinds)).fetchone()
    return (row["done"] or 0), (row["late"] or 0), (row["total"] or 0)


def _outcome_count(con, cid, outcomes):
    return con.execute(
        "SELECT count(*) FROM action_log WHERE character_id=? AND outcome IN (%s)"
        % ",".join("?" * len(outcomes)), (cid,) + tuple(outcomes)).fetchone()[0]


def _peer_trust(con, cid, higher):
    """同级或下级对你的评价。看的是工作信任，不是好感度。"""
    rows = con.execute(
        "SELECT r.working_trust AS t, "
        " CASE WHEN r.character_a=:me THEN r.character_b ELSE r.character_a END AS other "
        "FROM relationship r WHERE r.character_a=:me OR r.character_b=:me",
        {"me": cid}).fetchall()
    my = _level_of(con, cid)
    vals = []
    for r in rows:
        lv = _level_of(con, r["other"])
        if lv is None or my is None:
            continue
        if (lv >= my) if higher else (lv < my):
            vals.append(r["t"])
    if not vals:
        return None, 0
    return int(sum(vals) / len(vals)), len(vals)


def _level_of(con, cid):
    from gongpu.appointment import LEVEL_ORDER
    r = con.execute(
        "SELECT d.leadership_level FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.character_id=? AND h.end_date IS NULL AND h.primary_position=1 "
        "LIMIT 1", (cid,)).fetchone()
    return LEVEL_ORDER.get(r[0]) if r else None


def of(con, cid, on=None):
    """这个人的十五个维度，每一项带依据。

    返回 [{维度, 值, 依据}]。值可以是 None——没有记录就是没有记录，
    不要拿一个默认分填上去装作知道。
    """
    on = on or date.today()
    out = []

    def add(key, val, why):
        out.append({"维度": DIM_CN[key], "code": key, "值": val, "依据": why})

    # 执行能力：交办的事办没办成，逾期了几件。
    done, late, total = _kind_stats(con, cid, ("督办", "材料", "协调", "调研",
                                               "信访", "应急"))
    add("execution", _rate(done, late),
        "经办 %d 件，办结 %d 件，逾期 %d 件" % (total, done, late) if total
        else "还没有交办记录")

    # 政策业务能力：材料、调研这类要动笔动脑的事，以及签发过多少文。
    d2, l2, t2 = _kind_stats(con, cid, ("材料", "调研"))
    signed = con.execute(
        "SELECT count(*) FROM document WHERE signer_id=?", (cid,)).fetchone()[0]
    drafted = con.execute(
        "SELECT count(*) FROM document WHERE drafter_id=?", (cid,)).fetchone()[0]
    add("policy_ability", _rate(d2, l2),
        "材料调研 %d 件办结 %d 件；起草公文 %d 份，签发 %d 份"
        % (t2, d2, drafted, signed))

    # 协调能力：协调类事项，加上和对口单位打交道的次数。
    d3, l3, t3 = _kind_stats(con, cid, ("协调",))
    contacts = con.execute(
        "SELECT count(*) FROM action_log WHERE character_id=? "
        "AND action_type IN ('联系','托人')", (cid,)).fetchone()[0]
    add("coordination", _rate(d3, l3),
        "协调事项 %d 件办结 %d 件；横向联系 %d 次" % (t3, d3, contacts))

    # 组织管理能力：带过班子没有，主持过会没有。
    led = con.execute(
        "SELECT count(*) FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.character_id=? AND d.is_leadership=1", (cid,)).fetchone()[0]
    chaired = con.execute(
        "SELECT count(*) FROM meeting WHERE chair_id=?", (cid,)).fetchone()[0]
    add("organization", (40 + min(led, 5) * 8 + min(chaired, 10) * 2) if led else None,
        "担任领导职务 %d 次，主持会议 %d 次" % (led, chaired) if led
        else "还没有担任过领导职务")

    # 应急能力：应急类事项。这类事没有第二次机会。
    d5, l5, t5 = _kind_stats(con, cid, ("应急",))
    add("crisis_management", _rate(d5, l5, base=50, weight=40),
        "应急事项 %d 件，办结 %d 件，逾期 %d 件" % (t5, d5, l5) if t5
        else "还没有遇到过应急事项")

    # 合规记录：办事方式失当、越权的次数。
    bad = _outcome_count(con, cid, ("失当", "越权"))
    acts = con.execute("SELECT count(*) FROM action_log WHERE character_id=?",
                       (cid,)).fetchone()[0]
    add("legal_compliance", max(0, 100 - bad * 12) if acts else None,
        "办事 %d 次，其中方式失当或越权 %d 次" % (acts, bad) if acts
        else "还没有可供判断的记录")

    # 纪律风险：这里只算**已经被发现**的。没发现的问题在库里躺着，
    # 但档案上就是干净的（§44），组织部门也看不见。
    found = con.execute(
        "SELECT count(*) FROM conduct_record WHERE character_id=? "
        "AND discovered_date IS NOT NULL", (cid,)).fetchone()[0]
    add("discipline_risk", found * 30 if found else 0,
        "已查实 %d 起" % found if found else "无已发现的问题")

    # 上级评价：年度考核等次。这是制度里真有的东西，不是好感度。
    grades = [r[0] for r in con.execute(
        "SELECT result FROM assessment WHERE character_id=? ORDER BY year DESC LIMIT 5",
        (cid,))] if _has_assessment(con) else []
    score = {"优秀": 90, "称职": 65, "基本称职": 45, "不称职": 20}
    add("superior_evaluation",
        int(sum(score.get(g, 60) for g in grades) / len(grades)) if grades else None,
        "近五年考核：%s" % "、".join(grades) if grades else "还没有考核记录")

    # 班子同级、下属：看工作信任的平均值，不看人数多少。
    v, n = _peer_trust(con, cid, higher=True)
    add("peer_reputation", v, "同级及以上 %d 人，平均工作信任 %s"
        % (n, v) if n else "还没有可参照的同级关系")
    v2, n2 = _peer_trust(con, cid, higher=False)
    add("subordinate_reputation", v2, "下级 %d 人，平均工作信任 %s"
        % (n2, v2) if n2 else "还没有可参照的下级关系")

    # 社会反馈：信访办得怎么样，经手的项目社会效果如何。
    d8, l8, t8 = _kind_stats(con, cid, ("信访",))
    eff = con.execute(
        "SELECT avg(p.social_effect) FROM project p "
        "JOIN project_decision pd ON pd.project_id = p.id "
        "WHERE pd.character_id=? AND p.state='CLOSED'", (cid,)).fetchone()[0]
    add("public_response", _rate(d8, l8) if t8 else (int(eff) if eff else None),
        "信访 %d 件办结 %d 件%s" % (t8, d8,
            "；经手项目社会效果均值 %d" % eff if eff else ""))

    # 项目履历：经手过什么项目、什么角色。这是审计和追责的入口。
    rows = con.execute(
        "SELECT pd.role, count(*) AS n FROM project_decision pd "
        "WHERE pd.character_id=? GROUP BY pd.role", (cid,)).fetchall()
    nproj = sum(r["n"] for r in rows)
    add("project_record", min(100, nproj * 6) if nproj else None,
        "经手项目环节 %d 次（%s）" % (
            nproj, "、".join("%s%d" % (r["role"], r["n"]) for r in rows))
        if nproj else "还没有经手过项目")

    # 干部工作履历：在组织人事部门干过多久。这是一种特殊履历（蓝图十三），
    # 但绝不等于"在组织部所以升得快"。
    # 用游戏里的日期，不能用 date('now')——那是现实世界的今天，
    # 1996 年的玩家会被算成"在组织部干了四十年"。
    days = con.execute(
        "SELECT sum(julianday(COALESCE(h.end_date, ?)) "
        "         - julianday(h.start_date)) FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.character_id=? AND o.archetype='ORGANIZATION_DEPT'",
        (on.isoformat(), cid)).fetchone()[0]
    yrs = (days or 0) / 365.25
    add("cadre_work_record", min(100, int(yrs * 14)) if yrs else None,
        "在组织人事部门任职 %.1f 年" % yrs if yrs else "没有干部工作经历")

    # 失误记录、整改记录：分开记。出过错和改没改，是两件事。
    over = con.execute(
        "SELECT count(*) FROM work_item WHERE assignee_id=? AND state='OVERDUE'",
        (cid,)).fetchone()[0]
    add("error_record", over + bad, "逾期 %d 件，方式失当 %d 次" % (over, bad))
    d9, l9, t9 = _kind_stats(con, cid, ("督办",))
    add("rectification_record", _rate(d9, l9) if t9 else None,
        "督办整改 %d 件，办结 %d 件" % (t9, d9) if t9 else "还没有整改事项")
    return out


_HAS_ASSESS = {}


def _has_assessment(con):
    if "v" not in _HAS_ASSESS:
        _HAS_ASSESS["v"] = bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assessment'"
        ).fetchone())
    return _HAS_ASSESS["v"]


def brief(con, cid, on=None):
    """给组织部门用的一句话研判。

    不给总分——这是故意的。研判要看的是哪一项强、哪一项弱、
    哪一项根本没有记录，而不是一个能排序的数。
    """
    dims = of(con, cid, on)
    got = [d for d in dims if d["值"] is not None]
    strong = sorted((d for d in got if d["code"] not in
                     ("discipline_risk", "error_record")),
                    key=lambda d: -d["值"])[:2]
    weak = [d for d in got if d["code"] not in ("discipline_risk", "error_record")
            and d["值"] is not None and d["值"] < 45]
    risk = [d for d in dims if d["code"] in ("discipline_risk", "error_record")
            and (d["值"] or 0) > 0]
    parts = []
    if strong:
        parts.append("长于%s" % "、".join(d["维度"] for d in strong))
    if weak:
        parts.append("%s偏弱" % "、".join(d["维度"] for d in weak[:2]))
    if risk:
        parts.append("有%s" % "、".join(d["维度"] for d in risk))
    missing = [d for d in dims if d["值"] is None]
    if missing:
        parts.append("%d 项没有记录" % len(missing))
    return "；".join(parts) or "记录尚少，不足以研判"
