import sys, os
from datetime import date
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gongpu.db import open_world
from gongpu.rng import RandomService
from gongpu import rules

START = date(1986, 7, 15)


def build_world():
    """1986 年一个县：县政府 + 县长岗位 + 若干干部。MVP 的最小世界（§82）。"""
    con = open_world()
    con.execute("INSERT INTO organization(id,name,organization_type,valid_from) "
                "VALUES(1,'红山县人民政府','GOVERNMENT','1949-10-01')")
    # §21 县长由市管
    con.execute("INSERT INTO cadre_management_authority(id,level,organization_id) "
                "VALUES(1,'MUNICIPAL',1)")
    con.execute("""INSERT INTO position_definition
        (id,name,is_leadership,management_authority,min_age,max_age,
         party_requirement,min_years_experience,valid_from)
        VALUES(1,'县长',1,'MUNICIPAL',30,58,1,5,'1949-10-01')""")
    con.execute("INSERT INTO position_slot(id,position_definition_id,organization_id,"
                "valid_from,status) VALUES(1,1,1,'1949-10-01','VACANT')")

    people = [
        # id, 姓名, 生日, 党籍, 来源, 参加工作
        (1, '陈国栋', '1940-03-02', 'MEMBER', 'ORDINARY',          '1962-08-01'),
        (2, '李振华', '1945-11-20', 'MEMBER', 'ORDINARY',          '1968-09-01'),
        (3, '王建军', '1948-05-09', 'MEMBER', 'ORDINARY',          '1971-07-01'),
        (4, '赵晓梅', '1963-01-14', 'MEMBER', 'SELECTED_GRADUATE', '1985-07-01'),  # 选调生，太年轻
        (5, '孙志远', '1950-06-30', 'NONE',   'ORDINARY',          '1970-03-01'),  # 非党员
    ]
    for cid, name, birth, party, origin, work_from in people:
        con.execute("INSERT INTO character(id,name,birth_date,party_status,career_origin) "
                    "VALUES(?,?,?,?,?)", (cid, name, birth, party, origin))
        # 用一条已结束的任职表示参加工作年限（§20 履历即资历，不是 experience 数字）
        con.execute("INSERT INTO position_slot(position_definition_id,organization_id,"
                    "valid_from,status) VALUES(1,1,'1949-10-01','FROZEN')")
        sid = con.execute("SELECT max(id) FROM position_slot").fetchone()[0]
        con.execute("INSERT INTO office_holding(character_id,position_slot_id,start_date,"
                    "end_date,title_at_time) VALUES(?,?,?,?,?)",
                    (cid, sid, work_from, '1986-07-14', '干部'))
    con.commit()
    return con


@pytest.fixture
def world():
    return build_world()


@pytest.fixture
def rng():
    return RandomService("test-seed-1986")


@pytest.fixture
def r1986():
    return rules.resolve(START)
