"""项目环节链与 2010 年后的转向（V3 §11.4、§12）。"""
from datetime import date

import pytest

from gongpu import projects
from gongpu.world import advance_to, new_game


@pytest.fixture(scope="module")
def played():
    con, rng, clock, pid = new_game(seed="项目链测试")
    advance_to(con, clock, date(2026, 12, 31), rng)
    return con


def test_一套状态机走二十二道环节():
    """不按年代切换，一套走到底。环节多是因为每一道都是一次留痕。"""
    assert len(projects.STATES) == 22
    assert projects.STATES[0] == "RESERVE"
    assert projects.STATES[-1] == "CLOSED"
    # V3 §11.4 的顺序：可研 → 资金测算 → 征求意见 → 专业论证 → 程序审查 → 集体决策
    order = projects.STATES
    for a, b in (("FEASIBILITY", "COSTING"), ("COSTING", "CONSULT"),
                 ("CONSULT", "RESEARCH"), ("RESEARCH", "REVIEW"),
                 ("REVIEW", "APPROVAL"), ("APPROVAL", "PERMITS"),
                 ("TENDER", "START"), ("COMPLETE", "INSPECTION"),
                 ("INSPECTION", "AUDIT"), ("OPERATING", "POSTEVAL")):
        assert order.index(a) < order.index(b), "%s 要排在 %s 前面" % (a, b)


def test_项目走得完不会卡在中间(played):
    """八十个项目全卡在程序审查上，那是链条断了，不是项目慢。"""
    con = played
    done = con.execute(
        "SELECT count(*) FROM project WHERE state='CLOSED'").fetchone()[0]
    total = con.execute("SELECT count(*) FROM project").fetchone()[0]
    assert done > total * 0.6, "四十年里大半项目该走完：%d/%d" % (done, total)


def test_一个项目要跨好几年(played):
    con = played
    rows = con.execute(
        "SELECT proposed_date, closed_date FROM project "
        "WHERE closed_date IS NOT NULL").fetchall()
    assert rows
    years = [(date.fromisoformat(r["closed_date"])
              - date.fromisoformat(r["proposed_date"])).days / 365 for r in rows]
    avg = sum(years) / len(years)
    assert 4 < avg < 10, "平均 %.1f 年，政府投资项目本来就慢" % avg


def test_每一道环节都留下谁经手(played):
    """十年后审计、巡视查的就是这个：哪一步谁签的字。"""
    con = played
    pid = con.execute(
        "SELECT id FROM project WHERE state='CLOSED' LIMIT 1").fetchone()[0]
    h = projects.history(con, pid)
    kinds = {x["action_type"] for x in h}
    assert "PROJECT_PROPOSED" in kinds
    for stage in ("STAGE_FEASIBILITY", "STAGE_APPROVAL", "STAGE_TENDER",
                  "STAGE_INSPECTION", "STAGE_AUDIT"):
        assert stage in kinds, "%s 这一步没留痕" % stage
    for x in h:
        if x["action_type"].startswith("STAGE_"):
            assert x["reason"], "每一步都要写清楚办的是什么"


def test_立项必须上会集体决策(played):
    """程序审查走完了才上会，会上通过才进立项。"""
    con = played
    n = con.execute(
        "SELECT count(*) FROM meeting_item WHERE source='project' "
        "AND topic LIKE '集体决策%'").fetchone()[0]
    assert n > 0


def test_十二项指标会随环节变动(played):
    con = played
    pid = con.execute(
        "SELECT id FROM project WHERE state='CLOSED' LIMIT 1").fetchone()[0]
    ind = {x["code"]: x["值"] for x in projects.indicators(con, pid)}
    assert ind["progress"] == 100, "走完了进度该是满的"
    # 指标互相冲突：不可能十二项全是初始值
    assert any(v != 50 for k, v in ind.items()
               if k not in ("progress", "audit_risk", "integrity_risk",
                            "operation_burden"))


# ── 2010 年后的转向 ──────────────────────────────────

def test_二零一零年前没有数字项目(played):
    con = played
    n = con.execute(
        "SELECT count(*) FROM project WHERE proposed_date < '2010-01-01' "
        "AND policy_domain IN ('数字基础设施','科技创新')").fetchone()[0]
    assert n == 0, "1986 年的县里不会上数据中心"


def test_二零一零年后开始转向数字(played):
    con = played
    after = con.execute(
        "SELECT count(*) FROM project WHERE proposed_date >= '2012-01-01'").fetchone()[0]
    digital = con.execute(
        "SELECT count(*) FROM project WHERE proposed_date >= '2012-01-01' "
        "AND policy_domain IN ('数字基础设施','科技创新')").fetchone()[0]
    assert digital > 0, "2010 年之后总该有几个"
    assert digital < after, "路照样要修，不是旧的全没了"


def test_领域按项目内容判断(played):
    con = played
    for r in con.execute("SELECT name, policy_domain FROM project").fetchall():
        if "数据中心" in r["name"] or "宽带" in r["name"] or "基站" in r["name"]:
            assert r["policy_domain"] == "数字基础设施", r["name"]
        if "小学" in r["name"] or "中学" in r["name"]:
            assert r["policy_domain"] == "教育", r["name"]


# ── 事项模板库（V3 §16、§17）────────────────────────

def test_不同机关的事项池不一样():
    """同样是科员，组织部的和公安局的打开桌面看到的东西不一样。"""
    from gongpu import tasks
    con, _, _, _ = new_game(seed="事项池")
    def pool(org, lvl="科员"):
        r = con.execute("SELECT id FROM organization "
                        "WHERE COALESCE(short_name,name)=?", (org,)).fetchone()
        return {x["t"] for x in tasks.pool_for(con, r["id"], lvl)}
    zzb, gaj, bgs = pool("县委组织部"), pool("公安局"), pool("县委办")
    assert "干部考察" in zzb and "干部考察" not in gaj
    assert "重大治安事件处置" in gaj and "重大治安事件处置" not in zzb
    assert "常委会会务" in bgs and "常委会会务" not in gaj
    # 通用事项族是共享的底子
    assert "报送年度总结" in zzb & gaj & bgs


def test_事项池分层次():
    """领导班子分析研判不会落到科员头上。"""
    from gongpu import tasks
    con, _, _, _ = new_game(seed="事项层次")
    r = con.execute("SELECT id FROM organization "
                    "WHERE COALESCE(short_name,name)='县委组织部'").fetchone()
    low = {x["t"] for x in tasks.pool_for(con, r["id"], "科员")}
    high = {x["t"] for x in tasks.pool_for(con, r["id"], "副处级")}
    assert "领导班子分析研判" in high and "领导班子分析研判" not in low
    assert len(high) > len(low)


def test_事项带类型和密级():
    from gongpu import tasks
    con, _, _, _ = new_game(seed="事项属性")
    r = con.execute("SELECT id FROM organization "
                    "WHERE COALESCE(short_name,name)='中共红山县纪委'").fetchone()
    pool = {x["t"]: x for x in tasks.pool_for(con, r["id"], "正科级")}
    assert pool["问题线索处置"]["secrecy"] >= 70, "线索处置是有密级的"
    assert pool["问题线索处置"]["type"] == "DISCIPLINE"
