"""会议（蓝图 十七）。

蓝图原话：到了县级主要领导以上，游戏不应继续以个人办事为主要玩法。
玩家拥有的不是"决定所有事情的权力"，而是
**在一定程序中提出、协调、讨论和形成决定**。

所以会议不能是装饰。这里的做法是把本来就存在的程序环节搬到会上：

  任用状态机走到 DELIBERATION —— 那就是常委会讨论决定这一环（§34）
  项目走到 APPROVAL        —— 那就是政府常务会议研究立项（§41）

不上会，这两样就卡在那里不动。会议因此是承重的，不是过场。

玩家的位置决定他在会上能做什么：
  不是班子成员   议题涉及你时列席汇报，不参与决定
  班子成员       可以提出议题、发表意见
  主持人         定议题、拍板
"""
import json
from datetime import date, timedelta

from gongpu.appointment import LEVEL_ORDER, log_event

# 蓝图十七点名的几种会
PARTY_STANDING = "党委常委会"
GOV_EXECUTIVE = "政府常务会议"
PARTY_GROUP = "党组会议"
SPECIAL = "专题会议"

# 不同议题进入不同会议——这是蓝图十七的原话
TOPIC_ROUTING = {
    "appointment": PARTY_STANDING,      # 干部任免归党委
    "project": GOV_EXECUTIVE,           # 项目立项归政府
    "discipline": PARTY_STANDING,
    "work": SPECIAL,
}

DECISIONS = ("同意", "原则同意", "再研究", "缓议", "不同意")
PASSING = ("同意", "原则同意")

# 参加常委会的层次门槛：副处级以上才是县级班子成员
MEMBER_LEVEL = LEVEL_ORDER["副处级"]


def _org_for(con, kind, admin_level="COUNTY"):
    otype = "PARTY" if kind in (PARTY_STANDING, PARTY_GROUP) else "GOVERNMENT"
    return con.execute(
        "SELECT id, COALESCE(short_name,name) AS n FROM organization "
        "WHERE organization_type=? AND admin_level=? AND protocol_order IS NOT NULL "
        "ORDER BY id LIMIT 1", (otype, admin_level)).fetchone()


def members_of(con, org_id, admin_level="COUNTY"):
    """会议的**正式成员**。

    这一层要分清三件事，它们在会场里看着差不多，制度上完全不同：

      正式成员　有议事资格，决定是他们作出的
      列席　　　因为议题和你这摊有关，叫你进来说明情况，不是会议成员
      工作人员　办公厅负责文件、记录、会务的人，人在会场，但不议事

    党委常委会的正式成员只有常委——局长、厅长、办公厅处长级别再高，
    也不存在"当然进入常委会"的资格（《中国共产党地方委员会工作条例》：
    常委会由书记、副书记和其他常委组成，会议召集人可以根据工作需要
    确定有关人员列席）。

    政府常务会议这边法律写得更死：《地方各级人民代表大会和地方各级
    人民政府组织法》规定，常务会议由行政首长、副职和**秘书长**组成。
    所以一个发改委主任虽然是政府组成人员、可以参加政府全体会议，
    却不是常务会议的当然成员。
    """
    return [dict(r) for r in con.execute(
        "SELECT c.id, c.name, h.title_at_time AS title, d.leadership_level AS lvl, "
        " d.protocol_order AS po "
        "FROM office_holding h JOIN character c ON c.id = h.character_id "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.end_date IS NULL AND s.organization_id = ? AND d.is_leadership = 1 "
        "AND c.alive=1 AND c.retired=0 ORDER BY d.protocol_order, h.id", (org_id,))]


def pending_agenda(con, on, kind):
    """该上这个会的事项。全部来自库里真实卡在程序上的东西。"""
    items = []
    if kind == PARTY_STANDING:
        for r in con.execute(
                "SELECT p.id, p.position_slot_id AS slot, p.selected_id, "
                " COALESCE(o.short_name,o.name)||d.name AS title, c.name AS who "
                "FROM appointment_process p "
                "JOIN position_slot s ON s.id = p.position_slot_id "
                "JOIN organization o ON o.id = s.organization_id "
                "JOIN position_definition d ON d.id = s.position_definition_id "
                "LEFT JOIN character c ON c.id = p.selected_id "
                "WHERE p.state='DELIBERATION' AND p.closed_date IS NULL "
                "AND o.admin_level='COUNTY' ORDER BY p.id LIMIT 8"):
            items.append({"topic": "研究%s人选" % r["title"], "source": "appointment",
                          "source_id": r["id"], "detail": r["who"] or "候选人选"})
    elif kind == GOV_EXECUTIVE:
        for r in con.execute(
                "SELECT p.id, p.name, p.scale, COALESCE(o.short_name,o.name) AS org "
                "FROM project p LEFT JOIN organization o ON o.id = p.organization_id "
                "WHERE p.state='RESEARCH' AND o.admin_level='COUNTY' "
                # 论证期还没走完的不上会。上了的话，原来那条排期事件还挂着，
                # 会议推一步、排期再推一步，五六年的项目两年就建成了。
                "AND NOT EXISTS (SELECT 1 FROM scheduled_event se "
                "  WHERE se.fired=0 AND se.event_type='project_step' "
                "  AND se.data LIKE '%\"project\": ' || p.id || '%') "
                "ORDER BY p.id LIMIT 6"):
            items.append({"topic": "研究%s立项" % r["name"], "source": "project",
                          "source_id": r["id"],
                          "detail": "%s承办，体量%s" % (r["org"], "★" * r["scale"])})
    return items


def hold(con, on, kind, rng, rules, player_id=None):
    """开一次会。没有议题就不开——这一点本身就说明了会议不是装饰。"""
    org = _org_for(con, kind)
    if org is None:
        return None
    agenda = pending_agenda(con, on, kind)
    if not agenda:
        return None
    attendees = members_of(con, org["id"])
    if not attendees:
        return None
    chair = attendees[0]
    cur = con.execute(
        "INSERT INTO meeting(kind,organization_id,date,chair_id,attendees) "
        "VALUES(?,?,?,?,?)",
        (kind, org["id"], on.isoformat(), chair["id"],
         json.dumps([a["id"] for a in attendees])))
    mid = cur.lastrowid
    for i, it in enumerate(agenda, 1):
        # 议题要有汇报人：上会的事由承办单位的人来讲，不是凭空过一遍。
        reporter = _reporter_for(con, it)
        con.execute(
            "INSERT INTO meeting_item(meeting_id,topic,source,source_id,note,"
            "matter_id,proposer_org_id,reporter_id,sequence_no) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (mid, it["topic"], it["source"], it["source_id"], it["detail"],
             it["source_id"] if it["source"] == "work_item" else None,
             it.get("org_id"), reporter, i))
    _seat_participants(con, mid, org["id"], chair, attendees, agenda)
    log_event(con, on, "meeting",
              {"kind": kind, "org": org["n"], "items": len(agenda),
               "chair": chair["title"]},
              actors=[a["id"] for a in attendees])
    return mid


def _reporter_for(con, item):
    """谁上会汇报这个议题。

    项目由承办单位的正职来讲，干部任免由组织部门来讲。
    "汇报"是一种独立的参会身份：他为这一个议题而来，
    讲完就完了，不参与别的议题的讨论。
    """
    if item["source"] == "project":
        r = con.execute(
            "SELECT h.character_id FROM project p "
            "JOIN position_slot s ON s.organization_id = p.organization_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN office_holding h ON h.position_slot_id = s.id AND h.end_date IS NULL "
            "WHERE p.id=? AND d.is_leadership=1 "
            "ORDER BY d.protocol_order LIMIT 1", (item["source_id"],)).fetchone()
        return r[0] if r else None
    if item["source"] == "appointment":
        r = con.execute(
            "SELECT h.character_id FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN organization o ON o.id = s.organization_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.end_date IS NULL AND o.archetype='ORGANIZATION_DEPT' "
            "AND d.is_leadership=1 ORDER BY d.protocol_order LIMIT 1").fetchone()
        return r[0] if r else None
    return None


def _seat_participants(con, mid, org_id, chair, members, agenda):
    """登记参会身份。

    人在会场不等于列席，列席不等于会议成员——三件事在制度上完全不同，
    所以在库里也必须是三条不同的记录。
    """
    seen = set()
    con.execute(
        "INSERT INTO meeting_participant(meeting_id,character_id,role) VALUES(?,?,?)",
        (mid, chair["id"], "CHAIR"))
    seen.add(chair["id"])
    for m in members:
        if m["id"] in seen:
            continue
        con.execute(
            "INSERT INTO meeting_participant(meeting_id,character_id,role) "
            "VALUES(?,?,?)", (mid, m["id"], "MEMBER"))
        seen.add(m["id"])
    # 汇报人：为某一个议题而来
    for it in agenda:
        who = _reporter_for(con, it)
        if who and who not in seen:
            con.execute(
                "INSERT INTO meeting_participant(meeting_id,character_id,role,"
                "agenda_scope) VALUES(?,?,?,?)", (mid, who, "REPORTER", it["topic"]))
            seen.add(who)
    # 会务：办公厅的人负责文件、记录、纪要，没有成员权利
    for r in con.execute(
            "SELECT h.character_id AS cid FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE h.end_date IS NULL AND o.system_type='综合' AND o.parent_id=? "
            "ORDER BY h.id LIMIT 3", (org_id,)).fetchall():
        if r["cid"] in seen:
            continue
        con.execute(
            "INSERT INTO meeting_participant(meeting_id,character_id,role) "
            "VALUES(?,?,?)", (mid, r["cid"], "STAFF"))
        seen.add(r["cid"])


def resolve(con, meeting_id, on, rng, rules, player_choices=None):
    """形成决定，并把决定落回它来自的那个程序。

    决定不是随机拍的：讨论的是已经走完考察的人选、已经论证过的项目，
    所以多数会通过；卡住的那些是真有理由卡。
    """
    player_choices = player_choices or {}
    out = []
    for it in con.execute(
            "SELECT * FROM meeting_item WHERE meeting_id=? AND decision IS NULL "
            "ORDER BY id", (meeting_id,)).fetchall():
        if it["id"] in player_choices:
            d = player_choices[it["id"]]
        else:
            x = rng["governance"].random()
            d = "同意" if x < 0.62 else ("原则同意" if x < 0.80
                                        else ("再研究" if x < 0.92 else "缓议"))
        text, org_id, days = _decision_detail(con, it, d, on)
        con.execute(
            "UPDATE meeting_item SET decision=?,decision_text=?,"
            "responsible_org_id=?,deadline_date=?,status='DECIDED' WHERE id=?",
            (d, text, org_id, (on + timedelta(days=days)).isoformat() if days else None,
             it["id"]))
        _apply(con, it, d, on, rng, rules)
        # 闭环：决定定了谁去办、几天内办完，就要落成一件真的事项，
        # 并且挂上督办。不然"会上定了"就只是一行字。
        if org_id and days:
            _spawn_followup(con, it, d, text, org_id, on, days, meeting_id)
        out.append((it["topic"], d))
    return out


# 决定不是一个词就完了：它要说清楚谁去办、几天内办完。
DECISION_FOLLOWUP = {
    "同意": ("按会议决定办理", 30),
    "原则同意": ("原则同意，按会议提出的意见修改完善后报送", 21),
    "再研究": ("再作研究，补充材料后重新提请", 45),
    "缓议": ("暂缓，待条件成熟再议", 90),
    "不同意": (None, 0),
}


def _decision_detail(con, item, decision, on):
    """把决定写成一句能落地的话，并指明承办单位和时限。"""
    text, days = DECISION_FOLLOWUP.get(decision, (None, 0))
    if text is None:
        return "不同意，本次不予办理", None, 0
    org = None
    if item["source"] == "project":
        r = con.execute("SELECT organization_id FROM project WHERE id=?",
                        (item["source_id"],)).fetchone()
        org = r[0] if r else None
    elif item["source"] == "work_item":
        r = con.execute("SELECT organization_id FROM work_item WHERE id=?",
                        (item["source_id"],)).fetchone()
        org = r[0] if r else None
    return text, org, days


def _spawn_followup(con, item, decision, text, org_id, on, days, meeting_id):
    """会议决定 → 新的事项 + 督办。

    这是整个设计的闭环所在：决定拆成落实事项，事项进督办，
    督办到期要销号。否则会议就成了一个只会输出文字的装置。
    """
    who = con.execute(
        "SELECT h.character_id AS cid FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.end_date IS NULL AND s.organization_id=? AND d.is_leadership=1 "
        "ORDER BY d.protocol_order LIMIT 1", (org_id,)).fetchone()
    if who is None:
        return None
    due = (on + timedelta(days=days)).isoformat()
    mid = con.execute(
        "INSERT INTO work_item(kind,subject,organization_id,assignee_id,"
        "created_date,due_date,matter_type,origin_org_id,target_org_id,"
        "current_stage,parent_matter_id,importance,urgency) "
        "VALUES('督办',?,?,?,?,?,'SUPERVISION',?,?,'落实',?,?,?)",
        ("落实会议决定：%s" % item["topic"], org_id, who["cid"],
         on.isoformat(), due,
         con.execute("SELECT organization_id FROM meeting WHERE id=?",
                     (meeting_id,)).fetchone()[0],
         org_id, item["matter_id"],
         70 if decision == "同意" else 55, 60)).lastrowid
    con.execute(
        "INSERT INTO decision_log(event_date,matter_id,meeting_id,action_type,"
        "field,after_value,reason) VALUES(?,?,?,'MEETING_DECISION',?,?,?)",
        (on.isoformat(), mid, meeting_id, item["topic"], decision, text))
    return mid


def _apply(con, item, decision, on, rng, rules=None):
    """把会议决定落回程序。这一步让会议变成承重结构。"""
    if item["source"] == "appointment":
        if decision in PASSING:
            # 讨论决定通过，流程往下走一步——人选就是在这一步定下来的。
            # 必须走状态机本身，不能直接改字段，否则 selected_id 是空的。
            from gongpu import rules as R
            from gongpu.appointment import AppointmentProcess, ProcedureError
            try:
                proc = AppointmentProcess.load(con, item["source_id"], rng,
                                               rules or R.resolve(on), on)
                proc.advance(on)
                # 会上通过了，后面的环节由会议接着往下排
                from gongpu import scheduler
                from datetime import timedelta
                scheduler.schedule(con, on + timedelta(days=21), "appointment_step",
                                   slot_id=proc.slot_id,
                                   data={"project": None, "process": proc.id})
            except ProcedureError:
                log_event(con, on, "appointment_suspended",
                          {"process": item["source_id"], "reason": "会上无合适人选"})
        else:
            # 再研究/缓议：流程原地不动，下次会再议
            log_event(con, on, "appointment_deferred",
                      {"process": item["source_id"], "decision": decision})
    elif item["source"] == "project":
        if decision in PASSING:
            # 走 advance，不直接改字段——否则"批准"这一条决策留痕就没了，
            # 而留痕正是项目系统全部的意义所在（§42）。
            from gongpu import projects
            from gongpu import rules as R
            projects.advance(con, item["source_id"], on, rng,
                             rules or R.resolve(on), via_meeting=True)
            from gongpu import scheduler
            from datetime import timedelta
            scheduler.schedule(con, on + timedelta(days=projects.STATE_DAYS["APPROVAL"]),
                               "project_step", data={"project": item["source_id"]})
        else:
            log_event(con, on, "project_deferred",
                      {"project": item["source_id"], "decision": decision})


def player_role(con, cid, meeting_id):
    """玩家在这次会上是什么身份。

    人在会场不等于列席，列席不等于会议成员。这三件事必须分开：
    办公厅秘书处的干部负责文件、记录、纪要，本来就在会场工作，
    但他不是与会人员，也不因为坐在里面就有议事资格。
    """
    m = con.execute("SELECT * FROM meeting WHERE id=?", (meeting_id,)).fetchone()
    if m is None:
        return "无关"
    if m["chair_id"] == cid:
        return "主持"
    if cid in json.loads(m["attendees"] or "[]"):
        return "与会"
    # 议题涉及自己的，列席汇报
    hit = con.execute(
        "SELECT count(*) FROM meeting_item mi "
        "LEFT JOIN appointment_process p ON p.id = mi.source_id AND mi.source='appointment' "
        "WHERE mi.meeting_id=? AND p.selected_id=?", (meeting_id, cid)).fetchone()[0]
    if hit:
        return "列席"
    return "工作人员" if _is_secretariat(con, cid, m["organization_id"]) else "无关"


def _is_secretariat(con, cid, org_id):
    """这个人是不是这次会议的会务人员。

    办公厅（办公室）是为这个党委、政府办事的机关，全会、常委会、
    常务会议的会务本来就归它。在那里工作的人开会时人在场，
    干的是文件和记录。
    """
    mine = con.execute(
        "SELECT s.organization_id FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE h.character_id=? AND h.end_date IS NULL AND h.primary_position=1 "
        "LIMIT 1", (cid,)).fetchone()
    if mine is None:
        return False
    r = con.execute(
        "SELECT parent_id, system_type FROM organization WHERE id=?",
        (mine[0],)).fetchone()
    return bool(r and r["system_type"] == "综合" and r["parent_id"] == org_id)


def recent(con, limit=40, org_admin_level="COUNTY"):
    return [dict(r) for r in con.execute(
        "SELECT m.id, m.kind, m.date, COALESCE(o.short_name,o.name) AS org, "
        " c.name AS chair, "
        " (SELECT count(*) FROM meeting_item mi WHERE mi.meeting_id=m.id) AS n "
        "FROM meeting m LEFT JOIN organization o ON o.id = m.organization_id "
        "LEFT JOIN character c ON c.id = m.chair_id "
        "WHERE o.admin_level = ? ORDER BY m.id DESC LIMIT ?",
        (org_admin_level, limit))]


def items_of(con, meeting_id):
    return [dict(r) for r in con.execute(
        "SELECT mi.topic, mi.decision, mi.note, c.name AS proposer "
        "FROM meeting_item mi LEFT JOIN character c ON c.id = mi.proposer_id "
        "WHERE mi.meeting_id=? ORDER BY mi.id", (meeting_id,))]
