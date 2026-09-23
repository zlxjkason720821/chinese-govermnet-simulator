"""世界装配与推进（技术文档 §7、§9、§29、§82）。

bootstrap 把 YAML 里的编制变成一个有人在岗的县。
advance_to 让这个县自己往前走，每一天都跑完到期事项。
"""
import json
from datetime import date, timedelta
from pathlib import Path

from gongpu import paths

import yaml

from gongpu import (caps, leadership, migration, ministries, npc, regions,
                    relations, rules, training)
from gongpu.appointment import log_event
from gongpu.clock import SimulationClock
from gongpu.db import open_world, title_of
from gongpu.people import generate

DATA = paths.DATA

# 各层次岗位在任者的典型年龄区间：县委书记不会是 25 岁，办事员不会全是 50 岁。
AGE_BAND = {
    "正国级": (60, 68), "副国级": (57, 66),
    "正部级": (55, 64), "副部级": (52, 61), "正厅级": (48, 58), "副厅级": (44, 55),
    "正处级": (43, 56), "副处级": (38, 53), "正科级": (33, 52),
    "副科级": (29, 48), "科员": (22, 50), "办事员": (19, 35),
}
FILL_RATE = 0.92            # 一般岗位开局并非满编，留下少量真实空缺

# 但四套班子的正职开局必须有人。
# 县长、书记、人大主任、政协主席长期空着不成立，而且一旦开局就空，
# 由于提拔要求"现职满三年"，开局所有人都刚到任，这个坑会连着空好几年。
MUST_FILL_LEVELS = ("正处级", "正厅级", "正部级")


MUST_FILL_LEVELS_EXTRA = ("正厅级",)


def female_rate(level, on):
    """领导班子里女干部的比例随年代上升。

    1986 年县处级班子里女干部很少；2000 年代起有班子配备女干部的要求，
    比例逐步提高。用统一的 25% 会让 1986 年的县委常委会失真。
    """
    if level in ("科员", "办事员", "副科级"):
        return 0.35
    base = {"正科级": 0.12, "副处级": 0.08, "正处级": 0.05,
            "副厅级": 0.06, "正厅级": 0.04, "副部级": 0.03, "正部级": 0.02}.get(level, 0.2)
    # 按年份线性抬升，到 2020 年代约为 1986 年的三倍
    return min(base * (1 + (on.year - 1986) * 0.05), 0.35)


def load_scenario(name="county_1986"):
    return yaml.safe_load((DATA / (name + ".yaml")).read_text(encoding="utf-8"))


def bootstrap(con, rng, cfg=None):
    cfg = cfg or load_scenario()
    on = cfg["start_date"]
    on = on if isinstance(on, date) else date.fromisoformat(on)
    w = rng["world"]

    org_id, pdef_id = {}, {}
    for o in cfg["organizations"]:
        cur = con.execute(
            "INSERT INTO organization(name,short_name,admin_level,protocol_order,"
            "organization_type,system_type,institution_grade,parent_id,valid_from,"
            "archetype,personnel_control,visibility) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (o["name"], o.get("short", o["name"]), o.get("level", "COUNTY"),
             o.get("protocol"), o["type"], o.get("system"), o["grade"],
             org_id.get(o.get("parent")), "1949-10-01",
             o.get("archetype"), o.get("control", "LOCAL"),
             o.get("visibility", "PUBLIC")))
        org_id[o["key"]] = cur.lastrowid
    for a in cfg["authorities"]:
        con.execute("INSERT INTO cadre_management_authority(level,organization_id) "
                    "VALUES(?,?)", (a["level"], org_id[a["organization"]]))
    for p in cfg["position_definitions"]:
        cur = con.execute(
            "INSERT INTO position_definition(name,is_leadership,management_authority,"
            "min_age,max_age,party_requirement,min_years_experience,leadership_level,"
            "protocol_order,valid_from) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (p["name"], p["lead"], p["auth"], p["min_age"], p["max_age"],
             p["party"], p["exp"], p["level"], p.get("protocol", 99), "1949-10-01"))
        pdef_id[p["key"]] = cur.lastrowid

    training.install(con)
    levels = {p["key"]: p["level"] for p in cfg["position_definitions"]}
    protocols = {p["key"]: p.get("protocol", 99) for p in cfg["position_definitions"]}
    for s in cfg["slots"]:
        # 领导秘书跟人走：先找到所服务的领导岗位，一个秘书配一个领导。
        served = []
        if s.get("serves"):
            sv = s["serves"]
            served = [r[0] for r in con.execute(
                "SELECT id FROM position_slot WHERE organization_id=? "
                "AND position_definition_id=? ORDER BY id",
                (org_id[sv["org"]], pdef_id[sv["pos"]]))]
        for i in range(s["n"]):
            cur = con.execute(
                "INSERT INTO position_slot(position_definition_id,organization_id,"
                "valid_from,status,serves_slot_id) VALUES(?,?,?,'VACANT',?)",
                (pdef_id[s["pos"]], org_id[s["org"]], "1949-10-01",
                 served[i] if i < len(served) else None))
            must = (levels[s["pos"]] in MUST_FILL_LEVELS and protocols[s["pos"]] == 1)
            if must or w.random() < FILL_RATE:
                _seat(con, cur.lastrowid, levels[s["pos"]], on, w)

    # §20 兼任。县委常委兼组织部长、公安局长兼副县长——这是常态。
    # 兼任有结构性后果：一把手既然是常委，就顾不上本机关的日常，
    # 那个机关才需要设分管日常工作的副职。
    _seat_concurrent(con, cfg, org_id, pdef_id, on, rng)
    # 中央部委。带年代：1986 年是国家教委、外经贸部、冶金工业部那一套，
    # 不是 2026 年的教育部、商务部、工信部。
    ministries.install(con, on, rng)
    # §72 全国其余省份只建省级班子，不往下铺市县——背景世界不必实例化到底
    regions.install(con, on, rng, skip_name=regions.home_province())
    # 班子不是"一个正职 + 若干完全相同的副职"：排序、党内职务、
    # 以及该不该设分管日常工作的副职，都在这里定下来。
    leadership.install(con, on)
    leadership.assign_party_posts(con)
    # 权限按岗位给，不按级别开关（V3 §5）
    caps.install(con)
    relations.seed_colleagues(con, on)
    relations.seed_classmates(con, on, rng)
    con.execute(
        "INSERT INTO world_state(world_id,scenario_id,current_date,random_seed,"
        "ruleset_version) VALUES(?,?,?,?,?)",
        ("w1", cfg["scenario_id"], on.isoformat(), str(rng.seed), rules.resolve(on).era_id))
    con.commit()
    return on


def _seat(con, slot_id, level, on, w):
    """给一个岗位配一名在任干部。年龄和资历都必须撑得住这个岗位。"""
    lo, hi = AGE_BAND[level]
    age = lo + int(w.random() * (hi - lo + 1))
    gender = "F" if w.random() < female_rate(level, on) else "M"
    p = generate(w, on.year - age, on, gender=gender)
    pdef = con.execute(
        "SELECT d.* FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.id = ?", (slot_id,)).fetchone()
    if pdef["party_requirement"] and p["party_status"] != "MEMBER":
        p["party_status"] = "MEMBER"
        p["party_join_date"] = date(on.year - age + 24, 7, 1).isoformat()
    # 资历必须真的够，否则开局就有人不满足自己在任岗位的资格条件
    need = on.year - pdef["min_years_experience"] - 1
    if date.fromisoformat(p["work_start_date"]).year > need:
        p["work_start_date"] = date(max(need, on.year - age + 18), 7, 1).isoformat()

    cur = con.execute(
        "INSERT INTO character(name,gender,birth_date,party_status,party_join_date,"
        "education_level,career_origin,work_start_date,personality,tier) "
        "VALUES(?,?,?,?,?,?,?,?,?,'B')",
        (p["name"], p["gender"], p["birth_date"], p["party_status"], p["party_join_date"],
         p["education_level"], p["career_origin"], p["work_start_date"],
         json.dumps(p["personality"])))
    cid = cur.lastrowid

    # 参加工作到现岗位之间的经历压成一条已结束履历，保证资历可按履历累计
    ws = date.fromisoformat(p["work_start_date"])
    prior_end = date(on.year - 1 - int(w.random() * 5), 6, 30)
    if ws < prior_end:
        # 现岗位之前的经历不挂在这个岗位上（slot 为空），否则这个岗位的
        # 历任名单会从此人参加工作那年算起
        con.execute(
            "INSERT INTO office_holding(character_id,position_slot_id,start_date,"
            "end_date,title_at_time,exit_reason) VALUES(?,NULL,?,?,'干部','TRANSFER')",
            (cid, ws.isoformat(), prior_end.isoformat()))
        start = prior_end + timedelta(days=1)
    else:
        start = ws
    con.execute(
        "INSERT INTO office_holding(character_id,position_slot_id,start_date,title_at_time) "
        "VALUES(?,?,?,?)", (cid, slot_id, start.isoformat(), title_of(con, slot_id)))
    con.execute("UPDATE position_slot SET status='OCCUPIED',holder_id=? WHERE id=?",
                (cid, slot_id))
    _backfill_training(con, cid, level, on)
    return cid


def _backfill_training(con, cid, level, on):
    """开局在任的干部，履历里要有与其层次相称的培训经历。

    不补的话，1986 年全县没有一个人满足新加的培训门槛，
    整个任用链会当场断掉——开局就违反制度的世界，后面推演都不可信。
    """
    need = training.requirement_for(level)
    if not need:
        return
    kind = need[0]
    row = con.execute(
        "SELECT * FROM training_program WHERE program_type=? AND valid_from <= ? "
        "ORDER BY id LIMIT 1", (kind, on.isoformat())).fetchone()
    if row is None:
        return
    start = date(on.year - 2 - int(hash((cid, kind)) % 6), 3, 1)
    con.execute(
        "INSERT INTO training_enrollment(character_id,program,program_type,school_level,"
        "start_date,end_date,completed,result) VALUES(?,?,?,?,?,?,1,'结业')",
        (cid, training.school_name(row["school_level"]) + row["name"], kind,
         row["school_level"], start.isoformat(),
         (start + timedelta(days=row["days"])).isoformat()))


def create_player(con, rng, on, name, career_origin="SELECTED_GRADUATE",
                  education="本科", gender="M", birth_year=None):
    """§82 创建角色。选调生是干部来源标签，不是职业 class（§39）。"""
    birth_year = birth_year or on.year - 23
    # §82 开局是县乡基层世界。选调生报到只能到县里——
    # 按 slot id 取"第一个空缺的非领导岗位"的话，中央和省级机构的 id 更小，
    # 新人会直接出现在省委组织部，而界面上的班子、项目、会议全是县里的，
    # 整个世界当场对不上。
    # 起步只在有奔头的地方：党委、政府和它们的工作部门、乡镇。
    # 人大政协是职业后段的去处，新人分到那儿等于开局就进了养老院。
    slot = con.execute(
        "SELECT s.id FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE s.status='VACANT' AND d.is_leadership=0 AND o.admin_level='COUNTY' "
        "AND o.organization_type NOT IN ('PEOPLES_CONGRESS','CPPCC') "
        "AND COALESCE(o.system_type,'') NOT IN ('人大','政协') "
        "ORDER BY s.id LIMIT 1").fetchone()
    if slot is None:
        raise RuntimeError("县里没有空缺的基层岗位可供报到")
    p = generate(rng["world"], birth_year, on, career_origin, gender)
    cur = con.execute(
        "INSERT INTO character(name,gender,birth_date,party_status,party_join_date,"
        "education_level,career_origin,work_start_date,personality,tier,is_player) "
        "VALUES(?,?,?,?,?,?,?,?,?,'A',1)",
        (name, gender, p["birth_date"], p["party_status"], p["party_join_date"],
         education, career_origin, date(on.year, 7, 1).isoformat(),
         json.dumps(p["personality"])))
    cid = cur.lastrowid
    title = title_of(con, slot["id"])
    con.execute(
        "INSERT INTO office_holding(character_id,position_slot_id,start_date,title_at_time) "
        "VALUES(?,?,?,?)", (cid, slot["id"], on.isoformat(), title))
    con.execute("UPDATE position_slot SET status='OCCUPIED',holder_id=? WHERE id=?",
                (cid, slot["id"]))
    # 玩家和同屋的人当天就认识了；直接领导是 mentor 关系的起点
    for (other,) in con.execute(
            "SELECT h.character_id FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "WHERE h.end_date IS NULL AND s.organization_id = "
            "(SELECT organization_id FROM position_slot WHERE id=?) "
            "AND h.character_id != ?", (slot["id"], cid)):
        relations.ensure(con, cid, other, "同事", familiarity=15, trust=5, on=on)
    boss = con.execute(
        "SELECT h.character_id FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.end_date IS NULL AND d.is_leadership=1 AND s.organization_id = "
        "(SELECT organization_id FROM position_slot WHERE id=?) "
        "ORDER BY d.protocol_order LIMIT 1", (slot["id"],)).fetchone()
    if boss:
        relations.ensure(con, cid, boss[0], "上下级", familiarity=20, trust=10, on=on)
    con.execute("UPDATE world_state SET player_id=? WHERE world_id='w1'", (cid,))
    log_event(con, on, "player_reported", {"slot": slot["id"], "title": title}, actors=[cid])
    con.commit()
    return cid


def new_game(seed="1986", player_name="林致远", scenario="county_1986", path=":memory:"):
    from gongpu.rng import RandomService
    con = open_world(path)
    rng = RandomService(seed)
    on = bootstrap(con, rng, load_scenario(scenario))
    pid = create_player(con, rng, on, player_name)
    return con, rng, SimulationClock(on), pid


def _seat_concurrent(con, cfg, org_id, pdef_id, on, rng):
    """把兼任关系做出来：同一个人并存两条任职记录（§20）。

    主职是层次高的那个（县委常委），兼的是工作部门的正职（组织部长）。
    """
    made = 0
    for c in cfg.get("concurrent", []):
        hi, lo = c["holder"], c["also"]
        if hi["org"] not in org_id or lo["org"] not in org_id:
            continue
        # 已经有人的高层岗位里挑一个还没兼职的
        holder = con.execute(
            "SELECT h.character_id AS cid FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "WHERE h.end_date IS NULL AND s.organization_id=? "
            "AND s.position_definition_id=? "
            "AND NOT EXISTS (SELECT 1 FROM office_holding h2 "
            "  JOIN position_slot s2 ON s2.id = h2.position_slot_id "
            "  WHERE h2.character_id = h.character_id AND h2.end_date IS NULL "
            "    AND s2.organization_id != s.organization_id) "
            "ORDER BY h.id LIMIT 1",
            (org_id[hi["org"]], pdef_id[hi["pos"]])).fetchone()
        slot = con.execute(
            "SELECT id FROM position_slot WHERE organization_id=? "
            "AND position_definition_id=? ORDER BY id LIMIT 1",
            (org_id[lo["org"]], pdef_id[lo["pos"]])).fetchone()
        if holder is None or slot is None:
            continue
        old = con.execute("SELECT holder_id FROM position_slot WHERE id=?",
                          (slot["id"],)).fetchone()[0]
        if old is not None:
            con.execute("UPDATE office_holding SET end_date=?,exit_reason='CONCURRENT' "
                        "WHERE position_slot_id=? AND end_date IS NULL",
                        (on.isoformat(), slot["id"]))
        con.execute(
            "INSERT INTO office_holding(character_id,position_slot_id,start_date,"
            "title_at_time,primary_position,appointment_type) "
            "VALUES(?,?,?,?,0,'CONCURRENT')",
            (holder["cid"], slot["id"], on.isoformat(), title_of(con, slot["id"])))
        con.execute("UPDATE position_slot SET status='OCCUPIED',holder_id=? WHERE id=?",
                    (holder["cid"], slot["id"]))
        made += 1
    return made


def advance_to(con, clock, target, rng):
    """§29 推进不是改日期：跨过的每一天都要跑完到期事项。"""
    era = rules.resolve(clock.date).era_id
    while clock.date < target:
        _, today = clock.advance(days=1)
        r = rules.resolve(today)
        if r.era_id != era:
            # §25 跨制度年份执行迁移。没有登记迁移的 Era 只记一条时代变更。
            if migration.run(con, today, r.era_id, rng, r) is None:
                log_event(con, today, "era_change", {"from": era, "to": r.era_id})
            era = r.era_id
        # 区划本身也会变：1988 海南建省、1997 重庆直辖、1997/1999 港澳回归。
        # 这几件事就落在这局游戏的时间窗口里，到了日子才出现。
        for p in regions.due_on(con, today, rng):
            log_event(con, today, "region_established",
                      {"name": p["name"], "note": p.get("note", "")})
        # 机构改革也落在这四十年里：1998 年一次撤掉十几个部。
        ministries.reform(con, today, rng)
        npc.tick(con, today, rng, r)
    con.execute(
        "UPDATE world_state SET current_date=?,ruleset_version=?,random_state=? "
        "WHERE world_id='w1'",
        (clock.date.isoformat(), era, json.dumps(rng.dump())))
    con.commit()
    return clock.date
