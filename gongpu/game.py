"""应用层（技术文档 §5 Application Layer、§62 Save、§58 前端窗口）。

这一层对 UI 无知：终端和 PySide6 用的是同一套 Game。
UI 只负责把这里返回的字典画出来，绝不自己算世界。
"""
import json
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from gongpu import (actions, career, central, discipline, meetings, npc,
                    paths, projects, regions, relations, rules, tasks,
                    training)
from gongpu.actions import ActionIntent, AuthorityError
from gongpu.clock import SimulationClock
from gongpu.db import open_world
from gongpu.narrative import TextEngine, date_cn
from gongpu.rng import RandomService
from gongpu.world import advance_to, bootstrap, create_player, load_scenario

SAVE_ROOT = paths.SAVES

# §36 说的是组织掌握到什么程度，所以不给数字，给描述
VISIBILITY_LABEL = ["尚未进入视野", "有所了解", "比较熟悉", "重点掌握"]
DEVELOPMENT_LABEL = {
    "NORMAL": "一般", "OBSERVED": "列入观察", "DEVELOPMENT_POOL": "后备人选",
    "KEY_TRAINING": "重点培养", "POSITION_CANDIDATE": "岗位人选",
    "FORMAL_INSPECTION": "正在考察",
}
GAME_VERSION = "0.1.0"
SCHEMA_VERSION = 1


class Game:
    def __init__(self, con, rng, clock, player_id, name="未命名"):
        self.con, self.rng, self.clock = con, rng, clock
        self.player_id = player_id
        self.name = name
        self.text = TextEngine(rng)
        self.log = []

    # ---------- 开始与存档 ----------

    @classmethod
    def new(cls, player_name, seed="1986", scenario="county_1986", gender="M",
            career_origin="SELECTED_GRADUATE", education="本科"):
        con = open_world()
        rng = RandomService(seed)
        on = bootstrap(con, rng, load_scenario(scenario))
        pid = create_player(con, rng, on, player_name, career_origin, education, gender)
        g = cls(con, rng, SimulationClock(on), pid, player_name)
        g.log.append(g.text.render("player_reported", on, org=g.org_name(), post=g.post_name())
                     or "")
        tasks.assign(con, pid, on, rng)        # 报到第一天就有事交办
        con.commit()
        return g

    def save(self, slot="save_01"):
        """§62 一个存档 = world.db + metadata.json。"""
        d = SAVE_ROOT / slot
        d.mkdir(parents=True, exist_ok=True)
        self._persist()
        target = sqlite3.connect(d / "world.db")
        with target:
            self.con.backup(target)
        target.close()
        (d / "metadata.json").write_text(json.dumps({
            "world_name": self.name,
            "date": self.clock.date.isoformat(),
            "player": self.player_name(),
            "current_position": self.post_name(),
            "game_version": GAME_VERSION,
            "rules_version": rules.resolve(self.clock.date).era_id,
            "schema_version": SCHEMA_VERSION,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return d

    @classmethod
    def load(cls, slot="save_01"):
        d = SAVE_ROOT / slot
        meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        src = sqlite3.connect(d / "world.db")
        con = sqlite3.connect(":memory:")
        with con:
            src.backup(con)
        src.close()
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        w = con.execute("SELECT * FROM world_state WHERE world_id='w1'").fetchone()
        # §12 只恢复 seed 不够，必须把 RNG state 一起恢复，否则不是同一个世界
        rng = RandomService(w["random_seed"],
                            json.loads(w["random_state"]) if w["random_state"] else None)
        clock = SimulationClock(date.fromisoformat(w["current_date"]))
        return cls(con, rng, clock, w["player_id"], meta.get("world_name", "未命名"))

    def _persist(self):
        self.con.execute(
            "UPDATE world_state SET current_date=?,ruleset_version=?,random_state=? "
            "WHERE world_id='w1'",
            (self.clock.date.isoformat(), rules.resolve(self.clock.date).era_id,
             json.dumps(self.rng.dump())))
        self.con.commit()

    # ---------- 查询（§5 Query Service）----------

    def _one(self, sql, args=()):
        return self.con.execute(sql, args).fetchone()

    def player_name(self):
        return self._one("SELECT name FROM character WHERE id=?", (self.player_id,))[0]

    def holding(self):
        return self._one(
            "SELECT h.*, d.name AS post, d.leadership_level AS level, o.name AS org "
            "FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE h.character_id = ? AND h.end_date IS NULL "
            "ORDER BY h.id DESC LIMIT 1", (self.player_id,))

    def post_name(self):
        h = self.holding()
        return h["post"] if h else "（待安排）"

    def org_name(self):
        h = self.holding()
        return h["org"] if h else "—"

    def profile(self):
        """§58 人物窗口。§33 领导职务与职级必须分开显示，不能合成"官阶"。"""
        c = self._one("SELECT * FROM character WHERE id=?", (self.player_id,))
        h = self.holding()
        rank = self._one("SELECT rank_name FROM rank_holding WHERE character_id=? "
                         "AND end_date IS NULL ORDER BY id DESC LIMIT 1", (self.player_id,))
        vis = self._one("SELECT visibility, development_status FROM organization_attention "
                        "WHERE character_id=?", (self.player_id,))
        last = self._one("SELECT result, year FROM assessment WHERE character_id=? "
                         "ORDER BY year DESC LIMIT 1", (self.player_id,))
        born = date.fromisoformat(c["birth_date"])
        on = self.clock.date
        return {
            "姓名": c["name"],
            "性别": "男" if c["gender"] == "M" else "女",
            "年龄": on.year - born.year - ((on.month, on.day) < (born.month, born.day)),
            "学历": c["education_level"],
            "干部来源": {"SELECTED_GRADUATE": "选调生"}.get(c["career_origin"], "普通录用"),
            "政治面貌": {"MEMBER": "中共党员", "PROBATIONARY": "中共预备党员"}
                         .get(c["party_status"], "群众"),
            "单位": h["org"] if h else "—",
            "领导职务": (h["post"] if h and h["level"] not in ("科员", "办事员") else "无"),
            "公务员职级": rank["rank_name"] if rank else "—",
            "党内身份": central.current_status(self.con, self.player_id) or "—",
            "上年度考核": f"{last['year']}年 {last['result']}" if last else "—",
            "组织视野": VISIBILITY_LABEL[min((vis["visibility"] if vis else 0) // 3, 3)],
            "培养状态": DEVELOPMENT_LABEL.get(
                vis["development_status"] if vis else "NORMAL", "一般"),
        }

    def resume(self):
        """§60 履历：历年岗位，历史称谓按当时的叫法（§26）。"""
        return [dict(r) for r in self.con.execute(
            "SELECT start_date, end_date, title_at_time, exit_reason FROM office_holding "
            "WHERE character_id=? ORDER BY start_date", (self.player_id,))]

    def rank_history(self):
        return [dict(r) for r in self.con.execute(
            "SELECT start_date, end_date, rank_name, source FROM rank_holding "
            "WHERE character_id=? ORDER BY id", (self.player_id,))]

    def org_tree(self):
        """§59 机构树。"""
        rows = self.con.execute(
            "SELECT id, name, parent_id FROM organization WHERE active=1 ORDER BY id").fetchall()
        kids = {}
        for r in rows:
            kids.setdefault(r["parent_id"], []).append(r)

        def walk(pid, depth):
            for r in kids.get(pid, []):
                staff = self.con.execute(
                    "SELECT count(*) FROM position_slot WHERE organization_id=? "
                    "AND status='OCCUPIED'", (r["id"],)).fetchone()[0]
                vac = self.con.execute(
                    "SELECT count(*) FROM position_slot WHERE organization_id=? "
                    "AND status='VACANT'", (r["id"],)).fetchone()[0]
                yield {"depth": depth, "name": r["name"], "在岗": staff, "空缺": vac}
                yield from walk(r["id"], depth + 1)
        return list(walk(None, 0))

    # 各级班子的正副职层次。玩家升到市里之后，班子页要跟着换成市级，
    # 不能还盯着红山县——那会让整个界面和你的处境对不上。
    LEVELS_BY_ADMIN = {
        "COUNTY": ("正处级", "副处级"),
        "MUNICIPAL": ("正厅级", "副厅级"),
        "PROVINCIAL": ("正部级", "副部级"),
        "CENTRAL": ("正国级", "副国级"),
    }
    ADMIN_LABEL = {"COUNTY": "县级", "MUNICIPAL": "市级",
                   "PROVINCIAL": "省级", "CENTRAL": "中央"}

    def my_admin_level(self):
        """玩家现在在哪一级。界面上凡是分级的东西都跟着它走。"""
        org = actions.own_org(self.con, self.player_id)
        if org is None:
            return "COUNTY"
        r = self._one("SELECT admin_level FROM organization WHERE id=?", (org,))
        return (r[0] if r and r[0] else "COUNTY")

    def leadership(self, admin_level=None):
        """所在这一级的四套班子。次序本身就是制度：党委、人大、政府、政协、纪委。"""
        admin_level = admin_level or self.my_admin_level()
        ranks = self.LEVELS_BY_ADMIN.get(admin_level, ("正处级", "副处级"))
        return [dict(r) for r in self.con.execute(
            "SELECT c.id, c.name, c.gender, c.birth_date, c.education_level, "
            "c.party_status, h.title_at_time, h.start_date, o.short_name AS org, "
            "o.protocol_order AS op, d.protocol_order AS dp "
            "FROM office_holding h JOIN character c ON c.id = h.character_id "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "JOIN organization o ON o.id = s.organization_id "
            "WHERE h.end_date IS NULL AND d.leadership_level IN (?, ?) "
            "AND o.protocol_order IS NOT NULL AND o.admin_level = ? "
            "ORDER BY o.protocol_order, d.protocol_order, h.start_date",
            ranks + (admin_level,))]

    def colleagues(self, limit=40):
        """本单位同事，含玩家自己。"""
        org = actions.own_org(self.con, self.player_id)
        return [dict(r) for r in self.con.execute(
            "SELECT c.id, c.name, c.gender, c.birth_date, c.education_level, "
            "h.title_at_time, h.start_date FROM office_holding h "
            "JOIN character c ON c.id = h.character_id "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.end_date IS NULL AND s.organization_id = ? "
            "ORDER BY d.protocol_order, h.start_date LIMIT ?", (org, limit))]

    def promotion_outlook(self):
        """晋升路线：下一步可能去哪，每条路差什么。

        条件明细直接来自任用引擎本身（appointment.explain_for），
        不是界面另写一套——另写一套迟早和引擎不一致，界面就会骗人。

        注意这里给的是资格，不是结果。§69：满足资格不等于晋升。
        """
        from gongpu.appointment import LEVEL_ORDER, explain_for
        on = self.clock.date
        r = rules.resolve(on)
        cur = actions.player_level(self.con, self.player_id)
        out = []
        from gongpu import tracks as _tk
        mysys = _tk.system_of_character(self.con, self.player_id)
        mylvl = next((k for k, v in LEVEL_ORDER.items() if v == cur), "科员")
        mine = actions.own_org(self.con, self.player_id)
        # 按（职务定义 × 系统）分组，不能只按职务定义。
        # "副主任"这一个定义被县委办、组织部、政府办共用，只分一组的话
        # 三个系统会挤成一行，路线全串。
        #
        # 领导秘书（大秘）不是领导职务，但它确实是办公厅科员摆在桌上的
        # 一条去向，蓝图十二专门讲过它。所以这里放它进来，
        # 靠"路线"那一栏说清楚它是什么——不是台阶。
        # 秘书岗不按职务名认——"书记秘书"不是职务名。
        # 按服务关系找：这些岗位的 serves_slot_id 指向某位领导。
        sec = [r[0] for r in self.con.execute(
            "SELECT DISTINCT d.name FROM position_slot s "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE s.serves_slot_id IS NOT NULL")]
        q = ("SELECT d.*, o.system_type AS sys, "
             " count(*) AS total, "
             " sum(s.status='VACANT') AS vacant, "
             " min(CASE WHEN s.status='VACANT' THEN s.id END) AS free_slot, "
             " min(CASE WHEN s.status='VACANT' AND o.id=? THEN s.id END) AS own_free, "
             " min(CASE WHEN o.id=? THEN s.id END) AS own_any, "
             " min(s.id) AS any_slot "
             "FROM position_definition d "
             "JOIN position_slot s ON s.position_definition_id = d.id "
             "JOIN organization o ON o.id = s.organization_id "
             "WHERE d.is_leadership=1 OR d.name IN (%s) "
             "GROUP BY d.id, o.system_type "
             "ORDER BY d.protocol_order, d.id" % ",".join("?" * len(sec)))
        for d in self.con.execute(q, (mine, mine) + tuple(sec)):
            lvl = LEVEL_ORDER.get(d["leadership_level"])
            if lvl is None or not (cur <= lvl <= cur + 1):
                continue
            # 本单位的位子优先举例。市政府办公厅的人要看的是自己那栋楼里的
            # 秘书一处，不是市委办公厅的。
            slot_id = (d["own_free"] or d["own_any"]
                       or d["free_slot"] or d["any_slot"])
            conds = explain_for(self.con, slot_id, self.player_id, on, r)
            conds = [c for c in conds if c["条件"] != "现任"]
            org = self.con.execute(
                "SELECT COALESCE(o.short_name,o.name) FROM position_slot s "
                "JOIN organization o ON o.id=s.organization_id WHERE s.id=?",
                (slot_id,)).fetchone()[0]
            tgt_sys = d["sys"]
            # 梯子按你现在所在的行政层级取：市委办公厅的人走市级那张表，
            # 不能和县委办的人共用一张——那正是"只有那老三样"的来源。
            tier = _tk.tier_of(self.con, actions.own_org(self.con, self.player_id))
            if _tk.is_secretary_slot(self.con, slot_id):
                track = "领导秘书"
                why = "服务%s。%s" % (_tk.serves(self.con, slot_id) or "领导",
                                      _tk.secretary().get("caution", ""))
            elif tgt_sys == mysys and d["name"] in _tk.next_posts(mysys, mylvl, tier):
                track, why = "本系统上行", "%s%s这条路的下一步" % (
                    _tk.TIER_CN.get(tier, ""), _tk.label(mysys))
            elif tgt_sys in _tk.exit_systems(mysys, mylvl, tier):
                track, why = "外放", _tk.exit_note(mysys, tier)
            elif tgt_sys == mysys:
                track, why = "本系统", "还在%s" % _tk.label(mysys)
            else:
                track, why = "转系统", "从%s转到%s" % (_tk.label(mysys),
                                                     _tk.label(tgt_sys))
            out.append({
                "职务": d["name"],
                "示例": org + d["name"],
                "路线": track,
                "路线说明": why,
                "层次": d["leadership_level"],
                "方向": "提拔" if lvl > cur else "平调",
                "管理权限": {"MUNICIPAL": "市管", "COUNTY": "县管",
                             "PROVINCIAL": "省管", "CENTRAL": "中管"}.get(
                    d["management_authority"], d["management_authority"]),
                "空缺": d["vacant"] or 0, "编制": d["total"],
                "条件": conds,
                "达标": all(c["ok"] for c in conds),
                "缺": [c["条件"] for c in conds if not c["ok"]],
            })
        from gongpu import tracks as _tk2
        out.sort(key=lambda x: (x["方向"] != "提拔",
                                _tk2.ORDER.get(x["路线"], 9),
                                not x["达标"], -x["空缺"]))
        return out

    def units(self):
        """机构清单，供人员页切换。"""
        return [dict(r) for r in self.con.execute(
            "SELECT id, COALESCE(short_name,name) AS name, protocol_order, parent_id "
            "FROM organization WHERE active=1 "
            "ORDER BY COALESCE(protocol_order, 99), id")]

    def unit_people(self, org_id):
        """某机构在岗人员，按职务序列排。"""
        return [dict(r) for r in self.con.execute(
            "SELECT c.id, c.name, c.gender, c.birth_date, c.education_level, "
            "c.party_status, h.title_at_time, h.start_date, d.name AS post "
            "FROM office_holding h JOIN character c ON c.id = h.character_id "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.end_date IS NULL AND s.organization_id = ? "
            "ORDER BY d.protocol_order, h.start_date", (org_id,))]

    def my_unit_id(self):
        return actions.own_org(self.con, self.player_id)

    def career_ladder(self):
        """整条职务层次阶梯。路线要看得见，虽然走不走得通是另一回事。"""
        from gongpu.appointment import LEVEL_ORDER
        cur = actions.player_level(self.con, self.player_id)
        # 玩家经历过哪些层次，从履历里算，不另存状态
        passed = set()
        for (lvl,) in self.con.execute(
                "SELECT DISTINCT d.leadership_level FROM office_holding h "
                "JOIN position_slot s ON s.id = h.position_slot_id "
                "JOIN position_definition d ON d.id = s.position_definition_id "
                "WHERE h.character_id = ?", (self.player_id,)):
            if lvl in LEVEL_ORDER:
                passed.add(LEVEL_ORDER[lvl])
        out = []
        for name, order in sorted(LEVEL_ORDER.items(), key=lambda t: t[1]):
            row = self.con.execute(
                "SELECT count(*) total, sum(s.status='VACANT') vacant, "
                "group_concat(DISTINCT d.name) posts FROM position_slot s "
                "JOIN position_definition d ON d.id = s.position_definition_id "
                "WHERE d.leadership_level = ?", (name,)).fetchone()
            if not row["total"]:
                continue
            out.append({
                "层次": name, "序": order,
                "典型职务": (row["posts"] or "").split(","),
                "编制": row["total"], "空缺": row["vacant"] or 0,
                "当前": order == cur,
                "已历": order in passed and order != cur,
                "可及": order == cur + 1,
            })
        return out

    def relationships(self):
        """§40 关系网。熟悉程度和工作信任是两个量，不合成一个"交情"。"""
        return relations.for_character(self.con, self.player_id)

    def benchmarks(self):
        """参考项：不给分数，给参照系。

        §32 说性格参数不直接显示成"野心87"。同理，这里不发明一个"能力值"。
        玩家想知道自己干得怎么样，看的应该是同期的人现在都在哪儿、
        本单位的考核是怎么分布的、自己办的事比别人多还是少。
        这些全是世界里本来就有的事实，只是把它摆出来。
        """
        from gongpu.appointment import LEVEL_ORDER
        con, on = self.con, self.clock.date
        me = self._one("SELECT * FROM character WHERE id=?", (self.player_id,))
        my_level = actions.player_level(con, self.player_id)
        out = []

        # 一、同期参照：参加工作时间相近的人，现在都在什么层次
        ws = me["work_start_date"]
        peers = [r[0] for r in con.execute(
            "SELECT id FROM character WHERE alive=1 AND retired=0 AND id != ? "
            "AND work_start_date IS NOT NULL "
            "AND abs(julianday(work_start_date) - julianday(?)) <= 365*3",
            (self.player_id, ws))] if ws else []
        if peers:
            lv = self.con.execute(
                "SELECT d.leadership_level AS lvl, count(*) n FROM office_holding h "
                "JOIN position_slot s ON s.id = h.position_slot_id "
                "JOIN position_definition d ON d.id = s.position_definition_id "
                "WHERE h.end_date IS NULL AND h.character_id IN (%s) "
                "GROUP BY d.leadership_level" % ",".join("?" * len(peers)), peers).fetchall()
            dist = {r["lvl"]: r["n"] for r in lv}
            ahead = sum(n for k, n in dist.items() if LEVEL_ORDER.get(k, 0) > my_level)
            same = sum(n for k, n in dist.items() if LEVEL_ORDER.get(k, 0) == my_level)
            behind = sum(n for k, n in dist.items() if LEVEL_ORDER.get(k, 0) < my_level)
            out.append({
                "项目": "同期干部",
                "说明": "与你前后三年参加工作、目前在职的干部",
                "数值": "共 %d 人" % len(peers),
                "对比": "走在你前面 %d 人　同层次 %d 人　在你后面 %d 人"
                        % (ahead, same, behind),
                "好": ahead <= same + behind,
            })

        # 二、年度考核在本单位的位置
        org = actions.own_org(con, self.player_id)
        year = on.year - 1
        rows = con.execute(
            "SELECT a.result, a.character_id FROM assessment a "
            "JOIN office_holding h ON h.character_id = a.character_id AND h.end_date IS NULL "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "WHERE a.year = ? AND s.organization_id = ?", (year, org)).fetchall()
        if rows:
            mine = next((r["result"] for r in rows if r["character_id"] == self.player_id),
                        None)
            good = sum(1 for r in rows if r["result"] == "优秀")
            out.append({
                "项目": "%d年度考核" % year,
                "说明": "本单位参加考核 %d 人，其中优秀 %d 人" % (len(rows), good),
                "数值": mine or "未参加",
                "对比": "优秀比例一般不超过 15%，是稀缺的",
                "好": mine == "优秀",
            })

        # 三、今年办事记录，与同层次干部比
        rec = tasks.record(con, self.player_id, on)
        done, late = rec.get("办结", 0), rec.get("逾期", 0)
        same_level = [r[0] for r in con.execute(
            "SELECT h.character_id FROM office_holding h "
            "JOIN position_slot s ON s.id = h.position_slot_id "
            "JOIN position_definition d ON d.id = s.position_definition_id "
            "WHERE h.end_date IS NULL AND d.leadership_level = ?",
            (me and self.holding()["level"] if self.holding() else "科员",))]
        others = []
        for cid in same_level:
            if cid == self.player_id:
                continue
            r = tasks.record(con, cid, on)
            others.append(r.get("办结", 0))
        median = sorted(others)[len(others) // 2] if others else 0
        out.append({
            "项目": "本年办事",
            "说明": "办结与逾期都写进记录，年度考核据此评定",
            "数值": "办结 %d 件　逾期 %d 件" % (done, late),
            "对比": "同层次干部办结中位数 %d 件" % median,
            "好": done >= median and late == 0,
        })

        # 四、组织视野在同层次干部里的位次
        vis = con.execute(
            "SELECT visibility FROM organization_attention WHERE character_id=?",
            (self.player_id,)).fetchone()
        v = vis[0] if vis else 0
        peers_v = [r[0] for r in con.execute(
            "SELECT COALESCE(oa.visibility, 0) FROM character c "
            "LEFT JOIN organization_attention oa ON oa.character_id = c.id "
            "WHERE c.alive=1 AND c.retired=0 AND c.id != ?", (self.player_id,))]
        better = sum(1 for x in peers_v if x > v)
        out.append({
            "项目": "组织视野",
            "说明": "§36 这是组织掌握到什么程度，不是好感度，办砸了会回落",
            "数值": VISIBILITY_LABEL[min(v // 3, 3)],
            "对比": "在职干部中有 %d 人比你更受关注" % better,
            "好": better <= len(peers_v) // 4,
        })

        # 五、年龄窗口：这个才是真正的硬约束
        born = date.fromisoformat(me["birth_date"])
        age = on.year - born.year - ((on.month, on.day) < (born.month, born.day))
        nxt = [p for p in self.promotion_outlook() if p["方向"] == "提拔"]
        limit = None
        for p in nxt:
            for c in p["条件"]:
                if c["条件"] == "年龄":
                    limit = c["要求"]
                    break
            if limit:
                break
        out.append({
            "项目": "年龄",
            "说明": "年龄窗口是干部任用里最硬的一条，过了就是过了",
            "数值": "%d岁" % age,
            "对比": ("下一级职务要求 " + limit) if limit else "—",
            "好": True,
        })
        return out

    def projects(self):
        """§41 重大项目，可能跨越十几年。"""
        return projects.listing(self.con)

    def project_trail(self, project_id):
        """§42 决策留痕：谁提的、谁批的、谁签的、谁实施的、谁监督的。"""
        return projects.decisions(self.con, project_id)

    def discipline_records(self, cid=None):
        """§44 只看得到已经被发现的。没被发现的事，这里什么也没有——
        那正是这个系统的设计。"""
        return discipline.records_for(self.con, cid or self.player_id)

    # ---------- 党校（§38；蓝图 八/九/十）----------

    def training_state(self):
        """在校 / 可申请 / 冷却中。界面据此决定给什么。"""
        on = self.clock.date
        cur = training.current(self.con, self.player_id, on)
        if cur:
            end = date.fromisoformat(cur["s_end"]) if cur["s_end"] else on
            return {
                "在校": True, "报名": dict(cur), "结业日": end.isoformat(),
                "剩余天数": max((end - on).days, 0),
                "可结业": on >= end,
            }
        ok, why = training.request_status(self.con, self.player_id, on)
        return {"在校": False, "可申请": ok, "原因": why,
                "班次": training.programs_for(self.con, self.player_id, on)}

    def unfinished_items(self):
        """没办好的事，逐条。界面要能让玩家核对那个数字。"""
        return career.unfinished(self.con, self.player_id, self.clock.date)

    def pending_offer(self):
        """结业后待安排：有空缺了组织就来找你谈话。没有就继续等。"""
        on = self.clock.date
        ent = career.pending_entitlement(self.con, self.player_id)
        if ent is None:
            return None
        opts = career.promotion_options(self.con, self.player_id, on, self.rng,
                                        rules.resolve(on))
        return {"培训": ent["program"], "结业": ent["end_date"],
                "岗位": opts,
                "没办好": career.unfinished_count(self.con, self.player_id, on)}

    def training_history(self):
        return training.history(self.con, self.player_id)

    def apply_training(self, program_id):
        """向领导请示要求参加培训。五年一次。"""
        on = self.clock.date
        ok, why = training.request_status(self.con, self.player_id, on)
        if not ok:
            raise actions.AuthorityError(why)
        eid, start, end = training.enroll(self.con, self.player_id, program_id, on,
                                          requested=1, rng=self.rng)
        self.con.commit()
        name = self.con.execute("SELECT program FROM training_enrollment WHERE id=?",
                                (eid,)).fetchone()[0]
        self.log.append("组织上同意了。%s——%s，到%s学习。"
                        % (start.isoformat(), end.isoformat(), name))
        self.advance((start - on).days)
        return eid

    def campus_actions(self):
        return training.campus_actions()

    def do_campus(self, action):
        """校内动作。占用在校的日子，只影响结业等次和认识多少同学。"""
        st = self.training_state()
        if not st["在校"]:
            raise actions.AuthorityError("你现在不在学习期间。")
        spec = training.do_campus(self.con, st["报名"]["id"], action,
                                  self.clock.date, self.rng)
        self.con.commit()
        self.log.append("%s。%s" % (spec["label"], spec["note"]))
        self.advance(min(spec["days"], max(st["剩余天数"], 1)))
        return spec

    def finish_training(self):
        """结业，并按规则提一级——去哪个岗位由组织谈话定。"""
        st = self.training_state()
        if not st["在校"]:
            raise actions.AuthorityError("你现在不在学习期间。")
        if not st["可结业"]:
            raise actions.AuthorityError("班还没上完。")
        on = self.clock.date
        result = training.graduate(self.con, st["报名"]["id"], on)
        self.con.commit()
        self.log.append("%s。回到单位，等组织安排。" % result)
        opts = career.promotion_options(self.con, self.player_id, on, self.rng,
                                        rules.resolve(on))
        if not opts:
            self.log.append("暂时没有合适的空缺。组织把你记在名单上了，等位子。")
        return {"result": result, "options": opts,
                "unfinished": career.unfinished_count(self.con, self.player_id, on)}

    def take_offer(self, slot_id):
        """接受组织给的岗位。手上积压太多的话，这里会降为平调并调离本部门。"""
        on = self.clock.date
        r = career.take_post(self.con, self.player_id, slot_id, on, self.rng,
                             rules.resolve(on), reason="TRAINING")
        self.con.commit()
        if not r["ok"]:
            self.log.append(r["note"])
            return r
        if r["demoted"]:
            self.log.append(r["note"] + "　到%s报到。原单位的关系断了 %d 条。"
                            % (r["title"], r["cleared"]))
        else:
            self.log.append("组织上找你谈了话。从今天起，你是%s。" % r["title"])
        return r

    # ---------- 中央与会议 ----------

    def central_roster(self):
        """中央委员会名单。§二十三 党内身份与行政职务是两回事，分开显示。"""
        return central.roster(self.con)

    def my_central_status(self):
        return central.current_status(self.con, self.player_id)

    def meetings(self, limit=40):
        """所在这一级的会议。目前只有县级建了会议机制。"""
        return meetings.recent(self.con, limit, self.my_admin_level())

    def meeting_items(self, meeting_id):
        return meetings.items_of(self.con, meeting_id)

    def my_meeting_role(self, meeting_id):
        return meetings.player_role(self.con, self.player_id, meeting_id)

    def person(self, cid):
        r = self._one("SELECT * FROM character WHERE id=?", (cid,))
        return dict(r) if r else None

    def timeline(self, limit=30):
        """§58 时间线：只给玩家看得到的事（visibility=PUBLIC）。"""
        return [dict(r) for r in self.con.execute(
            "SELECT date, event_type, data, actors FROM world_event "
            "WHERE visibility='PUBLIC' ORDER BY id DESC LIMIT ?", (limit,))]

    def actions(self):
        return actions.available(self.con, self.player_id)

    # ---------- 命令（§5 Command Dispatcher）----------

    def act(self, action_type, target_id=None, work_item_id=None, method="当面"):
        """办一件事。办完时间就过去了——这是取舍的来源。"""
        intent = ActionIntent(action_type, target_id, work_item_id, method)
        r = rules.resolve(self.clock.date)
        fact = actions.resolve(self.con, self.player_id, intent, self.clock.date,
                               self.rng, r)
        self.con.commit()
        line = f"你{fact['label']}"
        if fact["target"]:
            line += f"：{fact['target']}"
        if fact["note"]:
            line += f"。{fact['note']}"
        self.log.append(line.rstrip("。") + "。")
        # 动作占用的天数照常推进世界：别的事的时限不会等你
        self.advance(fact["days"])
        return fact

    def tasks(self):
        """§58 任务窗口：待办事项。"""
        return tasks.pending(self.con, self.player_id, self.clock.date)

    def central_path(self):
        """到中央去的两条轴：行政级别和党内身份。

        蓝图二十三：这两条不是一回事。摆在明面上，不做隐藏机制。
        """
        from gongpu import tracks as _tk
        from gongpu.appointment import LEVEL_ORDER as _LEVEL
        cfg = _tk.cfg().get("central_path", {})
        cur = actions.player_level(self.con, self.player_id)
        lvl = next((k for k, v in _LEVEL.items() if v == cur), "科员")
        mine = central.current_status(self.con, self.player_id)
        rungs = []
        for r in cfg.get("rungs", []):
            n = _LEVEL.get(r["level"], 99)
            rungs.append(dict(r, 到了="是" if cur >= n else "未到",
                              当前=(r["level"] == lvl)))
        return {"现在": lvl, "党内身份": mine, "台阶": rungs,
                "身份": cfg.get("identity", []), "说明": cfg.get("note", ""),
                "提醒": cfg.get("caution", "")}

    def central_bodies(self):
        """中央机构总览。带年代：1986 年是国家教委，不是教育部。"""
        from gongpu import ministries
        return ministries.listing(self.con, self.clock.date)

    def secretary_rules(self):
        """秘书这条路的规矩。摆在明面上，不做隐藏机制。"""
        from gongpu import tracks as _tk
        return _tk.secretary()

    def contacts(self):
        """你认识的人，带明确的级别和职务。"""
        return relations.contacts(self.con, self.player_id)

    def region_listing(self):
        """全国省级行政区（§72）。"""
        return regions.listing(self.con)

    def targets(self, action_type, work_item_id=None):
        return actions.targets_for(self.con, self.player_id, action_type, work_item_id)

    SCENE_EVERY = 21        # 大约每三周写一段日常，长推进才不会只剩一行字

    def advance(self, days=1):
        """§29 推进：跨过的每一天都跑，然后把与玩家有关的事讲出来。"""
        start = self.clock.date
        before = self._one("SELECT max(id) FROM world_event")[0] or 0
        advance_to(self.con, self.clock, start + timedelta(days=days), self.rng)
        dated = self._narrate_since(before)
        # 日常片段用 text 流抽，绝不碰世界随机（§51）
        for i in range(self.SCENE_EVERY, days + 1, self.SCENE_EVERY):
            if len(dated) > 14:
                break
            when = start + timedelta(days=i)
            dated.append((when, self.text.render("idle", when)))
        if not dated:
            dated.append((self.clock.date, self.text.render("idle", self.clock.date)))
        # 按真实日期排。按文本前缀排会把 8月25日 排到 8月4日 前面。
        dated.sort(key=lambda t: t[0])
        self.log.extend(text for _, text in dated if text)
        return self.clock.date

    def _narrate_since(self, last_event_id):
        """把这段时间里与玩家有关的世界事实写成文本。事实在前，文本在后（§6）。"""
        out = []
        for r in self.con.execute(
                "SELECT * FROM world_event WHERE id > ? AND visibility='PUBLIC' ORDER BY id",
                (last_event_id,)):
            on = date.fromisoformat(r["date"])
            data = json.loads(r["data"])
            actors = json.loads(r["actors"] or "[]")
            if r["event_type"] == "appointment":  # noqa: E501
                mine = self.player_id in actors
                name = self._one("SELECT name FROM character WHERE id=?",
                                 (actors[0],))[0] if actors else "有关同志"
                # 谁宣布的，取决于这个岗位归谁管，不是玩家坐在哪儿。
                org = data.get("authority") or "组织部门"
                if mine:
                    kind = "appointment_self"
                elif data.get("org") == actions.own_org(self.con, self.player_id):
                    kind = "appointment_announced"      # 本单位的人事，当场宣布
                else:
                    kind = "appointment_elsewhere"      # 别处的任免，是听来的
                    if self.rng["text"].random() > 0.35:
                        continue        # 不是每一次别人的任免都值得写一段
                out.append((on, self.text.render(kind, on, org=org,
                                                 post=data.get("title", ""), name=name)))
            elif r["event_type"] == "institution_abolished":
                mine = self.player_id in actors
                out.append((on, "%s，%s撤销。%s%s" % (
                    date_cn(on), data.get("name", ""), data.get("note", ""),
                    "　你所在的单位没有了，等组织上另行安排。" if mine else "")))
            elif r["event_type"] == "institution_established":
                out.append((on, "%s，%s设立。%s" % (
                    date_cn(on), data.get("name", ""), data.get("note", ""))))
            elif r["event_type"] == "central_alternate_promoted"                     and self.player_id in actors:
                out.append((on, "%s，中央委员出缺，你由候补委员递补为中央委员。"
                            % date_cn(on)))
            elif r["event_type"] == "secretary_settled" and self.player_id in actors:
                out.append((on, "%s，组织上找你谈话。%s%s" % (
                    date_cn(on), data.get("why", ""),
                    "，去%s。" % data["to"] if data.get("to") else "，位子还没定。")))
            elif r["event_type"] == "region_established":
                out.append((on, "%s，%s设立。%s" % (
                    date_cn(on), data.get("name", ""), data.get("note", ""))))
            elif r["event_type"] == "institution_migration":
                out.append((on, self.text.render("institution_migration", on,
                                                 note=data.get("note", "制度发生了变化。"))))
            elif r["event_type"] == "retirement" and self.rng["text"].random() < 0.25:
                name = self._one("SELECT name FROM character WHERE id=?",
                                 (actors[0],))[0] if actors else "一位老同志"
                out.append((on, self.text.render("retirement", on, name=name)))
        return [(d, t) for d, t in out if t]

    def drain_log(self):
        out, self.log = self.log, []
        return out
