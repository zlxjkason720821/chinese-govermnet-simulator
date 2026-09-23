"""领导秘书的出路（蓝图十二）。

秘书不是跟着领导走的。惯例是：领导动了，秘书按级别安排出路，
而且怎么安排，取决于领导是怎么走的——

    领导升迁　　下放到地方主政，默认升一级（副科到正科）
    领导退休/去世　平调到地方或者本地行政机关
    领导被查　　同样平调；但如果秘书本人也牵扯进去，
                那就去人大政协，这一辈子到头了

这三条是同一个制度的三面：跟对了人是台阶，跟错了人是坑，
而"对不对"要等领导落幕那天才知道。玩家在选择当谁的秘书时，
看得见的只有这个人现在的位子。
"""
from gongpu.appointment import LEVEL_ORDER, log_event

# 主政岗位：管一个地方的人。秘书外放，放的就是这些位子。
# 不是"随便找个同级空缺"——秘书外放的意义正在于从服务岗转到主政岗。
GOVERNING = {
    "副科级": ["副乡镇长", "副书记"],
    "正科级": ["乡镇长", "书记"],
    "副处级": ["副县长", "常务副县长", "常委"],
    "正处级": ["县长", "书记"],
    "副厅级": ["副市长", "常委"],
    "正厅级": ["市长", "书记"],
    "副部级": ["副省长", "常委"],
    "正部级": ["省长", "书记"],
}

# 牵连之后的去处。人大政协不是惩罚，是"不再安排实职"的制度说法。
RETIREMENT_HOME = ("PEOPLES_CONGRESS", "CPPCC")


def _level_up(level):
    n = LEVEL_ORDER.get(level)
    if n is None:
        return level
    return next((k for k, v in LEVEL_ORDER.items() if v == n + 1), level)


def leader_fate(con, leader_id, on):
    """领导是怎么走的。看人现在的状态，不看当初那一行 exit_reason。"""
    c = con.execute("SELECT alive, retired FROM character WHERE id=?",
                    (leader_id,)).fetchone()
    if c is None:
        return "去世"
    disciplined = con.execute(
        "SELECT 1 FROM conduct_record WHERE character_id=? AND closed_date IS NOT NULL "
        "AND discovered_date IS NOT NULL LIMIT 1", (leader_id,)).fetchone()
    if disciplined:
        return "被查"
    if not c["alive"]:
        return "去世"
    if c["retired"]:
        return "退休"
    return "升迁"


def implicated(con, secretary_id):
    """秘书自己有没有牵扯进去。

    §44 那套潜伏问题本来就在库里躺着——领导出事，查的人第一个看的
    就是身边人。有问题的，这时候就翻出来了。
    """
    return con.execute(
        "SELECT 1 FROM conduct_record WHERE character_id=? LIMIT 1",
        (secretary_id,)).fetchone() is not None


def _find_slot(con, level, names=None, types=None, near_org=None):
    """找一个安排得下的空位。

    绝不能再是一个秘书岗——从秘书到秘书不叫出路，那是原地打转，
    而且新领导一走又要重排一次。
    """
    sql = ("SELECT s.id, o.id AS org, o.admin_level AS al FROM position_slot s "
           "JOIN position_definition d ON d.id = s.position_definition_id "
           "JOIN organization o ON o.id = s.organization_id "
           "WHERE s.status='VACANT' AND d.leadership_level=? AND o.simulated=1 "
           "AND s.serves_slot_id IS NULL AND d.name NOT LIKE '%秘书'")
    args = [level]
    if not types:
        # 人大政协只有受牵连时才去。领导升迁把秘书放进政协，
        # 那不是外放，那是发落。
        sql += " AND o.organization_type NOT IN (%s)" % ",".join(
            "?" * len(RETIREMENT_HOME))
        args += list(RETIREMENT_HOME)
    if names:
        sql += " AND d.name IN (%s)" % ",".join("?" * len(names))
        args += list(names)
    if types:
        sql += " AND o.organization_type IN (%s)" % ",".join("?" * len(types))
        args += list(types)
    rows = con.execute(sql + " ORDER BY s.id", args).fetchall()
    return rows[0]["id"] if rows else None


def outlet(con, secretary_id, cur_level, fate, hurt):
    """这个秘书该去哪儿。返回 (岗位id, 去向说明)。"""
    if hurt:
        slot = _find_slot(con, cur_level, types=RETIREMENT_HOME)
        if slot is None:
            slot = _find_slot(con, cur_level, types=RETIREMENT_HOME[:1])
        if slot:
            return slot, "领导被查，本人受牵连，不再安排实职"
        return None, "领导被查，本人受牵连，暂不安排"
    if fate == "升迁":
        up = _level_up(cur_level)
        slot = _find_slot(con, up, names=GOVERNING.get(up))
        if slot:
            return slot, "领导升迁，下放地方主政，提一级"
        slot = _find_slot(con, up)
        if slot:
            return slot, "领导升迁，提一级，但没有主政的位子"
        slot = _find_slot(con, cur_level, names=GOVERNING.get(cur_level))
        if slot:
            return slot, "领导升迁，可上一级没有空缺，平级放到地方"
        slot = _find_slot(con, cur_level)
        if slot:
            return slot, "领导升迁，可眼下没有上一级的位子，先平级安排"
        return None, "领导升迁了，可一时没有合适的位子"
    # 退休、去世、被查：平调。先找主政岗，找不到就本地机关同级安排。
    slot = _find_slot(con, cur_level, names=GOVERNING.get(cur_level))
    if slot:
        return slot, "领导%s，平调地方" % fate
    slot = _find_slot(con, cur_level)
    if slot:
        return slot, "领导%s，平调本地机关" % fate
    return None, "一时没有合适的位子"


def settle(con, on):
    """把所有领导已经离任的秘书安排掉。每月初跑一次。"""
    done = []
    for r in con.execute(
            "SELECT s.id AS slot, s.holder_id AS who, h.id AS hid, "
            " h.start_date AS since, d.name AS post, d.leadership_level AS lvl, "
            " ls.id AS lslot, ls.status AS lstatus, lh.start_date AS lsince "
            "FROM position_slot s "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN office_holding h ON h.position_slot_id = s.id AND h.end_date IS NULL "
            "JOIN position_slot ls ON ls.id = s.serves_slot_id "
            "LEFT JOIN office_holding lh ON lh.position_slot_id = ls.id "
            "  AND lh.end_date IS NULL "
            "WHERE s.serves_slot_id IS NOT NULL AND s.status='OCCUPIED'").fetchall():
        if r["lstatus"] == "OCCUPIED" and (r["lsince"] or "") <= (r["since"] or ""):
            continue                       # 领导还是那个人
        left = con.execute(
            "SELECT character_id FROM office_holding WHERE position_slot_id=? "
            "AND end_date IS NOT NULL ORDER BY end_date DESC LIMIT 1",
            (r["lslot"],)).fetchone()
        if left is None:
            continue
        fate = leader_fate(con, left["character_id"], on)
        hurt = fate == "被查" and implicated(con, r["who"])
        dest, why = outlet(con, r["who"], r["lvl"], fate, hurt)
        _move(con, r, dest, on)
        log_event(con, on, "secretary_settled",
                  {"post": r["post"], "fate": fate, "why": why,
                   "hurt": hurt, "to": _title(con, dest)},
                  actors=[r["who"]])
        done.append({"who": r["who"], "fate": fate, "why": why, "hurt": hurt,
                     "to": _title(con, dest)})
    return done


def _title(con, slot_id):
    if slot_id is None:
        return None
    r = con.execute(
        "SELECT COALESCE(o.short_name,o.name) || d.name FROM position_slot s "
        "JOIN organization o ON o.id = s.organization_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.id=?", (slot_id,)).fetchone()
    return r[0] if r else None


def _move(con, r, dest, on):
    con.execute("UPDATE office_holding SET end_date=?,exit_reason='SECRETARY_OUTLET' "
                "WHERE id=?", (on.isoformat(), r["hid"]))
    con.execute("UPDATE position_slot SET status='VACANT',holder_id=NULL WHERE id=?",
                (r["slot"],))
    if dest is None:
        return
    con.execute(
        "INSERT INTO office_holding(character_id,position_slot_id,start_date,"
        "title_at_time,primary_position) VALUES(?,?,?,?,1)",
        (r["who"], dest, on.isoformat(), _title(con, dest)))
    con.execute("UPDATE position_slot SET status='OCCUPIED',holder_id=? WHERE id=?",
                (r["who"], dest))
