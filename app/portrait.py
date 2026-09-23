"""程序化人像（证件照风格）。

不用任何外部图片素材：每个干部的脸由他自己的 id 决定，
同一个人任何时候打开都是同一张脸，换个存档也一样（§73 人一旦生成就不再变）。

发型、眼镜、衣着按性别、年龄和年代变化——1986 年的县委书记不穿西装打领带。
"""
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QLinearGradient, QPainter, QPen,
                           QPainterPath, QPixmap, QPolygonF)

# 证件照底色
BACKDROPS = ["#c8d8e8", "#b9cde0", "#d6dfe6", "#cdd9e5"]
SKIN = ["#e8c4a0", "#e0b894", "#d8ab86", "#eccdae", "#d2a077"]
HAIR = ["#2a2420", "#1d1a17", "#3a3128", "#4a4038"]
GRAY = ["#8a8580", "#9a948d", "#b0aaa3"]

# 上衣：年代不同，穿的不一样
MALE_WEAR = {
    "中山装": "#4a5259",
    "深色夹克": "#3c4147",
    "白衬衫": "#e9edf1",
    "西装": "#2f3640",
}
FEMALE_WEAR = {
    "深色外套": "#3f4650",
    "浅色衬衫": "#e6e9ee",
    "套装": "#46505c",
}


def _wear(gender, year, rnd):
    if gender == "M":
        if year < 1995:
            return rnd.choice(["中山装", "深色夹克", "白衬衫"])
        if year < 2005:
            return rnd.choice(["深色夹克", "白衬衫", "西装"])
        return rnd.choice(["白衬衫", "西装", "西装"])
    if year < 1998:
        return rnd.choice(["深色外套", "浅色衬衫"])
    return rnd.choice(["套装", "浅色衬衫", "深色外套"])


def portrait(character_id, gender="M", birth_year=1960, on_year=1986,
             education="高中", size=120):
    """返回一张 QPixmap。同样的参数永远画出同一张脸。"""
    rnd = random.Random(f"face:{character_id}")
    age = max(on_year - birth_year, 18)
    w, h = int(size * 0.78), size
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    # 背景
    g = QLinearGradient(0, 0, 0, h)
    base = QColor(rnd.choice(BACKDROPS))
    g.setColorAt(0, base.lighter(112))
    g.setColorAt(1, base.darker(106))
    p.fillRect(0, 0, w, h, QBrush(g))

    cx = w / 2
    skin = QColor(rnd.choice(SKIN))
    if age > 60:
        skin = skin.darker(104)

    # 肩与上衣
    wear_name = _wear(gender, on_year, rnd)
    wear = QColor((MALE_WEAR if gender == "M" else FEMALE_WEAR)[wear_name])
    p.setPen(Qt.NoPen)
    p.setBrush(wear)
    p.drawRoundedRect(QRectF(cx - w * 0.46, h * 0.78, w * 0.92, h * 0.30),
                      w * 0.14, w * 0.14)
    # 领口
    p.setBrush(wear.lighter(118))
    p.drawPolygon(QPolygonF([QPointF(cx - w * 0.14, h * 0.78),
                             QPointF(cx + w * 0.14, h * 0.78),
                             QPointF(cx, h * 0.92)]))
    if gender == "M" and wear_name == "西装":
        p.setBrush(QColor("#8c2f33"))          # 领带
        p.drawPolygon(QPolygonF([QPointF(cx - w * 0.035, h * 0.84),
                                 QPointF(cx + w * 0.035, h * 0.84),
                                 QPointF(cx, h * 1.0)]))

    # 脖子
    p.setBrush(skin.darker(112))
    p.drawRoundedRect(QRectF(cx - w * 0.135, h * 0.63, w * 0.27, h * 0.19),
                      w * 0.04, w * 0.04)

    # 脸。脸型宽窄因人而异，否则一屋子人长得一模一样。
    fw = w * (0.46 + rnd.random() * 0.10)
    fh = h * (0.43 + rnd.random() * 0.06)
    face = QRectF(cx - fw / 2, h * 0.21, fw, fh)

    hair_color = QColor(rnd.choice(GRAY) if (age > 55 and rnd.random() < 0.6)
                        else rnd.choice(HAIR))
    balding = gender == "M" and age > 48 and rnd.random() < 0.40
    style = (rnd.choice(["背头", "分头", "平头"]) if gender == "M"
             else rnd.choice(["短发", "盘发", "齐耳"]))

    # 先画发型的体积（后脑与两鬓），再把脸盖上去，头发就只剩一圈轮廓
    p.setPen(Qt.NoPen)
    p.setBrush(hair_color)
    if gender == "M":
        grow = 0.04 if not balding else 0.015
        p.drawEllipse(QRectF(face.x() - fw * grow, face.y() - fh * (0.10 if not balding else 0.02),
                             fw * (1 + grow * 2), fh * 0.78))
    else:
        if style == "盘发":
            p.drawEllipse(QPointF(cx, face.y() - fh * 0.06), fw * 0.19, fh * 0.15)
            p.drawEllipse(QRectF(face.x() - fw * 0.09, face.y() - fh * 0.13,
                                 fw * 1.18, fh * 0.92))
        else:
            # 垂到下颌以下：远看就能和男干部区分开
            drop = 1.34 if style == "短发" else 1.20
            p.drawRoundedRect(QRectF(face.x() - fw * 0.15, face.y() - fh * 0.14,
                                     fw * 1.30, fh * drop), fw * 0.30, fw * 0.30)

    p.setBrush(skin)
    p.drawEllipse(face)

    # 刘海／发际线：裁在脸的范围内画，绝不糊到脸颊上
    path = QPainterPath()
    path.addEllipse(face)
    p.save()
    p.setClipPath(path)
    p.setBrush(hair_color)
    top = face.y()
    if gender == "M":
        if balding:
            # 发际线后移，只剩两鬓
            p.drawEllipse(QRectF(face.x() - fw * 0.05, top - fh * 0.06, fw * 0.34, fh * 0.42))
            p.drawEllipse(QRectF(face.right() - fw * 0.29, top - fh * 0.06, fw * 0.34, fh * 0.42))
        elif style == "背头":
            p.drawEllipse(QRectF(face.x() - fw * 0.05, top - fh * 0.22, fw * 1.1, fh * 0.48))
        elif style == "平头":
            p.drawRect(QRectF(face.x() - 1, top - 1, fw + 2, fh * 0.26))
        else:                                   # 分头：一道细分缝，不是一块秃斑
            p.drawEllipse(QRectF(face.x() - fw * 0.05, top - fh * 0.18, fw * 1.1, fh * 0.46))
            p.setPen(QPen(skin, max(w / 42, 1.6)))
            p.drawLine(QPointF(face.x() + fw * 0.62, top + fh * 0.02),
                       QPointF(face.x() + fw * 0.76, top + fh * 0.22))
            p.setPen(Qt.NoPen)
    else:
        if style == "齐耳":
            p.drawRect(QRectF(face.x() - 1, top - 1, fw + 2, fh * 0.30))
        else:
            p.drawEllipse(QRectF(face.x() - fw * 0.06, top - fh * 0.16, fw * 1.12, fh * 0.46))
    p.restore()

    # 眉眼
    eye_y = face.y() + fh * 0.54
    dx = fw * 0.20
    p.setPen(QPen(QColor("#33302c"), max(size / 70, 1.2)))
    p.setBrush(Qt.NoBrush)
    brow = fh * 0.10
    for s in (-1, 1):
        p.drawLine(QPointF(cx + s * dx - fw * 0.09, eye_y - brow),
                   QPointF(cx + s * dx + fw * 0.09, eye_y - brow * 1.25))
    p.setBrush(QColor("#2b2724"))
    p.setPen(Qt.NoPen)
    for s in (-1, 1):
        p.drawEllipse(QPointF(cx + s * dx, eye_y), fw * 0.052, fh * 0.035)

    # 鼻、嘴
    p.setPen(QPen(skin.darker(125), max(size / 90, 1.0)))
    p.drawLine(QPointF(cx, eye_y + fh * 0.06), QPointF(cx - fw * 0.04, eye_y + fh * 0.19))
    p.setPen(QPen(QColor("#9c6a60"), max(size / 75, 1.2)))
    p.drawLine(QPointF(cx - fw * 0.11, eye_y + fh * 0.32),
               QPointF(cx + fw * 0.11, eye_y + fh * 0.32))

    # 皱纹：上了年纪才有
    if age > 52:
        p.setPen(QPen(skin.darker(118), max(size / 110, 0.8)))
        for s in (-1, 1):
            p.drawLine(QPointF(cx + s * fw * 0.22, eye_y + fh * 0.10),
                       QPointF(cx + s * fw * 0.17, eye_y + fh * 0.26))

    # 眼镜：学历越高、年纪越大越可能戴
    chance = 0.22 + {"本科": 0.18, "硕士": 0.26, "博士": 0.30}.get(education, 0.0)
    if age > 50:
        chance += 0.15
    if rnd.random() < chance:
        p.setPen(QPen(QColor("#4a4a48"), max(size / 80, 1.1)))
        p.setBrush(Qt.NoBrush)
        r = fw * 0.15
        for s in (-1, 1):
            p.drawEllipse(QPointF(cx + s * dx, eye_y), r, r * 0.82)
        p.drawLine(QPointF(cx - dx + r, eye_y), QPointF(cx + dx - r, eye_y))

    p.end()
    return pm


def portrait_for(row, on_year, size=120):
    """直接吃一行 character 记录。"""
    return portrait(row["id"], row["gender"] or "M",
                    int(str(row["birth_date"])[:4]), on_year,
                    row["education_level"] or "高中", size)
