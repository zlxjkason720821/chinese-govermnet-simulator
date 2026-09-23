"""NPC 与世界自转（技术文档 §19、§29、§30-33、§38）。

这个模块回答 §82 那个唯一重要的问题：
这个世界在玩家什么都不做的情况下，会不会自己运行下去。

会：人变老 → 到龄退休 → 岗位出现真实空缺 → 组织启动任用程序 →
程序逐级走完 → 新人上任 → 再过若干年他也退休。
"""
import json
from datetime import date, timedelta

from gongpu import (actions, discipline, projects, ranks, relations, scheduler,
                    tasks, training)
from gongpu import (central, documents, leadership, meetings, proranks,
                    secretary)
from gongpu.appointment import (AppointmentProcess, ProcedureError, STATES,
                                _age, log_event)
from gongpu.db import title_of
from gongpu.rules import retirement_age

APPOINTMENT_STEP_DAYS = 21          # 每个环节约三周，一次任免走大半年


def _level_order(con, char_id):
    """现任职务的层次序号。退休年龄按这个分档。"""
    from gongpu.appointment import LEVEL_ORDER
    best = 0
    for (lvl,) in con.execute(
            "SELECT d.leadership_level FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.character_id=? AND h.end_date IS NULL", (char_id,)):
        best = max(best, LEVEL_ORDER.get(lvl, 0))
    return best


def vacate(con, char_id, on, reason):
    """人离开岗位，岗位变空。§19 空缺只能这样产生。"""
    slots = [r["position_slot_id"] for r in con.execute(
        "SELECT position_slot_id FROM office_holding "
        "WHERE character_id=? AND end_date IS NULL", (char_id,))]
    con.execute("UPDATE office_holding SET end_date=?,exit_reason=? "
                "WHERE character_id=? AND end_date IS NULL", (on.isoformat(), reason, char_id))
    for sid in slots:
        con.execute("UPDATE position_slot SET status='VACANT',holder_id=NULL WHERE id=?", (sid,))
    # 调离警察（法官、检察官）工作岗位的，衔级不予保留（警衔条例第十九条）。
    # 这要在离岗的当下就办，不能等月初扫描——中间那段时间他既不在岗，
    # 库里却还挂着警衔。
    if reason not in ("RETIREMENT",):
        proranks.revoke_on_transfer(con, on)
    return slots


def _close_central_status(con, char_id, on):
    """党内身份终止。

    只有去世和开除党籍才算数。**退休不终止中央委员身份**——
    中央委员会是党代会选出来的一届名册，任期五年，任期内从岗位上退下来
    的人仍然是中央委员，要等下一届换届才不在名单里。

    原来把退休也算进来，结果三年掉了四十九个委员，候补委员被抽干去填缺。
    真实的二十届四年只递补了十四名。这两个数字差了一个数量级，
    差别就出在这一行。
    """
    central.seat_vacated(con, char_id, on, "去世或开除党籍")


def retire(con, char_id, on):
    # 退休只是离开岗位。中央委员是一届名册上的名字，不随岗位走。
    con.execute("UPDATE character SET retired=1 WHERE id=?", (char_id,))
    slots = vacate(con, char_id, on, "RETIREMENT")
    log_event(con, on, "retirement", {"slots": slots}, actors=[char_id])
    return slots


def die(con, char_id, on):
    con.execute("UPDATE character SET alive=0,death_date=? WHERE id=?", (on.isoformat(), char_id))
    _close_central_status(con, char_id, on)
    slots = vacate(con, char_id, on, "DEATH")
    log_event(con, on, "death", {"slots": slots}, actors=[char_id])
    return slots


def _death_probability(age):
    """年死亡概率的粗略年龄曲线。只用于让世界不至于人人活到一百岁。"""
    if age < 45:
        return 0.001
    if age < 60:
        return 0.004
    if age < 70:
        return 0.012
    if age < 80:
        return 0.035
    return 0.10


def annual_pass(con, on, rng, rules):
    """每年 1 月 1 日跑一遍全体在册干部（§29）。按 id 排序保证可复现。

    返回本次产生的空缺数，调用方据此决定要不要启动任用程序。
    """
    vacated = 0
    # 先取完：循环体里的 die/retire 会 UPDATE 这张表
    for c in con.execute("SELECT * FROM character WHERE alive=1 ORDER BY id").fetchall():
        age = _age(c["birth_date"], on)
        if rng["world"].random() < _death_probability(age):
            vacated += len(die(con, c["id"], on))
            continue
        if not c["retired"]:
            # 在查期间不予办理退休手续：案件要先了结。
            # 判断依据是有没有未了结的问题线索，不能只看 discipline_status——
            # 从出线索到正式立案之间状态还是 CLEAR，人正好从这个缝里走掉。
            if discipline.has_pending(con, c["id"]):
                continue
            # 党和国家领导人不在任期中途按年龄办退休：这一层的年龄线
            # 是党代会上的"七上八下"，在换届那一天起作用（蓝图二十）。
            # 不这样处理，会出现总书记到龄离任、位子空着、人还挂着常委身份
            # 这种库里自相矛盾的状态。
            from gongpu.appointment import LEVEL_ORDER as _LO
            if _level_order(con, c["id"]) >= _LO["副国级"]:
                continue
            limit = retirement_age(c["gender"], _level_order(con, c["id"]), on)
            if age >= limit:
                vacated += len(retire(con, c["id"], on))
    return vacated


def open_vacancy_processes(con, on, rng, rules):
    """有空缺就启动任用程序。没有空缺则什么都不发生。"""
    _fill_long_vacancies(con, on, rng, rules)
    _fill_background(con, on, rng, rules)
    started = []
    # 同一轮里一个人只能被安排一次。
    # "在办流程"那条互斥靠的是流程还开着，而背景层的流程当场就关了，
    # 于是同一批空缺会把同一个人反复抓走，履历上全是零天的任职。
    taken_now = set()
    for s in con.execute("""
            SELECT s.id FROM position_slot s
            JOIN position_definition d ON d.id = s.position_definition_id
            JOIN organization o ON o.id = s.organization_id
            WHERE s.status='VACANT' AND d.is_leadership=1
              AND o.simulated = 1
              -- 总书记和国家主席不走常规任用：前者由党代会选举产生，
              -- 后者由总书记兼任（三位一体）。让常规流程碰这两个岗位，
              -- 在任总书记会被"提拔"去当总理，总书记的位子反倒空出来。
              AND d.name != '总书记'
              AND o.organization_type != 'STATE'
              AND NOT EXISTS (SELECT 1 FROM appointment_process p
                              WHERE p.position_slot_id=s.id AND p.closed_date IS NULL)
            ORDER BY s.id"""):
        try:
            proc = AppointmentProcess(con, s["id"], on, rng, rules)
        except ProcedureError:
            continue                        # 没有管理权限的岗位，组织动不了
        if proc.selected_id in taken_now:
            continue
        if not _is_county_slot(con, s["id"]):
            # §30 分层模拟：玩家所在的县逐环节走完整程序（那是玩法本身），
            # 市级以上是背景世界，用结构化模拟一次走完。
            # 不分层的话，四套班子乘四级机构的状态机会把四十年模拟拖慢几十倍。
            who = _resolve_background(con, proc, on, rng, rules, taken_now)
            if who:
                taken_now.add(who)
            continue
        scheduler.schedule(con, on + _step(), "appointment_step", slot_id=s["id"],
                           data={"process": proc.id})
        started.append(proc.id)
    return started


def _step():
    from datetime import timedelta
    return timedelta(days=APPOINTMENT_STEP_DAYS)


def train_cadres(con, on, rng, rules):
    """组织调训。名额有限，按组织视野和考核挑人。

    NPC 不走校内玩法：他们去了、回来了，履历上多一条（§30 Tier B 结构化模拟）。
    但名额和轮训范围跟玩家是同一套——玩家不是特例。
    """
    sent = []
    for p in con.execute(
            "SELECT * FROM training_program WHERE valid_from <= ? AND valid_to >= ? "
            "ORDER BY id", (on.isoformat(), on.isoformat())).fetchall():
        targets = p["targets"].split(",")
        pool = []
        for r in con.execute(
                "SELECT c.id, c.birth_date, d.leadership_level AS lvl, "
                " COALESCE(oa.visibility,0) AS vis "
                "FROM office_holding h JOIN character c ON c.id = h.character_id "
                "JOIN position_slot s ON s.id = h.position_slot_id "
                "JOIN position_definition d ON d.id = s.position_definition_id "
                "LEFT JOIN organization_attention oa ON oa.character_id = c.id "
                "WHERE h.end_date IS NULL AND c.alive=1 AND c.retired=0 "
                "AND c.is_player=0 AND c.discipline_status='CLEAR' "
                "ORDER BY c.id").fetchall():
            if r["lvl"] not in targets:
                continue
            if p["max_age"] is not None and _age(r["birth_date"], on) > p["max_age"]:
                continue
            if p["program_type"] in training.completed_types(con, r["id"]):
                continue
            pool.append((r["vis"] + rng["career"].random() * 4, r["id"]))
        if not pool:
            continue
        pool.sort(key=lambda t: (-t[0], t[1]))
        for _, cid in pool[:p["quota"]]:
            start = on + timedelta(days=30 + int(rng["career"].random() * 200))
            con.execute(
                "INSERT INTO training_enrollment(character_id,program,program_type,"
                "school_level,start_date,end_date,completed,result) "
                "VALUES(?,?,?,?,?,?,1,'结业')",
                (cid, training.school_name(p["school_level"]) + p["name"],
                 p["program_type"], p["school_level"], start.isoformat(),
                 (start + timedelta(days=p["days"])).isoformat()))
            training.make_classmates(con, cid, p["school_level"], start, rng)
            sent.append(cid)
    if sent:
        log_event(con, on, "training_dispatch", {"count": len(sent)}, visibility="DEV")
    return sent


def _fill_background(con, on, rng, rules):
    """外省的空缺直接补人，不跑候选池。

    §72：没有直接参与玩家世界的干部只作为背景。那边谁当省长不影响这局，
    重要的是位子上有人、这些人存在、以后可能和玩家产生交集。
    """
    if on.day != 1:
        return []
    filled = []
    for r in con.execute(
            "SELECT s.id FROM position_slot s "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE s.status='VACANT' AND o.simulated = 0 ORDER BY s.id").fetchall():
        if transfer_in(con, r["id"], on, rng, rules):
            filled.append(r["id"])
    return filled


LONG_VACANCY_DAYS = 300     # 空这么久，上级就不等了
# 一次任用程序本身要走半年多，再加上上会可能被"再研究"，
# 所以这个数不能太小——小了就等于绕过程序直接派人。


def _fill_long_vacancies(con, on, rng, rules):
    """领导职务空太久，上级直接派人。

    决定要上常委会，而会上有可能"再研究"、"缓议"，一拖就是一年。
    现实中不会让县长的位子一直空着——拖到一定程度，市里就把人派下来了。
    只对上级管理的岗位生效；县管岗位空着就空着，那是县里自己的事。
    """
    if on.day != 1:
        return []
    filled = []
    for r in con.execute(
            "SELECT s.id, "
            " (SELECT max(h.end_date) FROM office_holding h "
            "  WHERE h.position_slot_id = s.id) AS last_end "
            "FROM position_slot s "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE s.status='VACANT' AND d.is_leadership=1 "
            "AND d.management_authority != 'COUNTY' AND o.simulated = 1 "
            "ORDER BY s.id").fetchall():
        if not r["last_end"]:
            continue
        if (on - date.fromisoformat(r["last_end"])).days < LONG_VACANCY_DAYS:
            continue
        if transfer_in(con, r["id"], on, rng, rules):
            con.execute("UPDATE appointment_process SET state='SUSPENDED',closed_date=? "
                        "WHERE position_slot_id=? AND closed_date IS NULL",
                        (on.isoformat(), r["id"]))
            filled.append(r["id"])
    return filled


def _resolve_background(con, proc, on, rng, rules, taken_now=None):
    """背景世界的任免：程序照走，但当天走完。

    状态机的每一环都走到，约束一条不少（空缺、资格、层次、任职年限、
    培训经历），省掉的只是"每三周推一步"这个排期。

    日期一律用当天。往未来推着提交等于时间旅行——岗位现在就被占了，
    而任职记录写的是半年后，任职区间会和后来的人重叠。
    """
    while proc.state != "APPOINTMENT":
        try:
            proc.advance(on)
        except ProcedureError:
            _suspend(con, proc.id, on, "无合适人选")
            transfer_in(con, proc.slot_id, on, rng, rules)
            return None
        if taken_now and proc.selected_id in taken_now:
            _suspend(con, proc.id, on, "拟任人选已另有安排")
            return None
    try:
        return proc.commit(on, title_of(con, proc.slot_id))
    except ProcedureError:
        _suspend(con, proc.id, on, "拟任人选情况发生变化")
        return None


def _is_county_slot(con, slot_id):
    r = con.execute(
        "SELECT o.admin_level FROM position_slot s "
        "JOIN organization o ON o.id = s.organization_id WHERE s.id=?",
        (slot_id,)).fetchone()
    return bool(r) and r[0] == "COUNTY"


def _suspend(con, process_id, on, reason):
    con.execute("UPDATE appointment_process SET state='SUSPENDED',closed_date=? WHERE id=?",
                (on.isoformat(), process_id))
    log_event(con, on, "appointment_suspended", {"process": process_id, "reason": reason})


def _advance_appointment(con, ev, on, rng, rules):
    pid = json.loads(ev["data"])["process"]
    try:
        proc = AppointmentProcess.load(con, pid, rng, rules, on)
    except ProcedureError:
        return          # 流程已中止或已结束，这条排期作废
    if proc.state == "DELIBERATION" and _is_county_slot(con, proc.slot_id):
        # 讨论决定这一环在党委常委会上发生（蓝图十七）。
        # 只有县级卡在这里等会——会议是玩家所在那一层的玩法。
        # 上面几级没有对应的会来推，卡住就会让整层排空。
        # 这里什么也不排期：会议会把它捡起来，通过之后由会议接着往下排。
        # 排期等会的话，一个卡住的流程每三周就生成一条调度记录，
        # 四十年下来调度表会涨到几万条。
        return
    try:
        proc.advance(on)
    except ProcedureError:
        # 无人合格：流程中止。市管岗位由上级调人补，县管岗位继续空着等下一轮。
        _suspend(con, pid, on, "无合适人选")
        transfer_in(con, proc.slot_id, on, rng, rules)
        return
    if proc.state == "APPOINTMENT":
        try:
            proc.commit(on, title_of(con, proc.slot_id))
        except ProcedureError:
            # 人选退休/去世/被查，任用中止。这是制度上的正常结果，不是异常。
            _suspend(con, pid, on, "拟任人选情况发生变化")
        return
    else:
        scheduler.schedule(con, on + _step(), "appointment_step",
                           slot_id=proc.slot_id, data={"process": pid})


def _finish_training(con, ev, on):
    """§38 结业是履历节点。它不改变任何人的职位。"""
    con.execute("UPDATE training_enrollment SET completed=1 WHERE id=?",
                (json.loads(ev["data"])["enrollment"],))
    log_event(con, on, "training_completed", {}, actors=[ev["character_id"]])
    # §36 结业进入组织视野，但视野不是晋升
    con.execute("""INSERT INTO organization_attention(character_id,visibility,last_review)
                   VALUES(?,1,?) ON CONFLICT(character_id) DO UPDATE SET
                   visibility=min(organization_attention.visibility+1,5),last_review=?""",
                (ev["character_id"], on.isoformat(), on.isoformat()))


def _advance_project(con, ev, on, rng, rules):
    proj = json.loads(ev["data"])["project"]
    nxt = projects.advance(con, proj, on, rng, rules)
    if nxt and nxt != "CLOSED":
        scheduler.schedule(con, on + timedelta(days=projects.STATE_DAYS.get(nxt, 180)),
                           "project_step", data={"project": proj})


def _new_project(con, on, rng, rules):
    """一个县一年上两三个大项目。项目是决策留痕的来源，也是纪律问题的来源。

    动议日期散在全年：都写成 1 月 1 日，项目表一眼就看得出是刷出来的。
    """
    for _ in range(3):
        if rng["governance"].random() > 0.7:
            continue
        when = on + timedelta(days=int(rng["governance"].random() * 330))
        pid = projects.propose(con, when, rng)
        if pid:
            scheduler.schedule(con, when + timedelta(days=projects.STATE_DAYS["PROPOSED"]),
                               "project_step", data={"project": pid})


# 换届：党代会、人代会各五年一届。换届前集中核查干部，这是查问题的一条真实渠道。
TERM_YEARS = 5


def _term_sessions(con, on, rng, rules):
    for kind, org_type in (("PARTY_CONGRESS", "PARTY"),
                           ("PEOPLES_CONGRESS", "PEOPLES_CONGRESS")):
        # 按 admin_level 找县级机构。原来靠 parent_id IS NULL 找，
        # 加了市级之后那样会找到市委。
        org = con.execute(
            "SELECT id FROM organization WHERE organization_type=? "
            "AND admin_level='COUNTY' AND protocol_order IS NOT NULL "
            "ORDER BY id LIMIT 1", (org_type,)).fetchone()
        if org is None:
            continue
        last = con.execute(
            "SELECT ordinal, held_date FROM term_session WHERE kind=? "
            "ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
        if last and (on - date.fromisoformat(last["held_date"])).days < TERM_YEARS * 365:
            continue
        ordinal = (last["ordinal"] + 1) if last else 1
        con.execute(
            "INSERT INTO term_session(kind,organization_id,ordinal,held_date) "
            "VALUES(?,?,?,?)", (kind, org["id"], ordinal, on.isoformat()))
        log_event(con, on, "term_session",
                  {"kind": kind, "ordinal": ordinal,
                   "note": "县第%d次党代会" % ordinal if kind == "PARTY_CONGRESS"
                           else "县第%d届人代会" % ordinal})
        # 换届考察：这时候会把干部过一遍，陈年的事可能就在这时候翻出来
        discipline.sweep(con, on, rng, rules, "换届考察")


HANDLERS = {
    "appointment_step": lambda con, ev, on, rng, rules: _advance_appointment(con, ev, on, rng, rules),
    "training_end": lambda con, ev, on, rng, rules: _finish_training(con, ev, on),
    "project_step": _advance_project,
}


MAX_PENDING = 3         # 手上同时压着的交办事项上限


def _keep_supplied(con, on, rng):
    """手头的事办完了，新的会派下来。

    半月一派、每次两成半概率，一年大约五六件。原来是逐日 25% 概率，
    一个科员一年会接到二十多件"重大交办事项"——既不像话，
    也让"调职前夕两件以上没办好就降格"这条规则变成必然触发。
    """
    if on.day not in (1, 16):
        return
    for (cid,) in con.execute("SELECT id FROM character WHERE is_player=1 AND alive=1 "
                              "AND retired=0"):
        n = con.execute("SELECT count(*) FROM work_item WHERE assignee_id=? "
                        "AND state='PENDING'", (cid,)).fetchone()[0]
        if n < MAX_PENDING and rng["event"].random() < 0.25:
            tasks.assign(con, cid, on, rng)


def tick(con, on, rng, rules):
    """推进到某一天：跑到期事项，元旦跑年度，分配季补员，有空缺才研究任用。

    空缺扫描不每天做：组织部不是每天开会研究缺人，有人走了或每月初才看一次。
    这同时是性能要害——每天全表扫描会让 40 年模拟慢一个数量级。
    """
    changed = False
    for ev in scheduler.due(con, on):
        scheduler.fire(con, ev["id"])
        h = HANDLERS.get(ev["event_type"])
        if h:
            h(con, ev, on, rng, rules)
            changed = True
    if (on.month, on.day) == (1, 1):
        # 纪律整套排在年度退休考察之前：先查、先了结，再论谁到龄。
        # 反过来的话，到龄的人会赶在线索出现或案件了结前办完手续，
        # 撤职就落空了——查处变成档案上一行字。
        discipline.sweep(con, on, rng, rules, "审计")
        if on.year % 3 == 0:
            discipline.sweep(con, on, rng, rules, "巡视")
        discipline.open_review(con, on, rng, rules)
        discipline.close_review(con, on, rng, rules)
        changed = annual_pass(con, on, rng, rules) > 0 or changed
        party_development(con, on, rng, rules)
        relations.decay(con, on)         # 一年不走动，关系就淡了
        if rules.civil_service:                # 1993 建立公务员制度后才有职级序列
            ranks.promote_ranks(con, on, rng, rules)
        # §44 发现渠道各有周期：审计年年有，巡视几年一轮
        _term_sessions(con, on, rng, rules)
        train_cadres(con, on, rng, rules)
        _new_project(con, on, rng, rules)
    # 事项有客观时限，到点没办就是逾期，不需要谁来裁量
    if tasks.sweep_overdue(con, on):
        changed = True
    # 办事就是办文。在办的公文每天往下推一步——
    # 推得动的条件是有人对得上那一步要的权限。
    if on.day % 5 == 0:
        documents.run(con, on, rng)
    # 领导动了，秘书按级别安排出路。跟人走是错的：秘书不会跟着领导
    # 到新单位去，他会被放到地方上当主政官员。（蓝图十二）
    if on.day == 1 and secretary.settle(con, on):
        changed = True
    if on.day == 1:
        # 中央委员出缺，候补委员依次递补（党章第二十二条）
        central.fill_vacancies(con, on)
        # 正职空缺时由排第一的副职主持工作。这和"分管日常工作"不是一回事：
        # 分管日常工作的时候正职还在，主持工作是正职的位子空着。
        # 主持工作每月看一次（位子空了就得有人顶）；
        # 班子排序和党内职务一年重排一次就够——每月扫全部机构太贵。
        leadership.acting_heads(con, on)
        proranks.finish(con, on)          # 挂职到期，回原单位
        proranks.revoke_on_transfer(con, on)   # 调离本系统的撤衔
        if on.month == 1:
            leadership.install(con, on)
            leadership.assign_party_posts(con)
            # 专业序列一年评一次。1992 年才有警衔，1997 年才评法官检察官等级，
            # 在那之前这一步什么也不做。
            proranks.grant(con, on)
            proranks.grant_high_rank(con, on, rng["world"])
            proranks.start(con, on, rng["world"])
    _keep_supplied(con, on, rng)
    if (on.month, on.day) == (12, 31):         # 年度考核
        actions.annual_assessment(con, on, rng, rules)
    # 全国党代会。这是唯一产生中央委员身份的时刻（蓝图二十四）。
    if central.is_congress_year(on):
        central.hold_congress(con, on, rng, rules)
        changed = True
    # 每月开一次常委会和常务会议。没有议题就不开。
    if on.day == 8:
        for kind in (meetings.PARTY_STANDING, meetings.GOV_EXECUTIVE):
            mid = meetings.hold(con, on, kind, rng, rules)
            if mid:
                meetings.resolve(con, mid, on, rng, rules)
                changed = True
    if on.day == 1 and rng["event"].random() < 0.12:
        discipline.sweep(con, on, rng, rules, "信访")
    if (on.month, on.day) == (7, 15):          # 毕业分配 / 考录季
        annual_intake(con, on, rng, rules)
        changed = True
    if changed or on.day == 1:
        open_vacancy_processes(con, on, rng, rules)


# 干部录用方式按年代变化：1993 公务员制度建立前是分配/招干，之后是考试录用。
def intake_method(rules):
    return "EXAM" if rules.civil_service else "ASSIGNMENT"


def recruit(con, slot_id, on, rng, rules, career_origin="ORDINARY"):
    """新干部进入这个世界。没有这个，县里的人只会越退越少。"""
    from gongpu.people import generate
    w = rng["world"]
    age = 20 + int(w.random() * 5)
    p = generate(w, on.year - age, on, career_origin)
    cur = con.execute(
        "INSERT INTO character(name,gender,birth_date,party_status,party_join_date,"
        "education_level,career_origin,work_start_date,personality,tier) "
        "VALUES(?,?,?,?,?,?,?,?,?,'C')",
        (p["name"], p["gender"], p["birth_date"], p["party_status"], p["party_join_date"],
         p["education_level"], career_origin, on.isoformat(),
         json.dumps(p["personality"])))
    cid = cur.lastrowid
    con.execute(
        "INSERT INTO office_holding(character_id,position_slot_id,start_date,title_at_time) "
        "VALUES(?,?,?,?)", (cid, slot_id, on.isoformat(), title_of(con, slot_id)))
    con.execute("UPDATE position_slot SET status='OCCUPIED',holder_id=? WHERE id=?",
                (cid, slot_id))
    if rules.civil_service:
        # 新录用人员按制度入序列：1993 起是公务员，之前只是"干部"
        system = rules.civil_service[-4:]
        con.execute(
            "INSERT INTO rank_holding(character_id,rank_name,rank_system,start_date,source) "
            "VALUES(?,?,?,?,'INITIAL')",
            (cid, "二级科员" if rules.rank_parallel else "办事员", system, on.isoformat()))
    log_event(con, on, "recruitment",
              {"slot": slot_id, "method": intake_method(rules), "origin": career_origin},
              actors=[cid])
    return cid


def annual_intake(con, on, rng, rules):
    """每年毕业分配/考录季补充基层。领导职务不在此列——那必须走任用程序。"""
    vacancies = [r["id"] for r in con.execute(
        "SELECT s.id FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.status='VACANT' AND d.is_leadership=0 ORDER BY s.id")]
    hired = []
    for sid in vacancies:
        if rng["world"].random() > 0.75:      # 编制不会当年全部补齐
            continue
        origin = ("SELECTED_GRADUATE" if rng["world"].random() < 0.12 else "ORDINARY")
        hired.append(recruit(con, sid, on, rng, rules, origin))
    if hired:
        log_event(con, on, "annual_intake",
                  {"count": len(hired), "method": intake_method(rules)}, visibility="DEV")
    return hired


def party_development(con, on, rng, rules):
    """入党是干部职业里的真实节点，不是出生就定死的标签。

    绝大多数领导职务要求党籍（§35）。干部进来时多数不是党员，
    如果此后永远不会入党，几年以后整个县就没有人够格当局长了。
    预备一年后转正，与制度一致。
    """
    for c in con.execute(
            "SELECT id,party_status,party_join_date FROM character "
            "WHERE alive=1 AND retired=0 AND party_status!='MEMBER' "
            "AND discipline_status='CLEAR' ORDER BY id").fetchall():
        if c["party_status"] == "PROBATIONARY":
            joined = date.fromisoformat(c["party_join_date"])
            if (on - joined).days >= 365:          # 预备期满一年转正
                con.execute("UPDATE character SET party_status='MEMBER' WHERE id=?", (c["id"],))
                log_event(con, on, "party_full_member", {}, actors=[c["id"]], visibility="DEV")
            continue
        if rng["career"].random() < 0.10:          # 每年约一成非党员干部被发展
            con.execute("UPDATE character SET party_status='PROBATIONARY',party_join_date=? "
                        "WHERE id=?", (on.isoformat(), c["id"]))
            log_event(con, on, "party_probationary", {}, actors=[c["id"]], visibility="DEV")


def transfer_in(con, slot_id, on, rng, rules):
    """县内无合适人选时，上级从外面调一个来（§72 背景干部池，需要时实例化）。

    这不是给引擎打补丁，是补一个真实存在的机制：
    县委书记、县长很多本来就是市里或外县交流来的，不是本县副处级一级级熬上来的。
    没有这条通道，一个县在四十年里会有五分之一的时间没有县长——那才是失真。

    §19 依然成立：这里有真实空缺才会发生。
    §21 也依然成立：只有市管岗位才由市里调人，县管岗位市里不插手。
    """
    from gongpu.people import generate
    from gongpu.appointment import LEVEL_ORDER
    pdef = con.execute(
        "SELECT d.* FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.id = ?", (slot_id,)).fetchone()
    # 上级调任不分层级：市里从外县调，省里从别的市调，中央从各省调。
    # 只有县管岗位必须从县内产生——那才是玩家真正在竞争的那一层。
    if pdef is None or pdef["management_authority"] == "COUNTY":
        return None
    # §19 岗位不能凭空生成，也不能一个坑塞两个人。
    # 流程开着的这几个月里可能已经有人上任了，这时候上级就不会再调人来。
    slot = con.execute("SELECT status, holder_id FROM position_slot WHERE id=?",
                       (slot_id,)).fetchone()
    if slot is None or slot["status"] != "VACANT" or slot["holder_id"] is not None:
        return None

    w = rng["career"]
    lo = pdef["min_age"] or 35
    hi = pdef["max_age"] or 58
    # 调进来的人取年龄区间的中段：太年轻撑不住，太老没几年就到点
    lo, hi = lo + (hi - lo) // 5, hi - (hi - lo) // 4
    age = lo + int(w.random() * max(hi - lo + 1, 1))
    p = generate(w, on.year - age, on, gender="F" if w.random() < 0.15 else "M")
    p["party_status"] = "MEMBER"
    p["party_join_date"] = date(on.year - age + 24, 7, 1).isoformat()
    # 资历要真的够：参加工作时间往前压到满足门槛
    need_year = on.year - max(pdef["min_years_experience"], 1) - 2
    p["work_start_date"] = date(min(need_year, on.year - age + 22), 7, 1).isoformat()

    cur = con.execute(
        "INSERT INTO character(name,gender,birth_date,party_status,party_join_date,"
        "education_level,career_origin,work_start_date,personality,tier) "
        "VALUES(?,?,?,?,?,?,?,?,?,'B')",
        (p["name"], p["gender"], p["birth_date"], p["party_status"], p["party_join_date"],
         p["education_level"], p["career_origin"], p["work_start_date"],
         json.dumps(p["personality"])))
    cid = cur.lastrowid

    # 调入前的经历压成一条已结束履历，履历上是外单位，不是本县任何一个岗位
    ws = date.fromisoformat(p["work_start_date"])
    con.execute(
        "INSERT INTO office_holding(character_id,position_slot_id,start_date,end_date,"
        "title_at_time,exit_reason) VALUES(?,NULL,?,?,?,'TRANSFER_IN')",
        (cid, ws.isoformat(), (on - timedelta(days=1)).isoformat(),
         "市直机关及外县任职"))
    title = title_of(con, slot_id)
    con.execute(
        "INSERT INTO office_holding(character_id,position_slot_id,start_date,title_at_time,"
        "holding_type) VALUES(?,?,?,?,'TRANSFER')",
        (cid, slot_id, on.isoformat(), title))
    con.execute("UPDATE position_slot SET status='OCCUPIED',holder_id=? WHERE id=?",
                (cid, slot_id))
    from gongpu.world import _backfill_training
    _backfill_training(con, cid, pdef["leadership_level"], on)
    log_event(con, on, "transfer_in", {"slot": slot_id, "title": title,
                                       "note": "上级调任，本地无合适人选"}, actors=[cid])
    return cid
