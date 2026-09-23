"""制度版本与规则解析（技术文档 §22-§24）。

所有制度规则带 valid_from / valid_to，日期决定用哪一套。
模块里禁止散写 `if year >= 2019:`，一律问 RuleResolver。
"""
from datetime import date

FOREVER = date(9999, 12, 31)

# §24 Era 是规则集合，不是剧情章节。
# 起始日取制度生效日：1993 暂行条例 1993-10-01 施行，2006 公务员法 2006-01-01 施行。
ERAS = [
    # (era_id, valid_from, valid_to, flags)
    ("1986_REFORM",            date(1986, 1, 1),  date(1993, 9, 30),
     {"civil_service": None, "rank_parallel": False,
      "non_leadership_positions": False, "supervision_commission": False}),
    ("1993_CIVIL_SERVICE",     date(1993, 10, 1), date(2002, 7, 8),
     {"civil_service": "CivilServiceRules1993", "rank_parallel": False,
      "non_leadership_positions": True, "supervision_commission": False}),
    ("2002_APPOINTMENT",       date(2002, 7, 9),  date(2005, 12, 31),
     {"civil_service": "CivilServiceRules1993", "rank_parallel": False,
      "non_leadership_positions": True, "supervision_commission": False}),
    ("2006_CIVIL_SERVICE_LAW", date(2006, 1, 1),  date(2014, 1, 14),
     {"civil_service": "CivilServiceRules2006", "rank_parallel": False,
      "non_leadership_positions": True, "supervision_commission": False}),
    ("2014_APPOINTMENT",       date(2014, 1, 15), date(2018, 3, 19),
     {"civil_service": "CivilServiceRules2006", "rank_parallel": False,
      "non_leadership_positions": True, "supervision_commission": False}),
    ("2018_SUPERVISION",       date(2018, 3, 20), date(2019, 5, 31),
     {"civil_service": "CivilServiceRules2006", "rank_parallel": False,
      "non_leadership_positions": True, "supervision_commission": True}),
    ("2019_RANK",              date(2019, 6, 1),  date(2021, 12, 31),
     {"civil_service": "CivilServiceRules2019", "rank_parallel": True,
      "non_leadership_positions": False, "supervision_commission": True}),
    ("2022_UP_DOWN",           date(2022, 1, 1),  date(2025, 12, 31),
     {"civil_service": "CivilServiceRules2019", "rank_parallel": True,
      "non_leadership_positions": False, "supervision_commission": True}),
    ("2026_CURRENT",           date(2026, 1, 1),  date(2026, 12, 31),
     {"civil_service": "CivilServiceRules2019", "rank_parallel": True,
      "non_leadership_positions": False, "supervision_commission": True}),
    ("2027_FICTIONAL",         date(2027, 1, 1),  FOREVER,
     {"civil_service": "CivilServiceRules2019", "rank_parallel": True,
      "non_leadership_positions": False, "supervision_commission": True}),
]


class Ruleset:
    def __init__(self, era_id, flags):
        self.era_id = era_id
        self.flags = flags

    def __getattr__(self, name):
        try:
            return self.flags[name]
        except KeyError:
            raise AttributeError(name) from None

    def __repr__(self):
        return f"<Ruleset {self.era_id}>"


def resolve(d):
    """§23 RuleResolver：日期 -> 当前生效规则集。"""
    for era_id, lo, hi, flags in ERAS:
        if lo <= d <= hi:
            return Ruleset(era_id, flags)
    raise ValueError(f"无制度覆盖的日期：{d}（世界起点为 1986-01-01）")


# 退休年龄（1982 老干部退休制度确立，是 §19 空缺的主要来源）。
# 2025-01-01 起渐进式延迟退休：男每 4 个月 +1 月，女干部每 2 个月 +1 月。
DELAY_START = date(2025, 1, 1)


# 退休年龄按层次分档。这是真实制度，不是手感：
#   县处级及以下   男 60 女 55（县处级以上女干部 60）
#   厅局级         60
#   副部级         62
#   省部级正职     65（中管干部）
#   副国级以上     按七上八下的惯例，68
# 少了这一档，总书记 61 岁就会被"到龄退休"，整个上层每几年排空一次。
LEVEL_RETIREMENT = {11: 68, 10: 68, 9: 65, 8: 62, 7: 60, 6: 60, 5: 60, 4: 60}


def retirement_age(gender, level_order, on):
    """返回该干部的法定退休年龄（浮点年）。§23 年龄规则也必须走 Resolver。

    level_order 是 LEVEL_ORDER 里的序号；传 True/False 兼容旧调用
    （True 视为县处级）。
    """
    if level_order is True:
        level_order = 4
    elif level_order is False or level_order is None:
        level_order = 0
    fixed = LEVEL_RETIREMENT.get(level_order)
    if fixed is not None:
        base = float(fixed)
    else:
        base = 60.0 if gender == "M" else 55.0
    if on < DELAY_START:
        return base
    months = (on.year - 2025) * 12 + on.month - 1
    # ponytail: 按月线性外推，够用到 2026；真实方案分批次表，需要时换成查表
    step = months / 4 if (gender == "M" or level_order >= 4) else months / 2
    cap = base + 3.0
    return min(base + step / 12, cap)
