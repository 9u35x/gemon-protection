import asyncio
import logging
import os
import re
import random
import sqlite3
from collections import defaultdict, deque

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatPermissions,
)
from aiogram.enums import ChatMemberStatus

from ai_chat import ask_ai, is_ai_ready

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
BOT_NAME = "جيمون"
RIGHTS = "@fbb24"
PRIMARY_DEVELOPER = 815777525
DB_NAME = "jaimon.db"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jaimon")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
message_cache = defaultdict(lambda: defaultdict(lambda: deque(maxlen=5)))
# ألعاب مؤقتة: chat_id -> state
game_state = {}

db = sqlite3.connect(DB_NAME, check_same_thread=False)
db.row_factory = sqlite3.Row

db.executescript("""
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    enabled INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    chat_id INTEGER,
    name TEXT,
    value INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id,name)
);
CREATE TABLE IF NOT EXISTS developers (
    user_id INTEGER PRIMARY KEY,
    role TEXT DEFAULT 'developer'
);
CREATE TABLE IF NOT EXISTS roles (
    chat_id INTEGER,
    user_id INTEGER,
    role TEXT,
    PRIMARY KEY(chat_id,user_id)
);
CREATE TABLE IF NOT EXISTS warnings (
    chat_id INTEGER,
    user_id INTEGER,
    count INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id,user_id)
);
CREATE TABLE IF NOT EXISTS points (
    chat_id INTEGER,
    user_id INTEGER,
    points INTEGER DEFAULT 0,
    messages INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id,user_id)
);
CREATE TABLE IF NOT EXISTS public_replies (
    trigger TEXT PRIMARY KEY,
    answer TEXT
);
CREATE TABLE IF NOT EXISTS group_replies (
    chat_id INTEGER,
    trigger TEXT,
    answer TEXT,
    PRIMARY KEY(chat_id,trigger)
);
CREATE TABLE IF NOT EXISTS global_ban (
    user_id INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS force_sub (
    channel TEXT PRIMARY KEY
);
""")
db.execute(
    "INSERT OR IGNORE INTO developers(user_id,role) VALUES (?,?)",
    (PRIMARY_DEVELOPER, "primary"),
)
db.commit()


def set_setting(chat_id, name, value):
    db.execute(
        """INSERT INTO settings(chat_id,name,value) VALUES(?,?,?)
           ON CONFLICT(chat_id,name) DO UPDATE SET value=excluded.value""",
        (chat_id, name, int(value)),
    )
    db.commit()


def get_setting(chat_id, name, default=0):
    row = db.execute(
        "SELECT value FROM settings WHERE chat_id=? AND name=?",
        (chat_id, name),
    ).fetchone()
    return row["value"] if row else default


def ensure_group(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return
    db.execute(
        """INSERT INTO groups(chat_id,title) VALUES(?,?)
           ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title""",
        (message.chat.id, message.chat.title or ""),
    )
    db.commit()


def is_developer(user_id: int) -> bool:
    if user_id == PRIMARY_DEVELOPER:
        return True
    row = db.execute(
        "SELECT user_id FROM developers WHERE user_id=?", (user_id,)
    ).fetchone()
    return row is not None


def is_primary_developer(user_id: int) -> bool:
    return user_id == PRIMARY_DEVELOPER


def set_role(chat_id, user_id, role):
    if role == "member":
        db.execute(
            "DELETE FROM roles WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )
    else:
        db.execute(
            """INSERT INTO roles(chat_id,user_id,role) VALUES(?,?,?)
               ON CONFLICT(chat_id,user_id) DO UPDATE SET role=excluded.role""",
            (chat_id, user_id, role),
        )
    db.commit()



def norm_trigger(s: str) -> str:
    return " ".join((s or "").strip().split())


def find_public_reply(text: str):
    t = norm_trigger(text)
    if not t:
        return None
    row = db.execute(
        "SELECT answer FROM public_replies WHERE trigger=?",
        (t,),
    ).fetchone()
    if row:
        return row["answer"]
    rows = db.execute("SELECT trigger, answer FROM public_replies").fetchall()
    for r in rows:
        nt = norm_trigger(r["trigger"])
        if nt == t or nt.casefold() == t.casefold():
            return r["answer"]
    return None


def find_group_reply(chat_id: int, text: str):
    t = norm_trigger(text)
    if not t:
        return None
    row = db.execute(
        "SELECT answer FROM group_replies WHERE chat_id=? AND trigger=?",
        (chat_id, t),
    ).fetchone()
    if row:
        return row["answer"]
    rows = db.execute(
        "SELECT trigger, answer FROM group_replies WHERE chat_id=?",
        (chat_id,),
    ).fetchall()
    for r in rows:
        if norm_trigger(r["trigger"]) == t:
            return r["answer"]
    return None


def get_role(chat_id, user_id):
    if is_developer(user_id):
        return "developer"
    row = db.execute(
        "SELECT role FROM roles WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    return row["role"] if row else "member"


ROLE_AR = {
    "member": "عضو",
    "vip": "مميز",
    "admin": "ادمن",
    "manager": "مدير",
    "creator": "منشئ",
    "owner": "مالك",
    "developer": "مطور",
    "secondary": "مطور ثانوي",
    "primary": "مطور أساسي",
}

OPEN_PERMS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)

LOCKS = {
    "الروابط": "links",
    "التاك": "tags",
    "البوتات": "bots",
    "التكرار": "repeat",
    "الصور": "photos",
    "الفيديو": "videos",
    "الملفات": "files",
    "الملصقات": "stickers",
    "التوجيه": "forwarding",
    "التعديل": "editing",
}

RANK_COMMANDS = {
    "رفع مميز": "vip",
    "تنزيل مميز": "member",
    "رفع ادمن": "admin",
    "رفع أدمن": "admin",
    "تنزيل ادمن": "member",
    "تنزيل أدمن": "member",
    "رفع مشرف": "admin",
    "تنزيل مشرف": "member",
    "رفع مدير": "manager",
    "تنزيل مدير": "member",
    "رفع منشئ": "creator",
    "تنزيل منشئ": "member",
    "رفع مالك": "owner",
    "تنزيل مالك": "member",
}

RIDDLES = [
    ("ما الشيء الذي يمشي بلا أرجل؟", "الوقت"),
    ("ما هو الشيء الذي له أسنان ولا يعض؟", "المشط"),
    ("شيء إذا أكلته كله تموت؟", "الجوع"),
    ("ما هو الشيء الذي كلما أخذت منه كبر؟", "الحفرة"),
    ("طائر يطير بلا جناح؟", "الخيال"),
]

GUESS_WORDS = ["تفاح", "قمر", "كتاب", "شمس", "نهر", "جبل", "بحر", "ورد"]


async def telegram_admin(message: Message, user_id=None) -> bool:
    if user_id is None:
        user_id = message.from_user.id
    try:
        member = await bot.get_chat_member(message.chat.id, user_id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        )
    except Exception:
        return False


async def require_admin(message: Message) -> bool:
    if is_developer(message.from_user.id):
        return True
    if message.chat.type not in ("group", "supergroup"):
        await message.reply("❌ هذا الأمر للمجموعات.")
        return False
    if not await telegram_admin(message):
        await message.reply("❌ هذا الأمر للمشرفين فقط.")
        return False
    return True


async def require_developer(message: Message) -> bool:
    if not is_developer(message.from_user.id):
        await message.reply("❌ هذا الأمر للمطورين فقط.")
        return False
    return True


async def get_target(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    parts = (message.text or "").split()
    for part in reversed(parts):
        if part.lstrip("-").isdigit():
            return int(part)
    return None


async def check_force_sub(user_id: int) -> list:
    """يرجع قائمة القنوات غير المشترك بها"""
    rows = db.execute("SELECT channel FROM force_sub").fetchall()
    missing = []
    for row in rows:
        ch = row["channel"].strip()
        if not ch:
            continue
        try:
            # يقبل @channel أو -100id
            member = await bot.get_chat_member(ch, user_id)
            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.KICKED,
            ):
                missing.append(ch)
        except Exception:
            # إذا البوت مو ادمن بالقناة نتجاهل التحقق لهذه القناة
            log.warning("force_sub check failed for %s", ch)
    return missing


def force_sub_keyboard(channels: list):
    buttons = []
    for ch in channels:
        link = ch if ch.startswith("http") else f"https://t.me/{ch.lstrip('@')}"
        buttons.append(
            [InlineKeyboardButton(text=f"📢 اشترك: {ch}", url=link)]
        )
    buttons.append(
        [InlineKeyboardButton(text="✅ تحققت من الاشتراك", callback_data="force_check")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🛡 م1 الحماية", callback_data="menu_1"),
                InlineKeyboardButton(text="👮 م2 الأدمنية", callback_data="menu_2"),
            ],
            [
                InlineKeyboardButton(text="👑 م3 المدراء", callback_data="menu_3"),
                InlineKeyboardButton(text="🎮 م4 الألعاب", callback_data="menu_4"),
            ],
            [
                InlineKeyboardButton(text="⚙️ م5 الإعدادات", callback_data="menu_5"),
                InlineKeyboardButton(text="🔧 م6 المطور", callback_data="menu_6"),
            ],
        ]
    )


def back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ الرئيسية", callback_data="home")]]
    )


def developer_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 الإحصائيات", callback_data="dev_stats")],
            [InlineKeyboardButton(text="👨‍💻 المطورين", callback_data="dev_list")],
            [
                InlineKeyboardButton(text="➕ مطور", callback_data="dev_add"),
                InlineKeyboardButton(text="➖ مطور", callback_data="dev_remove"),
            ],
            [InlineKeyboardButton(text="📡 الاشتراك الإجباري", callback_data="dev_force")],
            [InlineKeyboardButton(text="📋 الردود العامة", callback_data="dev_replies")],
            [InlineKeyboardButton(text="📢 إذاعة", callback_data="dev_broadcast")],
            [InlineKeyboardButton(text="↩️ الرئيسية", callback_data="home")],
        ]
    )


MENUS = {
    "1": "🛡 الحماية\n\nقفل/فتح الروابط، التاك، التكرار، الصور، الفيديو، الملفات، الملصقات، التوجيه",
    "2": "👮 الأدمنية\n\nتفعيل/تعطيل\nحظر طرد كتم تقييد + إلغاء (بالرد)\nانذار كشف تثبيت مسح\nرفع/تنزيل رتب",
    "3": "👑 الرتب\n\nرفع مميز/ادمن/مدير/منشئ/مالك\nالمميزين الادمنية المدراء المنشئين المالكين",
    "4": (
        "🎮 الألعاب + الذكاء\n\n"
        "تفعيل الذكاء / تعطيل الذكاء\n"
        "جيمون + سؤالك\n"
        "أو رد على رسالة البوت\n\n"
        "/dice نرد\n"
        "/rps حجر ورقة مقص\n"
        "حزورة\n"
        "خمن\n"
        "روليت\n"
        "حظ\n"
        "ترتيب\n"
        "نقاطي"
    ),
    "5": "⚙️ الإعدادات\n\nايدي رتبتي معلوماتي نقاطي\nاوامر جيمون",
    "6": (
        "🔧 المطور (خاص)\n\n"
        "لوحة المطور\n"
        "المطورين\n"
        "اضافة مطور ID\n"
        "مسح مطور ID\n"
        "اضافة مطور ثانوي ID\n"
        "اضف رد عام كلمة | الرد\n"
        "مسح رد عام كلمة\n"
        "الردود العامة\n"
        "اضف اشتراك @channel\n"
        "مسح اشتراك @channel\n"
        "الاشتراك الاجباري\n"
        "اذاعة (بالرد)\n"
        "حظر عام ID"
    ),
}


@dp.message(CommandStart())
async def start(message: Message):
    missing = await check_force_sub(message.from_user.id)
    if missing and not is_developer(message.from_user.id):
        await message.answer(
            "🔐 للاستخدام يجب الاشتراك بالقنوات التالية:",
            reply_markup=force_sub_keyboard(missing),
        )
        return
    await message.answer(
        f"🤖 أهلاً بك في {BOT_NAME}\n\n🧠 الذكاء: جيمون سؤالك\nحقوق: {RIGHTS}",
        reply_markup=main_keyboard(),
    )


@dp.callback_query(F.data == "force_check")
async def force_check_cb(callback: CallbackQuery):
    missing = await check_force_sub(callback.from_user.id)
    if missing:
        await callback.answer("ما زلت غير مشترك في كل القنوات", show_alert=True)
        try:
            await callback.message.edit_reply_markup(
                reply_markup=force_sub_keyboard(missing)
            )
        except Exception:
            pass
        return
    await callback.message.edit_text(
        f"✅ تم التحقق.\n\n🤖 أهلاً بك في {BOT_NAME}",
        reply_markup=main_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):
    await callback.message.edit_text(
        f"🤖 {BOT_NAME}\n\nاختر القسم:", reply_markup=main_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("menu_"))
async def menu(callback: CallbackQuery):
    number = callback.data.split("_")[1]
    if number == "6" and not is_developer(callback.from_user.id):
        await callback.answer("❌ للمطورين فقط.", show_alert=True)
        return
    await callback.message.edit_text(MENUS[number], reply_markup=back_keyboard())
    await callback.answer()


@dp.message(Command("dice"))
async def cmd_dice(message: Message):
    await message.answer_dice(emoji="🎲")


@dp.message(Command("rps"))
async def cmd_rps(message: Message):
    await message.answer(
        "🎮 جيمون اختار:\n" + random.choice(["🪨 حجر", "📄 ورقة", "✂️ مقص"])
    )


@dp.my_chat_member()
async def bot_added(event):
    if event.chat.type not in ("group", "supergroup"):
        return
    if event.new_chat_member.status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
    ):
        try:
            await bot.send_message(
                event.chat.id,
                f"🤖 أهلاً، أنا {BOT_NAME}.\nارفعني مشرف واكتب: تفعيل\nحقوق: {RIGHTS}",
            )
        except Exception:
            pass


@dp.message(F.text)
async def text_router(message: Message):
    text = (message.text or "").strip()
    if not text:
        return
    low = text
    chat = message.chat
    is_group = chat.type in ("group", "supergroup")
    uid = message.from_user.id

    # ========== AI أولاً (حتى ما ينبلع) ==========
    if low in {"تفعيل الذكاء", "تفعيل ذكاء", "تفعيل ai"}:
        if not is_group:
            await message.reply("استخدم داخل المجموعة.")
            return
        if not await require_admin(message):
            return
        set_setting(chat.id, "ai_chat", 1)
        st = "المفتاح جاهز ✅" if is_ai_ready() else "⚠️ أضف GEMINI_API_KEY في Railway"
        await message.reply(f"✅ تم تفعيل الذكاء.\n{st}\n\nاكتب:\nجيمون سؤالك")
        return

    if low in {"تعطيل الذكاء", "تعطيل ذكاء"}:
        if is_group and await require_admin(message):
            set_setting(chat.id, "ai_chat", 0)
            await message.reply("🔕 تم تعطيل الذكاء.")
        return

    if low in {"حالة الذكاء", "الذكاء"}:
        await message.reply(
            f"🧠 المفتاح: {'✅' if is_ai_ready() else '❌'}\n"
            f"المجموعة: {'مفعل' if (is_group and get_setting(chat.id, 'ai_chat', 0)) else 'معطل'}"
        )
        return

    ai_question = None
    for p in ("جيمون ", "يا جيمون ", "اسأل ", "اسال "):
        if text.startswith(p):
            ai_question = text[len(p):].strip()
            break


    if low in {"احصائيات", "إحصائيات", "الاحصائيات", "الإحصائيات", "stats"}:
        try:
            gcount = db.execute("SELECT COUNT(*) AS c FROM groups").fetchone()["c"]
        except Exception:
            gcount = 0
        try:
            # users who have points rows
            ucount = db.execute("SELECT COUNT(DISTINCT user_id) AS c FROM points").fetchone()["c"]
        except Exception:
            ucount = 0
        try:
            rcount = db.execute("SELECT COUNT(*) AS c FROM public_replies").fetchone()["c"]
        except Exception:
            rcount = 0
        try:
            grcount = db.execute("SELECT COUNT(*) AS c FROM group_replies").fetchone()["c"]
        except Exception:
            grcount = 0
        enabled = 0
        try:
            rows = db.execute("SELECT enabled FROM groups").fetchall()
            enabled = sum(1 for r in rows if r["enabled"])
        except Exception:
            pass
        local_pts = 0
        if is_group:
            try:
                local_pts = db.execute(
                    "SELECT COUNT(*) AS c FROM points WHERE chat_id=?",
                    (chat.id,),
                ).fetchone()["c"]
            except Exception:
                pass
        msg = (
            "📊 إحصائيات جيمون\n\n"
            f"👥 مجموعات محفوظة: {gcount}\n"
            f"✅ مجموعات مفعلة: {enabled}\n"
            f"🧑 مستخدمين (نقاط): {ucount}\n"
            f"💬 ردود عامة: {rcount}\n"
            f"💬 ردود مجموعات: {grcount}\n"
        )
        if is_group:
            msg += f"⭐ أعضاء لديهم نقاط هنا: {local_pts}\n"
        msg += f"\n🧠 الذكاء: {'جاهز' if is_ai_ready() else 'لا مفتاح'}"
        await message.reply(msg)
        return


    if ai_question is not None:
        if is_group and not (get_setting(chat.id, "ai_chat", 0) or is_developer(uid)):
            await message.reply("الذكاء مطفأ. اكتب: تفعيل الذكاء")
            return
        if not is_ai_ready():
            await message.reply("❌ أضف GEMINI_API_KEY في Railway Variables ثم Redeploy")
            return
        wait = await message.reply("⏳ جيمون يفكر...")
        try:
            answer = await asyncio.wait_for(asyncio.to_thread(ask_ai, ai_question), timeout=25)
        except asyncio.TimeoutError:
            answer = "\u23f1\ufe0f انتهى الوقت — جرب مرة ثانية بعد شوي."
        try:
            await wait.edit_text(answer)
        except Exception:
            await message.reply(answer)
        return


    # اشتراك إجباري في الخاص فقط (ما يمنع أوامر المطور)
    if chat.type == "private" and not is_developer(uid):
        if not low.startswith(("اضف", "اضافة", "مسح", "لوحة", "المطورين", "اذاعة", "حظر عام")):
            missing = await check_force_sub(uid)
            if missing and low not in {"/start"}:
                # السماح بالقوائم العامة بعد الاشتراك فقط عند أوامر عامة
                pass  # تم الفحص في /start؛ هنا لا نقطع كل شيء

    # ----- قوائم -----
    if low in {"اوامر", "أوامر", "الاوامر", "الأوامر"}:
        await message.answer(
            f"🤖 {BOT_NAME}\n\nاختر القسم:", reply_markup=main_keyboard()
        )
        return

    if low in {"م1", "م2", "م3", "م4", "م5", "م6"}:
        num = low[-1]
        if num == "6" and not is_developer(uid):
            await message.reply("❌ للمطورين فقط.")
            return
        await message.answer(MENUS[num], reply_markup=back_keyboard())
        return

    # ===================== ألعاب =====================
    if low in {"حزورة", "احجية", "أحجية"}:
        q, a = random.choice(RIDDLES)
        game_state[chat.id] = {"type": "riddle", "answer": a}
        await message.reply(f"🧠 حزورة:\n{q}\n\nاكتب الجواب مباشرة.")
        return

    if low in {"خمن", "خمن كلمة"}:
        w = random.choice(GUESS_WORDS)
        game_state[chat.id] = {"type": "guess", "answer": w}
        await message.reply(
            f"🔤 خمن الكلمة!\nعدد الحروف: {len(w)}\nأول حرف: {w[0]}"
        )
        return

    if low == "روليت":
        await message.reply(
            "🎰 الروليت: " + random.choice(
                ["💎 جوهرة", "💰 ذهب", "😢 خسارة", "🎁 هدية", "⭐ نقطة إضافية"]
            )
        )
        return

    if low == "حظ":
        await message.reply(
            "🍀 حظك اليوم: " + random.choice(
                ["ممتاز 🔥", "جيد 🙂", "عادي 😐", "حاول لاحقاً 😅", "أسطوري 👑"]
            )
        )
        return

    if low == "ترتيب":
        word = random.choice(GUESS_WORDS)
        letters = list(word)
        random.shuffle(letters)
        game_state[chat.id] = {"type": "order", "answer": word}
        await message.reply(
            "🔀 رتّب الحروف لتكوين كلمة:\n" + " ".join(letters)
        )
        return

    if low in {"حجر", "ورقة", "مقص"}:
        bot_choice = random.choice(["حجر", "ورقة", "مقص"])
        user = low
        if user == bot_choice:
            res = "تعادل 🤝"
        elif (
            (user == "حجر" and bot_choice == "مقص")
            or (user == "ورقة" and bot_choice == "حجر")
            or (user == "مقص" and bot_choice == "ورقة")
        ):
            res = "فوزت 🎉"
        else:
            res = "خسرت 😅"
        await message.reply(f"أنت: {user}\nجيمون: {bot_choice}\nالنتيجة: {res}")
        return

    # تحقق جواب الألعاب
    st = game_state.get(chat.id)
    if st and st.get("type") in {"riddle", "guess", "order"}:
        if low.replace(" ", "") == st["answer"].replace(" ", ""):
            game_state.pop(chat.id, None)
            if is_group:
                db.execute(
                    """INSERT INTO points(chat_id,user_id,points,messages)
                       VALUES(?,?,5,0)
                       ON CONFLICT(chat_id,user_id)
                       DO UPDATE SET points=points+5""",
                    (chat.id, uid),
                )
                db.commit()
            await message.reply("✅ صحيح! +5 نقاط")
            return

    # ----- ردود محفوظة (خاص + مجموعة) -----
    ans = find_public_reply(text)
    if ans:
        await message.reply(ans)
        return
    if is_group:
        gans = find_group_reply(chat.id, text)
        if gans:
            await message.reply(gans)
            return

    # ===================== تفعيل =====================
    if low in {"تفعيل", "تفعيل البوت"}:
        if not is_group:
            return
        if not await require_admin(message):
            return
        ensure_group(message)
        db.execute("UPDATE groups SET enabled=1 WHERE chat_id=?", (chat.id,))
        db.commit()
        await message.reply(f"✅ تم تفعيل {BOT_NAME}.")
        return

    if low in {"تعطيل", "تعطيل البوت"}:
        if not is_group:
            return
        if not await require_admin(message):
            return
        db.execute("UPDATE groups SET enabled=0 WHERE chat_id=?", (chat.id,))
        db.commit()
        await message.reply("🔕 تم تعطيل البوت.")
        return

    # قفل/فتح
    if low.startswith("قفل ") or low.startswith("فتح "):
        if not is_group:
            return
        if not await require_admin(message):
            return
        action, _, item = low.partition(" ")
        item = item.strip()
        if item not in LOCKS:
            await message.reply("❌ مثال: قفل الروابط")
            return
        set_setting(chat.id, LOCKS[item], 1 if action == "قفل" else 0)
        await message.reply(f"✅ تم {action} {item}.")
        return

    # مسح
    if low == "مسح" or (low.startswith("مسح ") and not low.startswith("مسح مطور") and not low.startswith("مسح رد") and not low.startswith("مسح اشتراك")):
        if is_group and await require_admin(message):
            if message.reply_to_message:
                try:
                    await message.reply_to_message.delete()
                    await message.delete()
                except Exception:
                    await message.reply("❌ تعذر المسح.")
                return
            parts = low.split()
            if len(parts) >= 2 and parts[1].isdigit():
                count = max(1, min(int(parts[1]), 100))
                deleted = 0
                mid = message.message_id
                for i in range(1, count + 1):
                    try:
                        await bot.delete_message(chat.id, mid - i)
                        deleted += 1
                    except Exception:
                        pass
                try:
                    await message.delete()
                except Exception:
                    pass
                try:
                    m = await bot.send_message(chat.id, f"🧹 تم مسح {deleted} رسالة.")
                    await asyncio.sleep(2)
                    await m.delete()
                except Exception:
                    pass
                return
            await message.reply("❌ مسح 20 أو مسح بالرد")
            return

    if low in {"تثبيت"}:
        if not is_group:
            return
        if not await require_admin(message):
            return
        if not message.reply_to_message:
            await message.reply("⚠️ رد على الرسالة.")
            return
        try:
            await message.reply_to_message.pin(disable_notification=True)
            await message.reply("📌 تم التثبيت.")
        except Exception:
            await message.reply("❌ تعذر التثبيت.")
        return

    if low in {"الغاء تثبيت", "إلغاء تثبيت"}:
        if not is_group:
            return
        if not await require_admin(message):
            return
        try:
            if message.reply_to_message:
                await message.reply_to_message.unpin()
            else:
                await bot.unpin_all_chat_messages(chat.id)
            await message.reply("📌 تم إلغاء التثبيت.")
        except Exception:
            await message.reply("❌ تعذر.")
        return

    # إدارة
    mod_starts = [
        ("الغاء حظر", "unban"),
        ("إلغاء حظر", "unban"),
        ("حظر", "ban"),
        ("طرد", "kick"),
        ("الغاء كتم", "unmute"),
        ("إلغاء كتم", "unmute"),
        ("كتم", "mute"),
        ("الغاء تقييد", "unrestrict"),
        ("إلغاء تقييد", "unrestrict"),
        ("تقييد", "restrict"),
    ]
    for prefix, action in mod_starts:
        if low == prefix or low.startswith(prefix + " "):
            if not is_group:
                return
            if not await require_admin(message):
                return
            target = await get_target(message)
            if not target:
                await message.reply("⚠️ بالرد أو مع ID.")
                return
            try:
                if action == "ban":
                    await bot.ban_chat_member(chat.id, target)
                    ans = "🚫 تم الحظر."
                elif action == "unban":
                    await bot.unban_chat_member(chat.id, target, only_if_banned=True)
                    ans = "✅ إلغاء الحظر."
                elif action == "kick":
                    await bot.ban_chat_member(chat.id, target)
                    await bot.unban_chat_member(chat.id, target, only_if_banned=True)
                    ans = "👢 تم الطرد."
                elif action == "mute":
                    await bot.restrict_chat_member(
                        chat.id, target, permissions=ChatPermissions(can_send_messages=False)
                    )
                    ans = "🔇 تم الكتم."
                elif action == "unmute":
                    await bot.restrict_chat_member(chat.id, target, permissions=OPEN_PERMS)
                    ans = "🔊 إلغاء الكتم."
                elif action == "restrict":
                    await bot.restrict_chat_member(
                        chat.id, target, permissions=ChatPermissions(can_send_messages=False)
                    )
                    ans = "🔒 تم التقييد."
                else:
                    await bot.restrict_chat_member(chat.id, target, permissions=OPEN_PERMS)
                    ans = "🔓 إلغاء التقييد."
                await message.reply(ans)
            except Exception as e:
                log.error(e)
                await message.reply("❌ تأكد من صلاحيات البوت.")
            return

    if low in {"انذار", "تحذير"} or low.startswith("انذار ") or low.startswith("تحذير "):
        if not is_group:
            return
        if not await require_admin(message):
            return
        target = await get_target(message)
        if not target:
            await message.reply("⚠️ بالرد.")
            return
        row = db.execute(
            "SELECT count FROM warnings WHERE chat_id=? AND user_id=?",
            (chat.id, target),
        ).fetchone()
        count = (row["count"] if row else 0) + 1
        db.execute(
            """INSERT INTO warnings(chat_id,user_id,count) VALUES(?,?,?)
               ON CONFLICT(chat_id,user_id) DO UPDATE SET count=excluded.count""",
            (chat.id, target, count),
        )
        db.commit()
        await message.reply(f"⚠️ إنذار. العدد: {count}")
        return

    if low == "كشف" or low.startswith("كشف "):
        if not is_group:
            return
        target = await get_target(message) or uid
        role = get_role(chat.id, target)
        try:
            member = await bot.get_chat_member(chat.id, target)
            user = member.user
            uname = f"@{user.username}" if user.username else "لا يوجد"
            w = db.execute(
                "SELECT count FROM warnings WHERE chat_id=? AND user_id=?",
                (chat.id, target),
            ).fetchone()
            p = db.execute(
                "SELECT points,messages FROM points WHERE chat_id=? AND user_id=?",
                (chat.id, target),
            ).fetchone()
            await message.reply(
                f"🔍 {user.full_name}\n🔗 {uname}\n🆔 {user.id}\n"
                f"🏅 {ROLE_AR.get(role, role)}\n"
                f"⚠️ إنذارات: {w['count'] if w else 0}\n"
                f"⭐ {p['points'] if p else 0} | 💬 {p['messages'] if p else 0}"
            )
        except Exception:
            await message.reply(f"🆔 {target}\n🏅 {ROLE_AR.get(role, role)}")
        return

    for cmd in sorted(RANK_COMMANDS.keys(), key=len, reverse=True):
        if low == cmd or low.startswith(cmd + " "):
            if not is_group:
                return
            if not await require_admin(message):
                return
            target = await get_target(message)
            if not target:
                await message.reply("⚠️ بالرد.")
                return
            set_role(chat.id, target, RANK_COMMANDS[cmd])
            await message.reply(f"✅ {cmd}")
            return

    role_lists = {
        "المميزين": "vip",
        "الادمنية": "admin",
        "الأدمنية": "admin",
        "المدراء": "manager",
        "المنشئين": "creator",
        "المالكين": "owner",
    }
    if low in role_lists:
        if not is_group:
            return
        if not await require_admin(message):
            return
        rows = db.execute(
            "SELECT user_id FROM roles WHERE chat_id=? AND role=?",
            (chat.id, role_lists[low]),
        ).fetchall()
        if not rows:
            await message.reply(f"📋 لا يوجد {low}.")
            return
        body = "\n".join(f"• `{r['user_id']}`" for r in rows)
        await message.reply(f"📋 {low}\n\n{body}", parse_mode="Markdown")
        return

    if low in {"ايدي", "الايدي", "آيدي"}:
        user = message.reply_to_message.from_user if message.reply_to_message else message.from_user
        uname = f"@{user.username}" if user.username else "لا يوجد"
        role = get_role(chat.id, user.id) if is_group else ("developer" if is_developer(user.id) else "member")
        await message.reply(
            f"🆔 `{user.id}`\n👤 {user.full_name}\n🔗 {uname}\n🏅 {ROLE_AR.get(role, role)}",
            parse_mode="Markdown",
        )
        return

    if low in {"رتبتي", "الرتبة"}:
        role = get_role(chat.id, uid) if is_group else ("developer" if is_developer(uid) else "member")
        await message.reply(f"🏅 رتبتك: {ROLE_AR.get(role, role)}")
        return

    if low in {"معلوماتي", "معلومات"}:
        u = message.from_user
        uname = f"@{u.username}" if u.username else "لا يوجد"
        role = get_role(chat.id, u.id) if is_group else ("developer" if is_developer(u.id) else "member")
        p = None
        if is_group:
            p = db.execute(
                "SELECT points,messages FROM points WHERE chat_id=? AND user_id=?",
                (chat.id, u.id),
            ).fetchone()
        await message.reply(
            f"👤 {u.full_name}\n🆔 `{u.id}`\n🔗 {uname}\n🏅 {ROLE_AR.get(role, role)}\n"
            f"⭐ {p['points'] if p else 0} | 💬 {p['messages'] if p else 0}",
            parse_mode="Markdown",
        )
        return

    if low == "نقاطي":
        if not is_group:
            await message.reply("داخل المجموعة.")
            return
        row = db.execute(
            "SELECT points,messages FROM points WHERE chat_id=? AND user_id=?",
            (chat.id, uid),
        ).fetchone()
        await message.reply(
            f"⭐ نقاطك: {row['points'] if row else 0}\n💬 رسائلك: {row['messages'] if row else 0}"
        )
        return

    # ===================== مطورين =====================
    if low in {"لوحة المطور", "المطور"}:
        if not await require_developer(message):
            return
        await message.reply(
            f"🔧 لوحة {BOT_NAME}\nالأساسي: {PRIMARY_DEVELOPER}\nحقوق: {RIGHTS}",
            reply_markup=developer_keyboard(),
        )
        return

    if low in {"المطورين", "قائمة المطورين"}:
        if not await require_developer(message):
            return
        rows = db.execute("SELECT user_id, role FROM developers ORDER BY role").fetchall()
        lines = [f"• `{PRIMARY_DEVELOPER}` — أساسي"]
        for r in rows:
            if r["user_id"] == PRIMARY_DEVELOPER:
                continue
            lines.append(f"• `{r['user_id']}` — {r['role']}")
        await message.reply("👨‍💻 المطورون:\n\n" + "\n".join(lines), parse_mode="Markdown")
        return

    m = re.fullmatch(r"(اضافة|إضافة|اضف)\s+مطور\s+(\d+)", low)
    if m:
        if not is_primary_developer(uid):
            await message.reply("❌ المطور الأساسي فقط.")
            return
        new_id = int(m.group(2))
        db.execute(
            "INSERT OR REPLACE INTO developers(user_id,role) VALUES(?,?)",
            (new_id, "developer"),
        )
        db.commit()
        await message.reply(f"✅ تمت إضافة المطور: `{new_id}`", parse_mode="Markdown")
        return

    m = re.fullmatch(r"مسح\s+مطور\s+(\d+)", low)
    if m:
        if not is_primary_developer(uid):
            await message.reply("❌ المطور الأساسي فقط.")
            return
        new_id = int(m.group(1))
        if new_id == PRIMARY_DEVELOPER:
            await message.reply("❌ لا يمكن حذف الأساسي.")
            return
        db.execute("DELETE FROM developers WHERE user_id=?", (new_id,))
        db.commit()
        await message.reply(f"✅ تم حذف المطور: {new_id}")
        return

    m = re.fullmatch(r"(اضافة|إضافة|اضف)\s+مطور\s+ثانوي\s+(\d+)", low)
    if m:
        if not is_primary_developer(uid):
            await message.reply("❌ المطور الأساسي فقط.")
            return
        new_id = int(m.group(2))
        db.execute(
            "INSERT OR REPLACE INTO developers(user_id,role) VALUES(?,?)",
            (new_id, "secondary"),
        )
        db.commit()
        await message.reply(f"✅ مطور ثانوي: {new_id}")
        return

    m = re.fullmatch(r"مسح\s+مطور\s+ثانوي\s+(\d+)", low)
    if m:
        if not is_primary_developer(uid):
            await message.reply("❌ المطور الأساسي فقط.")
            return
        new_id = int(m.group(1))
        db.execute(
            "DELETE FROM developers WHERE user_id=? AND role='secondary'",
            (new_id,),
        )
        db.commit()
        await message.reply(f"✅ تم حذف الثانوي: {new_id}")
        return

    # ===================== ردود =====================
    # اضف رد عام |  / اضافة رد عام
    for prefix in ("اضف رد عام ", "اضافة رد عام ", "إضافة رد عام ", "اضف رد ", "اضافة رد "):
        if low.startswith(prefix) or text.startswith(prefix):
            if not await require_developer(message):
                return
            body = text[len(prefix):]
            if "|" not in body:
                await message.reply(
                    "الصيغة:\nاضف رد عام الكلمة | الرد\nأو\nاضف رد الكلمة | الرد"
                )
                return
            trigger, answer = body.split("|", 1)
            trigger, answer = norm_trigger(trigger), answer.strip()
            if not trigger or not answer:
                await message.reply("❌ الكلمة والرد مطلوبان.")
                return
            is_global = "عام" in prefix
            if is_global or not is_group:
                db.execute(
                    "INSERT OR REPLACE INTO public_replies(trigger,answer) VALUES(?,?)",
                    (trigger, answer),
                )
                db.commit()
                await message.reply(f"✅ رد عام:\n🔤 {trigger}\n💬 {answer}")
            else:
                db.execute(
                    """INSERT OR REPLACE INTO group_replies(chat_id,trigger,answer)
                       VALUES(?,?,?)""",
                    (chat.id, trigger, answer),
                )
                db.commit()
                await message.reply(f"✅ رد للمجموعة:\n🔤 {trigger}\n💬 {answer}")
            return

    for prefix in ("مسح رد عام ", "حذف رد عام ", "مسح رد ", "حذف رد "):
        if text.startswith(prefix) or low.startswith(prefix):
            if not await require_developer(message):
                return
            trigger = text[len(prefix):].strip()
            if "عام" in prefix or not is_group:
                db.execute("DELETE FROM public_replies WHERE trigger=?", (trigger,))
            else:
                db.execute(
                    "DELETE FROM group_replies WHERE chat_id=? AND trigger=?",
                    (chat.id, trigger),
                )
            db.commit()
            await message.reply(f"✅ تم مسح الرد: {trigger}")
            return

    if low in {"الردود العامة", "الردود"}:
        if not await require_developer(message):
            return
        rows = db.execute("SELECT trigger,answer FROM public_replies").fetchall()
        if not rows:
            await message.reply("لا توجد ردود عامة.\nأضف بـ:\nاضف رد عام مرحبا | هلا بيك")
            return
        body = "\n".join(f"• {r['trigger']} → {r['answer']}" for r in rows)
        await message.reply(f"📋 الردود العامة:\n\n{body}")
        return

    # ===================== اشتراك إجباري (مطورين) =====================
    for prefix in ("اضف اشتراك ", "اضافة اشتراك ", "إضافة اشتراك "):
        if text.startswith(prefix):
            if not await require_developer(message):
                return
            ch = text[len(prefix):].strip()
            if not ch:
                await message.reply("مثال: اضف اشتراك @channel")
                return
            db.execute("INSERT OR REPLACE INTO force_sub(channel) VALUES(?)", (ch,))
            db.commit()
            await message.reply(
                f"✅ تمت إضافة الاشتراك الإجباري:\n{ch}\n\n"
                "⚠️ خل البوت مشرف في القناة حتى يقدر يتحقق."
            )
            return

    if text.startswith("مسح اشتراك "):
        if not await require_developer(message):
            return
        ch = text[len("مسح اشتراك "):].strip()
        db.execute("DELETE FROM force_sub WHERE channel=?", (ch,))
        db.commit()
        await message.reply(f"✅ تم حذف: {ch}")
        return

    if low in {"الاشتراك الاجباري", "الاشتراك الإجباري", "قائمة الاشتراك"}:
        if not await require_developer(message):
            return
        rows = db.execute("SELECT channel FROM force_sub").fetchall()
        if not rows:
            await message.reply("لا توجد قنوات.\nاضف اشتراك @channel")
            return
        body = "\n".join(f"• {r['channel']}" for r in rows)
        await message.reply(f"📡 الاشتراك الإجباري:\n\n{body}")
        return

    m = re.fullmatch(r"حظر عام (\d+)", low)
    if m:
        if not await require_developer(message):
            return
        db.execute(
            "INSERT OR IGNORE INTO global_ban(user_id) VALUES(?)", (int(m.group(1)),)
        )
        db.commit()
        await message.reply(f"🌐 حظر عام: {m.group(1)}")
        return

    m = re.fullmatch(r"(الغاء|إلغاء) حظر عام (\d+)", low)
    if m:
        if not await require_developer(message):
            return
        db.execute("DELETE FROM global_ban WHERE user_id=?", (int(m.group(2)),))
        db.commit()
        await message.reply("✅ إلغاء الحظر العام.")
        return

    if low == "قائمة العام":
        if not await require_developer(message):
            return
        rows = db.execute("SELECT user_id FROM global_ban").fetchall()
        body = "\n".join(f"• {r['user_id']}" for r in rows) or "فارغة"
        await message.reply(f"🌐 الحظر العام:\n{body}")
        return

    if low == "اذاعة":
        if not await require_developer(message):
            return
        if not message.reply_to_message:
            await message.reply("⚠️ اذاعة بالرد على الرسالة.")
            return
        rows = db.execute("SELECT chat_id FROM groups WHERE enabled=1").fetchall()
        ok = fail = 0
        for row in rows:
            try:
                await bot.copy_message(
                    chat_id=row["chat_id"],
                    from_chat_id=message.chat.id,
                    message_id=message.reply_to_message.message_id,
                )
                ok += 1
            except Exception:
                fail += 1
            await asyncio.sleep(0.05)
        await message.reply(f"📢 ✅ {ok} | ❌ {fail}")
        return

    # ===================== حماية + ردود تلقائية =====================
    if not is_group:
        return

    ensure_group(message)
    row = db.execute(
        "SELECT enabled FROM groups WHERE chat_id=?", (chat.id,)
    ).fetchone()
    if not row or row["enabled"] != 1:
        return

    ans = find_public_reply(text)
    if ans:
        await message.reply(ans)
        return
    gans = find_group_reply(chat.id, text)
    if gans:
        await message.reply(gans)
        return

    if await telegram_admin(message) or is_developer(uid):
        db.execute(
            """INSERT INTO points(chat_id,user_id,points,messages) VALUES(?,?,1,1)
               ON CONFLICT(chat_id,user_id) DO UPDATE SET
               points=points+1, messages=messages+1""",
            (chat.id, uid),
        )
        db.commit()
        return

    if db.execute(
        "SELECT 1 FROM global_ban WHERE user_id=?", (uid,)
    ).fetchone():
        try:
            await bot.ban_chat_member(chat.id, uid)
        except Exception:
            pass
        return

    if get_setting(chat.id, "links") and message.text:
        if re.search(r"(https?://|t\.me/|www\.)", message.text, re.I):
            try:
                await message.delete()
            except Exception:
                pass
            return

    if get_setting(chat.id, "tags") and message.text and "@" in message.text:
        try:
            await message.delete()
        except Exception:
            pass
        return

    if get_setting(chat.id, "photos") and message.photo:
        try:
            await message.delete()
        except Exception:
            pass
        return

    if get_setting(chat.id, "videos") and message.video:
        try:
            await message.delete()
        except Exception:
            pass
        return

    if get_setting(chat.id, "files") and message.document:
        try:
            await message.delete()
        except Exception:
            pass
        return

    if get_setting(chat.id, "stickers") and message.sticker:
        try:
            await message.delete()
        except Exception:
            pass
        return

    if get_setting(chat.id, "forwarding") and message.forward_origin:
        try:
            await message.delete()
        except Exception:
            pass
        return

    if get_setting(chat.id, "repeat") and message.text:
        cache = message_cache[chat.id][uid]
        cache.append(message.text)
        if len(cache) >= 3:
            last = list(cache)[-3:]
            if last[0] == last[1] == last[2]:
                try:
                    await message.delete()
                except Exception:
                    pass
                cache.clear()
                return

    db.execute(
        """INSERT INTO points(chat_id,user_id,points,messages) VALUES(?,?,1,1)
           ON CONFLICT(chat_id,user_id) DO UPDATE SET
           points=points+1, messages=messages+1""",
        (chat.id, uid),
    )
    db.commit()


@dp.callback_query(F.data.startswith("dev_"))
async def developer_buttons(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ للمطورين فقط.", show_alert=True)
        return
    action = callback.data
    if action == "dev_stats":
        g = db.execute("SELECT COUNT(*) c FROM groups").fetchone()["c"]
        e = db.execute(
            "SELECT COUNT(*) c FROM groups WHERE enabled=1"
        ).fetchone()["c"]
        d = db.execute("SELECT COUNT(*) c FROM developers").fetchone()["c"]
        await callback.message.edit_text(
            f"📊 إحصائيات\n👥 {g}\n✅ مفعلة {e}\n👨‍💻 مطورين {d}",
            reply_markup=developer_keyboard(),
        )
    elif action == "dev_list":
        rows = db.execute("SELECT user_id,role FROM developers").fetchall()
        text = "👨‍💻 المطورون:\n\n" + "\n".join(
            f"• {r['user_id']} — {r['role']}" for r in rows
        )
        await callback.message.edit_text(text, reply_markup=developer_keyboard())
    elif action == "dev_add":
        await callback.message.answer("صيغة:\nاضافة مطور 123456789")
    elif action == "dev_remove":
        await callback.message.answer("صيغة:\nمسح مطور 123456789")
    elif action == "dev_force":
        rows = db.execute("SELECT channel FROM force_sub").fetchall()
        body = "\n".join(f"• {r['channel']}" for r in rows) or "لا توجد"
        await callback.message.edit_text(
            f"📡 الاشتراك الإجباري:\n{body}\n\n"
            "اضف اشتراك @channel\nمسح اشتراك @channel",
            reply_markup=developer_keyboard(),
        )
    elif action == "dev_replies":
        rows = db.execute("SELECT trigger,answer FROM public_replies").fetchall()
        body = "\n".join(f"• {r['trigger']} → {r['answer']}" for r in rows) or "لا توجد"
        await callback.message.edit_text(
            f"📋 الردود:\n{body}\n\nاضف رد عام كلمة | الرد",
            reply_markup=developer_keyboard(),
        )
    elif action == "dev_broadcast":
        await callback.message.answer("📢 اكتب اذاعة بالرد على الرسالة")
    await callback.answer()


async def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN مفقود")
        return
    me = await bot.get_me()
    print("=" * 45)
    print(f"🤖 {BOT_NAME} @{me.username}")
    print(f"Dev: {PRIMARY_DEVELOPER}")
    print("Privacy OFF + Admin required for groups")
    print("AI ready:", "YES" if is_ai_ready() else "NO (set GEMINI_API_KEY)")
    print("=" * 45)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
