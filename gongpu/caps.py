"""权限（Capability）。

界面上哪些页签显示、哪些按钮能按，不按行政级别硬开关：

    if rank >= 厅级:  开放会议        ← 错的

一个正处级的县委书记进常委会，一个正处级的省厅处长不进。
能不能进常委会，唯一的依据是他是不是常委——不是他几级，
也不是他管的单位有多重要。

页签显示的判据是三条的并集（V3 §6.1）：

    visible = 有相关实体 or 有权限 or 有会务任务

所以一个办公厅的科员看得见会议页（他要做会务），
一个民政局的科员看不见（那些会跟他没关系）。
"""
import yaml

from gongpu import paths

_CFG = {}

SCOPE_ORDER = {"OWN": 0, "UNIT": 1, "SUBORDINATE": 2, "JURISDICTION": 3}


def cfg():
    if not _CFG:
        _CFG.update(yaml.safe_load(
            (paths.DATA / "capabilities.yaml").read_text(encoding="utf-8")))
    return _CFG


def install(con):
    """把权限表装进库。每个岗位定义按职务名匹配规则。"""
    c = cfg()
    for code, desc in c["capabilities"].items():
        con.execute("INSERT OR IGNORE INTO capability(code,description) VALUES(?,?)",
                    (code, desc))
    con.execute("DELETE FROM position_capability")
    by_post = c.get("by_post", {})
    for pd in con.execute("SELECT id, name FROM position_definition").fetchall():
        for rule in by_post.get(pd["name"], []):
            con.execute(
                "INSERT INTO position_capability(position_definition_id,"
                "capability_code,scope) VALUES(?,?,?)",
                (pd["id"], rule["cap"], rule.get("scope", "UNIT")))
    for arch, rules in c.get("by_archetype", {}).items():
        for rule in rules:
            con.execute(
                "INSERT INTO position_capability(archetype,capability_code,scope) "
                "VALUES(?,?,?)", (arch, rule["cap"], rule.get("scope", "UNIT")))
    return con.execute("SELECT count(*) FROM position_capability").fetchone()[0]


def of(con, cid):
    """这个人现在有哪些权限。返回 {权限: 最大范围}。

    兼任也算：一个人兼着县委常委和组织部长，两边的权限都有。
    """
    out = {}
    rows = con.execute(
        "SELECT pc.capability_code AS cap, pc.scope AS scope "
        "FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN organization o ON o.id = s.organization_id "
        "JOIN position_capability pc "
        "  ON pc.position_definition_id = s.position_definition_id "
        "  OR pc.archetype = o.archetype "
        "WHERE h.character_id=? AND h.end_date IS NULL", (cid,)).fetchall()
    for r in rows:
        cur = out.get(r["cap"])
        if cur is None or SCOPE_ORDER.get(r["scope"], 0) > SCOPE_ORDER.get(cur, 0):
            out[r["cap"]] = r["scope"]
    return out


def has(con, cid, code, scope=None):
    """有没有这项权限。scope 给了就还要够得着那么大范围。"""
    got = of(con, cid).get(code)
    if got is None:
        return False
    if scope is None:
        return True
    return SCOPE_ORDER.get(got, 0) >= SCOPE_ORDER.get(scope, 0)


def describe(con, cid):
    """给界面看的：你这个岗位能做什么，一条条写出来。

    §57：权限不够时不弹"动作不可用"，要把制度上的理由讲给玩家。
    这里做的是正面那一半——先让他知道自己有什么。
    """
    desc = cfg()["capabilities"]
    mine = of(con, cid)
    scope_cn = {"OWN": "限本人", "UNIT": "本单位", "SUBORDINATE": "下属单位",
                "JURISDICTION": "本辖区"}
    return [{"code": k, "说明": desc.get(k, k), "范围": scope_cn.get(v, v)}
            for k, v in sorted(mine.items())]


# 页签显示：有相关实体、有权限、或者有会务任务，三条满足一条就显示。
TAB_CAPS = {
    "项目": ("PROJECT_VIEW", "PROJECT_PARTICIPATE", "PROJECT_MANAGE",
             "PROJECT_PROPOSE", "PROJECT_COORDINATE", "PROJECT_SUPERVISE"),
    "会议": ("MEETING_VIEW", "MEETING_ATTEND", "MEETING_DELIBERATE",
             "MEETING_CHAIR", "MEETING_REPORT", "MEETING_PREPARE"),
    "人事": ("PERSONNEL_VIEW", "PERSONNEL_PROPOSE", "PERSONNEL_INSPECT",
             "PERSONNEL_DELIBERATE"),
}


def tab_visible(con, cid, tab, has_entity=False):
    """页签显不显示。

    OWN 范围的权限不单独点亮页签——"我能参与分给我的项目"这句话，
    在手上没有项目的时候不构成显示项目页的理由。
    P0 无关人员就是看不见，不因为级别高就自动能看所有项目。
    """
    if has_entity:
        return True
    mine = of(con, cid)
    return any(mine.get(c) not in (None, "OWN") for c in TAB_CAPS.get(tab, ()))
