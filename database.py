import aiosqlite
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

DB_PATH = "pr_lich.db"

# ==================== LEVELS CONFIG ====================
LEVELS = {
    1: {"name": "🐣 Новичок", "min_xp": 0, "max_xp": 499, "check_limit": 0, "ref_deposit": 10, "ref_tasks": 3},
    2: {"name": "🌱 Активист", "min_xp": 500, "max_xp": 1499, "check_limit": 200_000, "ref_deposit": 10, "ref_tasks": 3},
    3: {"name": "🌟 Мастер заданий", "min_xp": 1500, "max_xp": 4999, "check_limit": 500_000, "ref_deposit": 10, "ref_tasks": 5},
    4: {"name": "🧙 Гуру подписок", "min_xp": 5000, "max_xp": 9999, "check_limit": 2_500_000, "ref_deposit": 10, "ref_tasks": 7},
    5: {"name": "🪙 Мастер над монетой", "min_xp": 10000, "max_xp": float("inf"), "check_limit": float("inf"), "ref_deposit": 10, "ref_tasks": 10},
    6: {"name": "🎓 Реферальный специалист", "min_xp": 10000, "max_xp": float("inf"), "check_limit": float("inf"), "ref_deposit": 12.5, "ref_tasks": 12.5, "min_refs": 100},
    7: {"name": "👑 Властелин пиара", "min_xp": 10000, "max_xp": float("inf"), "check_limit": float("inf"), "ref_deposit": 15, "ref_tasks": 15, "min_refs": 500},
}

XP_REWARDS = {
    "views": 1,
    "subscribe": 5,          # после 7 суток
    "reactions": 5,
    "bots": 5,
    "boost": 15,
    "referral": 25,
    "deposit_per_star": 10,
}


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance REAL DEFAULT 0,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            referrer_id INTEGER,
            language TEXT DEFAULT 'ru',
            notifications INTEGER DEFAULT 1,
            created_at REAL,
            last_active REAL,
            views_count INTEGER DEFAULT 0,          -- для капчи
            captcha_pending INTEGER DEFAULT 0,
            captcha_answer TEXT,
            FOREIGN KEY (referrer_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            type TEXT NOT NULL,                     -- channel, group, view, bot_standard, bot_webapp, bot_extra, reaction, boost
            title TEXT,
            link TEXT NOT NULL,                     -- t.me/... или invite
            price REAL NOT NULL,
            description TEXT,                      -- для bot_extra условия
            status TEXT DEFAULT 'active',          -- active, paused, finished, banned
            total_needed INTEGER DEFAULT 0,        -- сколько нужно выполнений
            completed_count INTEGER DEFAULT 0,
            created_at REAL,
            FOREIGN KEY (owner_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS task_completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',         -- pending, approved, rejected, auto_approved
            screenshot_file_id TEXT,
            screenshot_unique_id TEXT,             -- для анти-дубликата
            reward REAL,
            created_at REAL,
            reviewed_at REAL,
            UNIQUE(task_id, user_id),
            FOREIGN KEY (task_id) REFERENCES tasks(id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS hidden_tasks (
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, task_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );

        CREATE TABLE IF NOT EXISTS bans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,                -- channels, groups, views, bots, reactions, boost, all
            reason TEXT,
            until_ts REAL NOT NULL,
            created_at REAL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            custom_text TEXT,
            status TEXT DEFAULT 'open',            -- open, resolved, rejected
            created_at REAL,
            FOREIGN KEY (reporter_id) REFERENCES users(user_id),
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            type TEXT NOT NULL,                    -- earn, deposit, withdraw, referral, penalty
            description TEXT,
            related_id INTEGER,                    -- task_id / payment_id
            created_at REAL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS used_screenshots (
            unique_id TEXT PRIMARY KEY,
            user_id INTEGER,
            task_id INTEGER,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            stars INTEGER NOT NULL,
            lich_amount REAL NOT NULL,
            payload TEXT UNIQUE,
            status TEXT DEFAULT 'pending',         -- pending, paid, expired
            created_at REAL,
            paid_at REAL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_type_status ON tasks(type, status);
        CREATE INDEX IF NOT EXISTS idx_tasks_price ON tasks(price DESC);
        CREATE INDEX IF NOT EXISTS idx_completions_user ON task_completions(user_id);
        CREATE INDEX IF NOT EXISTS idx_bans_user ON bans(user_id, until_ts);
        """)
        await db.commit()


# ==================== USERS ====================

async def get_or_create_user(user_id: int, username: str = None, full_name: str = None, referrer_id: int = None) -> Dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if row:
                await db.execute("UPDATE users SET last_active = ?, username = COALESCE(?, username), full_name = COALESCE(?, full_name) WHERE user_id = ?",
                                 (time.time(), username, full_name, user_id))
                await db.commit()
                return dict(row)

        # create
        now = time.time()
        await db.execute(
            "INSERT INTO users (user_id, username, full_name, referrer_id, created_at, last_active) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, full_name, referrer_id, now, now)
        )
        await db.commit()

        # referral bonus
        if referrer_id and referrer_id != user_id:
            # будет начисляться при первом действии или отдельно
            pass

        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            return dict(await cur.fetchone())


async def get_user(user_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_balance(user_id: int, amount: float, tx_type: str, description: str = "", related_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.execute(
            "INSERT INTO transactions (user_id, amount, type, description, related_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, amount, tx_type, description, related_id, time.time())
        )
        await db.commit()


async def add_xp(user_id: int, xp: int) -> Tuple[int, int, bool]:
    """Returns (new_xp, new_level, level_up)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT xp, level FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return 0, 1, False
            old_xp = row["xp"]
            old_level = row["level"]
            new_xp = old_xp + xp

        # calculate level
        new_level = 1
        for lvl in range(5, 0, -1):
            if new_xp >= LEVELS[lvl]["min_xp"]:
                new_level = lvl
                break

        # check special levels by refs later if needed
        level_up = new_level > old_level
        await db.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (new_xp, new_level, user_id))
        await db.commit()
        return new_xp, new_level, level_up


async def get_level_info(xp: int, refs_count: int = 0) -> Dict:
    level = 1
    for lvl in range(5, 0, -1):
        if xp >= LEVELS[lvl]["min_xp"]:
            level = lvl
            break
    if refs_count >= 500:
        level = 7
    elif refs_count >= 100:
        level = 6
    info = LEVELS[level].copy()
    info["level"] = level
    return info


# ==================== TASKS ====================

async def get_active_tasks(task_type: str, user_id: int, page: int = 1, per_page: int = 10) -> Tuple[List[Dict], int]:
    """Returns tasks and total pages. Excludes hidden and already completed by user."""
    offset = (page - 1) * per_page
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # total count
        query_count = """
            SELECT COUNT(*) as cnt FROM tasks t
            WHERE t.type = ? AND t.status = 'active'
            AND t.id NOT IN (SELECT task_id FROM hidden_tasks WHERE user_id = ?)
            AND t.id NOT IN (SELECT task_id FROM task_completions WHERE user_id = ? AND status IN ('approved', 'auto_approved', 'pending'))
        """
        async with db.execute(query_count, (task_type, user_id, user_id)) as cur:
            total = (await cur.fetchone())["cnt"]

        total_pages = max(1, (total + per_page - 1) // per_page)

        query = """
            SELECT t.* FROM tasks t
            WHERE t.type = ? AND t.status = 'active'
            AND t.id NOT IN (SELECT task_id FROM hidden_tasks WHERE user_id = ?)
            AND t.id NOT IN (SELECT task_id FROM task_completions WHERE user_id = ? AND status IN ('approved', 'auto_approved', 'pending'))
            ORDER BY t.price DESC, t.id ASC
            LIMIT ? OFFSET ?
        """
        async with db.execute(query, (task_type, user_id, user_id, per_page, offset)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows], total_pages


async def get_task(task_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def count_active_tasks(task_type: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM tasks WHERE type = ? AND status = 'active'", (task_type,)) as cur:
            return (await cur.fetchone())[0]


async def hide_task_for_user(user_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO hidden_tasks (user_id, task_id) VALUES (?, ?)", (user_id, task_id))
        await db.commit()


# ==================== COMPLETIONS ====================

async def create_completion(task_id: int, user_id: int, reward: float, screenshot_file_id: str = None, screenshot_unique_id: str = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO task_completions (task_id, user_id, status, screenshot_file_id, screenshot_unique_id, reward, created_at)
               VALUES (?, ?, 'pending', ?, ?, ?, ?)""",
            (task_id, user_id, screenshot_file_id, screenshot_unique_id, reward, time.time())
        )
        await db.commit()
        return cur.lastrowid


async def is_screenshot_used(unique_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM used_screenshots WHERE unique_id = ?", (unique_id,)) as cur:
            return await cur.fetchone() is not None


async def mark_screenshot_used(unique_id: str, user_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO used_screenshots (unique_id, user_id, task_id, created_at) VALUES (?, ?, ?, ?)",
            (unique_id, user_id, task_id, time.time())
        )
        await db.commit()


async def approve_completion(completion_id: int, auto: bool = False):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM task_completions WHERE id = ?", (completion_id,)) as cur:
            comp = await cur.fetchone()
            if not comp or comp["status"] != "pending":
                return False

        status = "auto_approved" if auto else "approved"
        await db.execute(
            "UPDATE task_completions SET status = ?, reviewed_at = ? WHERE id = ?",
            (status, time.time(), completion_id)
        )
        # начисление будет в bot.py после вызова
        await db.commit()
        return True


# ==================== BANS ====================

async def is_banned(user_id: int, category: str) -> Optional[Dict]:
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM bans WHERE user_id = ? AND (category = ? OR category = 'all') AND until_ts > ?
               ORDER BY until_ts DESC LIMIT 1""",
            (user_id, category, now)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def add_ban(user_id: int, category: str, days: int = 7, reason: str = ""):
    until = time.time() + days * 86400
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO bans (user_id, category, reason, until_ts, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, category, reason, until, time.time())
        )
        await db.commit()


# ==================== COMPLAINTS ====================

async def create_complaint(reporter_id: int, task_id: int, reason: str, custom_text: str = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO complaints (reporter_id, task_id, reason, custom_text, created_at) VALUES (?, ?, ?, ?, ?)",
            (reporter_id, task_id, reason, custom_text, time.time())
        )
        await db.commit()
        return cur.lastrowid


# ==================== REFERRALS ====================

async def get_referral_stats(user_id: int) -> Dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,)) as cur:
            invited = (await cur.fetchone())[0]

        # упрощённо — из transactions
        async with db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE user_id = ? AND type = 'referral_deposit'",
            (user_id,)
        ) as cur:
            from_deposits = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE user_id = ? AND type = 'referral_tasks'",
            (user_id,)
        ) as cur:
            from_tasks = (await cur.fetchone())[0]

        return {
            "invited": invited,
            "from_deposits": from_deposits,
            "from_tasks": from_tasks
        }


# ==================== PAYMENTS ====================

async def create_payment(user_id: int, stars: int, lich_amount: float, payload: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO payments (user_id, stars, lich_amount, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, stars, lich_amount, payload, time.time())
        )
        await db.commit()
        return cur.lastrowid


async def get_payment_by_payload(payload: str) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM payments WHERE payload = ?", (payload,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def mark_payment_paid(payload: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE payments SET status = 'paid', paid_at = ? WHERE payload = ?",
            (time.time(), payload)
        )
        await db.commit()


# ==================== VIEWS CAPTCHA ====================

async def increment_views_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET views_count = views_count + 1 WHERE user_id = ?", (user_id,))
        await db.commit()
        async with db.execute("SELECT views_count FROM users WHERE user_id = ?", (user_id,)) as cur:
            return (await cur.fetchone())[0]


async def set_captcha(user_id: int, answer: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET captcha_pending = 1, captcha_answer = ? WHERE user_id = ?", (answer, user_id))
        await db.commit()


async def clear_captcha(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET captcha_pending = 0, captcha_answer = NULL, views_count = 0 WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_captcha_state(user_id: int) -> Tuple[bool, Optional[str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT captcha_pending, captcha_answer FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return bool(row[0]), row[1]
            return False, None
