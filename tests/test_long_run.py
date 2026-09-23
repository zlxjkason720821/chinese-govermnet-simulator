"""Long Simulation Test（技术文档 §67）：世界自己跑 40 年。

目标不是"跑完不崩"，而是 §85 那句话：
断网状态下，制度按年份自己迁移，人物履历一条不丢，且完全可复现。
"""
from datetime import date, timedelta
from gongpu import rules
from gongpu.clock import SimulationClock
from gongpu.rng import RandomService
from gongpu.appointment import log_event
from conftest import build_world

START, END = date(1986, 1, 1), date(2026, 12, 31)


def simulate(seed):
    """逐日推进 41 年，制度跨年份时执行迁移（§25）并落 WorldEvent。"""
    con = build_world()
    rng = RandomService(seed)
    clock = SimulationClock(START)
    era = rules.resolve(clock.date).era_id
    migrations = []
    while clock.date < END:
        _, today = clock.advance(days=1)
        now = rules.resolve(today)                  # §23 一律问 RuleResolver
        if now.era_id != era:
            migrations.append((today, era, now.era_id))
            log_event(con, today, "institution_migration",
                      {"from": era, "to": now.era_id})
            era = now.era_id
        if today.month == 1 and today.day == 1:     # 每年一次随机外部事件（§47）
            rng["event"].random()
    con.commit()
    return con, migrations


def test_跑满四十年不中断():
    con, migrations = simulate("long-run")
    assert [m[2] for m in migrations] == [e[0] for e in rules.ERAS[1:-1]], \
        "制度迁移顺序与 Era 表不一致"
    assert len(migrations) == 8


def test_每一天都恰好命中一个制度版本():
    """§22 不能有空档，也不能两套制度同时适用。"""
    d = START
    while d <= END:
        hits = [e for e in rules.ERAS if e[1] <= d <= e[2]]
        assert len(hits) == 1, f"{d} 命中 {len(hits)} 个 Era"
        d += timedelta(days=1)


def test_关键制度节点落在正确日期():
    con, migrations = simulate("long-run")
    at = {to: on for on, _, to in migrations}
    assert at["1993_CIVIL_SERVICE"] == date(1993, 10, 1)
    assert at["2006_CIVIL_SERVICE_LAW"] == date(2006, 1, 1)
    assert at["2018_SUPERVISION"] == date(2018, 3, 20)
    assert at["2019_RANK"] == date(2019, 6, 1)


def test_迁移不丢人物履历():
    """§25 跨制度迁移必须保留历史，§26 历史称谓不被覆盖。"""
    before = build_world().execute("SELECT count(*) FROM office_holding").fetchone()[0]
    con, _ = simulate("long-run")
    after = con.execute("SELECT count(*) FROM office_holding").fetchone()[0]
    assert after == before == 5
    assert con.execute("SELECT count(*) FROM office_holding "
                       "WHERE title_at_time='干部'").fetchone()[0] == 5


def test_四十年模拟可复现():
    """§70 同种子跑两遍，WorldEvent 必须逐条相同。"""
    def log(seed):
        con, _ = simulate(seed)
        return [tuple(r) for r in con.execute(
            "SELECT date,event_type,data FROM world_event ORDER BY id")]
    assert log("long-run") == log("long-run")
    assert len(log("long-run")) == 8
