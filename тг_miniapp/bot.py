# -*- coding: utf-8 -*-
"""
Телеграм-бот: бои, гача (суммоны), инвентарь, лидерборд, админка.
Весь проект в одном файле — удобно заливать на GitHub/хостинг.

ЗАПУСК:   python bot.py
При первом запуске сам доустановит библиотеки (aiogram, aiosqlite).

ХОСТИНГ / БЕЗОПАСНОСТЬ:
  Не храни реальный токен в публичном репозитории. Лучше задать переменные окружения:
    BOT_TOKEN  = токен бота
    ADMIN_IDS  = твои Telegram ID через запятую, напр. "123456789,987654321"
    DB_PATH    = путь к файлу базы (необязательно)
  Если переменных нет — берутся значения из констант ниже.
"""
import importlib
import subprocess
import sys

# ======================= АВТО-УСТАНОВКА БИБЛИОТЕК =======================
_REQUIRED = {"aiogram": "aiogram>=3.7,<4.0", "aiosqlite": "aiosqlite>=0.20.0"}


def _ensure_deps():
    missing = []
    for module, pip_name in _REQUIRED.items():
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(pip_name)
    if missing:
        print("Доустанавливаю библиотеки:", ", ".join(missing))
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "--disable-pip-version-check", *missing])
        importlib.invalidate_caches()


_ensure_deps()

import asyncio
import json
import logging
import math
import os
import random
import time

import html as _html

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.methods import EditMessageCaption, EditMessageText, SendMessage, SendPhoto
from aiogram.types import (
    BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats,
    CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, KeyboardButton, LabeledPrice, Message, PreCheckoutQuery,
    ReplyKeyboardMarkup,
)


# ===================== ЖИРНЫЙ ТЕКСТ ВЕЗДЕ (middleware) =====================
def _boldify(s: str) -> str:
    esc = _html.escape(s, quote=False)
    esc = esc.replace(chr(0xE000), "<s>").replace(chr(0xE001), "</s>")
    return f"<b>{esc}</b>"


class BoldMiddleware:
    """Оборачивает текст/подпись всех исходящих сообщений в <b>…</b>.
    Сентинелы chr(0xE000/0xE001) превращаются в <s>…</s> (зачёркивание)."""

    async def __call__(self, make_request, bot, method):
        upd = {}
        if isinstance(method, (SendMessage, EditMessageText)) and isinstance(getattr(method, "text", None), str):
            upd = {"text": _boldify(method.text), "parse_mode": "HTML"}
        elif isinstance(method, (SendPhoto, EditMessageCaption)) and isinstance(getattr(method, "caption", None), str):
            upd = {"caption": _boldify(method.caption), "parse_mode": "HTML"}
        if upd:
            method = method.model_copy(update=upd)
        return await make_request(bot, method)

# ============================== НАСТРОЙКИ ==============================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8832427200:AAHEwrjcStekNPR64C_4wi7SqOjz8xLa-LE")

# URL сервиса на Render.com — задай после деплоя через переменную окружения WEBAPP_URL
# Например: https://battle-bot.onrender.com
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

# Свой Telegram ID узнать у @userinfobot. Это ВЛАДЕЛЬЦЫ — их нельзя снять из бота.
# Дополнительных админов можно добавлять/удалять прямо в админке (хранятся в БД).
ADMIN_IDS: list[int] = [1018561747]
_env_admins = os.getenv("ADMIN_IDS", "")
if _env_admins.strip():
    ADMIN_IDS = [int(x) for x in _env_admins.replace(" ", "").split(",") if x.lstrip("-").isdigit()]

# Динамические админы (из БД), заполняется в db_init().
DYN_ADMINS: set[int] = set()

# Глобальная ссылка на экземпляр бота (для рассылки уведомлений), ставится в _main().
_BOT = None

DB_PATH = os.getenv("DB_PATH", "bot.db")

# Встроенная валюта — монеты
COIN_ICON = "💰"
START_COINS = 100
COIN_CURRENCY_ID = 0

# Бой
TURN_DELAY = 4.0
BATTLE_START_DELAY = 5
MAX_TURNS = 100
COIN_REWARD_WIN = (50, 100)    # монеты за победу
COIN_REWARD_LOSS = (10, 15)    # монеты за поражение

# Классы (слоты) предметов — один предмет каждого класса на юните, порядок = порядок поломки
ITEM_SLOTS = [
    ("⚔️", "оружие"),
    ("🧢", "шлем"),
    ("👕", "кольцо"),
    ("👖", "штаны"),
    ("👞", "ботинки"),
    ("💍", "кольцо"),
    ("🧣", "амулет"),
]
SLOT_EMOJIS = [e for e, _ in ITEM_SLOTS]
SLOT_NAME = dict(ITEM_SLOTS)

# Сентинелы для зачёркивания внутри жирного текста (см. BoldMiddleware)
S_OPEN = ""
S_CLOSE = ""


S_OPEN, S_CLOSE = "", ""


def strike(s):
    return chr(0xE000) + s + chr(0xE001)


# Суммон
SUMMON_REFRESH_SECONDS = 30 * 60
SUMMON_X10_DISCOUNT = 9

# Категории / ранги
CATEGORY_STEP = 100
MAX_CATEGORY = 10
RANK_MULTIPLIER = {1: 1.0, 2: 1.5, 3: 2.0, 4: 2.5, 5: 3.0, 6: 3.5, 7: 4.0, 8: 5.0, 9: 6.0, 10: 7.0}
CUP_REWARDS = {
    1: (1, 15, 1, 3), 2: (1, 15, 1, 3), 3: (1, 15, 1, 4), 4: (1, 15, 1, 5), 5: (1, 15, 2, 5),
    6: (1, 10, 3, 6), 7: (1, 10, 4, 7), 8: (1, 10, 4, 8), 9: (1, 10, 4, 9), 10: (1, 10, 4, 10),
}
CATEGORY_RANGE = {c: ((c - 1) * CATEGORY_STEP, c * CATEGORY_STEP) for c in range(1, MAX_CATEGORY + 1)}

SEP = "================================="

# ============================== ДОНАТ ==============================
# Для дочернего бота: задайте PARENT_BOT_USERNAME чтобы перенаправлять базовый донат
PARENT_BOT_USERNAME = os.getenv("PARENT_BOT_USERNAME", "")

# IS_CHILD=1 проставляется родителем при запуске дочернего бота (отдельный процесс).
# Дочерний бот не поднимает своих «детей» и не шлёт апдейт-лог в основной канал.
IS_CHILD = bool(os.getenv("IS_CHILD"))

DONATE_VIP_STARS     = 10
DONATE_X2COINS_STARS = 30
DONATE_X2LUCK_STARS  = 35

VIP_COIN_MULT = 1.3   # +30% ко всем монетам/валютам за бой
VIP_LUCK_MULT = 1.5   # x1.5 удача на суммоны/крейты

# ============================== ПОДПИСКИ (MyBots) ==============================
# "free" — тариф по умолчанию, навсегда: 1 бот, но с рекламой канала.
# Платные тарифы убирают рекламу ("ads": False).
SUB_PLANS = {
    "free":  {"label": "Free",   "stars_per_day": 0, "max_bots": 1, "can_donate": False, "discount": 0,   "ads": True},
    "basic": {"label": "Basic",  "stars_per_day": 1, "max_bots": 1, "can_donate": False, "discount": 0,   "ads": False},
    "pro":   {"label": "Pro",    "stars_per_day": 2, "max_bots": 3, "can_donate": True,  "discount": 0,   "ads": False,
              "support": "https://t.me/+UH1JZhbGrJwxYTMy"},
    "ultra": {"label": "Ultra",  "stars_per_day": 3, "max_bots": 5, "can_donate": True,  "discount": 0.5, "ads": False,
              "support": "https://t.me/+UH1JZhbGrJwxYTMy"},
}
FREE_SUB   = "free"
TRIAL_SUB  = "basic"
TRIAL_DAYS = 3

# ============================== РЕКЛАМА ==============================
# Реклама крутится В ДОЧЕРНИХ БОТАХ владельцев на бесплатном тарифе (Free)
# и продвигает основной канал. Родитель выставляет каждому дочернему боту
# переменную AD_CHANNEL: канал — если владелец на Free, пусто — если платный тариф.
# В основном боте AD_CHANNEL по умолчанию пуст → рекламы в нём нет.
AD_CHANNEL = os.getenv("AD_CHANNEL", "")
AD_EVERY_N_MSG       = 5         # каждые N сообщений пользователя — реклама
AD_BROADCAST_SECONDS = 30 * 60   # раз в 30 минут — реклама всем игрокам бота

# ============================== СОБЫТИЯ ==============================
EVENT_TYPES = {
    "luck":   "🍀Luck Boost🍀",
    "earn":   "💸Earn Boost💸",
    "power":  "⚔️Power Boost⚔️",
    "mana":   "☯️Mana Boost☯️",
    "wins":   "🏆Wins Boost🏆",
    "health": "❤️Health Boost❤️",
}
EVENT_BOT_EXEMPT = {"power", "mana", "health"}

_ACTIVE_EVENTS: list[dict] = []

# ============================== АПДЕЙТ-ЛОГ ==============================
# Меняй строку при каждом новом обновлении — бот пришлёт лог в канал ровно один раз.
CHANGELOG_VERSION = "2026-06-24-v6-perks"
CHANGELOG_CHANNEL = os.getenv("CHANGELOG_CHANNEL", "@L1meYT")
CHANGELOG_TEXT = """\
✨ Новые перки!

🔪 Last Miss — с шансом 2% оставляет врагу 10% его текущего HP
🦴 Bone Dance — каждая атака отнимает у врага 1-10% твоего текущего HP
🔮 Маг — каждый ход срабатывает случайный перк (кроме иммунитета)
🛡 Щит 1-5 — с шансом блокирует часть входящего урона (50-100%)
🧨 Сплеш 1-5 — задевает предметы и юнита, не получившие основной урон

Подробности — в разделе «📖 Об игре → 🎯 Перки».

— — — — —

🆓 Обновление: бесплатный тариф MyBots!

• Free-тариф навсегда: можно привязать 1 бота бесплатно
• В кабинете теперь видно «Привязано: N/лимит»
• В бесплатных дочерних ботах крутится реклама канала
  (каждые 5 сообщений и раз в 30 минут)
• Платные подписки (Basic/Pro/Ultra) убирают рекламу из ботов 🚫

— — — — —

💳 Большое обновление: ДОНАТ и MYBOTS!

⭐️ /donate — магазин за Telegram Stars:
• VIP (10⭐️) — +30% ко всем наградам + x1.5 удача
• x2 монеты (30⭐️) — удвоенная валюта за бои навсегда
• x2 удача (35⭐️) — удвоенная удача в суммонах/крейтах
• Специальные предложения на валюты от администратора
• Ultra-подписка даёт скидку 50% на все позиции

🤖 /mybots — личный кабинет:
• Привяжи своего бота к этому и управляй им
• Подписки Basic/Pro/Ultra (от 1⭐️/день)
• Пробный период 3 дня бесплатно
• Экспорт/импорт базы данных своего бота

🛠 Обновления для администраторов:
• Новая секция «Донат» — создавай кастомные предложения на валюты
• «Выдать игроку» в Абьюз-панели — юниты, предметы, валюта, донат и подписка по ID или @username
• При каждой покупке владелец бота получает уведомление в ЛС

— — — — —

🛡 Большое обновление: КЛАНЫ!

• Создавай клан (5000💰): фото, название, описание (до 20 человек)
• Находи и вступай в кланы (открытый вход или по заявкам)
• Настройки клана для лидера: заявки, описание, переименование (10000💰), кик
• Босс клана: выбери его навсегда, корми предметами из инвентаря, лечи (1000❤️ за 1000💰)
• Клановые бои: дерись с боссами других кланов (случайный или выбор)
  — HP босса не восстанавливается после боя, сломанные предметы теряются!

👹 Боссы теперь создаются в админке (как юниты).
⚡️ Админам: можно удалять активные события и завершать все разом.
🏆 Кубки за победу: ранги 1-5 → 1-15, ранги 6-10 → 1-10.

— — — — —

🆕 Обновление бота!

👾 Абьюз-панель (для админов):
• Рассылка сообщений всем игрокам и группам
• Раздача юнитов, предметов и валюты всем сразу
• Рандом-раздача — каждый игрок получает вещь с указанным шансом
• Запуск глобальных событий

⚡️ События (временные бусты для всех):
🍀 Luck Boost — удача на суммоны и монеты
💸 Earn Boost — больше монет с боёв
⚔️ Power Boost — увеличенный урон (PvP)
☯️ Mana Boost — выше шанс перков (PvP)
🏆 Wins Boost — больше кубков за победу
❤️ Health Boost — увеличенное HP (PvP)
Активные события отображаются на главном экране с таймером!

🤖 Новые бои с ботами:
• 3 уровня сложности: 😊 Лёгкий · 😐 Средний · 😤 Сложный
• Боты теперь тоже дают кубки (множитель зависит от сложности)
• Ничья = 0 кубков + награда как за победу
• Сила бота подбирается под твой ранг

Мощь ботов по рангам:
Ранг  | 😊 Лёгкий | 😐 Средний | 😤 Сложный
  1   |   0-15    |    0-30    |    0-60
  2   |   0-30    |    0-60    |   0-120
  3   |   0-50    |   0-100    |   0-200
  4   |  0-100    |   0-200    |   0-400
  5   |  0-150    |   0-300    |   0-600
  6   |  0-250    |   0-500    |   0-1000
  7   |  0-400    |   0-800    |   0-1600
  8   |  0-500    |   0-1000   |   0-2000
  9   |  0-650    |   0-1300   |   0-2600
  10  |  0-800    |   0-1600   |   0-3200
"""


def get_event_mult(etype: str, is_bot_battle: bool = False) -> float:
    if is_bot_battle and etype in EVENT_BOT_EXEMPT:
        return 1.0
    now = int(time.time())
    result = 1.0
    for ev in _ACTIVE_EVENTS:
        if ev["etype"] == etype and ev["end_time"] > now:
            result *= ev["multiplier"]
    return result


# ============================== СЛОЖНОСТИ БОТОВ ==============================
BOT_DIFF_INFO = {
    "easy":   {"label": "😊 Лёгкий",  "reward_mult": 0.2},
    "medium": {"label": "😐 Средний", "reward_mult": 0.5},
    "hard":   {"label": "😤 Сложный", "reward_mult": 1.0},
}
BOT_POWER_MAX = {
    "easy":   [15,  30,  50,  100, 150,  250,  400,  500,  650,  800],
    "medium": [30,  60,  100, 200, 300,  500,  800,  1000, 1300, 1600],
    "hard":   [60,  120, 200, 400, 600,  1000, 1600, 2000, 2600, 3200],
}

# ============================== ПЕРКИ ==============================
# "levels" — сколько уровней у семейства (по умолчанию 5). У одноуровневых
# перков (Last Miss, Bone Dance, Маг) метка показывается без номера уровня.
PERK_FAMILIES = {
    "fire":   {"emoji": "🔥", "name": "Огонь",      "kind": "debuff",  "levels": 5},
    "boom":   {"emoji": "💥", "name": "Взрыв",      "kind": "offense", "levels": 5},
    "boost":  {"emoji": "⚡️", "name": "Буст",       "kind": "self",    "levels": 5},
    "freeze": {"emoji": "❄️", "name": "Фриз",       "kind": "debuff",  "levels": 5},
    "steal":  {"emoji": "🥷🏻", "name": "Кража",      "kind": "debuff",  "levels": 5},
    "shield": {"emoji": "🛡", "name": "Щит",        "kind": "defense", "levels": 5},
    "splash": {"emoji": "🧨", "name": "Сплеш",      "kind": "offense", "levels": 5},
    "last":   {"emoji": "🔪", "name": "Last Miss",  "kind": "offense", "levels": 1},
    "bone":   {"emoji": "🦴", "name": "Bone Dance", "kind": "offense", "levels": 1},
    "mag":    {"emoji": "🔮", "name": "Маг",        "kind": "special", "levels": 1},
    "invuln": {"emoji": "⛔️", "name": "Неуязвимый", "kind": "passive", "levels": 5},
}
PERK_CHANCE = {
    "fire":   {1: 0.05, 2: 0.10, 3: 0.15, 4: 0.20, 5: 0.25},
    "boom":   {1: 0.10, 2: 0.15, 3: 0.20, 4: 0.30, 5: 0.50},
    "boost":  {1: 0.05, 2: 0.10, 3: 0.15, 4: 0.20, 5: 0.30},
    "freeze": {1: 0.05, 2: 0.10, 3: 0.15, 4: 0.15, 5: 0.20},
    "steal":  {1: 0.05, 2: 0.10, 3: 0.20, 4: 0.30, 5: 0.40},
    "shield": {1: 0.05, 2: 0.10, 3: 0.20, 4: 0.30, 5: 0.40},
    "last":   {1: 0.02},
}
# Щит: % блокируемого урона по уровням. Сплеш: диапазон % от макс. урона по уровням.
SHIELD_BLOCK = {1: 0.50, 2: 0.60, 3: 0.80, 4: 0.90, 5: 1.00}
SPLASH_PCT   = {1: (0.05, 0.10), 2: (0.10, 0.15), 3: (0.20, 0.30), 4: (0.30, 0.40), 5: (0.50, 0.60)}
LAST_KEEP    = 0.10   # Last Miss оставляет врагу 10% текущего HP
BONE_PCT     = (0.01, 0.10)   # Bone Dance: 1-10% своего текущего HP
# Маг: каждый ход срабатывает один случайный перк из этого набора (без иммунитета и самого Мага)
MAG_POOL = ["fire", "boom", "boost", "freeze", "steal", "shield", "splash", "last", "bone"]


def perk_levels(fam):
    return PERK_FAMILIES.get(fam, {}).get("levels", 5)


PERK_ORDER = [f"{fam}{lvl}" for fam in PERK_FAMILIES for lvl in range(1, perk_levels(fam) + 1)]


def perk_parse(code):
    for fam in PERK_FAMILIES:
        if code.startswith(fam):
            rest = code[len(fam):]
            if rest.isdigit():
                return fam, int(rest)
    return None, None


def perk_label(code):
    fam, lvl = perk_parse(code)
    if not fam:
        return code
    e = PERK_FAMILIES[fam]["emoji"]
    if perk_levels(fam) <= 1:
        return f"{e}{PERK_FAMILIES[fam]['name']}{e}"
    return f"{e}{PERK_FAMILIES[fam]['name']} {lvl}{e}"


def perk_chance(code):
    fam, lvl = perk_parse(code)
    return PERK_CHANCE.get(fam, {}).get(lvl, 0.0)


def effective_perk_chance(code, is_bot_battle=False):
    base = perk_chance(code)
    if not is_bot_battle:
        base = min(1.0, base * get_event_mult("mana"))
    return base


# ============================== ИГРОВАЯ ЛОГИКА ==============================
def category_of(cups):
    if cups <= 0:
        return 1
    return max(1, min(MAX_CATEGORY, math.ceil(cups / CATEGORY_STEP)))


def rank_multiplier(cups):
    return RANK_MULTIPLIER[category_of(cups)]


def cup_reward(category, won):
    lo_w, hi_w, lo_l, hi_l = CUP_REWARDS[category]
    return random.randint(lo_w, hi_w) if won else -random.randint(lo_l, hi_l)


def display_unit_name(name, rarity_icon):
    return f"{rarity_icon}{name}{rarity_icon}" if rarity_icon else name


def calc_power(unit_row, items):
    """Мощь = (общее HP + средний урон) * (1 + сумма уровней перков)."""
    total_hp = unit_row["hp"] + sum(it["hp_add"] for it in items)
    avg_dmg = (unit_row["dmg_min"] + unit_row["dmg_max"]) // 2
    total_dmg = avg_dmg + sum(it["dmg_add"] for it in items)
    perks = list(json.loads(unit_row["perks"] or "[]"))
    for it in items:
        perks += json.loads(it["perks"] or "[]")
    perk_sum = sum(lvl for _, lvl in [perk_parse(p) for p in perks] if _ is not None)
    return int((total_hp + total_dmg) * (1 + perk_sum))


def roll_summon(pool, luck_mult=1.0):
    """Катаем от редкой к частой: первая сработавшая редкость = результат.
    Если ни одна не сработала — гарантированно самая частая. Всегда что-то выпадает."""
    if not pool:
        return None
    for entry in sorted(pool, key=lambda u: u["rarity_chance"], reverse=True):
        x = max(1, int(entry["rarity_chance"]))
        if random.random() < min(1.0, (1.0 / x) * luck_mult):
            return _pick_same_rarity(pool, entry["rarity_id"])
    most_common = min(pool, key=lambda u: u["rarity_chance"])
    return _pick_same_rarity(pool, most_common["rarity_id"])


def _pick_same_rarity(pool, rarity_id):
    return random.choice([u for u in pool if u["rarity_id"] == rarity_id])


class Combatant:
    def __init__(self, side, player_name, unit, is_bot_battle=False, is_bot_side=False):
        self.side = side
        self.player_name = player_name
        self.unit_name = unit["name"]
        self.photo = unit.get("photo")
        self.base_dmg_min = max(0, int(unit["dmg_min"]))
        self.base_dmg_max = max(self.base_dmg_min, int(unit["dmg_max"]))
        self.base_max_hp = max(1, int(unit["hp"]))
        self.base_hp = self.base_max_hp
        self.unit_perks = list(unit.get("perks") or [])
        self.is_bot_battle = is_bot_battle
        # предметы по слотам (в порядке поломки сверху вниз)
        self.items = []
        for it in (unit.get("items") or []):
            shield = max(0, int(it["hp_add"]))
            self.items.append({
                "slot": it["slot"], "name": it["name"],
                "dmg_add": int(it["dmg_add"]),
                "perks": list(it.get("perks") or []),
                "max_hp": shield, "cur_hp": shield, "broken": False,
                "ref": it.get("ref"),
            })
        # Health Boost применяется только в PvP и только к игроку (не к боту-противнику)
        if not is_bot_battle and not is_bot_side:
            h_mult = get_event_mult("health")
            if h_mult != 1.0:
                self.base_max_hp = max(1, round(self.base_max_hp * h_mult))
                self.base_hp = self.base_max_hp
                for it in self.items:
                    it["cur_hp"] = max(0, round(it["cur_hp"] * h_mult))
                    it["max_hp"] = it["cur_hp"]
        self.boost_mult = 1.0
        self.boost_stacks = []
        self.burns = []
        self.freezes = []
        self.blocked = {}
        self.surrendered = False
        self.last_hp_loss = 0
        self.boom_markers = []
        self.temp_block = 0.0     # одноразовый блок (от Мага), действует на следующий удар
        self.last_block = 0.0     # сколько урона заблокировал щит в этот ход (для лога)

    def all_perks(self):
        res = list(self.unit_perks)
        for it in self.items:
            if not it["broken"]:
                res += it["perks"]
        return res

    def active_perks(self, family):
        res = []
        for code in self.all_perks():
            fam, lvl = perk_parse(code)
            if fam == family and code not in self.blocked:
                res.append((code, lvl))
        return res

    def invuln_level(self):
        lvls = [lvl for _, lvl in self.active_perks("invuln")]
        return max(lvls) if lvls else 0

    def immune_to(self, level):
        return self.invuln_level() >= level

    def is_frozen(self):
        return any(f["left"] > 0 for f in self.freezes)

    def alive(self):
        return self.base_hp > 0 and not self.surrendered

    def can_act(self):
        return self.alive() and not self.is_frozen()

    def total_hp(self):
        return self.base_hp + sum(it["cur_hp"] for it in self.items if it["max_hp"] > 0 and not it["broken"])

    def _roll_block(self):
        """Шанс щита заблокировать урон. Учитывает разовый блок temp_block (от Мага)."""
        best = self.temp_block
        self.temp_block = 0.0
        for code, lvl in self.active_perks("shield"):
            if random.random() < effective_perk_chance(code, self.is_bot_battle):
                best = max(best, SHIELD_BLOCK.get(lvl, 0.0))
        return min(1.0, best)

    def take(self, dmg, splash=0, is_attack=False):
        """Получить урон. splash — урон по частям, не задетым основным уроном.
        is_attack=True → срабатывает щит (блокирует часть урона атаки)."""
        if dmg <= 0 and splash <= 0:
            return
        if is_attack:
            block = self._roll_block()
            if block > 0:
                dmg = round(dmg * (1 - block))
                splash = round(splash * (1 - block))
                self.last_block = max(self.last_block, block)
        before = self.total_hp()
        remaining = dmg
        hit_main = set()
        # основной урон поглощают предметы-щиты сверху вниз, остаток — по юниту
        for idx, it in enumerate(self.items):
            if remaining <= 0:
                break
            if it["max_hp"] > 0 and not it["broken"] and it["cur_hp"] > 0:
                absorb = min(it["cur_hp"], remaining)
                it["cur_hp"] -= absorb
                remaining -= absorb
                hit_main.add(idx)
                if it["cur_hp"] <= 0:
                    it["broken"] = True
        base_hit = False
        if remaining > 0:
            self.base_hp = max(0, self.base_hp - remaining)
            base_hit = True
        # сплеш — по предметам и юниту, НЕ получившим основной урон
        if splash > 0:
            for idx, it in enumerate(self.items):
                if idx in hit_main or it["broken"] or it["max_hp"] <= 0 or it["cur_hp"] <= 0:
                    continue
                it["cur_hp"] = max(0, it["cur_hp"] - splash)
                if it["cur_hp"] <= 0:
                    it["broken"] = True
            if not base_hit:
                self.base_hp = max(0, self.base_hp - splash)
        self.last_hp_loss += before - self.total_hp()

    def dmg_bonus(self):
        return sum(it["dmg_add"] for it in self.items if not it["broken"])

    def eff_dmg_range(self):
        bonus = self.dmg_bonus()
        mult = self.boost_mult * get_event_mult("power", self.is_bot_battle)
        lo = round((self.base_dmg_min + bonus) * mult)
        hi = round((self.base_dmg_max + bonus) * mult)
        return lo, hi

    def effect_markers(self):
        marks = []
        for b in self.burns:
            marks.append(f"🔥{b['level']}")
        for f in self.freezes:
            if f["left"] > 0:
                marks.append(f"❄️{f['level']}")
        for lvl in self.boost_stacks:
            marks.append(f"⚡️{lvl}")
        for lvl in self.boom_markers:
            marks.append(f"💥{lvl}")
        return marks


class Battle:
    def __init__(self, c1, c2, is_bot=False):
        self.c1, self.c2 = c1, c2
        self.turn = 0
        self.events = ["Бой начался!"]
        self.finished = False
        self.winner = None
        self.is_bot = is_bot

    def surrender(self, side):
        c = self.c1 if side == 1 else self.c2
        c.surrendered = True
        self.events = [f"🏳️ {c.player_name} сдался"]
        self._check_end()

    def step(self):
        if self.finished:
            return
        self.turn += 1
        ev = []
        for c in (self.c1, self.c2):
            c.last_hp_loss = 0
            c.last_block = 0.0
            c.boom_markers = []
        ev += self._tick_burns(self.c1)
        ev += self._tick_burns(self.c2)
        a1, a2 = self.c1.can_act(), self.c2.can_act()
        if self.c1.alive() and not a1:
            ev.append(f"- {self.c1.player_name} заморожен")
        if self.c2.alive() and not a2:
            ev.append(f"- {self.c2.player_name} заморожен")
        # 🔮 Маг: каждый ход срабатывает случайный перк (до основной атаки)
        if a1:
            ev += self._mag_tick(self.c1, self.c2)
        if a2:
            ev += self._mag_tick(self.c2, self.c1)
        dmg2, splash2, ev_a1 = self._attack(self.c1, self.c2) if a1 else (0, 0, [])
        dmg1, splash1, ev_a2 = self._attack(self.c2, self.c1) if a2 else (0, 0, [])
        self.c2.take(dmg2, splash2, is_attack=True)
        self.c1.take(dmg1, splash1, is_attack=True)
        ev += ev_a1 + ev_a2
        self._tick_freezes(self.c1)
        self._tick_freezes(self.c2)
        for c in (self.c1, self.c2):
            if c.last_block > 0:
                ev.append(f"- {c.player_name} 🛡 заблокировал {int(c.last_block * 100)}%")
            if c.last_hp_loss > 0:
                ev.append(f"- {c.player_name} получил {c.last_hp_loss} урон")
        self.events = ev or ["—"]
        self._check_end()

    def _splash_amount(self, atk, lvl):
        """Урон сплеша = % от макс. урона атакующего (по уровню перка)."""
        lo_pct, hi_pct = SPLASH_PCT.get(lvl, (0.0, 0.0))
        _, hi = atk.eff_dmg_range()
        return max(1, round(hi * random.uniform(lo_pct, hi_pct))) if hi > 0 else 0

    def _attack(self, atk, dfn):
        ev = []
        lo, hi = atk.eff_dmg_range()
        dmg = random.randint(lo, hi) if hi > 0 else 0
        for code, lvl in atk.active_perks("boom"):
            if random.random() < effective_perk_chance(code, self.is_bot):
                dmg *= 2
                atk.boom_markers.append(lvl)
                ev.append(f"- {atk.player_name}: 💥взрыв x2")
        # 🦴 Bone Dance: +1-10% своего текущего HP к урону (каждая атака)
        for code, lvl in atk.active_perks("bone"):
            bonus = max(1, round(atk.total_hp() * random.uniform(*BONE_PCT)))
            dmg += bonus
            ev.append(f"- {atk.player_name}: 🦴 +{bonus}")
        # 🔪 Last Miss: с шансом оставить врагу 10% текущего HP
        for code, lvl in atk.active_perks("last"):
            if random.random() < effective_perk_chance(code, self.is_bot):
                execute = max(0, round(dfn.total_hp() * (1 - LAST_KEEP)))
                dmg = max(dmg, execute)
                ev.append(f"- {atk.player_name}: 🔪 Last Miss!")
        # 🧨 Сплеш: урон по частям, не задетым основным уроном (берём лучший уровень)
        splash = 0
        splash_lvls = [lvl for _, lvl in atk.active_perks("splash")]
        if splash_lvls:
            splash = self._splash_amount(atk, max(splash_lvls))
            if splash > 0:
                ev.append(f"- {atk.player_name}: 🧨 сплеш {splash}")
        for code, lvl in atk.active_perks("fire"):
            if random.random() < effective_perk_chance(code, self.is_bot) and not dfn.immune_to(lvl):
                dfn.burns.append({"left": 3, "dmg": max(1, round(atk.base_dmg_min * 0.5)), "level": lvl})
                ev.append(f"- {dfn.player_name} подожжён (🔥{lvl})")
        for code, lvl in atk.active_perks("freeze"):
            if random.random() < effective_perk_chance(code, self.is_bot) and not dfn.immune_to(lvl):
                dfn.freezes.append({"left": 2, "level": lvl})
                ev.append(f"- {dfn.player_name} заморожен на 2 хода (❄️{lvl})")
        for code, lvl in atk.active_perks("steal"):
            if random.random() < effective_perk_chance(code, self.is_bot) and not dfn.immune_to(lvl):
                free = [p for p in dfn.all_perks() if p not in dfn.blocked]
                if free:
                    victim = random.choice(free)
                    dfn.blocked[victim] = lvl
                    ev.append(f"- {atk.player_name} украл перк {perk_label(victim)}")
        for code, lvl in atk.active_perks("boost"):
            if random.random() < effective_perk_chance(code, self.is_bot):
                atk.boost_mult *= 1.2
                atk.boost_stacks.append(lvl)
                ev.append(f"- {atk.player_name} усилился (⚡️{lvl})")
        return dmg, splash, ev

    def _mag_tick(self, src, foe):
        """🔮 Маг: за каждый перк Мага один раз срабатывает случайный перк из MAG_POOL."""
        ev = []
        for _code, _lvl in src.active_perks("mag"):
            fam = random.choice(MAG_POOL)
            flvl = random.randint(1, perk_levels(fam))
            self._proc_mag(fam, flvl, src, foe, ev)
        return ev

    def _proc_mag(self, fam, lvl, src, foe, ev):
        """Гарантированно применить эффект перка fam/lvl от src к foe (для Мага)."""
        tag = f"🔮{src.player_name}"
        if fam == "fire":
            if not foe.immune_to(lvl):
                foe.burns.append({"left": 3, "dmg": max(1, round(src.base_dmg_min * 0.5)), "level": lvl})
                ev.append(f"- {tag} → поджёг (🔥{lvl})")
        elif fam == "freeze":
            if not foe.immune_to(lvl):
                foe.freezes.append({"left": 2, "level": lvl})
                ev.append(f"- {tag} → заморозка (❄️{lvl})")
        elif fam == "steal":
            if not foe.immune_to(lvl):
                free = [p for p in foe.all_perks() if p not in foe.blocked]
                if free:
                    victim = random.choice(free)
                    foe.blocked[victim] = lvl
                    ev.append(f"- {tag} → украл {perk_label(victim)}")
        elif fam == "boost":
            src.boost_mult *= 1.2
            src.boost_stacks.append(lvl)
            ev.append(f"- {tag} → усиление (⚡️{lvl})")
        elif fam == "shield":
            src.temp_block = max(src.temp_block, SHIELD_BLOCK.get(lvl, 0.0))
            ev.append(f"- {tag} → щит {int(SHIELD_BLOCK.get(lvl, 0.0) * 100)}%")
        elif fam == "boom":
            lo, hi = src.eff_dmg_range()
            d = (random.randint(lo, hi) if hi > 0 else 0) * 2
            if d > 0:
                foe.take(d, is_attack=True)
                ev.append(f"- {tag} → 💥взрыв {d}")
        elif fam == "splash":
            amt = self._splash_amount(src, lvl)
            if amt > 0:
                foe.take(0, splash=amt, is_attack=True)
                ev.append(f"- {tag} → 🧨 сплеш {amt}")
        elif fam == "last":
            d = max(0, round(foe.total_hp() * (1 - LAST_KEEP)))
            if d > 0:
                foe.take(d, is_attack=True)
                ev.append(f"- {tag} → 🔪 Last Miss!")
        elif fam == "bone":
            bonus = max(1, round(src.total_hp() * random.uniform(*BONE_PCT)))
            foe.take(bonus, is_attack=True)
            ev.append(f"- {tag} → 🦴 {bonus}")

    def _tick_burns(self, c):
        ev = []
        total = sum(b["dmg"] for b in c.burns)
        for b in c.burns:
            b["left"] -= 1
        if total:
            c.take(total)
            ev.append(f"- {c.player_name} горит, -{total}❤️")
        c.burns = [b for b in c.burns if b["left"] > 0]
        return ev

    def _tick_freezes(self, c):
        for f in c.freezes:
            f["left"] -= 1
        c.freezes = [f for f in c.freezes if f["left"] > 0]

    def _check_end(self):
        a1, a2 = self.c1.alive(), self.c2.alive()
        if a1 and a2:
            if self.turn >= MAX_TURNS:
                self.finished = True
                self.winner = 1 if self.c1.total_hp() >= self.c2.total_hp() else 2
            return
        self.finished = True
        self.winner = 0 if (not a1 and not a2) else (1 if not a2 else 2)

    def render(self):
        return f"{self._block(self.c1, 1)}\n{self._block(self.c2, 2)}\n\n{self._events_block()}"

    def _block(self, c, num):
        marks = c.effect_markers()
        name_line = f"Игрок {num} {c.player_name}"
        if marks:
            name_line += " (" + " ".join(f"[{m}]" for m in marks) + ")"
        loss = f"  (-{c.last_hp_loss}❤️)" if c.last_hp_loss > 0 else ""
        lo, hi = c.eff_dmg_range()
        lines = [SEP, name_line, f"{c.unit_name}{loss}", f"⚔️{lo}-{hi}", f"❤️{c.base_hp}"]
        # слоты предметов (все 6, пусто если нет)
        by_slot = {it["slot"]: it for it in c.items}
        for emoji in SLOT_EMOJIS:
            it = by_slot.get(emoji)
            if not it:
                lines.append(f"{emoji}пусто{emoji}")
                continue
            pstr = (" " + " ".join(perk_label(p) for p in it["perks"])) if it["perks"] else ""
            body = f"{emoji}{it['name']}{emoji}/⚔️+{it['dmg_add']} ❤️{max(0, it['cur_hp'])}{pstr}"
            lines.append(strike(body) if it["broken"] else body)
        # собственные перки юнита
        for p in c.unit_perks:
            line = perk_label(p)
            if p in c.blocked:
                line += f"([🥷🏻{c.blocked[p]}])"
            lines.append(line)
        lines.append(SEP)
        return "\n".join(lines)

    def _events_block(self):
        return f"{SEP}\n" + "\n".join(self.events) + f"\n{SEP}"


def shift_rarity_id(rarities, rarity_id):
    """20% — на 1 редкость выше (реже), 40% — на 1 ниже (чаще), 40% — та же.
    rarities отсортированы по chance по возрастанию (обычные → редкие)."""
    ids = [r["id"] for r in rarities]
    if not ids:
        return rarity_id
    i = ids.index(rarity_id) if rarity_id in ids else 0
    r = random.random()
    if r < 0.20:
        i += 1          # выше редкость
    elif r < 0.60:
        i -= 1          # ниже редкость
    # иначе (40%) — та же
    i = max(0, min(len(ids) - 1, i))
    return ids[i]


async def _random_unit_of_rarity(rarity_id):
    units = [u for u in await list_units() if u["rarity_id"] == rarity_id]
    return random.choice(units) if units else None


async def _random_item_of_rarity(rarity_id, slot):
    items = [it for it in await list_items() if it["rarity_id"] == rarity_id and it["slot"] == slot]
    return random.choice(items) if items else None


async def make_bot_opponent(player_pu_id, difficulty="medium", player_name="🤖 Бот"):
    """Бот: подбирает юнит+предметы по диапазону мощи согласно сложности и рангу игрока."""
    pu = await get_player_unit(player_pu_id)
    player = await get_player(pu["user_id"])
    category = category_of(player["cups"])
    max_power = BOT_POWER_MAX[difficulty][category - 1]

    all_units = await list_units()
    all_items_list = await list_items()
    items_by_slot: dict = {}
    for it in all_items_list:
        slot = it["slot"] or "🧣"
        items_by_slot.setdefault(slot, []).append(it)

    candidates = []
    for unit in all_units:
        if calc_power(unit, []) > max_power:
            continue
        chosen: list = []
        for slot_emoji in SLOT_EMOJIS:
            slot_items = list(items_by_slot.get(slot_emoji, []))
            random.shuffle(slot_items)
            for it in slot_items:
                if calc_power(unit, chosen + [it]) <= max_power:
                    chosen.append(it)
                    break
        candidates.append((unit, chosen, calc_power(unit, chosen)))

    if not candidates:
        if not all_units:
            return None
        unit_row = min(all_units, key=lambda u: calc_power(u, []))
        chosen = []
    else:
        candidates.sort(key=lambda x: x[2], reverse=True)
        unit_row, chosen, _ = candidates[0]

    rar = await get_rarity(unit_row["rarity_id"]) if unit_row["rarity_id"] else None
    bot_items = []
    for it in chosen:
        irar = await get_rarity(it["rarity_id"]) if it["rarity_id"] else None
        bot_items.append({
            "slot": it["slot"],
            "name": display_unit_name(it["name"], irar["icon"] if irar else ""),
            "dmg_add": it["dmg_add"], "hp_add": it["hp_add"],
            "perks": json.loads(it["perks"] or "[]"),
        })

    unit_data = {
        "name": display_unit_name(unit_row["name"], rar["icon"] if rar else ""),
        "photo": unit_row["photo"],
        "dmg_min": max(0, unit_row["dmg_min"]),
        "dmg_max": max(0, unit_row["dmg_max"]),
        "hp": max(1, unit_row["hp"]),
        "perks": json.loads(unit_row["perks"] or "[]"),
        "items": bot_items,
    }
    return Combatant(2, player_name, unit_data, is_bot_battle=True, is_bot_side=True)


# ============================== БАЗА ДАННЫХ ==============================
_db: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    user_id INTEGER PRIMARY KEY, username TEXT, cups INTEGER NOT NULL DEFAULT 0,
    coins INTEGER NOT NULL DEFAULT 0, equipped_pu INTEGER, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS currencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT, icon TEXT NOT NULL,
    win_chance REAL NOT NULL DEFAULT 0, win_min INTEGER NOT NULL DEFAULT 0, win_max INTEGER NOT NULL DEFAULT 0,
    loss_chance REAL NOT NULL DEFAULT 0, loss_min INTEGER NOT NULL DEFAULT 0, loss_max INTEGER NOT NULL DEFAULT 0,
    unlock_rank INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS rarities (
    id INTEGER PRIMARY KEY AUTOINCREMENT, icon TEXT NOT NULL, name TEXT NOT NULL, chance INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT, photo TEXT, name TEXT NOT NULL, rarity_id INTEGER,
    dmg_min INTEGER NOT NULL DEFAULT 1, dmg_max INTEGER NOT NULL DEFAULT 1, hp INTEGER NOT NULL DEFAULT 1,
    perks TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS bosses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, photo TEXT, name TEXT NOT NULL, rarity_id INTEGER,
    dmg_min INTEGER NOT NULL DEFAULT 1, dmg_max INTEGER NOT NULL DEFAULT 1, hp INTEGER NOT NULL DEFAULT 1,
    perks TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, photo TEXT, name TEXT NOT NULL, type TEXT NOT NULL DEFAULT '',
    rarity_id INTEGER, slot TEXT NOT NULL DEFAULT '🧣', dmg_add INTEGER NOT NULL DEFAULT 0,
    hp_add INTEGER NOT NULL DEFAULT 0, perks TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS summons (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, price INTEGER NOT NULL DEFAULT 0,
    currency_id INTEGER NOT NULL DEFAULT 0, unit_ids TEXT NOT NULL DEFAULT '[]', item_ids TEXT NOT NULL DEFAULT '[]',
    kind TEXT NOT NULL DEFAULT 'summon', display TEXT NOT NULL DEFAULT '{}', next_refresh INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS player_units (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, unit_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS player_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id INTEGER NOT NULL, equipped_pu INTEGER);
CREATE TABLE IF NOT EXISTS player_currencies (
    user_id INTEGER NOT NULL, currency_id INTEGER NOT NULL, amount INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, currency_id));
CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS groups (chat_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    etype TEXT NOT NULL,
    multiplier REAL NOT NULL DEFAULT 1.0,
    end_time INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS clans (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, photo TEXT, description TEXT NOT NULL DEFAULT '',
    owner_id INTEGER NOT NULL, entry_mode TEXT NOT NULL DEFAULT 'open',
    boss_id INTEGER, boss_hp INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS clan_members (
    user_id INTEGER PRIMARY KEY, clan_id INTEGER NOT NULL, joined_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS clan_requests (
    clan_id INTEGER NOT NULL, user_id INTEGER NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY (clan_id, user_id));
CREATE TABLE IF NOT EXISTS clan_boss_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, clan_id INTEGER NOT NULL, item_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS player_donations (
    user_id INTEGER NOT NULL, don_type TEXT NOT NULL,
    PRIMARY KEY (user_id, don_type));
CREATE TABLE IF NOT EXISTS admin_donations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    price_stars INTEGER NOT NULL DEFAULT 1,
    currency_id INTEGER NOT NULL DEFAULT 0, amount INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS mybots_subs (
    user_id INTEGER PRIMARY KEY, sub_type TEXT NOT NULL DEFAULT 'none',
    expires_at INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS mybots_bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL,
    name TEXT NOT NULL, token TEXT NOT NULL, created_at INTEGER NOT NULL);
"""

CLAN_MAX_MEMBERS = 20
CLAN_CREATE_COST = 5000
CLAN_RENAME_COST = 10000
CLAN_BOSS_HEAL = 1000
CLAN_BOSS_HEAL_COST = 1000


async def db_init():
    global _db
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA)
    # миграции для старых баз
    await _ensure_column("items", "rarity_id", "INTEGER")
    await _ensure_column("items", "slot", "TEXT NOT NULL DEFAULT '🧣'")
    await _ensure_column("items", "dmg_add", "INTEGER NOT NULL DEFAULT 0")
    await _ensure_column("items", "hp_add", "INTEGER NOT NULL DEFAULT 0")
    await _ensure_column("summons", "item_ids", "TEXT NOT NULL DEFAULT '[]'")
    await _ensure_column("summons", "kind", "TEXT NOT NULL DEFAULT 'summon'")
    await _db.commit()
    # загрузить динамических админов в память
    DYN_ADMINS.clear()
    DYN_ADMINS.update(await list_admins_db())
    # загрузить активные события
    await load_active_events_db()


async def _ensure_column(table, column, decl):
    cur = await _db.execute(f"PRAGMA table_info({table})")
    cols = [r["name"] for r in await cur.fetchall()]
    if column not in cols:
        await _db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        await _db.commit()


async def db_close():
    if _db:
        await _db.close()


async def _update_row(table, row_id, fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    await _db.execute(f"UPDATE {table} SET {cols} WHERE id=?", list(fields.values()) + [row_id])
    await _db.commit()


# --- игроки ---
async def get_or_create_player(user_id, username):
    cur = await _db.execute("SELECT * FROM players WHERE user_id=?", (user_id,))
    row = await cur.fetchone()
    if row:
        if username and row["username"] != username:
            await _db.execute("UPDATE players SET username=? WHERE user_id=?", (username, user_id))
            await _db.commit()
        return row
    await _db.execute("INSERT INTO players (user_id, username, cups, coins, created_at) VALUES (?,?,0,?,?)",
                      (user_id, username, START_COINS, int(time.time())))
    await _db.commit()
    cur = await _db.execute("SELECT * FROM players WHERE user_id=?", (user_id,))
    return await cur.fetchone()


async def get_player(user_id):
    cur = await _db.execute("SELECT * FROM players WHERE user_id=?", (user_id,))
    return await cur.fetchone()


async def add_cups(user_id, delta):
    await _db.execute("UPDATE players SET cups = MAX(0, cups + ?) WHERE user_id=?", (delta, user_id))
    await _db.commit()


async def set_equipped(user_id, pu_id):
    await _db.execute("UPDATE players SET equipped_pu=? WHERE user_id=?", (pu_id, user_id))
    await _db.commit()


async def top_players(limit=10):
    cur = await _db.execute("SELECT * FROM players ORDER BY cups DESC, created_at ASC LIMIT ?", (limit,))
    return await cur.fetchall()


# --- админы (динамические, в БД) ---
async def list_admins_db():
    cur = await _db.execute("SELECT user_id FROM admins ORDER BY user_id")
    return [r["user_id"] for r in await cur.fetchall()]


async def add_admin_db(uid):
    await _db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (uid,))
    await _db.commit()


async def remove_admin_db(uid):
    await _db.execute("DELETE FROM admins WHERE user_id=?", (uid,))
    await _db.commit()


# --- группы (для рассылки) ---
async def save_group(chat_id: int):
    await _db.execute("INSERT OR IGNORE INTO groups (chat_id) VALUES (?)", (chat_id,))
    await _db.commit()


async def list_groups():
    cur = await _db.execute("SELECT chat_id FROM groups")
    return [r["chat_id"] for r in await cur.fetchall()]


# --- события ---
async def load_active_events_db():
    global _ACTIVE_EVENTS
    now = int(time.time())
    await _db.execute("DELETE FROM events WHERE end_time <= ?", (now,))
    await _db.commit()
    cur = await _db.execute("SELECT * FROM events WHERE end_time > ? ORDER BY id", (now,))
    _ACTIVE_EVENTS = [dict(r) for r in await cur.fetchall()]


async def add_event_db(etype: str, multiplier: float, duration_minutes: int):
    end_time = int(time.time()) + duration_minutes * 60
    await _db.execute("INSERT INTO events (etype, multiplier, end_time) VALUES (?,?,?)",
                      (etype, multiplier, end_time))
    await _db.commit()
    await load_active_events_db()


async def delete_event_db(event_id: int):
    await _db.execute("DELETE FROM events WHERE id=?", (event_id,))
    await _db.commit()
    await load_active_events_db()


async def clear_events_db():
    await _db.execute("DELETE FROM events")
    await _db.commit()
    await load_active_events_db()


# --- валюты ---
async def add_currency(icon, wc, wmin, wmax, lc, lmin, lmax, unlock_rank):
    await _db.execute("INSERT INTO currencies (icon,win_chance,win_min,win_max,loss_chance,loss_min,loss_max,unlock_rank)"
                      " VALUES (?,?,?,?,?,?,?,?)", (icon, wc, wmin, wmax, lc, lmin, lmax, unlock_rank))
    await _db.commit()


async def list_currencies():
    cur = await _db.execute("SELECT * FROM currencies ORDER BY id")
    return await cur.fetchall()


async def get_currency(cid):
    cur = await _db.execute("SELECT * FROM currencies WHERE id=?", (cid,))
    return await cur.fetchone()


async def update_currency(cid, **f):
    await _update_row("currencies", cid, f)


async def delete_currency(cid):
    await _db.execute("DELETE FROM currencies WHERE id=?", (cid,))
    await _db.execute("DELETE FROM player_currencies WHERE currency_id=?", (cid,))
    await _db.commit()


# --- редкости ---
async def add_rarity(icon, name, chance):
    await _db.execute("INSERT INTO rarities (icon,name,chance) VALUES (?,?,?)", (icon, name, chance))
    await _db.commit()


async def list_rarities():
    cur = await _db.execute("SELECT * FROM rarities ORDER BY chance")
    return await cur.fetchall()


async def get_rarity(rid):
    cur = await _db.execute("SELECT * FROM rarities WHERE id=?", (rid,))
    return await cur.fetchone()


async def update_rarity(rid, **f):
    await _update_row("rarities", rid, f)


async def delete_rarity(rid):
    await _db.execute("DELETE FROM rarities WHERE id=?", (rid,))
    await _db.commit()


# --- юниты ---
async def add_unit(photo, name, rarity_id, dmin, dmax, hp, perks_list):
    await _db.execute("INSERT INTO units (photo,name,rarity_id,dmg_min,dmg_max,hp,perks) VALUES (?,?,?,?,?,?,?)",
                      (photo, name, rarity_id, dmin, dmax, hp, json.dumps(perks_list)))
    await _db.commit()


async def list_units():
    cur = await _db.execute("SELECT * FROM units ORDER BY id")
    return await cur.fetchall()


async def get_unit(uid):
    cur = await _db.execute("SELECT * FROM units WHERE id=?", (uid,))
    return await cur.fetchone()


async def update_unit(uid, **f):
    if "perks" in f and not isinstance(f["perks"], str):
        f["perks"] = json.dumps(f["perks"])
    await _update_row("units", uid, f)


async def delete_unit(uid):
    await _db.execute("DELETE FROM units WHERE id=?", (uid,))
    await _db.commit()


# --- боссы (как юниты, но для кланов) ---
async def add_boss(photo, name, rarity_id, dmin, dmax, hp, perks_list):
    await _db.execute("INSERT INTO bosses (photo,name,rarity_id,dmg_min,dmg_max,hp,perks) VALUES (?,?,?,?,?,?,?)",
                      (photo, name, rarity_id, dmin, dmax, hp, json.dumps(perks_list)))
    await _db.commit()


async def list_bosses():
    cur = await _db.execute("SELECT * FROM bosses ORDER BY id")
    return await cur.fetchall()


async def get_boss(bid):
    cur = await _db.execute("SELECT * FROM bosses WHERE id=?", (bid,))
    return await cur.fetchone()


async def update_boss(bid, **f):
    if "perks" in f and not isinstance(f["perks"], str):
        f["perks"] = json.dumps(f["perks"])
    await _update_row("bosses", bid, f)


async def delete_boss(bid):
    await _db.execute("DELETE FROM bosses WHERE id=?", (bid,))
    # снять этого босса у кланов, где он выбран
    await _db.execute("UPDATE clans SET boss_id=NULL, boss_hp=0 WHERE boss_id=?", (bid,))
    await _db.commit()


# --- предметы ---
async def add_item(photo, name, type_, rarity_id, slot, dmg_add, hp_add, perks_list):
    await _db.execute(
        "INSERT INTO items (photo,name,type,rarity_id,slot,dmg_add,hp_add,perks) VALUES (?,?,?,?,?,?,?,?)",
        (photo, name, type_, rarity_id, slot, dmg_add, hp_add, json.dumps(perks_list)))
    await _db.commit()


async def list_items():
    cur = await _db.execute("SELECT * FROM items ORDER BY id")
    return await cur.fetchall()


async def get_item(iid):
    cur = await _db.execute("SELECT * FROM items WHERE id=?", (iid,))
    return await cur.fetchone()


async def update_item(iid, **f):
    if "perks" in f and not isinstance(f["perks"], str):
        f["perks"] = json.dumps(f["perks"])
    await _update_row("items", iid, f)


async def delete_item(iid):
    await _db.execute("DELETE FROM items WHERE id=?", (iid,))
    await _db.execute("DELETE FROM player_items WHERE item_id=?", (iid,))
    await _db.commit()


# --- суммоны / крейты ---
async def add_summon(name, price, currency_id, unit_ids, item_ids, kind="summon"):
    await _db.execute("INSERT INTO summons (name,price,currency_id,unit_ids,item_ids,kind,display,next_refresh)"
                      " VALUES (?,?,?,?,?,?,'{}',0)",
                      (name, price, currency_id, json.dumps(unit_ids), json.dumps(item_ids), kind))
    await _db.commit()


async def list_summons(kind="summon"):
    cur = await _db.execute("SELECT * FROM summons WHERE kind=? ORDER BY id", (kind,))
    return await cur.fetchall()


async def get_summon(sid):
    cur = await _db.execute("SELECT * FROM summons WHERE id=?", (sid,))
    return await cur.fetchone()


async def update_summon(sid, **f):
    for key in ("unit_ids", "item_ids"):
        if key in f and not isinstance(f[key], str):
            f[key] = json.dumps(f[key])
    await _update_row("summons", sid, f)


async def delete_summon(sid):
    await _db.execute("DELETE FROM summons WHERE id=?", (sid,))
    await _db.commit()


# --- инвентарь игрока ---
async def add_player_unit(user_id, unit_id):
    cur = await _db.execute("INSERT INTO player_units (user_id, unit_id) VALUES (?,?)", (user_id, unit_id))
    await _db.commit()
    return cur.lastrowid


async def list_player_units(user_id):
    cur = await _db.execute("SELECT pu.id AS pu_id, u.* FROM player_units pu JOIN units u ON u.id=pu.unit_id "
                            "WHERE pu.user_id=? ORDER BY pu.id", (user_id,))
    return await cur.fetchall()


async def get_player_unit(pu_id):
    cur = await _db.execute("SELECT pu.id AS pu_id, pu.user_id, u.* FROM player_units pu "
                            "JOIN units u ON u.id=pu.unit_id WHERE pu.id=?", (pu_id,))
    return await cur.fetchone()


async def add_player_item(user_id, item_id):
    cur = await _db.execute("INSERT INTO player_items (user_id, item_id) VALUES (?,?)", (user_id, item_id))
    await _db.commit()
    return cur.lastrowid


async def list_player_items(user_id):
    cur = await _db.execute("SELECT pi.id AS pi_id, pi.equipped_pu, it.* FROM player_items pi "
                            "JOIN items it ON it.id=pi.item_id WHERE pi.user_id=? ORDER BY pi.id", (user_id,))
    return await cur.fetchall()


async def items_on_unit(pu_id):
    cur = await _db.execute("SELECT pi.id AS pi_id, it.* FROM player_items pi "
                            "JOIN items it ON it.id=pi.item_id WHERE pi.equipped_pu=?", (pu_id,))
    return await cur.fetchall()


async def set_item_equipped(pi_id, pu_id):
    # один предмет каждого класса на юните: снимаем другие того же слота
    if pu_id is not None:
        cur = await _db.execute(
            "SELECT it.slot FROM player_items pi JOIN items it ON it.id=pi.item_id WHERE pi.id=?", (pi_id,))
        row = await cur.fetchone()
        if row:
            await _db.execute(
                "UPDATE player_items SET equipped_pu=NULL WHERE equipped_pu=? AND id IN "
                "(SELECT pi.id FROM player_items pi JOIN items it ON it.id=pi.item_id WHERE it.slot=?)",
                (pu_id, row["slot"]))
    await _db.execute("UPDATE player_items SET equipped_pu=? WHERE id=?", (pu_id, pi_id))
    await _db.commit()


# --- валюта игрока ---
async def get_player_currency_amount(user_id, currency_id):
    if currency_id == COIN_CURRENCY_ID:
        p = await get_player(user_id)
        return p["coins"] if p else 0
    cur = await _db.execute("SELECT amount FROM player_currencies WHERE user_id=? AND currency_id=?",
                            (user_id, currency_id))
    row = await cur.fetchone()
    return row["amount"] if row else 0


async def spend_currency(user_id, currency_id, amount):
    if await get_player_currency_amount(user_id, currency_id) < amount:
        return False
    if currency_id == COIN_CURRENCY_ID:
        await _db.execute("UPDATE players SET coins = coins - ? WHERE user_id=?", (amount, user_id))
    else:
        await _db.execute("UPDATE player_currencies SET amount = amount - ? WHERE user_id=? AND currency_id=?",
                          (amount, user_id, currency_id))
    await _db.commit()
    return True


async def give_currency(user_id, currency_id, amount):
    if currency_id == COIN_CURRENCY_ID:
        await _db.execute("UPDATE players SET coins = coins + ? WHERE user_id=?", (amount, user_id))
        await _db.commit()
    else:
        await _db.execute("INSERT INTO player_currencies (user_id, currency_id, amount) VALUES (?,?,?) "
                          "ON CONFLICT(user_id, currency_id) DO UPDATE SET amount = amount + excluded.amount",
                          (user_id, currency_id, amount))
        await _db.commit()


async def list_player_currencies(user_id):
    cur = await _db.execute("SELECT currency_id, amount FROM player_currencies WHERE user_id=? AND amount>0", (user_id,))
    return await cur.fetchall()


async def build_battle_unit(pu_id):
    pu = await get_player_unit(pu_id)
    if not pu:
        return None
    rarity = await get_rarity(pu["rarity_id"]) if pu["rarity_id"] else None
    items = []
    for it in await items_on_unit(pu_id):
        irar = await get_rarity(it["rarity_id"]) if it["rarity_id"] else None
        items.append({
            "slot": it["slot"],
            "name": display_unit_name(it["name"], irar["icon"] if irar else ""),
            "dmg_add": it["dmg_add"], "hp_add": it["hp_add"],
            "perks": json.loads(it["perks"] or "[]"),
        })
    # порядок слотов = порядок поломки
    items.sort(key=lambda x: SLOT_EMOJIS.index(x["slot"]) if x["slot"] in SLOT_EMOJIS else 99)
    return {
        "name": display_unit_name(pu["name"], rarity["icon"] if rarity else ""),
        "photo": pu["photo"],
        "dmg_min": max(0, pu["dmg_min"]),
        "dmg_max": max(0, pu["dmg_max"]),
        "hp": max(1, pu["hp"]),
        "perks": json.loads(pu["perks"] or "[]"),
        "items": items,
    }


async def clan_boss_equipped_items(clan_id):
    """Лучший предмет каждого слота из запаса босса (по щиту+урону). Остальное — резерв."""
    by_slot = {}
    for it in await clan_boss_items_rows(clan_id):
        by_slot.setdefault(it["slot"] or "🧣", []).append(it)
    chosen = []
    for slot, rows in by_slot.items():
        chosen.append(max(rows, key=lambda x: (x["hp_add"] + x["dmg_add"])))
    return chosen


async def build_boss_combatant(clan):
    """Combatant босса клана: статы из шаблона, лучшие предметы по слотам, текущее (не восстановленное) HP."""
    if not clan["boss_id"]:
        return None
    boss = await get_boss(clan["boss_id"])
    if not boss:
        return None
    rar = await get_rarity(boss["rarity_id"]) if boss["rarity_id"] else None
    items = []
    for it in await clan_boss_equipped_items(clan["id"]):
        irar = await get_rarity(it["rarity_id"]) if it["rarity_id"] else None
        items.append({
            "slot": it["slot"],
            "name": display_unit_name(it["name"], irar["icon"] if irar else ""),
            "dmg_add": it["dmg_add"], "hp_add": it["hp_add"],
            "perks": json.loads(it["perks"] or "[]"),
            "ref": it["cbi_id"],
        })
    items.sort(key=lambda x: SLOT_EMOJIS.index(x["slot"]) if x["slot"] in SLOT_EMOJIS else 99)
    unit_data = {
        "name": display_unit_name(boss["name"], rar["icon"] if rar else ""),
        "photo": boss["photo"],
        "dmg_min": max(0, boss["dmg_min"]), "dmg_max": max(0, boss["dmg_max"]),
        "hp": max(1, boss["hp"]),
        "perks": json.loads(boss["perks"] or "[]"),
        "items": items,
    }
    c = Combatant(2, f"[{clan['name']}]", unit_data, is_bot_battle=True, is_bot_side=True)
    # босс выходит в бой с текущим HP (после боя не восстанавливается)
    c.base_max_hp = max(1, boss["hp"])
    c.base_hp = max(0, min(int(clan["boss_hp"]), c.base_max_hp))
    return c


async def persist_boss_after_battle(clan_id, boss_c):
    """Сохранить оставшееся HP босса и убрать сломанные в бою предметы из запаса."""
    await update_clan(clan_id, boss_hp=max(0, boss_c.base_hp))
    for it in boss_c.items:
        if it.get("broken") and it.get("ref") is not None:
            await _db.execute("DELETE FROM clan_boss_items WHERE id=?", (it["ref"],))
    await _db.commit()


async def grant_battle_currencies(user_id, won, cups_for_rank, don_mult=1.0):
    rank_mult = rank_multiplier(cups_for_rank)
    player_rank = category_of(cups_for_rank)
    given = []
    for c in await list_currencies():
        if player_rank < c["unlock_rank"]:
            continue
        chance = c["win_chance"] if won else c["loss_chance"]
        if chance <= 0:
            continue
        if random.random() * 100 < chance:
            lo = c["win_min"] if won else c["loss_min"]
            hi = c["win_max"] if won else c["loss_max"]
            amount = max(0, round(random.randint(min(lo, hi), max(lo, hi)) * rank_mult * don_mult))
            if amount > 0:
                await give_currency(user_id, c["id"], amount)
                given.append((c["icon"], amount))
    return given


async def get_coin_mult(user_id) -> float:
    """Суммарный множитель монет за бой (VIP + x2coins)."""
    m = 1.0
    if await has_donation(user_id, "vip"):
        m *= VIP_COIN_MULT
    if await has_donation(user_id, "x2coins"):
        m *= 2.0
    return m


async def get_luck_mult(user_id) -> float:
    """Суммарный множитель удачи для суммонов (event + VIP + x2luck)."""
    m = get_event_mult("luck")
    if await has_donation(user_id, "vip"):
        m *= VIP_LUCK_MULT
    if await has_donation(user_id, "x2luck"):
        m *= 2.0
    return m


async def _donate_price(base_stars: int, user_id: int) -> int:
    """Возвращает цену со скидкой Ultra (50%) если активна."""
    sub = await active_sub(user_id)
    if sub == "ultra":
        return max(1, base_stars // 2)
    return base_stars


async def notify_owner_purchase(bot, buyer_name: str, what: str):
    if ADMIN_IDS:
        try:
            await bot.send_message(ADMIN_IDS[0], f"💳 Покупка\n{buyer_name} купил: {what}")
        except Exception:
            pass


# --- удаление предмета игрока (для выдачи боссу) ---
async def delete_player_item(pi_id):
    await _db.execute("DELETE FROM player_items WHERE id=?", (pi_id,))
    await _db.commit()


# --- кланы ---
async def get_player_clan_id(user_id):
    cur = await _db.execute("SELECT clan_id FROM clan_members WHERE user_id=?", (user_id,))
    row = await cur.fetchone()
    return row["clan_id"] if row else None


async def get_clan(clan_id):
    cur = await _db.execute("SELECT * FROM clans WHERE id=?", (clan_id,))
    return await cur.fetchone()


async def get_player_clan(user_id):
    cid = await get_player_clan_id(user_id)
    return await get_clan(cid) if cid else None


async def update_clan(clan_id, **f):
    await _update_row("clans", clan_id, f)


async def list_clans():
    cur = await _db.execute("SELECT * FROM clans ORDER BY id")
    return await cur.fetchall()


async def clan_member_count(clan_id):
    cur = await _db.execute("SELECT COUNT(*) AS n FROM clan_members WHERE clan_id=?", (clan_id,))
    return (await cur.fetchone())["n"]


async def clan_members_list(clan_id):
    cur = await _db.execute(
        "SELECT cm.user_id, p.username FROM clan_members cm LEFT JOIN players p ON p.user_id=cm.user_id "
        "WHERE cm.clan_id=? ORDER BY cm.joined_at", (clan_id,))
    return await cur.fetchall()


async def create_clan(owner_id, name, photo, description):
    now = int(time.time())
    cur = await _db.execute(
        "INSERT INTO clans (name, photo, description, owner_id, entry_mode, boss_id, boss_hp, created_at) "
        "VALUES (?,?,?,?, 'open', NULL, 0, ?)", (name, photo, description or "", owner_id, now))
    clan_id = cur.lastrowid
    await _db.execute("INSERT INTO clan_members (user_id, clan_id, joined_at) VALUES (?,?,?)", (owner_id, clan_id, now))
    await _db.execute("DELETE FROM clan_requests WHERE user_id=?", (owner_id,))
    await _db.commit()
    return clan_id


async def add_clan_member(clan_id, user_id):
    await _db.execute("INSERT OR REPLACE INTO clan_members (user_id, clan_id, joined_at) VALUES (?,?,?)",
                      (user_id, clan_id, int(time.time())))
    await _db.execute("DELETE FROM clan_requests WHERE user_id=?", (user_id,))
    await _db.commit()


async def remove_clan_member(clan_id, user_id):
    await _db.execute("DELETE FROM clan_members WHERE user_id=? AND clan_id=?", (user_id, clan_id))
    await _db.commit()


async def add_clan_request(clan_id, user_id):
    await _db.execute("INSERT OR IGNORE INTO clan_requests (clan_id, user_id, created_at) VALUES (?,?,?)",
                      (clan_id, user_id, int(time.time())))
    await _db.commit()


async def has_clan_request(clan_id, user_id):
    cur = await _db.execute("SELECT 1 FROM clan_requests WHERE clan_id=? AND user_id=?", (clan_id, user_id))
    return await cur.fetchone() is not None


async def clan_requests_list(clan_id):
    cur = await _db.execute(
        "SELECT cr.user_id, p.username FROM clan_requests cr LEFT JOIN players p ON p.user_id=cr.user_id "
        "WHERE cr.clan_id=? ORDER BY cr.created_at", (clan_id,))
    return await cur.fetchall()


async def remove_clan_request(clan_id, user_id):
    await _db.execute("DELETE FROM clan_requests WHERE clan_id=? AND user_id=?", (clan_id, user_id))
    await _db.commit()


# --- босс клана ---
async def add_clan_boss_item(clan_id, item_id):
    await _db.execute("INSERT INTO clan_boss_items (clan_id, item_id) VALUES (?,?)", (clan_id, item_id))
    await _db.commit()


async def clan_boss_items_rows(clan_id):
    cur = await _db.execute(
        "SELECT cbi.id AS cbi_id, it.* FROM clan_boss_items cbi JOIN items it ON it.id=cbi.item_id WHERE cbi.clan_id=?",
        (clan_id,))
    return await cur.fetchall()


async def delete_clan_boss_item(cbi_id):
    await _db.execute("DELETE FROM clan_boss_items WHERE id=?", (cbi_id,))
    await _db.commit()


# --- донат игрока ---
async def has_donation(user_id, don_type):
    cur = await _db.execute("SELECT 1 FROM player_donations WHERE user_id=? AND don_type=?", (user_id, don_type))
    return await cur.fetchone() is not None


async def give_donation(user_id, don_type):
    await _db.execute("INSERT OR IGNORE INTO player_donations (user_id, don_type) VALUES (?,?)", (user_id, don_type))
    await _db.commit()


async def get_player_donations(user_id):
    cur = await _db.execute("SELECT don_type FROM player_donations WHERE user_id=?", (user_id,))
    return [r["don_type"] for r in await cur.fetchall()]


# --- admin donations ---
async def add_admin_donation(name, description, price_stars, currency_id, amount):
    await _db.execute(
        "INSERT INTO admin_donations (name,description,price_stars,currency_id,amount) VALUES (?,?,?,?,?)",
        (name, description, price_stars, currency_id, amount))
    await _db.commit()


async def list_admin_donations():
    cur = await _db.execute("SELECT * FROM admin_donations ORDER BY id")
    return await cur.fetchall()


async def get_admin_donation(did):
    cur = await _db.execute("SELECT * FROM admin_donations WHERE id=?", (did,))
    return await cur.fetchone()


async def update_admin_donation(did, **f):
    await _update_row("admin_donations", did, f)


async def delete_admin_donation(did):
    await _db.execute("DELETE FROM admin_donations WHERE id=?", (did,))
    await _db.commit()


# --- подписки mybots ---
async def get_mybots_sub(user_id):
    cur = await _db.execute("SELECT * FROM mybots_subs WHERE user_id=?", (user_id,))
    return await cur.fetchone()


async def set_mybots_sub(user_id, sub_type, days):
    now = int(time.time())
    cur_row = await get_mybots_sub(user_id)
    if cur_row and cur_row["expires_at"] > now and cur_row["sub_type"] == sub_type:
        expires = cur_row["expires_at"] + days * 86400
    else:
        expires = now + days * 86400
    await _db.execute("INSERT OR REPLACE INTO mybots_subs (user_id,sub_type,expires_at) VALUES (?,?,?)",
                      (user_id, sub_type, expires))
    await _db.commit()


async def active_sub(user_id):
    """Тип активной подписки. Если платной нет — бесплатный тариф 'free' (навсегда)."""
    row = await get_mybots_sub(user_id)
    if row and row["sub_type"] != FREE_SUB and row["expires_at"] > int(time.time()):
        return row["sub_type"]
    return FREE_SUB


async def give_sub_admin(user_id, sub_type, days):
    await set_mybots_sub(user_id, sub_type, days)


# --- боты mybots ---
async def get_mybots_bots(owner_id):
    cur = await _db.execute("SELECT * FROM mybots_bots WHERE owner_id=? ORDER BY id", (owner_id,))
    return await cur.fetchall()


async def add_mybot(owner_id, name, token):
    cur = await _db.execute(
        "INSERT INTO mybots_bots (owner_id,name,token,created_at) VALUES (?,?,?,?)",
        (owner_id, name, token, int(time.time())))
    await _db.commit()
    return cur.lastrowid


async def get_mybot(bot_id):
    cur = await _db.execute("SELECT * FROM mybots_bots WHERE id=?", (bot_id,))
    return await cur.fetchone()


async def update_mybot_name(bot_id, name):
    await _db.execute("UPDATE mybots_bots SET name=? WHERE id=?", (name, bot_id))
    await _db.commit()


async def delete_mybot(bot_id):
    await _db.execute("DELETE FROM mybots_bots WHERE id=?", (bot_id,))
    await _db.commit()


# ============================== КЛАВИАТУРЫ / ХЕЛПЕРЫ ==============================
BTN_PLAY = "🎮Играть🎮"
BTN_SUMMON = "👤Суммон👤"
BTN_CRATE = "🧳Крейты🧳"
BTN_INV = "📦Инвентарь📦"
BTN_LB = "🏆Лидерборд🏆"
BTN_CLAN = "🛡Кланы🛡"
BTN_ABOUT = "📖Об игре📖"
BTN_ADMIN = "🛠 Админка"


def _mk_btn(t, d):
    """callback-кнопка, либо URL-кнопка если data — ссылка (http/https/tg)."""
    if isinstance(d, str) and (d.startswith("http://") or d.startswith("https://") or d.startswith("tg://")):
        return InlineKeyboardButton(text=t, url=d)
    return InlineKeyboardButton(text=t, callback_data=d)


def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [_mk_btn(t, d) for t, d in row] for row in rows])


def main_menu_kb(is_adm):
    rows = [[KeyboardButton(text=BTN_PLAY), KeyboardButton(text=BTN_SUMMON)],
            [KeyboardButton(text=BTN_CRATE), KeyboardButton(text=BTN_INV)],
            [KeyboardButton(text=BTN_LB), KeyboardButton(text=BTN_CLAN)],
            [KeyboardButton(text=BTN_ABOUT)]]
    if is_adm:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def fmt_mult(m):
    return str(int(m)) if float(m).is_integer() else ("%g" % m)


def is_admin(user_id):
    return user_id in ADMIN_IDS or user_id in DYN_ADMINS


def display_name(player):
    return player["username"] or f"id{player['user_id']}"


def username_of(u):
    return (u.username and f"@{u.username}") or (u.full_name or f"id{u.id}")


async def main_menu_text(player):
    cups = player["cups"]
    cat = category_of(cups)
    lines = ["Добро пожаловать!", f"⚜️ранг {cat}⚜️ (x{fmt_mult(rank_multiplier(cups))})",
             f"🏆{cups}", "", f"{COIN_ICON} {player['coins']}"]
    amounts = {r["currency_id"]: r["amount"] for r in await list_player_currencies(player["user_id"])}
    for c in await list_currencies():
        if cat >= c["unlock_rank"]:
            lines.append(f"{c['icon']} {amounts.get(c['id'], 0)}")
    # активные события
    now = int(time.time())
    active = [ev for ev in _ACTIVE_EVENTS if ev["end_time"] > now]
    if active:
        lines.append("")
        lines.append("Активные события:")
        for ev in active:
            left = ev["end_time"] - now
            mins = left // 60
            label = EVENT_TYPES.get(ev["etype"], ev["etype"])
            lines.append(f"{label} x{fmt_mult(ev['multiplier'])} — {mins} мин")
    return "\n".join(lines)


async def send_main_menu(message, user_id, username):
    # выход в меню = принудительно завершить идущие бои/подбор игрока
    try:
        await force_end_user_battles(user_id, message.bot)
    except Exception:
        pass
    player = await get_or_create_player(user_id, username)
    kb = main_menu_kb(is_admin(user_id))
    # Кнопка Mini App если задан WEBAPP_URL
    if WEBAPP_URL:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
        wa_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🌐 Открыть Mini App", web_app=WebAppInfo(url=WEBAPP_URL + "/webapp/"))
        ]])
        await message.answer(await main_menu_text(player), reply_markup=kb)
        await message.answer("↓ Или открой визуальный интерфейс:", reply_markup=wa_kb)
    else:
        await message.answer(await main_menu_text(player), reply_markup=kb)


async def go_main(call: "CallbackQuery"):
    """Любой выход → удалить текущее сообщение и показать главное меню."""
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_main_menu(call.message, call.from_user.id, username_of(call.from_user))


# ===================== АЛЬБОМ ФОТО ДЛЯ ГАЧИ =====================
_gacha_album: dict[int, list[int]] = {}   # chat_id -> id сообщений альбома (чтобы удалять при обновлении)


async def _clear_gacha_album(bot, chat_id):
    for mid in _gacha_album.pop(chat_id, []):
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass


async def send_album(bot, chat_id, photos):
    """Шлёт фото альбомом (по file_id — без перезагрузки). Возвращает id сообщений."""
    photos = photos[:10]
    if len(photos) >= 2:
        msgs = await bot.send_media_group(chat_id, [InputMediaPhoto(media=p) for p in photos])
        return [m.message_id for m in msgs]
    if len(photos) == 1:
        m = await bot.send_photo(chat_id, photos[0])
        return [m.message_id]
    return []


router = Router()              # основной роутер — сообщения только в ЛС
group_router = Router()        # команды, работающие и в группах (/freeunit)
router.message.filter(F.chat.type == "private")


# ============================== МЕНЮ / ЛИДЕРБОРД ==============================
@router.message(CommandStart())
async def h_start(message: Message, state: FSMContext):
    await state.clear()
    await send_main_menu(message, message.from_user.id, username_of(message.from_user))


@router.message(F.text == BTN_LB)
async def h_leaderboard(message: Message):
    rows = await top_players(10)
    line = "===================="
    parts = ["🏆Топ 10 кубков🏆"]
    if not rows:
        parts += [line, "Пока пусто", line]
    else:
        for i, p in enumerate(rows, 1):
            parts += [line, f"#{i}", display_name(p), f"🏆{p['cups']}🏆"]
        parts.append(line)
    await message.answer("\n".join(parts), reply_markup=ikb([[("🚪 Выйти", "lb_exit")]]))


@router.callback_query(F.data == "lb_exit")
async def h_lb_exit(call: CallbackQuery):
    await go_main(call)


# ============================== ОБ ИГРЕ ==============================
PERK_INFO = {
    "fire": "с шансом {c}% поджигает врага: каждый ход −50% мин. урона юнита в течение 3 ходов (суммируется)",
    "boom": "с шансом {c}% наносит ×2 урон в этот ход",
    "boost": "с шансом {c}% увеличивает урон ×1.2 до конца боя (суммируется)",
    "freeze": "с шансом {c}% замораживает врага на 2 хода (суммируется)",
    "steal": "с шансом {c}% при атаке блокирует случайный перк врага до конца боя",
}


def about_menu_kb():
    return ikb([[("🎯 Перки", "about:perks")], [("👤 Юниты", "about:units")],
                [("🧩 Предметы", "about:items")], [("🎲 Редкости", "about:rarities")],
                [("💰 Валюты", "about:currencies")], [("⚡️ События", "about:events")],
                [("🤖 Боты", "about:bots")], [("🚪 Выйти", "about:exit")]])


def _about_back_kb():
    return ikb([[("⬅️ К разделам", "about:menu")], [("🚪 Выйти", "about:exit")]])


async def send_long(bot, chat_id, text, markup):
    """Шлёт длинный текст, разбивая по строкам (лимит сообщения Telegram). markup на последнем куске."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3500:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur if cur else "—")
    for i, ch in enumerate(chunks):
        await bot.send_message(chat_id, ch, reply_markup=markup if i == len(chunks) - 1 else None)


async def unit_sources(unit_id):
    return [s["name"] for s in await list_summons("summon") if unit_id in json.loads(s["unit_ids"] or "[]")]


async def item_sources(item_id):
    src = []
    for s in (await list_summons("summon")) + (await list_summons("crate")):
        if item_id in json.loads(s["item_ids"] or "[]"):
            src.append(s["name"])
    return src


async def unit_exist_counts():
    """Сколько копий каждого юнита существует у всех игроков: {unit_id: count}."""
    cur = await _db.execute("SELECT unit_id, COUNT(*) AS n FROM player_units GROUP BY unit_id")
    return {r["unit_id"]: r["n"] for r in await cur.fetchall()}


async def item_exist_counts():
    """Сколько копий каждого предмета существует у всех игроков: {item_id: count}."""
    cur = await _db.execute("SELECT item_id, COUNT(*) AS n FROM player_items GROUP BY item_id")
    return {r["item_id"]: r["n"] for r in await cur.fetchall()}


def _perk_about_line(fam, lvl, code):
    lbl = perk_label(code)
    if fam == "invuln":
        return f"{lbl} — иммунитет к дебаффам (Огонь/Фриз/Кража) уровня ≤ {lvl}"
    if fam == "shield":
        c = int(PERK_CHANCE["shield"][lvl] * 100)
        b = int(SHIELD_BLOCK[lvl] * 100)
        return f"{lbl} — с шансом {c}% блокирует {b}% входящего урона"
    if fam == "splash":
        lo, hi = SPLASH_PCT[lvl]
        return (f"{lbl} — наносит {int(lo * 100)}-{int(hi * 100)}% макс. урона предметам и юниту, "
                "не задетым основным уроном")
    if fam == "last":
        c = int(PERK_CHANCE["last"][1] * 100)
        return f"{lbl} — с шансом {c}% оставляет врагу {int(LAST_KEEP * 100)}% его текущего HP"
    if fam == "bone":
        return f"{lbl} — каждая атака отнимает у врага {int(BONE_PCT[0]*100)}-{int(BONE_PCT[1]*100)}% твоего текущего HP"
    if fam == "mag":
        return f"{lbl} — каждый ход срабатывает случайный перк (кроме иммунитета)"
    c = int(PERK_CHANCE[fam][lvl] * 100)
    return f"{lbl} — " + PERK_INFO[fam].format(c=c)


def about_perks_text():
    lines = ["🎯 ПЕРКИ", ""]
    for fam, info in PERK_FAMILIES.items():
        lines.append(f"{info['emoji']} {info['name']}")
        for lvl in range(1, perk_levels(fam) + 1):
            code = f"{fam}{lvl}"
            lines.append("• " + _perk_about_line(fam, lvl, code))
        lines.append("")
    return "\n".join(lines)


async def about_units_text():
    units = await list_units()
    if not units:
        return "👤 ЮНИТЫ\n\nПока нет юнитов."
    exists = await unit_exist_counts()
    line = "===================="
    lines = ["👤 ЮНИТЫ"]
    for u in units:
        rar = await get_rarity(u["rarity_id"]) if u["rarity_id"] else None
        lines.append(line)
        lines.append(display_unit_name(u["name"], rar["icon"] if rar else ""))
        if rar:
            lines.append(f"• редкость: {rar['icon']}{rar['name']} (1 in {rar['chance']})")
        lines.append(f"• ⚔️{u['dmg_min']}-{u['dmg_max']}  ❤️{u['hp']}")
        perks = json.loads(u["perks"] or "[]")
        if perks:
            lines.append("• перки: " + ", ".join(perk_label(p) for p in perks))
        srcs = await unit_sources(u["id"])
        lines.append("• откуда: " + ((", ".join(srcs) + " (суммоны)") if srcs else "не в суммонах") + ", /freeunit")
        lines.append(f"• 📊 экзистов: {exists.get(u['id'], 0)}")
    lines.append(line)
    return "\n".join(lines)


async def about_items_text():
    items = await list_items()
    if not items:
        return "🧩 ПРЕДМЕТЫ\n\nПока нет предметов."
    exists = await item_exist_counts()
    line = "===================="
    lines = ["🧩 ПРЕДМЕТЫ"]
    for it in items:
        rar = await get_rarity(it["rarity_id"]) if it["rarity_id"] else None
        slot = it["slot"] or "🧣"
        lines.append(line)
        lines.append(display_unit_name(it["name"], rar["icon"] if rar else ""))
        lines.append(f"• класс: {slot}{SLOT_NAME.get(slot, '')}{slot}")
        if rar:
            lines.append(f"• редкость: {rar['icon']}{rar['name']} (1 in {rar['chance']})")
        lines.append(f"• ⚔️+{it['dmg_add']}  ❤️{it['hp_add']} (щит)")
        perks = json.loads(it["perks"] or "[]")
        if perks:
            lines.append("• перки: " + ", ".join(perk_label(p) for p in perks))
        srcs = await item_sources(it["id"])
        lines.append("• откуда: " + (", ".join(srcs) if srcs else "пока нигде"))
        lines.append(f"• 📊 экзистов: {exists.get(it['id'], 0)}")
    lines.append(line)
    return "\n".join(lines)


async def about_rarities_text():
    rs = await list_rarities()
    if not rs:
        return "🎲 РЕДКОСТИ\n\nПока нет редкостей."
    lines = ["🎲 РЕДКОСТИ", "", "(чем больше X в «1 in X» — тем реже)", ""]
    for r in rs:
        lines.append(f"{r['icon']}{r['name']} — 1 in {r['chance']}")
    return "\n".join(lines)


async def about_currencies_text():
    lines = ["💰 ВАЛЮТЫ", "",
             f"{COIN_ICON} Монеты — базовая валюта. {START_COINS} на старте.",
             f"За бой: победа +{COIN_REWARD_WIN[0]}-{COIN_REWARD_WIN[1]}, поражение +{COIN_REWARD_LOSS[0]}-{COIN_REWARD_LOSS[1]}.",
             ""]
    curs = await list_currencies()
    if not curs:
        lines.append("Других валют пока нет.")
    for c in curs:
        lines.append(f"{c['icon']} — видна/падает с ранга {c['unlock_rank']}")
        lines.append(f"• победа: {c['win_chance']:g}% ({c['win_min']}-{c['win_max']})")
        lines.append(f"• поражение: {c['loss_chance']:g}% ({c['loss_min']}-{c['loss_max']})")
        lines.append("• кол-во умножается на множитель ранга")
        lines.append("")
    return "\n".join(lines)


def about_events_text():
    lines = ["⚡️ СОБЫТИЯ", "",
             "Администраторы могут запускать временные события,",
             "усиливающие игру для всех игроков.", ""]
    for etype, label in EVENT_TYPES.items():
        exempt = " (не в боях с ботами)" if etype in EVENT_BOT_EXEMPT else ""
        lines.append(f"{label}{exempt}")
    lines += ["", "Активные события отображаются на главном экране с таймером."]
    return "\n".join(lines)


def about_bots_text():
    lines = ["🤖 БОИ С БОТАМИ", "",
             "Три уровня сложности:", ""]
    for key, info in BOT_DIFF_INFO.items():
        mult = info["reward_mult"]
        lines.append(f"{info['label']} — x{fmt_mult(mult)} кубков за победу")
    lines += ["",
              "Потери кубков при поражении одинаковы на всех сложностях.",
              "При ничье кубки не начисляются, но выдаётся валюта как за победу.",
              "",
              "Мощь бота рассчитывается по формуле:",
              "мощь = (HP + средний урон) × (1 + сумма уровней перков)"]
    return "\n".join(lines)


_ABOUT_BUILDERS = {
    "perks": about_perks_text,
    "units": about_units_text,
    "items": about_items_text,
    "rarities": about_rarities_text,
    "currencies": about_currencies_text,
    "events": about_events_text,
    "bots": about_bots_text,
}


@router.message(F.text == BTN_ABOUT)
async def h_about(message: Message):
    await message.answer("📖 Об игре\nВыбери раздел:", reply_markup=about_menu_kb())


@router.callback_query(F.data == "about:menu")
async def h_about_menu(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.bot.send_message(call.message.chat.id, "📖 Об игре\nВыбери раздел:", reply_markup=about_menu_kb())
    await call.answer()


@router.callback_query(F.data == "about:exit")
async def h_about_exit(call: CallbackQuery):
    await go_main(call)


@router.callback_query(F.data.startswith("about:"))
async def h_about_section(call: CallbackQuery):
    section = call.data.split(":")[1]
    builder = _ABOUT_BUILDERS.get(section)
    if not builder:
        return await call.answer()
    res = builder()
    text = await res if asyncio.iscoroutine(res) else res
    chat_id = call.message.chat.id
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_long(call.bot, chat_id, text, _about_back_kb())
    await call.answer()


# ============================== СУММОН ==============================
async def currency_icon(summon):
    if summon["currency_id"] == COIN_CURRENCY_ID:
        return COIN_ICON
    c = await get_currency(summon["currency_id"])
    return c["icon"] if c else "?"


def _entry(kind, row, rarity):
    return {"kind": kind, "id": row["id"], "name": row["name"], "photo": row["photo"],
            "rarity_id": row["rarity_id"],
            "rarity_icon": rarity["icon"] if rarity else "",
            "rarity_chance": rarity["chance"] if rarity else 1}


async def build_pool(summon):
    """Пул суммона = юниты + предметы, у каждого своя редкость."""
    pool = []
    for uid in json.loads(summon["unit_ids"] or "[]"):
        u = await get_unit(uid)
        if u and u["rarity_id"]:
            pool.append(_entry("unit", u, await get_rarity(u["rarity_id"])))
    for iid in json.loads(summon["item_ids"] or "[]"):
        it = await get_item(iid)
        if it and it["rarity_id"]:
            pool.append(_entry("item", it, await get_rarity(it["rarity_id"])))
    return pool


def _ref(e):
    return f"{e['kind']}:{e['id']}"


async def ensure_display(summon):
    pool = await build_pool(summon)
    by_rarity = {}
    for e in pool:
        by_rarity.setdefault(e["rarity_id"], []).append(e)
    display = json.loads(summon["display"] or "{}")
    refs_now = {e_ref for es in by_rarity.values() for e_ref in [_ref(x) for x in es]}
    valid = display and set(display.keys()) == {str(r) for r in by_rarity} \
        and all(v in refs_now for v in display.values())
    now = int(time.time())
    if summon["next_refresh"] <= now or not valid:
        display = {str(r): _ref(random.choice(es)) for r, es in by_rarity.items()}
        await update_summon(summon["id"], display=json.dumps(display), next_refresh=now + SUMMON_REFRESH_SECONDS)
        summon = await get_summon(summon["id"])
    return summon


async def displayed_entries(summon):
    pool = await build_pool(summon)
    index = {_ref(e): e for e in pool}
    display = json.loads(summon["display"] or "{}")
    res = [index[ref] for ref in display.values() if ref in index]
    res.sort(key=lambda x: x["rarity_chance"])
    return res


KIND_TITLE = {"summon": "Суммон", "crate": "Крейт"}


async def render_gacha(kind, index):
    """Возвращает (text, markup, photos) для гачи."""
    summons = await list_summons(kind)
    if not summons:
        return f"{KIND_TITLE[kind]}ов пока нет. Загляни позже!", ikb([[("🚪 Выйти", "gc:exit")]]), []
    index %= len(summons)
    summon = await ensure_display(summons[index])
    icon = await currency_icon(summon)
    entries = await displayed_entries(summon)
    left = max(0, summon["next_refresh"] - int(time.time()))
    lines = [summon["name"], f"⏳ обновление через {left // 60:02d}:{left % 60:02d}", ""]
    if not entries:
        lines.append("(пусто)")
    for e in entries:
        tag = "" if e["kind"] == "unit" else " 🧩"
        lines.append(f"{display_unit_name(e['name'], e['rarity_icon'])}{tag} 1 in {e['rarity_chance']}")
    price1, price10 = summon["price"], summon["price"] * SUMMON_X10_DISCOUNT
    rows = [[(f"🎲Открыть х1🎲 ({price1}{icon})", f"gc:p1:{kind}:{index}")],
            [(f"🎲Открыть х10🎲 ({price10}{icon})", f"gc:p10:{kind}:{index}")]]
    if len(summons) > 1:
        rows.append([("⏪Назад", f"gc:nav:{kind}:{index - 1}"), ("⏩Вперёд", f"gc:nav:{kind}:{index + 1}")])
    rows.append([("🚪 Выйти", "gc:exit")])
    photos = [e["photo"] for e in entries if e["photo"]]
    return "\n".join(lines), ikb(rows), photos


async def send_gacha(bot, chat_id, kind, index):
    await _clear_gacha_album(bot, chat_id)
    text, markup, photos = await render_gacha(kind, index)
    if photos:
        _gacha_album[chat_id] = await send_album(bot, chat_id, photos)
    await bot.send_message(chat_id, text, reply_markup=markup)


@router.message(F.text == BTN_SUMMON)
async def h_summon(message: Message):
    await send_gacha(message.bot, message.chat.id, "summon", 0)


@router.message(F.text == BTN_CRATE)
async def h_crate(message: Message):
    await send_gacha(message.bot, message.chat.id, "crate", 0)


@router.callback_query(F.data.startswith("gc:nav:"))
async def h_gacha_nav(call: CallbackQuery):
    _, _, kind, index = call.data.split(":")
    await call.message.delete()
    await send_gacha(call.bot, call.message.chat.id, kind, int(index))
    await call.answer()


@router.callback_query(F.data == "gc:exit")
async def h_gacha_exit(call: CallbackQuery):
    await _clear_gacha_album(call.bot, call.message.chat.id)
    await go_main(call)


async def _do_pull(call, kind, index, count):
    summons = await list_summons(kind)
    if not summons:
        return await call.answer("Пусто", show_alert=True)
    index %= len(summons)
    summon = await ensure_display(summons[index])
    entries = await displayed_entries(summon)
    if not entries:
        return await call.answer("Пусто", show_alert=True)
    icon = await currency_icon(summon)
    total = summon["price"] * (SUMMON_X10_DISCOUNT if count == 10 else 1)
    uid = call.from_user.id
    if not await spend_currency(uid, summon["currency_id"], total):
        return await call.answer(f"Не хватает валюты! Нужно {total}{icon}", show_alert=True)

    luck_mult = await get_luck_mult(uid)
    results = [roll_summon(entries, luck_mult) for _ in range(count)]
    for r in results:
        if r["kind"] == "unit":
            await add_player_unit(uid, r["id"])
        else:
            await add_player_item(uid, r["id"])

    chat_id = call.message.chat.id
    ok_kb = ikb([[("✅ ОК", f"gc:ok:{kind}:{index}")]])
    # заменяем окно суммона/ящика на дроп: убираем альбом и само окно
    await _clear_gacha_album(call.bot, chat_id)
    try:
        await call.message.delete()
    except Exception:
        pass

    if count == 1:
        r = results[0]
        tag = "" if r["kind"] == "unit" else " (предмет)"
        caption = "Тебе выпало:\n" + display_unit_name(r["name"], r["rarity_icon"]) + tag
        if r["photo"]:
            await call.bot.send_photo(chat_id, r["photo"], caption=caption, reply_markup=ok_kb)
        else:
            await call.bot.send_message(chat_id, caption, reply_markup=ok_kb)
    else:
        counts = {}
        for r in results:
            tag = "" if r["kind"] == "unit" else " 🧩"
            key = display_unit_name(r["name"], r["rarity_icon"]) + tag
            counts[key] = counts.get(key, 0) + 1
        text = "Тебе выпало:\n" + "\n".join(f"{n} x{c}" for n, c in counts.items())
        await call.bot.send_message(chat_id, text, reply_markup=ok_kb)
    await call.answer()


@router.callback_query(F.data.startswith("gc:ok:"))
async def h_gacha_ok(call: CallbackQuery):
    _, _, kind, index = call.data.split(":")
    await call.message.delete()
    await send_gacha(call.bot, call.message.chat.id, kind, int(index))
    await call.answer()


@router.callback_query(F.data.startswith("gc:p1:"))
async def h_pull1(call: CallbackQuery):
    _, _, kind, index = call.data.split(":")
    await _do_pull(call, kind, int(index), 1)


@router.callback_query(F.data.startswith("gc:p10:"))
async def h_pull10(call: CallbackQuery):
    _, _, kind, index = call.data.split(":")
    await _do_pull(call, kind, int(index), 10)


# ============================== ИНВЕНТАРЬ ==============================
INV_EXIT = ("🚪Выйти🚪", "inv:exit")


async def _inv_send(message, text, markup, photo):
    if photo:
        await message.answer_photo(photo, caption=text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def grouped_units(user_id):
    """Группировка одинаковых юнитов: [{row, count, pu_ids, rep}]."""
    pus = await list_player_units(user_id)
    player = await get_player(user_id)
    groups, order = {}, []
    for pu in pus:
        key = pu["id"]  # id юнита (не player_unit)
        if key not in groups:
            groups[key] = {"row": pu, "count": 0, "pu_ids": []}
            order.append(key)
        groups[key]["count"] += 1
        groups[key]["pu_ids"].append(pu["pu_id"])
    result = []
    for k in order:
        g = groups[k]
        g["rep"] = player["equipped_pu"] if player["equipped_pu"] in g["pu_ids"] else g["pu_ids"][0]
        result.append(g)
    return result


async def show_unit(message, user_id, index):
    groups = await grouped_units(user_id)
    if not groups:
        await message.answer("У тебя пока нет юнитов. Загляни в 👤Суммон👤!", reply_markup=ikb([[INV_EXIT]]))
        return
    index %= len(groups)
    g = groups[index]
    pu = g["row"]
    rarity = await get_rarity(pu["rarity_id"]) if pu["rarity_id"] else None
    player = await get_player(user_id)
    equipped = player["equipped_pu"] in g["pu_ids"]

    name = display_unit_name(pu["name"], rarity["icon"] if rarity else "")
    title = f"{name} x{g['count']}" if g["count"] > 1 else name
    lines = [title, f"⚔️{pu['dmg_min']}-{pu['dmg_max']}", f"❤️{pu['hp']}"]
    for p in json.loads(pu["perks"] or "[]"):
        lines.append(perk_label(p))
    eq_btn = ("⚙️Настроить⚙️", f"inv:cfg:{g['rep']}") if equipped \
        else ("❇️Экипировать❇️", f"inv:equip:{g['rep']}:{index}")
    rows = [[eq_btn]]
    if len(groups) > 1:
        rows.append([("⏪Назад⏪", f"inv:u:{index - 1}"), ("⏩Вперёд⏩", f"inv:u:{index + 1}")])
    rows.append([INV_EXIT])
    await _inv_send(message, "\n".join(lines), ikb(rows), pu["photo"])


@router.message(F.text == BTN_INV)
async def h_inventory(message: Message):
    await show_unit(message, message.from_user.id, 0)


@router.callback_query(F.data.startswith("inv:u:"))
async def h_inv_nav(call: CallbackQuery):
    await call.message.delete()
    await show_unit(call.message, call.from_user.id, int(call.data.split(":")[2]))
    await call.answer()


@router.callback_query(F.data.startswith("inv:equip:"))
async def h_inv_equip(call: CallbackQuery):
    _, _, pu_id, index = call.data.split(":")
    await set_equipped(call.from_user.id, int(pu_id))
    await call.message.delete()
    await show_unit(call.message, call.from_user.id, int(index))
    await call.answer("Экипировано!")


async def grouped_items_by_slot(user_id, slot_emoji, pu_id):
    """Предметы данного слота, стакнутые по item_id."""
    groups, order = {}, []
    for it in await list_player_items(user_id):
        if (it["slot"] or "🧣") != slot_emoji:
            continue
        k = it["id"]  # id предмета (не player_item)
        if k not in groups:
            groups[k] = {"row": it, "pi_ids": [], "equipped_here": None, "free_pi": None}
            order.append(k)
        g = groups[k]
        g["pi_ids"].append(it["pi_id"])
        if it["equipped_pu"] == pu_id:
            g["equipped_here"] = it["pi_id"]
        if it["equipped_pu"] is None and g["free_pi"] is None:
            g["free_pi"] = it["pi_id"]
    return [groups[k] for k in order]


async def show_slots(message, user_id, pu_id):
    """Меню категорий (слотов) при настройке юнита."""
    equipped = {it["slot"]: it["name"] for it in await items_on_unit(pu_id)}
    rows = []
    for idx, (emoji, nm) in enumerate(ITEM_SLOTS):
        cur = equipped.get(emoji)
        label = f"{emoji}{nm}: {cur}" if cur else f"{emoji}{nm}: —"
        rows.append([(label, f"inv:slot:{pu_id}:{idx}")])
    rows.append([("⬅️ К юниту", f"inv:back:{pu_id}")])
    rows.append([INV_EXIT])
    await _inv_send(message, "⚙️ Настройка предметов\nВыбери категорию:", ikb(rows), None)


async def show_slot_items(message, user_id, pu_id, slot_index, item_index):
    emoji, nm = ITEM_SLOTS[slot_index]
    groups = await grouped_items_by_slot(user_id, emoji, pu_id)
    if not groups:
        rows = [[("⬅️ К категориям", f"inv:cfg:{pu_id}")], [INV_EXIT]]
        await _inv_send(message, f"{emoji}{nm}{emoji}\nНет предметов этой категории.", ikb(rows), None)
        return
    item_index %= len(groups)
    g = groups[item_index]
    it = g["row"]
    rarity = await get_rarity(it["rarity_id"]) if it["rarity_id"] else None
    name = display_unit_name(it["name"], rarity["icon"] if rarity else "")
    count = len(g["pi_ids"])
    title = f"{name} x{count}" if count > 1 else name
    on_this = g["equipped_here"] is not None
    lines = [f"{emoji}{nm}{emoji}", title, f"⚔️+{it['dmg_add']}", f"❤️{it['hp_add']}"]
    for p in json.loads(it["perks"] or "[]"):
        lines.append(perk_label(p))
    eq_btn = ("⬜️Экипировано⬜️", f"inv:itemeq:{pu_id}:{slot_index}:{item_index}") if on_this \
        else ("❇️Экипировать❇️", f"inv:itemeq:{pu_id}:{slot_index}:{item_index}")
    rows = [[eq_btn]]
    if len(groups) > 1:
        rows.append([("⏪Назад⏪", f"inv:item:{pu_id}:{slot_index}:{item_index - 1}"),
                     ("⏩Вперёд⏩", f"inv:item:{pu_id}:{slot_index}:{item_index + 1}")])
    rows.append([("⬅️ К категориям", f"inv:cfg:{pu_id}")])
    rows.append([INV_EXIT])
    await _inv_send(message, "\n".join(lines), ikb(rows), it["photo"])


@router.callback_query(F.data.startswith("inv:cfg:"))
async def h_inv_cfg(call: CallbackQuery):
    pu_id = int(call.data.split(":")[2])
    await call.message.delete()
    await show_slots(call.message, call.from_user.id, pu_id)
    await call.answer()


@router.callback_query(F.data.startswith("inv:slot:"))
async def h_inv_slot(call: CallbackQuery):
    _, _, pu_id, slot_index = call.data.split(":")
    await call.message.delete()
    await show_slot_items(call.message, call.from_user.id, int(pu_id), int(slot_index), 0)
    await call.answer()


@router.callback_query(F.data.startswith("inv:item:"))
async def h_inv_item_nav(call: CallbackQuery):
    _, _, pu_id, slot_index, item_index = call.data.split(":")
    await call.message.delete()
    await show_slot_items(call.message, call.from_user.id, int(pu_id), int(slot_index), int(item_index))
    await call.answer()


@router.callback_query(F.data.startswith("inv:itemeq:"))
async def h_inv_item_eq(call: CallbackQuery):
    _, _, pu_id, slot_index, item_index = call.data.split(":")
    pu_id, slot_index, item_index = int(pu_id), int(slot_index), int(item_index)
    emoji, _nm = ITEM_SLOTS[slot_index]
    groups = await grouped_items_by_slot(call.from_user.id, emoji, pu_id)
    if not groups:
        return await call.answer()
    g = groups[item_index % len(groups)]
    if g["equipped_here"] is not None:
        await set_item_equipped(g["equipped_here"], None)
    else:
        await set_item_equipped(g["free_pi"] or g["pi_ids"][0], pu_id)
    await call.message.delete()
    await show_slot_items(call.message, call.from_user.id, pu_id, slot_index, item_index)
    await call.answer("Готово!")


@router.callback_query(F.data.startswith("inv:back:"))
async def h_inv_back(call: CallbackQuery):
    await call.message.delete()
    player = await get_player(call.from_user.id)
    groups = await grouped_units(call.from_user.id)
    index = next((i for i, g in enumerate(groups) if player["equipped_pu"] in g["pu_ids"]), 0)
    await show_unit(call.message, call.from_user.id, index)
    await call.answer()


@router.callback_query(F.data == "inv:exit")
async def h_inv_exit(call: CallbackQuery):
    await go_main(call)


# ============================== /freeunit (в группах) ==============================
_freeunit_cd: dict[int, float] = {}     # user_id -> время последнего использования
_BOT_USERNAME = None
FREEUNIT_COOLDOWN = 3600                 # раз в час


async def _bot_username(bot):
    global _BOT_USERNAME
    if _BOT_USERNAME is None:
        me = await bot.get_me()
        _BOT_USERNAME = me.username
    return _BOT_USERNAME


async def _user_photo(bot, uid):
    try:
        photos = await bot.get_user_profile_photos(uid, limit=1)
        if photos.total_count > 0:
            return photos.photos[0][-1].file_id
    except Exception:
        pass
    return None


async def random_unit_by_rarity():
    """Случайный юнит, взвешенный по редкости (обычные чаще редких)."""
    units = [u for u in await list_units() if u["rarity_id"]]
    if not units:
        return None
    weights = []
    for u in units:
        r = await get_rarity(u["rarity_id"])
        weights.append(1.0 / max(1, r["chance"]) if r else 1.0)
    return random.choices(units, weights=weights, k=1)[0]


@group_router.message(Command("freeunit"))
async def h_freeunit(message: Message):
    if message.chat.type != "private":
        await save_group(message.chat.id)
    uid = message.from_user.id
    now = time.time()
    left = FREEUNIT_COOLDOWN - (now - _freeunit_cd.get(uid, 0))
    if left > 0:
        return await message.reply(f"⏳ Команда доступна раз в час. Подожди ещё {int(left // 60) + 1} мин.")
    unit = await random_unit_by_rarity()
    if not unit:
        return await message.reply("В игре пока нет юнитов.")
    _freeunit_cd[uid] = now
    await get_or_create_player(uid, username_of(message.from_user))
    await add_player_unit(uid, unit["id"])

    rarity = await get_rarity(unit["rarity_id"]) if unit["rarity_id"] else None
    name = display_unit_name(unit["name"], rarity["icon"] if rarity else "")
    lines = [f"{message.from_user.full_name}, ты выбил юнита!", name,
             f"⚔️{unit['dmg_min']}-{unit['dmg_max']}", f"❤️{unit['hp']}"]
    for p in json.loads(unit["perks"] or "[]"):
        lines.append(perk_label(p))
    text = "\n".join(lines)

    uname = await _bot_username(message.bot)
    btn = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✉️ Перейти в ЛС бота", url=f"https://t.me/{uname}")]])
    if unit["photo"]:
        await message.answer_photo(unit["photo"], caption=text, reply_markup=btn)
    else:
        await message.answer(text, reply_markup=btn)


@group_router.message(F.chat.type.in_({"group", "supergroup"}))
async def h_group_track(message: Message):
    """Запоминаем любую группу где есть бот — для рассылок абьюза."""
    await save_group(message.chat.id)


# ============================== БОЙ / ПОДБОР ==============================
_queue: list[dict] = []
_sessions: dict[int, "Session"] = {}
_next_sid = 1


class Session:
    def __init__(self, sid, battle, sides, is_bot, boss_clan_id=None):
        self.id = sid
        self.battle = battle
        self.sides = sides
        self.is_bot = is_bot
        self.boss_clan_id = boss_clan_id
        self.task = None
        self.done = False


def matchmaking_text(qcount):
    return f"Идёт подбор...\nИгроков в очереди: {qcount}"


def matchmaking_kb():
    return ikb([[("⬆️ Найти противника выше категории", "play:higher")],
                [("❌ Отмена", "play:cancel")]])


@router.message(F.text == BTN_PLAY)
async def h_play(message: Message):
    uid = message.from_user.id
    if any(q["user_id"] == uid for q in _queue) or _in_battle(uid):
        return await message.answer("Ты уже в бою или подборе!")
    await message.answer("Выберите с кем хотите играть:",
                         reply_markup=ikb([[("🏆Игрок🏆", "play:pvp")], [("🤖Бот🤖", "play:bot")]]))


async def _ensure_equipped(user_id):
    player = await get_player(user_id)
    units = await list_player_units(user_id)
    if not units:
        return None
    if not player["equipped_pu"] or not any(u["pu_id"] == player["equipped_pu"] for u in units):
        await set_equipped(user_id, units[0]["pu_id"])
        return units[0]["pu_id"]
    return player["equipped_pu"]


def _in_battle(uid):
    return any(info["user_id"] == uid for s in _sessions.values() for info in s.sides.values())


@router.callback_query(F.data == "play:bot")
async def h_play_bot(call: CallbackQuery):
    uid = call.from_user.id
    if any(q["user_id"] == uid for q in _queue) or _in_battle(uid):
        return await call.answer("Ты уже в бою или подборе!", show_alert=True)
    pu = await _ensure_equipped(uid)
    if not pu:
        return await call.answer("Сначала получи юнита в 👤Суммон👤!", show_alert=True)
    await call.message.edit_text(
        "Выберите сложность бота:",
        reply_markup=ikb([
            [("😊 Лёгкий (x0.2 кубков)", "play:diff:easy")],
            [("😐 Средний (x0.5 кубков)", "play:diff:medium")],
            [("😤 Сложный (x1.0 кубков)", "play:diff:hard")],
            [("❌ Отмена", "play:cancel")],
        ]))
    await call.answer()


@router.callback_query(F.data.startswith("play:diff:"))
async def h_play_bot_diff(call: CallbackQuery):
    difficulty = call.data.split(":")[2]
    if difficulty not in BOT_DIFF_INFO:
        return await call.answer()
    uid = call.from_user.id
    if any(q["user_id"] == uid for q in _queue) or _in_battle(uid):
        return await call.answer("Ты уже в бою или подборе!", show_alert=True)
    pu = await _ensure_equipped(uid)
    if not pu:
        return await call.answer("Сначала получи юнита в 👤Суммон👤!", show_alert=True)
    label = BOT_DIFF_INFO[difficulty]["label"]
    await call.message.edit_text(f"🤖 Бой с ботом ({label}) начнётся через 3 секунды...")
    await call.answer()
    player = await get_player(uid)
    unit = await build_battle_unit(pu)
    c1 = Combatant(1, display_name(player), unit, is_bot_battle=True)
    c2 = await make_bot_opponent(pu, difficulty=difficulty)
    if not c2:
        return await call.bot.send_message(call.message.chat.id, "Нет подходящих противников. Попробуй позже.")
    await asyncio.sleep(3)
    sides = {1: {"user_id": uid, "chat_id": call.message.chat.id, "msg_id": None,
                 "pre_cups": player["cups"], "diff_mult": BOT_DIFF_INFO[difficulty]["reward_mult"]}}
    await _start_session(call.bot, Battle(c1, c2, is_bot=True), sides, True)


@router.callback_query(F.data == "play:pvp")
async def h_play_pvp(call: CallbackQuery):
    uid = call.from_user.id
    if any(q["user_id"] == uid for q in _queue) or _in_battle(uid):
        return await call.answer("Ты уже в подборе/бою", show_alert=True)
    pu = await _ensure_equipped(uid)
    if not pu:
        return await call.answer("Сначала получи юнита в 👤Суммон👤!", show_alert=True)
    player = await get_player(uid)
    category = category_of(player["cups"])
    opp = next((q for q in _queue if q["category"] == category), None)
    if opp:
        _queue.remove(opp)
        await call.message.edit_text("Игрок найден!\nБой начнётся через 5 секунд...")
        try:
            await call.bot.edit_message_text("Игрок найден!\nБой начнётся через 5 секунд...",
                                             chat_id=opp["chat_id"], message_id=opp["msg_id"])
        except Exception:
            pass
        await call.answer()
        await _start_pvp(call.bot, opp, {"user_id": uid, "chat_id": call.message.chat.id,
                                         "msg_id": call.message.message_id, "pre_cups": player["cups"],
                                         "pu": pu, "name": display_name(player)})
    else:
        await call.message.edit_text(matchmaking_text(len(_queue) + 1), reply_markup=matchmaking_kb())
        _queue.append({"user_id": uid, "chat_id": call.message.chat.id, "msg_id": call.message.message_id,
                       "category": category, "pre_cups": player["cups"], "pu": pu, "name": display_name(player)})
        await call.answer()


@router.callback_query(F.data == "play:higher")
async def h_play_higher(call: CallbackQuery):
    uid = call.from_user.id
    me = next((q for q in _queue if q["user_id"] == uid), None)
    if not me:
        return await call.answer("Ты не в очереди", show_alert=True)
    # ближайший по силе соперник выше моей категории
    higher = [q for q in _queue if q["user_id"] != uid and q["category"] > me["category"]]
    if not higher:
        return await call.answer("Пока нет соперников выше категории. Ждём дальше...", show_alert=True)
    opp = min(higher, key=lambda q: q["category"])
    _queue.remove(me)
    _queue.remove(opp)
    await call.message.edit_text("Игрок найден!\nБой начнётся через 5 секунд...")
    try:
        await call.bot.edit_message_text("Игрок найден!\nБой начнётся через 5 секунд...",
                                         chat_id=opp["chat_id"], message_id=opp["msg_id"])
    except Exception:
        pass
    await call.answer()
    await _start_pvp(call.bot, opp, me)


@router.callback_query(F.data == "play:cancel")
async def h_play_cancel(call: CallbackQuery):
    entry = next((q for q in _queue if q["user_id"] == call.from_user.id), None)
    if entry:
        _queue.remove(entry)
    await go_main(call)


async def _start_pvp(bot, e1, e2):
    await asyncio.sleep(BATTLE_START_DELAY)
    u1, u2 = await build_battle_unit(e1["pu"]), await build_battle_unit(e2["pu"])
    if not u1 or not u2:
        return
    sides = {1: {"user_id": e1["user_id"], "chat_id": e1["chat_id"], "msg_id": None, "pre_cups": e1["pre_cups"]},
             2: {"user_id": e2["user_id"], "chat_id": e2["chat_id"], "msg_id": None, "pre_cups": e2["pre_cups"]}}
    await _start_session(bot, Battle(Combatant(1, e1["name"], u1), Combatant(2, e2["name"], u2)), sides, False)


def _surrender_kb(sid, side):
    return ikb([[("🏳️сдаться🏳️", f"bt:surr:{sid}:{side}")]])


async def _start_session(bot, battle, sides, is_bot, boss_clan_id=None):
    global _next_sid
    sid = _next_sid
    _next_sid += 1
    session = Session(sid, battle, sides, is_bot, boss_clan_id=boss_clan_id)
    _sessions[sid] = session
    photos = [c.photo for c in (battle.c1, battle.c2) if c.photo]
    for side, info in sides.items():
        ids = []
        try:
            ids += await send_album(bot, info["chat_id"], photos)
        except Exception:
            pass
        board = await bot.send_message(info["chat_id"], battle.render(), reply_markup=_surrender_kb(sid, side))
        info["board_id"] = board.message_id
        ids.append(board.message_id)
        info["msg_ids"] = ids
    session.task = asyncio.create_task(_run_loop(session, bot))


async def _broadcast(session, bot):
    text = session.battle.render()
    for side, info in session.sides.items():
        markup = None if session.battle.finished else _surrender_kb(session.id, side)
        try:
            await bot.edit_message_text(text, chat_id=info["chat_id"], message_id=info["board_id"], reply_markup=markup)
        except Exception:
            pass


async def _run_loop(session, bot):
    try:
        while not session.battle.finished:
            await asyncio.sleep(TURN_DELAY)
            if session.done or session.battle.finished:
                break
            session.battle.step()
            await _broadcast(session, bot)
    except asyncio.CancelledError:
        return
    await _end_session(session, bot)


@router.callback_query(F.data.startswith("bt:surr:"))
async def h_surrender(call: CallbackQuery):
    _, _, sid, side = call.data.split(":")
    sid, side = int(sid), int(side)
    session = _sessions.get(sid)
    if not session or session.done:
        return await call.answer()
    info = session.sides.get(side)
    if not info or info["user_id"] != call.from_user.id:
        return await call.answer("Это не твоя кнопка", show_alert=True)
    session.battle.surrender(side)
    await call.answer("Ты сдался")
    await _broadcast(session, call.bot)
    if session.task:
        session.task.cancel()
    await _end_session(session, call.bot)


async def _end_session(session, bot):
    if session.done:
        return
    session.done = True
    _sessions.pop(session.id, None)
    b = session.battle
    # бой с боссом клана: сохранить оставшееся HP и убрать сломанные предметы
    if session.boss_clan_id:
        try:
            await persist_boss_after_battle(session.boss_clan_id, b.c2)
        except Exception:
            pass
    for side, info in session.sides.items():
        # удалить сообщение(я) боя, затем показать окно результата
        for mid in info.get("msg_ids", []):
            try:
                await bot.delete_message(info["chat_id"], mid)
            except Exception:
                pass
        await _send_result(bot, info, b.winner == side, b.winner == 0, session.is_bot)


async def _send_result(bot, info, won, draw, is_bot):
    uid, pre_cups = info["user_id"], info["pre_cups"]
    diff_mult = info.get("diff_mult", 1.0) if is_bot else 1.0
    lines = []

    don_mult = await get_coin_mult(uid)
    if draw:
        lines.append("Ничья!")
        # 0 кубков, валюта как за победу
        coins = round(random.randint(*COIN_REWARD_WIN) * get_event_mult("earn") * don_mult)
        await give_currency(uid, COIN_CURRENCY_ID, coins)
        lines.append(f"+{coins}{COIN_ICON}")
        for icon, amt in await grant_battle_currencies(uid, True, pre_cups, don_mult):
            lines.append(f"+{amt}{icon}")
    else:
        lines.append("Вы победили!" if won else "Вы проиграли!")
        cat = category_of(pre_cups)
        # Кубки
        if won:
            raw_delta = cup_reward(cat, True)
            delta = max(1, round(raw_delta * diff_mult * get_event_mult("wins")))
        else:
            delta = cup_reward(cat, False)  # отрицательный, без множителя сложности
        await add_cups(uid, delta)
        lines.append(f"{'+' if delta >= 0 else ''}{delta}🏆")
        # Монеты
        coin_range = COIN_REWARD_WIN if won else COIN_REWARD_LOSS
        coins = round(random.randint(*coin_range) * get_event_mult("earn") * don_mult)
        await give_currency(uid, COIN_CURRENCY_ID, coins)
        lines.append(f"+{coins}{COIN_ICON}")
        # Доп. валюты
        for icon, amt in await grant_battle_currencies(uid, won, pre_cups, don_mult):
            lines.append(f"+{amt}{icon}")

    await bot.send_message(info["chat_id"], "\n".join(lines), reply_markup=ikb([[("🚪 Выход", "bt:exit")]]))


async def force_end_user_battles(uid, bot):
    """Принудительно завершить все бои/подбор игрока (вызывается при выходе в меню).
    Сопернику в PvP засчитывается победа, бот/босс-бои просто прекращаются."""
    # убрать из очереди подбора
    _queue[:] = [q for q in _queue if q["user_id"] != uid]
    for sid, session in list(_sessions.items()):
        if session.done or not any(info["user_id"] == uid for info in session.sides.values()):
            continue
        session.done = True
        _sessions.pop(sid, None)
        if session.task:
            session.task.cancel()
        # клановый бой — сохранить нанесённый боссу урон
        if session.boss_clan_id:
            try:
                await persist_boss_after_battle(session.boss_clan_id, session.battle.c2)
            except Exception:
                pass
        for side, info in session.sides.items():
            for mid in info.get("msg_ids", []):
                try:
                    await bot.delete_message(info["chat_id"], mid)
                except Exception:
                    pass
            if info["user_id"] == uid:
                continue  # уходящему игроку покажется главное меню вызывающим кодом
            # соперник по PvP — победа, т.к. оппонент покинул бой
            try:
                await bot.send_message(info["chat_id"], "Соперник покинул бой — тебе засчитана победа!")
            except Exception:
                pass
            try:
                await _send_result(bot, info, True, False, session.is_bot)
            except Exception:
                pass


@router.callback_query(F.data == "bt:exit")
async def h_battle_exit(call: CallbackQuery):
    await go_main(call)


# ============================== АДМИНКА ==============================
class Linear(StatesGroup):
    active = State()


SKIP = ("⏭ Пропустить", "adm:skip")
SECTION_TITLES = {"currency": "Валюты", "rarity": "Редкости", "unit": "Юниты",
                  "summon": "Суммоны", "item": "Предметы", "crate": "Крейты", "boss": "Боссы",
                  "donate": "Донат"}
STEPS = {
    "currency": [
        ("icon", "text", "Отправьте иконку валюты (эмодзи):"),
        ("win_chance", "float", "Шанс выпадения при ПОБЕДЕ, % (0-100):"),
        ("win_min", "int", "Мин. количество при победе:"),
        ("win_max", "int", "Макс. количество при победе:"),
        ("loss_chance", "float", "Шанс выпадения при ПОРАЖЕНИИ, % (0-100):"),
        ("loss_min", "int", "Мин. количество при поражении:"),
        ("loss_max", "int", "Макс. количество при поражении:"),
        ("unlock_rank", "rank", "С какого ранга падает/видна валюта (1-10):"),
    ],
    "rarity": [
        ("icon", "text", "Отправьте иконку редкости (эмодзи):"),
        ("name", "text", "Отправьте название редкости:"),
        ("chance", "int", "Шанс редкости — X в «1 in X» (напр. 10):"),
    ],
    "unit": [
        ("photo", "photo", "Отправьте фото юнита:"),
        ("name", "text", "Отправьте название юнита:"),
        ("rarity_id", "rarity_select", ""),
        ("dmg_min", "int", "Минимальный урон юнита:"),
        ("dmg_max", "int", "Максимальный урон юнита:"),
        ("hp", "int", "ХП юнита:"),
        ("perks", "perks_select", ""),
    ],
    "boss": [
        ("photo", "photo", "Отправьте фото босса:"),
        ("name", "text", "Отправьте название босса:"),
        ("rarity_id", "rarity_select", ""),
        ("dmg_min", "int", "Минимальный урон босса:"),
        ("dmg_max", "int", "Максимальный урон босса:"),
        ("hp", "int", "ХП босса:"),
        ("perks", "perks_select", ""),
    ],
    "item": [
        ("photo", "photo", "Отправьте фото предмета:"),
        ("name", "text", "Название предмета:"),
        ("rarity_id", "rarity_select", ""),
        ("slot", "slot_select", ""),
        ("dmg_add", "int", "Прибавка к урону (+ к мин и макс урону юнита), напр. 5:"),
        ("hp_add", "int", "ХП предмета (щит, ровно столько), напр. 20:"),
        ("perks", "perks_select", ""),
    ],
    "summon": [
        ("name", "text", "Название суммона:"),
        ("price", "int", "Цена одного открытия:"),
        ("unit_ids", "units_select", ""),
        ("item_ids", "items_select", ""),
        ("currency_id", "currency_select", ""),
    ],
    "crate": [
        ("name", "text", "Название крейта:"),
        ("price", "int", "Цена одного открытия:"),
        ("item_ids", "items_select", ""),
        ("currency_id", "currency_select", ""),
    ],
    "admin": [
        ("user_id", "int", "Отправьте Telegram ID нового админа (числом):"),
    ],
    "givecur": [
        ("currency_id", "currency_select", ""),
        ("amount", "int", "Сколько выдать:"),
    ],
    "donate": [
        ("name",        "text",            "Название предложения:"),
        ("description", "text",            "Краткое описание:"),
        ("price_stars", "int",             "Цена в звёздах (⭐️):"),
        ("currency_id", "currency_select", ""),
        ("amount",      "int",             "Количество валюты:"),
    ],
}


def admin_main_kb():
    return ikb([[("🔰Валюты🔰", "adm:sec:currency")], [("🔰Редкости🔰", "adm:sec:rarity")],
                [("🔰Юниты🔰", "adm:sec:unit")], [("🔰Боссы🔰", "adm:sec:boss")],
                [("🔰Предметы🔰", "adm:sec:item")],
                [("🔰Суммон🔰", "adm:sec:summon")], [("🔰Крейты🔰", "adm:sec:crate")],
                [("💳 Донат", "adm:sec:donate")],
                [("👑 Админы", "adm:admins")], [("👾 Абьюз", "abz:home")],
                [("🚪 Выйти", "adm:exit")]])


def slot_kb(edit):
    rows = [[(f"{emoji}{name}{emoji}", f"adm:selslot:{emoji}")] for emoji, name in ITEM_SLOTS]
    if edit:
        rows.append([SKIP])
    return ikb(rows)


def section_kb(form):
    rows = [[("➕ Создать", f"adm:new:{form}")], [("✏️ Изменить", f"adm:editlist:{form}")],
            [("🗑 Удалить", f"adm:dellist:{form}")]]
    if form == "currency":
        rows.append([("💸 Выдать валюту себе", "adm:givecur")])
    rows.append([("⬅️ В админку", "adm:home")])
    return ikb(rows)


def donate_section_kb():
    return ikb([[("➕ Создать предложение", "adm:new:donate")],
                [("✏️ Изменить",           "adm:editlist:donate")],
                [("🗑 Удалить",            "adm:dellist:donate")],
                [("⬅️ В админку",          "adm:home")]])


def skip_kb():
    return ikb([[SKIP]])


def perks_kb(selected, edit):
    rows, row = [], []
    for code in PERK_ORDER:
        row.append((f"{'✅' if code in selected else '▫️'}{perk_label(code)}", f"adm:perk:{code}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([("✅ Готово", "adm:perkdone")])
    if edit:
        rows.append([SKIP])
    return ikb(rows)


def picker_kb(selected, rows_data, cb_prefix, done_cb, edit):
    rows = [[(f"{'✅' if r['id'] in selected else '▫️'}{r['name']}", f"{cb_prefix}{r['id']}")] for r in rows_data]
    rows.append([("✅ Готово", done_cb)])
    if edit:
        rows.append([SKIP])
    return ikb(rows)


def rarity_kb(rarities, edit):
    rows = [[(f"{r['icon']}{r['name']} (1in{r['chance']})", f"adm:selrar:{r['id']}")] for r in rarities]
    if edit:
        rows.append([SKIP])
    return ikb(rows)


async def currency_kb(edit):
    rows = [[(f"{COIN_ICON} Монеты", "adm:selcur:0")]]
    for c in await list_currencies():
        rows.append([(f"{c['icon']} (id{c['id']})", f"adm:selcur:{c['id']}")])
    if edit:
        rows.append([SKIP])
    return ikb(rows)


@router.message(F.text == BTN_ADMIN)
async def h_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🛠 Админ-панель", reply_markup=admin_main_kb())


@router.callback_query(F.data == "adm:home")
async def h_adm_home(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await call.message.edit_text("🛠 Админ-панель", reply_markup=admin_main_kb())
    await call.answer()


@router.callback_query(F.data == "adm:exit")
async def h_adm_exit(call: CallbackQuery):
    await go_main(call)


def render_admins():
    owners = list(ADMIN_IDS)
    dyn = sorted(DYN_ADMINS)
    lines = ["👑 Управление админами", "", "Владельцы (нельзя снять):"]
    lines += [f"• {o}" for o in owners] if owners else ["• —"]
    lines += ["", "Добавленные:"]
    lines += [f"• {d}" for d in dyn] if dyn else ["• —"]
    rows = [[(f"🗑 {d}", f"adm:deladmin:{d}")] for d in dyn]
    rows.append([("➕ Добавить по ID", "adm:new:admin")])
    rows.append([("⬅️ В админку", "adm:home")])
    return "\n".join(lines), ikb(rows)


@router.callback_query(F.data == "adm:admins")
async def h_adm_admins(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
    text, markup = render_admins()
    await call.message.edit_text(text, reply_markup=markup)
    await call.answer()


@router.callback_query(F.data.startswith("adm:deladmin:"))
async def h_adm_deladmin(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
    uid = int(call.data.split(":")[2])
    await remove_admin_db(uid)
    DYN_ADMINS.discard(uid)
    # обновить клавиатуру снятому админу (убрать кнопку «Админка»), если он не владелец
    if uid not in ADMIN_IDS:
        try:
            await call.bot.send_message(uid, "С тебя сняты права администратора.", reply_markup=main_menu_kb(False))
        except Exception:
            pass
    await call.answer("Админ снят")
    text, markup = render_admins()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("adm:sec:"))
async def h_adm_section(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
    form = call.data.split(":")[2]
    if form == "donate":
        dons = await list_admin_donations()
        lines = ["💳Донат — спецпредложения💳",
                 "(донат на валюты за ⭐️ Stars)", "===================="]
        for d in dons:
            c    = await get_currency(d["currency_id"]) if d["currency_id"] != COIN_CURRENCY_ID else None
            icon = c["icon"] if c else COIN_ICON
            lines.append(f"• {d['name']}: {d['price_stars']}⭐️ → +{d['amount']}{icon}")
        if not dons:
            lines.append("Предложений пока нет.")
        await call.message.edit_text("\n".join(lines), reply_markup=donate_section_kb())
    else:
        await call.message.edit_text(f"🔰 {SECTION_TITLES[form]}", reply_markup=section_kb(form))
    await call.answer()


@router.callback_query(F.data.startswith("adm:new:"))
async def h_adm_new(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    form = call.data.split(":")[2]
    await state.set_state(Linear.active)
    await state.set_data({"_form": form, "_mode": "create", "_i": 0})
    await call.answer()
    await show_step(call.message.answer, state)


@router.callback_query(F.data == "adm:givecur")
async def h_adm_givecur(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await state.set_state(Linear.active)
    await state.set_data({"_form": "givecur", "_mode": "create", "_i": 0, "_admin": call.from_user.id})
    await call.answer()
    await show_step(call.message.answer, state)


@router.callback_query(F.data.startswith("adm:editlist:"))
async def h_adm_editlist(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
    form = call.data.split(":")[2]
    items = await entity_list(form)
    if not items:
        return await call.answer("Пусто", show_alert=True)
    rows = [[(lbl, f"adm:edit:{form}:{rid}")] for rid, lbl in items]
    rows.append([("⬅️ Назад", f"adm:sec:{form}")])
    await call.message.edit_text("Выбери для изменения:", reply_markup=ikb(rows))
    await call.answer()


@router.callback_query(F.data.startswith("adm:edit:"))
async def h_adm_edit(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    _, _, form, rid = call.data.split(":")
    data = await preload(form, int(rid))
    data.update(_form=form, _mode="edit", _id=int(rid), _i=0)
    await state.set_state(Linear.active)
    await state.set_data(data)
    await call.answer()
    await call.message.answer("✏️ Изменение. На каждом шаге можно нажать «Пропустить».")
    await show_step(call.message.answer, state)


async def show_step(send, state):
    data = await state.get_data()
    form = data["_form"]
    steps = STEPS[form]
    i = data["_i"]
    if i >= len(steps):
        await FINALIZERS[form](state, send)
        await state.clear()
        return
    field, kind, prompt = steps[i]
    edit = data.get("_mode") == "edit"
    if kind == "rarity_select":
        rars = await list_rarities()
        if not rars:
            await send("⚠️ Сначала создай хотя бы одну редкость.")
            return await state.clear()
        await send("Выберите редкость:", reply_markup=rarity_kb(rars, edit))
    elif kind == "perks_select":
        await send("Выберите перки (✅) и нажмите «Готово»:", reply_markup=perks_kb(set(data.get("perks") or []), edit))
    elif kind == "units_select":
        units = await list_units()
        if not units:
            await send("⚠️ Сначала создай юнитов.")
            return await state.clear()
        await send("Выберите юнитов для суммона (✅) и «Готово»:",
                   reply_markup=picker_kb(set(data.get("unit_ids") or []), units, "adm:selunit:", "adm:unitsdone", edit))
    elif kind == "slot_select":
        await send("Выберите класс (слот) предмета:", reply_markup=slot_kb(edit))
    elif kind == "items_select":
        items = await list_items()
        if not items:
            if form == "crate":
                await send("⚠️ Сначала создай предметы.")
                return await state.clear()
            # для суммона предметы не обязательны — пропускаем шаг
            await state.update_data(item_ids=data.get("item_ids") or [], _i=i + 1)
            return await show_step(send, state)
        opt = "" if form == "crate" else " (можно без предметов)"
        await send(f"Выберите предметы (✅) и «Готово»{opt}:",
                   reply_markup=picker_kb(set(data.get("item_ids") or []), items, "adm:selitem:", "adm:itemsdone", edit))
    elif kind == "currency_select":
        await send("Выберите валюту для открытия:", reply_markup=await currency_kb(edit))
    else:
        await send(prompt, reply_markup=skip_kb() if edit else None)


def _parse(kind, message):
    if kind == "photo":
        return (message.photo[-1].file_id, True) if message.photo else (None, False)
    t = message.text
    if t is None:
        return None, False
    t = t.strip()
    if kind == "text":
        return (t, True) if t else (None, False)
    if kind == "int":
        try:
            return int(t), True
        except ValueError:
            return None, False
    if kind == "float":
        try:
            return float(t.replace(",", ".")), True
        except ValueError:
            return None, False
    if kind == "rank":
        try:
            v = int(t)
            if 1 <= v <= MAX_CATEGORY:
                return v, True
        except ValueError:
            pass
        return None, False
    return None, False


@router.message(Linear.active)
async def h_linear_input(message: Message, state: FSMContext):
    data = await state.get_data()
    field, kind, prompt = STEPS[data["_form"]][data["_i"]]
    if kind in ("rarity_select", "slot_select", "perks_select", "units_select", "items_select", "currency_select"):
        return await message.answer("Используй кнопки выше 👆")
    val, ok = _parse(kind, message)
    if not ok:
        return await message.answer("⚠️ Неверное значение. Попробуй ещё раз:")
    await state.update_data(**{field: val}, _i=data["_i"] + 1)
    await show_step(message.answer, state)


@router.callback_query(Linear.active, F.data == "adm:skip")
async def h_linear_skip(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(_i=data["_i"] + 1)
    await call.answer("Пропущено")
    await show_step(call.message.answer, state)


@router.callback_query(Linear.active, F.data.startswith("adm:selrar:"))
async def h_sel_rarity(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(rarity_id=int(call.data.split(":")[2]), _i=data["_i"] + 1)
    await call.answer()
    await show_step(call.message.answer, state)


@router.callback_query(Linear.active, F.data.startswith("adm:selcur:"))
async def h_sel_currency(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(currency_id=int(call.data.split(":")[2]), _i=data["_i"] + 1)
    await call.answer()
    await show_step(call.message.answer, state)


@router.callback_query(Linear.active, F.data.startswith("adm:selslot:"))
async def h_sel_slot(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(slot=call.data.split(":", 2)[2], _i=data["_i"] + 1)
    await call.answer()
    await show_step(call.message.answer, state)


@router.callback_query(Linear.active, F.data.startswith("adm:perk:"))
async def h_toggle_perk(call: CallbackQuery, state: FSMContext):
    code = call.data.split(":", 2)[2]
    data = await state.get_data()
    sel = list(data.get("perks") or [])
    sel.remove(code) if code in sel else sel.append(code)
    await state.update_data(perks=sel)
    await call.message.edit_reply_markup(reply_markup=perks_kb(set(sel), data.get("_mode") == "edit"))
    await call.answer()


@router.callback_query(Linear.active, F.data == "adm:perkdone")
async def h_perks_done(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(_i=data["_i"] + 1, perks=data.get("perks") or [])
    await call.answer("Готово")
    await show_step(call.message.answer, state)


@router.callback_query(Linear.active, F.data.startswith("adm:selunit:"))
async def h_toggle_unit(call: CallbackQuery, state: FSMContext):
    uid = int(call.data.split(":")[2])
    data = await state.get_data()
    sel = list(data.get("unit_ids") or [])
    sel.remove(uid) if uid in sel else sel.append(uid)
    await state.update_data(unit_ids=sel)
    await call.message.edit_reply_markup(
        reply_markup=picker_kb(set(sel), await list_units(), "adm:selunit:", "adm:unitsdone", data.get("_mode") == "edit"))
    await call.answer()


@router.callback_query(Linear.active, F.data == "adm:unitsdone")
async def h_units_done(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(_i=data["_i"] + 1, unit_ids=data.get("unit_ids") or [])
    await call.answer("Готово")
    await show_step(call.message.answer, state)


@router.callback_query(Linear.active, F.data.startswith("adm:selitem:"))
async def h_toggle_item(call: CallbackQuery, state: FSMContext):
    iid = int(call.data.split(":")[2])
    data = await state.get_data()
    sel = list(data.get("item_ids") or [])
    sel.remove(iid) if iid in sel else sel.append(iid)
    await state.update_data(item_ids=sel)
    await call.message.edit_reply_markup(
        reply_markup=picker_kb(set(sel), await list_items(), "adm:selitem:", "adm:itemsdone", data.get("_mode") == "edit"))
    await call.answer()


@router.callback_query(Linear.active, F.data == "adm:itemsdone")
async def h_items_done(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(_i=data["_i"] + 1, item_ids=data.get("item_ids") or [])
    await call.answer("Готово")
    await show_step(call.message.answer, state)


@router.callback_query(F.data.startswith("adm:dellist:"))
async def h_adm_dellist(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
    form = call.data.split(":")[2]
    items = await entity_list(form)
    if not items:
        return await call.answer("Пусто", show_alert=True)
    rows = [[(f"🗑 {lbl}", f"adm:del:{form}:{rid}")] for rid, lbl in items]
    rows.append([("⬅️ Назад", f"adm:sec:{form}")])
    await call.message.edit_text("Выбери для удаления:", reply_markup=ikb(rows))
    await call.answer()


@router.callback_query(F.data.startswith("adm:del:"))
async def h_adm_del(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
    _, _, form, rid = call.data.split(":")
    await DELETERS[form](int(rid))
    await call.answer("Удалено")
    await call.message.edit_text("🗑 Удалено.", reply_markup=section_kb(form))


async def entity_list(form):
    if form == "currency":
        return [(r["id"], f"{r['icon']} победа{r['win_min']}-{r['win_max']} ранг{r['unlock_rank']}")
                for r in await list_currencies()]
    if form == "rarity":
        return [(r["id"], f"{r['icon']}{r['name']} (1in{r['chance']})") for r in await list_rarities()]
    if form == "unit":
        return [(r["id"], r["name"]) for r in await list_units()]
    if form == "boss":
        return [(r["id"], r["name"]) for r in await list_bosses()]
    if form == "item":
        return [(r["id"], f"{r['slot']}{r['name']}") for r in await list_items()]
    if form == "summon":
        return [(r["id"], r["name"]) for r in await list_summons("summon")]
    if form == "crate":
        return [(r["id"], r["name"]) for r in await list_summons("crate")]
    if form == "donate":
        return [(r["id"], f"{r['name']} {r['price_stars']}⭐️") for r in await list_admin_donations()]
    return []


async def preload(form, rid):
    if form == "currency":
        r = await get_currency(rid)
        return dict(icon=r["icon"], win_chance=r["win_chance"], win_min=r["win_min"], win_max=r["win_max"],
                    loss_chance=r["loss_chance"], loss_min=r["loss_min"], loss_max=r["loss_max"], unlock_rank=r["unlock_rank"])
    if form == "rarity":
        r = await get_rarity(rid)
        return dict(icon=r["icon"], name=r["name"], chance=r["chance"])
    if form == "unit":
        r = await get_unit(rid)
        return dict(photo=r["photo"], name=r["name"], rarity_id=r["rarity_id"], dmg_min=r["dmg_min"],
                    dmg_max=r["dmg_max"], hp=r["hp"], perks=json.loads(r["perks"] or "[]"))
    if form == "boss":
        r = await get_boss(rid)
        return dict(photo=r["photo"], name=r["name"], rarity_id=r["rarity_id"], dmg_min=r["dmg_min"],
                    dmg_max=r["dmg_max"], hp=r["hp"], perks=json.loads(r["perks"] or "[]"))
    if form == "item":
        r = await get_item(rid)
        return dict(photo=r["photo"], name=r["name"], type=r["type"], rarity_id=r["rarity_id"], slot=r["slot"],
                    dmg_add=r["dmg_add"], hp_add=r["hp_add"], perks=json.loads(r["perks"] or "[]"))
    if form in ("summon", "crate"):
        r = await get_summon(rid)
        return dict(name=r["name"], price=r["price"], currency_id=r["currency_id"],
                    unit_ids=json.loads(r["unit_ids"] or "[]"), item_ids=json.loads(r["item_ids"] or "[]"))
    if form == "donate":
        r = await get_admin_donation(rid)
        return dict(name=r["name"], description=r["description"], price_stars=r["price_stars"],
                    currency_id=r["currency_id"], amount=r["amount"])
    return {}


async def fin_currency(state, send):
    d = await state.get_data()
    if d["_mode"] == "create":
        await add_currency(d["icon"], d["win_chance"], d["win_min"], d["win_max"],
                           d["loss_chance"], d["loss_min"], d["loss_max"], d["unlock_rank"])
    else:
        await update_currency(d["_id"], icon=d["icon"], win_chance=d["win_chance"], win_min=d["win_min"],
                              win_max=d["win_max"], loss_chance=d["loss_chance"], loss_min=d["loss_min"],
                              loss_max=d["loss_max"], unlock_rank=d["unlock_rank"])
    await send("✅ Валюта сохранена.", reply_markup=section_kb("currency"))


async def fin_rarity(state, send):
    d = await state.get_data()
    if d["_mode"] == "create":
        await add_rarity(d["icon"], d["name"], d["chance"])
    else:
        await update_rarity(d["_id"], icon=d["icon"], name=d["name"], chance=d["chance"])
    await send("✅ Редкость сохранена.", reply_markup=section_kb("rarity"))


async def fin_unit(state, send):
    d = await state.get_data()
    if d["_mode"] == "create":
        await add_unit(d["photo"], d["name"], d["rarity_id"], d["dmg_min"], d["dmg_max"], d["hp"], d.get("perks") or [])
    else:
        await update_unit(d["_id"], photo=d["photo"], name=d["name"], rarity_id=d["rarity_id"], dmg_min=d["dmg_min"],
                          dmg_max=d["dmg_max"], hp=d["hp"], perks=d.get("perks") or [])
    await send("✅ Юнит сохранён.", reply_markup=section_kb("unit"))


async def fin_boss(state, send):
    d = await state.get_data()
    if d["_mode"] == "create":
        await add_boss(d["photo"], d["name"], d["rarity_id"], d["dmg_min"], d["dmg_max"], d["hp"], d.get("perks") or [])
    else:
        await update_boss(d["_id"], photo=d["photo"], name=d["name"], rarity_id=d["rarity_id"], dmg_min=d["dmg_min"],
                          dmg_max=d["dmg_max"], hp=d["hp"], perks=d.get("perks") or [])
    await send("✅ Босс сохранён.", reply_markup=section_kb("boss"))


async def fin_item(state, send):
    d = await state.get_data()
    slot = d.get("slot") or "🧣"
    type_ = SLOT_NAME.get(slot, "")   # тип = класс предмета
    if d["_mode"] == "create":
        await add_item(d["photo"], d["name"], type_, d["rarity_id"], slot,
                       d["dmg_add"], d["hp_add"], d.get("perks") or [])
    else:
        await update_item(d["_id"], photo=d["photo"], name=d["name"], type=type_, rarity_id=d["rarity_id"],
                          slot=slot, dmg_add=d["dmg_add"], hp_add=d["hp_add"],
                          perks=d.get("perks") or [])
    await send("✅ Предмет сохранён.", reply_markup=section_kb("item"))


async def fin_summon(state, send):
    d = await state.get_data()
    if d["_mode"] == "create":
        await add_summon(d["name"], d["price"], d["currency_id"], d.get("unit_ids") or [], d.get("item_ids") or [])
    else:
        await update_summon(d["_id"], name=d["name"], price=d["price"], currency_id=d["currency_id"],
                            unit_ids=d.get("unit_ids") or [], item_ids=d.get("item_ids") or [],
                            display="{}", next_refresh=0)
    await send("✅ Суммон сохранён.", reply_markup=section_kb("summon"))


async def fin_crate(state, send):
    d = await state.get_data()
    if d["_mode"] == "create":
        await add_summon(d["name"], d["price"], d["currency_id"], [], d.get("item_ids") or [], kind="crate")
    else:
        await update_summon(d["_id"], name=d["name"], price=d["price"], currency_id=d["currency_id"],
                            unit_ids=[], item_ids=d.get("item_ids") or [], display="{}", next_refresh=0)
    await send("✅ Крейт сохранён.", reply_markup=section_kb("crate"))


async def fin_admin(state, send):
    d = await state.get_data()
    uid = int(d["user_id"])
    await add_admin_db(uid)
    DYN_ADMINS.add(uid)
    # сразу выдать новому админу обновлённую клавиатуру с кнопкой «Админка»
    if _BOT is not None:
        try:
            await _BOT.send_message(
                uid, "👑 Тебе выданы права администратора!\nКнопка «🛠 Админка» теперь доступна.",
                reply_markup=main_menu_kb(True))
        except Exception:
            pass  # новый админ ещё не писал боту — увидит кнопку после /start
    await send(f"✅ Админ {uid} добавлен.", reply_markup=ikb([[("👑 К админам", "adm:admins")]]))


async def fin_givecur(state, send):
    d = await state.get_data()
    cid, amount = d["currency_id"], int(d["amount"])
    await give_currency(d["_admin"], cid, amount)
    icon = COIN_ICON if cid == COIN_CURRENCY_ID else (await get_currency(cid))["icon"]
    await send(f"✅ Выдано {amount}{icon}", reply_markup=section_kb("currency"))


async def fin_donate(state, send):
    d = await state.get_data()
    if d["_mode"] == "create":
        await add_admin_donation(d["name"], d.get("description", ""), int(d["price_stars"]),
                                 d["currency_id"], int(d["amount"]))
    else:
        await update_admin_donation(d["_id"], name=d["name"], description=d.get("description", ""),
                                    price_stars=int(d["price_stars"]), currency_id=d["currency_id"],
                                    amount=int(d["amount"]))
    await send("✅ Донат-предложение сохранено.", reply_markup=donate_section_kb())


FINALIZERS = {"currency": fin_currency, "rarity": fin_rarity, "unit": fin_unit, "item": fin_item,
              "summon": fin_summon, "crate": fin_crate, "admin": fin_admin, "givecur": fin_givecur,
              "boss": fin_boss, "donate": fin_donate}
DELETERS = {"currency": delete_currency, "rarity": delete_rarity, "unit": delete_unit,
            "item": delete_item, "summon": delete_summon, "crate": delete_summon, "boss": delete_boss,
            "donate": delete_admin_donation}


# ============================== АБЬЮЗ-ПАНЕЛЬ ==============================
class AbhuzFSM(StatesGroup):
    bc_text = State()       # рассылка: текст
    ga_kind = State()       # раздача: тип (unit/currency/item)
    ga_select = State()     # раздача: выбор сущности
    ga_amount = State()     # раздача: количество
    rnd_kind = State()      # рандом: тип
    rnd_select = State()    # рандом: выбор сущности
    rnd_amount = State()    # рандом: количество
    rnd_chance = State()    # рандом: шанс %
    ev_type = State()       # событие: тип
    ev_time = State()       # событие: длительность мин
    ev_mult = State()       # событие: множитель


def abhuz_kb():
    return ikb([[("📢 Сообщение",    "abz:bc")],  [("🎁 Раздача",     "abz:ga")],
                [("🎲 Рандом",       "abz:rnd")], [("⚡️ События",     "abz:ev")],
                [("👤 Выдать игроку","abz:giveid")],
                [("⬅️ Назад",        "adm:home")]])


@router.callback_query(F.data == "abz:home")
async def h_abz_home(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await state.clear()
    await call.message.edit_text("👾 Абьюз-панель", reply_markup=abhuz_kb())
    await call.answer()


@router.callback_query(F.data == "abz:cancel")
async def h_abz_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await call.bot.send_message(call.message.chat.id, "👾 Абьюз-панель", reply_markup=abhuz_kb())
    await call.answer()


# ─── ВСПОМОГАТЕЛЬНАЯ РАССЫЛКА ────────────────────────────────────────────────
async def _broadcast_dm(bot, text, photo=None, reply_markup=None):
    """Рассылка в личку всем игрокам. Возвращает кол-во успешных отправок."""
    cur = await _db.execute("SELECT user_id FROM players")
    players = await cur.fetchall()
    sent = 0
    for p in players:
        try:
            if photo:
                await bot.send_photo(p["user_id"], photo, caption=text, reply_markup=reply_markup)
            else:
                await bot.send_message(p["user_id"], text, reply_markup=reply_markup)
            sent += 1
        except Exception:
            pass
    return sent


async def _broadcast_groups(bot, text, reply_markup=None):
    for chat_id in await list_groups():
        try:
            await bot.send_message(chat_id, text, reply_markup=reply_markup)
        except Exception:
            pass


async def _goto_btn_kb(bot):
    uname = await _bot_username(bot)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✉️ Перейти в ЛС бота", url=f"https://t.me/{uname}")]])


# ─── СООБЩЕНИЕ ───────────────────────────────────────────────────────────────
@router.callback_query(F.data == "abz:bc")
async def h_abz_bc(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await state.set_state(AbhuzFSM.bc_text)
    await call.message.answer("Введите сообщение для рассылки:",
                              reply_markup=ikb([[("❌ Отмена", "abz:cancel")]]))
    await call.answer()


@router.message(AbhuzFSM.bc_text)
async def h_abz_bc_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    text = (message.text or "").strip()
    if not text:
        return await message.answer("Пустое сообщение. Попробуй ещё раз:")
    await state.clear()
    header = f"[Админ Панель]\nСообщение от {username_of(message.from_user)}:\n{text}"
    go_btn = await _goto_btn_kb(message.bot)
    sent = await _broadcast_dm(message.bot, header)
    await _broadcast_groups(message.bot, header, reply_markup=go_btn)
    await message.answer(f"✅ Разослано {sent} пользователям.", reply_markup=abhuz_kb())


# ─── РАЗДАЧА ─────────────────────────────────────────────────────────────────
def _ga_kind_kb(prefix):
    return ikb([
        [("👤 Юнит", f"{prefix}:unit"), ("💰 Валюта", f"{prefix}:currency")],
        [("🧩 Предмет", f"{prefix}:item")],
        [("❌ Отмена", "abz:cancel")],
    ])


@router.callback_query(F.data == "abz:ga")
async def h_abz_ga(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await state.set_state(AbhuzFSM.ga_kind)
    await call.message.answer("Что раздаём?", reply_markup=_ga_kind_kb("abz:ga:k"))
    await call.answer()


@router.callback_query(AbhuzFSM.ga_kind, F.data.startswith("abz:ga:k:"))
async def h_abz_ga_kind(call: CallbackQuery, state: FSMContext):
    kind = call.data.split(":")[3]
    await state.update_data(ga_kind=kind)
    await call.answer()
    await call.message.delete()
    if kind == "currency":
        curs = [(COIN_ICON, COIN_CURRENCY_ID)] + [(c["icon"], c["id"]) for c in await list_currencies()]
        rows = [[(f"{icon} (id{cid})" if cid != COIN_CURRENCY_ID else f"{icon} Монеты", f"abz:ga:sel:{cid}")]
                for icon, cid in curs]
        rows.append([("❌ Отмена", "abz:cancel")])
        await state.set_state(AbhuzFSM.ga_select)
        await call.bot.send_message(call.message.chat.id, "Выберите валюту:", reply_markup=ikb(rows))
    elif kind == "unit":
        units = await list_units()
        if not units:
            await state.clear()
            return await call.bot.send_message(call.message.chat.id, "Нет юнитов.", reply_markup=abhuz_kb())
        rows = [[(u["name"], f"abz:ga:sel:{u['id']}")] for u in units]
        rows.append([("❌ Отмена", "abz:cancel")])
        await state.set_state(AbhuzFSM.ga_select)
        await call.bot.send_message(call.message.chat.id, "Выберите юнита:", reply_markup=ikb(rows))
    else:
        items = await list_items()
        if not items:
            await state.clear()
            return await call.bot.send_message(call.message.chat.id, "Нет предметов.", reply_markup=abhuz_kb())
        rows = [[(f"{it['slot']}{it['name']}", f"abz:ga:sel:{it['id']}")] for it in items]
        rows.append([("❌ Отмена", "abz:cancel")])
        await state.set_state(AbhuzFSM.ga_select)
        await call.bot.send_message(call.message.chat.id, "Выберите предмет:", reply_markup=ikb(rows))


@router.callback_query(AbhuzFSM.ga_select, F.data.startswith("abz:ga:sel:"))
async def h_abz_ga_sel(call: CallbackQuery, state: FSMContext):
    entity_id = int(call.data.split(":")[3])
    await state.update_data(ga_entity_id=entity_id)
    await state.set_state(AbhuzFSM.ga_amount)
    await call.message.delete()
    await call.bot.send_message(call.message.chat.id, "Сколько выдать каждому?",
                                reply_markup=ikb([[("❌ Отмена", "abz:cancel")]]))
    await call.answer()


@router.message(AbhuzFSM.ga_amount)
async def h_abz_ga_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        return await message.answer("Введи целое положительное число:")
    data = await state.get_data()
    await state.clear()
    kind = data["ga_kind"]
    entity_id = data["ga_entity_id"]
    await _exec_giveall(message.bot, message.chat.id, kind, entity_id, amount)


async def _exec_giveall(bot, admin_chat_id, kind, entity_id, amount):
    cur = await _db.execute("SELECT user_id FROM players")
    all_players = [r["user_id"] for r in await cur.fetchall()]
    go_btn = await _goto_btn_kb(bot)

    if kind == "currency":
        icon = COIN_ICON if entity_id == COIN_CURRENCY_ID else (await get_currency(entity_id))["icon"]
        for uid in all_players:
            await give_currency(uid, entity_id, amount)
        dm_text = f"[абьюз]\nПолучено {amount}{icon}!"
        group_text = f"[абьюз]\nВсем выдано по {amount}{icon}!"
        sent = await _broadcast_dm(bot, dm_text)
        await _broadcast_groups(bot, group_text, reply_markup=go_btn)
        await bot.send_message(admin_chat_id, f"✅ Выдано {amount}{icon} x{sent} игроков.", reply_markup=abhuz_kb())

    elif kind == "unit":
        unit = await get_unit(entity_id)
        if not unit:
            return await bot.send_message(admin_chat_id, "Юнит не найден.", reply_markup=abhuz_kb())
        rar = await get_rarity(unit["rarity_id"]) if unit["rarity_id"] else None
        name = display_unit_name(unit["name"], rar["icon"] if rar else "")
        for _ in range(amount):
            for uid in all_players:
                await add_player_unit(uid, entity_id)
        stats = f"⚔️{unit['dmg_min']}-{unit['dmg_max']} ❤️{unit['hp']}"
        dm_text = f"[абьюз]\nВы получили юнита!\n{name}\n{stats}"
        group_text = f"[абьюз]\nВсем был выдан халявный юнит!"
        sent = await _broadcast_dm(bot, dm_text, photo=unit["photo"])
        await _broadcast_groups(bot, group_text, reply_markup=go_btn)
        await bot.send_message(admin_chat_id, f"✅ Выдан юнит «{name}» x{sent} игроков.", reply_markup=abhuz_kb())

    else:
        item = await get_item(entity_id)
        if not item:
            return await bot.send_message(admin_chat_id, "Предмет не найден.", reply_markup=abhuz_kb())
        rar = await get_rarity(item["rarity_id"]) if item["rarity_id"] else None
        name = display_unit_name(item["name"], rar["icon"] if rar else "")
        for _ in range(amount):
            for uid in all_players:
                await add_player_item(uid, entity_id)
        stats = f"⚔️+{item['dmg_add']} ❤️{item['hp_add']}"
        dm_text = f"[абьюз]\nВы получили предмет!\n{name}\n{stats}"
        group_text = f"[абьюз]\nВсем был выдан халявный предмет!"
        sent = await _broadcast_dm(bot, dm_text, photo=item["photo"])
        await _broadcast_groups(bot, group_text, reply_markup=go_btn)
        await bot.send_message(admin_chat_id, f"✅ Выдан предмет «{name}» x{sent} игроков.", reply_markup=abhuz_kb())


# ─── РАНДОМ ──────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "abz:rnd")
async def h_abz_rnd(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await state.set_state(AbhuzFSM.rnd_kind)
    await call.message.answer("Что разыгрываем?", reply_markup=_ga_kind_kb("abz:rnd:k"))
    await call.answer()


@router.callback_query(AbhuzFSM.rnd_kind, F.data.startswith("abz:rnd:k:"))
async def h_abz_rnd_kind(call: CallbackQuery, state: FSMContext):
    kind = call.data.split(":")[3]
    await state.update_data(rnd_kind=kind)
    await call.answer()
    await call.message.delete()
    if kind == "currency":
        curs = [(COIN_ICON, COIN_CURRENCY_ID)] + [(c["icon"], c["id"]) for c in await list_currencies()]
        rows = [[(f"{icon} (id{cid})" if cid != COIN_CURRENCY_ID else f"{icon} Монеты", f"abz:rnd:sel:{cid}")]
                for icon, cid in curs]
        rows.append([("❌ Отмена", "abz:cancel")])
        await state.set_state(AbhuzFSM.rnd_select)
        await call.bot.send_message(call.message.chat.id, "Выберите валюту:", reply_markup=ikb(rows))
    elif kind == "unit":
        units = await list_units()
        if not units:
            await state.clear()
            return await call.bot.send_message(call.message.chat.id, "Нет юнитов.", reply_markup=abhuz_kb())
        rows = [[(u["name"], f"abz:rnd:sel:{u['id']}")] for u in units]
        rows.append([("❌ Отмена", "abz:cancel")])
        await state.set_state(AbhuzFSM.rnd_select)
        await call.bot.send_message(call.message.chat.id, "Выберите юнита:", reply_markup=ikb(rows))
    else:
        items = await list_items()
        if not items:
            await state.clear()
            return await call.bot.send_message(call.message.chat.id, "Нет предметов.", reply_markup=abhuz_kb())
        rows = [[(f"{it['slot']}{it['name']}", f"abz:rnd:sel:{it['id']}")] for it in items]
        rows.append([("❌ Отмена", "abz:cancel")])
        await state.set_state(AbhuzFSM.rnd_select)
        await call.bot.send_message(call.message.chat.id, "Выберите предмет:", reply_markup=ikb(rows))


@router.callback_query(AbhuzFSM.rnd_select, F.data.startswith("abz:rnd:sel:"))
async def h_abz_rnd_sel(call: CallbackQuery, state: FSMContext):
    entity_id = int(call.data.split(":")[3])
    await state.update_data(rnd_entity_id=entity_id)
    await state.set_state(AbhuzFSM.rnd_amount)
    await call.message.delete()
    await call.bot.send_message(call.message.chat.id, "Сколько выдать победителям?",
                                reply_markup=ikb([[("❌ Отмена", "abz:cancel")]]))
    await call.answer()


@router.message(AbhuzFSM.rnd_amount)
async def h_abz_rnd_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        return await message.answer("Введи целое положительное число:")
    await state.update_data(rnd_amount=amount)
    await state.set_state(AbhuzFSM.rnd_chance)
    await message.answer("Шанс на успех (0-100%):",
                         reply_markup=ikb([[("❌ Отмена", "abz:cancel")]]))


@router.message(AbhuzFSM.rnd_chance)
async def h_abz_rnd_chance(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    try:
        chance = float(message.text.strip().replace(",", "."))
        if not (0 < chance <= 100):
            raise ValueError
    except (ValueError, AttributeError):
        return await message.answer("Введи число от 0.1 до 100:")
    data = await state.get_data()
    await state.clear()
    await _exec_random(message.bot, message.chat.id,
                       data["rnd_kind"], data["rnd_entity_id"], data["rnd_amount"], chance)


async def _exec_random(bot, admin_chat_id, kind, entity_id, amount, chance):
    cur = await _db.execute("SELECT user_id FROM players")
    all_players = [r["user_id"] for r in await cur.fetchall()]
    go_btn = await _goto_btn_kb(bot)
    chance_str = f"{chance:g}%"

    # подготовить метаданные
    if kind == "currency":
        icon = COIN_ICON if entity_id == COIN_CURRENCY_ID else (await get_currency(entity_id))["icon"]
        item_label = f"{amount}{icon}"
        photo = None
    elif kind == "unit":
        unit = await get_unit(entity_id)
        rar = await get_rarity(unit["rarity_id"]) if unit and unit["rarity_id"] else None
        item_label = display_unit_name(unit["name"], rar["icon"] if rar else "") if unit else "?"
        photo = unit["photo"] if unit else None
    else:
        item = await get_item(entity_id)
        rar = await get_rarity(item["rarity_id"]) if item and item["rarity_id"] else None
        item_label = display_unit_name(item["name"], rar["icon"] if rar else "") if item else "?"
        photo = item["photo"] if item else None

    winners = 0
    for uid in all_players:
        success = random.random() * 100 < chance
        if success:
            winners += 1
            if kind == "currency":
                await give_currency(uid, entity_id, amount)
                dm_text = f"❇️Успех ({chance_str})❇️\n[абьюз]\nПолучено {amount}{icon}!"
            elif kind == "unit":
                for _ in range(amount):
                    await add_player_unit(uid, entity_id)
                stats = f"⚔️{unit['dmg_min']}-{unit['dmg_max']} ❤️{unit['hp']}" if unit else ""
                dm_text = f"❇️Успех ({chance_str})❇️\n[абьюз]\nВы получили юнита!\n{item_label}\n{stats}"
            else:
                for _ in range(amount):
                    await add_player_item(uid, entity_id)
                stats = f"⚔️+{item['dmg_add']} ❤️{item['hp_add']}" if item else ""
                dm_text = f"❇️Успех ({chance_str})❇️\n[абьюз]\nВы получили предмет!\n{item_label}\n{stats}"
            try:
                if photo and kind != "currency":
                    await bot.send_photo(uid, photo, caption=dm_text)
                else:
                    await bot.send_message(uid, dm_text)
            except Exception:
                pass
        else:
            fail_text = f"⛔️Неудача ({chance_str})⛔️\n[абьюз]\nВам не повезло..."
            try:
                await bot.send_message(uid, fail_text)
            except Exception:
                pass

    group_text = f"[абьюз]\nЗапущена случайная раздача {item_label}! Шанс: {chance_str}"
    await _broadcast_groups(bot, group_text, reply_markup=go_btn)
    await bot.send_message(admin_chat_id,
                           f"✅ Рандом завершён. Победителей: {winners}/{len(all_players)}.",
                           reply_markup=abhuz_kb())


# ─── СОБЫТИЯ ─────────────────────────────────────────────────────────────────
def _abz_ev_text():
    now = int(time.time())
    active = [ev for ev in _ACTIVE_EVENTS if ev["end_time"] > now]
    lines = ["⚡️ События", ""]
    if active:
        lines.append("Активные сейчас:")
        for ev in active:
            mins = (ev["end_time"] - now) // 60
            label = EVENT_TYPES.get(ev["etype"], ev["etype"])
            lines.append(f"• {label} x{fmt_mult(ev['multiplier'])} — {mins} мин")
    else:
        lines.append("Активных событий нет.")
    lines += ["", "Выберите событие для запуска:"]
    return "\n".join(lines)


def _abz_ev_kb():
    now = int(time.time())
    active = [ev for ev in _ACTIVE_EVENTS if ev["end_time"] > now]
    rows = []
    for ev in active:
        label = EVENT_TYPES.get(ev["etype"], ev["etype"])
        rows.append([(f"🗑 {label} x{fmt_mult(ev['multiplier'])}", f"abz:evdel:{ev['id']}")])
    if active:
        rows.append([("🧹 Закончить все события", "abz:evclear")])
    rows += [[(label, f"abz:ev:type:{etype}")] for etype, label in EVENT_TYPES.items()]
    rows.append([("❌ Отмена", "abz:cancel")])
    return ikb(rows)


@router.callback_query(F.data == "abz:ev")
async def h_abz_ev(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await state.set_state(AbhuzFSM.ev_type)
    await call.message.answer(_abz_ev_text(), reply_markup=_abz_ev_kb())
    await call.answer()


@router.callback_query(F.data.startswith("abz:evdel:"))
async def h_abz_ev_del(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await delete_event_db(int(call.data.split(":")[2]))
    await state.set_state(AbhuzFSM.ev_type)
    await call.answer("Событие удалено")
    try:
        await call.message.edit_text(_abz_ev_text(), reply_markup=_abz_ev_kb())
    except Exception:
        pass


@router.callback_query(F.data == "abz:evclear")
async def h_abz_ev_clear(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await clear_events_db()
    await state.set_state(AbhuzFSM.ev_type)
    await call.answer("Все события завершены")
    try:
        await call.message.edit_text(_abz_ev_text(), reply_markup=_abz_ev_kb())
    except Exception:
        pass


@router.callback_query(AbhuzFSM.ev_type, F.data.startswith("abz:ev:type:"))
async def h_abz_ev_type(call: CallbackQuery, state: FSMContext):
    etype = call.data.split(":")[3]
    await state.update_data(ev_type=etype)
    await state.set_state(AbhuzFSM.ev_time)
    await call.message.delete()
    await call.bot.send_message(call.message.chat.id, "Длительность события в минутах:",
                                reply_markup=ikb([[("❌ Отмена", "abz:cancel")]]))
    await call.answer()


@router.message(AbhuzFSM.ev_time)
async def h_abz_ev_time(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    try:
        minutes = int(message.text.strip())
        if minutes <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        return await message.answer("Введи целое положительное число минут:")
    await state.update_data(ev_time=minutes)
    await state.set_state(AbhuzFSM.ev_mult)
    await message.answer("Множитель события (напр. 2):",
                         reply_markup=ikb([[("❌ Отмена", "abz:cancel")]]))


@router.message(AbhuzFSM.ev_mult)
async def h_abz_ev_mult(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    try:
        mult = float(message.text.strip().replace(",", "."))
        if mult <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        return await message.answer("Введи положительное число (напр. 2):")
    data = await state.get_data()
    await state.clear()
    etype = data["ev_type"]
    minutes = data["ev_time"]
    await add_event_db(etype, mult, minutes)
    label = EVENT_TYPES.get(etype, etype)
    broadcast_text = f"[Событие]\nЗапущено событие {label} x{fmt_mult(mult)} на {minutes} минут!"
    go_btn = await _goto_btn_kb(message.bot)
    await _broadcast_dm(message.bot, broadcast_text)
    await _broadcast_groups(message.bot, broadcast_text, reply_markup=go_btn)
    await message.answer(f"✅ Событие запущено: {label} x{fmt_mult(mult)} на {minutes} мин.",
                         reply_markup=abhuz_kb())


# ============================== КЛАНЫ ==============================
class ClanFSM(StatesGroup):
    cr_photo = State()
    cr_name = State()
    cr_desc = State()
    edit_desc = State()
    edit_name = State()


def _uname(row):
    return row["username"] or f"id{row['user_id']}"


async def send_clan_home(bot, chat_id, user_id):
    clan = await get_player_clan(user_id)
    if not clan:
        kb = ikb([[("➕ Создать клан (5000💰)", "clan:create")],
                  [("🔎 Найти клан", "clan:find:0")],
                  [("🚪 Выйти", "clan:exit")]])
        await bot.send_message(chat_id, "Вы не состоите в клане!", reply_markup=kb)
        return
    count = await clan_member_count(clan["id"])
    is_owner = clan["owner_id"] == user_id
    lines = [clan["name"], f"👥 {count}/{CLAN_MAX_MEMBERS}", "", clan["description"] or "—"]
    rows = [[("⚙️ Настроить клан", "clan:settings")],
            [("👹 Настроить босса", "clan:boss")],
            [("⚔️ Клановые бои", "clan:battles")]]
    if not is_owner:
        rows.append([("🚪 Покинуть клан", "clan:leave")])
    rows.append([("🚪 Выйти", "clan:exit")])
    if clan["photo"]:
        await bot.send_photo(chat_id, clan["photo"], caption="\n".join(lines), reply_markup=ikb(rows))
    else:
        await bot.send_message(chat_id, "\n".join(lines), reply_markup=ikb(rows))


@router.message(F.text == BTN_CLAN)
async def h_clan(message: Message):
    await send_clan_home(message.bot, message.chat.id, message.from_user.id)


@router.callback_query(F.data == "clan:exit")
async def h_clan_exit(call: CallbackQuery):
    await go_main(call)


@router.callback_query(F.data == "clan:noop")
async def h_clan_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data == "clan:home")
async def h_clan_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_home(call.bot, call.message.chat.id, call.from_user.id)
    await call.answer()


@router.callback_query(F.data == "clan:cancel")
async def h_clan_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_home(call.bot, call.message.chat.id, call.from_user.id)
    await call.answer()


# ─── СОЗДАНИЕ КЛАНА ───────────────────────────────────────────────────────────
@router.callback_query(F.data == "clan:create")
async def h_clan_create(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if await get_player_clan_id(uid):
        return await call.answer("Ты уже в клане!", show_alert=True)
    player = await get_player(uid)
    if player["coins"] < CLAN_CREATE_COST:
        return await call.answer(f"Нужно {CLAN_CREATE_COST}{COIN_ICON}!", show_alert=True)
    await state.set_state(ClanFSM.cr_photo)
    await state.set_data({})
    await call.message.answer("0) Отправьте фото клана:", reply_markup=ikb([[("❌ Отмена", "clan:cancel")]]))
    await call.answer()


@router.message(ClanFSM.cr_photo)
async def h_clan_cr_photo(message: Message, state: FSMContext):
    if not message.photo:
        return await message.answer("Нужно отправить именно фото. Попробуй ещё раз:")
    await state.update_data(photo=message.photo[-1].file_id)
    await state.set_state(ClanFSM.cr_name)
    await message.answer("1) Введите название клана:")


@router.message(ClanFSM.cr_name)
async def h_clan_cr_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        return await message.answer("Название не может быть пустым. Введите ещё раз:")
    await state.update_data(name=name)
    await state.set_state(ClanFSM.cr_desc)
    await message.answer("2) Введите описание клана:")


@router.message(ClanFSM.cr_desc)
async def h_clan_cr_desc(message: Message, state: FSMContext):
    desc = (message.text or "").strip()
    uid = message.from_user.id
    data = await state.get_data()
    await state.clear()
    if await get_player_clan_id(uid):
        return await message.answer("Ты уже в клане!")
    if not await spend_currency(uid, COIN_CURRENCY_ID, CLAN_CREATE_COST):
        return await message.answer(f"Не хватает {CLAN_CREATE_COST}{COIN_ICON}!")
    await create_clan(uid, data["name"], data.get("photo"), desc)
    await message.answer("✅ Клан создан!")
    await send_clan_home(message.bot, message.chat.id, uid)


# ─── ПОИСК / ВСТУПЛЕНИЕ ──────────────────────────────────────────────────────
async def send_clan_find(bot, chat_id, user_id, index):
    my = await get_player_clan_id(user_id)
    clans = [c for c in await list_clans() if c["id"] != my]
    if not clans:
        return await bot.send_message(chat_id, "Пока нет кланов для вступления.",
                                      reply_markup=ikb([[("⬅️ Назад", "clan:home")]]))
    index %= len(clans)
    clan = clans[index]
    count = await clan_member_count(clan["id"])
    mode = "по заявкам" if clan["entry_mode"] == "requests" else "открыто"
    lines = [clan["name"], f"👥 {count}/{CLAN_MAX_MEMBERS}", f"Вход: {mode}", "", clan["description"] or "—"]
    if my:
        join_btn = ("Ты уже в клане", "clan:noop")
    elif count >= CLAN_MAX_MEMBERS:
        join_btn = ("❌ Клан заполнен", "clan:noop")
    elif clan["entry_mode"] == "requests":
        if await has_clan_request(clan["id"], user_id):
            join_btn = ("⏳ Заявка отправлена", "clan:noop")
        else:
            join_btn = ("📨 Подать заявку", f"clan:join:{clan['id']}")
    else:
        join_btn = ("✅ Вступить", f"clan:join:{clan['id']}")
    rows = [[join_btn]]
    if len(clans) > 1:
        rows.append([("⏪ Назад", f"clan:find:{index - 1}"), ("⏩ Вперёд", f"clan:find:{index + 1}")])
    rows.append([("⬅️ Назад", "clan:home")])
    if clan["photo"]:
        await bot.send_photo(chat_id, clan["photo"], caption="\n".join(lines), reply_markup=ikb(rows))
    else:
        await bot.send_message(chat_id, "\n".join(lines), reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("clan:find:"))
async def h_clan_find(call: CallbackQuery):
    idx = int(call.data.split(":")[2])
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_find(call.bot, call.message.chat.id, call.from_user.id, idx)
    await call.answer()


@router.callback_query(F.data.startswith("clan:join:"))
async def h_clan_join(call: CallbackQuery):
    cid = int(call.data.split(":")[2])
    uid = call.from_user.id
    if await get_player_clan_id(uid):
        return await call.answer("Ты уже в клане!", show_alert=True)
    clan = await get_clan(cid)
    if not clan:
        return await call.answer("Клан не найден", show_alert=True)
    if await clan_member_count(cid) >= CLAN_MAX_MEMBERS:
        return await call.answer("Клан заполнен", show_alert=True)
    try:
        await call.message.delete()
    except Exception:
        pass
    if clan["entry_mode"] == "requests":
        await add_clan_request(cid, uid)
        await call.answer("Заявка отправлена!", show_alert=True)
        await send_clan_find(call.bot, call.message.chat.id, uid, 0)
    else:
        await add_clan_member(cid, uid)
        await call.answer("Ты вступил в клан!")
        await send_clan_home(call.bot, call.message.chat.id, uid)


@router.callback_query(F.data == "clan:leave")
async def h_clan_leave(call: CallbackQuery):
    uid = call.from_user.id
    clan = await get_player_clan(uid)
    if not clan:
        return await call.answer()
    if clan["owner_id"] == uid:
        return await call.answer("Лидер не может покинуть клан.", show_alert=True)
    await remove_clan_member(clan["id"], uid)
    await call.answer("Ты покинул клан")
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_home(call.bot, call.message.chat.id, uid)


# ─── НАСТРОЙКИ КЛАНА (лидер) ─────────────────────────────────────────────────
def _owner_only(clan, user_id):
    return clan and clan["owner_id"] == user_id


async def send_clan_settings(bot, chat_id, user_id):
    clan = await get_player_clan(user_id)
    if not _owner_only(clan, user_id):
        return await bot.send_message(chat_id, "Только лидер клана может настраивать клан.",
                                      reply_markup=ikb([[("⬅️ Назад", "clan:home")]]))
    mode = "по заявкам" if clan["entry_mode"] == "requests" else "открыто"
    reqs = await clan_requests_list(clan["id"])
    rows = [[(f"🔁 Вход: {mode}", "clan:entry")],
            [(f"📨 Заявки ({len(reqs)})", "clan:reqs")],
            [("✏️ Изменить описание", "clan:editdesc")],
            [(f"✏️ Изменить название ({CLAN_RENAME_COST}💰)", "clan:editname")],
            [("👢 Кикнуть игрока", "clan:kick")],
            [("⬅️ Назад", "clan:home")]]
    await bot.send_message(chat_id, "⚙️ Настройки клана", reply_markup=ikb(rows))


@router.callback_query(F.data == "clan:settings")
async def h_clan_settings(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_settings(call.bot, call.message.chat.id, call.from_user.id)
    await call.answer()


@router.callback_query(F.data == "clan:entry")
async def h_clan_entry(call: CallbackQuery):
    clan = await get_player_clan(call.from_user.id)
    if not _owner_only(clan, call.from_user.id):
        return await call.answer()
    await update_clan(clan["id"], entry_mode="requests" if clan["entry_mode"] == "open" else "open")
    await call.answer("Режим входа изменён")
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_settings(call.bot, call.message.chat.id, call.from_user.id)


async def send_clan_reqs(bot, chat_id, clan):
    reqs = await clan_requests_list(clan["id"])
    if not reqs:
        return await bot.send_message(chat_id, "Заявок нет.", reply_markup=ikb([[("⬅️ Назад", "clan:settings")]]))
    rows = []
    for r in reqs:
        rows.append([(f"✅ {_uname(r)}", f"clan:reqok:{r['user_id']}"), ("❌", f"clan:reqno:{r['user_id']}")])
    rows.append([("⬅️ Назад", "clan:settings")])
    await bot.send_message(chat_id, "📨 Заявки на вступление:", reply_markup=ikb(rows))


@router.callback_query(F.data == "clan:reqs")
async def h_clan_reqs(call: CallbackQuery):
    clan = await get_player_clan(call.from_user.id)
    if not _owner_only(clan, call.from_user.id):
        return await call.answer()
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_reqs(call.bot, call.message.chat.id, clan)
    await call.answer()


@router.callback_query(F.data.startswith("clan:reqok:"))
async def h_clan_reqok(call: CallbackQuery):
    clan = await get_player_clan(call.from_user.id)
    if not _owner_only(clan, call.from_user.id):
        return await call.answer()
    target = int(call.data.split(":")[2])
    if await get_player_clan_id(target):
        await remove_clan_request(clan["id"], target)
        await call.answer("Игрок уже в клане", show_alert=True)
    elif await clan_member_count(clan["id"]) >= CLAN_MAX_MEMBERS:
        await call.answer("Клан заполнен", show_alert=True)
    else:
        await add_clan_member(clan["id"], target)
        await call.answer("Принят")
        try:
            await call.bot.send_message(target, f"✅ Тебя приняли в клан «{clan['name']}»!")
        except Exception:
            pass
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_reqs(call.bot, call.message.chat.id, clan)


@router.callback_query(F.data.startswith("clan:reqno:"))
async def h_clan_reqno(call: CallbackQuery):
    clan = await get_player_clan(call.from_user.id)
    if not _owner_only(clan, call.from_user.id):
        return await call.answer()
    await remove_clan_request(clan["id"], int(call.data.split(":")[2]))
    await call.answer("Отклонено")
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_reqs(call.bot, call.message.chat.id, clan)


@router.callback_query(F.data == "clan:editdesc")
async def h_clan_editdesc(call: CallbackQuery, state: FSMContext):
    clan = await get_player_clan(call.from_user.id)
    if not _owner_only(clan, call.from_user.id):
        return await call.answer()
    await state.set_state(ClanFSM.edit_desc)
    await call.message.answer("Введите новое описание клана:", reply_markup=ikb([[("❌ Отмена", "clan:cancel")]]))
    await call.answer()


@router.message(ClanFSM.edit_desc)
async def h_clan_editdesc_in(message: Message, state: FSMContext):
    desc = (message.text or "").strip()
    clan = await get_player_clan(message.from_user.id)
    await state.clear()
    if _owner_only(clan, message.from_user.id):
        await update_clan(clan["id"], description=desc)
        await message.answer("✅ Описание обновлено.")
    await send_clan_home(message.bot, message.chat.id, message.from_user.id)


@router.callback_query(F.data == "clan:editname")
async def h_clan_editname(call: CallbackQuery, state: FSMContext):
    clan = await get_player_clan(call.from_user.id)
    if not _owner_only(clan, call.from_user.id):
        return await call.answer()
    player = await get_player(call.from_user.id)
    if player["coins"] < CLAN_RENAME_COST:
        return await call.answer(f"Нужно {CLAN_RENAME_COST}{COIN_ICON}!", show_alert=True)
    await state.set_state(ClanFSM.edit_name)
    await call.message.answer(f"Введите новое название клана ({CLAN_RENAME_COST}{COIN_ICON}):",
                              reply_markup=ikb([[("❌ Отмена", "clan:cancel")]]))
    await call.answer()


@router.message(ClanFSM.edit_name)
async def h_clan_editname_in(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        return await message.answer("Название не может быть пустым. Введите ещё раз:")
    clan = await get_player_clan(message.from_user.id)
    await state.clear()
    if not _owner_only(clan, message.from_user.id):
        return await send_clan_home(message.bot, message.chat.id, message.from_user.id)
    if not await spend_currency(message.from_user.id, COIN_CURRENCY_ID, CLAN_RENAME_COST):
        await message.answer(f"Не хватает {CLAN_RENAME_COST}{COIN_ICON}!")
    else:
        await update_clan(clan["id"], name=name)
        await message.answer("✅ Название обновлено.")
    await send_clan_home(message.bot, message.chat.id, message.from_user.id)


@router.callback_query(F.data == "clan:kick")
async def h_clan_kick(call: CallbackQuery):
    clan = await get_player_clan(call.from_user.id)
    if not _owner_only(clan, call.from_user.id):
        return await call.answer()
    members = [m for m in await clan_members_list(clan["id"]) if m["user_id"] != clan["owner_id"]]
    if not members:
        return await call.answer("Некого кикать", show_alert=True)
    rows = [[(f"👢 {_uname(m)}", f"clan:kickdo:{m['user_id']}")] for m in members]
    rows.append([("⬅️ Назад", "clan:settings")])
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.bot.send_message(call.message.chat.id, "Выбери кого кикнуть:", reply_markup=ikb(rows))
    await call.answer()


@router.callback_query(F.data.startswith("clan:kickdo:"))
async def h_clan_kickdo(call: CallbackQuery):
    clan = await get_player_clan(call.from_user.id)
    if not _owner_only(clan, call.from_user.id):
        return await call.answer()
    target = int(call.data.split(":")[2])
    if target == clan["owner_id"]:
        return await call.answer()
    await remove_clan_member(clan["id"], target)
    await call.answer("Игрок кикнут")
    try:
        await call.bot.send_message(target, f"Тебя исключили из клана «{clan['name']}».")
    except Exception:
        pass
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_settings(call.bot, call.message.chat.id, call.from_user.id)


# ─── НАСТРОЙКА БОССА ─────────────────────────────────────────────────────────
async def send_clan_boss(bot, chat_id, user_id):
    clan = await get_player_clan(user_id)
    if not clan:
        return await bot.send_message(chat_id, "Ты не в клане.", reply_markup=ikb([[("⬅️ Назад", "clan:home")]]))
    is_owner = clan["owner_id"] == user_id
    if not clan["boss_id"]:
        if is_owner:
            await bot.send_message(chat_id, "Босс ещё не выбран.\nВыбор босса навсегда!",
                                   reply_markup=ikb([[("👹 Выбрать босса", "clan:bosspick:0")],
                                                     [("⬅️ Назад", "clan:home")]]))
        else:
            await bot.send_message(chat_id, "Босс ещё не выбран. Его выбирает лидер клана.",
                                   reply_markup=ikb([[("⬅️ Назад", "clan:home")]]))
        return
    boss = await get_boss(clan["boss_id"])
    if not boss:
        return await bot.send_message(chat_id, "Босс недоступен.", reply_markup=ikb([[("⬅️ Назад", "clan:home")]]))
    rar = await get_rarity(boss["rarity_id"]) if boss["rarity_id"] else None
    name = display_unit_name(boss["name"], rar["icon"] if rar else "")
    items = await clan_boss_items_rows(clan["id"])
    lines = [f"🟩{name}🟩", f"❤️{clan['boss_hp']}/{boss['hp']}", f"⚔️{boss['dmg_min']}-{boss['dmg_max']}"]
    for p in json.loads(boss["perks"] or "[]"):
        lines.append(perk_label(p))
    lines.append(f"🎒 предметов в запасе: {len(items)}")
    rows = [[("🎁 Выдать предмет боссу", "clan:bossgive:0")],
            [(f"❤️ Восстановить {CLAN_BOSS_HEAL} ({CLAN_BOSS_HEAL_COST}💰)", "clan:bossheal")],
            [("⬅️ Назад", "clan:home")]]
    if boss["photo"]:
        await bot.send_photo(chat_id, boss["photo"], caption="\n".join(lines), reply_markup=ikb(rows))
    else:
        await bot.send_message(chat_id, "\n".join(lines), reply_markup=ikb(rows))


@router.callback_query(F.data == "clan:boss")
async def h_clan_boss(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_boss(call.bot, call.message.chat.id, call.from_user.id)
    await call.answer()


async def send_boss_pick(bot, chat_id, user_id, index):
    clan = await get_player_clan(user_id)
    if not _owner_only(clan, user_id):
        return await bot.send_message(chat_id, "Только лидер выбирает босса.",
                                      reply_markup=ikb([[("⬅️ Назад", "clan:boss")]]))
    if clan["boss_id"]:
        return await bot.send_message(chat_id, "Босс уже выбран.", reply_markup=ikb([[("⬅️ Назад", "clan:boss")]]))
    bosses = await list_bosses()
    if not bosses:
        return await bot.send_message(chat_id, "Боссы ещё не созданы администраторами.",
                                      reply_markup=ikb([[("⬅️ Назад", "clan:home")]]))
    index %= len(bosses)
    b = bosses[index]
    rar = await get_rarity(b["rarity_id"]) if b["rarity_id"] else None
    name = display_unit_name(b["name"], rar["icon"] if rar else "")
    lines = [name, f"⚔️{b['dmg_min']}-{b['dmg_max']}", f"❤️{b['hp']}"]
    for p in json.loads(b["perks"] or "[]"):
        lines.append(perk_label(p))
    rows = [[("✅ Выбрать этого босса", f"clan:bosssel:{b['id']}")]]
    if len(bosses) > 1:
        rows.append([("⏪ Назад", f"clan:bosspick:{index - 1}"), ("⏩ Вперёд", f"clan:bosspick:{index + 1}")])
    rows.append([("⬅️ Назад", "clan:boss")])
    if b["photo"]:
        await bot.send_photo(chat_id, b["photo"], caption="\n".join(lines), reply_markup=ikb(rows))
    else:
        await bot.send_message(chat_id, "\n".join(lines), reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("clan:bosspick:"))
async def h_clan_bosspick(call: CallbackQuery):
    idx = int(call.data.split(":")[2])
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_boss_pick(call.bot, call.message.chat.id, call.from_user.id, idx)
    await call.answer()


@router.callback_query(F.data.startswith("clan:bosssel:"))
async def h_clan_bosssel(call: CallbackQuery):
    clan = await get_player_clan(call.from_user.id)
    if not _owner_only(clan, call.from_user.id):
        return await call.answer()
    if clan["boss_id"]:
        return await call.answer("Босс уже выбран", show_alert=True)
    bid = int(call.data.split(":")[2])
    boss = await get_boss(bid)
    if not boss:
        return await call.answer("Босс не найден", show_alert=True)
    await update_clan(clan["id"], boss_id=bid, boss_hp=boss["hp"])
    await call.answer("Босс выбран!")
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_boss(call.bot, call.message.chat.id, call.from_user.id)


async def send_boss_give(bot, chat_id, user_id, index):
    clan = await get_player_clan(user_id)
    if not clan or not clan["boss_id"]:
        return await bot.send_message(chat_id, "Сначала выберите босса.", reply_markup=ikb([[("⬅️ Назад", "clan:boss")]]))
    items = await list_player_items(user_id)
    if not items:
        return await bot.send_message(chat_id, "У тебя нет предметов для выдачи.",
                                      reply_markup=ikb([[("⬅️ Назад", "clan:boss")]]))
    groups, order = {}, []
    for it in items:
        k = it["id"]
        if k not in groups:
            groups[k] = {"row": it, "pi_ids": []}
            order.append(k)
        groups[k]["pi_ids"].append(it["pi_id"])
    glist = [groups[k] for k in order]
    index %= len(glist)
    g = glist[index]
    it = g["row"]
    rar = await get_rarity(it["rarity_id"]) if it["rarity_id"] else None
    name = display_unit_name(it["name"], rar["icon"] if rar else "")
    slot = it["slot"] or "🧣"
    count = len(g["pi_ids"])
    title = f"{name} x{count}" if count > 1 else name
    lines = [f"{slot}{SLOT_NAME.get(slot, '')}{slot}", title, f"⚔️+{it['dmg_add']}", f"❤️{it['hp_add']}"]
    for p in json.loads(it["perks"] or "[]"):
        lines.append(perk_label(p))
    rows = [[("🎁 Выдать боссу", f"clan:bossgivedo:{g['pi_ids'][0]}")]]
    if len(glist) > 1:
        rows.append([("⏪ Назад", f"clan:bossgive:{index - 1}"), ("⏩ Вперёд", f"clan:bossgive:{index + 1}")])
    rows.append([("⬅️ Назад", "clan:boss")])
    if it["photo"]:
        await bot.send_photo(chat_id, it["photo"], caption="\n".join(lines), reply_markup=ikb(rows))
    else:
        await bot.send_message(chat_id, "\n".join(lines), reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("clan:bossgive:"))
async def h_clan_bossgive(call: CallbackQuery):
    idx = int(call.data.split(":")[2])
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_boss_give(call.bot, call.message.chat.id, call.from_user.id, idx)
    await call.answer()


@router.callback_query(F.data.startswith("clan:bossgivedo:"))
async def h_clan_bossgivedo(call: CallbackQuery):
    clan = await get_player_clan(call.from_user.id)
    if not clan or not clan["boss_id"]:
        return await call.answer()
    pi_id = int(call.data.split(":")[2])
    cur = await _db.execute("SELECT item_id FROM player_items WHERE id=? AND user_id=?", (pi_id, call.from_user.id))
    row = await cur.fetchone()
    if not row:
        return await call.answer("Предмет недоступен", show_alert=True)
    await delete_player_item(pi_id)
    await add_clan_boss_item(clan["id"], row["item_id"])
    await call.answer("Предмет передан боссу!")
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_boss_give(call.bot, call.message.chat.id, call.from_user.id, 0)


@router.callback_query(F.data == "clan:bossheal")
async def h_clan_bossheal(call: CallbackQuery):
    clan = await get_player_clan(call.from_user.id)
    if not clan or not clan["boss_id"]:
        return await call.answer()
    boss = await get_boss(clan["boss_id"])
    if not boss:
        return await call.answer()
    if clan["boss_hp"] >= boss["hp"]:
        return await call.answer("HP босса уже максимально", show_alert=True)
    if not await spend_currency(call.from_user.id, COIN_CURRENCY_ID, CLAN_BOSS_HEAL_COST):
        return await call.answer(f"Нужно {CLAN_BOSS_HEAL_COST}{COIN_ICON}!", show_alert=True)
    await update_clan(clan["id"], boss_hp=min(boss["hp"], clan["boss_hp"] + CLAN_BOSS_HEAL))
    await call.answer(f"+{CLAN_BOSS_HEAL}❤️ боссу")
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_clan_boss(call.bot, call.message.chat.id, call.from_user.id)


# ─── КЛАНОВЫЕ БОИ ────────────────────────────────────────────────────────────
@router.callback_query(F.data == "clan:battles")
async def h_clan_battles(call: CallbackQuery):
    if not await get_player_clan_id(call.from_user.id):
        return await call.answer()
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.bot.send_message(call.message.chat.id, "⚔️ Клановые бои\nВыберите противника:",
                                reply_markup=ikb([[("🎲 Случайный босс", "clan:bfrand")],
                                                  [("📋 Выбрать босса", "clan:bfpick:0")],
                                                  [("⬅️ Назад", "clan:home")]]))
    await call.answer()


async def _start_boss_battle(call, clan):
    uid = call.from_user.id
    if any(q["user_id"] == uid for q in _queue) or _in_battle(uid):
        return await call.answer("Ты уже в бою или подборе!", show_alert=True)
    pu = await _ensure_equipped(uid)
    if not pu:
        return await call.answer("Сначала получи юнита в 👤Суммон👤!", show_alert=True)
    boss_c = await build_boss_combatant(clan)
    if not boss_c:
        return await call.answer("У клана нет босса.", show_alert=True)
    player = await get_player(uid)
    unit = await build_battle_unit(pu)
    if not unit:
        return await call.answer("Ошибка юнита", show_alert=True)
    c1 = Combatant(1, display_name(player), unit, is_bot_battle=True)
    await call.answer()
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.bot.send_message(call.message.chat.id, f"⚔️ Бой с боссом клана [{clan['name']}] через 3 секунды...")
    await asyncio.sleep(3)
    sides = {1: {"user_id": uid, "chat_id": call.message.chat.id, "msg_id": None,
                 "pre_cups": player["cups"], "diff_mult": 1.0}}
    await _start_session(call.bot, Battle(c1, boss_c, is_bot=True), sides, True, boss_clan_id=clan["id"])


@router.callback_query(F.data == "clan:bfrand")
async def h_clan_bfrand(call: CallbackQuery):
    my = await get_player_clan_id(call.from_user.id)
    candidates = [c for c in await list_clans() if c["boss_id"] and c["id"] != my]
    if not candidates:
        return await call.answer("Нет доступных боссов других кланов.", show_alert=True)
    await _start_boss_battle(call, random.choice(candidates))


async def send_bf_pick(bot, chat_id, user_id, index):
    my = await get_player_clan_id(user_id)
    clans = [c for c in await list_clans() if c["boss_id"] and c["id"] != my]
    if not clans:
        return await bot.send_message(chat_id, "Нет доступных боссов других кланов.",
                                      reply_markup=ikb([[("⬅️ Назад", "clan:battles")]]))
    index %= len(clans)
    clan = clans[index]
    boss = await get_boss(clan["boss_id"])
    if not boss:
        return await bot.send_message(chat_id, "Босс недоступен.", reply_markup=ikb([[("⬅️ Назад", "clan:battles")]]))
    rar = await get_rarity(boss["rarity_id"]) if boss["rarity_id"] else None
    name = display_unit_name(boss["name"], rar["icon"] if rar else "")
    lines = [clan["name"], f"🟩{name}🟩", f"❤️{clan['boss_hp']}/{boss['hp']}", f"⚔️{boss['dmg_min']}-{boss['dmg_max']}"]
    for p in json.loads(boss["perks"] or "[]"):
        lines.append(perk_label(p))
    rows = [[("⚔️ Атаковать", f"clan:bfattack:{clan['id']}")]]
    if len(clans) > 1:
        rows.append([("⏪ Назад", f"clan:bfpick:{index - 1}"), ("⏩ Вперёд", f"clan:bfpick:{index + 1}")])
    rows.append([("🚪 Выйти", "clan:battles")])
    if boss["photo"]:
        await bot.send_photo(chat_id, boss["photo"], caption="\n".join(lines), reply_markup=ikb(rows))
    else:
        await bot.send_message(chat_id, "\n".join(lines), reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("clan:bfpick:"))
async def h_clan_bfpick(call: CallbackQuery):
    idx = int(call.data.split(":")[2])
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_bf_pick(call.bot, call.message.chat.id, call.from_user.id, idx)
    await call.answer()


@router.callback_query(F.data.startswith("clan:bfattack:"))
async def h_clan_bfattack(call: CallbackQuery):
    cid = int(call.data.split(":")[2])
    clan = await get_clan(cid)
    if not clan or not clan["boss_id"]:
        return await call.answer("Босс недоступен", show_alert=True)
    if clan["id"] == await get_player_clan_id(call.from_user.id):
        return await call.answer("Нельзя атаковать босса своего клана", show_alert=True)
    await _start_boss_battle(call, clan)


# ============================== /donate ==============================
class DonateFSM(StatesGroup):
    pass   # покупка через invoice, FSM не нужен


DON_SEP = "===================="


async def _show_donate_menu(bot, chat_id, user_id):
    """Главная страница доната."""
    is_child = bool(PARENT_BOT_USERNAME)
    dn  = await get_player_donations(user_id)
    sub = await active_sub(user_id)

    lines = ["💎Донат💎", DON_SEP]
    rows  = []

    if sub == "ultra":
        lines.append("⭐️Ultra: скидка 50% уже учтена⭐️")
        lines.append(DON_SEP)

    if is_child:
        # дочерний бот: базовый донат покупается только в основном боте
        lines.append("Базовый донат (👑VIP, 💰x2 Монеты, 🎲x2 Удача)")
        lines.append("покупается в основном боте и действует")
        lines.append("сразу во всех твоих ботах.")
        lines.append(DON_SEP)
        rows.append([("✉️ Перейти в основной бот", f"https://t.me/{PARENT_BOT_USERNAME}")])
    else:
        vip_p = await _donate_price(DONATE_VIP_STARS,     user_id)
        x2c_p = await _donate_price(DONATE_X2COINS_STARS, user_id)
        x2l_p = await _donate_price(DONATE_X2LUCK_STARS,  user_id)

        # 👑 VIP
        lines += ["👑VIP👑", "• +30% ко всем наградам за бои",
                  "• x1.5 удача на суммоны и крейты"]
        if "vip" in dn:
            lines.append("✅ Уже куплено")
        else:
            lines.append(f"💎 Цена: {vip_p}⭐️")
            rows.append([(f"👑 Купить VIP — {vip_p}⭐️", "don:buy:vip")])
        lines.append(DON_SEP)

        # 💰 x2 монеты
        lines += ["💰x2 Монеты💰", "• x2 валюты за бои (навсегда)"]
        if "x2coins" in dn:
            lines.append("✅ Уже куплено")
        else:
            lines.append(f"💎 Цена: {x2c_p}⭐️")
            rows.append([(f"💰 Купить x2 Монеты — {x2c_p}⭐️", "don:buy:x2coins")])
        lines.append(DON_SEP)

        # 🎲 x2 удача
        lines += ["🎲x2 Удача🎲", "• x2 удача в суммонах и крейтах"]
        if "x2luck" in dn:
            lines.append("✅ Уже куплено")
        else:
            lines.append(f"💎 Цена: {x2l_p}⭐️")
            rows.append([(f"🎲 Купить x2 Удача — {x2l_p}⭐️", "don:buy:x2luck")])
        lines.append(DON_SEP)

    admin_dons = await list_admin_donations()
    if admin_dons:
        lines.append("🎁Спецпредложения🎁")
        for d in admin_dons:
            c = await get_currency(d["currency_id"]) if d["currency_id"] != COIN_CURRENCY_ID else None
            icon = c["icon"] if c else COIN_ICON
            lines.append(f"• {d['name']}: +{d['amount']}{icon} — {d['price_stars']}⭐️")
            if d["description"]:
                lines.append(f"  {d['description']}")
            rows.append([(f"🎁 {d['name']} — {d['price_stars']}⭐️", f"don:buy:admin:{d['id']}")])
        lines.append(DON_SEP)

    rows.append([("🚪 Закрыть", "don:close")])
    await bot.send_message(chat_id, "\n".join(lines), reply_markup=ikb(rows))


@group_router.message(Command("donate"))
async def h_donate(message: Message):
    if message.chat.type != "private":
        await save_group(message.chat.id)
    await get_or_create_player(message.from_user.id, username_of(message.from_user))
    await _show_donate_menu(message.bot, message.chat.id, message.from_user.id)


@router.callback_query(F.data.startswith("don:buy:"))
async def h_don_buy(call: CallbackQuery):
    parts    = call.data.split(":")
    don_type = parts[2]
    uid      = call.from_user.id

    if don_type in ("vip", "x2coins", "x2luck"):
        if await has_donation(uid, don_type):
            return await call.answer("Уже куплено!", show_alert=True)
        base_prices = {"vip": DONATE_VIP_STARS, "x2coins": DONATE_X2COINS_STARS, "x2luck": DONATE_X2LUCK_STARS}
        labels      = {"vip": "VIP", "x2coins": "x2 Монеты", "x2luck": "x2 Удача"}
        descs       = {
            "vip":     "+30% ко всем наградам и x1.5 удача на суммоны/крейты (навсегда)",
            "x2coins": "x2 ко всем наградам монет/валют за бои (навсегда)",
            "x2luck":  "x2 удача в суммонах и крейтах (навсегда)",
        }
        price = await _donate_price(base_prices[don_type], uid)
        await call.bot.send_invoice(
            chat_id=call.message.chat.id,
            title=labels[don_type],
            description=descs[don_type],
            payload=f"don_{don_type}",
            currency="XTR",
            prices=[LabeledPrice(label=labels[don_type], amount=price)],
        )
    elif don_type == "admin" and len(parts) >= 4:
        did = int(parts[3])
        d   = await get_admin_donation(did)
        if not d:
            return await call.answer("Не найдено", show_alert=True)
        await call.bot.send_invoice(
            chat_id=call.message.chat.id,
            title=d["name"],
            description=d["description"] or d["name"],
            payload=f"don_admin_{did}",
            currency="XTR",
            prices=[LabeledPrice(label=d["name"], amount=d["price_stars"])],
        )
    await call.answer()


@router.callback_query(F.data == "don:close")
async def h_don_close(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data == "don:noop")
async def h_don_noop(call: CallbackQuery):
    await call.answer("Уже куплено!")


# ─── Оплата Stars ────────────────────────────────────────────────────────────
@router.pre_checkout_query()
async def h_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def h_payment_success(message: Message):
    payload = message.successful_payment.invoice_payload
    uid     = message.from_user.id
    name    = username_of(message.from_user)

    if payload.startswith("don_admin_"):
        did = int(payload.split("_")[2])
        d   = await get_admin_donation(did)
        if d:
            c    = await get_currency(d["currency_id"]) if d["currency_id"] != COIN_CURRENCY_ID else None
            icon = c["icon"] if c else COIN_ICON
            await give_currency(uid, d["currency_id"], d["amount"])
            await message.answer(f"✅Оплата прошла✅\n{DON_SEP}\n🎁 {d['name']}\n+{d['amount']}{icon}")
            await notify_owner_purchase(message.bot, name, f"{d['name']} ({d['price_stars']}⭐️)")
        return

    if payload == "don_vip":
        await give_donation(uid, "vip")
        await message.answer("✅VIP активирован✅\n" + DON_SEP +
                             "\n👑 +30% ко всем наградам\n👑 x1.5 удача на суммоны и крейты")
        await notify_owner_purchase(message.bot, name, f"VIP ({DONATE_VIP_STARS}⭐️)")

    elif payload == "don_x2coins":
        await give_donation(uid, "x2coins")
        await message.answer("✅x2 Монеты активировано✅\n" + DON_SEP +
                             "\n💰 Валюта за бои теперь удваивается")
        await notify_owner_purchase(message.bot, name, f"x2 монеты ({DONATE_X2COINS_STARS}⭐️)")

    elif payload == "don_x2luck":
        await give_donation(uid, "x2luck")
        await message.answer("✅x2 Удача активирована✅\n" + DON_SEP +
                             "\n🎲 Удача в суммонах и крейтах удвоена")
        await notify_owner_purchase(message.bot, name, f"x2 удача ({DONATE_X2LUCK_STARS}⭐️)")

    elif payload.startswith("sub_"):
        parts    = payload.split("_")
        sub_type = parts[1]
        days     = int(parts[2])
        if sub_type in SUB_PLANS:
            await set_mybots_sub(uid, sub_type, days)
            plan = SUB_PLANS[sub_type]
            await message.answer(f"✅Подписка оформлена✅\n{DON_SEP}\n"
                                 f"{SUB_ICON.get(sub_type, '💎')} {plan['label']} — {days} дн.")
            if "support" in plan:
                await message.answer(f"💬 Чат с поддержкой:\n{plan['support']}")
            await notify_owner_purchase(message.bot, name, f"Подписка {plan['label']} {days} дн.")
            await _maybe_sync_children()   # запустить ботов, ставшие активными
            await _send_mybots_cabinet(message.bot, message.chat.id, uid)


# ============================== /mybots ==============================
class MyBotsFSM(StatesGroup):
    attach_name    = State()
    attach_token   = State()
    rename_select  = State()
    rename_input   = State()
    sub_days       = State()
    export_select  = State()
    import_select  = State()
    import_file    = State()


_mybots_pending_sub: dict[int, str] = {}  # user_id -> sub_type


@router.message(Command("mybots"))
async def h_mybots(message: Message):
    await get_or_create_player(message.from_user.id, username_of(message.from_user))
    await _send_mybots_cabinet(message.bot, message.chat.id, message.from_user.id)


async def _send_mybots_cabinet(bot, chat_id, user_id):
    sub_type = await active_sub(user_id)          # минимум — "free"
    plan     = SUB_PLANS[sub_type]
    row      = await get_mybots_sub(user_id)
    now      = int(time.time())
    bots     = await get_mybots_bots(user_id)
    icon     = SUB_ICON.get(sub_type, "💎")

    lines = ["🤖Личный кабинет🤖", DON_SEP]
    if sub_type == FREE_SUB:
        lines.append(f"{icon} Тариф: {plan['label']}")
        lines.append("⏳ Действует: навсегда")
    else:
        left  = (row["expires_at"] - now) if row else 0
        days  = left // 86400
        hours = (left % 86400) // 3600
        lines.append(f"{icon} Подписка: {plan['label']}")
        lines.append(f"⏳ Осталось: {days} дн. {hours} ч.")
    lines.append(f"🤖 Привязано: {len(bots)}/{plan['max_bots']}")
    if plan.get("ads"):
        lines.append("📢 В твоих ботах есть реклама канала")
        lines.append("   (убери её на платном тарифе)")
    lines.append(DON_SEP)

    rows = [
        [("🔗 Привязать бота",            "mb:attach")],
        [("💎 Оформить подписку",          "mb:sub")],
        [("⚙️ Управление ботами",          "mb:manage")],
        [(f"🎁 Пробный период (Basic {TRIAL_DAYS} дня)", "mb:trial")],
        [("🚪 Закрыть",                    "mb:close")],
    ]
    await bot.send_message(chat_id, "\n".join(lines), reply_markup=ikb(rows))


@router.callback_query(F.data == "mb:close")
async def h_mb_close(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data == "mb:home")
async def h_mb_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.delete()
    except Exception:
        pass
    await _send_mybots_cabinet(call.bot, call.message.chat.id, call.from_user.id)
    await call.answer()


@router.callback_query(F.data == "mb:cancel")
async def h_mb_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.delete()
    except Exception:
        pass
    await _send_mybots_cabinet(call.bot, call.message.chat.id, call.from_user.id)
    await call.answer()


# ─── Пробный период ──────────────────────────────────────────────────────────
@router.callback_query(F.data == "mb:trial")
async def h_mb_trial(call: CallbackQuery):
    uid = call.from_user.id
    cur = await _db.execute("SELECT 1 FROM meta WHERE key=?", (f"trial_used_{uid}",))
    if await cur.fetchone():
        return await call.answer("Пробный период уже использован!", show_alert=True)
    await set_mybots_sub(uid, TRIAL_SUB, TRIAL_DAYS)
    await _db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?,?)", (f"trial_used_{uid}", "1"))
    await _db.commit()
    await call.answer(f"✅ Пробная подписка Basic на {TRIAL_DAYS} дня выдана!", show_alert=True)
    await _maybe_sync_children()
    try:
        await call.message.delete()
    except Exception:
        pass
    await _send_mybots_cabinet(call.bot, call.message.chat.id, uid)


# ─── Оформить подписку ───────────────────────────────────────────────────────
SUB_ICON = {"free": "🆓", "basic": "🥉", "pro": "🥈", "ultra": "🥇"}


@router.callback_query(F.data == "mb:sub")
async def h_mb_sub(call: CallbackQuery):
    lines = ["💎Оформить подписку💎", DON_SEP,
             "🆓 Free (по умолчанию, навсегда):",
             "• 1 бот", "• с рекламой канала", DON_SEP]
    rows  = []
    for key, plan in SUB_PLANS.items():
        if key == FREE_SUB:
            continue   # Free — тариф по умолчанию, его не покупают
        icon = SUB_ICON.get(key, "💎")
        lines.append(f"{icon}{plan['label']}{icon} — {plan['stars_per_day']}⭐️/день")
        lines.append(f"• до {plan['max_bots']} привязанных ботов")
        lines.append("• 🚫 удаление рекламы")
        if plan["can_donate"]:
            lines.append("• донат на валюты в своих ботах")
        if plan.get("discount"):
            lines.append(f"• -{int(plan['discount'] * 100)}% на донат в основном боте")
        if "support" in plan:
            lines.append("• чат с поддержкой")
        lines.append(DON_SEP)
        rows.append([(f"{icon} {plan['label']} — {plan['stars_per_day']}⭐️/день", f"mb:sub:pick:{key}")])
    rows.append([("⬅️ Назад", "mb:home")])
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.bot.send_message(call.message.chat.id, "\n".join(lines), reply_markup=ikb(rows))
    await call.answer()


@router.callback_query(F.data.startswith("mb:sub:pick:"))
async def h_mb_sub_pick(call: CallbackQuery, state: FSMContext):
    sub_key = call.data.split(":")[3]
    if sub_key not in SUB_PLANS or sub_key == FREE_SUB:
        return await call.answer()
    _mybots_pending_sub[call.from_user.id] = sub_key
    await state.set_state(MyBotsFSM.sub_days)
    await call.message.answer(
        f"Введите количество дней подписки {SUB_PLANS[sub_key]['label']} (1-365):",
        reply_markup=ikb([[("❌ Отмена", "mb:cancel")]]))
    await call.answer()


@router.message(MyBotsFSM.sub_days)
async def h_mb_sub_days(message: Message, state: FSMContext):
    try:
        days = int(message.text.strip())
        if not (1 <= days <= 365):
            raise ValueError
    except (ValueError, AttributeError):
        return await message.answer("Введи число от 1 до 365:")
    uid     = message.from_user.id
    sub_key = _mybots_pending_sub.pop(uid, None)
    if not sub_key:
        await state.clear()
        return await message.answer("Ошибка. Начни сначала (/mybots).")
    await state.clear()
    plan        = SUB_PLANS[sub_key]
    total_stars = plan["stars_per_day"] * days
    await message.bot.send_invoice(
        chat_id=message.chat.id,
        title=f"Подписка {plan['label']}",
        description=f"{plan['label']} на {days} дн. (до {plan['max_bots']} бот(а))",
        payload=f"sub_{sub_key}_{days}",
        currency="XTR",
        prices=[LabeledPrice(label=f"Подписка {plan['label']}", amount=total_stars)],
    )


# ─── Привязать бота ───────────────────────────────────────────────────────────
@router.callback_query(F.data == "mb:attach")
async def h_mb_attach(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    sub = await active_sub(uid)
    if not sub:
        return await call.answer("Нужна активная подписка!", show_alert=True)
    bots     = await get_mybots_bots(uid)
    max_bots = SUB_PLANS[sub]["max_bots"]
    if len(bots) >= max_bots:
        hint = " Оформи подписку для большего лимита." if sub == FREE_SUB else ""
        return await call.answer(
            f"Лимит ботов на тарифе {SUB_PLANS[sub]['label']}: {max_bots}.{hint}", show_alert=True)
    await state.set_state(MyBotsFSM.attach_name)
    await call.message.answer("Введите название бота:", reply_markup=ikb([[("❌ Отмена", "mb:cancel")]]))
    await call.answer()


@router.message(MyBotsFSM.attach_name)
async def h_mb_attach_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        return await message.answer("Название не может быть пустым:")
    await state.update_data(bot_name=name)
    await state.set_state(MyBotsFSM.attach_token)
    await message.answer("Отправьте токен бота (из @BotFather):")


@router.message(MyBotsFSM.attach_token)
async def h_mb_attach_token(message: Message, state: FSMContext):
    token = (message.text or "").strip()
    if not token or ":" not in token:
        return await message.answer("Неверный формат токена. Попробуй ещё раз:")
    data = await state.get_data()
    await state.clear()
    await add_mybot(message.from_user.id, data["bot_name"], token)
    await _maybe_sync_children()   # сразу запустить, если подписка активна
    await message.answer(f"✅ Бот «{data['bot_name']}» привязан и запущен!")
    await _send_mybots_cabinet(message.bot, message.chat.id, message.from_user.id)


# ─── Управление ботами ───────────────────────────────────────────────────────
@router.callback_query(F.data == "mb:manage")
async def h_mb_manage(call: CallbackQuery):
    uid  = call.from_user.id
    bots = await get_mybots_bots(uid)
    if not bots:
        return await call.answer("У тебя нет привязанных ботов", show_alert=True)
    lines = ["⚙️Управление ботами⚙️", DON_SEP]
    for b in bots:
        lines.append(f"🤖 {b['name']}")
    lines.append(DON_SEP)
    rows = [
        [("❌ Отвязать бота",  "mb:detach"),  ("✏️ Переименовать", "mb:rename")],
        [("📤 Экспорт БД",     "mb:export"),  ("📥 Импорт БД",     "mb:import")],
        [("⬅️ Назад",          "mb:home")],
    ]
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.bot.send_message(call.message.chat.id, "\n".join(lines), reply_markup=ikb(rows))
    await call.answer()


async def _bot_pick_kb(bots, cb_prefix, back_cb):
    rows = [[(f"🤖 {b['name']}", f"{cb_prefix}{b['id']}")] for b in bots]
    rows.append([("⬅️ Назад", back_cb)])
    return ikb(rows)


@router.callback_query(F.data == "mb:detach")
async def h_mb_detach(call: CallbackQuery):
    bots = await get_mybots_bots(call.from_user.id)
    if not bots:
        return await call.answer("Нет ботов", show_alert=True)
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.bot.send_message(call.message.chat.id, "Выберите бота для отвязки:",
                                reply_markup=await _bot_pick_kb(bots, "mb:detachdo:", "mb:manage"))
    await call.answer()


@router.callback_query(F.data.startswith("mb:detachdo:"))
async def h_mb_detachdo(call: CallbackQuery):
    bot_id = int(call.data.split(":")[2])
    b = await get_mybot(bot_id)
    if not b or b["owner_id"] != call.from_user.id:
        return await call.answer()
    await delete_mybot(bot_id)
    _kill_child(bot_id)            # остановить процесс отвязанного бота
    await call.answer("Бот отвязан")
    try:
        await call.message.delete()
    except Exception:
        pass
    await _send_mybots_cabinet(call.bot, call.message.chat.id, call.from_user.id)


@router.callback_query(F.data == "mb:rename")
async def h_mb_rename(call: CallbackQuery, state: FSMContext):
    bots = await get_mybots_bots(call.from_user.id)
    if not bots:
        return await call.answer("Нет ботов", show_alert=True)
    await state.set_state(MyBotsFSM.rename_select)
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.bot.send_message(call.message.chat.id, "Выберите бота для переименования:",
                                reply_markup=await _bot_pick_kb(bots, "mb:rensel:", "mb:manage"))
    await call.answer()


@router.callback_query(MyBotsFSM.rename_select, F.data.startswith("mb:rensel:"))
async def h_mb_rensel(call: CallbackQuery, state: FSMContext):
    bot_id = int(call.data.split(":")[2])
    b = await get_mybot(bot_id)
    if not b or b["owner_id"] != call.from_user.id:
        return await call.answer()
    await state.update_data(rename_bot_id=bot_id)
    await state.set_state(MyBotsFSM.rename_input)
    await call.message.answer(f"Введите новое имя для бота «{b['name']}»:",
                              reply_markup=ikb([[("❌ Отмена", "mb:cancel")]]))
    await call.answer()


@router.message(MyBotsFSM.rename_input)
async def h_mb_rename_in(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        return await message.answer("Имя не может быть пустым:")
    data = await state.get_data()
    await state.clear()
    await update_mybot_name(data["rename_bot_id"], name)
    await message.answer(f"✅ Бот переименован в «{name}».")
    await _send_mybots_cabinet(message.bot, message.chat.id, message.from_user.id)


@router.callback_query(F.data == "mb:export")
async def h_mb_export(call: CallbackQuery, state: FSMContext):
    bots = await get_mybots_bots(call.from_user.id)
    if not bots:
        return await call.answer("Нет ботов", show_alert=True)
    await state.set_state(MyBotsFSM.export_select)
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.bot.send_message(call.message.chat.id, "Выберите бота для экспорта БД:",
                                reply_markup=await _bot_pick_kb(bots, "mb:exportdo:", "mb:manage"))
    await call.answer()


@router.callback_query(MyBotsFSM.export_select, F.data.startswith("mb:exportdo:"))
async def h_mb_exportdo(call: CallbackQuery, state: FSMContext):
    bot_id = int(call.data.split(":")[2])
    b = await get_mybot(bot_id)
    if not b or b["owner_id"] != call.from_user.id:
        await state.clear()
        return await call.answer()
    await state.clear()
    await call.answer("Отправляю базу данных...")
    path = _child_db_path(bot_id)
    if not os.path.exists(path):
        return await call.bot.send_message(
            call.message.chat.id,
            "БД ещё не создана — бот не запускался. Активируй подписку и дождись запуска.",
            reply_markup=ikb([[("⬅️ Назад", "mb:manage")]]))
    try:
        db_file = FSInputFile(path, filename=f"bot_{bot_id}.db")
        await call.bot.send_document(call.message.chat.id, db_file,
                                     caption=f"База данных бота «{b['name']}»")
    except Exception as e:
        await call.bot.send_message(call.message.chat.id, f"Ошибка экспорта: {e}")


@router.callback_query(F.data == "mb:import")
async def h_mb_import(call: CallbackQuery, state: FSMContext):
    bots = await get_mybots_bots(call.from_user.id)
    if not bots:
        return await call.answer("Нет ботов", show_alert=True)
    await state.set_state(MyBotsFSM.import_select)
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.bot.send_message(call.message.chat.id, "Выберите бота для импорта БД:",
                                reply_markup=await _bot_pick_kb(bots, "mb:impsel:", "mb:manage"))
    await call.answer()


@router.callback_query(MyBotsFSM.import_select, F.data.startswith("mb:impsel:"))
async def h_mb_impsel(call: CallbackQuery, state: FSMContext):
    bot_id = int(call.data.split(":")[2])
    b = await get_mybot(bot_id)
    if not b or b["owner_id"] != call.from_user.id:
        await state.clear()
        return await call.answer()
    await state.update_data(import_bot_id=bot_id)
    await state.set_state(MyBotsFSM.import_file)
    await call.message.answer("Отправьте файл базы данных (.db) для импорта:",
                              reply_markup=ikb([[("❌ Отмена", "mb:cancel")]]))
    await call.answer()


@router.message(MyBotsFSM.import_file)
async def h_mb_import_file(message: Message, state: FSMContext):
    if not message.document:
        return await message.answer("Нужно отправить файл .db")
    data = await state.get_data()
    await state.clear()
    b = await get_mybot(data.get("import_bot_id", 0))
    if not b:
        return await message.answer("Бот не найден.")
    bot_id = b["id"]
    dest = _child_db_path(bot_id)
    try:
        _kill_child(bot_id)                      # остановить бот, чтобы не держал файл БД
        await asyncio.sleep(1)
        file = await message.bot.get_file(message.document.file_id)
        await message.bot.download_file(file.file_path, destination=dest)
        await _maybe_sync_children()             # снова поднять бот с новой БД
        await message.answer("✅ БД импортирована. Бот перезапущен с новыми данными.")
    except Exception as e:
        await message.answer(f"Ошибка импорта: {e}")
    await _send_mybots_cabinet(message.bot, message.chat.id, message.from_user.id)


# ============================== ВЫДАЧА ПО ID (Абьюз) ==============================
class GiveByIdFSM(StatesGroup):
    target  = State()
    kind    = State()
    select  = State()
    amount  = State()


_giveid_pending: dict[int, dict] = {}


@router.callback_query(F.data == "abz:giveid")
async def h_abz_giveid(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await state.set_state(GiveByIdFSM.target)
    await call.message.answer(
        "Введите Telegram ID или @username игрока:",
        reply_markup=ikb([[("❌ Отмена", "abz:cancel")]]))
    await call.answer()


@router.message(GiveByIdFSM.target)
async def h_giveid_target(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    raw = (message.text or "").strip().lstrip("@")
    # Попытка найти по ID
    player = None
    if raw.lstrip("-").isdigit():
        player = await get_player(int(raw))
    if not player:
        # Поиск по username
        cur = await _db.execute("SELECT * FROM players WHERE LOWER(username)=?", (raw.lower(),))
        player = await cur.fetchone()
    if not player:
        return await message.answer("Игрок не найден. Введи ID или @username ещё раз:")
    _giveid_pending[message.from_user.id] = {"target_uid": player["user_id"],
                                              "target_name": display_name(player)}
    await state.set_state(GiveByIdFSM.kind)
    target_str = f"{display_name(player)} (id{player['user_id']})"
    await message.answer(
        f"Выдать игроку {target_str}:",
        reply_markup=ikb([
            [("👤 Юнит",       "gid:k:unit"),    ("💰 Валюта",    "gid:k:currency")],
            [("🧩 Предмет",    "gid:k:item"),    ("📱 Подписка",  "gid:k:sub")],
            [("⚔️ Донат VIP",  "gid:k:don_vip"), ("🎲 x2 Удача",  "gid:k:don_x2luck")],
            [("💸 x2 Монеты",  "gid:k:don_x2coins")],
            [("❌ Отмена",     "abz:cancel")],
        ]))


@router.callback_query(GiveByIdFSM.kind, F.data.startswith("gid:k:"))
async def h_giveid_kind(call: CallbackQuery, state: FSMContext):
    kind = call.data.split(":")[2]
    adm  = call.from_user.id
    if adm not in _giveid_pending:
        return await call.answer()
    _giveid_pending[adm]["kind"] = kind
    await call.answer()
    await call.message.delete()

    if kind.startswith("don_"):
        # выдать don_type напрямую
        don_type = kind[4:]
        info = _giveid_pending.pop(adm)
        await state.clear()
        await give_donation(info["target_uid"], don_type)
        don_names = {"vip": "👑 VIP", "x2coins": "💰 x2 Монеты", "x2luck": "🎲 x2 Удача"}
        pretty = don_names.get(don_type, don_type)
        await call.bot.send_message(call.message.chat.id,
            f"✅ Донат {pretty} выдан игроку {info['target_name']}.", reply_markup=abhuz_kb())
        try:
            await call.bot.send_message(info["target_uid"], f"🎁 Тебе выдан донат: {pretty}!")
        except Exception:
            pass
        return

    if kind == "sub":
        rows = [[(f"{p['label']} ({p['stars_per_day']}⭐️/день)", f"gid:sel:{key}")] for key, p in SUB_PLANS.items()]
        rows.append([("❌ Отмена", "abz:cancel")])
        await state.set_state(GiveByIdFSM.select)
        await call.bot.send_message(call.message.chat.id, "Выберите тип подписки:", reply_markup=ikb(rows))
        return

    if kind == "currency":
        curs = [(COIN_ICON, COIN_CURRENCY_ID)] + [(c["icon"], c["id"]) for c in await list_currencies()]
        rows = [[(f"{icon} {'Монеты' if cid == COIN_CURRENCY_ID else f'id{cid}'}", f"gid:sel:{cid}")]
                for icon, cid in curs]
        rows.append([("❌ Отмена", "abz:cancel")])
        await state.set_state(GiveByIdFSM.select)
        await call.bot.send_message(call.message.chat.id, "Выберите валюту:", reply_markup=ikb(rows))
        return

    if kind == "unit":
        units = await list_units()
        if not units:
            _giveid_pending.pop(adm, None)
            await state.clear()
            return await call.bot.send_message(call.message.chat.id, "Нет юнитов.", reply_markup=abhuz_kb())
        rows = [[(u["name"], f"gid:sel:{u['id']}")] for u in units]
        rows.append([("❌ Отмена", "abz:cancel")])
        await state.set_state(GiveByIdFSM.select)
        await call.bot.send_message(call.message.chat.id, "Выберите юнита:", reply_markup=ikb(rows))
        return

    if kind == "item":
        items = await list_items()
        if not items:
            _giveid_pending.pop(adm, None)
            await state.clear()
            return await call.bot.send_message(call.message.chat.id, "Нет предметов.", reply_markup=abhuz_kb())
        rows = [[(f"{it['slot']}{it['name']}", f"gid:sel:{it['id']}")] for it in items]
        rows.append([("❌ Отмена", "abz:cancel")])
        await state.set_state(GiveByIdFSM.select)
        await call.bot.send_message(call.message.chat.id, "Выберите предмет:", reply_markup=ikb(rows))


@router.callback_query(GiveByIdFSM.select, F.data.startswith("gid:sel:"))
async def h_giveid_sel(call: CallbackQuery, state: FSMContext):
    sel_id = call.data.split(":")[2]
    adm    = call.from_user.id
    if adm not in _giveid_pending:
        return await call.answer()
    _giveid_pending[adm]["sel_id"] = sel_id
    await call.answer()
    await call.message.delete()
    kind = _giveid_pending[adm]["kind"]
    if kind == "sub":
        await state.set_state(GiveByIdFSM.amount)
        await call.bot.send_message(call.message.chat.id, "На сколько дней выдать подписку?",
                                    reply_markup=ikb([[("❌ Отмена", "abz:cancel")]]))
    else:
        await state.set_state(GiveByIdFSM.amount)
        label = "Сколько выдать (шт.):" if kind != "currency" else "Сколько выдать:"
        await call.bot.send_message(call.message.chat.id, label,
                                    reply_markup=ikb([[("❌ Отмена", "abz:cancel")]]))


@router.message(GiveByIdFSM.amount)
async def h_giveid_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await state.clear()
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        return await message.answer("Введи целое положительное число:")
    adm  = message.from_user.id
    info = _giveid_pending.pop(adm, None)
    await state.clear()
    if not info:
        return await message.answer("Ошибка. Начни сначала.")
    uid  = info["target_uid"]
    name = info["target_name"]
    kind = info["kind"]
    sel  = info.get("sel_id", "0")

    async def _notify(text):
        try:
            await message.bot.send_message(uid, text)
        except Exception:
            pass

    if kind == "sub":
        sub_key = sel
        if sub_key in SUB_PLANS:
            await give_sub_admin(uid, sub_key, amount)
            await _maybe_sync_children()
            await message.answer(
                f"✅ Подписка {SUB_PLANS[sub_key]['label']} на {amount} дн. выдана игроку {name}.",
                reply_markup=abhuz_kb())
            await _notify(f"📱 Тебе выдана подписка {SUB_PLANS[sub_key]['label']} на {amount} дн.!")
        else:
            await message.answer("Неизвестный тип подписки.", reply_markup=abhuz_kb())

    elif kind == "currency":
        cid = int(sel)
        await give_currency(uid, cid, amount)
        icon = COIN_ICON if cid == COIN_CURRENCY_ID else (await get_currency(cid))["icon"]
        await message.answer(f"✅ Выдано {amount}{icon} игроку {name}.", reply_markup=abhuz_kb())
        await _notify(f"🎁 Тебе выдано {amount}{icon}!")

    elif kind == "unit":
        uid_item = int(sel)
        for _ in range(amount):
            await add_player_unit(uid, uid_item)
        u = await get_unit(uid_item)
        await message.answer(f"✅ Юнит «{u['name']}» x{amount} выдан игроку {name}.", reply_markup=abhuz_kb())
        await _notify(f"🎁 Тебе выдан юнит «{u['name']}» x{amount}!")

    elif kind == "item":
        iid = int(sel)
        for _ in range(amount):
            await add_player_item(uid, iid)
        it = await get_item(iid)
        await message.answer(f"✅ Предмет «{it['name']}» x{amount} выдан игроку {name}.", reply_markup=abhuz_kb())
        await _notify(f"🎁 Тебе выдан предмет «{it['name']}» x{amount}!")


# ============================== FALLBACK (последним!) ==============================
@router.message()
async def h_fallback(message: Message, state: FSMContext):
    if await state.get_state() is not None:
        return
    await send_main_menu(message, message.from_user.id, username_of(message.from_user))


# ============================== РЕКЛАМА (в дочерних ботах) ==============================
# Активна, когда задан AD_CHANNEL (родитель ставит его дочерним ботам владельцев на Free).
# В основном боте AD_CHANNEL пуст → рекламы нет. Платный тариф → родитель убирает AD_CHANNEL.
_msg_count: dict[int, int] = {}


def ad_text():
    return ("📢Реклама📢\n" + DON_SEP +
            f"\nПодпишись на наш канал {AD_CHANNEL}!\n"
            "Новости, обновления и розыгрыши 🎁\n" + DON_SEP)


def ad_kb():
    ch = AD_CHANNEL.lstrip("@")
    return ikb([[("📲 Перейти в канал", f"https://t.me/{ch}")]])


class AdMiddleware:
    """Каждые AD_EVERY_N_MSG сообщений показывает рекламу канала (только ЛС, если задан AD_CHANNEL)."""

    async def __call__(self, handler, event, data):
        result = await handler(event, data)
        try:
            chat = getattr(event, "chat", None)
            if AD_CHANNEL and chat and chat.type == "private" and event.from_user:
                uid = event.from_user.id
                _msg_count[uid] = _msg_count.get(uid, 0) + 1
                if _msg_count[uid] % AD_EVERY_N_MSG == 0:
                    await event.bot.send_message(uid, ad_text(), reply_markup=ad_kb())
        except Exception:
            pass
        return result


async def _ad_broadcast_loop(bot):
    """Каждые AD_BROADCAST_SECONDS — реклама канала всем игрокам бота (если задан AD_CHANNEL)."""
    while True:
        await asyncio.sleep(AD_BROADCAST_SECONDS)
        if not AD_CHANNEL:
            continue
        try:
            cur = await _db.execute("SELECT user_id FROM players")
            players = await cur.fetchall()
        except Exception:
            continue
        for p in players:
            try:
                await bot.send_message(p["user_id"], ad_text(), reply_markup=ad_kb())
            except Exception:
                pass


# ============================== ДОЧЕРНИЕ БОТЫ (MyBots) ==============================
# Каждый привязанный бот запускается как ОТДЕЛЬНЫЙ процесс того же bot.py:
# свой токен + своя пустая БД. Работают, пока у владельца активна подписка.
_child_procs: dict[int, "subprocess.Popen"] = {}
_child_ads: dict[int, bool] = {}   # bot_id -> запущен ли с рекламой (для перезапуска при смене тарифа)
_MAIN_USERNAME = None


def _child_db_path(bot_id):
    base = DB_PATH[:-3] if DB_PATH.endswith(".db") else DB_PATH
    return f"{base}_child{bot_id}.db"


def _spawn_child(b, ads_on):
    """Запустить дочерний бот. ads_on=True → в нём крутится реклама основного канала."""
    bot_id = b["id"]
    existing = _child_procs.get(bot_id)
    if existing and existing.poll() is None:
        return
    env = dict(os.environ)
    env["BOT_TOKEN"]         = b["token"]
    env["DB_PATH"]           = _child_db_path(bot_id)
    env["ADMIN_IDS"]         = str(b["owner_id"])   # владелец = админ своего бота
    env["IS_CHILD"]          = "1"
    env["CHANGELOG_CHANNEL"] = ""                   # не слать апдейт-лог в основной канал
    env["AD_CHANNEL"]        = CHANGELOG_CHANNEL if ads_on else ""   # реклама — только на Free
    if _MAIN_USERNAME:
        env["PARENT_BOT_USERNAME"] = _MAIN_USERNAME
    try:
        _child_procs[bot_id] = subprocess.Popen([sys.executable, os.path.abspath(__file__)], env=env)
        _child_ads[bot_id] = ads_on
        logging.info(f"[child] запущен бот #{bot_id} «{b['name']}» (реклама: {'да' if ads_on else 'нет'})")
    except Exception as e:
        logging.warning(f"[child] не удалось запустить бот #{bot_id}: {e}")


def _kill_child(bot_id):
    proc = _child_procs.pop(bot_id, None)
    _child_ads.pop(bot_id, None)
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            logging.info(f"[child] остановлен бот #{bot_id}")
        except Exception:
            pass


def _stop_all_children():
    for bid in list(_child_procs.keys()):
        _kill_child(bid)


async def _sync_children():
    """Запустить ботов в пределах тарифа владельца, выставить рекламу по тарифу, погасить лишние."""
    cur = await _db.execute("SELECT * FROM mybots_bots ORDER BY owner_id, id")
    bots = await cur.fetchall()
    by_owner: dict = {}
    for b in bots:
        by_owner.setdefault(b["owner_id"], []).append(b)
    should: dict = {}   # bot_id -> (row, ads_on)
    for owner_id, owner_bots in by_owner.items():
        sub    = await active_sub(owner_id)          # минимум "free"
        plan   = SUB_PLANS[sub]
        ads_on = plan.get("ads", False)              # реклама в ботах владельца на Free
        for b in owner_bots[:plan["max_bots"]]:      # сверх лимита тарифа — не запускаем
            should[b["id"]] = (b, ads_on)
    # остановить те, что больше не должны работать
    for bid in list(_child_procs.keys()):
        if bid not in should:
            _kill_child(bid)
    # запустить нужные / перезапустить упавшие и при смене рекламного статуса
    for bid, (b, ads_on) in should.items():
        proc = _child_procs.get(bid)
        running = proc is not None and proc.poll() is None
        if running and _child_ads.get(bid) != ads_on:
            _kill_child(bid)                          # тариф сменился → перезапуск с новой рекламой
            running = False
        if not running:
            _spawn_child(b, ads_on)


async def _maybe_sync_children():
    """Немедленная синхронизация (после покупки/выдачи/привязки). Только в основном боте."""
    if IS_CHILD:
        return
    try:
        await _sync_children()
    except Exception as e:
        logging.warning(f"[child] sync error: {e}")


async def _child_supervisor():
    """Фоновый надзор: раз в минуту сверяет дочерние боты с подписками."""
    while True:
        await _maybe_sync_children()
        await asyncio.sleep(60)


# ============================== АПДЕЙТ-ЛОГ В КАНАЛ ==============================
async def maybe_send_changelog(bot: Bot):
    if not CHANGELOG_CHANNEL:
        return
    cur = await _db.execute("SELECT value FROM meta WHERE key='changelog_version'")
    row = await cur.fetchone()
    if row and row["value"] == CHANGELOG_VERSION:
        return
    try:
        await bot.send_message(CHANGELOG_CHANNEL, CHANGELOG_TEXT)
    except Exception as e:
        logging.warning(f"Не удалось отправить changelog в {CHANGELOG_CHANNEL}: {e}")
    await _db.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('changelog_version', ?)",
        (CHANGELOG_VERSION,))
    await _db.commit()


# ============================== ЗАПУСК ==============================
async def _main():
    logging.basicConfig(level=logging.INFO)
    await db_init()
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS пуст — админ-кнопки никому не видны. Задай ADMIN_IDS (env или в коде).")
    global _BOT
    bot = Bot(BOT_TOKEN)
    _BOT = bot
    bot.session.middleware(BoldMiddleware())   # всё жирным
    dp = Dispatcher()
    dp.message.outer_middleware(AdMiddleware())  # реклама каждые N сообщений (free-тариф)
    dp.include_router(group_router)            # /freeunit (работает и в группах) — первым
    dp.include_router(router)                  # остальное — только ЛС
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await maybe_send_changelog(bot)
        # запомнить юзернейм основного бота (для редиректа доната в дочерних ботах)
        global _MAIN_USERNAME
        try:
            me = await bot.get_me()
            _MAIN_USERNAME = me.username
        except Exception:
            pass
        # команды в меню «/»: в ЛС — start+freeunit(+donate/mybots), в группах — freeunit+donate
        priv_cmds = [
            BotCommand(command="start",    description="Открыть меню"),
            BotCommand(command="freeunit", description="Бесплатный юнит (раз в час)"),
            BotCommand(command="donate",   description="Магазин доната (⭐️ Stars)"),
        ]
        if not IS_CHILD:   # /mybots — только в основном боте (дочерние пустые)
            priv_cmds.append(BotCommand(command="mybots", description="Личный кабинет (привязка ботов)"))
        await bot.set_my_commands(priv_cmds, scope=BotCommandScopeAllPrivateChats())
        await bot.set_my_commands([
            BotCommand(command="freeunit", description="Бесплатный юнит (раз в час)"),
            BotCommand(command="donate",   description="Магазин доната (⭐️ Stars)"),
        ], scope=BotCommandScopeAllGroupChats())
        # основной бот поднимает дочерние боты и следит за подписками
        if not IS_CHILD:
            asyncio.create_task(_child_supervisor())
        # периодическая реклама канала (раз в 30 мин) — игрокам на Free
        asyncio.create_task(_ad_broadcast_loop(bot))
        await dp.start_polling(bot)
    finally:
        _stop_all_children()
        await db_close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(_main())
