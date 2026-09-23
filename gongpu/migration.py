"""制度迁移（技术文档 §25、§26）。

跨制度年份时，世界要换一套规则，但人物历史一条都不能丢。
1999 年是"副主任科员"的人，2020 年打开履历，那一段仍然写着副主任科员。
套转只是从某一天起开始用新称谓，不是把过去重写一遍。
"""
import json
from datetime import date

from gongpu.appointment import log_event

# 1993《国家公务员暂行条例》非领导职务序列，按资历套入
RANKS_1993 = ((0, "办事员"), (3, "科员"), (10, "副主任科员"), (18, "主任科员"))

# 2019 职务职级并行：非领导职务 -> 职级。这张表决定几十万人的称谓，必须显式写出来。
# 时间轴文档 §32 列出的综合管理类职级只有 12 级，最低是二级科员——
# 没有"三级科员"这一级，办事员和科员都套转为二级科员。
CONVERSION_2019 = {
    "办事员": "二级科员",
    "科员": "二级科员",
    "副主任科员": "一级科员",
    "主任科员": "四级主任科员",
    "副调研员": "四级调研员",
    "调研员": "二级调研员",
    "副巡视员": "二级巡视员",
    "巡视员": "一级巡视员",
}


def _years_worked(con, char_id, on):
    ws = con.execute("SELECT work_start_date FROM character WHERE id=?",
                     (char_id,)).fetchone()[0]
    return (on - date.fromisoformat(ws)).days // 365 if ws else 0


def _current_rank(con, char_id):
    return con.execute(
        "SELECT * FROM rank_holding WHERE character_id=? AND end_date IS NULL "
        "ORDER BY id DESC LIMIT 1", (char_id,)).fetchone()


def _set_rank(con, char_id, rank_name, system, on, source):
    """封口旧记录，另起一条。旧记录原文保留（§26）。"""
    con.execute("UPDATE rank_holding SET end_date=? WHERE character_id=? AND end_date IS NULL",
                (on.isoformat(), char_id))
    con.execute(
        "INSERT INTO rank_holding(character_id,rank_name,rank_system,start_date,source) "
        "VALUES(?,?,?,?,?)", (char_id, rank_name, system, on.isoformat(), source))


def to_1993_civil_service(con, on, rng, rules):
    """1993-10-01：干部进入国家公务员制度。按资历套入非领导职务序列。"""
    n = 0
    for c in con.execute("SELECT id FROM character WHERE alive=1 AND retired=0 ORDER BY id"):
        if _current_rank(con, c["id"]):
            continue
        y = _years_worked(con, c["id"], on)
        rank = next(name for need, name in reversed(RANKS_1993) if y >= need)
        _set_rank(con, c["id"], rank, "1993", on, "INITIAL")
        n += 1
    log_event(con, on, "institution_migration",
              {"to": "1993_CIVIL_SERVICE", "converted": n,
               "note": "干部过渡为国家公务员，原有履历不变"})
    return n


def to_2006_civil_service_law(con, on, rng, rules):
    """2006-01-01：《公务员法》施行，暂行条例体系废止。职务序列延续，制度版本换代。"""
    # fetchall 不是可有可无：_set_rank 会插入新的 end_date IS NULL 行，
    # 边迭代边插会让同一次扫描不断捞到自己刚写的行，死循环。
    rows = con.execute("SELECT * FROM rank_holding WHERE end_date IS NULL "
                       "ORDER BY id").fetchall()
    n = 0
    for r in rows:
        _set_rank(con, r["character_id"], r["rank_name"], "2006", on, "CONVERSION")
        n += 1
    log_event(con, on, "institution_migration",
              {"to": "2006_CIVIL_SERVICE_LAW", "converted": n,
               "note": "称谓不变，适用法律由暂行条例改为公务员法"})
    return n


def to_2018_supervision(con, on, rng, rules):
    """2018-03-20：设立监察委员会。这是新增机构，不是给谁加属性。"""
    xianwei = con.execute(
        "SELECT id FROM organization WHERE organization_type='PARTY' "
        "AND admin_level='COUNTY' AND protocol_order IS NOT NULL "
        "ORDER BY id LIMIT 1").fetchone()
    # 按全称和简称两头找：机构全称是"纪律检查委员会"，简称才是"纪委"。
    # 只匹配其中一种，改个机构名就会让这场改革静默地不发生。
    jiwei = con.execute(
        "SELECT * FROM organization WHERE active=1 AND admin_level='COUNTY' AND ("
        "name LIKE '%纪律检查委员会' OR short_name LIKE '%纪委') "
        "ORDER BY id LIMIT 1").fetchone()
    if jiwei is None:
        return None
    name = (jiwei["name"].replace("纪律检查委员会", "监察委员会")
            if "纪律检查委员会" in jiwei["name"]
            else jiwei["name"].replace("纪委", "监察委员会"))
    short = (jiwei["short_name"] or name).replace("纪委", "监委")
    cur = con.execute(
        "INSERT INTO organization(name,short_name,organization_type,institution_grade,"
        "parent_id,valid_from) VALUES(?,?,'SUPERVISION',?,?,?)",
        (name, short, jiwei["institution_grade"],
         xianwei["id"] if xianwei else None, on.isoformat()))
    new_org = cur.lastrowid
    # 纪委监委合署办公：岗位随之落地，不是凭空多出一个牌子。
    #
    # 这里必须显式建职务定义，不能按名字去库里捞。
    # 按 name='主任' 捞会捞到"人大常委会主任"——正处级、市管、要求十年资历，
    # 于是县监委主任八年没人够得上，岗位一直空着。
    pdef = con.execute(
        "INSERT INTO position_definition(name,is_leadership,management_authority,"
        "min_age,max_age,party_requirement,min_years_experience,leadership_level,"
        "protocol_order,valid_from) "
        "VALUES('主任',1,'MUNICIPAL',33,58,1,6,'副处级',1,?)", (on.isoformat(),))
    con.execute(
        "INSERT INTO position_slot(position_definition_id,organization_id,valid_from,"
        "status) VALUES(?,?,?,'VACANT')", (pdef.lastrowid, new_org, on.isoformat()))
    log_event(con, on, "institution_migration",
              {"to": "2018_SUPERVISION", "organization": name,
               "note": "设立监察委员会，与纪委合署办公"})
    return new_org


def to_2019_rank(con, on, rng, rules):
    """2019-06-01：职务职级并行，非领导职务套转为职级。"""
    rows = con.execute("SELECT * FROM rank_holding WHERE end_date IS NULL "
                       "ORDER BY id").fetchall()          # 同上，必须先取完
    n, unmapped = 0, []
    for r in rows:
        new = CONVERSION_2019.get(r["rank_name"])
        if new is None:
            unmapped.append(r["rank_name"])
            continue
        _set_rank(con, r["character_id"], new, "2019", on, "CONVERSION")
        n += 1
    log_event(con, on, "institution_migration",
              {"to": "2019_RANK", "converted": n, "unmapped": sorted(set(unmapped)),
               "note": "非领导职务套转为职级，历史称谓保留"})
    return n


MIGRATIONS = {
    "1993_CIVIL_SERVICE": to_1993_civil_service,
    "2006_CIVIL_SERVICE_LAW": to_2006_civil_service_law,
    "2018_SUPERVISION": to_2018_supervision,
    "2019_RANK": to_2019_rank,
}


def run(con, on, to_era, rng, rules):
    """世界跨进新制度时调用。没有登记迁移的 Era 什么都不做，这是允许的。"""
    fn = MIGRATIONS.get(to_era)
    return fn(con, on, rng, rules) if fn else None
