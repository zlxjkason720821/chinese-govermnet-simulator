"""党校与干部培训（技术文档 §38；总规划 §27-30；蓝图 八/九/十）。

党校不是设施，是履历结构里的一个节点。蓝图八说它同时影响四件事：
理论与政策能力、履历完整度、组织观察、横向干部关系。其中没有一样叫晋升率。

两条通道：

  组织调训   —— 组织决定，通知下来才去。名额是稀缺资源。
  本人申请   —— 五年一次，或晋升前夕。批不批要看条件。

结业之后必定提一级（设计决定），但**去哪个岗位由组织谈话决定**，
给一到两个选择。所以它是升迁通道，不是自选跳板。
"""
import json
from datetime import date, timedelta
from pathlib import Path

from gongpu import paths

import yaml

from gongpu.appointment import LEVEL_ORDER, _age, log_event

DATA = paths.DATA
_CFG = {}

REQUEST_COOLDOWN_YEARS = 5      # 本人申请：五年一次
# 刚报到就申请任职培训是不成立的：任职培训是为任新职而训，
# 没有这两条，玩家可以报到当天申请、两个月后结业、随即提一级。
MIN_SERVICE_YEARS = 2.0         # 工龄
MIN_TENURE_YEARS = 1.0          # 现职任职年限
FOREVER = "9999-12-31"


def cfg():
    if not _CFG:
        _CFG.update(yaml.safe_load((DATA / "training.yaml").read_text(encoding="utf-8")))
    return _CFG


def school_name(level):
    return cfg()["schools"][level]["short"]


def install(con):
    """把班次定义写进库。开局做一次。"""
    if con.execute("SELECT count(*) FROM training_program").fetchone()[0]:
        return
    for p in cfg()["programs"]:
        con.execute(
            "INSERT INTO training_program(key,name,school_level,program_type,days,"
            "targets,max_age,quota,valid_from,valid_to) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (p["key"], p["name"], p["school"], p["type"], p["days"],
             ",".join(p["targets"]), p.get("max_age"), p.get("quota", 1),
             str(p["valid_from"]), str(p.get("valid_to", FOREVER))))


def programs_for(con, cid, on):
    """这个人现在够得上哪些班次。轮训范围和年龄都是硬的。"""
    from gongpu import actions
    lvl = actions.player_level(con, cid)
    lvl_name = next((k for k, v in LEVEL_ORDER.items() if v == lvl), "科员")
    c = con.execute("SELECT birth_date FROM character WHERE id=?", (cid,)).fetchone()
    if c is None:
        return []
    age = _age(c["birth_date"], on)
    out = []
    for p in con.execute(
            "SELECT * FROM training_program WHERE valid_from <= ? AND valid_to >= ? "
            "ORDER BY id", (on.isoformat(), on.isoformat())):
        if lvl_name not in p["targets"].split(","):
            continue
        if p["max_age"] is not None and age > p["max_age"]:
            continue
        out.append(dict(p))
    return out


def last_training(con, cid):
    return con.execute(
        "SELECT * FROM training_enrollment WHERE character_id=? "
        "ORDER BY start_date DESC LIMIT 1", (cid,)).fetchone()


def current(con, cid, on):
    """在读的班。在校期间不能办本单位的事。"""
    return con.execute(
        "SELECT e.*, s.end_date AS s_end FROM training_enrollment e "
        "LEFT JOIN training_session s ON s.id = e.session_id "
        "WHERE e.character_id=? AND e.completed=0 AND e.end_date IS NULL "
        "ORDER BY e.id DESC LIMIT 1", (cid,)).fetchone()


def request_status(con, cid, on, pre_promotion=False):
    """本人能不能申请。五年一次；晋升前夕另算一次机会。"""
    if current(con, cid, on):
        return False, "你正在学习期间。"
    last = con.execute(
        "SELECT start_date FROM training_enrollment WHERE character_id=? AND requested=1 "
        "ORDER BY start_date DESC LIMIT 1", (cid,)).fetchone()
    if last:
        gap = (on - date.fromisoformat(last["start_date"])).days / 365
        if gap < REQUEST_COOLDOWN_YEARS and not pre_promotion:
            return False, "上次本人申请离现在不足五年，还差 %.1f 年。" % (
                REQUEST_COOLDOWN_YEARS - gap)
    c = con.execute("SELECT work_start_date FROM character WHERE id=?", (cid,)).fetchone()
    if c and c["work_start_date"]:
        years = (on - date.fromisoformat(c["work_start_date"])).days / 365
        if years < MIN_SERVICE_YEARS:
            return False, "参加工作还不满两年，先把手头的事做熟。"
    h = con.execute(
        "SELECT start_date FROM office_holding WHERE character_id=? AND end_date IS NULL "
        "AND position_slot_id IS NOT NULL ORDER BY start_date DESC LIMIT 1",
        (cid,)).fetchone()
    if h:
        tenure = (on - date.fromisoformat(h["start_date"])).days / 365
        if tenure < MIN_TENURE_YEARS:
            return False, "到这个岗位还不满一年，这时候提出去学习不合适。"
    if not programs_for(con, cid, on):
        return False, "按现在的职务层次，没有对口的班次。"
    return True, None


def open_session(con, program_id, on):
    """开一期班。"""
    p = con.execute("SELECT * FROM training_program WHERE id=?", (program_id,)).fetchone()
    start = on + timedelta(days=30)
    end = start + timedelta(days=p["days"])
    cur = con.execute(
        "INSERT INTO training_session(program_id,start_date,end_date,quota) "
        "VALUES(?,?,?,?)", (p["id"], start.isoformat(), end.isoformat(), p["quota"]))
    return cur.lastrowid, p, start, end


# 各级党校招生的范围：县级班在本县，省级班来自全省各市各部门，
# 中央党校来自全国。世界里有几级就取到几级——这就是"横向干部关系"
# 唯一能落地的地方（蓝图八第 4 条）。
CLASSMATE_SCOPE = {
    "COUNTY": ("COUNTY",),
    "MUNICIPAL": ("COUNTY", "MUNICIPAL"),
    "PROVINCIAL": ("MUNICIPAL", "PROVINCIAL"),
    "CENTRAL": ("PROVINCIAL", "CENTRAL"),
}
CLASSMATE_COUNT = {"COUNTY": 4, "MUNICIPAL": 5, "PROVINCIAL": 6, "CENTRAL": 6}


def make_classmates(con, cid, school_level, on, rng):
    """同班同学。

    蓝图八说党校给的第四样东西是横向干部关系：同一班次的人来自不同地区
    和系统，几十年后他们可能分别进入地方党委、省级机关、中央机关。

    所以班次层级越高，同学的来路越远——县委党校的同学还是本县的人，
    省委党校的同学就是市里和省直机关的人了。这些关系在你日后
    联系那些单位时才用得上。
    """
    from gongpu import relations
    scope = CLASSMATE_SCOPE.get(school_level, ("COUNTY",))
    n = CLASSMATE_COUNT.get(school_level, 4)
    mine = con.execute(
        "SELECT s.organization_id FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE h.character_id=? AND h.end_date IS NULL LIMIT 1", (cid,)).fetchone()
    mine = mine[0] if mine else None
    pool = [r[0] for r in con.execute(
        "SELECT DISTINCT c.id FROM office_holding h "
        "JOIN character c ON c.id = h.character_id "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.end_date IS NULL AND c.alive=1 AND c.retired=0 AND c.id != ? "
        "AND o.admin_level IN (%s) AND s.organization_id IS NOT ? "
        "ORDER BY c.id" % ",".join("?" * len(scope)),
        (cid,) + scope + (mine,))]
    if not pool:
        return []
    picked = []
    for _ in range(min(n, len(pool))):
        who = pool.pop(int(rng["career"].random() * len(pool)))
        relations.ensure(con, cid, who, "党校同学", familiarity=30, trust=20, on=on)
        picked.append(who)
    return picked


def enroll(con, cid, program_id, on, requested=0, rng=None):
    """报到入学。名额占用一个，同时认识一批同学。"""
    sid, p, start, end = open_session(con, program_id, on)
    cur = con.execute(
        "INSERT INTO training_enrollment(character_id,session_id,program,program_type,"
        "school_level,start_date,requested) VALUES(?,?,?,?,?,?,?)",
        (cid, sid, school_name(p["school_level"]) + p["name"], p["program_type"],
         p["school_level"], start.isoformat(), requested))
    mates = make_classmates(con, cid, p["school_level"], start, rng) if rng else []
    if mates:
        con.execute("UPDATE training_enrollment SET classmates=? WHERE id=?",
                    (len(mates), cur.lastrowid))
    log_event(con, on, "training_enrolled",
              {"program": p["name"], "school": school_name(p["school_level"]),
               "type": p["program_type"], "days": p["days"],
               "requested": bool(requested)}, actors=[cid])
    return cur.lastrowid, start, end


def campus_actions():
    return cfg()["campus_actions"]


def do_campus(con, enrollment_id, action, on, rng=None):
    """校内动作。只影响结业等次和同学数量，不影响任何任免条件。"""
    spec = cfg()["campus_actions"][action]
    g = spec.get("gain", {})
    if g.get("classmates") and rng is not None:
        e = con.execute("SELECT character_id, school_level FROM training_enrollment "
                        "WHERE id=?", (enrollment_id,)).fetchone()
        if e:
            make_classmates(con, e["character_id"], e["school_level"], on, rng)
    con.execute(
        "UPDATE training_enrollment SET theory = theory + ?, classmates = classmates + ? "
        "WHERE id=?", (g.get("theory", 0), g.get("classmates", 0), enrollment_id))
    return spec


def graduate(con, enrollment_id, on):
    """结业。等次看在校投入——但等次不改变任何任职条件，只写进履历。"""
    e = con.execute("SELECT * FROM training_enrollment WHERE id=?",
                    (enrollment_id,)).fetchone()
    if e is None or e["completed"]:
        return None
    result = "优秀学员" if e["theory"] >= 5 else "结业"
    con.execute(
        "UPDATE training_enrollment SET completed=1,end_date=?,result=? WHERE id=?",
        (on.isoformat(), result, enrollment_id))
    # §36 进过班次本身就是组织掌握干部的一条渠道
    con.execute(
        "INSERT INTO organization_attention(character_id,visibility,last_review) "
        "VALUES(?,2,?) ON CONFLICT(character_id) DO UPDATE SET "
        "visibility = min(organization_attention.visibility + 2, 10), last_review = ?",
        (e["character_id"], on.isoformat(), on.isoformat()))
    log_event(con, on, "training_completed",
              {"program": e["program"], "type": e["program_type"], "result": result},
              actors=[e["character_id"]])
    return result


def completed_types(con, cid):
    """这个人结业过哪些类型的班。任职资格要查这个。"""
    return {r[0] for r in con.execute(
        "SELECT DISTINCT program_type FROM training_enrollment "
        "WHERE character_id=? AND completed=1 AND program_type IS NOT NULL", (cid,))}


def requirement_for(level_name):
    """某个层次要求什么培训经历。满足其中任一项即可。"""
    return cfg()["requirements"].get(level_name, [])


def meets_requirement(con, cid, level_name):
    need = requirement_for(level_name)
    if not need:
        return True, None
    have = completed_types(con, cid)
    if have & set(need):
        return True, None
    return False, "／".join(need)


def history(con, cid):
    return [dict(r) for r in con.execute(
        "SELECT program, program_type, start_date, end_date, completed, result, "
        "classmates FROM training_enrollment WHERE character_id=? ORDER BY start_date",
        (cid,))]
