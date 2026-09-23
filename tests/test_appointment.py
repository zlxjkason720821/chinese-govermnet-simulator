"""任用测试（技术文档 §69）。六条全部来自文档原文。"""
from datetime import date
import pytest
from gongpu.appointment import (AppointmentProcess, ProcedureError, STATES,
                                eligible_candidates)

ON = date(1986, 7, 15)


def run_to(proc, state, on=ON):
    while proc.state != state:
        proc.advance(on)
    return proc


def test_没有空缺不能任用(world, rng, r1986):
    """岗位有人就不存在空缺，不能因为谁优秀而再生一个县长（§19）。"""
    world.execute("UPDATE position_slot SET status='OCCUPIED',holder_id=1 WHERE id=1")
    with pytest.raises(ProcedureError, match="不存在空缺"):
        AppointmentProcess(world, 1, ON, rng, r1986)


def test_不满足资格不能进入正式流程(world, rng, r1986):
    """§35 硬门槛先过滤：非党员、年龄不够、资历不足的人进不了候选池。"""
    pool = {c["id"] for c in eligible_candidates(world, 1, ON, r1986)}
    assert 5 not in pool, "非党员不应进入要求党籍的领导岗位候选池"
    assert 4 not in pool, "1986 年 23 岁的选调生不满足县长最低年龄"
    assert pool == {1, 2, 3}


def test_无人合格时流程无法推进(world, rng, r1986):
    world.execute("UPDATE character SET discipline_status='UNDER_REVIEW'")
    proc = AppointmentProcess(world, 1, ON, rng, r1986)
    proc.advance(ON)                                  # -> MOTION
    with pytest.raises(ProcedureError, match="无人满足任职资格"):
        proc.advance(ON)


def test_满足资格不等于晋升(world, rng, r1986):
    """三个人都合格，最终只有一个上，其余两人仍在原岗位（§69）。"""
    proc = AppointmentProcess(world, 1, ON, rng, r1986)
    run_to(proc, "APPOINTMENT")
    winner = proc.commit(ON, "红山县人民政府县长")
    assert winner in {1, 2, 3}
    losers = {1, 2, 3} - {winner}
    for cid in losers:
        held = world.execute("SELECT 1 FROM office_holding WHERE character_id=? "
                             "AND position_slot_id=1", (cid,)).fetchone()
        assert held is None, f"{cid} 未被任命却写入了县长履历"


def test_培训不能直接晋升(world, rng, r1986):
    """§38 党校结业是履历节点，不是 training+1 换职位。"""
    world.execute("INSERT INTO training_enrollment(character_id,program,start_date,"
                  "end_date,completed) VALUES(3,'中央党校进修班','1986-03-01','1986-06-30',1)")
    world.commit()
    slot = world.execute("SELECT status,holder_id FROM position_slot WHERE id=1").fetchone()
    assert slot["status"] == "VACANT" and slot["holder_id"] is None, \
        "结业本身不得改变任何岗位归属"


def test_选调身份不能直接晋升(world, rng, r1986):
    """§39 选调生是 CareerOrigin 标签，不是升官通道。"""
    # 把选调生的年龄和资历都补足，让他仅凭硬条件进池
    world.execute("UPDATE character SET birth_date='1950-01-14' WHERE id=4")
    world.execute("UPDATE office_holding SET start_date='1972-07-01' WHERE character_id=4")
    world.commit()
    pool = {c["id"] for c in eligible_candidates(world, 1, ON, r1986)}
    assert 4 in pool, "补足硬条件后应当能进池"
    proc = AppointmentProcess(world, 1, ON, rng, r1986)
    run_to(proc, "CANDIDATE_POOL")
    # 进池 = 和别人一样排队，不是直接到 DECISION
    assert proc.state == "CANDIDATE_POOL"
    assert proc.selected_id is None


def test_关系好不能绕过程序(world, rng, r1986):
    """§34 状态机逐级推进，没有任何入口能跳到 ACTIVE。"""
    proc = AppointmentProcess(world, 1, ON, rng, r1986)
    with pytest.raises(ProcedureError, match="未走到任命环节"):
        proc.commit(ON, "红山县人民政府县长")          # VACANCY 就想落实
    run_to(proc, "INSPECTION")
    with pytest.raises(ProcedureError, match="未走到任命环节"):
        proc.commit(ON, "红山县人民政府县长")          # 考察阶段也不行
    with pytest.raises(ProcedureError, match="不可跳过"):
        proc.advance(ON, skip=True)                    # DELIBERATION 不可跳


def test_任免是一个事务(world, rng, r1986):
    """§64 旧任职结束 / 岗位占用 / 新履历 / 事件，必须同时生效。"""
    world.execute("UPDATE position_slot SET status='VACANT',holder_id=NULL WHERE id=1")
    proc = AppointmentProcess(world, 1, ON, rng, r1986)
    run_to(proc, "APPOINTMENT")
    winner = proc.commit(ON, "红山县人民政府县长")
    slot = world.execute("SELECT status,holder_id FROM position_slot WHERE id=1").fetchone()
    assert (slot["status"], slot["holder_id"]) == ("OCCUPIED", winner)
    h = world.execute("SELECT * FROM office_holding WHERE position_slot_id=1 "
                      "AND end_date IS NULL").fetchone()
    assert h["character_id"] == winner
    assert h["title_at_time"] == "红山县人民政府县长"   # §26 历史称谓落库
    ev = world.execute("SELECT * FROM world_event WHERE event_type='appointment'").fetchone()
    assert ev is not None, "重大变化必须写 WorldEvent（§65）"


def test_岗位没有管理权限时不能任免(world, rng, r1986):
    """§21 任免引擎第一步是查谁管这个岗位，查不到就不许动。"""
    world.execute("DELETE FROM cadre_management_authority")
    with pytest.raises(ProcedureError, match="干部管理权限"):
        AppointmentProcess(world, 1, ON, rng, r1986)


def test_人选在流程中途退休就不能上任(world, rng, r1986):
    """从讨论决定到正式任命隔着几个月，人选可能退休、去世、被查。

    不复核就会出现"元旦退的休，月底上的任"——这在四十年模拟里真的跑出来过。
    """
    proc = AppointmentProcess(world, 1, ON, rng, r1986)
    run_to(proc, "APPOINTMENT")
    world.execute("UPDATE character SET retired=1 WHERE id=?", (proc.selected_id,))
    with pytest.raises(ProcedureError, match="情况发生变化"):
        proc.commit(ON, "红山县人民政府县长")
    slot = world.execute("SELECT status, holder_id FROM position_slot WHERE id=1").fetchone()
    assert (slot["status"], slot["holder_id"]) == ("VACANT", None)


def test_人选在流程中途被查也不能上任(world, rng, r1986):
    proc = AppointmentProcess(world, 1, ON, rng, r1986)
    run_to(proc, "APPOINTMENT")
    world.execute("UPDATE character SET discipline_status='UNDER_REVIEW' WHERE id=?",
                  (proc.selected_id,))
    with pytest.raises(ProcedureError, match="情况发生变化"):
        proc.commit(ON, "红山县人民政府县长")


def test_人选在流程中途去世也不能上任(world, rng, r1986):
    proc = AppointmentProcess(world, 1, ON, rng, r1986)
    run_to(proc, "APPOINTMENT")
    world.execute("UPDATE character SET alive=0,death_date='1986-12-01' WHERE id=?",
                  (proc.selected_id,))
    with pytest.raises(ProcedureError, match="情况发生变化"):
        proc.commit(ON, "红山县人民政府县长")
