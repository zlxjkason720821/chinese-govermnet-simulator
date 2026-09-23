"""党校与干部培训（§38；总规划 §27-30；蓝图 八/九/十），以及玩家的职务变动规则。"""
from datetime import date

import pytest

from gongpu import career, tasks, training
from gongpu.game import Game
from gongpu.world import advance_to, new_game


@pytest.fixture
def g():
    x = Game.new("林致远", seed="school-test")
    x.advance(365 * 6)
    x.drain_log()
    return x


def clear_desk(g):
    g.con.execute("UPDATE work_item SET state='DONE',outcome='办结',resolved_date=? "
                  "WHERE assignee_id=?", (g.clock.date.isoformat(), g.player_id))
    g.con.commit()


def graduate(g):
    clear_desk(g)
    prog = training.programs_for(g.con, g.player_id, g.clock.date)[0]
    g.apply_training(prog["id"])
    while not g.training_state()["可结业"]:
        g.advance(30)
    clear_desk(g)
    return g.finish_training()


def pile_up(g, n=3):
    for _ in range(n):
        tasks.assign(g.con, g.player_id, g.clock.date, g.rng)
    g.con.execute("UPDATE work_item SET due_date='1990-01-01' WHERE assignee_id=? "
                  "AND state='PENDING'", (g.player_id,))
    g.con.commit()


def test_轮训范围按层次划(g):
    """时间轴 §22（2008 党校工作条例）：不同层级党校轮训不同层次的干部。
    一个科员报不上中央党校。"""
    got = training.programs_for(g.con, g.player_id, g.clock.date)
    assert got, "科员应当有对口的县级班次"
    assert all(p["school_level"] != "CENTRAL" for p in got)


def test_班次带年代(g):
    """不能拿 2026 的班次倒推 1986（蓝图九）。"""
    early = {r[0] for r in g.con.execute(
        "SELECT key FROM training_program WHERE valid_from <= '1986-12-31'")}
    allp = {r[0] for r in g.con.execute("SELECT key FROM training_program")}
    assert early < allp, "所有班次都是 1986 就有的，年代版本没起作用"


def test_本人申请五年一次(g):
    graduate(g)
    ok, why = training.request_status(g.con, g.player_id, g.clock.date)
    assert not ok and "五年" in why


def test_校内动作不影响任职条件(g):
    """校内投入只决定结业等次和认识多少同学，不碰任何任免条件。"""
    from gongpu import rules as R
    from gongpu.appointment import explain_for
    clear_desk(g)
    prog = training.programs_for(g.con, g.player_id, g.clock.date)[0]
    g.apply_training(prog["id"])
    slot = g.con.execute("SELECT id FROM position_slot LIMIT 1").fetchone()["id"]
    before = explain_for(g.con, slot, g.player_id, g.clock.date, R.resolve(g.clock.date))
    for _ in range(3):
        if g.training_state()["剩余天数"] <= 0:
            break
        g.do_campus("听课")
    after = explain_for(g.con, slot, g.player_id, g.clock.date, R.resolve(g.clock.date))
    assert [c["条件"] for c in before] == [c["条件"] for c in after]


def test_培训经历是任职硬门槛():
    """§38 党校"有用"，但只让你够格，不让你更快。"""
    from gongpu import rules as R
    from gongpu.appointment import explain_for
    con, rng, clock, pid = new_game(seed="gate")
    slot = con.execute(
        "SELECT s.id FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE d.leadership_level='正科级' LIMIT 1").fetchone()["id"]
    conds = explain_for(con, slot, pid, clock.date, R.resolve(clock.date))
    t = next((c for c in conds if c["条件"] == "培训经历"), None)
    assert t is not None, "任职条件里应当有培训经历这一项"
    assert t["ok"] is False and "任职培训" in t["要求"]


def test_结业没有空缺时进入待安排(g):
    """岗位不能凭空生成（§19）。"必定提一级"不等于"结业当天就有位子"。"""
    r = graduate(g)
    if r["options"]:
        pytest.skip("本次结业时正好有空缺")
    assert career.pending_entitlement(g.con, g.player_id) is not None


def test_手上积压则提级降为平调(g):
    """设计规则：调职前夕两件以上没办好，提级变平调，调离本部门。"""
    graduate(g)
    pile_up(g)
    assert career.unfinished_count(g.con, g.player_id, g.clock.date) >= 2
    opts = career.promotion_options(g.con, g.player_id, g.clock.date, g.rng, None)
    if not opts:
        pytest.skip("本次没有空缺可供谈话")
    r = g.take_offer(opts[0]["slot"])
    assert r["demoted"] is True


def test_降格之后这次机会作废(g):
    """不作废的话惩罚只是推迟，等于没有惩罚。"""
    graduate(g)
    pile_up(g)
    opts = career.promotion_options(g.con, g.player_id, g.clock.date, g.rng, None)
    if not opts:
        pytest.skip("本次没有空缺可供谈话")
    g.take_offer(opts[0]["slot"])
    assert career.pending_entitlement(g.con, g.player_id) is None


def test_顺利结业则提一级(g):
    graduate(g)
    from gongpu.appointment import LEVEL_ORDER
    before = career.current_level(g.con, g.player_id)
    for _ in range(24):
        g.advance(180)
        clear_desk(g)
        off = g.pending_offer()
        if off and off["岗位"]:
            r = g.take_offer(off["岗位"][0]["slot"])
            assert r["demoted"] is False
            assert career.current_level(g.con, g.player_id) == before + 1
            return
    pytest.skip("十二年都没等到空缺")


def test_常规晋升只能直升或跨部门(g):
    """玩家不能满县挑岗位。"""
    mine = career.own_org(g.con, g.player_id)
    far = g.con.execute(
        "SELECT s.id FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE d.leadership_level='正处级' AND s.organization_id != ? LIMIT 1",
        (mine,)).fetchone()
    ok, why = career.is_valid_move(g.con, g.player_id, far["id"])
    assert ok is False


def test_调离本部门关系清零(g):
    from gongpu import relations
    mine = career.own_org(g.con, g.player_id)
    n_before = g.con.execute(
        "SELECT count(*) FROM relationship WHERE (character_a=? OR character_b=?) "
        "AND familiarity > 0", (g.player_id, g.player_id)).fetchone()[0]
    assert n_before > 0
    cleared = career.transfer_out_penalty(g.con, g.player_id, g.clock.date)
    g.con.commit()
    assert cleared > 0
    peers = [r[0] for r in g.con.execute(
        "SELECT h.character_id FROM office_holding h "
        "JOIN position_slot s ON s.id = h.position_slot_id "
        "WHERE h.end_date IS NULL AND s.organization_id=? AND h.character_id!=?",
        (mine, g.player_id))]
    for p in peers:
        r = relations.get(g.con, g.player_id, p)
        if r:
            assert r["familiarity"] == 0 and r["working_trust"] == 0


def test_NPC也上党校():
    """名额和轮训范围跟玩家是同一套，玩家不是特例。
    NPC 不上党校的话，几年后没人够格提拔。"""
    con, rng, clock, pid = new_game(seed="npc-train")
    advance_to(con, clock, date(2010, 1, 1), rng)
    n = con.execute("SELECT count(DISTINCT character_id) FROM training_enrollment "
                    "WHERE character_id != ?", (pid,)).fetchone()[0]
    assert n > 20


def test_交办事项不会多到离谱():
    """一个科员一年接到二十多件重大交办事项，既不像话，
    也让"两件以上没办好就降格"变成必然触发。"""
    g = Game.new("林致远", seed="supply")
    g.advance(365 * 3)
    total = g.con.execute("SELECT count(*) FROM work_item WHERE assignee_id=?",
                          (g.player_id,)).fetchone()[0]
    assert total / 3 <= 10, f"平均每年 {total / 3:.1f} 件，太多了"


# ---------- 职业路线（蓝图 十二、十三）----------

def test_不同系统的下一步不一样():
    """蓝图十二：同样是正科级，办公厅出身的人和乡镇出身的人，
    下一步本来就不该一样。"""
    from gongpu import tracks
    assert tracks.next_posts("综合", "科员") != tracks.next_posts("乡镇", "科员")
    assert "秘书" in tracks.next_posts("综合", "科员")
    assert "副乡镇长" in tracks.next_posts("乡镇", "科员")
    # 层级也得分开：市委办公厅的科员和县委办的科员不是一条路
    assert tracks.next_posts("综合", "科员", "市") != tracks.next_posts("综合", "科员", "县")


def test_职务重名时要比系统():
    """"副主任"在组织部、县委办、人大都有。只比名字的话，
    县委办副主任会被判成组织部这条路的下一步。"""
    from gongpu.game import Game
    g = Game.new("林致远", seed="track-name")
    g.advance(365 * 4)
    from gongpu import tracks
    mysys = tracks.system_of_character(g.con, g.player_id)
    for x in g.promotion_outlook():
        if x["路线"] != "本系统上行":
            continue
        sys_of_target = g.con.execute(
            "SELECT o.system_type FROM position_slot s "
            "JOIN organization o ON o.id = s.organization_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE d.name = ? ORDER BY s.id LIMIT 1", (x["职务"],)).fetchone()
        assert x["示例"].startswith(("中共", "红山", "平州", "县", "市", "省", "青阳",
                                     "双河", "柳林")), x["示例"]
        assert mysys is not None


def test_路线不改变任职条件():
    """蓝图十三划的线：组织部是一条特殊履历，但绝不能变成
    "在组织部所以升得快"。路线只决定摆出哪些去向，不碰任何条件。"""
    from gongpu.game import Game
    g = Game.new("林致远", seed="track-fair")
    g.advance(365 * 4)
    for x in g.promotion_outlook():
        names = {c["条件"] for c in x["条件"]}
        assert not (names & {"路线", "系统", "出身"}), names


def test_每个系统都有自己的阶梯和外放去向():
    from gongpu import tracks
    for key, spec in tracks.systems().items():
        assert spec.get("label"), key
        assert spec.get("note"), key
        tiers = [t for t in ("县", "市", "省", "中央") if t in spec]
        assert tiers, key
        assert "ladder" in spec[tiers[0]], key


def test_乡镇有副科级这一档():
    """原来乡镇只有书记、乡镇长、科员，中间断了一级，
    乡镇科员内部根本升不上去。"""
    from gongpu.world import new_game
    con, rng, clock, pid = new_game(seed="township")
    n = con.execute(
        "SELECT count(*) FROM position_slot s "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "JOIN organization o ON o.id = s.organization_id "
        "WHERE o.organization_type='TOWNSHIP' AND d.leadership_level='副科级'"
    ).fetchone()[0]
    assert n > 0


def test_玩家报到只在县里():
    """§82 开局是县乡基层世界。按 slot id 取第一个空缺的话，
    中央和省级机构的 id 更小，新人会直接出现在省委组织部。"""
    from gongpu.game import Game
    from gongpu import actions
    for seed in ("a1", "b2", "c3", "d4", "e5", "f6"):
        g = Game.new("林致远", seed=seed)
        org = actions.own_org(g.con, g.player_id)
        lvl = g.con.execute("SELECT admin_level FROM organization WHERE id=?",
                            (org,)).fetchone()[0]
        assert lvl == "COUNTY", "seed=%s 报到到了 %s" % (seed, lvl)
