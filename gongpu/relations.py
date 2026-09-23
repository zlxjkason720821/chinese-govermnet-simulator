"""人际关系（技术文档 §40）。

文档划的线：用图结构记同事、上下级、党校同学、大学同学、老乡、mentor、
合作关系，**不要直接写"派系"**。

这里还守另一条更要紧的线——§69 关系好不能绕过程序。
所以关系影响的是"事办不办得动"：认识对口部门的人，联系一次顶两次；
托得住的领导给的指点更实在。关系绝不进任免的候选池条件，
一条都不进。想升迁，还是得年龄、资历、基层经历、现职任职年限样样够。

关系会淡。不联系就掉，这是"踩平衡木"的那个平衡：
你的时间就那么多，维护了这头就顾不上那头。
"""
from datetime import date

TYPES = ("同事", "上下级", "党校同学", "大学同学", "老乡", "mentor", "合作关系")

FAMILIARITY_CAP = 100
TRUST_CAP = 100
DECAY_PER_YEAR = 8          # 一年不走动，熟悉程度掉这么多
TRUST_DECAY_PER_YEAR = 5
SMOOTH_TRUST = 45           # 工作信任到这个数，办事才真的顺


def _pair(a, b):
    return (a, b) if a < b else (b, a)


def get(con, a, b):
    lo, hi = _pair(a, b)
    return con.execute("SELECT * FROM relationship WHERE character_a=? AND character_b=?",
                       (lo, hi)).fetchone()


def ensure(con, a, b, kind, familiarity=0, trust=0, on=None):
    if a == b:
        return None
    lo, hi = _pair(a, b)
    con.execute(
        "INSERT INTO relationship(character_a,character_b,type,familiarity,working_trust,"
        "last_contact) VALUES(?,?,?,?,?,?) ON CONFLICT(character_a,character_b) DO NOTHING",
        (lo, hi, kind, familiarity, trust, on.isoformat() if on else None))
    return get(con, a, b)


def touch(con, a, b, on, familiarity=0, trust=0, kind="合作关系"):
    """打一次交道。没有这层关系就建立，有就加深。"""
    ensure(con, a, b, kind, on=on)
    lo, hi = _pair(a, b)
    con.execute(
        "UPDATE relationship SET "
        "familiarity = max(0, min(familiarity + ?, ?)), "
        "working_trust = max(0, min(working_trust + ?, ?)), "
        "last_contact = ? WHERE character_a=? AND character_b=?",
        (familiarity, FAMILIARITY_CAP, trust, TRUST_CAP, on.isoformat(), lo, hi))
    return get(con, a, b)


def for_character(con, cid, limit=60):
    """某人的关系网，按工作信任排序。"""
    rows = con.execute(
        "SELECT r.*, "
        " CASE WHEN r.character_a=:me THEN r.character_b ELSE r.character_a END AS other "
        "FROM relationship r WHERE r.character_a=:me OR r.character_b=:me "
        "ORDER BY r.working_trust DESC, r.familiarity DESC LIMIT :n",
        {"me": cid, "n": limit}).fetchall()
    out = []
    for r in rows:
        c = con.execute(
            "SELECT c.id, c.name, c.gender, c.birth_date, c.education_level, "
            "h.title_at_time FROM character c "
            "LEFT JOIN office_holding h ON h.character_id = c.id AND h.end_date IS NULL "
            "WHERE c.id = ?", (r["other"],)).fetchone()
        if c is None:
            continue
        out.append(dict(c, type=r["type"], familiarity=r["familiarity"],
                        working_trust=r["working_trust"], last_contact=r["last_contact"]))
    return out


def contacts(con, cid):
    """你认识的人，带明确的级别和职务。

    关系不该只是关系页上的一行数字。认识了人，行动里就要找得到他——
    这是"人脉"这两个字唯一的意思。级别和职务必须写清楚：
    托谁办事，先得知道他说了算不算。
    """
    out = []
    for r in con.execute(
            "SELECT r.type, r.familiarity, r.working_trust, r.last_contact, "
            " CASE WHEN r.character_a=:me THEN r.character_b ELSE r.character_a END AS other "
            "FROM relationship r WHERE r.character_a=:me OR r.character_b=:me",
            {"me": cid}).fetchall():
        c = con.execute(
            "SELECT c.id, c.name, c.alive, c.retired, "
            " d.name AS post, d.leadership_level AS rank, d.is_leadership, "
            " s.organization_id AS org_id, COALESCE(o.short_name, o.name) AS org "
            "FROM character c "
            "LEFT JOIN office_holding h ON h.character_id = c.id "
            "  AND h.end_date IS NULL AND h.primary_position = 1 "
            "LEFT JOIN position_slot s ON s.id = h.position_slot_id "
            "LEFT JOIN position_definition d ON d.id = s.position_definition_id "
            "LEFT JOIN organization o ON o.id = s.organization_id "
            "WHERE c.id = ?", (r["other"],)).fetchone()
        if c is None or not c["alive"]:
            continue
        out.append({
            "id": c["id"], "name": c["name"], "post": c["post"],
            "rank": c["rank"] or ("退休" if c["retired"] else "无职务"),
            "org": c["org"], "org_id": c["org_id"], "retired": bool(c["retired"]),
            "is_leadership": bool(c["is_leadership"]),
            "type": r["type"], "familiarity": r["familiarity"],
            "trust": r["working_trust"], "last_contact": r["last_contact"]})
    out.sort(key=lambda x: (-x["trust"], -x["familiarity"]))
    return out


# 托人是在动用交情，不是免费的。每托一次就支掉一点，
# 支完了就得重新走动。这才叫"在人们之间踩平衡木"。
FAVOR_TRUST_COST = 6
FAVOR_WASTED_COST = 10      # 托错了人，交情折得更多
VISIT_FAMILIARITY = 8       # 一次走动，正好抵一年的自然衰减
VISIT_TRUST = 4


def spend(con, a, b, on, trust_cost):
    """动用一次交情。"""
    lo, hi = _pair(a, b)
    con.execute(
        "UPDATE relationship SET working_trust = max(working_trust - ?, 0), "
        "last_contact = ? WHERE character_a=? AND character_b=?",
        (trust_cost, on.isoformat(), lo, hi))


def trust_with_leader_of(con, cid, org_id):
    """和某单位主要负责人之间的工作信任。联系办事顺不顺，看这个。"""
    leader = con.execute(
        "SELECT c.id FROM office_holding h JOIN character c ON c.id = h.character_id "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE h.end_date IS NULL AND s.organization_id = ? AND d.is_leadership = 1 "
        "ORDER BY d.protocol_order LIMIT 1", (org_id,)).fetchone()
    if leader is None:
        return 0, None
    r = get(con, cid, leader["id"])
    return (r["working_trust"] if r else 0), leader["id"]


def seed_colleagues(con, on):
    """开局建立同事关系。同一单位的人天然认识，但认识不等于托得住。"""
    for (org,) in con.execute(
            "SELECT DISTINCT organization_id FROM position_slot WHERE status='OCCUPIED'"):
        ids = [r[0] for r in con.execute(
            "SELECT h.character_id FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "WHERE h.end_date IS NULL AND s.organization_id=? ORDER BY h.character_id",
            (org,))]
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                ensure(con, a, b, "同事", familiarity=25, trust=15, on=on)


def seed_classmates(con, on, rng):
    """同年代、同学历的人里抽一部分互为大学同学。

    这是履历派生的，不是随便连线：1962 年生的本科生和 1985 年生的本科生
    不可能是大学同学。
    """
    rows = con.execute(
        "SELECT id, birth_date, education_level FROM character "
        "WHERE education_level IN ('大专','本科','硕士','博士') ORDER BY id").fetchall()
    buckets = {}
    for r in rows:
        key = (str(r["birth_date"])[:3], r["education_level"])   # 同一个十年、同一学历
        buckets.setdefault(key, []).append(r["id"])
    n = 0
    for ids in buckets.values():
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if rng["world"].random() < 0.06:
                    ensure(con, a, b, "大学同学", familiarity=35, trust=20, on=on)
                    n += 1
    return n


COLLEAGUE_FLOOR = 20        # 同一个单位天天照面，熟悉程度掉不到这个数以下


def decay(con, on):
    """一年没走动，关系就淡了。维护是要花时间的，时间正是稀缺的那样东西。

    但现同事有个底线：一个办公室坐着，不会因为"没专门走动"就形同陌路。
    真正会归零的是调走了的人、别的单位的人——那才是需要花力气维持的。
    """
    cutoff = date(on.year - 1, on.month, min(on.day, 28)).isoformat()
    # 当前仍在同一单位的两个人
    same_unit = """
        SELECT 1 FROM office_holding ha
        JOIN position_slot sa ON sa.id = ha.position_slot_id
        JOIN office_holding hb ON hb.end_date IS NULL
        JOIN position_slot sb ON sb.id = hb.position_slot_id
        WHERE ha.end_date IS NULL
          AND ha.character_id = relationship.character_a
          AND hb.character_id = relationship.character_b
          AND sa.organization_id = sb.organization_id
    """
    con.execute(
        "UPDATE relationship SET "
        " familiarity = CASE WHEN EXISTS (%s) "
        "   THEN max(?, familiarity - ?) ELSE max(0, familiarity - ?) END, "
        " working_trust = max(0, working_trust - ?) "
        "WHERE last_contact IS NULL OR last_contact < ?" % same_unit,
        (COLLEAGUE_FLOOR, DECAY_PER_YEAR, DECAY_PER_YEAR,
         TRUST_DECAY_PER_YEAR, cutoff))
