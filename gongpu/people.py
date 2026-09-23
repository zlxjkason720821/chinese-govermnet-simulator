"""人物生成（技术文档 §74）。

硬约束：背景必须年代正确。
禁止出现 1986 年 22 岁的人拥有 2020 年代典型教育履历。
所以学历、入党年龄、干部来源全部按出生队列抽样，不是一张全局表。
"""
from datetime import date

SURNAMES = ("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾"
            "肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦")

# 用字带年代色彩，且分性别：50 年代生的不叫子轩，男干部不叫秀兰。
GIVEN_BY_COHORT = (
    (1900, 1945, "国建军民忠正德文武成明华兴邦振永清远山河",
                 "秀兰英淑珍桂芝凤莲玉梅华贞琴云翠芳"),
    (1946, 1962, "建军红卫东兵国庆援朝跃进学工农伟利新春",
                 "红卫梅英兰芳玲丽华秀娟凤霞淑琴桂萍"),
    (1963, 1978, "晓小志勇强伟刚磊鹏涛峰勤斌军辉健",
                 "敏静丽芳燕平娟红梅霞玲艳萍洁莉"),
    (1979, 1995, "宇轩浩然子博文昊鑫嘉阳睿彬扬帆",
                 "佳欣怡婷雨晨璐倩雪琳静怡妍蕾"),
    (1996, 2100, "梓轩浩宇子睿泽宸沐辰瑞逸航骏",
                 "梓涵欣怡诗琪雨萱一诺芊语晗歆玥"),
)

# §74 学历结构按出生队列变化。(概率, 学历)，同队列内归一。
EDUCATION_BY_COHORT = (
    (1900, 1945, ((0.45, "高中"), (0.35, "中专"), (0.15, "大专"), (0.05, "本科"))),
    (1946, 1962, ((0.20, "高中"), (0.35, "中专"), (0.30, "大专"), (0.15, "本科"))),
    (1963, 1975, ((0.05, "高中"), (0.20, "中专"), (0.40, "大专"), (0.33, "本科"), (0.02, "硕士"))),
    (1976, 1988, ((0.10, "中专"), (0.25, "大专"), (0.55, "本科"), (0.10, "硕士"))),
    (1989, 2100, ((0.10, "大专"), (0.62, "本科"), (0.25, "硕士"), (0.03, "博士"))),
)

# §32 性格只影响行为概率，不作为面板数值展示给玩家
TRAITS = ("risk_tolerance", "work_discipline", "ambition", "cooperation",
          "public_orientation", "procedural_preference", "innovation",
          "stress_resistance")


def _cohort(table, year):
    for row in table:
        if row[0] <= year <= row[1]:
            return row[2] if len(row) == 3 else row[2:]
    raise ValueError(f"无队列覆盖的出生年份：{year}")


def _weighted(rng, pairs):
    total = sum(w for w, _ in pairs)
    x = rng.random() * total
    for w, v in pairs:
        x -= w
        if x < 0:
            return v
    return pairs[-1][1]


def make_name(rng, birth_year, gender="M"):
    male, female = _cohort(GIVEN_BY_COHORT, birth_year)
    chars = male if gender == "M" else female
    n = 1 if rng.random() < 0.35 else 2
    return rng.choice(SURNAMES) + "".join(rng.choice(chars) for _ in range(n))


def work_start_year(education, birth_year):
    """参加工作年份由学历倒推，不是随便给一个资历数字。"""
    years = {"高中": 18, "中专": 20, "大专": 22, "本科": 22, "硕士": 25, "博士": 28}
    return birth_year + years[education]


def generate(rng, birth_year, on, career_origin="ORDINARY", gender=None):
    """rng 必须是 world 流。返回可直接写库的 dict。"""
    gender = gender or ("F" if rng.random() < 0.25 else "M")
    education = _weighted(rng, _cohort(EDUCATION_BY_COHORT, birth_year))
    # 选调生按定义是应届大学毕业生，学历不能低于大专
    if career_origin == "SELECTED_GRADUATE" and education in ("高中", "中专"):
        education = "本科" if birth_year >= 1960 else "大专"
    ws = work_start_year(education, birth_year)
    # 入党：参加工作前后若干年，不是出生就入党
    join = ws + int(rng.random() * 8) - 2
    member = join <= on.year and rng.random() < (0.75 if career_origin == "SELECTED_GRADUATE" else 0.55)
    return {
        "name": make_name(rng, birth_year, gender),
        "gender": gender,
        "birth_date": date(birth_year, 1 + int(rng.random() * 12),
                           1 + int(rng.random() * 28)).isoformat(),
        "party_status": "MEMBER" if member else "NONE",
        "party_join_date": date(max(join, birth_year + 18), 7, 1).isoformat() if member else None,
        "education_level": education,
        "career_origin": career_origin,
        "work_start_date": date(ws, 7, 1).isoformat(),
        "personality": {t: round(rng.random(), 3) for t in TRAITS},
    }
