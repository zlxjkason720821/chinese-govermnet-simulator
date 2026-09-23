"""专业序列、高配、挂职。

这三样的共同点是：**都不能和行政级别合并**。

  警衔 / 法官等级 / 检察官等级
      一个正科级的公安局长有警衔，一个正科级的民政局长没有。
      "二级高级法官"不是行政级别，是另一条序列上的位置。
      而且各有设立年代：警衔 1992-07-01 条例施行，
      法官检察官等级 1997 年《暂行规定》才开始评定——
      1986 年开局时三样一个都不存在。

  高配
      副主任（正部长级）不是主任。职务是副职，个人职级更高。
      所以"副局长 = 固定副处"这种写法本来就是错的。

  挂职
      有期限，到期回原单位，**不占实际班子序列**。
      挂职副局长和真正的副局长，在库里从一开始就不是一回事。
      它给的是见识和履历上的一行字，不是台阶。
"""
from datetime import date

from gongpu.appointment import LEVEL_ORDER
from gongpu.leadership import templates

RANK_ARCHETYPES = ("PUBLIC_SECURITY", "STATE_SECURITY", "COURT", "PROCURATORATE")


def kinds():
    return templates().get("extra_ranks", {})


def kind_for(archetype, on):
    """这个机关的人该有哪一种专业序列。还没到设立年代就返回 None。"""
    for kind, spec in kinds().items():
        if archetype in spec.get("archetypes", []):
            if date.fromisoformat(str(spec["from"])) <= on:
                return kind, spec
    return None, None


def _grade_for(spec, level, years):
    """按职务层次定衔级。

    警衔条例第八条写的是"职务等级编制警衔"：一个层次对应一到三个衔级，
    任职久的往上走。所以同样是正科级，干了十年的和刚提的不一样。
    """
    fit = [g for g in spec["grades"] if level in g.get("levels", [])]
    if not fit:
        return None
    step = 0 if years < 4 else (1 if years < 9 else 2)
    return fit[min(step, len(fit) - 1)]["name"]


def current(con, cid):
    r = con.execute(
        "SELECT kind, grade FROM professional_rank "
        "WHERE character_id=? AND end_date IS NULL ORDER BY id DESC LIMIT 1",
        (cid,)).fetchone()
    return (r["kind"], r["grade"]) if r else (None, None)


def history(con, cid):
    return [dict(r) for r in con.execute(
        "SELECT kind, grade, start_date, end_date, exit_reason "
        "FROM professional_rank WHERE character_id=? ORDER BY id", (cid,))]


def revoke_on_transfer(con, on):
    """调离警察（法官、检察官）工作岗位的，衔级不予保留（条例第十九条）。

    这一条每月看一次：人一调走衔级就没了，不能等到年底评衔的时候才想起来。
    退休的不动——退休保留，调离不保留，这是两回事。
    """
    out = []
    for r in con.execute(
            "SELECT p.id, p.character_id AS cid FROM professional_rank p "
            "JOIN character c ON c.id = p.character_id "
            "WHERE p.end_date IS NULL AND c.retired=0 AND c.alive=1 "
            "AND NOT EXISTS (SELECT 1 FROM office_holding h "
            "  JOIN position_slot s ON s.id = h.position_slot_id "
            "  JOIN organization o ON o.id = s.organization_id "
            "  WHERE h.character_id = p.character_id AND h.end_date IS NULL "
            "    AND h.primary_position=1 AND o.archetype IN (%s))"
            % ",".join("?" * len(RANK_ARCHETYPES)), RANK_ARCHETYPES).fetchall():
        con.execute("UPDATE professional_rank SET end_date=?,exit_reason=? WHERE id=?",
                    (on.isoformat(), "调离本系统，衔级不予保留", r["id"]))
        out.append(r["cid"])
    return out


def grant(con, on):
    """每年评一次专业序列。

    在编在职的警察、法官、检察官该有衔级；调离这个系统的，
    警衔不予保留（条例第十九条），法官检察官等级同理。
    退休的保留——退休和调离不是一回事。
    """
    granted, revoked = [], []
    holders = {}
    for r in con.execute(
            "SELECT h.character_id AS cid, o.archetype AS arch, "
            " d.leadership_level AS lvl, h.start_date AS since "
            "FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "JOIN character c ON c.id = h.character_id "
            "WHERE h.end_date IS NULL AND h.primary_position=1 "
            "AND c.alive=1 AND o.archetype IN (%s)"
            % ",".join("?" * len(RANK_ARCHETYPES)), RANK_ARCHETYPES).fetchall():
        holders[r["cid"]] = r

    revoked = revoke_on_transfer(con, on)

    for cid, r in holders.items():
        kind, spec = kind_for(r["arch"], on)
        if kind is None:
            continue                      # 还没到设立年代
        years = (on - date.fromisoformat(str(r["since"]))).days / 365.2425
        grade = _grade_for(spec, r["lvl"], years)
        if grade is None:
            continue
        have = con.execute(
            "SELECT id, grade FROM professional_rank "
            "WHERE character_id=? AND end_date IS NULL", (cid,)).fetchone()
        if have and have["grade"] == grade:
            continue
        if have:
            con.execute("UPDATE professional_rank SET end_date=?,exit_reason='晋衔' "
                        "WHERE id=?", (on.isoformat(), have["id"]))
        con.execute(
            "INSERT INTO professional_rank(character_id,kind,grade,start_date) "
            "VALUES(?,?,?,?)", (cid, kind, grade, on.isoformat()))
        granted.append((cid, kind, grade))
    return granted, revoked


# ── 高配 ────────────────────────────────────────────

def grant_high_rank(con, on, rng):
    """给分管日常工作的副职高配。

    他实际承担整个机关的运转，级别上给一格，是对这个安排的承认。
    但不是每个都给——所以"副局长 = 固定副处"这种写法本来就是错的。
    """
    p = templates().get("high_ranked", {}).get("daily_work_probability", 0)
    out = []
    for r in con.execute(
            "SELECT h.id, d.leadership_level AS lvl FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.end_date IS NULL AND s.executive_deputy=1 "
            "AND h.personal_rank IS NULL "
            # 兼任的人级别来自主职，那不是高配
            "AND h.appointment_type='NORMAL'").fetchall():
        if rng.random() >= p:
            continue
        n = LEVEL_ORDER.get(r["lvl"])
        up = next((k for k, v in LEVEL_ORDER.items() if v == (n or 0) + 1), None)
        if not up:
            continue
        con.execute("UPDATE office_holding SET personal_rank=? WHERE id=?",
                    (up, r["id"]))
        out.append((r["id"], up))
    return out


# ── 挂职 ────────────────────────────────────────────

def _cfg():
    return templates().get("secondment", {})


def start(con, on, rng):
    """每年放几个人下去挂职。

    挂职岗位是临时增设的，不占本机关的实际编制，也不进班子排序。
    """
    cfg = _cfg()
    years = cfg.get("years", 2)
    levels = cfg.get("levels", [])
    if not levels:
        return []
    out = []
    for _ in range(cfg.get("per_year", 0)):
        who = con.execute(
            "SELECT h.character_id AS cid, d.leadership_level AS lvl "
            "FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "JOIN character c ON c.id = h.character_id "
            "WHERE h.end_date IS NULL AND h.primary_position=1 "
            "AND c.alive=1 AND c.retired=0 AND o.simulated=1 "
            "AND o.admin_level IN ('MUNICIPAL','PROVINCIAL') "
            "AND d.leadership_level IN (%s) "
            "AND NOT EXISTS (SELECT 1 FROM office_holding h2 "
            "  WHERE h2.character_id = h.character_id AND h2.end_date IS NULL "
            "    AND h2.appointment_type='SECONDMENT') "
            "ORDER BY c.id LIMIT 1 OFFSET ?" % ",".join("?" * len(levels)),
            tuple(levels) + (rng.randrange(0, 12),)).fetchone()
        if who is None:
            continue
        host = con.execute(
            "SELECT o.id, COALESCE(o.short_name,o.name) AS n FROM organization o "
            "WHERE o.active=1 AND o.simulated=1 AND o.admin_level='COUNTY' "
            "AND o.parent_id IS NOT NULL ORDER BY o.id LIMIT 1 OFFSET ?",
            (rng.randrange(0, 10),)).fetchone()
        if host is None:
            continue
        # 挂的一定是副职，而且要是这个单位真有的职务：
        # 财政局不会有"副部长"，也不会让一个挂职干部当一把手。
        pdef = con.execute(
            "SELECT DISTINCT d.id, d.name FROM position_slot s "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE s.organization_id=? AND d.is_leadership=1 "
            "AND d.name LIKE '副%' "
            "ORDER BY (d.leadership_level = ?) DESC, d.protocol_order LIMIT 1",
            (host["id"], who["lvl"])).fetchone()
        if pdef is None:
            continue
        ends = date(on.year + years, on.month, min(on.day, 28))
        slot = con.execute(
            "INSERT INTO position_slot(position_definition_id,organization_id,"
            "valid_from,valid_to,status,temporary) VALUES(?,?,?,?,'VACANT',1)",
            (pdef["id"], host["id"], on.isoformat(), ends.isoformat())).lastrowid
        title = "%s%s" % (host["n"], pdef["name"])
        con.execute(
            "INSERT INTO office_holding(character_id,position_slot_id,start_date,"
            "title_at_time,primary_position,appointment_type) "
            "VALUES(?,?,?,?,0,'SECONDMENT')",
            (who["cid"], slot, on.isoformat(), title))
        con.execute("UPDATE position_slot SET status='OCCUPIED',holder_id=? WHERE id=?",
                    (who["cid"], slot))
        out.append((who["cid"], title, ends))
    return out


def finish(con, on):
    """挂职到期，回原单位。临时岗位随之撤销。"""
    done = []
    for r in con.execute(
            "SELECT h.id, h.character_id AS cid, h.position_slot_id AS slot, "
            " h.title_at_time AS title FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "WHERE h.end_date IS NULL AND h.appointment_type='SECONDMENT' "
            "AND s.valid_to IS NOT NULL AND s.valid_to <= ?",
            (on.isoformat(),)).fetchall():
        con.execute("UPDATE office_holding SET end_date=?,exit_reason='挂职期满' "
                    "WHERE id=?", (on.isoformat(), r["id"]))
        con.execute("UPDATE position_slot SET status='ABOLISHED',holder_id=NULL "
                    "WHERE id=?", (r["slot"],))
        done.append((r["cid"], r["title"]))
    return done
