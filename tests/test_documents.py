"""公文系统（V3 §21）与人物评价（V3 §24）。"""
from datetime import date

import pytest

from gongpu import appraisal, documents
from gongpu.world import advance_to, new_game


@pytest.fixture(scope="module")
def played():
    con, rng, clock, pid = new_game(seed="公文评价")
    advance_to(con, clock, date(1996, 12, 31), rng)
    return con, pid


# ── 公文 ─────────────────────────────────────────────

def test_文种带年代():
    """1993 年《国家行政机关公文处理办法》才把"意见"列为正式文种。"""
    assert "意见" not in documents.types_on(date(1986, 7, 15))
    assert "意见" in documents.types_on(date(1995, 1, 1))
    assert "请示" in documents.types_on(date(1986, 7, 15))


def test_事项类型决定发什么文():
    on = date(2000, 1, 1)
    assert documents.doc_for("PROJECT", on) == "请示"
    assert documents.doc_for("SUPERVISION", on) == "报告"
    assert documents.doc_for("COORDINATION", on) == "函"
    # 1986 年还没有"意见"，落回报告
    assert documents.doc_for("POLICY", date(1986, 7, 15)) == "报告"


def test_公文走完九个环节(played):
    con, _ = played
    d = con.execute(
        "SELECT id FROM document WHERE status='ARCHIVED' LIMIT 1").fetchone()
    assert d, "十年里总该有归档的文"
    steps = [t["step"] for t in documents.trail(con, d["id"])]
    assert steps == documents.STEP_ORDER


def test_每一步都有人经手而且有日期(played):
    con, _ = played
    d = con.execute(
        "SELECT id FROM document WHERE status='ARCHIVED' LIMIT 1").fetchone()
    for t in documents.trail(con, d["id"]):
        assert t["date"]
        assert t["name"], "%s 这一步没人经手" % t["step"]


def test_自己写的稿不能自己核(played):
    """自己写自己核，这道程序就是空的。"""
    con, _ = played
    bad = 0
    for d in con.execute(
            "SELECT id, drafter_id FROM document WHERE status='ARCHIVED'").fetchall():
        for t in con.execute(
                "SELECT character_id FROM document_step WHERE document_id=? "
                "AND step IN ('REVIEW','COUNTERSIGN')", (d["id"],)):
            if t[0] == d["drafter_id"]:
                bad += 1
    assert bad == 0


def test_核稿的人不签发自己核过的文(played):
    con, _ = played
    for d in con.execute(
            "SELECT id, signer_id FROM document WHERE signer_id IS NOT NULL").fetchall():
        reviewers = {r[0] for r in con.execute(
            "SELECT character_id FROM document_step WHERE document_id=? "
            "AND step IN ('REVIEW','COUNTERSIGN')", (d["id"],))}
        assert d["signer_id"] not in reviewers


def test_文号按机关和年份走(played):
    con, _ = played
    nums = [r[0] for r in con.execute(
        "SELECT doc_number FROM document WHERE doc_number IS NOT NULL")]
    assert nums
    for n in nums:
        assert "〔" in n and "号" in n
    # 同一机关同一年不重号
    rows = con.execute(
        "SELECT issuer_org_id, substr(created_date,1,4) AS y, doc_number, count(*) AS n "
        "FROM document WHERE doc_number IS NOT NULL "
        "GROUP BY issuer_org_id, y, doc_number HAVING n > 1").fetchall()
    assert not rows, [dict(r) for r in rows]


def test_密级跟着事项走(played):
    con, _ = played
    r = con.execute(
        "SELECT d.secrecy_level, w.secrecy FROM document d "
        "JOIN work_item w ON w.id = d.matter_id "
        "WHERE w.secrecy >= 60 LIMIT 1").fetchone()
    if r:
        assert r["secrecy_level"] in ("秘密", "机密")


# ── 人物评价 ─────────────────────────────────────────

def test_十五个维度(played):
    con, pid = played
    dims = appraisal.of(con, pid, date(1996, 12, 31))
    assert len(dims) == 15
    names = {d["维度"] for d in dims}
    assert {"执行能力", "政策业务能力", "协调能力", "组织管理能力", "应急能力",
            "合规记录", "纪律风险", "上级评价", "班子同级评价", "下属评价",
            "社会反馈", "项目履历", "干部工作履历", "失误记录",
            "整改记录"} == names


def test_每一项都带依据(played):
    """不是隐藏分：每一项都要说得出为什么是这个数。"""
    con, pid = played
    for d in appraisal.of(con, pid, date(1996, 12, 31)):
        assert d["依据"], d["维度"]


def test_没有记录就是没有记录(played):
    """不要拿一个默认分填上去装作知道。"""
    con, pid = played
    dims = appraisal.of(con, pid, date(1996, 12, 31))
    assert any(d["值"] is None for d in dims)


def test_没有总分(played):
    """组织部门研判读的是历史记录，不是 总分 > 80 = 自动晋升。"""
    con, pid = played
    b = appraisal.brief(con, pid, date(1996, 12, 31))
    assert b
    assert "分" not in b or "总分" not in b
    assert not hasattr(appraisal, "total")
    assert not hasattr(appraisal, "score")


def test_纪律风险只算已经发现的(played):
    """没发现的问题在库里躺着，但档案上就是干净的（§44）。"""
    con, pid = played
    # 埋一条未发现的问题
    con.execute(
        "INSERT INTO conduct_record(character_id,behavior,severity,behavior_date) "
        "VALUES(?,'测试用','一般','1990-01-01')", (pid,))
    d = next(x for x in appraisal.of(con, pid, date(1996, 12, 31))
             if x["code"] == "discipline_risk")
    assert d["值"] == 0, "还没被发现，档案上就该是干净的"
    con.execute("UPDATE conduct_record SET discovered_date='1995-01-01' "
                "WHERE character_id=? AND behavior='测试用'", (pid,))
    d2 = next(x for x in appraisal.of(con, pid, date(1996, 12, 31))
              if x["code"] == "discipline_risk")
    assert d2["值"] > 0, "查实了就要记上"
    con.execute("DELETE FROM conduct_record WHERE behavior='测试用'")


def test_干部工作履历按游戏日期算(played):
    """不能用 date('now')——那是现实世界的今天。"""
    con, pid = played
    d = next(x for x in appraisal.of(con, pid, date(1996, 12, 31))
             if x["code"] == "cadre_work_record")
    if d["值"] is not None:
        yrs = float(d["依据"].split("任职 ")[1].split(" 年")[0])
        assert yrs <= 11, "1986 年开局，1996 年最多十年"
