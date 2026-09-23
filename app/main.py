"""PySide6 桌面界面（技术文档 §4、§58-§61）。

这一层只画 Game 返回的东西，不自己算世界。
换掉这个文件，别的前端照样能跑同一个 Game。
"""
import json
import secrets
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFormLayout,
                               QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QSplitter, QTabWidget,
                               QTableWidget, QTableWidgetItem, QTextEdit,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from app.portrait import portrait
from app.style import ACCENT, INK, LINE, MUTED, OK, QSS, WARN
from gongpu.actions import (ACTIONS, FAVOR_TRUST_FLOOR as FAVOR_FLOOR,
                            METHODS, AuthorityError)
from gongpu.game import SAVE_ROOT, Game

EVENT_LABEL = {
    "appointment": "任免", "retirement": "退休", "death": "逝世",
    "recruitment": "录用", "institution_migration": "制度变革",
    "era_change": "时代变更", "player_reported": "报到",
    "appointment_suspended": "任用中止", "training_completed": "培训结业",
    "transfer_in": "上级调任", "term_session": "换届",
    "training_enrolled": "调训", "training_dispatch": "干部调训",
    "party_congress": "党代会", "general_secretary": "选举总书记",
    "state_chairman": "兼任国家主席", "meeting": "会议",
    "appointment_deferred": "任免缓议", "project_deferred": "项目缓议",
    "project_proposed": "项目动议", "project_approval": "项目报批",
    "project_funded": "资金落实", "project_implementation": "项目开工",
    "project_inspection": "项目验收", "project_closed": "项目结项",
    "discipline_clue": "问题线索", "discipline_review": "立案核查",
    "discipline_result": "处分", "discipline_removal": "撤职",
}
DISCIPLINE_EVENTS = ("discipline_clue", "discipline_review",
                     "discipline_result", "discipline_removal")
EFFECT_COLOR = {"办结": OK, "推进": INK, "走过场": MUTED, "失当": WARN, "逾期": ACCENT}


def face(row, year, size=54):
    return portrait(row["id"], row.get("gender") or "M",
                    int(str(row["birth_date"])[:4]), year,
                    row.get("education_level") or "高中", size)


def make_table(headers, stretch_last=True):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setShowGrid(False)
    t.setAlternatingRowColors(False)
    t.verticalHeader().setDefaultSectionSize(30)   # 固定行高，别让内容把表撑散
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    if stretch_last:
        h.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
    return t


def fill(t, rows, colors=None):
    t.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            item = QTableWidgetItem("" if v is None else str(v))
            if colors and colors(i, j):
                item.setForeground(QColor(colors(i, j)))
            t.setItem(i, j, item)


def clear_layout(layout):
    """立刻摘除并销毁布局里的控件，连嵌套的子布局一起。

    两个坑：
    1) 不能用 deleteLater 了事——它是异步的，控件已经离开布局但还没销毁，
       会以上一次的几何悬在页面上，把新画的东西盖住。
    2) 必须递归进子布局。装在 addLayout 里的那排按钮，takeAt 拿到的是
       布局不是控件，只摘布局的话按钮还挂在页面上，和新内容糊在一起。
    """
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
            continue
        sub = item.layout()
        if sub is not None:
            clear_layout(sub)
            sub.deleteLater()


def rule():
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet(f"color:{LINE};")
    return f


class NewGameDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("建立档案")
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        t = QLabel("公仆之心")
        t.setStyleSheet(f"color:{ACCENT};font-size:26px;font-weight:bold;")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        sub = QLabel("1986 年 7 月 15 日 · 红山县")
        sub.setObjectName("hint")
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(sub)
        lay.addWidget(rule())

        f = QFormLayout()
        f.setSpacing(10)
        self.name = QLineEdit("林致远")
        self.gender = QComboBox(); self.gender.addItems(["男", "女"])
        self.origin = QComboBox(); self.origin.addItems(["选调生", "普通录用"])
        self.edu = QComboBox(); self.edu.addItems(["本科", "大专", "中专", "硕士"])
        # 每次打开都换一个种子。固定成 "1986" 会让每局开头一模一样——
        # 同种子复现同一个世界是特性（§70），但不该是默认体验。
        self.seed = QLineEdit(self.new_seed())
        seed_row = QWidget()
        sr = QHBoxLayout(seed_row); sr.setContentsMargins(0, 0, 0, 0); sr.setSpacing(6)
        sr.addWidget(self.seed)
        reroll = QPushButton("换一个")
        reroll.setFixedWidth(70)
        reroll.clicked.connect(lambda: self.seed.setText(self.new_seed()))
        sr.addWidget(reroll)
        f.addRow("姓　　名", self.name)
        f.addRow("性　　别", self.gender)
        f.addRow("干部来源", self.origin)
        f.addRow("学　　历", self.edu)
        f.addRow("世界种子", seed_row)
        lay.addLayout(f)
        seed_note = QLabel("种子决定整个县：谁在什么位置、什么时候退休、你手上先接到哪件事。"
                           "填同一个种子会得到完全相同的世界。")
        seed_note.setObjectName("hint")
        seed_note.setWordWrap(True)
        lay.addWidget(seed_note)
        note = QLabel("干部来源是履历标签，不是职业类别，也不是升迁通道。")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        ok = QPushButton("到岗报到")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        lay.addWidget(ok)

    @staticmethod
    def new_seed():
        return str(secrets.randbelow(900000) + 100000)

    def values(self):
        return dict(
            player_name=self.name.text().strip() or "林致远",
            gender="M" if self.gender.currentText() == "男" else "F",
            career_origin=("SELECTED_GRADUATE" if self.origin.currentText() == "选调生"
                           else "ORDINARY"),
            education=self.edu.currentText(),
            seed=self.seed.text().strip() or self.new_seed())


class Main(QMainWindow):
    def __init__(self, game):
        super().__init__()
        self.game = game
        self.setWindowTitle("公仆之心 Ver 3.0")
        self.resize(1280, 840)

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._status_bar())
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        self.setCentralWidget(root)

        self._build_scene()
        self._build_profile()
        self._build_resume()
        self._build_leadership()
        self._build_people()
        self._build_promotion()
        self._build_school()
        self._build_relations()
        self._build_projects()
        self._build_meetings()
        self._build_central()
        self._build_bodies()
        self._build_regions()
        self._build_org()
        self._build_timeline()
        self._build_save()
        self.refresh()

    # ---------- 顶栏 ----------

    def _status_bar(self):
        bar = QWidget()
        bar.setObjectName("statusBar")
        bar.setFixedHeight(62)
        g = QHBoxLayout(bar)
        g.setContentsMargins(22, 8, 22, 8)
        self.s_date = QLabel(); self.s_date.setObjectName("statusDate")
        self.s_main = QLabel(); self.s_main.setObjectName("statusMain")
        self.s_sub = QLabel(); self.s_sub.setObjectName("statusSub")
        left = QVBoxLayout(); left.setSpacing(1)
        left.addWidget(self.s_date); left.addWidget(self.s_sub)
        g.addLayout(left)
        g.addSpacing(28)
        g.addWidget(self.s_main)
        g.addStretch()
        self.s_face = QLabel()
        g.addWidget(self.s_face)
        return bar

    # ---------- 主场景 ----------

    def _build_scene(self):
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(14, 12, 14, 12)

        split = QSplitter(Qt.Horizontal)

        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0)
        self.narrative = QTextEdit()
        self.narrative.setObjectName("narrative")
        self.narrative.setReadOnly(True)
        ll.addWidget(self.narrative)
        split.addWidget(left)

        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0)
        rsplit = QSplitter(Qt.Vertical)
        rl.addWidget(rsplit)
        top = QWidget(); tw = QVBoxLayout(top); tw.setContentsMargins(0, 0, 0, 0)

        tb = QGroupBox("待办事项")
        self.task_box = tb
        tl = QVBoxLayout(tb)
        self.task_list = QListWidget()
        self.task_list.setMinimumHeight(150)
        self.task_list.currentRowChanged.connect(self._task_changed)
        tl.addWidget(self.task_list)
        self.task_hint = QLabel(); self.task_hint.setObjectName("hint")
        self.task_hint.setWordWrap(True)
        tl.addWidget(self.task_hint)
        tw.addWidget(tb)

        ab = QGroupBox("办理")
        al = QFormLayout(ab)
        self.action = QComboBox()
        self.action.currentIndexChanged.connect(self._action_changed)
        self.target = QComboBox()
        self.method = QComboBox(); self.method.addItems(METHODS)
        al.addRow("事　由", self.action)
        al.addRow("对　象", self.target)
        al.addRow("方　式", self.method)
        self.hint = QLabel(); self.hint.setObjectName("hint"); self.hint.setWordWrap(True)
        al.addRow(self.hint)
        go = QPushButton("办　理"); go.setObjectName("primary")
        go.clicked.connect(self.do_action)
        al.addRow(go)
        tw.addWidget(ab)

        vb = QGroupBox("推进时间")
        vl = QGridLayout(vb)
        for i, (label, days) in enumerate((("一天", 1), ("一周", 7),
                                           ("一月", 30), ("一年", 365))):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, d=days: self.advance(d))
            vl.addWidget(b, i // 2, i % 2)
        tw.addWidget(vb)
        rsplit.addWidget(top)

        cb = QGroupBox("本单位在岗人员")
        cl = QVBoxLayout(cb)
        self.colleague_list = QListWidget()
        self.colleague_list.setIconSize(QSize(42, 54))
        self.colleague_list.setMinimumHeight(230)
        cl.addWidget(self.colleague_list)
        rsplit.addWidget(cb)
        rsplit.setSizes([430, 340])
        split.addWidget(right)
        split.setSizes([800, 420])
        lay.addWidget(split)
        self.tabs.addTab(page, "主场景")

    def _task_changed(self):
        t = self._current_task()
        if not t:
            self.task_hint.setText("手头暂时没有交办的事项。")
            return
        # 不再列"管用的办法"：那等于把答案写在题面上。
        # 判断用什么办法，本来就是这件事要玩家想的部分。
        prog = "　已推进 %d 次" % t["progress"] if t["progress"] else "　尚未动手"
        self.task_hint.setText("%s%s" % (t["desc"], prog))

    def _current_task(self):
        """只有前面那几行是真的待办；后面灰色的是已经没办好的记录。"""
        i = self.task_list.currentRow()
        return self._tasks[i] if 0 <= i < len(self._tasks) else None

    def _action_changed(self):
        key = self.action.currentData()
        if not key:
            return
        self.hint.setText(ACTIONS[key]["note"])
        self.target.clear()
        t = self._current_task()
        targets = self.game.targets(key, t["id"] if t else None)
        if not targets:
            self.target.addItem("（不需要对象）", None)
            self.target.setEnabled(False)
        else:
            self.target.setEnabled(True)
            for t in targets:
                # 级别必须写出来。托谁办事、向谁请示，先得知道他说了算不算。
                label = t["name"]
                if t.get("rank"):
                    label += "（%s）" % t["rank"]
                if t.get("tag"):
                    label += "　· " + t["tag"]
                self.target.addItem(label, t["id"])

    # ---------- 人物 ----------

    def _build_profile(self):
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(22, 18, 22, 18)

        card = QVBoxLayout()
        self.big_face = QLabel()
        self.big_face.setFixedSize(156, 200)
        card.addWidget(self.big_face)
        card.addStretch()
        lay.addLayout(card)
        lay.addSpacing(28)

        right = QVBoxLayout()
        self.p_name = QLabel(); self.p_name.setStyleSheet(
            f"font-size:24px;font-weight:bold;color:{INK};")
        right.addWidget(self.p_name)
        self.p_title = QLabel(); self.p_title.setStyleSheet(f"color:{ACCENT};font-size:15px;")
        right.addWidget(self.p_title)
        right.addWidget(rule())
        self.p_grid = QGridLayout()
        self.p_grid.setHorizontalSpacing(36)
        self.p_grid.setVerticalSpacing(12)
        right.addLayout(self.p_grid)
        note = QLabel("领导职务与公务员职级是两条并行的通道，不合并为单一官阶。")
        note.setObjectName("hint")
        right.addSpacing(8)
        right.addWidget(note)
        right.addSpacing(12)
        bt = QLabel("参考"); bt.setObjectName("sectionTitle")
        right.addWidget(bt)
        bh = QLabel("这里不给能力分。你干得怎么样，看同期的人现在都在哪儿、"
                    "本单位考核怎么分布、自己办的事比别人多还是少。")
        bh.setObjectName("hint"); bh.setWordWrap(True)
        right.addWidget(bh)
        self.bench_table = make_table(["项目", "现状", "参照", "说明"])
        self.bench_table.setMinimumHeight(212)
        right.addWidget(self.bench_table)
        right.addSpacing(12)
        dt = QLabel("纪律记录"); dt.setObjectName("sectionTitle")
        right.addWidget(dt)
        self.disc_note = QLabel(); self.disc_note.setObjectName("hint")
        self.disc_note.setWordWrap(True)
        right.addWidget(self.disc_note)
        self.disc_table = make_table(["行为", "程度", "发生", "发现", "渠道", "处理"])
        self.disc_table.setMinimumHeight(110)
        right.addWidget(self.disc_table)
        right.addStretch()
        lay.addLayout(right, 1)
        self.tabs.addTab(page, "人物")

    # ---------- 履历 ----------

    def _build_resume(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        t1 = QLabel("任职履历"); t1.setObjectName("sectionTitle")
        lay.addWidget(t1)
        h = QLabel("职务名称是任职当时的称谓，不随后来的制度改写。")
        h.setObjectName("hint")
        lay.addWidget(h)
        self.resume_table = make_table(["起", "止", "职务", "离任原因"])
        lay.addWidget(self.resume_table, 3)
        t2 = QLabel("公务员职级"); t2.setObjectName("sectionTitle")
        lay.addWidget(t2)
        self.rank_table = make_table(["起", "止", "职级", "来源"])
        lay.addWidget(self.rank_table, 2)
        self.tabs.addTab(page, "履历")

    # ---------- 班子 ----------

    def _build_leadership(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        self.banzi_title = QLabel("领导班子"); self.banzi_title.setObjectName("sectionTitle")
        lay.addWidget(self.banzi_title)
        h = QLabel("按党委、人大、政府、政协、纪委的次序排列。"
                   "你调到哪一级，这里就显示哪一级的班子。")
        h.setObjectName("hint")
        lay.addWidget(h)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.leader_box = QWidget()
        self.leader_grid = QGridLayout(self.leader_box)
        self.leader_grid.setSpacing(14)
        self.leader_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll.setWidget(self.leader_box)
        lay.addWidget(scroll)
        self.tabs.addTab(page, "班子")

    # ---------- 人员 ----------

    def _build_people(self):
        page = QWidget(); lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        head = QHBoxLayout()
        t = QLabel("在岗人员"); t.setObjectName("sectionTitle")
        head.addWidget(t)
        head.addSpacing(18)
        self.unit_pick = QComboBox()
        self.unit_pick.setMinimumWidth(280)
        self.unit_pick.currentIndexChanged.connect(self._refresh_people)
        head.addWidget(self.unit_pick)
        self.unit_count = QLabel(); self.unit_count.setObjectName("hint")
        head.addWidget(self.unit_count)
        head.addStretch()
        lay.addLayout(head)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.people_box = QWidget()
        self.people_grid = QGridLayout(self.people_box)
        self.people_grid.setSpacing(14)
        self.people_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll.setWidget(self.people_box)
        lay.addWidget(scroll)
        self.tabs.addTab(page, "人员")

    def _refresh_people(self):
        oid = self.unit_pick.currentData()
        if oid is None:
            return
        clear_layout(self.people_grid)
        year = self.game.clock.date.year
        people = self.game.unit_people(oid)
        self.unit_count.setText("在岗 %d 人" % len(people))
        for i, p in enumerate(people):
            self.people_grid.addWidget(self._person_card(p, year), i // 4, i % 4)

    def _person_card(self, p, year):
        card = QFrame()
        card.setObjectName("pcard")
        me = p["id"] == self.game.player_id
        card.setStyleSheet(
            "QFrame#pcard{background:#fdfcf9;border:1px solid %s;}"
            "QFrame#pcard QLabel{background:transparent;border:none;}"
            % (ACCENT if me else LINE))
        card.setFixedSize(254, 116)
        lay = QHBoxLayout(card); lay.setContentsMargins(10, 10, 10, 10)
        pic = QLabel(); pic.setPixmap(face(p, year, 84)); pic.setStyleSheet("border:none;")
        lay.addWidget(pic)
        box = QVBoxLayout(); box.setSpacing(2)
        n = QLabel(p["name"] + ("　（你）" if me else ""))
        n.setStyleSheet("border:none;font-size:15px;font-weight:bold;color:%s;"
                        % (ACCENT if me else INK))
        t = QLabel(p["post"]); t.setWordWrap(True)
        t.setStyleSheet("border:none;color:%s;font-size:12px;" % ACCENT)
        party = {"MEMBER": "中共党员", "PROBATIONARY": "预备党员"}.get(
            p.get("party_status"), "群众")
        d = QLabel("%s年生　%s\n%s　%s 到任"
                   % (str(p["birth_date"])[:4], p["education_level"],
                      party, p["start_date"]))
        d.setStyleSheet("border:none;color:%s;font-size:11px;" % MUTED)
        box.addWidget(n); box.addWidget(t); box.addWidget(d); box.addStretch()
        lay.addLayout(box, 1)
        return card

    # ---------- 党校 ----------

    def _build_school(self):
        page = QWidget(); lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        t = QLabel("党校"); t.setObjectName("sectionTitle")
        lay.addWidget(t)
        h = QLabel("党校不是升官加速器。它给的是理论与政策、履历完整度、"
                   "组织观察和横向干部关系四样东西——某些层次的岗位要求"
                   "相应的培训经历，没有就是不够格。")
        h.setObjectName("hint"); h.setWordWrap(True)
        lay.addWidget(h)

        self.school_box = QGroupBox("当前")
        self.school_lay = QVBoxLayout(self.school_box)
        lay.addWidget(self.school_box)

        lay.addWidget(QLabel("培训履历"))
        self.school_table = make_table(["班次", "类型", "起", "止", "结果", "同学"])
        lay.addWidget(self.school_table, 1)
        self.tabs.addTab(page, "党校")

    def _refresh_school(self):
        g = self.game
        clear_layout(self.school_lay)
        st = g.training_state()
        offer = g.pending_offer()

        if st["在校"]:
            e = st["报名"]
            info = QLabel("在 %s 学习。结业日 %s，还有 %d 天。"
                          % (e["program"], st["结业日"], st["剩余天数"]))
            info.setWordWrap(True)
            self.school_lay.addWidget(info)
            row = QHBoxLayout()
            for key, spec in g.campus_actions().items():
                b = QPushButton("%s　%d天" % (spec["label"], spec["days"]))
                b.setToolTip(spec["note"])
                b.clicked.connect(lambda _=False, k=key: self._campus(k))
                row.addWidget(b)
            self.school_lay.addLayout(row)
            fin = QPushButton("结　业"); fin.setObjectName("primary")
            fin.setEnabled(bool(st["可结业"]))
            fin.clicked.connect(self._finish_school)
            self.school_lay.addWidget(fin)
            return

        if offer:
            note = QLabel("%s 已结业（%s），待安排。"
                          % (offer["培训"], offer["结业"]))
            note.setWordWrap(True)
            self.school_lay.addWidget(note)
            if offer["没办好"] >= 2:
                items = self.game.unfinished_items()
                warn = QLabel(
                    "近一年有 %d 件事没办好（见下）。这时候接任命，"
                    "提级会降为平调，并且调离本部门——原单位的关系全部清零。"
                    % offer["没办好"])
                warn.setStyleSheet("color:%s;" % ACCENT)
                warn.setWordWrap(True)
                self.school_lay.addWidget(warn)
                lst = QLabel("　".join(
                    "%s·%s（%s）" % (x["kind"], x["subject"][:12], x["情形"])
                    for x in items[:6]))
                lst.setObjectName("hint"); lst.setWordWrap(True)
                self.school_lay.addWidget(lst)
            if offer["岗位"]:
                self.school_lay.addWidget(QLabel("组织上谈话，给了这些去向："))
                for o in offer["岗位"]:
                    tag = o.get("track", "")
                    b = QPushButton("[%s] %s%s" % (
                        tag, o["title"], "（本单位）" if o["same_unit"] else ""))
                    if o.get("why"):
                        b.setToolTip(o["why"])
                    b.clicked.connect(lambda _=False, sid=o["slot"]: self._take(sid))
                    self.school_lay.addWidget(b)
            else:
                w = QLabel("眼下没有合适的空缺。岗位不能凭空生成，只能等出缺。")
                w.setObjectName("hint"); w.setWordWrap(True)
                self.school_lay.addWidget(w)
            return

        if not st["可申请"]:
            w = QLabel(st["原因"] or "现在不能申请。")
            w.setObjectName("hint"); w.setWordWrap(True)
            self.school_lay.addWidget(w)
            return

        self.school_lay.addWidget(QLabel("可以向领导请示要求参加培训（五年一次）："))
        for p in st["班次"]:
            b = QPushButton("%s%s　%d天" % (
                __import__("gongpu.training", fromlist=["school_name"]).school_name(
                    p["school_level"]), p["name"], p["days"]))
            b.setToolTip("轮训对象：%s" % p["targets"])
            b.clicked.connect(lambda _=False, pid=p["id"]: self._apply_school(pid))
            self.school_lay.addWidget(b)

    def _apply_school(self, program_id):
        try:
            self.game.apply_training(program_id)
        except AuthorityError as e:
            self._append([str(e)], ACCENT); return
        self._append(self.game.drain_log())
        self.refresh()

    def _campus(self, action):
        try:
            self.game.do_campus(action)
        except AuthorityError as e:
            self._append([str(e)], ACCENT); return
        self._append(self.game.drain_log())
        self.refresh()

    def _finish_school(self):
        try:
            self.game.finish_training()
        except AuthorityError as e:
            self._append([str(e)], ACCENT); return
        self._append(self.game.drain_log(), OK)
        self.refresh()

    def _take(self, slot_id):
        r = self.game.take_offer(slot_id)
        self._append(self.game.drain_log(), WARN if r.get("demoted") else OK)
        self.refresh()

    # ---------- 关系 ----------

    def _build_relations(self):
        page = QWidget(); lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        t = QLabel("关系"); t.setObjectName("sectionTitle")
        lay.addWidget(t)
        h = QLabel("熟悉程度和工作信任是两个量，不合成一个交情。"
                   "关系能让事情办得动，但进不了任免的条件——"
                   "年龄、资历、基层经历、现职任职年限，一样都少不了。"
                   "一年不走动就会变淡——「走动」一趟正好抵一年的衰减，代价是一天。"
                   "工作信任到 35 才托得动人，托一次折 6：人情是有限的，"
                   "支完了得重新走动攒。")
        h.setObjectName("hint"); h.setWordWrap(True)
        lay.addWidget(h)
        self.rel_table = make_table(["姓名", "级别", "现任职务", "所在单位", "关系",
                                     "熟悉", "工作信任", "托得动", "最近往来"])
        lay.addWidget(self.rel_table)
        self.tabs.addTab(page, "关系")

    # ---------- 前程 ----------

    def _build_promotion(self):
        page = QWidget(); lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        t = QLabel("晋升路线"); t.setObjectName("sectionTitle")
        lay.addWidget(t)
        ladder_host = QWidget()
        ladder_host.setFixedHeight(100)
        self.ladder_row = QHBoxLayout(ladder_host)
        self.ladder_row.setContentsMargins(0, 4, 0, 4)
        self.ladder_row.setSpacing(0)
        self.ladder_row.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lay.addWidget(ladder_host)
        self.promo_head = QLabel(); self.promo_head.setObjectName("hint")
        self.promo_head.setWordWrap(True)
        lay.addSpacing(6)
        lay.addWidget(self.promo_head)
        lay.addSpacing(6)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.promo_box = QWidget()
        self.promo_lay = QVBoxLayout(self.promo_box)
        self.promo_lay.setSpacing(12)
        self.promo_lay.setAlignment(Qt.AlignTop)
        scroll.setWidget(self.promo_box)
        lay.addWidget(scroll)
        self.tabs.addTab(page, "前程")

    def _ladder_cell(self, r, last):
        """阶梯上的一格。当前所在、下一步可及、已经历过，三种状态一眼可分。"""
        cell = QFrame(); cell.setObjectName("lc")
        if r["当前"]:
            bg, fg, sub, bd = ACCENT, "#fdfcf9", "#e6c9c9", ACCENT
        elif r["可及"]:
            bg, fg, sub, bd = "#fdfcf9", OK, MUTED, OK
        elif r["已历"]:
            bg, fg, sub, bd = "#e9e3d6", MUTED, MUTED, LINE
        else:
            bg, fg, sub, bd = "#fdfcf9", MUTED, MUTED, LINE
        # 子标签必须显式透明，否则会继承全局 QWidget 的米白底，
        # 在深色格子里糊出一块块白条。
        cell.setStyleSheet(
            "QFrame#lc{background:%s;border:1px solid %s;}"
            "QFrame#lc QLabel{background:transparent;border:none;}" % (bg, bd))
        cell.setFixedSize(142, 92)
        cl = QVBoxLayout(cell); cl.setContentsMargins(9, 7, 9, 7); cl.setSpacing(2)

        name = QLabel(r["层次"] + ("　◀" if r["当前"] else ""))
        name.setStyleSheet("font-size:14px;font-weight:bold;color:%s;" % fg)
        cl.addWidget(name)
        posts = QLabel("、".join(r["典型职务"][:3]))
        posts.setStyleSheet("font-size:10px;color:%s;" % sub)
        posts.setWordWrap(True)
        cl.addWidget(posts)
        cl.addStretch()
        cap = QLabel("编制 %d　空缺 %d" % (r["编制"], r["空缺"]))
        cap.setStyleSheet("font-size:10px;color:%s;"
                          % (sub if not r["空缺"] or r["当前"] else ACCENT))
        cl.addWidget(cap)

        wrap = QWidget()
        wrap.setFixedHeight(92)
        wl = QHBoxLayout(wrap); wl.setContentsMargins(0, 0, 0, 0); wl.setSpacing(0)
        wl.addWidget(cell)
        if not last:
            arrow = QLabel("›")
            arrow.setAlignment(Qt.AlignCenter)
            arrow.setFixedWidth(22)
            arrow.setStyleSheet("background:transparent;color:%s;font-size:22px;" % LINE)
            wl.addWidget(arrow)
        return wrap

    def _promo_card(self, p):
        ok = p["达标"]
        card = QFrame(); card.setObjectName("promo")
        card.setStyleSheet(
            "QFrame#promo{background:#fdfcf9;border:1px solid %s;border-left:4px solid %s;}"
            "QFrame#promo QLabel{background:transparent;border:none;}"
            % (LINE, OK if ok else WARN))
        lay = QVBoxLayout(card); lay.setContentsMargins(14, 10, 14, 12)
        head = QHBoxLayout()
        title = QLabel("%s　%s　[%s]" % (p["方向"], p["示例"], p.get("路线", "")))
        title.setStyleSheet("border:none;font-size:15px;font-weight:bold;color:%s;" % INK)
        head.addWidget(title)
        meta = QLabel("%s　%s　空缺 %d/%d　%s"
                      % (p["层次"], p["管理权限"], p["空缺"], p["编制"],
                         p.get("路线说明", "")))
        meta.setStyleSheet("border:none;color:%s;font-size:12px;" % MUTED)
        head.addSpacing(14); head.addWidget(meta); head.addStretch()
        state = QLabel("条件已具备" if ok else "尚缺：" + "、".join(p["缺"]))
        state.setStyleSheet("border:none;font-weight:bold;color:%s;" % (OK if ok else WARN))
        head.addWidget(state)
        lay.addLayout(head)
        grid = QGridLayout(); grid.setHorizontalSpacing(16); grid.setVerticalSpacing(4)
        for i, c in enumerate(p["条件"]):
            mark = QLabel("●" if c["ok"] else "○")
            mark.setStyleSheet("border:none;color:%s;" % (OK if c["ok"] else WARN))
            k = QLabel(c["条件"]); k.setStyleSheet("border:none;color:%s;" % MUTED)
            v = QLabel("要求 %s　现状 %s" % (c["要求"], c["现状"]))
            v.setStyleSheet("border:none;color:%s;" % (INK if c["ok"] else WARN))
            r, col = i // 2, (i % 2) * 3
            grid.addWidget(mark, r, col); grid.addWidget(k, r, col + 1)
            grid.addWidget(v, r, col + 2)
        grid.setColumnStretch(2, 1); grid.setColumnStretch(5, 1)
        lay.addLayout(grid)
        return card

    # ---------- 项目 ----------

    def _build_projects(self):
        page = QWidget(); lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        t = QLabel("重大项目"); t.setObjectName("sectionTitle")
        lay.addWidget(t)
        h = QLabel("项目跨越多年。每个环节都记下是谁经的手——"
                   "十年后审计查到问题，能一路追回去，那时候当年签字的人"
                   "可能已经在别的位子上了。")
        h.setObjectName("hint"); h.setWordWrap(True)
        lay.addWidget(h)
        split = QSplitter(Qt.Horizontal)
        self.proj_table = make_table(["项目", "承办单位", "阶段", "体量", "动议", "结项"])
        self.proj_table.currentCellChanged.connect(
            lambda *_: self._refresh_trail())
        split.addWidget(self.proj_table)
        right = QWidget(); rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 0, 0, 0)
        tl = QLabel("决策留痕"); tl.setObjectName("sectionTitle")
        rl.addWidget(tl)
        self.trail_table = make_table(["环节", "日期", "经手人", "当时职务"])
        rl.addWidget(self.trail_table)
        self.trail_note = QLabel(); self.trail_note.setObjectName("hint")
        self.trail_note.setWordWrap(True)
        rl.addWidget(self.trail_note)
        split.addWidget(right)
        split.setSizes([680, 540])
        lay.addWidget(split, 1)      # 把余下空间给表，不然标题会把表挤到页底
        self.tabs.addTab(page, "项目")

    def _refresh_trail(self):
        i = self.proj_table.currentRow()
        if not (0 <= i < len(self._projects)):
            fill(self.trail_table, [])
            self.trail_note.setText("")
            return
        p = self._projects[i]
        fill(self.trail_table,
             [(d["role"], d["date"], d["name"], d["title_then"] or "—")
              for d in self.game.project_trail(p["id"])])
        self.trail_note.setText(
            "这些记录不会消失。项目结项之后，它们是审计和巡视唯一能凭的东西。")

    # ---------- 会议 ----------

    def _build_meetings(self):
        page = QWidget(); lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        t = QLabel("会议"); t.setObjectName("sectionTitle")
        lay.addWidget(t)
        self.meet_note = QLabel()
        self.meet_note.setObjectName("hint")
        h = QLabel("干部任免的讨论决定在党委常委会上，项目立项在政府常务会议上。"
                   "不上会，这两样就卡在那里不动——会议不是过场。"
                   "会上可以同意、原则同意，也可以再研究、缓议。")
        h.setObjectName("hint"); h.setWordWrap(True)
        lay.addWidget(h)
        lay.addWidget(self.meet_note)
        split = QSplitter(Qt.Horizontal)
        self.meet_table = make_table(["日期", "会议", "主持", "议题数"])
        self.meet_table.currentCellChanged.connect(lambda *_: self._refresh_meeting())
        split.addWidget(self.meet_table)
        right = QWidget(); rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 0, 0, 0)
        mt = QLabel("议题与决定"); mt.setObjectName("sectionTitle")
        rl.addWidget(mt)
        self.meet_role = QLabel(); self.meet_role.setObjectName("hint")
        rl.addWidget(self.meet_role)
        self.item_table = make_table(["议题", "决定", "说明"])
        rl.addWidget(self.item_table)
        split.addWidget(right)
        split.setSizes([560, 660])
        lay.addWidget(split, 1)
        self.tabs.addTab(page, "会议")

    def _refresh_meeting(self):
        i = self.meet_table.currentRow()
        if not (0 <= i < len(self._meetings)):
            fill(self.item_table, [])
            self.meet_role.setText("")
            return
        m = self._meetings[i]
        role = self.game.my_meeting_role(m["id"])
        self.meet_role.setText(
            {"主持": "你主持这次会议。",
             "与会": "你是正式成员，可以发表意见、参与决定。",
             "列席": "议题涉及你这摊，叫你进来说明情况——"
                     "列席不等于会议成员，你不参与决定。",
             "工作人员": "你负责这次会的文件和记录。人在会场，"
                         "但你不是与会人员，也不列席。",
             "无关": "这次会议与你无关，这里只是记录。"}[role])
        items = self.game.meeting_items(m["id"])
        fill(self.item_table,
             [(x["topic"], x["decision"] or "—", x["note"] or "") for x in items],
             colors=lambda r, c: (None if c != 1 else
                                  (OK if items[r]["decision"] in ("同意", "原则同意")
                                   else WARN)))

    # ---------- 中央 ----------

    def _build_central(self):
        page = QWidget(); lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        t = QLabel("中央委员会"); t.setObjectName("sectionTitle")
        lay.addWidget(t)
        h = QLabel("党内身份和行政级别是两回事。一个省部级干部可以不是中央委员，"
                   "一个中央委员也不等于比谁高一级。"
                   "中央委员只在党代会上产生，五年一次——错过一届的年龄窗口，"
                   "对六十岁的人可能就是职业上限。")
        h.setObjectName("hint"); h.setWordWrap(True)
        lay.addWidget(h)
        self.central_status = QLabel(); self.central_status.setObjectName("fieldVal")
        lay.addWidget(self.central_status)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.central_box = QWidget()
        self.central_grid = QGridLayout(self.central_box)
        self.central_grid.setSpacing(14)
        self.central_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll.setWidget(self.central_box)
        lay.addWidget(scroll, 1)

        # 到中央去：行政级别和党内身份是两个轴（蓝图二十三）。
        # 玩家问"省委书记之后是什么"，答案必须看得见，不能埋在代码里。
        box = QFrame(); box.setObjectName("ccard")
        box.setStyleSheet("QFrame#ccard{background:#fdfcf9;border:1px solid %s;"
                          "border-left:4px solid %s;}"
                          "QFrame#ccard QLabel{background:transparent;border:none;}"
                          % (LINE, ACCENT))
        bl = QVBoxLayout(box); bl.setContentsMargins(14, 10, 14, 10); bl.setSpacing(3)
        t2 = QLabel("到中央去")
        t2.setStyleSheet("font-size:13px;font-weight:bold;color:%s;" % ACCENT)
        bl.addWidget(t2)
        self.cpath_body = QLabel(); self.cpath_body.setWordWrap(True)
        self.cpath_body.setStyleSheet("color:%s;font-size:12px;" % MUTED)
        bl.addWidget(self.cpath_body)
        lay.addWidget(box)
        self.tabs.addTab(page, "中央")

    def _build_bodies(self):
        page = QWidget(); lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        t = QLabel("中央机构"); t.setObjectName("sectionTitle")
        lay.addWidget(t)
        self.body_hint = QLabel()
        self.body_hint.setObjectName("hint"); self.body_hint.setWordWrap(True)
        lay.addWidget(self.body_hint)
        self.body_table = make_table(["机构", "全称", "系统", "主要负责人", "设立"])
        lay.addWidget(self.body_table)
        self.tabs.addTab(page, "中央机构")

    def _central_card(self, r, year):
        card = QFrame(); card.setObjectName("ccard")
        top = r["status"] in ("总书记", "政治局常委")
        card.setStyleSheet(
            "QFrame#ccard{background:#fdfcf9;border:1px solid %s;"
            "border-left:4px solid %s;}"
            "QFrame#ccard QLabel{background:transparent;border:none;}"
            % (LINE, ACCENT if top else LINE))
        card.setFixedSize(280, 112)
        lay = QHBoxLayout(card); lay.setContentsMargins(10, 10, 10, 10)
        pic = QLabel(); pic.setPixmap(face(r, year, 82))
        lay.addWidget(pic)
        box = QVBoxLayout(); box.setSpacing(2)
        st = QLabel(r["status"])
        st.setStyleSheet("font-size:14px;font-weight:bold;color:%s;"
                         % (ACCENT if top else INK))
        n = QLabel(r["name"]); n.setStyleSheet("font-size:15px;font-weight:bold;")
        job = QLabel(r["title"] or "—"); job.setWordWrap(True)
        job.setStyleSheet("color:%s;font-size:11px;" % MUTED)
        d = QLabel("%s年生　第%d届" % (str(r["birth_date"])[:4], r["congress"]))
        d.setStyleSheet("color:%s;font-size:11px;" % MUTED)
        box.addWidget(st); box.addWidget(n); box.addWidget(job); box.addWidget(d)
        box.addStretch()
        lay.addLayout(box, 1)
        return card

    # ---------- 区划 ----------

    def _build_regions(self):
        page = QWidget(); lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        t = QLabel("全国省级行政区"); t.setObjectName("sectionTitle")
        lay.addWidget(t)
        self.region_hint = QLabel()
        self.region_hint.setObjectName("hint"); self.region_hint.setWordWrap(True)
        lay.addWidget(self.region_hint)
        self.region_table = make_table(["行政区", "全称", "设立", "省委班子", "省政府班子"])
        lay.addWidget(self.region_table)
        self.tabs.addTab(page, "区划")

    # ---------- 单位 / 时间线 / 存档 ----------

    def _build_org(self):
        page = QWidget(); lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        t = QLabel("机构与编制"); t.setObjectName("sectionTitle")
        lay.addWidget(t)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["机构", "在岗", "空缺"])
        self.tree.setRootIsDecorated(True)
        lay.addWidget(self.tree)
        self.tabs.addTab(page, "单位")

    def _build_timeline(self):
        page = QWidget(); lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 14)
        t = QLabel("世界时间线"); t.setObjectName("sectionTitle")
        lay.addWidget(t)
        self.timeline_table = make_table(["日期", "事项", "内容"])
        lay.addWidget(self.timeline_table)
        self.tabs.addTab(page, "时间线")

    def _build_save(self):
        page = QWidget(); lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 18, 22, 18)
        t = QLabel("存档"); t.setObjectName("sectionTitle")
        lay.addWidget(t)
        row = QHBoxLayout()
        self.slot = QLineEdit("save_01")
        self.slot.setMaximumWidth(220)
        s = QPushButton("保　存"); s.setObjectName("primary"); s.clicked.connect(self.do_save)
        ld = QPushButton("读　取"); ld.clicked.connect(self.do_load)
        row.addWidget(QLabel("存档名")); row.addWidget(self.slot)
        row.addWidget(s); row.addWidget(ld); row.addStretch()
        lay.addLayout(row)
        info = QLabel(f"存档目录：{SAVE_ROOT}\n"
                      "存档同时保存随机数状态，读档后的世界与存档那一刻完全一致。")
        info.setObjectName("hint"); info.setWordWrap(True)
        lay.addWidget(info)
        lay.addStretch()
        self.tabs.addTab(page, "存档")

    # ---------- 刷新 ----------

    def refresh(self):
        g = self.game
        p = g.profile()
        year = g.clock.date.year
        me = g.person(g.player_id)

        title = next((x for x in (p["领导职务"], p["公务员职级"], g.post_name())
                      if x not in ("无", "—")), "待安排")
        self.s_date.setText(f"{g.clock.date.year}年{g.clock.date.month}月{g.clock.date.day}日")
        self.s_sub.setText(f"{p['单位']}")
        self.s_main.setText(f"{p['姓名']}　{title}")
        self.s_face.setPixmap(face(me, year, 46))

        # 待办
        self._tasks = g.tasks()
        self.task_list.clear()
        for t in self._tasks:
            urgent = t["days_left"] <= 3
            it = QListWidgetItem(
                f"[{t['label']}] {t['subject']}　剩 {t['days_left']} 天"
                + ("　已推进" if t["progress"] else ""))
            if urgent:
                it.setForeground(QColor(ACCENT))
            self.task_list.addItem(it)
        bad = g.unfinished_items()
        for b in bad[:8]:
            it = QListWidgetItem("[%s] %s　%s" % (b["kind"], b["subject"], b["情形"]))
            it.setForeground(QColor(MUTED))
            self.task_list.addItem(it)
        if self._tasks:
            self.task_list.setCurrentRow(0)
        self._task_changed()
        self.task_box.setTitle(
            "待办事项" if not bad else "待办事项　（近一年 %d 件没办好）" % len(bad))

        # 动作
        cur = self.action.currentData()
        self.action.blockSignals(True)
        self.action.clear()
        for a in g.actions():
            days = ACTIONS[a["key"]]["days"]
            label = "%s　%d天" % (a["label"], days)
            self.action.addItem(label if a["allowed"] else label + "（无权限）", a["key"])
            if not a["allowed"]:
                self.action.model().item(self.action.count() - 1).setEnabled(False)
        i = self.action.findData(cur)
        self.action.setCurrentIndex(i if i >= 0 else 0)
        self.action.blockSignals(False)
        self._action_changed()

        # 本单位
        self.colleague_list.clear()
        for c in g.colleagues():
            it = QListWidgetItem(QIcon(face(c, year, 44)),
                                 f"{c['title_at_time']}　{c['name']}")
            if c["id"] == g.player_id:
                it.setForeground(QColor(ACCENT))
                it.setText(it.text() + "　（你）")
            self.colleague_list.addItem(it)

        # 人物
        self.big_face.setPixmap(face(me, year, 200))
        self.p_name.setText(f"{p['姓名']}　{p['性别']}　{p['年龄']}岁")
        self.p_title.setText(f"{p['单位']}{title}")
        clear_layout(self.p_grid)
        fields = [(k, v) for k, v in p.items() if k not in ("姓名", "性别", "年龄")]
        for idx, (k, v) in enumerate(fields):
            kl = QLabel(k); kl.setObjectName("fieldKey")
            vl = QLabel(str(v)); vl.setObjectName("fieldVal")
            self.p_grid.addWidget(kl, idx // 2 * 2, idx % 2)
            self.p_grid.addWidget(vl, idx // 2 * 2 + 1, idx % 2)

        bench = g.benchmarks()
        fill(self.bench_table,
             [(b["项目"], b["数值"], b["对比"], b["说明"]) for b in bench],
             colors=lambda i, j: (None if j != 1 else (OK if bench[i]["好"] else WARN)))

        self._refresh_school()
        fill(self.school_table,
             [(t["program"], t["program_type"] or "—", t["start_date"],
               t["end_date"] or "在读", t["result"] or "—", t["classmates"])
              for t in g.training_history()])

        self._meetings = g.meetings()
        self.meet_note.setText(
            "" if self._meetings else
            "你现在在%s，这一级的会议机制还没有建模——目前只有县级的常委会和常务会议。"
            % g.ADMIN_LABEL.get(g.my_admin_level(), "县里"))
        fill(self.meet_table,
             [(m["date"], m["kind"], m["chair"] or "—", m["n"]) for m in self._meetings])
        if self._meetings and self.meet_table.currentRow() < 0:
            self.meet_table.setCurrentCell(0, 0)
        self._refresh_meeting()

        clear_layout(self.central_grid)
        roster = g.central_roster()
        mine = g.my_central_status()
        self.central_status.setText(
            "你的党内身份：%s" % (mine or "无（中央委员只在党代会上产生）"))
        for i, r in enumerate(roster):
            self.central_grid.addWidget(self._central_card(r, year), i // 4, i % 4)

        cp = g.central_path()
        NL2 = chr(10)
        self.cpath_body.setText(NL2.join(
            [cp["说明"], "",
             "　行政级别　" + "　→　".join(
                 ("【%s】" % r["level"]) if r["当前"] else
                 (r["level"] if r["到了"] == "是" else "·" + r["level"])
                 for r in cp["台阶"]),
             "　你现在：%s　党内身份：%s" % (cp["现在"], cp["党内身份"] or "无"), ""]
            + ["　%s　%s" % (x["status"], x["need"]) for x in cp["身份"]]
            + ["", cp["提醒"].replace(NL2, "").replace("  ", "")]))

        bodies = g.central_bodies()
        self.body_hint.setText(
            "中央和国务院共 %d 个机构。带年代：1986 年是国家教育委员会、"
            "对外经济贸易部、冶金工业部那一套；1998 年那次机构改革一次撤掉十几个部，"
            "一代人的正部级岗位就此消失。这些机构的正职默认进中央委员会。" % len(bodies))
        fill(self.body_table,
             [(b["name"], b["full"], b["sys"] or "—",
               "%s %s" % (b["post"] or "", b["head"] or ""), str(b["since"])[:10])
              for b in bodies])

        rl = g.region_listing()
        self.region_hint.setText(
            "%d 个省级行政区。区划本身有年代：海南 1988 年建省，重庆 1997 年设直辖市，"
            "香港 1997 年、澳门 1999 年回归——这几件事就发生在这局游戏里。"
            "除本省之外只铺省级班子，下面的市县按背景处理。"
            % len(rl))
        fill(self.region_table,
             [(r["name"], r["full"], str(r["valid_from"]),
               str(r["party"]) if r["party"] is not None else "—",
               str(r["gov"]) if r["gov"] is not None else "—") for r in rl])

        self._projects = g.projects()
        fill(self.proj_table,
             [(p["name"], p["org"] or "—",
               __import__("gongpu.projects", fromlist=["STATE_CN"]).STATE_CN.get(
                   p["state"], p["state"]),
               "★" * p["scale"], p["proposed_date"], p["closed_date"] or "—")
              for p in self._projects])
        if self._projects and self.proj_table.currentRow() < 0:
            self.proj_table.setCurrentCell(0, 0)
        self._refresh_trail()

        disc = g.discipline_records()
        fill(self.disc_table,
             [(d["behavior"], d["severity"], d["behavior_date"],
               d["discovered_date"] or "—", d["discovered_by"] or "—",
               d["result"] or ("核查中" if d["status"] == "UNDER_REVIEW" else "—"))
              for d in disc])
        self.disc_note.setText(
            "档案上干净不代表干净，只代表还没查到。"
            if not disc else "已经查到的记录。行为发生的时间可能在很多年以前。")

        rels = g.contacts()
        fill(self.rel_table,
             [(r["name"], r["rank"], r["post"] or "—", r["org"] or "—", r["type"],
               r["familiarity"], r["trust"],
               "托得动" if r["trust"] >= FAVOR_FLOOR else "差 %d" % (FAVOR_FLOOR - r["trust"]),
               r["last_contact"] or "—") for r in rels],
             colors=lambda i, j: (
                 OK if (j == 6 and rels[i]["trust"] >= 45)
                       or (j == 7 and rels[i]["trust"] >= FAVOR_FLOOR)
                 else (WARN if j == 7 else None)))

        fill(self.resume_table, [(r["start_date"], r["end_date"] or "在　任",
                                  r["title_at_time"], r["exit_reason"] or "")
                                 for r in g.resume()])
        fill(self.rank_table, [(r["start_date"], r["end_date"] or "至　今",
                                r["rank_name"], r["source"]) for r in g.rank_history()])

        # 班子：跟着玩家所在层级走
        clear_layout(self.leader_grid)
        lvl = g.my_admin_level()
        self.banzi_title.setText("%s领导班子" % g.ADMIN_LABEL.get(lvl, "县级"))
        for i, l in enumerate(g.leadership()):
            self.leader_grid.addWidget(self._leader_card(l, year), i // 4, i % 4)

        # 人员页机构下拉，保持当前选择
        cur_unit = self.unit_pick.currentData()
        self.unit_pick.blockSignals(True)
        self.unit_pick.clear()
        for u in g.units():
            self.unit_pick.addItem(u["name"], u["id"])
        idx = self.unit_pick.findData(
            cur_unit if cur_unit is not None else g.my_unit_id())
        self.unit_pick.setCurrentIndex(max(idx, 0))
        self.unit_pick.blockSignals(False)
        self._refresh_people()

        # 前程：先画整条阶梯，再画下一步的条件
        clear_layout(self.ladder_row)
        ladder = g.career_ladder()
        for i, r in enumerate(ladder):
            self.ladder_row.addWidget(self._ladder_cell(r, i == len(ladder) - 1))
        clear_layout(self.promo_lay)
        paths = g.promotion_outlook()
        ready = sum(1 for x in paths if x["达标"])
        from gongpu import tracks as _tk
        sysname = _tk.system_of_character(g.con, g.player_id)
        self.promo_head.setText(
            "现任 %s%s，%s。%s　"
            "下一步可能去向 %d 类职务，其中 %d 类条件已具备。"
            "　条件具备不等于会被任用：还要有真实空缺，还要组织提名、考察、讨论决定。"
            % (p["单位"], g.post_name(), _tk.label(sysname), _tk.note(sysname),
               len(paths), ready))
        for x in paths:
            self.promo_lay.addWidget(self._promo_card(x))

        self.tree.clear()
        stack = {}
        for n in g.org_tree():
            item = QTreeWidgetItem([n["name"], str(n["在岗"]), str(n["空缺"])])
            if n["空缺"]:
                item.setForeground(2, QColor(ACCENT))
            if n["depth"] == 0:
                self.tree.addTopLevelItem(item)
            else:
                stack.get(n["depth"] - 1, self.tree.invisibleRootItem()).addChild(item)
            stack[n["depth"]] = item
        self.tree.expandAll()
        self.tree.resizeColumnToContents(0)

        rows = []
        for e in g.timeline(80):
            d = json.loads(e["data"])
            rows.append((e["date"],
                         EVENT_LABEL.get(e["event_type"], e["event_type"]),
                         d.get("note") or d.get("title") or d.get("to") or ""))
        fill(self.timeline_table, rows)

    def _leader_card(self, l, year):
        card = QFrame()
        card.setObjectName("lcard")
        card.setStyleSheet(
            f"QFrame#lcard{{background:#fdfcf9;border:1px solid {LINE};}}"
            f"QFrame#lcard QLabel{{background:transparent;border:none;}}")
        card.setFixedSize(246, 108)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(10, 10, 10, 10)
        pic = QLabel()
        pic.setPixmap(face(l, year, 76))
        pic.setStyleSheet("border:none;")
        lay.addWidget(pic)
        box = QVBoxLayout(); box.setSpacing(2)
        n = QLabel(l["name"])
        n.setStyleSheet(f"border:none;font-size:15px;font-weight:bold;color:{INK};")
        t = QLabel(l["title_at_time"])
        t.setStyleSheet(f"border:none;color:{ACCENT};font-size:12px;")
        t.setWordWrap(True)
        born = str(l["birth_date"])[:4]
        d = QLabel(f"{born}年生　{l['education_level']}\n{l['start_date']} 到任")
        d.setStyleSheet(f"border:none;color:{MUTED};font-size:11px;")
        box.addWidget(n); box.addWidget(t); box.addWidget(d); box.addStretch()
        lay.addLayout(box, 1)
        return card

    # ---------- 操作 ----------

    def _append(self, lines, color=None):
        for line in lines:
            if not line:
                continue
            if color:
                self.narrative.append(f'<span style="color:{color}">{line}</span>')
            else:
                self.narrative.append(line)
            self.narrative.append("")
        sb = self.narrative.verticalScrollBar()
        sb.setValue(sb.maximum())

    def do_action(self):
        key = self.action.currentData()
        if not key:
            return
        t = self._current_task()
        try:
            fact = self.game.act(key, self.target.currentData(),
                                 t["id"] if t else None,
                                 self.method.currentText())
        except AuthorityError as e:
            self._append([str(e)], ACCENT)      # §57 讲制度上的理由，不是"动作不可用"
            return
        self._append(self.game.drain_log(), EFFECT_COLOR.get(fact["effect"]))
        self.refresh()

    def advance(self, days):
        self.game.advance(days)
        self._append(self.game.drain_log())
        self.refresh()

    def do_save(self):
        d = self.game.save(self.slot.text().strip() or "save_01")
        QMessageBox.information(self, "存档", f"已保存到\n{d}")

    def do_load(self):
        try:
            self.game = Game.load(self.slot.text().strip() or "save_01")
        except Exception as e:
            QMessageBox.warning(self, "读档失败", str(e))
            return
        self.narrative.clear()
        self._append(["——　读取存档　——"], MUTED)
        self.refresh()


def selftest():
    """自检：打包之后没有控制台，出了问题什么也看不见。

        公仆之心.exe --selftest

    会在用户目录下写一个 selftest.txt，报告数据文件找不找得到、
    世界能不能开起来、能不能推进。给别人之前先跑这个。
    """
    import traceback
    from gongpu import paths
    lines = []
    try:
        lines.append("数据目录：%s" % paths.DATA)
        lines.append("存在：%s" % paths.DATA.exists())
        lines.append("文件：%s" % sorted(x.name for x in paths.DATA.glob("*.yaml")))
        lines.append("存档目录：%s" % paths.SAVES)
        g = Game.new("自检", seed="selftest")
        lines.append("开局：%s %s%s" % (g.clock.date, g.org_name(), g.post_name()))
        g.advance(365 * 3)
        lines.append("推进三年：%s" % g.clock.date)
        lines.append("在岗干部：%d 人" % g.con.execute(
            "SELECT count(*) FROM position_slot WHERE status='OCCUPIED'").fetchone()[0])
        # 把每一页都真建一遍、刷一遍。只查数据库查不出界面里的错。
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        w = Main(g)
        w.refresh()
        pages = [w.tabs.tabText(i) for i in range(w.tabs.count())]
        lines.append("页面 %d：%s" % (len(pages), "、".join(pages)))
        for i in range(w.tabs.count()):
            w.tabs.setCurrentIndex(i)
            app.processEvents()
        lines.append("区划：%d 个" % len(g.region_listing()))
        lines.append("结果：正常")
    except Exception:
        lines.append("结果：失败")
        lines.append(traceback.format_exc())
    from gongpu import paths
    out = paths.SAVES.parent / "selftest.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    out.write_text(text, encoding="utf-8")
    print(text)
    return out


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    app.setStyleSheet(QSS)
    dlg = NewGameDialog()
    if dlg.exec() != QDialog.Accepted:
        return
    game = Game.new(**dlg.values())
    w = Main(game)
    w._append(game.drain_log())
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
