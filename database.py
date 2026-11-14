import sqlite3
import json
from datetime import datetime
from config import DB_PATH
import os


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs("data", exist_ok=True)
    conn = get_conn()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        nickname TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        notes TEXT DEFAULT ''
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS contact_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_name TEXT UNIQUE NOT NULL,
        personality_tags TEXT DEFAULT '[]',
        interests TEXT DEFAULT '[]',
        communication_style TEXT DEFAULT '',
        key_info TEXT DEFAULT '{}',
        mbti TEXT DEFAULT '',
        love_language TEXT DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS relationship_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_name TEXT UNIQUE NOT NULL,
        stage TEXT DEFAULT '陌生人',
        ambiguity_percent INTEGER DEFAULT 0,
        interest_score REAL DEFAULT 0.0,
        momentum TEXT DEFAULT 'neutral',
        last_analysis TEXT DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_name TEXT NOT NULL,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        technique_used TEXT DEFAULT '',
        ioi_signals TEXT DEFAULT '[]',
        iod_signals TEXT DEFAULT '[]'
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS metrics_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_name TEXT NOT NULL,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
        ambiguity_percent INTEGER DEFAULT 0,
        interest_score REAL DEFAULT 0.0,
        relationship_stage TEXT DEFAULT ''
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS feedback_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_name TEXT NOT NULL,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
        message_id INTEGER,
        reply_option TEXT DEFAULT '',
        feedback_type TEXT DEFAULT '',
        feedback_score INTEGER DEFAULT 0,
        actual_reply TEXT DEFAULT '',
        outcome TEXT DEFAULT '',
        notes TEXT DEFAULT ''
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS developer_style_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_type TEXT NOT NULL,
        rule_content TEXT NOT NULL,
        priority INTEGER DEFAULT 5,
        source TEXT DEFAULT 'manual',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        active INTEGER DEFAULT 1
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS case_library (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        scenario TEXT NOT NULL,
        her_message TEXT DEFAULT '',
        my_reply TEXT NOT NULL,
        technique_tags TEXT DEFAULT '[]',
        outcome TEXT DEFAULT '',
        rating INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS knowledge_base (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        title TEXT DEFAULT '',
        content TEXT NOT NULL,
        tags TEXT DEFAULT '[]',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS advisor_chat (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_name TEXT DEFAULT '',
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
        role TEXT NOT NULL,
        content TEXT NOT NULL
    )''')

    # 修复旧数据：确保所有 contacts 在 profile/state 表里都有对应行
    c.execute("SELECT name FROM contacts")
    for (name,) in c.fetchall():
        c.execute("INSERT OR IGNORE INTO contact_profiles (contact_name) VALUES (?)", (name,))
        c.execute("INSERT OR IGNORE INTO relationship_state (contact_name) VALUES (?)", (name,))

    conn.commit()
    conn.close()


# ─── Contacts ────────────────────────────────────────────────

def add_contact(name, nickname=""):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO contacts (name, nickname) VALUES (?, ?)", (name, nickname))
        conn.execute("INSERT OR IGNORE INTO contact_profiles (contact_name) VALUES (?)", (name,))
        conn.execute("INSERT OR IGNORE INTO relationship_state (contact_name) VALUES (?)", (name,))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def delete_contact(name):
    conn = get_conn()
    conn.execute("DELETE FROM contacts WHERE name = ?", (name,))
    conn.execute("DELETE FROM contact_profiles WHERE contact_name = ?", (name,))
    conn.execute("DELETE FROM relationship_state WHERE contact_name = ?", (name,))
    conn.execute("DELETE FROM chat_history WHERE contact_name = ?", (name,))
    conn.execute("DELETE FROM metrics_history WHERE contact_name = ?", (name,))
    conn.execute("DELETE FROM feedback_logs WHERE contact_name = ?", (name,))
    conn.execute("DELETE FROM advisor_chat WHERE contact_name = ?", (name,))
    conn.commit()
    conn.close()


def get_contact(name):
    conn = get_conn()
    row = conn.execute(
        '''SELECT c.id, c.name, c.nickname, c.created_at,
                  cp.personality_tags, cp.interests, cp.communication_style,
                  cp.key_info, cp.mbti, cp.love_language,
                  rs.stage, rs.ambiguity_percent, rs.interest_score,
                  rs.momentum, rs.last_analysis
           FROM contacts c
           LEFT JOIN contact_profiles cp ON c.name = cp.contact_name
           LEFT JOIN relationship_state rs ON c.name = rs.contact_name
           WHERE c.name = ?''', (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_contacts():
    conn = get_conn()
    rows = conn.execute(
        '''SELECT c.name, c.nickname, c.created_at,
                  COALESCE(rs.stage, '陌生人') as stage,
                  COALESCE(rs.ambiguity_percent, 0) as ambiguity_percent,
                  COALESCE(rs.interest_score, 0.0) as interest_score,
                  COALESCE(rs.momentum, 'neutral') as momentum
           FROM contacts c
           LEFT JOIN relationship_state rs ON c.name = rs.contact_name
           ORDER BY c.created_at DESC'''
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_contact_profile(name, **kwargs):
    if not kwargs:
        return
    conn = get_conn()
    for k, v in kwargs.items():
        if isinstance(v, (list, dict)):
            kwargs[k] = json.dumps(v, ensure_ascii=False)
    kwargs['updated_at'] = datetime.now().isoformat()
    fields = ", ".join([f"{k} = ?" for k in kwargs])
    conn.execute(f"UPDATE contact_profiles SET {fields} WHERE contact_name = ?",
                 list(kwargs.values()) + [name])
    conn.commit()
    conn.close()


def update_relationship_state(name, **kwargs):
    if not kwargs:
        return
    conn = get_conn()
    kwargs['updated_at'] = datetime.now().isoformat()
    fields = ", ".join([f"{k} = ?" for k in kwargs])
    conn.execute(f"UPDATE relationship_state SET {fields} WHERE contact_name = ?",
                 list(kwargs.values()) + [name])
    conn.commit()
    conn.close()


# ─── Chat History ────────────────────────────────────────────

def save_message(contact_name, role, content, technique_used="", ioi_signals=None, iod_signals=None, timestamp=None):
    conn = get_conn()
    if timestamp:
        # 有时间戳：同一联系人+时间戳+内容已存在则跳过，防止重复导入
        exists = conn.execute(
            "SELECT 1 FROM chat_history WHERE contact_name=? AND timestamp=? AND content=?",
            (contact_name, timestamp, content)
        ).fetchone()
        if exists:
            conn.close()
            return
        conn.execute(
            '''INSERT INTO chat_history (contact_name, timestamp, role, content, technique_used, ioi_signals, iod_signals)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (contact_name, timestamp, role, content, technique_used,
             json.dumps(ioi_signals or [], ensure_ascii=False),
             json.dumps(iod_signals or [], ensure_ascii=False))
        )
    else:
        conn.execute(
            '''INSERT INTO chat_history (contact_name, role, content, technique_used, ioi_signals, iod_signals)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (contact_name, role, content, technique_used,
             json.dumps(ioi_signals or [], ensure_ascii=False),
             json.dumps(iod_signals or [], ensure_ascii=False))
        )
    conn.commit()
    conn.close()


def get_chat_history(contact_name, limit=50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM chat_history WHERE contact_name = ? ORDER BY timestamp DESC LIMIT ?",
        (contact_name, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def get_latest_chat_timestamp(contact_name: str) -> str | None:
    """返回该联系人 chat_history 中最大的 timestamp 字符串；无历史返回 None。只读，不影响其它逻辑。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(timestamp) as ts FROM chat_history WHERE contact_name = ?",
        (contact_name,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    ts = row["ts"]
    return ts if ts else None


def get_daily_message_counts(contact_name: str) -> list:
    """按天统计消息量，返回 [{"date": "2026-04-13", "her": 5, "me": 3, "total": 8}, ...]"""
    conn = get_conn()
    rows = conn.execute(
        """SELECT substr(timestamp, 1, 10) as date, role, COUNT(*) as cnt
           FROM chat_history
           WHERE contact_name = ?
           GROUP BY date, role
           ORDER BY date ASC""",
        (contact_name,)
    ).fetchall()
    conn.close()

    from collections import defaultdict
    day_map = defaultdict(lambda: {"her": 0, "me": 0})
    for row in rows:
        day_map[row["date"]][row["role"]] += row["cnt"]

    result = []
    for date in sorted(day_map.keys()):
        d = day_map[date]
        result.append({
            "date": date,
            "her": d["her"],
            "me": d["me"],
            "total": d["her"] + d["me"],
        })
    return result


# ─── Metrics ─────────────────────────────────────────────────

def save_metrics(contact_name, ambiguity, interest, stage, timestamp=None):
    conn = get_conn()
    if timestamp:
        conn.execute(
            '''INSERT INTO metrics_history (contact_name, timestamp, ambiguity_percent, interest_score, relationship_stage)
               VALUES (?, ?, ?, ?, ?)''',
            (contact_name, timestamp, ambiguity, interest, stage)
        )
    else:
        conn.execute(
            '''INSERT INTO metrics_history (contact_name, ambiguity_percent, interest_score, relationship_stage)
               VALUES (?, ?, ?, ?)''',
            (contact_name, ambiguity, interest, stage)
        )
    conn.commit()
    conn.close()


def get_metrics_history(contact_name):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM metrics_history WHERE contact_name = ? ORDER BY timestamp ASC",
        (contact_name,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Feedback ────────────────────────────────────────────────

def save_feedback(contact_name, feedback_score, message_id=None, reply_option="",
                  feedback_type="", actual_reply="", outcome="", notes=""):
    conn = get_conn()
    conn.execute(
        '''INSERT INTO feedback_logs
           (contact_name, message_id, reply_option, feedback_type, feedback_score, actual_reply, outcome, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (contact_name, message_id, reply_option, feedback_type,
         feedback_score, actual_reply, outcome, notes)
    )
    conn.commit()
    conn.close()


def get_feedback_logs(contact_name=None, limit=100):
    conn = get_conn()
    if contact_name:
        rows = conn.execute(
            "SELECT * FROM feedback_logs WHERE contact_name = ? ORDER BY timestamp DESC LIMIT ?",
            (contact_name, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM feedback_logs ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Style Rules ─────────────────────────────────────────────

def add_style_rule(rule_type, rule_content, priority=5, source="manual"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO developer_style_rules (rule_type, rule_content, priority, source) VALUES (?, ?, ?, ?)",
        (rule_type, rule_content, priority, source)
    )
    conn.commit()
    conn.close()


def get_style_rules(rule_type=None, active_only=True):
    conn = get_conn()
    conditions = []
    params = []
    if active_only:
        conditions.append("active = 1")
    if rule_type:
        conditions.append("rule_type = ?")
        params.append(rule_type)
    query = "SELECT * FROM developer_style_rules"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY priority DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Case Library ────────────────────────────────────────────

def add_case(title, scenario, my_reply, her_message="", technique_tags=None, outcome="", rating=0):
    conn = get_conn()
    conn.execute(
        '''INSERT INTO case_library (title, scenario, her_message, my_reply, technique_tags, outcome, rating)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (title, scenario, her_message, my_reply,
         json.dumps(technique_tags or [], ensure_ascii=False), outcome, rating)
    )
    conn.commit()
    conn.close()


def get_cases(technique_tag=None, limit=20):
    conn = get_conn()
    if technique_tag:
        rows = conn.execute(
            "SELECT * FROM case_library WHERE technique_tags LIKE ? ORDER BY rating DESC LIMIT ?",
            (f'%{technique_tag}%', limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM case_library ORDER BY rating DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Knowledge Base ──────────────────────────────────────────

def add_knowledge(category, content, title="", tags=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO knowledge_base (category, title, content, tags) VALUES (?, ?, ?, ?)",
        (category, title, content, json.dumps(tags or [], ensure_ascii=False))
    )
    conn.commit()
    conn.close()


def get_knowledge(category=None):
    conn = get_conn()
    if category:
        rows = conn.execute("SELECT * FROM knowledge_base WHERE category = ?", (category,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM knowledge_base").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Advisor Chat ────────────────────────────────────────────

def save_advisor_message(contact_name, role, content):
    conn = get_conn()
    conn.execute(
        "INSERT INTO advisor_chat (contact_name, role, content) VALUES (?, ?, ?)",
        (contact_name, role, content)
    )
    conn.commit()
    conn.close()


def get_advisor_history(contact_name):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM advisor_chat WHERE contact_name = ? ORDER BY timestamp ASC",
        (contact_name,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print("数据库初始化完成")
