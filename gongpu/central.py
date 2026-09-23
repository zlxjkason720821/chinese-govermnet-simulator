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
# 名额由全国代表大会决定，不是一个永远不变的常量。
# 这四十年里它一届一届在变，而且是查得到的：
#
#   十三大 1987　175 委员 + 110 候补　（首次差额选举）
#   十四大 1992　189 + 130
#   十五大 1997　193 + 151
#   十六大 2002　198 + 158
#   十七大 2007　204 + 167　（这一届起政治局不再设候补委员）
#   十八大 2012　205 + 171
#   十九大 2017　204 + 172
#   二十大 2022　205 + 171
#
# 规模不能缩：光是三十一个省区市的党政正职就有六十多人，再加上
# 中央和国务院几十个部委的正职，委员会小了就装不下，
# 于是省委书记会落选——那是算法的毛病，不是制度。
QUOTA = {
    13: (175, 110), 14: (189, 130), 15: (193, 151), 16: (198, 158),
    17: (204, 167), 18: (205, 171), 19: (204, 172), 20: (205, 171),
    21: (205, 171),          # 还没开，沿用上一届的名额
}

# 政治局的规模。二十届是 24 人（含常委 7 人）。
POLITBURO_SIZE = 24
STANDING_SIZE = 7


def quota(congress):
    """这一届选多少委员、多少候补。"""
    m, a = QUOTA.get(congress, QUOTA[max(QUOTA)])
    return {MEMBER: m, ALTERNATE: a, POLITBURO: POLITBURO_SIZE,
            STANDING: STANDING_SIZE, GENERAL_SECRETARY: 1}


# 兼容旧调用：没指明届次时按最近一届的名额
SIZE = {ALTERNATE: 171, MEMBER: 205, POLITBURO: POLITBURO_SIZE,
        STANDING: STANDING_SIZE, GENERAL_SECRETARY: 1}

# 蓝图二十：七上八下必须按历史惯例建模。
# 党代会当年年满 68 的不再进入新一届；67 及以下可以。
AGE_CEILING = 67
# 进入中央委员会的最低行政层次：副部级。这是"当时担任的重要岗位"那一条。
MIN_LEVEL = LEVEL_ORDER["副部级"]


def rules_cfg():
    from gongpu import ministries
    return ministries.committee_rules()


def ex_officio_posts():
    """中央委员的主要来源岗位。

    公开的构成说法是：各省、自治区、直辖市党委书记及政府首长，
    中共中央直属机构和国务院下属机构的正部级主要负责人，等等。

    注意这是**来源分类**，不是逐岗位配额——中央委员不是正省部级的
    同义词，某个岗位也不天然拥有一个席位。所以它在这里只用来把人
    排到前面，不保证进得去：党代会之后才上任的照样要等下一届。
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
    size = quota(congress)
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
    standing = [c for c in pool if c["id"] in central_posts][:size[STANDING]]
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
        if c["pb_region"] and len(politburo) < size[POLITBURO]:
            politburo.append(c)
    seated = {c["id"] for c in politburo}
    for c in eligible_pb:
        if len(politburo) >= size[POLITBURO]:
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
    need = size[MEMBER] + size[ALTERNATE] - len(politburo)
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
    _step_down(con, on)
    con.execute("UPDATE party_central_status SET end_date=? WHERE end_date IS NULL",
                (on.isoformat(),))
    politburo, standing, rest, prev, central_posts = _compose(con, on, congress, rng)
    if not politburo and not rest:
        return []

    size = quota(congress)
    members = rest[:size[MEMBER] - len(politburo)]
    alternates = rest[len(members):len(members) + size[ALTERNATE]]

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


def seat_vacated(con, cid, on, reason):
    """这个人的党内身份终止了。如果他是中央委员，位子当场由候补委员递补。

    出缺和递补是同一件事的两面，必须在同一个动作里完成。
    分开写就会出岔子：去世那一行先把身份关掉，月底再扫就什么也看不见了，
    于是名册一路缩水，候补委员在旁边闲着——上一版正是这么错的。
    """
    rows = con.execute(
        "SELECT id, status, congress FROM party_central_status "
        "WHERE character_id=? AND end_date IS NULL", (cid,)).fetchall()
    if not rows:
        return None
    con.execute("UPDATE party_central_status SET end_date=? "
                "WHERE character_id=? AND end_date IS NULL", (on.isoformat(), cid))
    seat = next((r for r in rows if r["status"] == MEMBER), None)
    if seat is None:
        return None                # 候补委员、政治局出缺都不由候补委员递补
    return _promote_alternate(con, on, seat["congress"], reason)


def _promote_alternate(con, on, congress, reason):
    """按当选时的得票顺序，递补一名候补委员（入库顺序即名次）。"""
    up = con.execute(
        "SELECT p.id, p.character_id FROM party_central_status p "
        "JOIN character c ON c.id = p.character_id "
        "WHERE p.end_date IS NULL AND p.status=? AND c.alive=1 "
        "AND c.discipline_status='CLEAR' ORDER BY p.id LIMIT 1", (ALTERNATE,)).fetchone()
    if up is None:
        return None                # 候补也用完了，那就空着
    con.execute("UPDATE party_central_status SET end_date=? WHERE id=?",
                (on.isoformat(), up["id"]))
    con.execute(
        "INSERT INTO party_central_status(character_id,status,congress,start_date) "
        "VALUES(?,?,?,?)", (up["character_id"], MEMBER, congress, on.isoformat()))
    log_event(con, on, "central_alternate_promoted",
              {"note": "中央委员出缺（%s），由候补委员依次递补" % reason},
              actors=[up["character_id"]])
    return up["character_id"]


def fill_vacancies(con, on):
    """中央委员出缺，由候补委员按得票多少依次递补（党章第二十二条）。

    出缺只有两种：去世，和开除党籍。不包括退休，也不包括岗位变动——
    地方或部委产生职位空缺，不会触发中央委员递补，那是两本不同的名册、
    两类不同的事件。

    真实的二十届，四年递补十四名（另有八人因违纪违法被开除党籍）。
    这是个位数量级的事，不是每月都在发生的常规操作。
    """
    gone = con.execute(
        "SELECT p.id, p.character_id, p.congress FROM party_central_status p "
        "JOIN character c ON c.id = p.character_id "
        "WHERE p.end_date IS NULL AND p.status = ? "
        "AND (c.alive = 0 OR c.discipline_status = 'REMOVED')", (MEMBER,)).fetchall()
    if not gone:
        return []
    filled = []
    for r in gone:                        # 兜底：有漏网的，这里补上
        con.execute("UPDATE party_central_status SET end_date=? WHERE id=?",
                    (on.isoformat(), r["id"]))
        who = _promote_alternate(con, on, r["congress"], "出缺")
        if who:
            filled.append(who)
    return filled


def _step_down(con, on):
    """换届时，超过年龄线的党和国家领导人离任。

    这一层不按行政退休年龄走，走的是党代会上的七上八下：
    当年年满六十八的不再进入新一届，位子在换届这天腾出来。
    """
    from gongpu import npc
    out = []
    for r in con.execute(
            "SELECT DISTINCT c.id, c.birth_date FROM office_holding h "
            "JOIN character c ON c.id = h.character_id "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.end_date IS NULL AND c.alive=1 AND c.retired=0 "
            "AND d.leadership_level IN ('副国级','正国级')").fetchall():
        if _age(r["birth_date"], on) > AGE_CEILING:
            npc.retire(con, r["id"], on)
            out.append(r["id"])
    return out


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


def committee_size(con):
    """中央委员会现有多少人。

    政治局委员、常委、总书记同时也是中央委员，只是挂着更高的身份标签，
    所以只数"中央委员"那一档会少二十几个人。
    """
    return con.execute(
        "SELECT count(*) FROM party_central_status WHERE end_date IS NULL "
        "AND status IN (?,?,?,?)",
        (MEMBER, POLITBURO, STANDING, GENERAL_SECRETARY)).fetchone()[0]


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
