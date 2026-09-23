"""干部任用状态机（技术文档 §19、§21、§34、§35、§64）。

三条硬约束，测试 §69 全部压在这上面：
  1. 没有真实空缺就没有任用 —— 岗位不能因为谁优秀而凭空长出来。
  2. 资格只决定"能不能进流程"，不决定"会不会上"。
  3. 流程必须逐级走完，没有任何输入能跳过中间状态。
"""
import json
from datetime import date

from gongpu.db import title_of

# 职务层次序列。任用只能平调或提拔一级：
# 蓝图文档"快速成长不等于跳级"，而无缘无故从副县长调去当副局长是降级，
# 不是正常的干部流动——那需要专门的免职/问责流程，不能由任用引擎顺手做掉。
LEVEL_ORDER = {"办事员": 0, "科员": 1, "副科级": 2, "正科级": 3,
               "副处级": 4, "正处级": 5, "副厅级": 6, "正厅级": 7,
               "副部级": 8, "正部级": 9, "副国级": 10, "正国级": 11}


def candidate_facts(con, on):
    """一次扫出筛选候选人要用的全部履历事实。

    全表扫一次，别逐人查——那是 N+1，候选池一筛就是几百人。

    条文来自时间轴文档 2014 条例一节：
      - 提任县处级领导职务：五年以上工龄、两年以上基层工作经历
      - 提任县处级以上：在下一级两个以上职位任职经历
      - 副职提任正职：副职岗位两年以上
      - 下级正职提任上级副职：下级正职岗位三年以上
    统一归结为：现职任职年限、工龄、基层年限、各层次任过几个职位。
    """
    iso = on.isoformat()
    facts = {}
    for r in con.execute(
            # LEFT JOIN：外单位和入职前的经历没有 slot，但照样算工龄
            "SELECT h.character_id AS cid, h.start_date, h.end_date, "
            "h.primary_position AS prim, "
            "d.leadership_level AS lvl, o.organization_type AS otype "
            "FROM office_holding h "
            "JOIN character ch ON ch.id = h.character_id "
            "LEFT JOIN position_slot s ON s.id = h.position_slot_id "
            "LEFT JOIN position_definition d ON d.id = s.position_definition_id "
            "LEFT JOIN organization o ON o.id = s.organization_id "
            # 退休和去世的人当不了候选人，他们的履历不用算
            "WHERE h.start_date <= ? AND ch.alive=1 AND ch.retired=0 "
            "ORDER BY h.character_id, h.start_date", (iso,)):
        f = facts.setdefault(r["cid"], {"tenure": 0.0, "total": 0.0,
                                        "grassroots": 0.0, "levels": {}})
        start = date.fromisoformat(r["start_date"])
        end = min(date.fromisoformat(r["end_date"]), on) if r["end_date"] else on
        yrs = max((end - start).days, 0) / 365
        f["total"] += yrs
        if r["otype"] == "TOWNSHIP" or (r["lvl"] and LEVEL_ORDER.get(r["lvl"], 0) <= 1):
            f["grassroots"] += yrs          # 乡镇工作与非领导职务算基层经历
        if r["end_date"] is None and r["prim"]:
            # 只算主职。兼任那条（比如总书记兼任国家主席）一挂十几年，
            # 算进去的话"现职任职年限"永远达标，人就能在几个岗位之间来回跳。
            f["tenure"] = max(f["tenure"], yrs)
        f["levels"].setdefault(r["lvl"], set()).add(r["start_date"])
    return facts


MIN_TENURE_YEARS = 2.0          # 副职提任正职：现职两年以上（同样用于平级交流）
MIN_TENURE_UP_LEVEL = 3.0       # 下级正职提任上级副职：下级正职三年以上
MIN_SERVICE_FOR_CHUJI = 5.0     # 提任县处级：五年以上工龄
MIN_GRASSROOTS_FOR_CHUJI = 2.0  # 提任县处级：两年以上基层工作经历
CHUJI = 4                       # 副处级在 LEVEL_ORDER 里的序号


def current_levels(con):
    """每个人现任职务的最高层次。一次查完，不逐人查。"""
    out = {}
    for cid, level in con.execute(
            "SELECT h.character_id, d.leadership_level FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.end_date IS NULL"):
        # 兼任时取层次最高的那个职务；按序号比，不能按字符串比
        if LEVEL_ORDER.get(level, -1) > LEVEL_ORDER.get(out.get(cid), -1):
            out[cid] = level
    return out


# §34 状态机。PUBLIC_NOTICE 按年代/职位可跳过，其余不可。
STATES = ["VACANCY", "MOTION", "CANDIDATE_POOL", "RECOMMENDATION",
          "INSPECTION", "DELIBERATION", "DECISION", "PUBLIC_NOTICE",
          "APPOINTMENT", "ACTIVE"]
SKIPPABLE = {"PUBLIC_NOTICE"}


def pdef_level_below(order):
    return next((k for k, v in LEVEL_ORDER.items() if v == order - 1), None)


class ProcedureError(Exception):
    """制度不允许的操作。不是程序 bug，是玩家/引擎越权。"""


def _iso(d):
    return d.isoformat() if isinstance(d, date) else d


def _age(birth, on):
    birth = date.fromisoformat(birth)
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


def log_event(con, on, event_type, data, actors=None, visibility="PUBLIC"):
    """§65 重要变化一律落 world_event，时间线/审计/存档修复都靠它。"""
    con.execute(
        "INSERT INTO world_event(date,event_type,actors,data,visibility) VALUES(?,?,?,?,?)",
        (_iso(on), event_type, json.dumps(actors or [], ensure_ascii=False),
         json.dumps(data, ensure_ascii=False), visibility))


def authority_for(con, slot_id):
    """§21 任免引擎第一步：这个岗位归谁管。查不到就不许动。

    管理权限沿机构层级上溯：县财政局自己不登记权限，局里的干部是县管，
    由县委管理。所以要顺着 parent_id 往上找第一个登记了权限的机构，
    并优先匹配职位本身要求的层级（县管职位找县管权限，市管职位找市管权限）。
    """
    row = con.execute("""
        WITH RECURSIVE chain(org_id, depth) AS (
            SELECT organization_id, 0 FROM position_slot WHERE id = :slot
            UNION ALL
            SELECT o.parent_id, chain.depth + 1 FROM organization o
            JOIN chain ON o.id = chain.org_id WHERE o.parent_id IS NOT NULL
        )
        SELECT a.* FROM chain
        JOIN cadre_management_authority a ON a.organization_id = chain.org_id
        JOIN position_slot s ON s.id = :slot
        JOIN position_definition d ON d.id = s.position_definition_id
        ORDER BY (a.level = d.management_authority) DESC, chain.depth, a.id
        LIMIT 1""", {"slot": slot_id}).fetchone()
    if row is None:
        raise ProcedureError(f"岗位 {slot_id} 没有对应的干部管理权限，任免无法启动")
    return row


def con_of(ctx):
    return ctx.con


class _Ctx:
    """筛选一次候选池所需的全部事实，一次查完。"""

    def __init__(self, con, slot_id, on, rules, self_id=-1):
        self.con = con
        self.slot = con.execute("SELECT * FROM position_slot WHERE id=?",
                                (slot_id,)).fetchone()
        self.pdef = con.execute("SELECT * FROM position_definition WHERE id=?",
                                (self.slot["position_definition_id"],)).fetchone()
        self.on, self.rules = on, rules
        self.years = service_years(con, on)
        self.facts = candidate_facts(con, on)
        self.target = LEVEL_ORDER.get(self.pdef["leadership_level"])
        self.levels = current_levels(con) if self.target is not None else {}
        # 培训经历一次查完。逐人查 completed_types 是 N+1：
        # 筛一次候选池要查三百多次，四十年模拟慢一个数量级。
        self.training = {}
        for cid, ptype in con.execute(
                "SELECT character_id, program_type FROM training_enrollment "
                "WHERE completed=1 AND program_type IS NOT NULL"):
            self.training.setdefault(cid, set()).add(ptype)
        # 蓝图二十五：政治局这一层不用普通升迁算法。
        # 在任的总书记和政治局常委不进常规候选池，否则会被"提拔"到别处去。
        self.top_leaders = {r[0] for r in con.execute(
            "SELECT character_id FROM party_central_status "
            "WHERE end_date IS NULL AND status IN ('总书记','政治局常委')")}
        self.taken = {r[0] for r in con.execute(
            "SELECT selected_id FROM appointment_process "
            "WHERE closed_date IS NULL AND selected_id IS NOT NULL AND id != ?", (self_id,))}
        # 人大政协是职业后段的去处。到了那儿的人不再是别处的人选——
        # 原来引擎会把政协主席"提拔"走，于是四十年换十四任，
        # 每次还要空出五个月走程序。那不是干部流动，那是算法在搅局。
        self.terminal = set()
        if self.slot is not None and _org_type(con, self.slot["id"]) not in TERMINAL_ORGS:
            self.terminal = {r[0] for r in con.execute(
                "SELECT h.character_id FROM office_holding h "
                "JOIN position_slot s ON s.id = h.position_slot_id "
                "JOIN organization o ON o.id = s.organization_id "
                "WHERE h.end_date IS NULL AND h.primary_position=1 "
                "AND o.organization_type IN (%s)"
                % ",".join("?" * len(TERMINAL_ORGS)), tuple(TERMINAL_ORGS))}


# 人大、政协：职业后段的去处。进去了就不再往别处调。
TERMINAL_ORGS = ("PEOPLES_CONGRESS", "CPPCC")
# 到龄前不足这么久的，不再提任新职。
# 六十岁上任、半年后退休，这种任命现实中不会发生——
# 考察、公示、任职走完流程人就到点了，等于白折腾一个岗位。
MIN_REMAINING_YEARS = 1.0


def _years_to_retirement(c, ctx):
    from gongpu.rules import retirement_age
    lvl = ctx.levels.get(c["id"], ctx.target)
    try:
        age_out = retirement_age(c["gender"], lvl, ctx.on)
    except Exception:
        return None
    return age_out - _age_exact(c["birth_date"], ctx.on)


def _age_exact(birth, on):
    b = date.fromisoformat(str(birth))
    return (on - b).days / 365.2425


def _org_admin_level(con, slot_id):
    r = con.execute(
        "SELECT o.admin_level FROM position_slot s "
        "JOIN organization o ON o.id = s.organization_id WHERE s.id=?",
        (slot_id,)).fetchone()
    return r[0] if r else None


def _org_type(con, slot_id):
    r = con.execute(
        "SELECT o.organization_type FROM position_slot s "
        "JOIN organization o ON o.id = s.organization_id WHERE s.id=?",
        (slot_id,)).fetchone()
    return r[0] if r else None


def check_conditions(c, ctx):
    """逐条列出这个人对这个岗位的任职条件，满足与否。

    引擎筛人和界面展示"我还差什么"用的是同一份代码。
    分开写迟早会不一致，界面就会骗人。
    返回 [{条件, 要求, 现状, ok}]，全部 ok 才进候选池。
    """
    pdef, on = ctx.pdef, ctx.on
    out = []
    add = lambda k, need, have, ok: out.append(
        {"条件": k, "要求": need, "现状": have, "ok": bool(ok)})

    if pdef["party_requirement"]:
        st = {"MEMBER": "中共党员", "PROBATIONARY": "预备党员"}.get(c["party_status"], "群众")
        add("政治面貌", "中共党员", st, c["party_status"] == "MEMBER")
    add("纪律情况", "无在查问题", "正常" if c["discipline_status"] == "CLEAR" else "在查",
        c["discipline_status"] == "CLEAR")

    a = _age(c["birth_date"], on)
    lo, hi = pdef["min_age"], pdef["max_age"]
    add("年龄", f"{lo}—{hi}岁", f"{a}岁",
        (lo is None or a >= lo) and (hi is None or a <= hi))

    y = ctx.years.get(c["id"], 0)
    add("任职资历", f"满{pdef['min_years_experience']}年", f"{y}年",
        y >= pdef["min_years_experience"])

    if c["id"] == ctx.slot["holder_id"]:
        add("现任", "非本岗位在任者", "已在此岗", False)

    if c["id"] in ctx.taken:
        add("在办流程", "未被其他任用流程定为人选", "已被其他岗位提名", False)

    if c["id"] in ctx.top_leaders:
        add("党内身份", "非在任总书记或政治局常委", "这一层不走常规任用", False)

    if c["id"] in getattr(ctx, "terminal", ()):
        add("现职去向", "非人大政协现职", "已到人大政协，不再另行安排", False)

    # 到龄前不足一年的不再提任。这条对玩家同样成立——
    # 界面里它会明明白白地写出来，不是暗地里把人筛掉。
    left = _years_to_retirement(c, ctx)
    if left is not None:
        add("距退休", "满%.0f年" % MIN_REMAINING_YEARS,
            "还有%.1f年" % left, left >= MIN_REMAINING_YEARS)

    if c["is_player"]:
        # 玩家的常规晋升只能是直升（本单位上一级）或跨部门调动（设计决定）。
        # 跨单位的提拔要走组织调训那条通道。
        from gongpu import career
        okm, why = career.is_valid_move(ctx.con, c["id"], ctx.slot["id"])
        add("变动方式", "直升或跨部门调动", why if not okm else why, okm)

    if ctx.target is None:
        return out

    cur = LEVEL_ORDER.get(ctx.levels.get(c["id"], "科员"), 1)
    cur_name = ctx.levels.get(c["id"], "科员")
    tgt_name = pdef["leadership_level"]
    add("职务层次", f"现任{pdef_level_below(ctx.target)}或{tgt_name}", f"现任{cur_name}",
        cur <= ctx.target <= cur + 1)

    f = ctx.facts.get(c["id"])
    if f is None:
        add("任职履历", "有在任职务", "无", False)
        return out

    need_tenure = MIN_TENURE_UP_LEVEL if ctx.target > cur else MIN_TENURE_YEARS
    add("现职任职年限", f"满{need_tenure:.0f}年", f"{f['tenure']:.1f}年",
        f["tenure"] >= need_tenure)

    # §38 培训经历是任职资格的一项，和年龄、资历并列。
    # 这让党校"有用"，但完全不是加速器——只让你够格，不让你更快。
    if ctx.target > cur:
        from gongpu import training
        need = training.requirement_for(tgt_name)
        if need:
            have = ctx.training.get(c["id"], set())
            add("培训经历", "／".join(need) + "结业",
                "／".join(sorted(have)) or "无", bool(have & set(need)))

    if ctx.target >= CHUJI > cur:          # 提任县处级领导职务
        add("工龄", f"满{MIN_SERVICE_FOR_CHUJI:.0f}年", f"{f['total']:.1f}年",
            f["total"] >= MIN_SERVICE_FOR_CHUJI)
        add("基层工作经历", f"满{MIN_GRASSROOTS_FOR_CHUJI:.0f}年", f"{f['grassroots']:.1f}年",
            f["grassroots"] >= MIN_GRASSROOTS_FOR_CHUJI)
        if ctx.rules.era_id >= "2014":
            below = pdef_level_below(ctx.target)
            n = len(f["levels"].get(below, ()))
            add(f"{below}职位经历", "两个以上职位", f"{n}个", n >= 2)
    return out


def eligible_candidates(con, slot_id, on, rules, self_id=-1):
    """§35 候选池不是全局排行榜：先按制度硬条件过滤，再谈匹配。

    这里只做硬性门槛。通过 != 会被提名。
    """
    ctx = _Ctx(con, slot_id, on, rules, self_id)
    out = []
    # 排除在外省任职的人。他们存在，但不会来竞争这边的岗位——
    # 每次开缺都全扫两千多人，四十年模拟要多花一分钟。
    # 注意用 NOT EXISTS：眼下没有职务的人（待分配、刚毕业）仍然是候选人。
    #
    # 但中央的岗位例外：部长本来就多是从各省省委书记、省长里出的，
    # 把外省的人排除掉，一百多个正部级位子就没人可选了。
    # 任用范围的大小，本来就是这个岗位由谁管的直接结果。
    nationwide = _org_admin_level(con, ctx.slot["id"]) == "CENTRAL"
    sql = "SELECT c.* FROM character c WHERE c.alive=1 AND c.retired=0 "
    if not nationwide:
        sql += ("AND NOT EXISTS (SELECT 1 FROM office_holding h "
                "  JOIN position_slot ps ON ps.id = h.position_slot_id "
                "  JOIN organization o ON o.id = ps.organization_id "
                "  WHERE h.character_id = c.id AND h.end_date IS NULL "
                "    AND o.simulated = 0) ")
    for c in con.execute(sql + "ORDER BY c.id"):
        if all(x["ok"] for x in check_conditions(c, ctx)):
            cur = LEVEL_ORDER.get(ctx.levels.get(c["id"], "科员"), 1)
            out.append((cur if ctx.target is not None else None, c))

    # 空缺优先从下一级提拔，同级平调只在没有可提拔人选时才用。
    # 反过来做会让同一批正科级干部被不停平调，谁也攒不够"现职三年"，
    # 于是副处级岗位永远没人够格——世界会从上往下空掉。
    if ctx.target is not None:
        promotable = [c for lvl, c in out if lvl == ctx.target - 1]
        if promotable:
            return promotable
    return [c for _, c in out]


def explain_for(con, slot_id, character_id, on, rules):
    """某人对某岗位的条件明细。界面用它画晋升路线。"""
    ctx = _Ctx(con, slot_id, on, rules)
    c = con.execute("SELECT * FROM character WHERE id=?", (character_id,)).fetchone()
    return check_conditions(c, ctx) if c else []


def service_years(con, on):
    """全员任职年限，一次查出来。

    资历按履历真实累计（§20），不是一个 experience 数字；但必须一次聚合——
    逐人查履历会让候选池筛选变成 N+1，§71 的一万名干部根本跑不动。
    """
    iso = on.isoformat()
    out = {r[0]: max(int(r[1] // 365), 0) for r in con.execute(
        "SELECT character_id, "
        "SUM(julianday(MIN(COALESCE(end_date,:on),:on)) - julianday(start_date)) "
        "FROM office_holding WHERE start_date <= :on GROUP BY character_id",
        {"on": iso}) if r[1] is not None}
    return out


class AppointmentProcess:
    def __init__(self, con, slot_id, opened, rng, rules):
        """§19 开流程的前提是真实空缺，不是候选人有多优秀。"""
        slot = con.execute("SELECT * FROM position_slot WHERE id=?", (slot_id,)).fetchone()
        if slot is None:
            raise ProcedureError(f"岗位 {slot_id} 不存在")
        if slot["status"] != "VACANT" or slot["holder_id"] is not None:
            raise ProcedureError(
                f"岗位 {slot_id} 现任 {slot['holder_id']} 在职，不存在空缺，不能启动任用")
        authority_for(con, slot_id)
        self.con, self.slot_id, self.rng, self.rules = con, slot_id, rng, rules
        self.state = "VACANCY"
        self.selected_id = None
        self.pool = []
        cur = con.execute(
            "INSERT INTO appointment_process(position_slot_id,state,opened_date) VALUES(?,?,?)",
            (slot_id, self.state, _iso(opened)))
        self.id = cur.lastrowid

    @classmethod
    def load(cls, con, process_id, rng, rules, on):
        """从库里恢复流程。跨月推进时状态活在数据库，不在内存。"""
        row = con.execute("SELECT * FROM appointment_process WHERE id=?",
                          (process_id,)).fetchone()
        if row is None:
            raise ProcedureError(f"任免流程 {process_id} 不存在")
        if row["state"] not in STATES:
            # SUSPENDED 之类的终止状态不在状态机序列里
            raise ProcedureError(f"任免流程 {process_id} 已中止")
        if row["closed_date"]:
            raise ProcedureError(f"任免流程 {process_id} 已结束")
        self = cls.__new__(cls)
        self.con, self.rng, self.rules = con, rng, rules
        self.id, self.slot_id = row["id"], row["position_slot_id"]
        self.state, self.selected_id = row["state"], row["selected_id"]
        # 只有下一步真要从池子里选人时才重算。重算本身不便宜，
        # 而且这期间有人退休、有人被查，池子本来就该以选人那一刻为准。
        # 已经走到 ACTIVE 的流程没有下一步，取下标会越界
        i = STATES.index(self.state)
        nxt = STATES[i + 1] if i + 1 < len(STATES) else None
        self.pool = (eligible_candidates(con, self.slot_id, on, rules, self.id)
                     if nxt == "DECISION" else [])
        return self

    def advance(self, on, skip=False):
        """只走一步。任何"跳到最后"的尝试都在这里撞墙（§69 关系好不能绕过程序）。"""
        i = STATES.index(self.state)
        if self.state == "ACTIVE":
            raise ProcedureError("流程已结束")
        nxt = STATES[i + 1]
        if skip:
            if nxt not in SKIPPABLE:
                raise ProcedureError(f"{nxt} 不可跳过")
            nxt = STATES[i + 2]
        if nxt == "CANDIDATE_POOL":
            self.pool = eligible_candidates(self.con, self.slot_id, on, self.rules, self.id)
            if not self.pool:
                raise ProcedureError("无人满足任职资格，流程无法进入正式环节")
            # §13 随机判定可解释：开发者模式可查，玩家看不到数字
            log_event(self.con, on, "appointment_candidate_selection",
                      {"process": self.id, "rng_stream": "career",
                       "candidate_count": len(self.pool),
                       "candidates": [c["id"] for c in self.pool]},
                      visibility="DEV")
        if nxt == "DECISION":
            # 走到这一步时池子可能已经空了：这期间有人退休、有人被查、
            # 有人被别的流程定走。这是"无合适人选"，不是程序错误。
            if not self.pool:
                raise ProcedureError("候选人均已不符合条件，本次任用中止")
            # 资格通过的人里随机取一个：满足资格 != 一定晋升（§69）
            self.selected_id = self.rng["career"].choice(self.pool)["id"]
        self.state = nxt
        self.con.execute("UPDATE appointment_process SET state=?,selected_id=? WHERE id=?",
                         (self.state, self.selected_id, self.id))
        return self.state

    def commit(self, on, title):
        """§64 任免必须事务化：旧任职结束 / 岗位变空 / 新任职建立 / 履历 / 事件，要么全成要么全不成。"""
        if self.state != "APPOINTMENT":
            raise ProcedureError(f"当前状态 {self.state}，未走到任命环节，不能落实任职")
        con = self.con
        # 从讨论决定到正式任命隔着好几个月，这期间人选可能退休、去世、被查。
        # 不复核就会出现"元旦退的休，月底上的任"。筛选时合格不等于现在还合格。
        c = con.execute("SELECT alive, retired, discipline_status FROM character WHERE id=?",
                        (self.selected_id,)).fetchone()
        if c is None or not c["alive"] or c["retired"] or c["discipline_status"] != "CLEAR":
            raise ProcedureError("拟任人选情况发生变化，本次任用中止")
        # 把现任再任命一次是没有意义的，只会在履历上留下一条零天的任职
        held = con.execute("SELECT holder_id FROM position_slot WHERE id=?",
                           (self.slot_id,)).fetchone()
        if held and held["holder_id"] == self.selected_id:
            raise ProcedureError("拟任人选已在此岗位，无需再次任用")
        with con:                                   # BEGIN ... COMMIT / ROLLBACK
            slot = con.execute("SELECT * FROM position_slot WHERE id=?",
                               (self.slot_id,)).fetchone()
            if slot["holder_id"] is not None:
                con.execute("UPDATE office_holding SET end_date=?,exit_reason=? "
                            "WHERE position_slot_id=? AND end_date IS NULL",
                            (_iso(on), "APPOINTMENT_REPLACED", self.slot_id))
            # 升迁的人要离开原岗位，原岗位随之出现空缺（§19 连锁空缺的来源）。
            # 不这样做，一个人会同时占着两个坑，"在岗人数"超过在职干部数。
            freed = [r[0] for r in con.execute(
                "SELECT position_slot_id FROM office_holding WHERE character_id=? "
                "AND end_date IS NULL AND primary_position=1", (self.selected_id,))]
            if freed:
                con.execute("UPDATE office_holding SET end_date=?,exit_reason='PROMOTED' "
                            "WHERE character_id=? AND end_date IS NULL AND primary_position=1",
                            (_iso(on), self.selected_id))
                con.executemany("UPDATE position_slot SET status='VACANT',holder_id=NULL "
                                "WHERE id=?", [(f,) for f in freed])
            con.execute("UPDATE position_slot SET status='OCCUPIED',holder_id=? WHERE id=?",
                        (self.selected_id, self.slot_id))
            # §26 历史称谓不能被覆盖：存任职当时的职务名
            con.execute("INSERT INTO office_holding"
                        "(character_id,position_slot_id,start_date,title_at_time) VALUES(?,?,?,?)",
                        (self.selected_id, self.slot_id, _iso(on), title))
            con.execute("UPDATE appointment_process SET state='ACTIVE',closed_date=? WHERE id=?",
                        (_iso(on), self.id))
            log_event(con, on, "appointment",
                      {"slot": self.slot_id, "title": title, "freed": freed},
                      actors=[self.selected_id])
        self.state = "ACTIVE"
        return self.selected_id
