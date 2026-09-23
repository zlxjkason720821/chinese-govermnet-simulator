"""调度器（技术文档 §28）。

未来已知的事情先登记，时间到了自动发生。
时间推进不是改日期（§29）：推进跨过的每一天都要把到期事项跑掉。
"""
import json
from datetime import date


def schedule(con, due_date, event_type, character_id=None, slot_id=None, data=None):
    con.execute("INSERT INTO scheduled_event(due_date,event_type,character_id,"
                "position_slot_id,data) VALUES(?,?,?,?,?)",
                (due_date.isoformat() if isinstance(due_date, date) else due_date,
                 event_type, character_id, slot_id,
                 json.dumps(data or {}, ensure_ascii=False)))


def due(con, on):
    """到期未触发的事项，按日期排序保证可复现。"""
    return con.execute(
        "SELECT * FROM scheduled_event WHERE fired=0 AND due_date<=? "
        "ORDER BY due_date, id", (on.isoformat(),)).fetchall()


def fire(con, event_id):
    con.execute("UPDATE scheduled_event SET fired=1 WHERE id=?", (event_id,))
