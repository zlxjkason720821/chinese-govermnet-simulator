"""玩家动作与权限（技术文档 §53-§57），以及应用层（§5、§62）。"""
from datetime import date

import pytest

from gongpu import actions
from gongpu.actions import ActionIntent, AuthorityError
from gongpu.game import Game


@pytest.fixture
def g():
    return Game.new("林致远", seed="act")


def test_科员不能决定干部任免(g):
    """§57 不弹"动作不可用"，而是说明这不属于你的权限范围。"""
    with pytest.raises(AuthorityError, match="权限范围"):
        g.act("任免", target_id=1)


def test_不可用动作仍然列出并说明原因(g):
    menu = {a["key"]: a for a in g.actions()}
    assert menu["任免"]["allowed"] is False
    assert "权限范围" in menu["任免"]["reason"]
    assert menu["汇报"]["allowed"] is True
    assert menu["汇报"]["reason"] is None


def test_权限随职务变化(g):
    """升到正科级才谈得上召集会议。"""
    assert actions.player_level(g.con, g.player_id) <= 1
    slot = g.con.execute(
        "SELECT s.id FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE d.leadership_level='正科级' LIMIT 1").fetchone()["id"]
    g.con.execute("UPDATE office_holding SET end_date='1986-08-01' WHERE character_id=?",
                  (g.player_id,))
    g.con.execute("INSERT INTO office_holding(character_id,position_slot_id,start_date,"
                  "title_at_time) VALUES(?,?,'1986-08-01','局长')", (g.player_id, slot))
    assert actions.player_level(g.con, g.player_id) == 3
    assert {a["key"] for a in g.actions() if a["allowed"]} >= {"召开会议"}


def test_动作留痕(g):
    """§42 决策留痕：以后审计、巡视、调查可以追溯。"""
    org = g.targets("走访")[0]
    item = g.tasks()[0]
    started = g.clock.date
    g.act("走访", target_id=org["id"], work_item_id=item["id"])
    row = g.con.execute("SELECT * FROM action_log WHERE character_id=?",
                        (g.player_id,)).fetchone()
    assert row["action_type"] == "走访"
    assert row["target"] == org["name"]
    assert row["work_item_id"] == item["id"]
    assert row["date"] == started.isoformat(), "留痕记的应当是动手那天"


def test_对象必须是库里真实存在且够得着的(g):
    """§54 的答案：不解析自由文本，只在真实实体里选。"""
    with pytest.raises(AuthorityError, match="需要指明对象"):
        g.act("走访")
    with pytest.raises(AuthorityError, match="范围内"):
        g.act("走访", target_id=999999)


def test_数据库按事项类型判定动作有没有用(g):
    """起草材料靠下乡走访办不成——这不是手感，是事项类型决定的。"""
    from gongpu import tasks
    con, on = g.con, g.clock.date
    con.execute("UPDATE work_item SET state='DONE' WHERE assignee_id=?", (g.player_id,))
    iid = tasks.assign(con, g.player_id, on, g.rng)
    con.execute("UPDATE work_item SET kind='材料',subject='测试材料' WHERE id=?", (iid,))
    assert tasks.judge(con, iid, "走访", on)[0] == "走过场"
    # 什么都没做就去汇报，事情不会因为汇报而办成
    assert tasks.judge(con, iid, "汇报", on)[0] == "推进"
    con.execute("UPDATE work_item SET progress=1 WHERE id=?", (iid,))
    assert tasks.judge(con, iid, "汇报", on)[0] == "办结"


def test_逾期是客观事实不是裁量(g):
    from gongpu import tasks
    item = g.tasks()[0]
    g.con.execute("UPDATE work_item SET due_date='1986-07-01' WHERE id=?", (item["id"],))
    assert tasks.judge(g.con, item["id"], "汇报", g.clock.date)[0] == "逾期"
    assert tasks.sweep_overdue(g.con, g.clock.date) == [item["id"]]
    assert g.con.execute("SELECT state FROM work_item WHERE id=?",
                         (item["id"],)).fetchone()[0] == "OVERDUE"


def test_组织视野有上限且不能靠空转刷满(g):
    """§36 这是"组织掌握到什么程度"，不是可以无限刷的好感度。"""
    for _ in range(60):
        # 对象每次重取：六十天里领导可能已经换人，缓存一个 id 会打到空处
        g.act("汇报", target_id=g.targets("汇报")[0]["id"])   # 不挂事项，纯空转
    assert g.profile()["组织视野"] == "尚未进入视野", "空转也能涨视野，等于回到了好感度"
    for _ in range(40):
        g.advance(20)
        allowed = {a["key"] for a in g.actions() if a["allowed"]}
        for t in g.tasks():
            for a in t["useful_actions"]:
                if a not in allowed:          # 科员召集不了会议，那就不是他的办法
                    continue
                tg = g.targets(a)
                g.act(a, target_id=tg[0]["id"] if tg else None, work_item_id=t["id"])
                break
    from gongpu.game import VISIBILITY_LABEL
    assert g.profile()["组织视野"] in VISIBILITY_LABEL


def test_未知动作被拒绝(g):
    with pytest.raises(ValueError):
        ActionIntent("篡位")


def test_年度考核优秀有比例限制(g):
    g.advance(400)
    year = g.clock.date.year - 1
    rows = g.con.execute("SELECT result, count(*) FROM assessment WHERE year=? "
                         "GROUP BY result", (year,)).fetchall()
    assert rows, "跨过年末应当产生年度考核"
    total = sum(r[1] for r in rows)
    excellent = dict(rows).get("优秀", 0)
    assert excellent / total <= 0.2, "优秀比例失控，考核就不是稀缺资源了"


def test_玩家不能绕过任用程序自己升职(g):
    """§69 关系好不能绕过程序。玩家没有任何直通车。"""
    before = g.post_name()
    for _ in range(50):
        g.act("汇报", target_id=g.targets("汇报")[0]["id"])
    assert g.post_name() == before, "刷动作直接改变了玩家职务"


def test_玩家和NPC走同一套任用规则(g):
    """玩家不是特例：进候选池要和所有人过同样的条件。"""
    from gongpu.appointment import eligible_candidates
    from gongpu import rules
    slot = g.con.execute(
        "SELECT s.id FROM position_slot s "
        "JOIN position_definition d ON d.id=s.position_definition_id "
        "WHERE d.leadership_level='副处级' LIMIT 1").fetchone()["id"]
    pool = eligible_candidates(g.con, slot, g.clock.date, rules.resolve(g.clock.date))
    assert g.player_id not in {c["id"] for c in pool}, \
        "刚报到的选调生不该出现在副处级岗位候选池里"


def test_存档读档是同一个世界(tmp_path, monkeypatch, g):
    """§62 + §12：存档必须带 RNG state，否则读档后世界会分叉。"""
    from gongpu import game as game_mod
    monkeypatch.setattr(game_mod, "SAVE_ROOT", tmp_path)
    g.advance(200)
    g.save("s1")
    a = Game.load("s1")
    b = Game.load("s1")
    a.advance(365)
    b.advance(365)
    ea = [tuple(r) for r in a.con.execute("SELECT date,event_type,data FROM world_event "
                                          "ORDER BY id")]
    eb = [tuple(r) for r in b.con.execute("SELECT date,event_type,data FROM world_event "
                                          "ORDER BY id")]
    assert ea == eb
    assert a.clock.date == b.clock.date


def test_读档保留履历与日期(tmp_path, monkeypatch, g):
    from gongpu import game as game_mod
    monkeypatch.setattr(game_mod, "SAVE_ROOT", tmp_path)
    g.advance(100)
    before_date, before_resume = g.clock.date, g.resume()
    g.save("s2")
    loaded = Game.load("s2")
    assert loaded.clock.date == before_date
    assert loaded.resume() == before_resume
    assert loaded.player_name() == g.player_name()


def test_领导职务与职级分开显示(g):
    """§33 绝不能合成一个"官阶"。"""
    p = g.profile()
    assert "领导职务" in p and "公务员职级" in p
    assert "官阶" not in p


def test_不同种子开局不同():
    """默认种子固定会让每局开头一模一样。种子必须真的起作用。"""
    firsts = set()
    for seed in ("s1", "s2", "s3", "s4", "s5", "s6"):
        g = Game.new("林致远", seed=seed)
        firsts.add(g.tasks()[0]["subject"])
    assert len(firsts) >= 4, f"六个种子只开出 {len(firsts)} 种开局：{firsts}"


def test_同种子开局完全一致():
    """§70 这是特性，不是 bug：填同一个种子必须得到同一个世界。"""
    a = Game.new("林致远", seed="same-seed")
    b = Game.new("林致远", seed="same-seed")
    assert a.tasks() == b.tasks()
    assert [r["title_at_time"] for r in a.leadership()] == \
           [r["title_at_time"] for r in b.leadership()]


def test_同一个人不会连着接到同一件事(g):
    """刚办完危房改造，转头又来一件危房改造，读起来就假了。"""
    from gongpu import tasks
    seen = []
    for _ in range(12):
        for t in g.tasks():
            seen.append(t["subject"])
            g.con.execute("UPDATE work_item SET state='DONE',resolved_date=?,outcome='办结' "
                          "WHERE id=?", (g.clock.date.isoformat(), t["id"]))
        tasks.assign(g.con, g.player_id, g.clock.date, g.rng)
    assert all(a != b for a, b in zip(seen, seen[1:])), f"出现了连续重复：{seen}"


def test_闰年派活不崩():
    """on.replace(year=...) 在 2 月 29 日会抛 ValueError。"""
    from datetime import date
    from gongpu import tasks
    g = Game.new("林致远", seed="leap")
    assert tasks.assign(g.con, g.player_id, date(1988, 2, 29), g.rng) > 0


def test_汇报不是万能解法(g):
    """什么实事都没做就去汇报，任何一类事项都不该被汇报直接办成。"""
    from gongpu import tasks
    con, on = g.con, g.clock.date
    for kind in tasks.kinds():
        con.execute("UPDATE work_item SET state='DONE' WHERE assignee_id=?", (g.player_id,))
        iid = tasks.assign(con, g.player_id, on, g.rng)
        con.execute("UPDATE work_item SET kind=?,progress=0 WHERE id=?", (kind, iid))
        assert tasks.judge(con, iid, "汇报", on)[0] != "办结", \
            f"{kind} 可以靠一句汇报直接办结"


def test_办事要花时间(g):
    """同时来两件事必须取舍——前提是办事有时间代价。"""
    before = g.clock.date
    org = g.targets("走访")[0]["id"]
    g.act("走访", target_id=org, work_item_id=g.tasks()[0]["id"])
    assert (g.clock.date - before).days == 3, "下乡走访应当占掉几天"
    d2 = g.clock.date
    g.act("调阅")
    assert (g.clock.date - d2).days == 2


def test_时限不会等你(g):
    """办甲事的时候，乙事的时限照样在走。"""
    from gongpu import tasks
    tasks.assign(g.con, g.player_id, g.clock.date, g.rng)
    g.con.commit()
    items = g.tasks()
    assert len(items) >= 2
    other = items[1]
    before_left = other["days_left"]
    g.act("走访", target_id=g.targets("走访")[0]["id"], work_item_id=items[0]["id"])
    after = {t["id"]: t for t in g.tasks()}
    if other["id"] in after:
        assert after[other["id"]]["days_left"] < before_left


def test_关系不进任用条件(g):
    """§69 关系好不能绕过程序。把信任拉满也不能出现在任职条件里。"""
    from gongpu import relations
    from gongpu.appointment import explain_for
    from gongpu import rules as R
    boss = g.targets("汇报")[0]["id"]
    for _ in range(40):
        relations.touch(g.con, g.player_id, boss, g.clock.date, familiarity=9, trust=9)
    slot = g.con.execute(
        "SELECT s.id FROM position_slot s "
        "JOIN position_definition d ON d.id=s.position_definition_id "
        "WHERE d.leadership_level='副科级' LIMIT 1").fetchone()["id"]
    conds = explain_for(g.con, slot, g.player_id, g.clock.date,
                        R.resolve(g.clock.date))
    names = {c["条件"] for c in conds}
    assert not (names & {"关系", "交情", "信任", "熟悉程度"}), names


def test_关系会变淡(g):
    """不走动就掉。维护关系要花时间，而时间正是稀缺的。"""
    from gongpu import relations
    boss = g.targets("汇报")[0]["id"]
    relations.touch(g.con, g.player_id, boss, g.clock.date, familiarity=40, trust=40)
    before = relations.get(g.con, g.player_id, boss)["familiarity"]
    from datetime import date
    relations.decay(g.con, date(g.clock.date.year + 3, 6, 1))
    after = relations.get(g.con, g.player_id, boss)["familiarity"]
    assert after < before


def test_熟人让事情推进得快(g):
    """关系帮你把事办动，这是它唯一该起作用的地方。"""
    from gongpu import relations, tasks
    con, on = g.con, g.clock.date
    org = g.targets("联系")[0]["id"]
    trust, leader = relations.trust_with_leader_of(con, g.player_id, org)
    assert leader is not None
    relations.touch(con, g.player_id, leader, on, familiarity=60,
                    trust=relations.SMOOTH_TRUST + 20)
    con.execute("UPDATE work_item SET state='DONE' WHERE assignee_id=?", (g.player_id,))
    iid = tasks.assign(con, g.player_id, on, g.rng)
    con.execute("UPDATE work_item SET kind='督办',progress=0 WHERE id=?", (iid,))
    con.commit()
    g.act("联系", target_id=org, work_item_id=iid)
    assert con.execute("SELECT progress FROM work_item WHERE id=?",
                       (iid,)).fetchone()[0] >= 2


def test_参考项给的是参照系不是分数(g):
    """§32 不显示"野心87"。这里同样不发明能力值，只摆事实。"""
    b = g.benchmarks()
    assert {x["项目"] for x in b} >= {"本年办事", "组织视野", "年龄"}
    for x in b:
        assert x["对比"], f"{x['项目']} 没给参照"
        assert "分" not in str(x["数值"]) or "件" in str(x["数值"])


def test_现同事的关系不会归零(g):
    """一个办公室坐着，不会因为没专门走动就形同陌路。
    真正需要花力气维持的是调走的人、别的单位的人。"""
    from datetime import date
    from gongpu import relations
    mate = g.con.execute(
        "SELECT h.character_id FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE h.end_date IS NULL AND h.character_id != ? AND s.organization_id = "
        "(SELECT organization_id FROM position_slot ps JOIN office_holding oh "
        " ON oh.position_slot_id = ps.id WHERE oh.character_id = ? "
        " AND oh.end_date IS NULL LIMIT 1) LIMIT 1",
        (g.player_id, g.player_id)).fetchone()[0]
    for y in range(1, 12):
        relations.decay(g.con, date(1986 + y, 7, 1))
    r = relations.get(g.con, g.player_id, mate)
    assert r["familiarity"] >= relations.COLLEAGUE_FLOOR, "同一单位的同事被衰减到了陌生人"
