"""Deterministic Replay（技术文档 §70）+ 随机流隔离（§11、§51）。

同一存档 + 同一 RNG state + 同一玩家操作，必须得到同一个世界。
这条不成立，存档、回放、调试、bug 复现全部失效。
"""
from datetime import date
from gongpu.rng import RandomService
from gongpu import rules
from gongpu.appointment import AppointmentProcess
from conftest import build_world

ON = date(1986, 7, 15)
R = rules.resolve(ON)


def play(rng):
    """一段固定的"玩家操作"：走完一次县长任用。"""
    con = build_world()
    proc = AppointmentProcess(con, 1, ON, rng, R)
    while proc.state != "APPOINTMENT":
        proc.advance(ON)
    return proc.commit(ON, "红山县人民政府县长")


def test_同种子同操作结果相同():
    assert play(RandomService("seed-A")) == play(RandomService("seed-A"))


def test_不同种子会分叉():
    """否则说明随机根本没起作用，测试本身就没有意义。"""
    winners = {play(RandomService(f"seed-{i}")) for i in range(30)}
    assert len(winners) > 1, "30 个不同种子产出同一结果，随机流没有接上"


def test_从RNG_state中途恢复(world):
    """§12 只存 seed 回不到序列中间，必须能从 state 续上。"""
    rng = RandomService("seed-B")
    [rng["career"].random() for _ in range(17)]      # 假装世界已经跑了一阵
    state = rng.dump()
    expected = [rng["career"].random() for _ in range(5)]

    restored = RandomService("seed-B", state)
    assert [restored["career"].random() for _ in range(5)] == expected


def test_文本随机不影响世界随机():
    """§51 最严重的那类 bug：玩家点开一次人物界面就改变了下个月谁升职。"""
    rng = RandomService("seed-C")
    baseline = play(rng)

    rng2 = RandomService("seed-C")
    for _ in range(500):                             # 大量抽取文本措辞
        rng2["text"].choice(["上午十点", "下午三点", "第二天一早"])
    assert play(rng2) == baseline, "text_rng 的消耗污染了 career_rng"


def test_各流互不串扰():
    a = RandomService("seed-D")
    b = RandomService("seed-D")
    [b["event"].random() for _ in range(100)]
    [b["governance"].random() for _ in range(100)]
    assert a["career"].random() == b["career"].random()


def test_dump可JSON往返():
    """§12 RNG state 要能落到数据库 JSON 字段再读回来。"""
    import json
    rng = RandomService("seed-E")
    [rng["world"].random() for _ in range(9)]
    state = json.loads(json.dumps(rng.dump()))       # tuple -> list，最容易翻车的地方
    expected = rng["world"].random()
    assert RandomService("seed-E", state)["world"].random() == expected
