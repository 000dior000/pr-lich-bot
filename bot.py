import asyncio
import logging
import time
import random
import string
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice, PreCheckoutQuery,
    ContentType, FSInputFile
)
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

import database as db

# ==================== CONFIG ====================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"          # <-- вставь токен
ADMIN_IDS = [123456789]                    # <-- ID админов
STARS_RATE = 1800                          # 1 ⭐ = 1800 LICH
SUPPORT_USERNAME = "@no_name0515"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


# ==================== STATES ====================
class Form(StatesGroup):
    waiting_screenshot = State()
    waiting_complaint_text = State()
    waiting_custom_stars = State()
    waiting_view_next = State()


# ==================== KEYBOARDS ====================

def main_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="💰Заработать"),
                KeyboardButton(text="🧾Чеки"),
                KeyboardButton(text="👥 ОП (Проверка подписки)"),
                KeyboardButton(text="🔗Полезные ссылки"),
            ],
            [
                KeyboardButton(text="📝Рекламировать"),
                KeyboardButton(text="👤Мой кабинет"),
                KeyboardButton(text="📊Статистика"),
                KeyboardButton(text="ℹ️Инструкция"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def earn_categories_kb(counts: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"📰 Каналы • {counts.get('channel', 0)}", callback_data="earn_cat:channel"),
            InlineKeyboardButton(text=f"👥 Группы • {counts.get('group', 0)}", callback_data="earn_cat:group"),
        ],
        [
            InlineKeyboardButton(text=f"👀 Просмотры • {counts.get('view', 0)}", callback_data="earn_cat:view"),
            InlineKeyboardButton(text=f"🤖 Боты • {counts.get('bot', 0)}", callback_data="earn_cat:bot"),
        ],
        [
            InlineKeyboardButton(text=f"❤️ Реакции • {counts.get('reaction', 0)}", callback_data="earn_cat:reaction"),
            InlineKeyboardButton(text=f"⚡️ Boost • {counts.get('boost', 0)}", callback_data="earn_cat:boost"),
        ],
        [InlineKeyboardButton(text="📝 Правила", callback_data="earn_rules")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")],
    ])


def rules_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="earn_back")]
    ])


def pagination_kb(page: int, total_pages: int, prefix: str, extra: list = None) -> list:
    buttons = []
    row = []
    if page > 1:
        row.append(InlineKeyboardButton(text="1", callback_data=f"{prefix}:page:1"))
        row.append(InlineKeyboardButton(text="<", callback_data=f"{prefix}:page:{page-1}"))
    row.append(InlineKeyboardButton(text=str(page), callback_data="noop"))
    if page < total_pages:
        row.append(InlineKeyboardButton(text=">", callback_data=f"{prefix}:page:{page+1}"))
        row.append(InlineKeyboardButton(text=str(total_pages), callback_data=f"{prefix}:page:{total_pages}"))
    if row:
        buttons.append(row)
    if extra:
        buttons.extend(extra)
    return buttons


# ==================== HELPERS ====================

async def safe_delete(message: Message):
    try:
        await message.delete()
    except Exception:
        pass


async def answer_and_delete(callback: CallbackQuery, text: str, reply_markup=None, parse_mode="HTML"):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        await callback.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        try:
            await callback.message.delete()
        except Exception:
            pass
    await callback.answer()


def format_balance(bal: float) -> str:
    return f"{bal:,.2f}".replace(",", " ")


# ==================== START ====================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    args = message.text.split(maxsplit=1)
    referrer_id = None
    if len(args) > 1 and args[1].startswith("ref"):
        try:
            referrer_id = int(args[1][3:])
        except ValueError:
            pass
    elif len(args) > 1 and args[1].isdigit():
        referrer_id = int(args[1])

    user = await db.get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
        referrer_id if referrer_id != message.from_user.id else None
    )

    text = (
        f"👋 <b>{message.from_user.first_name}</b>, добро пожаловать в <b>PR LICH</b>!\n\n"
        f"Платформа для продвижения в Telegram"
    )
    await message.answer(text, reply_markup=main_reply_kb())


@router.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text = (
        f"👋 <b>{callback.from_user.first_name}</b>, добро пожаловать в <b>PR LICH</b>!\n\n"
        f"Платформа для продвижения в Telegram"
    )
    await answer_and_delete(callback, text)
    # reply kb уже есть


# ==================== ЗАРАБОТАТЬ ====================

@router.message(F.text == "💰Заработать")
async def earn_menu(message: Message, state: FSMContext):
    await state.clear()
    counts = {
        "channel": await db.count_active_tasks("channel"),
        "group": await db.count_active_tasks("group"),
        "view": await db.count_active_tasks("view"),
        "bot": (
            await db.count_active_tasks("bot_standard") +
            await db.count_active_tasks("bot_webapp") +
            await db.count_active_tasks("bot_extra")
        ),
        "reaction": await db.count_active_tasks("reaction"),
        "boost": await db.count_active_tasks("boost"),
    }
    text = "📝 Выберите категорию заданий для заработка"
    await message.answer(text, reply_markup=earn_categories_kb(counts))


@router.callback_query(F.data == "earn_back")
async def earn_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    counts = {
        "channel": await db.count_active_tasks("channel"),
        "group": await db.count_active_tasks("group"),
        "view": await db.count_active_tasks("view"),
        "bot": (
            await db.count_active_tasks("bot_standard") +
            await db.count_active_tasks("bot_webapp") +
            await db.count_active_tasks("bot_extra")
        ),
        "reaction": await db.count_active_tasks("reaction"),
        "boost": await db.count_active_tasks("boost"),
    }
    text = "📝 Выберите категорию заданий для заработка"
    await answer_and_delete(callback, text, reply_markup=earn_categories_kb(counts))


@router.callback_query(F.data == "earn_rules")
async def earn_rules(callback: CallbackQuery):
    text = (
        "<b>📕 Правила заработка</b>\n\n"
        "<b>⛔ Запрещено</b>\n"
        "• Отписываться от каналов и чатов раньше чем через 7 суток.\n"
        "• Снимать поставленную реакцию.\n"
        "• Использовать больше 3 аккаунтов для заработка.\n"
        "• Обманывать с выполнением заданий — фейковые скриншоты.\n"
        "• Автоматизировать выполнение программами и другими инструментами.\n\n"
        "<b>❗ Что будет за нарушение</b>\n"
        "• Бан на выполнение заданий — 7 суток.\n"
        "• Повторное нарушение — снова 7 суток.\n"
        "• За отписку списывается вся сумма, полученная за это задание."
    )
    await answer_and_delete(callback, text, reply_markup=rules_kb())


# ==================== КАНАЛЫ / ГРУППЫ ====================

async def show_subscribe_tasks(callback: CallbackQuery, task_type: str, page: int = 1):
    user_id = callback.from_user.id
    ban = await db.is_banned(user_id, task_type if task_type != "channel" else "channels")
    if ban:
        until = datetime.fromtimestamp(ban["until_ts"]).strftime("%d.%m.%Y %H:%M")
        await callback.answer(f"Вы заблокированы в этой категории до {until}\nПричина: {ban.get('reason', '-')}", show_alert=True)
        return

    tasks, total_pages = await db.get_active_tasks(task_type, user_id, page)
    min_price = 750 if task_type == "channel" else 1000

    warning = (
        "⚠️ Не отписывайтесь от каналов раньше чем через 7 суток. "
        "Иначе выполнение заданий заблокируют, а заработанные с них LICH аннулируют."
        if task_type == "channel" else
        "⚠️ Не отписывайтесь от групп раньше чем через 7 суток. "
        "Иначе выполнение заданий заблокируют, а заработанные с них LICH аннулируют."
    )

    if not tasks:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="earn_back")]
        ])
        await answer_and_delete(callback, warning + "\n\nНет доступных заданий.", reply_markup=kb)
        return

    buttons = []
    for t in tasks:
        buttons.append([
            InlineKeyboardButton(
                text=f"💵 +{int(t['price'])} | Подписаться",
                url=t["link"] if t["link"].startswith("http") else f"https://t.me/{t['link'].lstrip('@')}"
            ),
            InlineKeyboardButton(text="🔄 Проверить", callback_data=f"check_sub:{t['id']}")
        ])

    extra = [
        [InlineKeyboardButton(text="❌ Пожаловаться", callback_data=f"complain_list:{task_type}:{page}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="earn_back")]
    ]
    buttons.extend(pagination_kb(page, total_pages, f"sublist:{task_type}", extra))

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await answer_and_delete(callback, warning, reply_markup=kb)


@router.callback_query(F.data.startswith("earn_cat:"))
async def earn_category(callback: CallbackQuery, state: FSMContext):
    cat = callback.data.split(":")[1]
    if cat in ("channel", "group"):
        await show_subscribe_tasks(callback, cat, 1)
    elif cat == "view":
        await show_views_menu(callback)
    elif cat == "bot":
        await show_bots_menu(callback)
    elif cat in ("reaction", "boost"):
        await callback.answer("Раздел в разработке", show_alert=True)
    else:
        await callback.answer()


@router.callback_query(F.data.startswith("sublist:"))
async def sublist_page(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_type = parts[1]
    page = int(parts[3])
    await show_subscribe_tasks(callback, task_type, page)


@router.callback_query(F.data.startswith("check_sub:"))
async def check_subscription(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    task = await db.get_task(task_id)
    if not task:
        await callback.answer("Задание не найдено", show_alert=True)
        return

    user_id = callback.from_user.id
    # Проверка подписки через getChatMember
    try:
        chat_id = task["link"]
        if "t.me/" in chat_id:
            chat_id = "@" + chat_id.split("t.me/")[-1].split("/")[0].split("?")[0]
        elif not chat_id.startswith("@"):
            chat_id = "@" + chat_id

        member = await bot.get_chat_member(chat_id, user_id)
        if member.status in ("member", "administrator", "creator"):
            # Успех
            reward = task["price"]
            await db.create_completion(task_id, user_id, reward)
            # сразу approved для подписок
            async with aiosqlite.connect(db.DB_PATH) as conn:  # быстрый апдейт
                await conn.execute(
                    "UPDATE task_completions SET status = 'approved', reviewed_at = ? WHERE task_id = ? AND user_id = ?",
                    (time.time(), task_id, user_id)
                )
                await conn.commit()

            await db.update_balance(user_id, reward, "earn", f"Подписка на задание #{task_id}", task_id)
            user = await db.get_user(user_id)
            text = (
                f"✅ Задание №{task_id} выполнено\n\n"
                f"💸 Вы получили +{int(reward)} LICH\n"
                f"💰 Баланс: {format_balance(user['balance'])} LICH"
            )
            await callback.message.answer(text)
            # обновляем список
            await show_subscribe_tasks(callback, task["type"], 1)
        else:
            await callback.answer("ℹ️ Вы ещё не подписаны на канал/чат. Подпишитесь и попробуйте ещё раз.", show_alert=True)
    except Exception as e:
        logger.error(f"Check sub error: {e}")
        await callback.answer("Не удалось проверить подписку. Убедитесь, что бот является админом канала/группы или ссылка корректна.", show_alert=True)


# ==================== ПРОСМОТРЫ ====================

async def show_views_menu(callback: CallbackQuery, page: int = 1):
    user_id = callback.from_user.id
    ban = await db.is_banned(user_id, "views")
    if ban:
        until = datetime.fromtimestamp(ban["until_ts"]).strftime("%d.%m.%Y %H:%M")
        await callback.answer(f"Вы заблокированы в просмотрах до {until}", show_alert=True)
        return

    pending, answer = await db.get_captcha_state(user_id)
    if pending:
        await send_captcha(callback, answer)
        return

    tasks, total_pages = await db.get_active_tasks("view", user_id, page)
    text = (
        "Чтобы получать LICH, нужно просматривать публикации, нажмите на кнопки, чтобы посмотреть\n\n"
        "Внимание! Некоторые посты слишком длинные, в этом случае нужно пролистать его вверх и вниз."
    )

    if not tasks:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="earn_back")]])
        await answer_and_delete(callback, text + "\n\nНет доступных постов.", reply_markup=kb)
        return

    buttons = []
    for t in tasks:
        buttons.append([
            InlineKeyboardButton(
                text=f"👀 Просмотр поста +{int(t['price'])} LICH",
                callback_data=f"view_post:{t['id']}"
            )
        ])

    extra = [[InlineKeyboardButton(text="🔙 Назад", callback_data="earn_back")]]
    buttons.extend(pagination_kb(page, total_pages, "viewlist", extra))
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await answer_and_delete(callback, text, reply_markup=kb)


@router.callback_query(F.data.startswith("viewlist:"))
async def viewlist_page(callback: CallbackQuery):
    page = int(callback.data.split(":")[2])
    await show_views_menu(callback, page)


@router.callback_query(F.data.startswith("view_post:"))
async def do_view_post(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split(":")[1])
    task = await db.get_task(task_id)
    if not task:
        await callback.answer("Пост не найден", show_alert=True)
        return

    user_id = callback.from_user.id
    # Пересылаем пост (предполагаем, что link содержит message_id или мы храним from_chat + msg_id)
    # Для простоты: если link вида "chat_id:msg_id"
    try:
        if ":" in task["link"]:
            chat_id, msg_id = task["link"].split(":")
            await bot.forward_message(callback.from_user.id, int(chat_id), int(msg_id))
        else:
            await callback.message.answer(f"Пост: {task['link']}")
    except Exception as e:
        logger.error(e)
        await callback.message.answer("Не удалось загрузить пост.")

    await callback.answer()
    # Ждём 4 секунды
    await asyncio.sleep(4)

    reward = task["price"]
    await db.create_completion(task_id, user_id, reward)
    # сразу approved
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute(
            "UPDATE task_completions SET status = 'approved', reviewed_at = ? WHERE task_id = ? AND user_id = ?",
            (time.time(), task_id, user_id)
        )
        await conn.commit()

    await db.update_balance(user_id, reward, "earn", f"Просмотр поста #{task_id}", task_id)
    await db.add_xp(user_id, db.XP_REWARDS["views"])
    views_count = await db.increment_views_count(user_id)
    user = await db.get_user(user_id)

    text = (
        f"💸 Вам начислено {int(reward)} LICH за просмотр поста #{task_id}!\n"
        f"💰 Ваш баланс: {format_balance(user['balance'])} LICH"
    )

    if views_count >= 7:
        # капча
        emojis = ["🍎", "🍌", "🍇", "🍊", "🍓", "🍉", "🥕", "🌽", "🥦", "🍅"]
        correct = random.choice(emojis)
        options = random.sample([e for e in emojis if e != correct], 5) + [correct]
        random.shuffle(options)
        await db.set_captcha(user_id, correct)

        buttons = []
        row = []
        for i, em in enumerate(options):
            row.append(InlineKeyboardButton(text=em, callback_data=f"captcha:{em}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.answer(
            f"🎯 Найди нужный эмодзи, нажми на {correct}",
            reply_markup=kb
        )
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="➡️ Следующий пост", callback_data="view_next"),
                InlineKeyboardButton(text="🚫 Пожаловаться", callback_data=f"complain_view:{task_id}"),
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="earn_cat:view")]
        ])
        await callback.message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "view_next")
async def view_next(callback: CallbackQuery):
    await show_views_menu(callback, 1)


@router.callback_query(F.data.startswith("captcha:"))
async def check_captcha(callback: CallbackQuery):
    chosen = callback.data.split(":")[1]
    pending, correct = await db.get_captcha_state(callback.from_user.id)
    if not pending:
        await callback.answer("Капча уже пройдена")
        return
    if chosen == correct:
        await db.clear_captcha(callback.from_user.id)
        await callback.answer("✅ Верно!")
        await show_views_menu(callback, 1)
    else:
        await callback.answer("❌ Неверно, попробуйте ещё раз", show_alert=True)


async def send_captcha(callback: CallbackQuery, correct: str):
    emojis = ["🍎", "🍌", "🍇", "🍊", "🍓", "🍉", "🥕", "🌽", "🥦", "🍅"]
    options = random.sample([e for e in emojis if e != correct], 5) + [correct]
    random.shuffle(options)
    buttons = []
    row = []
    for em in options:
        row.append(InlineKeyboardButton(text=em, callback_data=f"captcha:{em}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await answer_and_delete(callback, f"🎯 Найди нужный эмодзи, нажми на {correct}", reply_markup=kb)


# ==================== БОТЫ ====================

async def show_bots_menu(callback: CallbackQuery):
    c_std = await db.count_active_tasks("bot_standard")
    c_web = await db.count_active_tasks("bot_webapp")
    c_ext = await db.count_active_tasks("bot_extra")
    text = (
        "Выберите категорию заданий:\n\n"
        f"🤖 <b>Стандартные боты — {c_std}</b>\n"
        "Обычные Telegram-боты, только запуск.\n\n"
        f"📱 <b>Боты с Web App — {c_web}</b>\n"
        "Запустить мини-приложение в Telegram.\n\n"
        f"🤖 <b>С дополнительными условиями — {c_ext}</b>\n"
        "Кроме Start нужно выполнить действия: пройти капчу, подписка на спонсоров и т.п."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🤖 Обычные боты", callback_data="bots_sub:bot_standard"),
            InlineKeyboardButton(text="📱 Боты с Web App", callback_data="bots_sub:bot_webapp"),
        ],
        [InlineKeyboardButton(text="📝 С дополнительными условиями", callback_data="bots_sub:bot_extra")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="earn_back")],
    ])
    await answer_and_delete(callback, text, reply_markup=kb)


@router.callback_query(F.data.startswith("bots_sub:"))
async def bots_subcategory(callback: CallbackQuery):
    task_type = callback.data.split(":")[1]
    await show_bot_tasks(callback, task_type, 1)


async def show_bot_tasks(callback: CallbackQuery, task_type: str, page: int = 1):
    user_id = callback.from_user.id
    ban = await db.is_banned(user_id, "bots")
    if ban:
        until = datetime.fromtimestamp(ban["until_ts"]).strftime("%d.%m.%Y %H:%M")
        await callback.answer(f"Вы заблокированы в ботах до {until}", show_alert=True)
        return

    tasks, total_pages = await db.get_active_tasks(task_type, user_id, page)
    text = (
        "📝 Выберите задание для выполнения.\n\n"
        "⚠️ Запрещено блокировать ботов раньше 7 суток, иначе вы можете быть оштрафованы!"
    )

    if not tasks:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="earn_cat:bot")]])
        await answer_and_delete(callback, text + "\n\nНет доступных заданий.", reply_markup=kb)
        return

    buttons = []
    for t in tasks:
        buttons.append([
            InlineKeyboardButton(
                text=f"🤖 Перейти в бота | +{int(t['price'])} LICH",
                callback_data=f"bot_task:{t['id']}"
            )
        ])

    extra = [
        [InlineKeyboardButton(text="❌ Пожаловаться", callback_data=f"complain_list:{task_type}:{page}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="earn_cat:bot")]
    ]
    buttons.extend(pagination_kb(page, total_pages, f"botlist:{task_type}", extra))
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await answer_and_delete(callback, text, reply_markup=kb)


@router.callback_query(F.data.startswith("botlist:"))
async def botlist_page(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_type = parts[1]
    page = int(parts[3])
    await show_bot_tasks(callback, task_type, page)


@router.callback_query(F.data.startswith("bot_task:"))
async def open_bot_task(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split(":")[1])
    task = await db.get_task(task_id)
    if not task:
        await callback.answer("Задание не найдено", show_alert=True)
        return

    await state.update_data(current_bot_task=task_id)

    if task["type"] == "bot_extra" and task.get("description"):
        text = (
            "⚠️ Блокировать ботов раньше 7 дней запрещено, иначе можете получить штраф!\n\n"
            "📷 Пришлите скриншот, на котором ясно видно, что вы выполнили задание согласно условиям.\n"
            f"<b>📋 Условия:</b>\n"
            f"<blockquote>{task['description']}</blockquote>"
        )
    elif task["type"] == "bot_webapp":
        text = (
            "Перейдите по ссылке на Web App, откройте его и сделайте скриншот для подтверждения выполнения.\n\n"
            "📷 Отправьте скриншот, на котором четко видно, что вы открыли Web App, для подтверждения оплаты."
        )
    else:
        text = (
            "Перейдите в бота и запустите его.\n"
            "Если в боте есть капча — пройдите её. Остальные условия выполнять не обязательно.\n\n"
            "📷 Сделайте скриншот, где видно, что вы запустили бота, и отправьте его прямо здесь — это подтвердит выполнение."
        )

    link = task["link"]
    if not link.startswith("http"):
        link = f"https://t.me/{link.lstrip('@')}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Перейти к Боту", url=link)],
        [InlineKeyboardButton(text="👀 Скрыть задание", callback_data=f"hide_task:{task_id}")],
        [InlineKeyboardButton(text="🚫 Пожаловаться", callback_data=f"complain_bot:{task_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"bots_sub:{task['type']}")],
    ])
    await answer_and_delete(callback, text, reply_markup=kb)
    await state.set_state(Form.waiting_screenshot)


@router.message(Form.waiting_screenshot, F.photo)
async def process_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("current_bot_task")
    if not task_id:
        await message.answer("Сессия устарела. Выберите задание заново.")
        await state.clear()
        return

    task = await db.get_task(task_id)
    if not task:
        await message.answer("Задание не найдено.")
        await state.clear()
        return

    photo = message.photo[-1]
    unique_id = photo.file_unique_id

    if await db.is_screenshot_used(unique_id):
        await message.answer(
            "❌ Этот скриншот уже использовался ранее.\n"
            "Повторное использование одного и того же изображения для разных заданий "
            "рассматривается как намеренный обман при выполнении и может привести к применению ограничений.\n\n"
            "<b>Отправьте новый скриншот в соответствии с условиями задания.</b>"
        )
        return

    await db.mark_screenshot_used(unique_id, message.from_user.id, task_id)
    completion_id = await db.create_completion(
        task_id, message.from_user.id, task["price"],
        screenshot_file_id=photo.file_id,
        screenshot_unique_id=unique_id
    )

    # Уведомление автору задания (если нужно)
    try:
        await bot.send_photo(
            task["owner_id"],
            photo.file_id,
            caption=(
                f"📥 Новое выполнение задания #{task_id}\n"
                f"От: {message.from_user.full_name} (@{message.from_user.username or 'нет'})\n"
                f"ID: <code>{message.from_user.id}</code>\n"
                f"Награда: {int(task['price'])} LICH\n\n"
                f"Проверьте скриншот. Если не проверите за 24 часа — оплата пройдёт автоматически."
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_comp:{completion_id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_comp:{completion_id}"),
                ]
            ])
        )
    except Exception:
        pass

    text = (
        f"✅ Выполнение №{completion_id} отправлено автору на проверку.\n"
        f"🕒 Если не проверит за 24 часа — оплата пройдёт автоматически."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➡️ Следующий Бот", callback_data=f"bots_sub:{task['type']}"),
            InlineKeyboardButton(text="🔙 Назад", callback_data=f"bots_sub:{task['type']}"),
        ]
    ])
    await message.answer(text, reply_markup=kb)
    await state.clear()


@router.callback_query(F.data.startswith("approve_comp:"))
async def approve_comp(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS and True:  # владелец тоже может
        pass
    completion_id = int(callback.data.split(":")[1])
    # упрощённо — начисляем
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM task_completions WHERE id = ?", (completion_id,)) as cur:
            comp = await cur.fetchone()
            if not comp or comp["status"] != "pending":
                await callback.answer("Уже обработано")
                return
        await conn.execute(
            "UPDATE task_completions SET status = 'approved', reviewed_at = ? WHERE id = ?",
            (time.time(), completion_id)
        )
        await conn.commit()

    await db.update_balance(comp["user_id"], comp["reward"], "earn", f"Бот задание #{comp['task_id']}", comp["task_id"])
    await db.add_xp(comp["user_id"], db.XP_REWARDS["bots"])
    user = await db.get_user(comp["user_id"])
    try:
        await bot.send_message(
            comp["user_id"],
            f"✅ Задание №{comp['task_id']} выполнено\n\n"
            f"💸 Вы получили +{int(comp['reward'])} LICH\n"
            f"💰 Баланс: {format_balance(user['balance'])} LICH"
        )
    except Exception:
        pass
    await callback.answer("Одобрено")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("hide_task:"))
async def hide_task(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    await db.hide_task_for_user(callback.from_user.id, task_id)
    await callback.answer("Задание скрыто для вас")
    await callback.message.delete()


# ==================== ЖАЛОБЫ ====================

@router.callback_query(F.data.startswith("complain_bot:"))
async def complain_bot(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔞 Неприемлемый контент", callback_data=f"creason:{task_id}:bad_content")],
        [InlineKeyboardButton(text="⛔️ Нелегальный бот («пробив» / докс)", callback_data=f"creason:{task_id}:illegal")],
        [InlineKeyboardButton(text="🚫 Требует контактные данные", callback_data=f"creason:{task_id}:contacts")],
        [InlineKeyboardButton(text="💀 Бот не работает", callback_data=f"creason:{task_id}:not_work")],
        [InlineKeyboardButton(text="📝 Другая причина", callback_data=f"creason:{task_id}:other")],
        [InlineKeyboardButton(text="👀 Скрыть задание для меня", callback_data=f"hide_task:{task_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"bot_task:{task_id}")],
    ])
    await answer_and_delete(callback, "Выберите причину жалобы:", reply_markup=kb)


@router.callback_query(F.data.startswith("complain_view:"))
async def complain_view(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔞 Неприемлемый контент", callback_data=f"creason:{task_id}:bad_content")],
        [InlineKeyboardButton(text="📝 Другая причина", callback_data=f"creason:{task_id}:other")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="view_next")],
    ])
    await answer_and_delete(callback, "Выберите причину жалобы:", reply_markup=kb)


@router.callback_query(F.data.startswith("creason:"))
async def process_reason(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    reason = parts[2]

    if reason == "other":
        await state.update_data(complaint_task=task_id)
        await state.set_state(Form.waiting_complaint_text)
        await answer_and_delete(callback, "📝 Введите причину жалобы:")
        return

    await db.create_complaint(callback.from_user.id, task_id, reason)
    await db.hide_task_for_user(callback.from_user.id, task_id)

    task = await db.get_task(task_id)
    # Уведомление админам
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(
                admin,
                f"🚨 Новая жалоба\n"
                f"От: {callback.from_user.full_name} (<code>{callback.from_user.id}</code>)\n"
                f"Задание: #{task_id} ({task['type'] if task else '?'})\n"
                f"Ссылка: {task['link'] if task else '-'}\n"
                f"Причина: {reason}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="❌ Заблокировать", callback_data=f"ban_owner:{task_id}"),
                        InlineKeyboardButton(text="✅ Разжаловать", callback_data=f"dismiss_comp:{task_id}"),
                    ]
                ])
            )
        except Exception:
            pass

    await callback.answer("ℹ Спасибо! Жалоба отправлена!")
    await callback.message.delete()


@router.message(Form.waiting_complaint_text)
async def complaint_text(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("complaint_task")
    text = message.text
    await db.create_complaint(message.from_user.id, task_id, "other", text)
    await db.hide_task_for_user(message.from_user.id, task_id)
    await state.clear()

    task = await db.get_task(task_id)
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(
                admin,
                f"🚨 Жалоба (другая причина)\n"
                f"От: {message.from_user.full_name} (<code>{message.from_user.id}</code>)\n"
                f"Задание: #{task_id}\n"
                f"Ссылка: {task['link'] if task else '-'}\n"
                f"Текст: {text}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="❌ Заблокировать", callback_data=f"ban_owner:{task_id}"),
                        InlineKeyboardButton(text="✅ Разжаловать", callback_data=f"dismiss_comp:{task_id}"),
                    ]
                ])
            )
        except Exception:
            pass
    await message.answer("ℹ Спасибо! Жалоба отправлена!")


@router.callback_query(F.data.startswith("ban_owner:"))
async def ban_owner(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа")
        return
    task_id = int(callback.data.split(":")[1])
    task = await db.get_task(task_id)
    if not task:
        await callback.answer("Задание не найдено")
        return
    category = "bots" if task["type"].startswith("bot") else task["type"] + "s"
    await db.add_ban(task["owner_id"], category, 7, "Жалоба на задание")
    try:
        await bot.send_message(
            task["owner_id"],
            f"🚫 Вы заблокированы в категории «{category}» на 7 дней.\n"
            f"Причина: жалоба на ваше задание #{task_id}."
        )
    except Exception:
        pass
    await callback.answer("Владелец заблокирован на 7 дней")


# ==================== КАБИНЕТ ====================

@router.message(F.text == "👤Мой кабинет")
async def my_cabinet(message: Message):
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала /start")
        return
    level_info = await db.get_level_info(user["xp"])
    text = (
        f"👤 <b>Ваш кабинет:</b>\n\n"
        f"🆔 Мой ID: <code>{user['user_id']}</code>\n"
        f"📈 Уровень: {level_info['name']} {user['xp']}/∞ XP\n"
        f"💰 Баланс: {format_balance(user['balance'])} LICH"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="deposit"),
            InlineKeyboardButton(text="🔗 Реферальная система", callback_data="referrals"),
        ],
        [
            InlineKeyboardButton(text="📈 Уровневая система", callback_data="levels"),
            InlineKeyboardButton(text="📝 Мои задания", callback_data="my_tasks"),
        ],
        [
            InlineKeyboardButton(text="🌎 Изменить язык", callback_data="change_lang"),
            InlineKeyboardButton(text="🔕 Отключить уведомления", callback_data="toggle_notif"),
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")],
    ])
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "deposit")
async def deposit_menu(callback: CallbackQuery):
    text = (
        f"Если возникли проблемы с пополнением - обращайтесь {SUPPORT_USERNAME}\n\n"
        f"Введите сумму пополнения в LICH или выберите:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 90 000 LICH = 50⭐️", callback_data="pay_stars:50")],
        [InlineKeyboardButton(text="💳 180 000 LICH = 100⭐️", callback_data="pay_stars:100")],
        [InlineKeyboardButton(text="💳 450 000 LICH = 250⭐️", callback_data="pay_stars:250")],
        [InlineKeyboardButton(text="💳 1 350 000 LICH = 750⭐️", callback_data="pay_stars:750")],
        [InlineKeyboardButton(text="💳 2 700 000 LICH = 1499⭐️", callback_data="pay_stars:1499")],
        [InlineKeyboardButton(text="💳 4 500 000 LICH = 2499⭐️", callback_data="pay_stars:2499")],
        [InlineKeyboardButton(text="🌟 Другая сумма", callback_data="pay_custom")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="cabinet_back")],
    ])
    await answer_and_delete(callback, text, reply_markup=kb)


@router.callback_query(F.data.startswith("pay_stars:"))
async def create_stars_invoice(callback: CallbackQuery):
    stars = int(callback.data.split(":")[1])
    lich = stars * STARS_RATE
    payload = f"dep_{callback.from_user.id}_{int(time.time())}_{stars}"
    await db.create_payment(callback.from_user.id, stars, lich, payload)

    prices = [LabeledPrice(label=f"{lich} LICH", amount=stars)]
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Пополнение баланса PR LICH",
        description=f"Пополнение на {format_balance(lich)} LICH",
        payload=payload,
        provider_token="",  # для Stars оставляем пустым
        currency="XTR",
        prices=prices,
    )
    await callback.answer()


@router.callback_query(F.data == "pay_custom")
async def pay_custom(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_custom_stars)
    text = (
        "📝 Укажите количество звёзд для пополнения\n\n"
        f"1 ⭐ = {STARS_RATE} LICH\n"
        "Минимум: 1 ⭐\n"
        "Максимум: 10 000 ⭐"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="deposit")]])
    await answer_and_delete(callback, text, reply_markup=kb)


@router.message(Form.waiting_custom_stars)
async def process_custom_stars(message: Message, state: FSMContext):
    try:
        stars = int(message.text.strip())
        if stars < 1 or stars > 10000:
            await message.answer("Введите число от 1 до 10 000")
            return
    except ValueError:
        await message.answer("Введите целое число")
        return

    lich = stars * STARS_RATE
    payload = f"dep_{message.from_user.id}_{int(time.time())}_{stars}"
    await db.create_payment(message.from_user.id, stars, lich, payload)
    await state.clear()

    prices = [LabeledPrice(label=f"{lich} LICH", amount=stars)]
    await bot.send_invoice(
        chat_id=message.from_user.id,
        title="Пополнение баланса PR LICH",
        description=f"Пополнение на {format_balance(lich)} LICH",
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=prices,
    )


@router.pre_checkout_query()
async def pre_checkout(pre: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre.id, ok=True)


@router.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment(message: Message):
    payment = message.successful_payment
    payload = payment.invoice_payload
    pay = await db.get_payment_by_payload(payload)
    if not pay or pay["status"] == "paid":
        return
    await db.mark_payment_paid(payload)
    await db.update_balance(pay["user_id"], pay["lich_amount"], "deposit", f"Пополнение на {pay['stars']}⭐")
    await db.add_xp(pay["user_id"], pay["stars"] * db.XP_REWARDS["deposit_per_star"])

    # реферальный процент
    user = await db.get_user(pay["user_id"])
    if user and user.get("referrer_id"):
        ref_bonus = pay["lich_amount"] * 0.10
        await db.update_balance(user["referrer_id"], ref_bonus, "referral_deposit", f"Реф. с пополнения {pay['user_id']}")

    user = await db.get_user(pay["user_id"])
    await message.answer(
        f"✅ Баланс успешно пополнен на {format_balance(pay['lich_amount'])} LICH!\n"
        f"💰 Текущий баланс: {format_balance(user['balance'])} LICH"
    )


@router.callback_query(F.data == "referrals")
async def referrals_menu(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    stats = await db.get_referral_stats(callback.from_user.id)
    level_info = await db.get_level_info(user["xp"], stats["invited"])
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start={callback.from_user.id}"

    text = (
        "👥 <b>Реферальная система</b>\n\n"
        "Приглашайте друзей в PR LICH — получайте LICH за каждого и постоянный процент от их активности.\n\n"
        "<b>Бонус за друга</b>\n"
        "• 10 000 LICH — с Telegram Premium\n"
        "• 5 000 LICH — без Premium\n"
        "• 3 000 LICH — если присоединился через ОП\n\n"
        "<b>Постоянный доход от активности</b>\n"
        f"• {level_info['ref_deposit']}% от их пополнений\n"
        f"• {level_info['ref_tasks']}% от выполненных ими заданий\n"
        f"Ваш уровень: {level_info['name']} — повышайте его, чтобы процент рос.\n\n"
        "<b>Ваша статистика</b>\n"
        f"• Приглашено: {stats['invited']}\n"
        f"• Заработано с пополнений: {format_balance(stats['from_deposits'])} LICH\n"
        f"• Заработано с заданий: {format_balance(stats['from_tasks'])} LICH\n\n"
        f"<b>🔗 Ваша ссылка</b>\n"
        f"<code>{ref_link}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться ссылкой", url=f"https://t.me/share/url?url={ref_link}&text=Присоединяйся к PR LICH!")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="cabinet_back")],
    ])
    await answer_and_delete(callback, text, reply_markup=kb)


@router.callback_query(F.data == "levels")
async def levels_menu(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    level_info = await db.get_level_info(user["xp"])
    text = (
        f"Ваш уровень: {level_info['name']} — {user['xp']}/∞ XP\n\n"
        "<b>Начисление XP за выполнение:</b>\n"
        "👁 Просмотры — +1 XP\n"
        "📢 Подписки — +5 XP (через 7 суток)\n"
        "❤️ Реакции — +5 XP\n"
        "🤖 Запуск ботов — +5 XP\n"
        "⚡ Бусты — +15 XP\n"
        "👥 Рефералы — +25 XP\n"
        "👛 Пополнение баланса — +10 XP за 1 ⭐️\n\n"
        "<blockquote>XP за выполнение заданий на реакции и ботов начисляется после того, как автор оплатит это задание.</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ℹ️ Информация об уровнях", callback_data="levels_info")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="cabinet_back")],
    ])
    await answer_and_delete(callback, text, reply_markup=kb)


@router.callback_query(F.data == "levels_info")
async def levels_info(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    level_info = await db.get_level_info(user["xp"])
    text = (
        f"<b>🔝 Ваш уровень:</b> {level_info['name']} {user['xp']}/∞ XP\n\n"
        "<blockquote>🔔 Важно: лимиты на чеки применяются только в случае если вы выполняли задания на подписки в течение 7 последних дней.</blockquote>\n\n"
        "<b>Уровень 1 (🐣 Новичок):</b>\n"
        "<b>📊 Условия:</b> 0–499 XP\n"
        "<b>💰 Лимит на создание чеков:</b> ❌ недоступно\n"
        "<b>🎁 Реферальный бонус:</b> 10% от пополнений, 3% от выполнения заданий.\n\n"
        "<b>Уровень 2 (🌱 Активист):</b>\n"
        "<b>📊 Условия:</b> 500–1499 XP\n"
        "<b>💰 Лимит на создание чеков:</b> до 200 000 монет/день\n"
        "<b>🎁 Реферальный бонус:</b> 10% от пополнений, 3% от выполнения заданий.\n\n"
        "<b>Уровень 3 (🌟 Мастер заданий):</b>\n"
        "<b>📊 Условия:</b> 1500–4999 XP\n"
        "<b>💰 Лимит на создание чеков:</b> до 500 000 монет/день\n"
        "<b>🎁 Реферальный бонус:</b> 10% от пополнений, 5% от выполнения заданий.\n\n"
        "<b>Уровень 4 (🧙 Гуру подписок):</b>\n"
        "<b>📊 Условия:</b> 5000–9999 XP\n"
        "<b>💰 Лимит на создание чеков:</b> до 2 500 000 монет/день\n"
        "<b>🎁 Реферальный бонус:</b> 10% от пополнений, 7% от выполнения заданий.\n\n"
        "<b>Уровень 5 (🪙 Мастер над монетой):</b>\n"
        "<b>📊 Условия:</b> 10000+ XP\n"
        "<b>💰 Лимит на чеки:</b> ♾️\n"
        "<b>🎁 Реферальный бонус:</b> 10% от пополнений, 10% от выполнения заданий.\n\n"
        "<b>Уровень 6 (🎓 Реферальный специалист):</b>\n"
        "<b>📊 Условия:</b> 100 рефералов\n"
        "<b>💰 Лимит на создание чеков:</b> ♾️\n"
        "<b>🎁 Реферальный бонус:</b> 12.5% от пополнений, 12.5% от выполнения заданий.\n\n"
        "<b>Уровень 7 (👑 Властелин пиара):</b>\n"
        "<b>📊 Условия:</b> 500 рефералов\n"
        "<b>💰 Лимит на создание чеков:</b> ♾️\n"
        "<b>🎁 Реферальный бонус:</b> 15% от пополнений, 15% от выполнения заданий."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="levels")]
    ])
    await answer_and_delete(callback, text, reply_markup=kb)


@router.callback_query(F.data == "cabinet_back")
async def cabinet_back(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    level_info = await db.get_level_info(user["xp"])
    text = (
        f"👤 <b>Ваш кабинет:</b>\n\n"
        f"🆔 Мой ID: <code>{user['user_id']}</code>\n"
        f"📈 Уровень: {level_info['name']} {user['xp']}/∞ XP\n"
        f"💰 Баланс: {format_balance(user['balance'])} LICH"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="deposit"),
            InlineKeyboardButton(text="🔗 Реферальная система", callback_data="referrals"),
        ],
        [
            InlineKeyboardButton(text="📈 Уровневая система", callback_data="levels"),
            InlineKeyboardButton(text="📝 Мои задания", callback_data="my_tasks"),
        ],
        [
            InlineKeyboardButton(text="🌎 Изменить язык", callback_data="change_lang"),
            InlineKeyboardButton(text="🔕 Отключить уведомления", callback_data="toggle_notif"),
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")],
    ])
    await answer_and_delete(callback, text, reply_markup=kb)


# ==================== ЗАГЛУШКИ ====================

@router.message(F.text.in_({
    "🧾Чеки", "👥 ОП (Проверка подписки)", "🔗Полезные ссылки",
    "📝Рекламировать", "📊Статистика", "ℹ️Инструкция"
}))
async def stub_handlers(message: Message):
    await message.answer("Раздел в разработке. Скоро будет доступен!")


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "my_tasks")
@router.callback_query(F.data == "change_lang")
@router.callback_query(F.data == "toggle_notif")
async def stubs_cb(callback: CallbackQuery):
    await callback.answer("В разработке", show_alert=True)


# ==================== MAIN ====================

async def main():
    await db.init_db()
    logger.info("Database initialized")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
