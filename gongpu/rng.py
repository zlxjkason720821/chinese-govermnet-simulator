"""统一随机源（技术文档 §10-§13）。

禁止任何模块直接调用 random.random()：否则存档无法复现。
文本随机与世界随机分流（§11），换一句措辞不能改变干部任免结果。
"""
import random

# §10 五条独立随机流
STREAMS = ("world", "career", "event", "governance", "text")


class RandomService:
    def __init__(self, seed, state=None):
        self.seed = seed
        # 各流从 seed 派生独立序列，互不干扰
        self.streams = {n: random.Random(f"{seed}:{n}") for n in STREAMS}
        if state is not None:
            self.load(state)

    def __getitem__(self, name):
        return self.streams[name]

    def dump(self):
        """§12 只存 seed 不够，必须存 RNG state 才能回到序列中间位置。"""
        return {"seed": self.seed,
                "streams": {n: r.getstate() for n, r in self.streams.items()}}

    def load(self, state):
        for n, s in state["streams"].items():
            # JSON 往返会把 tuple 变成 list，setstate 要求内层是 tuple
            version, keys, gauss = s
            self.streams[n].setstate((version, tuple(keys), gauss))
