"""公文（V3 §21）。

机关里大部分"办事"其实是在办文。一件事走到哪一步，看的是文走到哪一步：

    起草 → 核稿 → 会签 → 修改 → 签发 → 编号 → 印发 → 签收 → 归档

每一步都有人经手，而且对得上权限：科员起草，处长核稿，
局长签发——这不是流程图，这是 DOCUMENT_DRAFT / DOCUMENT_REVIEW /
DOCUMENT_SIGN / DOCUMENT_ISSUE 四项权限在库里的样子。

文种带年代。1986 年的《国家行政机关公文处理暂行办法》和后来的几次
修订，文种不完全一样——"命令""指令"这类，县一级本来也用不上。
"""
from datetime import date, timedelta

from gongpu import caps
from gongpu.appointment import log_event

# 公文流转的环节。每一步要什么权限、大致占几天。
STEPS = [
    ("DRAFT", "起草", "DOCUMENT_DRAFT", 3),
    ("REVIEW", "核稿", "DOCUMENT_REVIEW", 2),
    ("COUNTERSIGN", "会签", "DOCUMENT_REVIEW", 4),
    ("REVISION", "修改", "DOCUMENT_DRAFT", 2),
    ("SIGN", "签发", "DOCUMENT_SIGN", 2),
    ("NUMBERING", "编号", "DOCUMENT_ISSUE", 1),
    ("ISSUED", "印发", "DOCUMENT_ISSUE", 1),
    ("RECEIVED", "签收", None, 2),
    ("ARCHIVED", "归档", None, 5),
]
STEP_CN = {k: cn for k, cn, _, _ in STEPS}
STEP_ORDER = [k for k, _, _, _ in STEPS]

# 文种。上行文、下行文、平行文各有各的用法，用错了本身就是个问题。
#
#   请示  向上级请求指示或批准，一文一事，不能同时抄送下级
#   报告  向上级汇报，不得夹带请示事项
#   批复  答复下级请示
#   通知  下行，布置工作
#   通报  下行，表彰批评或情况通报
#   意见  对重要问题提出见解和办理办法
#   函    平行机关之间商洽、询问、答复
#   纪要  记载会议主要情况和议定事项
DOC_TYPES = {
    "请示": {"direction": "上行", "from": "1949-10-01"},
    "报告": {"direction": "上行", "from": "1949-10-01"},
    "批复": {"direction": "下行", "from": "1949-10-01"},
    "通知": {"direction": "下行", "from": "1949-10-01"},
    "通报": {"direction": "下行", "from": "1949-10-01"},
    "决定": {"direction": "下行", "from": "1949-10-01"},
    "函":   {"direction": "平行", "from": "1949-10-01"},
    "会议纪要": {"direction": "内部", "from": "1949-10-01"},
    # 1993 年《国家行政机关公文处理办法》把"意见"列为正式文种
    "意见": {"direction": "通用", "from": "1993-11-21"},
}

# 事项类型决定该发什么文。这不是随机配的：
# 上级交办的督办事项，办完了要报告；向上请求批准，用请示。
TYPE_TO_DOC = {
    "SUPERVISION": "报告", "INSTRUCTION": "报告", "REPORT": "报告",
    "DOCUMENT": "请示", "PROJECT": "请示", "BUDGET": "请示",
    "COORDINATION": "函", "PETITION": "报告", "POLICY": "意见",
    "PLANNING": "报告", "INSPECTION": "报告", "AUDIT": "报告",
    "PERSONNEL": "请示", "DISCIPLINE": "报告", "EMERGENCY": "报告",
    "INVESTIGATION": "报告", "ROUTINE": "通知", "PUBLIC_OPINION": "报告",
}


def types_on(on):
    """这一年有哪些文种可用。"""
    return [k for k, v in DOC_TYPES.items()
            if date.fromisoformat(v["from"]) <= on]


def doc_for(matter_type, on):
    kind = TYPE_TO_DOC.get(matter_type or "", "报告")
    return kind if kind in types_on(on) else "报告"


def _org_short(con, org_id):
    r = con.execute("SELECT COALESCE(short_name,name) FROM organization WHERE id=?",
                    (org_id,)).fetchone()
    return r[0] if r else "有关单位"


def _number(con, org_id, on):
    """文号：平政发〔1987〕12号。

    序号按机关和年份走，不是全局流水——两个单位各发各的文。
    """
    # 序号按**编号的那一年**走，不是起草那一年。
    # 一份 1986 年起草、1990 年才编号的文，编的是 1990 年的号。
    n = con.execute(
        "SELECT count(*) FROM document WHERE issuer_org_id=? "
        "AND doc_number LIKE ?", (org_id, "%%〔%d〕%%" % on.year)).fetchone()[0]
    short = _org_short(con, org_id)
    tag = "".join(c for c in short if c not in "中共人民政府委员会县市省局部办公厅室")[:2]
    return "%s发〔%d〕%d号" % (tag or "政", on.year, n + 1)


def create_for(con, matter_id, on, rng=None):
    """一件事要办文了，起一份稿。

    起草人就是承办这件事的人——公文不是凭空出现的，
    它是某个具体的人在某一天坐下来写的。
    """
    m = con.execute("SELECT * FROM work_item WHERE id=?", (matter_id,)).fetchone()
    if m is None:
        return None
    if con.execute("SELECT 1 FROM document WHERE matter_id=?", (matter_id,)).fetchone():
        return None
    kind = doc_for(m["matter_type"], on)
    secrecy = ("机密" if (m["secrecy"] or 0) >= 80 else
               "秘密" if (m["secrecy"] or 0) >= 60 else
               "内部" if (m["secrecy"] or 0) >= 30 else None)
    title = "关于%s的%s" % (m["subject"], kind)
    did = con.execute(
        "INSERT INTO document(matter_id,doc_type,title,drafter_org_id,drafter_id,"
        "status,secrecy_level,created_date) VALUES(?,?,?,?,?,'DRAFT',?,?)",
        (matter_id, kind, title, m["organization_id"], m["assignee_id"],
         secrecy, on.isoformat())).lastrowid
    _step(con, did, "DRAFT", m["assignee_id"], m["organization_id"], on, "拟稿")
    return did


def _step(con, did, step, cid, org_id, on, note=None):
    con.execute(
        "INSERT INTO document_step(document_id,step,character_id,organization_id,"
        "date,note) VALUES(?,?,?,?,?,?)", (did, step, cid, org_id, on.isoformat(), note))


def _who_can(con, org_id, cap, exclude=(), prefer_low=False):
    """本单位里有这项权限的人。

    核稿找得到副职，签发找得到正职——这不是按级别猜的，
    是查 position_capability 查出来的。

    prefer_low：核稿、会签这类要从下往上找。一个人自己核稿自己签发，
    那这道程序就是空的。
    """
    order = ("DESC" if prefer_low else "ASC")
    rows = con.execute(
        "SELECT h.character_id AS cid, h.acting_head AS acting FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE h.end_date IS NULL AND s.organization_id=? "
        "ORDER BY COALESCE(s.leadership_order, 99) %s, h.id" % order,
        (org_id,)).fetchall()
    for r in rows:
        if r["cid"] in exclude:
            continue
        # 权限限定在本单位：兼着别处正职的人，不能凭那个身份签这里的文。
        if caps.has(con, r["cid"], cap, org_id=org_id):
            return r["cid"]
    # 正职空缺的时候，主持工作的副职代行——这正是"主持工作"的意思。
    if cap in ("DOCUMENT_SIGN", "DOCUMENT_ISSUE"):
        for r in rows:
            if r["acting"] and r["cid"] not in exclude:
                return r["cid"]
    return None


def advance(con, did, on, rng=None):
    """把公文往下推一步。推不动就停在那儿——没人有权签发，文就发不出去。"""
    d = con.execute("SELECT * FROM document WHERE id=?", (did,)).fetchone()
    if d is None or d["status"] == "ARCHIVED":
        return None
    i = STEP_ORDER.index(d["status"])
    nxt = STEP_ORDER[i + 1]
    spec = next(x for x in STEPS if x[0] == nxt)
    cap = spec[2]
    if cap is None:
        who = d["drafter_id"]
    elif nxt in ("REVIEW", "COUNTERSIGN", "REVISION"):
        # 核稿、会签从下往上找。两条硬规矩：
        # 不能是起草人自己（自己写自己核，这道程序就是空的），
        # 也不能是将来要签发这份文的人（核稿在签发之下，不是同一道）。
        signers = _signers(con, d["drafter_org_id"])
        who = _who_can(con, d["drafter_org_id"], cap,
                       exclude=set(signers) | {d["drafter_id"]}, prefer_low=True)
        if who is None:
            # 小单位没有第三个人。那就没有独立的核稿环节——
            # 编一个出来是假的，直接走到签发。
            return _skip_to(con, did, d, on, "SIGN")
    elif nxt == "SIGN":
        who = _who_can(con, d["drafter_org_id"], cap)
    else:
        who = _who_can(con, d["drafter_org_id"], cap)
    if cap and who is None:
        return None                   # 本单位没人有这项权限，文卡在这里
    note = None
    if nxt == "NUMBERING":
        num = _number(con, d["drafter_org_id"], on)
        con.execute("UPDATE document SET doc_number=?,issuer_org_id=? WHERE id=?",
                    (num, d["drafter_org_id"], did))
        note = num
    if nxt == "SIGN":
        con.execute("UPDATE document SET signer_id=? WHERE id=?", (who, did))
    if nxt == "ISSUED":
        con.execute("UPDATE document SET issued_date=? WHERE id=?", (on.isoformat(), did))
    con.execute("UPDATE document SET status=? WHERE id=?", (nxt, did))
    _step(con, did, nxt, who, d["drafter_org_id"], on, note)
    return nxt


def _signers(con, org_id):
    """本单位有签发权的人。核稿的人不能在这里面。"""
    return [r["cid"] for r in con.execute(
        "SELECT h.character_id AS cid FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE h.end_date IS NULL AND s.organization_id=?", (org_id,)).fetchall()
        if caps.has(con, r["cid"], "DOCUMENT_SIGN", org_id=org_id)]


def _skip_to(con, did, d, on, target):
    """跳过走不通的环节。

    小单位凑不出独立的核稿人，那就没有核稿这一道——
    在流转记录里如实写"未设独立核稿环节"，而不是随便找个人填上。
    """
    i = STEP_ORDER.index(d["status"])
    j = STEP_ORDER.index(target)
    for k in STEP_ORDER[i + 1:j]:
        _step(con, did, k, None, d["drafter_org_id"], on, "本单位未设独立环节")
    con.execute("UPDATE document SET status=? WHERE id=?", (STEP_ORDER[j - 1], did))
    return advance(con, did, on)


def run(con, on, rng):
    """每天推一推在办的公文。

    公文不会自己走完：每一步要有对得上权限的人，
    而且一步一步都有日期。一份请示从拟稿到归档，二十来天。
    """
    moved = 0
    for d in con.execute(
            "SELECT d.id, d.status, max(s.date) AS last FROM document d "
            "JOIN document_step s ON s.document_id = d.id "
            "WHERE d.status != 'ARCHIVED' GROUP BY d.id").fetchall():
        spec = next(x for x in STEPS if x[0] == d["status"])
        if (on - date.fromisoformat(d["last"])).days < spec[3]:
            continue
        if advance(con, d["id"], on, rng):
            moved += 1
    return moved


def for_matter(con, matter_id):
    d = con.execute("SELECT * FROM document WHERE matter_id=?", (matter_id,)).fetchone()
    return dict(d) if d else None


def trail(con, did):
    """这份文走过的每一步。谁核的稿、谁签的发，都在这儿。"""
    return [dict(r) for r in con.execute(
        "SELECT s.step, s.date, s.note, c.name, "
        " COALESCE(o.short_name,o.name) AS org "
        "FROM document_step s LEFT JOIN character c ON c.id = s.character_id "
        "LEFT JOIN organization o ON o.id = s.organization_id "
        "WHERE s.document_id=? ORDER BY s.id", (did,))]


def listing(con, cid, limit=40):
    """和这个人有关的公文：他起草的、他核的、他签发的。"""
    return [dict(r) for r in con.execute(
        "SELECT DISTINCT d.id, d.doc_type, d.title, d.status, d.doc_number, "
        " d.secrecy_level, d.created_date, d.issued_date "
        "FROM document d JOIN document_step s ON s.document_id = d.id "
        "WHERE s.character_id=? ORDER BY d.id DESC LIMIT ?", (cid, limit))]
