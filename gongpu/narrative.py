"""本地文本引擎（技术文档 §48-§52）。

文本只表达已经发生的世界事实（§6）：数据库先定下"人物 A 状态 =
ORGANIZATION_INSPECTION"，这里才把它写成一段可读的话。
反过来不行——文本不能决定"所以玩家升任县长"。

所有随机抽取走 text_rng（§51）。换一句措辞绝不能改变下个月谁升职。
"""
from datetime import date
from pathlib import Path

from gongpu import paths

import yaml
from jinja2 import Environment, StrictUndefined

DATA = paths.DATA
_ENV = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
_CACHE = {}


def _load():
    if not _CACHE:
        _CACHE.update(yaml.safe_load((DATA / "narrative.yaml").read_text(encoding="utf-8")))
    return _CACHE


def date_cn(d):
    return f"{d.year}年{d.month}月{d.day}日"


class TextEngine:
    def __init__(self, rng):
        # 只拿 text 流。拿错流就是 §51 说的那类严重 bug。
        self.rng = rng["text"]
        self.data = _load()

    def phrase(self, bank, **ctx):
        """从词库抽一句。同一个场景有几十种说法（§50）。"""
        options = self.data["phrases"][bank]
        picked = self.rng.choice(options)
        return _ENV.from_string(picked).render(**ctx) if "{{" in picked else picked

    def render(self, kind, on, **ctx):
        """把一条世界事实写成文本。没有对应模板就返回 None，让调用方自己决定。"""
        templates = self.data["templates"].get(kind)
        if not templates:
            return None
        source = self.rng.choice(templates)
        full = dict(ctx, date_cn=date_cn(on))
        # 只解析这条模板真正引用到的词库。全部预解析会让"通知谈话"这类
        # 需要额外上下文的词库在用不到它的模板里报错。
        for bank in self.data["phrases"]:
            if bank not in full and bank in source:
                full[bank] = self.phrase(bank, **ctx)
        return _ENV.from_string(source).render(**full).strip()
