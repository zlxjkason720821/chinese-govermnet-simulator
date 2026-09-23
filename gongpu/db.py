"""本地 SQLite 世界库（技术文档 §7、§14-§21、§65）。

Master Data / Save Data 分库（§8）：本文件只建 save 库。
用 stdlib sqlite3，不引 SQLAlchemy —— Phase 1 的表结构还没到需要 ORM 的规模。
"""
import sqlite3

SCHEMA = """
PRAGMA foreign_keys = ON;

-- §9 世界主状态
CREATE TABLE world_state (
    world_id        TEXT PRIMARY KEY,
    scenario_id     TEXT NOT NULL,
    current_date    TEXT NOT NULL,
    random_seed     TEXT NOT NULL,
    random_state    TEXT,              -- §12 RNG state，JSON
    ruleset_version TEXT,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    player_id       INTEGER
);

-- §14 人物。状态不塞一张表（§15），履历另立 office_holding。
CREATE TABLE character (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    gender        TEXT,
    birth_date    TEXT NOT NULL,
    death_date    TEXT,
    party_status  TEXT NOT NULL DEFAULT 'NONE',   -- NONE/PROBATIONARY/MEMBER
    party_join_date TEXT,
    education_level TEXT,
    career_origin TEXT,                            -- §39 选调生是来源标签，不是职业 class
    work_start_date TEXT,                          -- 参加工作时间，资历由履历累计
    personality   TEXT,                            -- §32 性格参数 JSON，不作为面板数值展示
    tier          TEXT NOT NULL DEFAULT 'C',       -- §30 A 完整模拟 / B 结构化 / C 统计池
    is_player     INTEGER NOT NULL DEFAULT 0,
    alive         INTEGER NOT NULL DEFAULT 1,
    retired       INTEGER NOT NULL DEFAULT 0,
    discipline_status TEXT NOT NULL DEFAULT 'CLEAR'
);

-- §16 机构，可改革（predecessor/successor）
CREATE TABLE organization (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,             -- 正式全称
    short_name    TEXT,                      -- 简称，也是称谓拼接用的前缀
    admin_level   TEXT,                      -- COUNTY/MUNICIPAL/PROVINCIAL/CENTRAL
    -- §72 背景人口抽象：只有玩家那条线上的机构才逐个环节地模拟。
    -- 外省的班子存在、有人、会换届，但空缺直接由上级调人补，
    -- 不跑候选池——三百个外省岗位跑完整状态机，四十年要多花一分钟。
    simulated     INTEGER NOT NULL DEFAULT 1,
    -- 机关类型。不是"党委/政府"这种大类，是"组织部/公安/法院/税务"
    -- 这种具体形态——因为这些机关连"副职怎么叫、谁是二把手、
    -- 谁能高配、谁经常兼任"都不一样，不能套同一张模板。
    archetype     TEXT,
    -- 干部管理方式：LOCAL 属地管理 / VERTICAL 垂直管理（税务、海关、国安）
    personnel_control TEXT NOT NULL DEFAULT 'LOCAL',
    -- 机构和班子的公开程度。国安系统公开信息本来就少，
    -- 为了"细"去虚构几十个处室，比留白更不真实。
    visibility    TEXT NOT NULL DEFAULT 'PUBLIC',
    protocol_order INTEGER,                  -- 四套班子次序：党委1 人大2 政府3 政协4
    organization_type TEXT,
    system_type   TEXT,
    parent_id     INTEGER REFERENCES organization(id),
    institution_grade TEXT,
    valid_from    TEXT NOT NULL,
    valid_to      TEXT,
    active        INTEGER NOT NULL DEFAULT 1
);

-- §17 "这种职位是什么"
CREATE TABLE position_definition (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    system_type   TEXT,
    leadership_level TEXT,
    protocol_order INTEGER,                  -- 本套班子内部排序
    is_leadership INTEGER NOT NULL DEFAULT 0,
    management_authority TEXT NOT NULL,   -- §21 中管/省管/市管/县管
    min_age       INTEGER,
    max_age       INTEGER,
    party_requirement INTEGER NOT NULL DEFAULT 0,
    min_years_experience INTEGER NOT NULL DEFAULT 0,
    valid_from    TEXT NOT NULL,
    valid_to      TEXT
);

-- §18 "某单位里真实存在的一个岗位"
CREATE TABLE position_slot (
    id            INTEGER PRIMARY KEY,
    position_definition_id INTEGER NOT NULL REFERENCES position_definition(id),
    organization_id INTEGER NOT NULL REFERENCES organization(id),
    valid_from    TEXT NOT NULL,
    valid_to      TEXT,
    status        TEXT NOT NULL DEFAULT 'VACANT',   -- VACANT/OCCUPIED/FROZEN
    holder_id     INTEGER REFERENCES character(id),
    -- 蓝图十二：领导个人秘书和办公厅干部不是一回事。
    -- "书记秘书""市长秘书"不是全国统一的职务名称，是一种工作关系：
    -- 正式职务是办公厅某处的职务，服务谁另记一笔。
    serves_slot_id INTEGER REFERENCES position_slot(id),
    -- 班子不是"一个正职 + 若干完全相同的副职"。
    --
    -- 分管日常工作的副职（很多地方仍称"常务副职"）：《中国共产党工作机关
    -- 条例》规定，正职由上级机构领导成员兼任的，可以设分管日常工作的副职。
    -- 所以它不是每个机关都有的固定槽位——它出现在"一把手高配、兼任、
    -- 还担着更高层职务"的机关里。
    executive_deputy INTEGER NOT NULL DEFAULT 0,
    -- 挂职用的临时岗位：有期限，到期撤销，不占本机关的实际编制序列。
    temporary     INTEGER NOT NULL DEFAULT 0,
    -- 班子排序。同为副职，党组副书记那一位和最后一位不是一回事。
    leadership_order INTEGER
);

-- 专业序列：警衔、法官等级、检察官等级。
-- 这三样和行政级别是两条线，绝不能合并——
-- 一个正科级的公安局长有警衔，一个正科级的民政局长没有。
-- 而且各有设立年代：警衔 1992 年，法官检察官等级 1997 年。
CREATE TABLE professional_rank (
    id            INTEGER PRIMARY KEY,
    character_id  INTEGER NOT NULL REFERENCES character(id),
    kind          TEXT NOT NULL,
    grade         TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    exit_reason   TEXT
);
CREATE INDEX idx_prof_rank ON professional_rank(character_id, end_date);

-- §20 任职历史。同一人可并存多条 = 兼任。
CREATE TABLE office_holding (
    id            INTEGER PRIMARY KEY,
    character_id  INTEGER NOT NULL REFERENCES character(id),
    -- 可以为空：入职前经历、外单位任职、上级调入前的履历，
    -- 这些职务不在本世界的编制里。挂到本县某个岗位上会让"历任某职"
    -- 出现"1952 年就当县长"这种记录。
    position_slot_id INTEGER REFERENCES position_slot(id),
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    holding_type  TEXT NOT NULL DEFAULT 'FORMAL',
    exit_reason   TEXT,
    -- 行政职务、党内职务、职级是三个维度，不能合成一个字段。
    -- 显示出来是"党组副书记、分管日常工作的副局长"这样一串，
    -- 但库里必须分开存，否则干部调动会把整个结构搞乱。
    party_post    TEXT,          -- 党组书记/党组副书记/党组成员/党委委员……
    -- 主持工作：正职空缺或无法履职，副职临时主持整个单位。
    -- 和"分管日常工作"完全不同——那时正职还在，一把手仍是正职。
    acting_head   INTEGER NOT NULL DEFAULT 0,
    -- NORMAL 正常任职 / CONCURRENT 兼任 / SECONDMENT 挂职 / ACTING 主持工作
    appointment_type TEXT NOT NULL DEFAULT 'NORMAL',
    -- 高配：职务是副职，个人职级更高。副主任（正部长级）不是主任。
    personal_rank TEXT,
    primary_position INTEGER NOT NULL DEFAULT 1,
    -- §26 历史称谓不能被覆盖：留任职当时的职务名
    title_at_time TEXT
);

-- §38 党校培训是真实实体，不是 training+1
-- 班次定义（哪个党校、什么类型、轮训谁）带年代：1986 不能用 2008 的轮训范围。
CREATE TABLE training_program (
    id            INTEGER PRIMARY KEY,
    key           TEXT NOT NULL,
    name          TEXT NOT NULL,
    school_level  TEXT NOT NULL,      -- CENTRAL/PROVINCIAL/MUNICIPAL/COUNTY
    program_type  TEXT NOT NULL,      -- 任职培训/进修班/中青班/专题研讨班/党性教育
    days          INTEGER NOT NULL,
    targets       TEXT NOT NULL,      -- 轮训对象层次，逗号分隔
    max_age       INTEGER,
    quota         INTEGER NOT NULL DEFAULT 1,
    valid_from    TEXT NOT NULL,
    valid_to      TEXT
);

-- 具体开的某一期。名额是真实资源，不是人人想上就能上。
CREATE TABLE training_session (
    id            INTEGER PRIMARY KEY,
    program_id    INTEGER NOT NULL REFERENCES training_program(id),
    start_date    TEXT NOT NULL,
    end_date      TEXT NOT NULL,
    quota         INTEGER NOT NULL
);

CREATE TABLE training_enrollment (
    id            INTEGER PRIMARY KEY,
    character_id  INTEGER NOT NULL REFERENCES character(id),
    session_id    INTEGER REFERENCES training_session(id),
    program       TEXT NOT NULL,      -- 班次名称，定格在参加当时（§26）
    program_type  TEXT,
    school_level  TEXT,
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    completed     INTEGER NOT NULL DEFAULT 0,
    result        TEXT,               -- 优秀学员 / 结业 / 中途退出
    theory        INTEGER NOT NULL DEFAULT 0,      -- 校内投入，只影响结业等次
    classmates    INTEGER NOT NULL DEFAULT 0,
    requested     INTEGER NOT NULL DEFAULT 0,      -- 1 = 本人申请，0 = 组织调训
    entitlement_used INTEGER NOT NULL DEFAULT 0    -- 结业带来的那次提级机会用掉了没有
);

-- §21 干部管理权限：中管/省管/市管/县管/部门管理/双重管理
CREATE TABLE cadre_management_authority (
    id            INTEGER PRIMARY KEY,
    level         TEXT NOT NULL,
    organization_id INTEGER NOT NULL REFERENCES organization(id),
    authority_type TEXT NOT NULL DEFAULT 'SINGLE'
);

-- §34 任免流程状态机实例
CREATE TABLE appointment_process (
    id            INTEGER PRIMARY KEY,
    position_slot_id INTEGER NOT NULL REFERENCES position_slot(id),
    state         TEXT NOT NULL,
    opened_date   TEXT NOT NULL,
    closed_date   TEXT,
    selected_id   INTEGER REFERENCES character(id)
);

-- 职级/非领导职务履历。§26 套转不得改写历史称谓，所以旧记录只封口不覆盖。
CREATE TABLE rank_holding (
    id            INTEGER PRIMARY KEY,
    character_id  INTEGER NOT NULL REFERENCES character(id),
    rank_name     TEXT NOT NULL,
    rank_system   TEXT NOT NULL,          -- 1993 / 2006 / 2019
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    source        TEXT                    -- INITIAL / PROMOTION / CONVERSION(套转)
);

-- §15 考核必须独立成表。年度考核是真实制度，不是一个隐藏的"实绩值"。
CREATE TABLE assessment (
    id            INTEGER PRIMARY KEY,
    character_id  INTEGER NOT NULL REFERENCES character(id),
    year          INTEGER NOT NULL,
    result        TEXT NOT NULL,     -- 优秀 / 称职 / 基本称职 / 不称职
    organization_id INTEGER REFERENCES organization(id),
    UNIQUE(character_id, year)
);

-- §58 任务窗口的实体。自由文本无法判定，所以事项必须是库里的真实对象：
-- 有类型、有时限、有归口单位、有状态。动作作用在它上面，判定才有依据。
-- 事项（Matter）。整个系统的核心对象，不是"一条任务文本"。
--
-- "[上级督办] 市委督查室交办的问题线索 剩2天"这一行字，后台至少要有：
-- 谁交办的、交给哪个机关、哪位领导负责、哪个处室承办、现在办到哪一步、
-- 由哪件事派生出来、有哪些风险。玩家点开之后，可用动作才按他的岗位生成。
--
-- 项目是长期事项的容器，会议是处理事项的一种制度化机制，
-- 待办只是某个岗位对事项的一个视图。
CREATE TABLE work_item (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,
    subject       TEXT NOT NULL,
    organization_id INTEGER REFERENCES organization(id),
    assignee_id   INTEGER NOT NULL REFERENCES character(id),
    created_date  TEXT NOT NULL,
    due_date      TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING/DONE/OVERDUE/TRANSFERRED
    progress      INTEGER NOT NULL DEFAULT 0,        -- 推进次数，够了才办得结
    resolved_date TEXT,
    outcome       TEXT,

    -- ── 以下是 Matter 模型 ──────────────────────────
    -- 事项类型。决定这件事走哪条流程、进哪种会、谁有权处理。
    matter_type   TEXT,        -- DOCUMENT/INSTRUCTION/SUPERVISION/PROJECT/
                               -- PERSONNEL/POLICY/PETITION/INSPECTION/
                               -- EMERGENCY/DISCIPLINE/BUDGET/AUDIT/
                               -- PLANNING/ROUTINE/COORDINATION/REPORT
    origin_org_id INTEGER REFERENCES organization(id),   -- 谁交办的
    target_org_id INTEGER REFERENCES organization(id),   -- 交给哪个机关
    origin_person_id INTEGER REFERENCES character(id),
    owner_position_id INTEGER REFERENCES position_slot(id),  -- 哪个岗位承办
    region        TEXT,
    policy_domain TEXT,        -- 交通/水利/医疗/教育/住房/生态环保……

    -- 四个维度分开。重要不等于紧急，紧急不等于敏感。
    importance    INTEGER NOT NULL DEFAULT 50,
    urgency       INTEGER NOT NULL DEFAULT 50,
    secrecy       INTEGER NOT NULL DEFAULT 0,
    complexity    INTEGER NOT NULL DEFAULT 50,
    political_sensitivity INTEGER NOT NULL DEFAULT 0,

    current_stage TEXT,        -- 核查 / 起草 / 征求意见 / 报批 ……
    parent_matter_id INTEGER REFERENCES work_item(id),
    root_matter_id   INTEGER REFERENCES work_item(id),
    project_id    INTEGER REFERENCES project(id)
);
CREATE INDEX idx_matter_parent ON work_item(parent_matter_id);
CREATE INDEX idx_matter_owner ON work_item(assignee_id, state);

-- V3 文档里管它叫 matter。同一张表，换个名字读着顺。
CREATE VIEW matter AS SELECT * FROM work_item;

-- 公文。事项的主要表现载体之一（V3 §21）。
--
-- 机关里大部分"办事"其实是在办文：起草、核稿、会签、修改、签发、
-- 编号、印发、签收、归档。一件事走到哪一步，看的是文走到哪一步。
--
-- 文种由年代规则控制：1986 年和 2026 年的文种不完全一样，
-- 《国家行政机关公文处理办法》改过好几次。
CREATE TABLE document (
    id            INTEGER PRIMARY KEY,
    matter_id     INTEGER REFERENCES work_item(id),
    doc_type      TEXT NOT NULL,      -- 请示/报告/通知/通报/意见/函/批复/纪要/决定
    title         TEXT NOT NULL,
    drafter_org_id INTEGER REFERENCES organization(id),
    drafter_id    INTEGER REFERENCES character(id),
    issuer_org_id INTEGER REFERENCES organization(id),
    signer_id     INTEGER REFERENCES character(id),
    -- DRAFT 起草 / REVIEW 核稿 / COUNTERSIGN 会签 / REVISION 修改 /
    -- SIGN 签发 / NUMBERING 编号 / ISSUED 印发 / RECEIVED 签收 / ARCHIVED 归档
    status        TEXT NOT NULL DEFAULT 'DRAFT',
    secrecy_level TEXT,               -- 无/内部/秘密/机密
    doc_number    TEXT,               -- 平政发〔1987〕12号
    created_date  TEXT NOT NULL,
    issued_date   TEXT
);
CREATE INDEX idx_doc_matter ON document(matter_id);

-- 公文流转的每一步。谁核的稿、谁会的签、谁签的发，都要有据可查。
CREATE TABLE document_step (
    id            INTEGER PRIMARY KEY,
    document_id   INTEGER NOT NULL REFERENCES document(id),
    step          TEXT NOT NULL,
    character_id  INTEGER REFERENCES character(id),
    organization_id INTEGER REFERENCES organization(id),
    date          TEXT NOT NULL,
    note          TEXT
);

-- 权限不按行政级别硬开关。
--
--     if rank >= 厅级:  开放会议        ← 错的
--
-- 一个正处级的县委书记进常委会，一个正处级的省厅处长不进；
-- 能不能开会，取决于这个岗位有没有这项权限，不取决于他几级。
CREATE TABLE capability (
    code          TEXT PRIMARY KEY,
    description   TEXT
);

-- 岗位 × 权限。scope 限定这项权限管得到多大范围：
-- OWN 本人 / UNIT 本单位 / SUBORDINATE 下属单位 / JURISDICTION 本辖区
CREATE TABLE position_capability (
    id            INTEGER PRIMARY KEY,
    position_definition_id INTEGER REFERENCES position_definition(id),
    archetype     TEXT,        -- 或者按机关类型给：所有公安机关的局长
    capability_code TEXT NOT NULL REFERENCES capability(code),
    scope         TEXT NOT NULL DEFAULT 'UNIT',
    valid_from    TEXT,
    valid_to      TEXT
);
CREATE INDEX idx_poscap ON position_capability(position_definition_id);

-- 决策留痕（Event Sourcing）。重要事实不可静默覆盖：
-- 项目投资 8亿 → 10亿 → 9.4亿，每一次变化都要留下。
-- 十年后审计、干部考察、责任追溯，读的就是这张表。
CREATE TABLE decision_log (
    id            INTEGER PRIMARY KEY,
    event_date    TEXT NOT NULL,
    matter_id     INTEGER REFERENCES work_item(id),
    project_id    INTEGER REFERENCES project(id),
    meeting_id    INTEGER REFERENCES meeting(id),
    actor_id      INTEGER REFERENCES character(id),
    actor_position_id INTEGER REFERENCES position_slot(id),
    action_type   TEXT NOT NULL,
    field         TEXT,
    before_value  TEXT,
    after_value   TEXT,
    reason        TEXT
);
CREATE INDEX idx_declog ON decision_log(project_id, event_date);

-- 玩家做过的事。§42 决策留痕：以后审计、巡视、调查可以追溯。
CREATE TABLE action_log (
    id            INTEGER PRIMARY KEY,
    date          TEXT NOT NULL,
    character_id  INTEGER NOT NULL REFERENCES character(id),
    action_type   TEXT NOT NULL,
    target        TEXT,
    subject       TEXT,
    method        TEXT,
    outcome       TEXT,
    work_item_id  INTEGER REFERENCES work_item(id)
);

-- §40 人际关系用图结构。注意文档的要求：不要直接写"派系"。
-- 记的是熟悉程度和工作信任，两个独立的量：
-- 天天见面不等于办事托得住，托得住也不等于私交好。
CREATE TABLE relationship (
    id            INTEGER PRIMARY KEY,
    character_a   INTEGER NOT NULL REFERENCES character(id),
    character_b   INTEGER NOT NULL REFERENCES character(id),
    type          TEXT NOT NULL,      -- 同事/上下级/党校同学/大学同学/老乡/mentor/合作关系
    familiarity   INTEGER NOT NULL DEFAULT 0,   -- 0-100 熟悉程度
    working_trust INTEGER NOT NULL DEFAULT 0,   -- 0-100 工作上托不托得住
    last_contact  TEXT,
    history       TEXT,
    UNIQUE(character_a, character_b)
);

-- §41 重大项目。可能跨越多年，状态机自己走。
-- 项目是长期对象，不是一次性任务。
--
-- "项目完成：政绩 +20"这种写法把十几个互相冲突的维度压成了一个数。
-- 现实里一个项目可以进度快但成本失控，可以建成了但后续运营是个包袱，
-- 可以程序合规但群众不满意。完工之后还会有审计、后评价、质量问题、
-- 运营成本、群众投诉、整改、责任追溯。
CREATE TABLE project (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    organization_id INTEGER REFERENCES organization(id),
    region        TEXT,
    state         TEXT NOT NULL DEFAULT 'PROPOSED',
    scale         INTEGER NOT NULL DEFAULT 1,     -- 体量，越大牵涉越广
    risk          INTEGER NOT NULL DEFAULT 0,     -- 0-100，埋下问题的概率
    proposed_date TEXT NOT NULL,
    closed_date   TEXT,
    outcome       TEXT,

    -- 地域：任何项目至少绑定到县一级，"红山县第二人民医院迁建工程"
    -- 必须知道它在哪个省、哪个市、哪个县。
    region_org_id INTEGER REFERENCES organization(id),
    location      TEXT,
    -- 领域：交通/水利/能源/医疗/教育/住房/城市更新/产业园区/生态环保/
    --       数字基础设施/农业农村/文化旅游/公共安全/应急能力/科技创新/社会服务
    policy_domain TEXT,
    investment    INTEGER,          -- 万元。改一次留一次痕，不静默覆盖。

    -- 十二项结果指标。不能只有一个"政绩值"。
    progress      INTEGER NOT NULL DEFAULT 0,
    cost_control  INTEGER NOT NULL DEFAULT 50,
    quality       INTEGER NOT NULL DEFAULT 50,
    safety        INTEGER NOT NULL DEFAULT 50,
    procedure_compliance INTEGER NOT NULL DEFAULT 50,
    fiscal_pressure INTEGER NOT NULL DEFAULT 50,
    social_effect INTEGER NOT NULL DEFAULT 50,
    ecological_effect INTEGER NOT NULL DEFAULT 50,
    coordination  INTEGER NOT NULL DEFAULT 50,
    audit_risk    INTEGER NOT NULL DEFAULT 0,
    integrity_risk INTEGER NOT NULL DEFAULT 0,
    operation_burden INTEGER NOT NULL DEFAULT 0
);

-- §42 决策留痕。以后审计、巡视、调查可以追溯到具体的人。
-- 这张表是纪律系统的证据来源：十年后查起来，签字的是谁一目了然。
CREATE TABLE project_decision (
    id            INTEGER PRIMARY KEY,
    project_id    INTEGER NOT NULL REFERENCES project(id),
    role          TEXT NOT NULL,      -- 提出/批准/签批/实施/监督
    character_id  INTEGER NOT NULL REFERENCES character(id),
    date          TEXT NOT NULL,
    note          TEXT
);

-- §43 纪律。取消"廉政值"，记的是具体行为。
-- §44 违规与发现分离：behavior_date 是发生的时候，discovered_date 是被发现的时候，
-- 这两个日期可以差十年。没被发现之前，这个人的档案是干净的。
CREATE TABLE conduct_record (
    id            INTEGER PRIMARY KEY,
    character_id  INTEGER NOT NULL REFERENCES character(id),
    behavior      TEXT NOT NULL,
    severity      TEXT NOT NULL,      -- 轻微/一般/严重
    behavior_date TEXT NOT NULL,
    project_id    INTEGER REFERENCES project(id),
    evidence      INTEGER NOT NULL DEFAULT 0,  -- 线索强度，决定查得出来查不出来
    discovered_date TEXT,             -- 空 = 还没人知道
    discovered_by TEXT,               -- 审计/巡视/信访/换届考察
    status        TEXT NOT NULL DEFAULT 'LATENT',  -- LATENT/CLUE/UNDER_REVIEW/CLOSED
    closed_date   TEXT,               -- 案件了结日期，与发现日期不是一回事
    result        TEXT                -- 了结方式：谈话提醒/警告/严重警告/撤职/开除
);

-- 蓝图二十三：中央委员会体系**独立于行政级别**。
-- 一个省部级干部可以不是中央委员；一个中央委员也不等于"比谁高一级"。
-- 所以这是一张单独的表，和 office_holding 并行，人物界面上两样都要显示。
CREATE TABLE party_central_status (
    id            INTEGER PRIMARY KEY,
    character_id  INTEGER NOT NULL REFERENCES character(id),
    status        TEXT NOT NULL,      -- 中央候补委员/中央委员/政治局委员/政治局常委/总书记
    congress      INTEGER NOT NULL,   -- 第几次全国代表大会
    start_date    TEXT NOT NULL,
    end_date      TEXT
);

-- 会议（蓝图十七）。到了县级主要领导以上，玩法不再是个人办事，
-- 而是"在一定程序中提出、协调、讨论和形成决定"。
CREATE TABLE meeting (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,      -- 党委常委会/政府常务会议/党组会议/专题会议
    organization_id INTEGER REFERENCES organization(id),
    date          TEXT NOT NULL,
    chair_id      INTEGER REFERENCES character(id),
    attendees     TEXT                -- 与会人 id，JSON
);

CREATE TABLE meeting_item (
    id            INTEGER PRIMARY KEY,
    meeting_id    INTEGER NOT NULL REFERENCES meeting(id),
    topic         TEXT NOT NULL,
    source        TEXT,               -- project / appointment / work_item
    source_id     INTEGER,
    proposer_id   INTEGER REFERENCES character(id),
    decision      TEXT,               -- 同意/原则同意/再研究/缓议/不同意
    note          TEXT,
    -- ── 议程（V3 §10 meeting_agenda）──────────────
    matter_id     INTEGER REFERENCES work_item(id),
    proposer_org_id INTEGER REFERENCES organization(id),
    reporter_id   INTEGER REFERENCES character(id),   -- 谁上会汇报
    sequence_no   INTEGER,
    status        TEXT NOT NULL DEFAULT 'PENDING',
    -- ── 决定（V3 §10 meeting_decision）────────────
    -- 决定不是一个字段就完了：定了谁去办、几天内办完，
    -- 系统据此生成新的事项和督办，这才叫闭环。
    decision_text TEXT,
    responsible_org_id INTEGER REFERENCES organization(id),
    deadline_date TEXT
);

-- 参会身份。人在会场不等于列席，列席不等于会议成员。
--
--   CHAIR     主持，控制议程，形成会议处理意见
--   MEMBER    正式成员，讨论并参与决定
--   ATTENDEE  列席，按议题参加，可以说明情况，不取得成员身份
--   REPORTER  汇报议题、回答问题
--   STAFF     会务、材料、记录、纪要，无成员权利
--   OBSERVER  特定规则下旁听
CREATE TABLE meeting_participant (
    id            INTEGER PRIMARY KEY,
    meeting_id    INTEGER NOT NULL REFERENCES meeting(id),
    character_id  INTEGER NOT NULL REFERENCES character(id),
    position_slot_id INTEGER REFERENCES position_slot(id),
    role          TEXT NOT NULL,
    agenda_scope  TEXT              -- 只为某个议题而来的，写在这里
);
CREATE INDEX idx_mpart ON meeting_participant(meeting_id, role);

-- 换届：党代会与人代会各有周期，届次是真实资源，不是随机刷新。
CREATE TABLE term_session (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,      -- PARTY_CONGRESS / PEOPLES_CONGRESS
    organization_id INTEGER REFERENCES organization(id),
    ordinal       INTEGER NOT NULL,   -- 第几届
    held_date     TEXT NOT NULL
);

-- §28 未来已知事项：任期结束、退休、培训开班、年度考核、换届、制度改革
CREATE TABLE scheduled_event (
    id            INTEGER PRIMARY KEY,
    due_date      TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    character_id  INTEGER REFERENCES character(id),
    position_slot_id INTEGER REFERENCES position_slot(id),
    data          TEXT,
    fired         INTEGER NOT NULL DEFAULT 0
);

-- §36 组织视野。是"组织掌握到什么程度"，不是"组织部好感度"。
CREATE TABLE organization_attention (
    character_id  INTEGER PRIMARY KEY REFERENCES character(id),
    authority_id  INTEGER REFERENCES cadre_management_authority(id),
    visibility    INTEGER NOT NULL DEFAULT 0,      -- 0 未进入视野
    development_status TEXT NOT NULL DEFAULT 'NORMAL',  -- §37 允许降级
    last_review   TEXT
);

-- §65 所有重要变化落这张表：时间线/审计/存档修复都靠它
CREATE TABLE world_event (
    id            INTEGER PRIMARY KEY,
    date          TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    actors        TEXT,
    organizations TEXT,
    data          TEXT,
    visibility    TEXT NOT NULL DEFAULT 'PUBLIC'
);

CREATE INDEX idx_holding_char ON office_holding(character_id);
CREATE INDEX idx_slot_org ON position_slot(organization_id);
CREATE INDEX idx_event_date ON world_event(date);
CREATE INDEX idx_sched_due ON scheduled_event(due_date, fired);
CREATE INDEX idx_rank_char ON rank_holding(character_id);
CREATE INDEX idx_slot_status ON position_slot(status, position_definition_id);
CREATE INDEX idx_work_assignee ON work_item(assignee_id, state);
CREATE INDEX idx_rel_a ON relationship(character_a);
CREATE INDEX idx_rel_b ON relationship(character_b);
CREATE INDEX idx_conduct_char ON conduct_record(character_id, status);
CREATE INDEX idx_decision_proj ON project_decision(project_id);
CREATE INDEX idx_enroll_char ON training_enrollment(character_id, completed);
CREATE INDEX idx_central_char ON party_central_status(character_id, end_date);
CREATE INDEX idx_mitem_meeting ON meeting_item(meeting_id);
"""


def title_of(con, slot_id):
    """岗位的完整称谓，写入履历时定格（§26 历史称谓不被后来的制度改写）。

    用简称作前缀：中共红山县委 + 书记 = 中共红山县委书记。
    """
    return con.execute(
        "SELECT COALESCE(o.short_name, o.name) || d.name FROM position_slot s "
        "JOIN organization o ON o.id = s.organization_id "
        "JOIN position_definition d ON d.id = s.position_definition_id "
        "WHERE s.id = ?", (slot_id,)).fetchone()[0]


def open_world(path=":memory:"):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
