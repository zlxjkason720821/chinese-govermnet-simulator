"""中央委员会体系（蓝图 二十、二十三、二十四、二十五）。

三条红线，全部来自蓝图：

  二十三　中央委员会身份**独立于行政级别**。
          一个省部级干部可以不是中央委员；一个中央委员也不等于比谁高一级。
          所以人物界面必须同时显示 领导职务 和 党内身份，绝不合并。

  二十四　中央委员产生**与党代会周期绑定**。不能随时获得。
          错过某一届的年龄窗口，就得再等五年——而五年对六十岁的人
          可能彻底改变职业上限。

  二十五　政治局与常委会**绝不能用普通升迁算法**。
          这里退出的是 PromotionEngine，进场的是 LeadershipCompositionEngine：
          它算的是结构（地域、部门、年龄梯次、连任），不是"政绩高就进"。
"""
import json
from datetime import date

from gongpu.appointment import LEVEL_ORDER, _age, log_event

# 全国党代会年份。这是公开记录，不是随机数。
CONGRESS_YEARS = {
    1987: 13, 1992: 14, 1997: 15, 2002: 16, 2007: 17,
    2012: 18, 2017: 19, 2022: 20, 2027: 21,
}

ALTERNATE = "中央候补委员"
MEMBER = "中央委员"
POLITBURO = "政治局委员"
STANDING = "政治局常委"
GENERAL_SECRETARY = "总书记"
RANKS = [ALTERNATE, MEMBER, POLITBURO, STANDING, GENERAL_SECRETARY]

# 规模按这个世界的体量缩放。真实中央委员会两百余人，这里一个省的世界装不下。
# 照真实规模来。二十届是中央委员 205 人、候补委员 152 人、
# 政治局委员 24 人（含常委 7 人）。
#
# 规模不能缩：光是三十一个省区市的党政正职就有六十多人，再加上
# 中央和国务院几十个部委的正职，委员会小了就装不下这些"本来就该在里面"的人，
# 于是省委书记会落选——那是算法的毛病，不是制度。
SIZE = {ALTERNATE: 150, MEMBER: 205, POLITBURO: 24, STANDING: 7,
        GENERAL_SECRETARY: 1}

# 蓝图二十：七上八下必须按历史惯例建模。
# 党代会当年年满 68 的不再进入新一届；67 及以下可以。
AGE_CEILING = 67
# 进入中央委员会的最低行政层次：副部级。这是"当时担任的重要岗位"那一条。
MIN_LEVEL = LEVEL_ORDER["副部级"]


def rules_cfg():
    from gongpu import ministries
    return ministries.committee_rules()


def ex_officio_posts():
    """默认进中央委员会的职务。

    公开的构成惯例：各省、自治区、直辖市党委书记及政府首长，
    中共中央直属机构和国务院下属机构的正部级主要负责人。
    这不是"表现好所以当选"，是这个位子本来就在委员会里。
    """
    return {x["post"] for x in rules_cfg()["ex_officio"]}


def politburo_regions():
    """由政治局委员兼任党委书记的地方。

    四个直辖市是定例，广东是经济第一大省，新疆是战略性自治区。
    这条惯例意味着：同样是省委书记，放在哪个省，党内身份不一样。
    """
    return set(rules_cfg()["politburo_regions"])


def party_years(con, cid, on):
    r = con.execute("SELECT party_join_date FROM character WHERE id=?", (cid,)).fetchone()
    if r is None or not r[0]:
        return 0.0
    return (on - date.fromisoformat(str(r[0]))).days / 365.2425


def is_congress_year(on):
    return on.year in CONGRESS_YEARS and (on.month, on.day) == (10, 20)


def current_status(con, cid):
    r = con.execute(
        "SELECT status FROM party_central_status WHERE character_id=? AND end_date IS NULL "
        "ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    return r["status"] if r else None


def rank_of(status):
    return RANKS.index(status) if status in RANKS else -1


def _candidates(con, on):
    """有资格进入新一届的人：在任、副部级以上、年龄过得去、没有纪律问题。

    这就是二十四说的"当时担任的重要岗位"——不是过去当过，是现在在任。
    """
    out = []
    for r in con.execute(
            "SELECT c.id, c.name, c.birth_date, c.gender, "
            " d.leadership_level AS lvl, o.short_name AS org, o.admin_level AS al, "
            " o.organization_type AS otype, h.title_at_time AS title, "
            " d.name AS post, o.name AS orgfull "
            "FROM office_holding h JOIN character c ON c.id = h.character_id "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE h.end_date IS NULL AND h.primary_position=1 "
            "AND c.alive=1 AND c.retired=0 "
            "AND c.discipline_status='CLEAR' ORDER BY c.id"):
        lvl = LEVEL_ORDER.get(r["lvl"], 0)
        if lvl < MIN_LEVEL:
            continue
        if _age(r["birth_date"], on) > AGE_CEILING:
            continue          # 七上八下
        # 党章第二十二条：中央委员会委员和候补委员必须有五年以上的党龄。
        if party_years(con, r["id"], on) < rules_cfg()["min_party_years"]:
            continue
        out.append(dict(r, order=lvl,
                        ex_officio=_is_ex_officio(r),
                        pb_region=_in_politburo_region(r)))
    return out


def _is_ex_officio(r):
    """这个位子本来就在中央委员会里。"""
    if LEVEL_ORDER.get(r["lvl"], 0) < LEVEL_ORDER["正部级"]:
        return False
    if r["post"] not in ex_officio_posts():
        return False
    # 省级党政正职，或者中央机关的正部级主要负责人
    return r["al"] in ("PROVINCIAL", "CENTRAL")


def _in_politburo_region(r):
    """京津沪渝粤新的党委书记，按惯例是政治局委员。"""
    if r["post"] != "书记" or r["al"] != "PROVINCIAL":
        return False
    full = r["orgfull"] or ""
    return any(name in full for name in politburo_regions())


def _central_post_holders(con):
    """现在担任中央职务的人。政治局常委的行政职务只能出在这里——
    总书记不可能是省长。"""
    return {r["id"]: dict(r) for r in con.execute(
        "SELECT c.id, d.leadership_level AS lvl, d.name AS post, "
        " h.title_at_time AS title FROM office_holding h "
        "JOIN character c ON c.id = h.character_id "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE h.end_date IS NULL AND o.admin_level='CENTRAL' "
        "AND d.is_leadership=1 AND c.alive=1 AND c.retired=0")}


def _compose(con, on, congress, rng):
    """LeadershipCompositionEngine：按结构定人选，不按政绩排序（二十五）。

    层次是照着真实结构来的：

      政治局常委　只能是担任中央职务的人（总书记、总理、委员长、
                  政协主席、书记处书记、中央纪委书记、副总理）
      政治局委员　常委 + 正部级（省委书记、中央部委负责人）
      中央委员　　副部级以上在任
      候补委员　　其余

    排序依据从重到轻：现任行政层次、上一届党内身份（连任是常态）、
    地域与部门结构、年龄梯次。政绩不在其中。
    """
    pool = _candidates(con, on)
    prev = {r["character_id"]: r["status"] for r in con.execute(
        "SELECT character_id, status FROM party_central_status WHERE end_date IS NULL")}
    central_posts = _central_post_holders(con)

    def key(c):
        return (-c["order"],
                not c["ex_officio"],          # 这个位子本来就在委员会里的排前面
                -rank_of(prev.get(c["id"], "")),
                _age(c["birth_date"], on),
                c["id"])
    pool.sort(key=key)

    # 常委：只从担任中央职务的人里出
    standing = [c for c in pool if c["id"] in central_posts][:SIZE[STANDING]]
    standing_ids = {c["id"] for c in standing}

    # 政治局：常委，加上按惯例兼任的几个地方党委书记，再按层次补足。
    #
    # 京津沪渝粤新的党委书记由政治局委员兼任——同样是省委书记，
    # 放在哪个省，党内身份不一样。这是玩家该知道的一条路。
    #
    # 政治局委员的预备人选一般是 64 周岁以下的正省部级以上干部，
    # 比中央委员那条七上八下的线更紧。
    pb_age = rules_cfg()["politburo_max_age"]
    eligible_pb = [c for c in pool
                   if c["id"] not in standing_ids
                   and _age(c["birth_date"], on) <= pb_age
                   and c["order"] >= LEVEL_ORDER["正部级"]]
    politburo = list(standing)
    for c in eligible_pb:                       # 先把按惯例该进的放进去
        if c["pb_region"] and len(politburo) < SIZE[POLITBURO]:
            politburo.append(c)
    seated = {c["id"] for c in politburo}
    for c in eligible_pb:
        if len(politburo) >= SIZE[POLITBURO]:
            break
        if c["id"] not in seated:
            politburo.append(c)
            seated.add(c["id"])
    pb_ids = {c["id"] for c in politburo}

    # 中央委员与候补：按地域部门交替取，避免某一系统包揽
    by_system = {}
    for c in pool:
        if c["id"] in pb_ids:
            continue
        by_system.setdefault(c["al"], []).append(c)
    rest, seen, systems, i = [], set(), sorted(by_system), 0
    need = SIZE[MEMBER] + SIZE[ALTERNATE] - len(politburo)
    while len(rest) < need and any(by_system.values()):
        sysname = systems[i % len(systems)]
        i += 1
        if by_system.get(sysname):
            c = by_system[sysname].pop(0)
            if c["id"] not in seen:
                seen.add(c["id"])
                rest.append(c)
    return politburo, standing, rest, prev, central_posts


def hold_congress(con, on, rng, rules):
    """党代会：上一届全部届满，按结构产生新一届。

    这是唯一能产生中央委员身份的时刻（二十四）。
    """
    congress = CONGRESS_YEARS[on.year]
    con.execute("UPDATE party_central_status SET end_date=? WHERE end_date IS NULL",
                (on.isoformat(),))
    politburo, standing, rest, prev, central_posts = _compose(con, on, congress, rng)
    if not politburo and not rest:
        return []

    members = rest[:SIZE[MEMBER] - len(politburo)]
    alternates = rest[len(members):len(members) + SIZE[ALTERNATE]]

    # 总书记：优先连任；否则从常委里出，并且必须是中共中央总书记这个岗位上的人。
    gs_slot = con.execute(
        "SELECT s.id, s.holder_id FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE d.name='总书记' LIMIT 1").fetchone()
    gs = None
    if gs_slot and gs_slot["holder_id"]:
        gs = next((c for c in standing if c["id"] == gs_slot["holder_id"]), None)
    if gs is None:
        gs = next((c for c in standing if prev.get(c["id"]) == GENERAL_SECRETARY),
                  standing[0] if standing else None)

    assign = {}
    for c in alternates:
        assign[c["id"]] = ALTERNATE
    for c in members:
        assign[c["id"]] = MEMBER
    for c in politburo:
        assign[c["id"]] = POLITBURO
    for c in standing:
        assign[c["id"]] = STANDING
    if gs:
        assign[gs["id"]] = GENERAL_SECRETARY

    for cid, status in assign.items():
        con.execute(
            "INSERT INTO party_central_status(character_id,status,congress,start_date) "
            "VALUES(?,?,?,?)", (cid, status, congress, on.isoformat()))
    log_event(con, on, "party_congress",
              {"congress": congress, "note": "中国共产党第%d次全国代表大会" % congress,
               "members": len(members), "alternates": len(alternates),
               "politburo": len(politburo), "standing": len(standing)})
    if gs:
        log_event(con, on, "general_secretary",
                  {"congress": congress, "note": "选举产生总书记"}, actors=[gs["id"]])
        _seat_post(con, gs["id"], on, "总书记", primary=True)
        _seat_state_chairman(con, gs["id"], on)
    return list(assign.items())


def fill_vacancies(con, on):
    """中央委员出缺，由候补委员按得票多少依次递补（党章第二十二条）。

    按缺额补，不盯着某一个人离开——人退了、走了、被处分了，
    党内身份在别处就已经终止了（见 npc._close_central_status）。
    这里只回答一个问题：这一届选了多少委员，现在还剩多少。

    没有递补，委员会就只是党代会那一天的快照：三年下来
    一百八十一个中央委员会掉到一百三十二个，而候补委员在旁边闲着。
    """
    cur = con.execute(
        "SELECT max(congress) FROM party_central_status WHERE end_date IS NULL"
    ).fetchone()[0]
    if cur is None:
        return []
    elected = con.execute(
        "SELECT count(*) FROM party_central_status WHERE congress=? AND status=? "
        "AND start_date = (SELECT min(start_date) FROM party_central_status "
        "                  WHERE congress=?)", (cur, MEMBER, cur)).fetchone()[0]
    sitting = con.execute(
        "SELECT count(*) FROM party_central_status WHERE end_date IS NULL AND status=?",
        (MEMBER,)).fetchone()[0]
    short = elected - sitting
    if short <= 0:
        return []
    # 候补委员按当初的名次（入库顺序即得票顺序）依次递补
    ups = con.execute(
        "SELECT p.id, p.character_id FROM party_central_status p "
        "JOIN character c ON c.id = p.character_id "
        "WHERE p.end_date IS NULL AND p.status=? AND c.alive=1 AND c.retired=0 "
        "AND c.discipline_status='CLEAR' ORDER BY p.id LIMIT ?",
        (ALTERNATE, short)).fetchall()
    filled = []
    for up in ups:
        con.execute("UPDATE party_central_status SET end_date=? WHERE id=?",
                    (on.isoformat(), up["id"]))
        con.execute(
            "INSERT INTO party_central_status(character_id,status,congress,start_date) "
            "VALUES(?,?,?,?)", (up["character_id"], MEMBER, cur, on.isoformat()))
        log_event(con, on, "central_alternate_promoted",
                  {"note": "中央委员出缺，由候补委员递补"},
                  actors=[up["character_id"]])
        filled.append(up["character_id"])
    return filled


def _seat_post(con, cid, on, post_name, primary=True):
    """把人放到某个中央岗位上。

    选出来的总书记必须坐在总书记那个位子上——党内身份和行政职务对不上，
    界面上就会出现"总书记　全国政协主席"这种东西。
    """
    from gongpu.db import title_of
    slot = con.execute(
        "SELECT s.id, s.holder_id FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE d.name=? ORDER BY s.id LIMIT 1", (post_name,)).fetchone()
    if slot is None or slot["holder_id"] == cid:
        return None
    if slot["holder_id"]:
        con.execute("UPDATE office_holding SET end_date=?,exit_reason='TERM_END' "
                    "WHERE position_slot_id=? AND end_date IS NULL",
                    (on.isoformat(), slot["id"]))
    # 本人原来的主职腾出来
    if primary:
        old = [r[0] for r in con.execute(
            "SELECT position_slot_id FROM office_holding WHERE character_id=? "
            "AND end_date IS NULL AND primary_position=1 AND position_slot_id IS NOT NULL",
            (cid,))]
        if old:
            con.execute("UPDATE office_holding SET end_date=?,exit_reason='PROMOTED' "
                        "WHERE character_id=? AND end_date IS NULL AND primary_position=1",
                        (on.isoformat(), cid))
            con.executemany("UPDATE position_slot SET status='VACANT',holder_id=NULL "
                            "WHERE id=?", [(o,) for o in old])
    con.execute(
        "INSERT INTO office_holding(character_id,position_slot_id,start_date,"
        "title_at_time,primary_position) VALUES(?,?,?,?,?)",
        (cid, slot["id"], on.isoformat(), title_of(con, slot["id"]), 1 if primary else 0))
    con.execute("UPDATE position_slot SET status='OCCUPIED',holder_id=? WHERE id=?",
                (cid, slot["id"]))
    return slot["id"]


def _seat_state_chairman(con, cid, on):
    """三位一体：1993 年起，总书记同时担任国家主席。

    这是 §20 说的兼任——同一个人有多条 OfficeHolding，
    国家主席那一条记为非主要职务（primary_position=0）。
    1993 年之前党政分设，不做这个兼任。
    """
    if on.year < 1993:
        return None
    slot = con.execute(
        "SELECT s.id, s.holder_id FROM position_slot s "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE o.organization_type='STATE' ORDER BY s.id LIMIT 1").fetchone()
    if slot is None or slot["holder_id"] == cid:
        return None
    if slot["holder_id"]:
        con.execute("UPDATE office_holding SET end_date=?,exit_reason='TERM_END' "
                    "WHERE position_slot_id=? AND end_date IS NULL",
                    (on.isoformat(), slot["id"]))
    from gongpu.db import title_of
    con.execute(
        "INSERT INTO office_holding(character_id,position_slot_id,start_date,"
        "title_at_time,primary_position,holding_type) "
        "VALUES(?,?,?,?,0,'CONCURRENT')",
        (cid, slot["id"], on.isoformat(), title_of(con, slot["id"])))
    con.execute("UPDATE position_slot SET status='OCCUPIED',holder_id=? WHERE id=?",
                (cid, slot["id"]))
    log_event(con, on, "state_chairman",
              {"note": "总书记兼任国家主席"}, actors=[cid])
    return slot["id"]


def roster(con, on=None):
    """现任中央委员会名单，按党内身份排序。"""
    return [dict(r) for r in con.execute(
        "SELECT p.status, p.congress, p.start_date, c.id, c.name, c.gender, "
        " c.birth_date, c.education_level, "
        " (SELECT h.title_at_time FROM office_holding h "
        "  WHERE h.character_id = c.id AND h.end_date IS NULL "
        # 兼任的那条不是主要职务，显示要取主职（总书记，不是国家主席）
        "  ORDER BY h.primary_position DESC, h.id DESC LIMIT 1) AS title "
        "FROM party_central_status p JOIN character c ON c.id = p.character_id "
        "WHERE p.end_date IS NULL ORDER BY "
        " CASE p.status WHEN '总书记' THEN 0 WHEN '政治局常委' THEN 1 "
        "  WHEN '政治局委员' THEN 2 WHEN '中央委员' THEN 3 ELSE 4 END, c.id")]


def history_for(con, cid):
    return [dict(r) for r in con.execute(
        "SELECT status, congress, start_date, end_date FROM party_central_status "
        "WHERE character_id=? ORDER BY id", (cid,))]
