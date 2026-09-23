"""职级/非领导职务晋升（时间轴文档 §32-§34，技术文档 §22）。

时间轴文档 §33 的红线：职级不是领导职务。
一级调研员 ≠ 处长。职级晋升不改变职位、不改变指挥关系。
所以这里只动 rank_holding，绝不碰 position_slot 和 office_holding。

文档 §34：职级职数是真实资源，职级不能无限生成。所以有职数上限。

可信度标签沿用时间轴文档 §0 的体系。
"""
from datetime import date

from gongpu.appointment import log_event, service_years

# VERIFIED（时间轴 §32）：2019 综合管理类职级，自下而上。注意没有"三级科员"。
LADDER_2019 = ["二级科员", "一级科员", "四级主任科员", "三级主任科员",
               "二级主任科员", "一级主任科员", "四级调研员", "三级调研员",
               "二级调研员", "一级调研员", "二级巡视员", "一级巡视员"]

# VERIFIED（时间轴 §9）：1993 暂行条例非领导职务序列
LADDER_1993 = ["办事员", "科员", "副主任科员", "主任科员", "副调研员", "调研员"]

# DERIVED：时间轴文档只写"受任职年限约束"，没有给具体年数。
# 这里取各级 2—5 年的递增年限，越往上越久。确认到确切条文后改这一张表即可。
MIN_YEARS_IN_RANK = 2
EXTRA_YEARS_PER_STEP = 0.5

# GAME_ABSTRACTION：职数按编制比例封顶（§34 职级不能无限生成）。
# 一个县的机关不可能人人都是一级调研员。
QUOTA_RATIO = {
    "四级主任科员": 0.25, "三级主任科员": 0.15, "二级主任科员": 0.08,
    "一级主任科员": 0.05, "四级调研员": 0.03, "三级调研员": 0.02,
    "二级调研员": 0.01, "一级调研员": 0.01, "二级巡视员": 0.0,
    "一级巡视员": 0.0,
    "副主任科员": 0.30, "主任科员": 0.15, "副调研员": 0.04, "调研员": 0.02,
}


def ladder_for(rules):
    return LADDER_2019 if rules.rank_parallel else LADDER_1993


def _quota(con, rank_name):
    """该职级在本世界的职数上限。没有列出的低职级不设限。"""
    ratio = QUOTA_RATIO.get(rank_name)
    if ratio is None:
        return None
    total = con.execute("SELECT count(*) FROM position_slot WHERE valid_to IS NULL").fetchone()[0]
    return max(int(total * ratio), 1 if ratio > 0 else 0)


def _held_count(con, rank_name):
    return con.execute(
        "SELECT count(*) FROM rank_holding r JOIN character c ON c.id = r.character_id "
        "WHERE r.end_date IS NULL AND r.rank_name = ? AND c.alive = 1 AND c.retired = 0",
        (rank_name,)).fetchone()[0]


def promote_ranks(con, on, rng, rules):
    """每年一次职级晋升。按资历排序择优，受职数限制。

    晋升的是职级，不是职位：晋升后这个人还在原来的岗位上做原来的事。
    """
    ladder = ladder_for(rules)
    years = service_years(con, on)
    promoted = []
    # 从高到低处理，腾出的低职级职数当年即可被下面的人用上
    for i in range(len(ladder) - 1, 0, -1):
        target, below = ladder[i], ladder[i - 1]
        quota = _quota(con, target)
        if quota is not None:
            room = quota - _held_count(con, target)
            if room <= 0:
                continue
        else:
            room = None

        need = MIN_YEARS_IN_RANK + EXTRA_YEARS_PER_STEP * (i - 1)
        candidates = []
        for r in con.execute(
                "SELECT r.id, r.character_id, r.start_date FROM rank_holding r "
                "JOIN character c ON c.id = r.character_id "
                "WHERE r.end_date IS NULL AND r.rank_name = ? AND c.alive = 1 "
                "AND c.retired = 0 AND c.discipline_status = 'CLEAR' ORDER BY r.id",
                (below,)).fetchall():
            in_rank = (on - date.fromisoformat(r["start_date"])).days / 365
            if in_rank >= need:
                candidates.append((years.get(r["character_id"], 0), r["character_id"]))
        if not candidates:
            continue
        # 资历深的优先，同资历按 id，保证可复现
        candidates.sort(key=lambda t: (-t[0], t[1]))
        for _, cid in candidates[:room] if room is not None else candidates:
            con.execute("UPDATE rank_holding SET end_date=? WHERE character_id=? "
                        "AND end_date IS NULL", (on.isoformat(), cid))
            con.execute(
                "INSERT INTO rank_holding(character_id,rank_name,rank_system,start_date,"
                "source) VALUES(?,?,?,?,'PROMOTION')",
                (cid, target, "2019" if rules.rank_parallel else "1993", on.isoformat()))
            promoted.append((cid, target))
    if promoted:
        log_event(con, on, "rank_promotion", {"count": len(promoted)}, visibility="DEV")
    return promoted
