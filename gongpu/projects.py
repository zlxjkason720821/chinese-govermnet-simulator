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
# 政府投资项目的环节链。
#
# 原来只有八步，"论证—报批—施工—验收"，看着像个流程图。
# 真实的政府投资项目要走二十几道：储备、动议、前期调研、项目建议、
# 可研、资金测算、征求部门意见、专业论证、程序审查、集体决策、立项、
# 用地规划环评、预算落实、招标、开工、建设、阶段检查、竣工、验收、
# 审计决算、投入使用、后评价。
#
# 环节多不是为了折腾玩家，是因为**每一道都是一次留痕**：
# 谁签的字、谁拍的板、哪一步被跳过了，十年后查起来一清二楚。
# 跳过的那几步，正是问题埋下去的地方。
STATES = [
    "RESERVE",          # 项目储备
    "PROPOSED",         # 提出动议
    "PRE_STUDY",        # 前期调研
    "PROPOSAL",         # 项目建议
    "FEASIBILITY",      # 可行性研究
    "COSTING",          # 资金测算
    "CONSULT",          # 征求有关部门意见
    "RESEARCH",         # 专业论证、风险评估　← 上会前停在这一步
    "REVIEW",           # 程序审查
    "APPROVAL",         # 集体决策、立项　　　← 会上通过才进得来
    "PERMITS",          # 规划、用地、环评等手续
    "FUNDED",           # 预算落实
    "TENDER",           # 采购、招标
    "START",            # 开工
    "IMPLEMENTATION",   # 建设
    "MIDCHECK",         # 阶段检查
    "COMPLETE",         # 竣工
    "INSPECTION",       # 验收
    "AUDIT",            # 审计、决算
    "OPERATING",        # 投入使用
    "POSTEVAL",         # 后评价
    "CLOSED",           # 结项
]
STATE_CN = {
    "RESERVE": "项目储备", "PROPOSED": "提出动议", "PRE_STUDY": "前期调研",
    "PROPOSAL": "项目建议", "FEASIBILITY": "可行性研究", "COSTING": "资金测算",
    "CONSULT": "征求意见", "RESEARCH": "专业论证", "REVIEW": "程序审查",
    "APPROVAL": "集体决策立项", "PERMITS": "用地规划环评", "FUNDED": "预算落实",
    "TENDER": "采购招标", "START": "开工", "IMPLEMENTATION": "建设",
    "MIDCHECK": "阶段检查", "COMPLETE": "竣工", "INSPECTION": "验收",
    "AUDIT": "审计决算", "OPERATING": "投入使用", "POSTEVAL": "后评价",
    "CLOSED": "结项",
}
# 各环节大致耗时（天）。一个中等项目从储备到结项六七年，项目本来就慢。
STATE_DAYS = {
    "RESERVE": 60, "PROPOSED": 45, "PRE_STUDY": 75, "PROPOSAL": 60,
    "FEASIBILITY": 120, "COSTING": 60, "CONSULT": 75, "RESEARCH": 90,
    "REVIEW": 45, "APPROVAL": 60, "PERMITS": 150, "FUNDED": 90,
    "TENDER": 75, "START": 30, "IMPLEMENTATION": 480, "MIDCHECK": 60,
    "COMPLETE": 45, "INSPECTION": 75, "AUDIT": 120, "OPERATING": 540,
    "POSTEVAL": 90,
}
ROLES = ("提出", "批准", "签批", "实施", "监督")

# 项目要落在对口的单位上。公安局承办供热管网、民政局盖小学，
# 决策链读起来就是假的——而决策链正是这个系统全部的价值所在。
NAMES_BY_ORG = {
    "教育局": ["青阳镇中心小学新建", "县第二中学教学楼改造", "农村学校危房改造"],
    "农业局": ["农田水利灌溉配套", "良种繁育基地建设", "农村电网配套改造"],
    "民政局": ["县社会福利院改扩建", "农村敬老院建设", "殡仪馆迁建"],
    "财政局": ["财政业务用房建设", "政府投资项目审核中心"],
    "公安局": ["县公安局业务技术用房", "看守所改扩建"],
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

# 2010 年之后，政府投资的方向变了。
#
# 前二十几年是修路、盖楼、通水通电——土地和基建撑起来的那一套。
# 之后逐渐转向网络、数据、平台、园区：同样是上项目、同样要走那二十几道
# 环节，但要办的事和要协调的部门完全不一样了。
#
# 这不是"旧的全没了"：路照样要修，只是新增的项目里多了另一类。
DIGITAL_FROM = 2010
DIGITAL_NAMES_BY_ORG = {
    "教育局": ["中小学网络教学平台", "教育城域网建设"],
    "农业局": ["农产品电商服务中心", "农业物联网示范基地"],
    "民政局": ["社会救助信息平台", "养老服务智慧化改造"],
    "财政局": ["财政预算一体化系统", "政府采购电子化平台",
               "县级财政信息化系统"],
    "公安局": ["视频监控联网平台", "警务大数据中心", "城区治安监控系统"],
    "审计局": ["审计数据分析平台"],
    "卫生局": ["区域健康信息平台", "远程诊疗系统建设"],
    "县计委": ["县域数据中心", "智慧城市运营平台", "电子商务产业园"],
    "县政府办": ["政务服务网上办事大厅", "县域数据中心",
                 "电子商务产业园", "5G 基站配套建设"],
}
DIGITAL_TOWNSHIP = ["村级电商服务站", "农村宽带入户工程", "乡镇政务自助终端"]
# 新增项目里有多大比例走数字这一路。2010 年起步，往后逐年加重。
DIGITAL_SHARE = [(2010, 0.15), (2015, 0.35), (2020, 0.5)]


def _digital_share(year):
    share = 0.0
    for y, v in DIGITAL_SHARE:
        if year >= y:
            share = v
    return share


def _names_for(org_name, org_type, year=None, w=None):
    """这个单位能上哪些项目。2010 年后多一路数字化的选择。"""
    if year and w and w.random() < _digital_share(year):
        pool = (DIGITAL_TOWNSHIP if org_type == "TOWNSHIP"
                else DIGITAL_NAMES_BY_ORG.get(org_name))
        if pool:
            return pool
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
    name = w.choice(_names_for(org["n"], org["t"], on.year, w))
    scale = 1 + int(w.random() * 3)
    # 体量越大，埋下问题的概率越高。这不是设定，是留痕越多越查得出来。
    risk = int(w.random() * 30) + scale * 8
    # 任何项目都要落到具体地方和具体领域。
    # "红山县第二人民医院迁建工程"必须知道它在哪个县、属哪个口——
    # 没有这两样，后面的责任链、部门协同、审计都无从谈起。
    region_org, region = _region_of(con, organization_id)
    domain = _domain_for(name, org["n"])
    investment = scale * (800 + int(w.random() * 2200))      # 万元
    cur = con.execute(
        "INSERT INTO project(name,organization_id,state,scale,risk,proposed_date,"
        "region_org_id,region,policy_domain,investment,"
        "coordination,fiscal_pressure,audit_risk,integrity_risk) "
        "VALUES(?,?,'PROPOSED',?,?,?,?,?,?,?,?,?,?,?)",
        (name, organization_id, scale, risk, on.isoformat(),
         region_org, region, domain, investment,
         30 + scale * 12, 25 + scale * 10, scale * 6, scale * 4))
    pid = cur.lastrowid
    record_decision(con, pid, "提出", _leader_of(con, organization_id), on, "动议")
    _log(con, on, pid, "PROJECT_PROPOSED", "investment", None, investment,
         "动议，初步投资估算")
    log_event(con, on, "project_proposed",
              {"project": pid, "name": name, "scale": scale,
               "domain": domain, "region": region})
    return pid


# 领域。项目属哪个口，决定它要过哪些部门、由谁分管、出事查谁。
DOMAIN_BY_KEYWORD = [
    ("医院 卫生 防疫 诊疗 健康 医疗", "医疗"), ("学校 小学 中学 教育 校舍 教学 学生 教师", "教育"),
    ("道路 公路 桥 客运", "交通"), ("水库 灌溉 河道 供排水 防洪 饮水 管网 水利", "水利"),
    ("电 供热 能源", "能源"), ("危房 住房 安置 棚户", "住房"),
    ("垃圾 污水 环保 生态", "生态环保"), ("园区 工业 产业", "产业园区"),
    ("农田 农业 农村 粮", "农业农村"), ("文化 旅游 体育", "文化旅游"),
    ("派出所 消防 应急 防灾", "公共安全"), ("街 城区 旧城 改造", "城市更新"),
    # 2010 年之后的那一路
    ("网络 宽带 数据中心 基站 联网 城域网", "数字基础设施"),
    ("平台 系统 电子化 信息化 自助终端 智慧", "数字基础设施"),
    ("电商 产业园 物联网", "科技创新"),
]


def _domain_for(name, org_name):
    for keys, domain in DOMAIN_BY_KEYWORD:
        if any(k in name for k in keys.split()):
            return domain
    if "民政" in org_name:
        return "社会服务"
    return "社会服务"


def _region_of(con, org_id):
    """项目落在哪个县。乡镇、工作部门都往上归到本县。

    找的是本级的政府（四套班子之一，protocol_order 不为空），
    不能靠"父机构为空"来判断——加了市级之后县委的父机构是市委。
    """
    cur = org_id
    for _ in range(5):
        r = con.execute(
            "SELECT id, COALESCE(short_name,name) AS n, admin_level AS al, "
            " protocol_order AS po, parent_id FROM organization WHERE id=?",
            (cur,)).fetchone()
        if r is None:
            return None, None
        if r["al"] == "COUNTY" and r["po"] is not None:
            return r["id"], r["n"]
        if r["parent_id"] is None:
            return r["id"], r["n"]
        cur = r["parent_id"]
    return None, None


def _log(con, on, pid, action, field, before, after, reason=None, actor=None):
    """决策留痕。重要事实不可静默覆盖：投资从八亿改到十亿再改到九点四亿，
    每一次都要留下，十年后查起来才有据可依。"""
    con.execute(
        "INSERT INTO decision_log(event_date,project_id,actor_id,action_type,"
        "field,before_value,after_value,reason) VALUES(?,?,?,?,?,?,?,?)",
        (on.isoformat(), pid, actor, action, field,
         None if before is None else str(before),
         None if after is None else str(after), reason))


def change_investment(con, pid, on, new_value, reason, actor=None):
    """调整投资。前后值都留痕，当前值和历史值同时保存。"""
    old = con.execute("SELECT investment FROM project WHERE id=?", (pid,)).fetchone()[0]
    con.execute("UPDATE project SET investment=? WHERE id=?", (new_value, pid))
    _log(con, on, pid, "INVESTMENT_CHANGED", "investment", old, new_value, reason, actor)
    return old, new_value


# 十二项结果指标。不能只有一个"政绩值"：
# 一个项目可以进度快但成本失控，可以建成了但后续运营是个包袱，
# 可以程序合规但群众不满意。这些维度互相冲突，压成一个数就全没了。
INDICATORS = ("progress", "cost_control", "quality", "safety",
              "procedure_compliance", "fiscal_pressure", "social_effect",
              "ecological_effect", "coordination", "audit_risk",
              "integrity_risk", "operation_burden")
INDICATOR_CN = {
    "progress": "进度", "cost_control": "成本控制", "quality": "质量",
    "safety": "安全", "procedure_compliance": "程序合规",
    "fiscal_pressure": "财政压力", "social_effect": "社会效果",
    "ecological_effect": "生态影响", "coordination": "协调难度",
    "audit_risk": "审计风险", "integrity_risk": "廉政风险",
    "operation_burden": "后续运营负担",
}


def indicators(con, pid):
    r = con.execute("SELECT %s FROM project WHERE id=?" % ",".join(INDICATORS),
                    (pid,)).fetchone()
    if r is None:
        return []
    return [{"指标": INDICATOR_CN[k], "值": r[k], "code": k} for k in INDICATORS]


def history(con, pid):
    """这个项目的决策链。审计、后评价、责任追溯读的就是它。"""
    return [dict(r) for r in con.execute(
        "SELECT event_date, action_type, field, before_value, after_value, reason "
        "FROM decision_log WHERE project_id=? ORDER BY id", (pid,))]


# 每一道环节由谁经手。这不是装饰：十年后审计、巡视、调查查的就是这张表——
# 哪一步谁签的字，哪一步根本没人签。
STAGE_ACTOR = {
    "PRE_STUDY":      ("实施", "own",      "组织前期调研"),
    "PROPOSAL":       ("提出", "own",      "上报项目建议"),
    "FEASIBILITY":    ("实施", "own",      "委托编制可行性研究报告"),
    "COSTING":        ("签批", "财政局",   "财政部门测算资金"),
    "CONSULT":        ("监督", "县计委",   "征求发展计划部门意见"),
    "RESEARCH":       ("监督", "own",      "专业论证与风险评估"),
    "REVIEW":         ("监督", "gov",      "政府办程序审查"),
    "APPROVAL":       ("批准", "gov",      "政府常务会议集体决策，同意立项"),
    "PERMITS":        ("签批", "县计委",   "办理规划、用地、环评手续"),
    "FUNDED":         ("签批", "财政局",   "预算落实"),
    "TENDER":         ("监督", "gov",      "组织采购招标"),
    "START":          ("实施", "own",      "开工"),
    "IMPLEMENTATION": ("实施", "own",      "组织施工"),
    "MIDCHECK":       ("监督", "gov",      "阶段检查"),
    "COMPLETE":       ("实施", "own",      "竣工"),
    "INSPECTION":     ("监督", "gov",      "组织验收"),
    "AUDIT":          ("监督", "审计局",   "审计决算"),
    "OPERATING":      ("实施", "own",      "交付使用"),
    "POSTEVAL":       ("监督", "县计委",   "项目后评价"),
}


def _actor_org(con, which, own_org):
    if which == "own":
        return own_org
    if which == "gov":
        r = con.execute(
            "SELECT id FROM organization WHERE organization_type='GOVERNMENT' "
            "AND admin_level='COUNTY' AND protocol_order IS NOT NULL "
            "ORDER BY id LIMIT 1").fetchone()
        return r[0] if r else own_org
    r = con.execute(
        "SELECT id FROM organization WHERE COALESCE(short_name,name)=? "
        "AND admin_level='COUNTY' LIMIT 1", (which,)).fetchone()
    return r[0] if r else own_org


def advance(con, project_id, on, rng, rules, via_meeting=False):
    """推一个环节。每个环节都要有人经手，都留痕。

    via_meeting=True 表示这一步是政府常务会议研究通过的，可以进集体决策。
    """
    p = con.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
    if p is None or p["state"] == "CLOSED":
        return None
    i = STATES.index(p["state"])
    nxt = STATES[i + 1]
    if nxt == "APPROVAL" and not via_meeting:
        # 立项要上政府常务会议集体决策（蓝图十七）。引擎在这里停住等会。
        return None

    spec = STAGE_ACTOR.get(nxt)
    if spec:
        role, which, note = spec
        who = _leader_of(con, _actor_org(con, which, p["organization_id"]))
        if who:
            record_decision(con, project_id, role, who, on, note)
        _log(con, on, project_id, "STAGE_" + nxt, "state",
             STATE_CN.get(p["state"]), STATE_CN[nxt], note, who)

    _move_indicators(con, p, nxt, rng)

    if nxt == "INSPECTION":
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


def _move_indicators(con, p, nxt, rng):
    """环节推进，十二项指标跟着动。

    它们互相冲突，这是重点：赶工期，质量和安全就往下走；
    压成本，后续运营负担就往上走。没有一个方向是全赢的。
    """
    w = rng["governance"]
    d = {}
    if nxt == "IMPLEMENTATION":
        d["progress"] = 25
    elif nxt in ("MIDCHECK", "COMPLETE"):
        d["progress"] = 30
    elif nxt == "OPERATING":
        d["progress"] = 100 - (p["progress"] or 0)
    elif nxt in ("START", "TENDER"):
        d["progress"] = 8
    # 体量越大，越容易在成本和协调上出问题
    heavy = p["scale"] >= 3
    if nxt == "TENDER":
        d["integrity_risk"] = 8 + (6 if heavy else 0)
        d["procedure_compliance"] = -4 if w.random() < 0.3 else 2
    if nxt == "IMPLEMENTATION":
        d["cost_control"] = -6 if heavy else -2
        d["safety"] = -5 if heavy else -2
    if nxt == "COMPLETE":
        d["quality"] = 6 if w.random() > 0.35 else -8
    if nxt == "AUDIT":
        d["audit_risk"] = 10 if (p["risk"] or 0) > 40 else -5
    if nxt == "OPERATING":
        d["operation_burden"] = 10 + p["scale"] * 6
        d["social_effect"] = 8 if w.random() > 0.3 else -4
    if not d:
        return
    sets, args = [], []
    for k, v in d.items():
        sets.append("%s = max(0, min(100, %s + ?))" % (k, k))
        args.append(v)
    con.execute("UPDATE project SET %s WHERE id=?" % ",".join(sets),
                tuple(args) + (p["id"],))


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
