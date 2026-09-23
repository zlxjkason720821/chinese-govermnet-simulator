"""纪律（技术文档 §43、§44）。

§43 取消"廉政值"。这里没有任何一个数字叫清廉度。记的是具体行为：
谁、什么时候、干了什么、有没有留下痕迹。

§44 是这个系统真正的设计：**违规与发现分离**。

    1994 发生违规
    1994 没人发现
    2003 审计出现线索
    2004 核查

所以行为的后果可以潜伏十年。档案上干干净净的人，不代表他干净，
只代表还没查到他。而查出来的时候，他可能已经是县长了。

发现的渠道是真实存在的那几条：审计、巡视、信访、换届考察。
每条渠道能查到什么，取决于当年留下的痕迹（project_decision）和线索强度。
"""
import json
from datetime import date, timedelta

from gongpu.appointment import log_event

BEHAVIORS = [
    ("工程未按规定招标", "一般"),
    ("违规确定施工单位", "严重"),
    ("挪用专项资金", "严重"),
    ("虚报工程量套取资金", "严重"),
    ("接受管理服务对象宴请", "轻微"),
    ("违规收受礼品礼金", "一般"),
    ("验收把关不严", "一般"),
    ("决策未经集体研究", "一般"),
]

# 查处渠道与它们的命中率。
#
# 这些数字压得很低是有意的：§44 举的例子是 1994 年的事 2003 年才出线索。
# 如果一两年就查出来，"行为后果潜伏十年"就不存在了，
# 而那恰恰是这个系统唯一值得做的地方。
CHANNELS = {
    "审计": 0.11,       # 盯资金和工程，年年有
    "巡视": 0.14,       # 盯领导干部，几年一轮
    "信访": 0.05,       # 群众举报，不挑日子
    "换届考察": 0.09,   # 换届前集中核查
}

MIN_LATENT_YEARS = 1.0      # 当年当期查不出来

RESULTS = {
    "轻微": ["谈话提醒", "批评教育"],
    "一般": ["警告", "严重警告"],
    "严重": ["撤销党内职务", "留党察看", "开除党籍"],
}
# 处分后果：撤职一级的要真的掉下去
REMOVAL = ("撤销党内职务", "留党察看", "开除党籍")


def maybe_plant(con, project_id, on, rng):
    """项目验收时，按风险埋下可能的问题。此刻没有任何人知道。"""
    p = con.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
    if p is None or rng["governance"].random() * 100 >= p["risk"]:
        return None
    # 问题记在经手人头上——谁签的字，谁担着
    handlers = con.execute(
        "SELECT character_id, role FROM project_decision WHERE project_id=? "
        "AND role IN ('签批','实施','批准') ORDER BY id", (project_id,)).fetchall()
    if not handlers:
        return None
    h = rng["governance"].choice(handlers)
    behavior, severity = rng["governance"].choice(BEHAVIORS)
    # 线索强度：留痕越多越查得出来，体量越大越藏不住
    evidence = min(20 + p["scale"] * 15 + int(rng["governance"].random() * 30), 95)
    cur = con.execute(
        "INSERT INTO conduct_record(character_id,behavior,severity,behavior_date,"
        "project_id,evidence,status) VALUES(?,?,?,?,?,?,'LATENT')",
        (h["character_id"], behavior, severity, on.isoformat(), project_id, evidence))
    # 注意：这里不写 world_event。事情发生了，但没有人知道。
    return cur.lastrowid


def sweep(con, on, rng, rules, channel):
    """某条渠道查一遍。查得到什么取决于线索强度和年头。

    2018 监察体制改革之后，覆盖面扩大（§46 这是制度事件，不是随机）。
    """
    base = CHANNELS[channel]
    if rules.supervision_commission:
        base *= 1.4
    # 巡视盯的是在任领导干部，不是翻退休老同志的旧账。
    # 没有这一条，因为潜伏期长，查出来的会清一色是已经退休的人，
    # "查处时他已经是县长了"这种情形根本不会发生。
    in_office = set()
    if channel in ("巡视", "换届考察"):
        in_office = {r[0] for r in con.execute(
            "SELECT h.character_id FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.end_date IS NULL AND d.is_leadership = 1")}
    found = []
    for r in con.execute(
            "SELECT * FROM conduct_record WHERE status='LATENT' ORDER BY id").fetchall():
        years = (on - date.fromisoformat(r["behavior_date"])).days / 365
        if years < MIN_LATENT_YEARS:
            continue          # 刚发生的事，当期查不出来
        # 时间越久，痕迹越淡；但线索强的事，放多久都在账上
        decay = max(0.30, 1.0 - years * 0.035)
        # 线索强度是主导因素：大工程、留痕多的藏不住，几年就出事；
        # 线索弱的可以压二三十年。这样两种情形都会发生。
        weight = (r["evidence"] / 100.0) ** 0.6
        chance = base * weight * decay
        if r["character_id"] in in_office:
            chance *= 3.0
        if rng["governance"].random() < chance:
            con.execute(
                "UPDATE conduct_record SET status='CLUE',discovered_date=?,"
                "discovered_by=? WHERE id=?", (on.isoformat(), channel, r["id"]))
            log_event(con, on, "discipline_clue",
                      {"record": r["id"], "channel": channel,
                       "behavior": r["behavior"],
                       "behavior_date": r["behavior_date"],
                       "latent_years": round(years, 1)},
                      actors=[r["character_id"]])
            found.append(r["id"])
    return found


def open_review(con, on, rng, rules):
    """有线索的转入核查。这一刻起，这个人的档案就不干净了——
    任职资格里的"纪律情况"会立刻把他挡在候选池外（§35）。"""
    opened = []
    for r in con.execute(
            "SELECT * FROM conduct_record WHERE status='CLUE' ORDER BY id").fetchall():
        if (on - date.fromisoformat(r["discovered_date"])).days < 60:
            continue
        con.execute("UPDATE conduct_record SET status='UNDER_REVIEW' WHERE id=?", (r["id"],))
        con.execute("UPDATE character SET discipline_status='UNDER_REVIEW' WHERE id=?",
                    (r["character_id"],))
        log_event(con, on, "discipline_review",
                  {"record": r["id"], "behavior": r["behavior"]},
                  actors=[r["character_id"]])
        opened.append(r["id"])
    return opened


def close_review(con, on, rng, rules):
    """核查了结。轻的谈话提醒，重的撤职——撤职是真的把人从岗位上拿下来。"""
    closed = []
    for r in con.execute(
            "SELECT * FROM conduct_record WHERE status='UNDER_REVIEW' ORDER BY id").fetchall():
        if (on - date.fromisoformat(r["discovered_date"])).days < 240:
            continue
        result = rng["governance"].choice(RESULTS[r["severity"]])
        con.execute("UPDATE conduct_record SET status='CLOSED',result=?,closed_date=? "
                    "WHERE id=?", (result, on.isoformat(), r["id"]))
        cid = r["character_id"]
        if result in REMOVAL:
            from gongpu import npc
            slots = npc.vacate(con, cid, on, "DISCIPLINE")
            npc._close_central_status(con, cid, on)
            con.execute("UPDATE character SET discipline_status='PUNISHED' WHERE id=?", (cid,))
            log_event(con, on, "discipline_removal",
                      {"record": r["id"], "result": result, "slots": slots,
                       "behavior": r["behavior"], "behavior_date": r["behavior_date"]},
                      actors=[cid])
        else:
            # 没到撤职的，处分记在档案上，但人还在岗位上
            con.execute("UPDATE character SET discipline_status='CLEAR' WHERE id=?", (cid,))
            log_event(con, on, "discipline_result",
                      {"record": r["id"], "result": result, "behavior": r["behavior"]},
                      actors=[cid])
        closed.append(r["id"])
    return closed


def has_pending(con, cid):
    """这个人有没有尚未了结的问题线索。

    只看 character.discipline_status 不够：从出线索到正式立案之间隔着一段，
    这段时间里状态还是 CLEAR，到龄的人正好从这个缝里退休走掉。
    """
    return con.execute(
        "SELECT count(*) FROM conduct_record WHERE character_id=? "
        "AND status IN ('CLUE','UNDER_REVIEW')", (cid,)).fetchone()[0] > 0


def records_for(con, cid):
    """某人的纪律记录。还没被发现的，这里也看不到——那正是重点。"""
    return [dict(r) for r in con.execute(
        "SELECT behavior, severity, behavior_date, discovered_date, discovered_by, "
        "status, closed_date, result FROM conduct_record "
        "WHERE character_id=? AND status != 'LATENT' "
        "ORDER BY behavior_date", (cid,))]


def pending_count(con):
    return con.execute(
        "SELECT sum(status='LATENT'), sum(status!='LATENT') FROM conduct_record").fetchone()
