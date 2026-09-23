"""全国省级行政区（§72 背景人口抽象）。

全国三十四个省级行政区都真实存在，但只有玩家所在的省往下铺市县。
其余的省只实例化省级班子——这正是 §72 说的：没有直接参与玩家世界的
干部，先作为背景存在，需要的时候再实例化。

区划本身也带年代：1988 海南建省、1997 重庆直辖、1997/1999 港澳回归。
拿 2026 的区划倒推 1986，和拿 2008 的党校条例倒推 1986 是一个性质的错。
"""
from datetime import date

import yaml

from gongpu import paths

_CFG = {}

# 背景省份的班子规格。只建正副职，不铺工作部门和科员——
# 那些人不参与玩家的世界，按 §72 留在统计池里。
PROVINCE_POSTS = [
    ("书记", "正部级", 1, "PARTY"),
    ("副书记", "副部级", 2, "PARTY"),
    ("常委", "副部级", 4, "PARTY"),
    ("省长", "正部级", 1, "GOVERNMENT"),
    ("副省长", "副部级", 3, "GOVERNMENT"),
]
MUNICIPALITY_POSTS = [
    ("书记", "正部级", 1, "PARTY"),
    ("副书记", "副部级", 2, "PARTY"),
    ("常委", "副部级", 4, "PARTY"),
    ("市长", "正部级", 1, "GOVERNMENT"),
    ("副市长", "副部级", 3, "GOVERNMENT"),
]


def cfg():
    if not _CFG:
        _CFG.update(yaml.safe_load(
            (paths.DATA / "regions.yaml").read_text(encoding="utf-8")))
    return _CFG


def provinces():
    return cfg()["provinces"]


def home_province():
    return cfg().get("home_province", "河北省")


def existing_on(on):
    """某个日期上真实存在的省级行政区。"""
    return [p for p in provinces()
            if date.fromisoformat(str(p["valid_from"])) <= on]


def _posts_for(p):
    return MUNICIPALITY_POSTS if p["type"] == "直辖市" else PROVINCE_POSTS


def install(con, on, rng, skip_name=None):
    """把全国的省级班子建出来。

    skip_name 是玩家所在的省——它在场景文件里已经建得更细（有市有县），
    这里不重复建。
    """
    made = 0
    for p in existing_on(on):
        if p["name"] == skip_name:
            continue
        made += _build(con, p, on, rng)
    return made


def due_on(con, on, rng):
    """今天该设立的区划。

    1988 海南建省、1997 重庆直辖、1997 香港回归、1999 澳门回归——
    这四件事就发生在这局游戏的时间窗口里。开局时它们不存在，
    到了日子才出现，而不是一开始就把 2026 年的地图摆上去。
    """
    out = []
    for p in provinces():
        if date.fromisoformat(str(p["valid_from"])) != on:
            continue
        if p["name"] == home_province():
            continue
        if con.execute("SELECT 1 FROM organization WHERE name=? OR name=?",
                       (p["name"], "%s人民政府" % p["name"])).fetchone():
            continue
        _build(con, p, on, rng)
        out.append(p)
    return out


def _build(con, p, on, rng):
    """建一个省级行政区。"""
    from gongpu.world import _seat
    made = 0
    if p.get("cadre_system") is False:
        # 港澳台不生成党政领导班子，但区划表里有它们
        con.execute(
            "INSERT INTO organization(name,short_name,admin_level,"
            "organization_type,institution_grade,valid_from,active,simulated) "
            "VALUES(?,?,'PROVINCIAL','REGION',?,?,1,0)",
            (p["name"], p["short"], p["grade"], str(p["valid_from"])))
        return 1
    is_city = p["type"] == "直辖市"
    # 全称用行政区的全名：中国共产党内蒙古自治区委员会，
    # 不是"中共内蒙古委员会"。简称也分类型：市委、区委、省委。
    party_name = "中国共产党%s委员会" % p["name"]
    short_suffix = {"直辖市": "市委", "自治区": "区委"}.get(p["type"], "省委")
    gov_name = "%s人民政府" % p["name"]
    pid_party = con.execute(
        "INSERT INTO organization(name,short_name,admin_level,protocol_order,"
        "organization_type,system_type,institution_grade,valid_from,simulated) "
        "VALUES(?,?,'PROVINCIAL',1,'PARTY','党委',?,?,0)",
        (party_name, "中共%s%s" % (p["short"], short_suffix),
         p["grade"], str(p["valid_from"]))).lastrowid
    pid_gov = con.execute(
        "INSERT INTO organization(name,short_name,admin_level,protocol_order,"
        "organization_type,system_type,institution_grade,valid_from,simulated) "
        "VALUES(?,?,'PROVINCIAL',3,'GOVERNMENT','政务',?,?,0)",
        (gov_name, gov_name, p["grade"], str(p["valid_from"]))).lastrowid
    con.execute("INSERT INTO cadre_management_authority(level,organization_id) "
                "VALUES('CENTRAL',?)", (pid_party,))
    con.execute("INSERT INTO cadre_management_authority(level,organization_id) "
                "VALUES('CENTRAL',?)", (pid_gov,))

    for name, level, n, kind in _posts_for(p):
        org = pid_party if kind == "PARTY" else pid_gov
        pdef = con.execute(
            "SELECT id FROM position_definition WHERE name=? AND leadership_level=? "
            "AND management_authority='CENTRAL' LIMIT 1", (name, level)).fetchone()
        if pdef is None:
            pdef_id = con.execute(
                "INSERT INTO position_definition(name,is_leadership,"
                "management_authority,min_age,max_age,party_requirement,"
                "min_years_experience,leadership_level,protocol_order,valid_from) "
                "VALUES(?,1,'CENTRAL',?,?,1,?,?,?,?)",
                (name, 45 if level == "正部级" else 42,
                 65 if level == "正部级" else 63,
                 28 if level == "正部级" else 24, level,
                 1 if level == "正部级" else 3, "1949-10-01")).lastrowid
        else:
            pdef_id = pdef["id"]
        for _ in range(n):
            slot = con.execute(
                "INSERT INTO position_slot(position_definition_id,organization_id,"
                "valid_from,status) VALUES(?,?,?,'VACANT')",
                (pdef_id, org, str(p["valid_from"]))).lastrowid
            _seat(con, slot, level, on, rng["world"])
            made += 1
    return made


def listing(con):
    """区划总览，给界面用：一个行政区一行，带党政两边的在岗人数。"""
    def staffed(name_like):
        r = con.execute(
            "SELECT count(*) FROM position_slot s "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE o.name LIKE ? AND o.admin_level='PROVINCIAL' "
            "AND s.status='OCCUPIED'", (name_like,)).fetchone()
        return r[0] if r else 0

    out = []
    for p in provinces():
        row = con.execute(
            "SELECT 1 FROM organization WHERE admin_level='PROVINCIAL' "
            "AND (name = ? OR name LIKE ? OR name LIKE ?) LIMIT 1",
            (p["name"], "%s%%" % p["name"], "%%%s委员会" % p["name"])).fetchone()
        if row is None:
            continue                       # 还没到设立的日子
        if p.get("cadre_system") is False:
            out.append({"name": p["short"], "full": p["name"],
                        "valid_from": p["valid_from"], "party": None, "gov": None})
            continue
        out.append({
            "name": p["short"], "full": p["name"], "valid_from": p["valid_from"],
            "party": staffed("中共%s%%" % p["short"]),
            "gov": staffed("%s人民政府" % p["name"])})
    return out
