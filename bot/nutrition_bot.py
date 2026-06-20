"""
Nutrition plan bot — admin panel for fitness coach.
"""
import base64
import json
import logging
import os
import re
import requests as _requests
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    MenuButtonWebApp, ReplyKeyboardMarkup, Update, WebAppInfo,
)
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Конфіг ───────────────────────────────────────────────────────────────────

OWNER_ID      = int(os.getenv("OWNER_ID", "716092714"))
BOT_TOKEN     = os.getenv("NUTRITION_BOT_TOKEN", "")
MINI_APP_URL  = os.getenv("MINI_APP_URL", "https://tiberiydoit.github.io/sculptnutrition")
BOT_USERNAME  = os.getenv("BOT_USERNAME", "sculptnutrition_bot")
GITHUB_TOKEN  = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO   = os.getenv("GITHUB_REPO", "tiberiydoit/sculptnutrition")

DB_PATH       = Path(os.getenv("DB_PATH", "clients_db.json"))
VARIANTS_PATH = Path(os.getenv("VARIANTS_PATH", "variants_db.json"))

# Типи прийомів для глобальної бази
MEAL_TYPES = ["сніданок", "обід", "вечеря", "перекус"]
MEAL_EMOJI = {"сніданок": "🌅", "обід": "⚡", "вечеря": "🌙", "перекус": "🥗"}

# ── FSM стани ────────────────────────────────────────────────────────────────

(
    NC_NAME, NC_TG_ID, NC_MEALS_COUNT,
    NC_DAILY, NC_STEPS,
    NC_MEAL_0, NC_MEAL_1, NC_MEAL_2, NC_MEAL_3, NC_MEAL_4,
) = range(10)

(
    FAST_NAME, FAST_TG_ID, FAST_STEPS, FAST_RATION,
) = range(10, 14)

(
    VAR_CLIENT_TEXT,       # варіанти для клієнта — текст
    VAR_GLOBAL_TEXT,       # варіант до бази — текст блоку
    VAR_GLOBAL_STEPS,      # кроки рецепту
    VAR_GLOBAL_NOTE,       # нотатка
    VAR_GLOBAL_PHOTO,      # фото варіанту
) = range(20, 25)

LANG_SELECT = 30

# Smart wizard — автоматичний розрахунок КБЖВ + підбір страв
(
    SM_NAME, SM_TG_ID, SM_SEX, SM_AGE, SM_WEIGHT, SM_HEIGHT,
    SM_ACTIVITY, SM_GOAL, SM_TARGET_WEIGHT, SM_MEALS_COUNT, SM_CONFIRM, SM_STEPS,
) = range(40, 52)

# Покрокове додавання страви в базу
(
    DA_TYPE, DA_NAME, DA_KCAL, DA_P, DA_F, DA_C,
    DA_INGREDIENTS, DA_STEPS, DA_NOTE, DA_PHOTO,
) = range(60, 70)

# ── Тексти для клієнтів (двома мовами) ───────────────────────────────────────

CLIENT_TEXTS = {
    "uk": {
        "choose_lang":    "Оберіть мову / Выберите язык:",
        "greeting_ready": "Привіт, <b>{name}</b>!\n\nТут твій персональний план харчування від тренера.\nНатисни кнопку нижче щоб відкрити його 👇",
        "greeting_wait":  "Привіт, <b>{name}</b>! 👋\n\nТвій план харчування ще готується.\nЯк тільки буде готовий — отримаєш повідомлення.",
        "greeting_noact": "Привіт, <b>{name}</b>!\n\nТвій доступ до плану харчування завершився.\nЗв'яжись з тренером для продовження.",
        "plan_updated":   "<b>{name}</b>, твій план харчування оновлено!",
        "btn_plan":       "Мій план",
        "btn_plan_menu":  "Мій план",
    },
    "ru": {
        "choose_lang":    "Оберіть мову / Выберите язык:",
        "greeting_ready": "Привет, <b>{name}</b>!\n\nЗдесь твой персональный план питания от тренера.\nНажми кнопку ниже чтобы открыть его 👇",
        "greeting_wait":  "Привет, <b>{name}</b>! 👋\n\nТвой план питания ещё готовится.\nКак только будет готов — получишь сообщение.",
        "greeting_noact": "Привет, <b>{name}</b>!\n\nТвой доступ к плану питания закончился.\nСвяжись с тренером для продления.",
        "plan_updated":   "<b>{name}</b>, твой план питания обновлён!",
        "btn_plan":       "Мой план",
        "btn_plan_menu":  "Мой план",
    },
}

def _t(client: dict, key: str, **kwargs) -> str:
    lang = client.get("lang", "uk")
    if lang not in CLIENT_TEXTS:
        lang = "uk"
    text = CLIENT_TEXTS[lang].get(key, CLIENT_TEXTS["uk"][key])
    return text.format(**kwargs) if kwargs else text

# ── Прийоми за замовчуванням ──────────────────────────────────────────────────

MEAL_DEFAULTS = {
    3: [
        ("Сніданок", "09:00-10:00"),
        ("Обід",     "13:00-15:00"),
        ("Вечеря",   "19:00-21:00"),
    ],
    4: [
        ("Сніданок", "09:00-10:00"),
        ("Обід №1",  "13:00-14:00"),
        ("Обід №2",  "15:00-16:00"),
        ("Вечеря",   "19:00-21:00"),
    ],
    5: [
        ("Сніданок",  "08:00-09:00"),
        ("Перекус 1", "11:00-11:30"),
        ("Обід",      "13:00-14:00"),
        ("Перекус 2", "16:30-17:00"),
        ("Вечеря",    "19:00-21:00"),
    ],
}

# Переклад назв прийомів їжі для російської мови
MEAL_NAME_RU = {
    "Сніданок":  "Завтрак",
    "Обід":      "Обед",
    "Обід №1":   "Обед №1",
    "Обід №2":   "Обед №2",
    "Обід №3":   "Обед №3",
    "Вечеря":    "Ужин",
    "Перекус 1": "Перекус 1",
    "Перекус 2": "Перекус 2",
    "Перекус":   "Перекус",
}

def _translate_meal_name(name: str, lang: str) -> str:
    if lang == "ru":
        return MEAL_NAME_RU.get(name, name)
    return name

# ── Клавіатура тренера ────────────────────────────────────────────────────────

ADMIN_KB = ReplyKeyboardMarkup(
    [
        ["➕ Новий клієнт", "👥 Клієнти"],
        ["📤 Надіслати план", "✏️ Редагувати"],
        ["🍽 Варіанти страв"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# ── GitHub файловий storage ───────────────────────────────────────────────────

def _gh_read(path: str) -> dict | list | None:
    """Read a JSON file from GitHub repo. Returns parsed content or None."""
    if not GITHUB_TOKEN:
        return None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    try:
        r = _requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            content = base64.b64decode(r.json()["content"]).decode("utf-8")
            return json.loads(content)
    except Exception as e:
        logger.warning("gh_read %s: %s", path, e)
    return None

def _gh_write(path: str, data: dict | list, message: str = "update") -> bool:
    """Write a JSON file to GitHub repo. Returns True on success."""
    if not GITHUB_TOKEN:
        return False
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()
    sha = None
    try:
        r = _requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json()["sha"]
    except Exception:
        pass
    body = {"message": message, "content": content}
    if sha:
        body["sha"] = sha
    try:
        r = _requests.put(url, json=body, headers=headers, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.warning("gh_write %s: %s", path, e)
        return False

# ── БД клієнтів ───────────────────────────────────────────────────────────────

def _load_db() -> dict:
    data = _gh_read("bot/clients_db.json")
    if data:
        return data
    if DB_PATH.exists():
        return json.loads(DB_PATH.read_text(encoding="utf-8"))
    return {"clients": []}

def _save_db(db: dict):
    _gh_write("bot/clients_db.json", db, "update clients_db")
    DB_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")

def _get_client_by_slug(slug: str) -> dict | None:
    db = _load_db()
    return next((c for c in db["clients"] if c["slug"] == slug), None)

def _get_client_by_tg_id(tg_id: int) -> dict | None:
    db = _load_db()
    return next((c for c in db["clients"] if c.get("telegram_id") == tg_id), None)

def _save_client(client: dict, push: bool = False):
    db = _load_db()
    idx = next((i for i, c in enumerate(db["clients"]) if c["slug"] == client["slug"]), None)
    if idx is not None:
        db["clients"][idx] = client
    else:
        db["clients"].append(client)
    _save_db(db)
    if push:
        _push_client_data(client)

def _all_clients() -> list[dict]:
    return _load_db()["clients"]

# ── БД варіантів ──────────────────────────────────────────────────────────────

def _load_variants() -> dict:
    data = _gh_read("bot/variants_db.json")
    if data:
        return data
    if VARIANTS_PATH.exists():
        return json.loads(VARIANTS_PATH.read_text(encoding="utf-8"))
    return {t: [] for t in MEAL_TYPES}

def _save_variants(db: dict):
    _gh_write("bot/variants_db.json", db, "update variants_db")

def _add_global_variant(meal_type: str, variant: dict):
    db = _load_variants()
    if meal_type not in db:
        db[meal_type] = []
    db[meal_type].append(variant)
    _save_variants(db)

def _delete_global_variant(meal_type: str, idx: int) -> bool:
    db = _load_variants()
    lst = db.get(meal_type, [])
    if 0 <= idx < len(lst):
        lst.pop(idx)
        db[meal_type] = lst
        _save_variants(db)
        return True
    return False

def _get_global_variants(meal_type: str) -> list[dict]:
    return _load_variants().get(meal_type, [])

# ── URL / invite ──────────────────────────────────────────────────────────────

def _push_client_data(client: dict) -> bool:
    """Push client JSON to GitHub Pages at data/{slug}.json for the Mini App."""
    slug = client["slug"].lstrip("@")
    return _gh_write(f"data/{slug}.json", client, f"update {slug}")

def _build_url(client: dict) -> str:
    lang = client.get("lang", "uk")
    slim = {k: v for k, v in client.items() if k != "variants"}
    # translate meal names for Russian clients
    if lang == "ru" and slim.get("meals"):
        slim["meals"] = [
            {**m, "name": _translate_meal_name(m["name"], lang)}
            for m in slim["meals"]
        ]
    d = base64.urlsafe_b64encode(
        json.dumps(slim, ensure_ascii=False).encode()
    ).decode()
    return f"{MINI_APP_URL}#d={d}"

def _build_invite_link(slug: str) -> str:
    return f"https://t.me/{BOT_USERNAME}?start={slug}"

# ── Хелпери ───────────────────────────────────────────────────────────────────

def _is_owner(update: Update) -> bool:
    return (update.effective_user.id if update.effective_user else 0) == OWNER_ID

def _display(c: dict) -> str:
    return c.get("display_name") or c["name"]

def _access_status(c: dict) -> tuple[bool, str]:
    """Returns (has_access, status_text)."""
    if not c.get("active", True):
        return False, "🔴 Відключено"
    exp = c.get("expires_at")
    if exp:
        days_left = (date.fromisoformat(exp) - date.today()).days
        if days_left < 0:
            return False, f"🔴 Термін вийшов ({exp})"
        elif days_left == 0:
            return True, "🟡 Останній день"
        elif days_left <= 7:
            return True, f"🟡 {days_left} д. залишилось"
        else:
            return True, f"🟢 До {exp}"
    return True, "🟢 Без терміну"

def _client_summary(c: dict) -> str:
    d = c["daily"]
    _, status = _access_status(c)
    return (
        f"👤 <b>{_display(c)}</b>\n"
        f"Доступ: {status}\n"
        f"КБЖВ: {d['kcal']} ккал | Б {d['protein']}г | Ж {d['fat']}г | В {d['carbs']}г\n"
        f"Кроки: {c.get('steps', 7000)} | Прийомів: {len(c.get('meals', []))}\n"
        f"Telegram ID: <code>{c.get('telegram_id') or 'не вказано'}</code>"
    )

async def _set_menu_button(bot, tg_id: int, url: str, label: str = "Мій план"):
    try:
        await bot.set_chat_menu_button(
            chat_id=tg_id,
            menu_button=MenuButtonWebApp(text=label, web_app=WebAppInfo(url=url)),
        )
    except Exception as e:
        logger.warning("menu button error %s: %s", tg_id, e)

async def _send_plan(bot, client: dict):
    tg_id = client.get("telegram_id")
    if not tg_id:
        return False
    url = _build_url(client)
    btn_label = _t(client, "btn_plan")
    await bot.send_message(
        chat_id=tg_id,
        text=_t(client, "plan_updated", name=_display(client)),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(btn_label, web_app=WebAppInfo(url=url))
        ]]),
    )
    await _set_menu_button(bot, tg_id, url)
    return True

# ── Парсер тексту раціону (КБЖВ+кроки) ───────────────────────────────────────

def _parse_ration_text(text: str) -> dict | None:
    meals = []
    header_match = re.search(r"(\d)\s*прийом", text, re.IGNORECASE)
    meals_count = int(header_match.group(1)) if header_match else None

    meal_pattern = re.compile(
        r"(\d)-[а-яА-ЯіІїЇєЄ]+:\s*(\d+)\s*ккал\s*\|\s*([\d.]+)\s*Б\s*/\s*([\d.]+)\s*Ж\s*/\s*([\d.]+)\s*В",
        re.IGNORECASE,
    )
    for m in meal_pattern.finditer(text):
        meals.append({
            "idx":  int(m.group(1)) - 1,
            "kcal": int(m.group(2)),
            "p":    round(float(m.group(3))),
            "f":    round(float(m.group(4))),
            "c":    round(float(m.group(5))),
        })
    if not meals:
        return None

    n = meals_count or len(meals)
    if n not in MEAL_DEFAULTS:
        n = len(meals)
    if n not in MEAL_DEFAULTS:
        return None

    meal_defs = MEAL_DEFAULTS[n]
    result_meals = []
    for i, md in enumerate(meals):
        name, time = meal_defs[i] if i < len(meal_defs) else (f"Прийом {i+1}", "")
        result_meals.append({"name": name, "time": time,
                              "kcal": md["kcal"], "p": md["p"], "f": md["f"], "c": md["c"]})

    summary = re.search(
        r"Калорії:\s*(\d+)\s*ккал\s*\|\s*Б:\s*([\d.]+)\s*г\s*\|\s*Ж:\s*([\d.]+)\s*г\s*\|\s*В:\s*([\d.]+)\s*г",
        text, re.IGNORECASE,
    )
    if summary:
        daily = {"kcal": int(summary.group(1)), "protein": round(float(summary.group(2))),
                 "fat": round(float(summary.group(3))), "carbs": round(float(summary.group(4)))}
    else:
        daily = {"kcal": sum(m["kcal"] for m in result_meals),
                 "protein": sum(m["p"] for m in result_meals),
                 "fat": sum(m["f"] for m in result_meals),
                 "carbs": sum(m["c"] for m in result_meals)}

    return {"meals": result_meals, "daily": daily}

# ── Парсер варіантів страв ────────────────────────────────────────────────────
#
# Формат тексту:
#
# СНІДАНОК
# ---
# Варіант 1: Назва варіанту
# інгредієнт 1
# інгредієнт 2
# Разом: 550 ккал | 45Б / 18Ж / 53В
# ---
# Варіант 2: Інша назва
# ...
#
# ОБІД №1
# ---
# ...

def _parse_variants_text(text: str) -> dict | None:
    """
    Повертає dict з двома ключами:
      "variants": { "сніданок": [...], ... }
      "meals_order": [{"key": "сніданок", "label": "Сніданок", "kcal":..., "p":..., "f":..., "c":...}, ...]
      "daily": {"kcal":..., "protein":..., "fat":..., "carbs":...} або None
    """
    variants: dict[str, list] = {}
    meals_order = []

    meal_type_pattern = re.compile(
        r"^(СНІДАНОК|ОБІД[^\n]*|ВЕЧЕРЯ|ПЕРЕКУС[^\n]*)",
        re.IGNORECASE | re.MULTILINE,
    )

    headers = list(meal_type_pattern.finditer(text))
    if not headers:
        return None

    def _normalize_key(raw: str) -> str:
        r = raw.strip().lower()
        if r.startswith("сніданок"): return "сніданок"
        if r.startswith("обід"):     return "обід"
        if r.startswith("вечеря"):   return "вечеря"
        if r.startswith("перекус"):  return "перекус"
        return r

    def _label(raw: str) -> str:
        # "ОБІД №1" → "Обід №1", "СНІДАНОК" → "Сніданок"
        return raw.strip().capitalize()

    totals_pattern = re.compile(
        r"Разом:\s*(\d+)\s*ккал\s*\|\s*([\d.]+)\s*Б\s*/\s*([\d.]+)\s*Ж\s*/\s*([\d.]+)\s*В",
        re.IGNORECASE,
    )

    def _parse_block(block_text: str) -> list[dict]:
        parsed = []
        parts = re.split(r"\n\s*---\s*\n", block_text)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            lines = [l.strip() for l in part.splitlines() if l.strip()]
            if not lines:
                continue
            name_line = lines[0]
            name_match = re.match(r"^Варіант\s*\d+:\s*(.+)", name_line, re.IGNORECASE)
            name = name_match.group(1).strip() if name_match else name_line
            totals_match = totals_pattern.search(part)
            if not totals_match:
                continue
            kcal = int(totals_match.group(1))
            p    = round(float(totals_match.group(2)))
            f    = round(float(totals_match.group(3)))
            c    = round(float(totals_match.group(4)))
            totals_line_idx = next(
                (i for i, l in enumerate(lines) if totals_pattern.search(l)), len(lines)
            )
            ingredients = lines[1:totals_line_idx]
            parsed.append({
                "name": name, "ingredients": ingredients,
                "kcal": kcal, "p": p, "f": f, "c": c,
                "steps": [], "note": None,
            })
        return parsed

    seen_keys = {}  # key → index in meals_order (для дедуплікації обід №1/№2)

    for i, header in enumerate(headers):
        raw_label = header.group(1)
        key   = _normalize_key(raw_label)
        label = _label(raw_label)
        start = header.end()
        end   = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[start:end]
        parsed = _parse_block(block)
        if not parsed:
            continue

        # Варіанти зберігаємо під унікальним ключем (обід, обід_2, обід_3...)
        unique_key = key
        count = sum(1 for k in seen_keys if k == key or k.startswith(key + "_"))
        if count > 0:
            unique_key = f"{key}_{count + 1}"

        seen_keys[unique_key] = True
        variants[unique_key] = parsed
        meals_order.append({
            "key":   unique_key,
            "label": label,
            "kcal":  parsed[0]["kcal"],
            "p":     parsed[0]["p"],
            "f":     parsed[0]["f"],
            "c":     parsed[0]["c"],
        })

    if not variants:
        return None

    # Парсимо ПІДСУМОК якщо є
    daily = None
    summary = re.search(
        r"ПІДСУМОК.*?Калорії:\s*(\d+)\s*ккал\s*\|\s*Б:\s*([\d.]+)\s*г\s*\|\s*Ж:\s*([\d.]+)\s*г\s*\|\s*В:\s*([\d.]+)\s*г",
        text, re.IGNORECASE | re.DOTALL,
    )
    if summary:
        daily = {
            "kcal":    int(summary.group(1)),
            "protein": round(float(summary.group(2))),
            "fat":     round(float(summary.group(3))),
            "carbs":   round(float(summary.group(4))),
        }

    return {"variants": variants, "meals_order": meals_order, "daily": daily}

# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args    = ctx.args

    if user_id == OWNER_ID:
        await update.message.reply_text(
            "<b>Nutrition Admin</b>\n\n"
            "/newclient — новий клієнт (wizard)\n"
            "/fast — швидке створення з тексту раціону\n"
            "/clients — всі клієнти\n"
            "/send — надіслати план\n"
            "/setid slug tg_id — вручну прив'язати ID",
            parse_mode="HTML",
            reply_markup=ADMIN_KB,
        )
        return

    # Deep link — прив'язуємо TG ID
    if args:
        slug = args[0]
        db = _load_db()
        for c in db["clients"]:
            if c["slug"].lstrip("@") == slug.lstrip("@") and not c.get("telegram_id"):
                c["telegram_id"] = user_id
                _save_db(db)
                logger.info("Прив'язано %s -> %s", user_id, c["slug"])
                break

    client = _get_client_by_tg_id(user_id)

    # Новий клієнт — створюємо запис і повідомляємо тренера
    if not client:
        user = update.effective_user
        username = f"@{user.username}" if user.username else str(user_id)
        slug = user.username or str(user_id)
        new_client = {
            "slug": slug,
            "name": username,
            "display_name": user.first_name or username,
            "telegram_id": user_id,
            "daily": {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0},
            "steps": 10000,
            "meals": [],
            "variants": {},
        }
        _save_client(new_client)
        client = new_client

        # Повідомлення тренеру
        await ctx.bot.send_message(
            OWNER_ID,
            f"🆕 <b>Новий клієнт!</b>\n\n"
            f"Ім'я: <b>{user.first_name or '—'}</b>\n"
            f"Username: <b>{username}</b>\n"
            f"Telegram ID: <code>{user_id}</code>\n\n"
            f"Додай йому раціон через <b>✏️ Редагувати</b>",
            parse_mode="HTML",
        )

    # Якщо мова ще не вибрана — питаємо
    if not client.get("lang"):
        ctx.user_data["pending_start_client_slug"] = client["slug"]
        await update.message.reply_text(
            "Оберіть мову / Выберите язык:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🇺🇦 Українська", callback_data="setlang:uk"),
                InlineKeyboardButton("🇷🇺 Русский",    callback_data="setlang:ru"),
            ]]),
        )
        return

    await _greet_client(update.message, client)


async def _greet_client(message, client: dict):
    """Send the appropriate greeting to a client based on their language and plan status."""
    user_id = client.get("telegram_id")
    first_name = client.get("display_name") or _display(client)
    has_access, _ = _access_status(client)

    if not has_access:
        await message.reply_text(
            _t(client, "greeting_noact", name=first_name),
            parse_mode="HTML",
        )
    elif client.get("meals"):
        url = _build_url(client)
        if user_id:
            await _set_menu_button(message.get_bot(), user_id, url, label=_t(client, "btn_plan_menu"))
        await message.reply_text(
            _t(client, "greeting_ready", name=first_name),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(_t(client, "btn_plan"), web_app=WebAppInfo(url=url))
            ]]),
        )
    else:
        await message.reply_text(
            _t(client, "greeting_wait", name=first_name),
            parse_mode="HTML",
        )

# ── /myid ─────────────────────────────────────────────────────────────────────

async def cmd_myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Твій Telegram ID: <code>{update.effective_user.id}</code>",
        parse_mode="HTML",
    )

# ── /setid ────────────────────────────────────────────────────────────────────

async def cmd_setid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "Формат: <code>/setid slug telegram_id</code>", parse_mode="HTML"
        )
        return
    slug = ctx.args[0].lstrip("@")
    try:
        tg_id = int(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ telegram_id має бути числом.")
        return

    db = _load_db()
    found = None
    for c in db["clients"]:
        if c["slug"].lstrip("@") == slug:
            c["telegram_id"] = tg_id
            found = c
            break
    if not found:
        await update.message.reply_text(f"❌ Клієнта <code>{slug}</code> не знайдено.", parse_mode="HTML")
        return
    _save_db(db)

    try:
        await _send_plan(ctx.bot, found)
        await update.message.reply_text(
            f"✅ ID прив'язано. План надіслано <b>{_display(found)}</b>.", parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"✅ ID прив'язано, але не вдалося надіслати: {e}", parse_mode="HTML")

# ── /clients ──────────────────────────────────────────────────────────────────

async def cmd_clients(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    clients = _all_clients()
    if not clients:
        await update.message.reply_text("Клієнтів ще немає.")
        return
    rows = []
    for c in clients:
        rows.append([
            InlineKeyboardButton(f"👤 {_display(c)}", callback_data=f"view:{c['slug']}"),
            InlineKeyboardButton("✏️", callback_data=f"editclient:{c['slug']}"),
            InlineKeyboardButton("📤", callback_data=f"send:{c['slug']}"),
        ])
    await update.message.reply_text(
        f"👥 <b>Клієнти ({len(clients)}):</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )

# ── /send ─────────────────────────────────────────────────────────────────────

async def cmd_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    clients = _all_clients()
    if not clients:
        await update.message.reply_text("Немає клієнтів.")
        return
    rows = [[InlineKeyboardButton(_display(c), callback_data=f"send:{c['slug']}")] for c in clients]
    await update.message.reply_text("📤 Кому надіслати план?", reply_markup=InlineKeyboardMarkup(rows))

# ── /edit ─────────────────────────────────────────────────────────────────────

async def cmd_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    clients = _all_clients()
    if not clients:
        await update.message.reply_text("Немає клієнтів.")
        return
    rows = [[InlineKeyboardButton(_display(c), callback_data=f"editclient:{c['slug']}")] for c in clients]
    await update.message.reply_text("✏️ Кого редагувати?", reply_markup=InlineKeyboardMarkup(rows))

# ── 🍽 Варіанти страв ─────────────────────────────────────────────────────────

async def cmd_variants_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    msg = update.message or (update.callback_query and update.callback_query.message)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Глобальна база", callback_data="vg:menu")],
        [InlineKeyboardButton("👤 Для клієнта",    callback_data="vc:menu")],
    ])
    text = "🍽 <b>Варіанти страв</b>\n\nОбери розділ:"
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

# ── Inline кнопки — головний роутер ──────────────────────────────────────────

async def cb_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data

    # ── Вибір мови клієнтом ───────────────────────────────────────────────────
    if data.startswith("setlang:"):
        lang = data[8:]  # "uk" or "ru"
        if lang not in CLIENT_TEXTS:
            await query.answer()
            return
        user_id = query.from_user.id
        client  = _get_client_by_tg_id(user_id)
        if client:
            client["lang"] = lang
            _save_client(client)
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        # Send greeting using bot directly
        if client:
            first_name = client.get("display_name") or _display(client)
            has_access, _ = _access_status(client)
            if not has_access:
                await query.get_bot().send_message(
                    chat_id=user_id,
                    text=_t(client, "greeting_noact", name=first_name),
                    parse_mode="HTML",
                )
            elif client.get("meals"):
                url = _build_url(client)
                await _set_menu_button(query.get_bot(), user_id, url, label=_t(client, "btn_plan_menu"))
                await query.get_bot().send_message(
                    chat_id=user_id,
                    text=_t(client, "greeting_ready", name=first_name),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(_t(client, "btn_plan"), web_app=WebAppInfo(url=url))
                    ]]),
                )
            else:
                await query.get_bot().send_message(
                    chat_id=user_id,
                    text=_t(client, "greeting_wait", name=first_name),
                    parse_mode="HTML",
                )
        return

    if query.from_user.id != OWNER_ID:
        return
    await query.answer()

    # ── Перегляд клієнта ──────────────────────────────────────────────────────
    if data.startswith("view:"):
        slug   = data[5:]
        client = _get_client_by_slug(slug)
        if not client:
            await query.edit_message_text("Клієнта не знайдено.")
            return
        has_access, _ = _access_status(client)
        toggle_label = "🔴 Відключити" if has_access else "🟢 Включити"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Редагувати", callback_data=f"editclient:{slug}"),
             InlineKeyboardButton("📤 Надіслати",  callback_data=f"send:{slug}")],
            [InlineKeyboardButton("🍽 Варіанти",   callback_data=f"vc:client:{slug}")],
            [InlineKeyboardButton("📅 Встановити термін", callback_data=f"access:setexp:{slug}"),
             InlineKeyboardButton("♾ Без терміну",        callback_data=f"access:noexp:{slug}")],
            [InlineKeyboardButton(toggle_label,            callback_data=f"access:toggle:{slug}"),
             InlineKeyboardButton("🗑 Видалити",           callback_data=f"access:delete:{slug}")],
            [InlineKeyboardButton("← Назад",               callback_data="back:clients")],
        ])
        await query.edit_message_text(_client_summary(client), parse_mode="HTML", reply_markup=kb)

    # ── Надіслати план ────────────────────────────────────────────────────────
    elif data.startswith("send:"):
        slug   = data[5:]
        client = _get_client_by_slug(slug)
        if not client:
            await query.edit_message_text("Клієнта не знайдено.")
            return
        try:
            ok = await _send_plan(ctx.bot, client)
            if ok:
                await query.edit_message_text(
                    f"✅ План надіслано <b>{_display(client)}</b>. Кнопка меню оновлена.",
                    parse_mode="HTML",
                )
            else:
                invite = _build_invite_link(client["slug"].lstrip("@"))
                await query.edit_message_text(
                    f"⚠️ Немає Telegram ID для <b>{_display(client)}</b>.\n\n"
                    f"Надішли клієнту invite:\n<code>{invite}</code>",
                    parse_mode="HTML",
                )
        except Exception as e:
            invite = _build_invite_link(client["slug"].lstrip("@"))
            await query.edit_message_text(
                f"⚠️ Помилка: {e}\n\nInvite:\n<code>{invite}</code>",
                parse_mode="HTML",
            )

    # ── Редагувати клієнта ────────────────────────────────────────────────────
    elif data.startswith("editclient:"):
        slug   = data[11:]
        client = _get_client_by_slug(slug)
        if not client:
            await query.edit_message_text("Клієнта не знайдено.")
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 КБЖВ",          callback_data=f"ef:{slug}:daily")],
            [InlineKeyboardButton("🍽 Порції прийомів", callback_data=f"ef:{slug}:meals")],
            [InlineKeyboardButton("👟 Кроки",           callback_data=f"ef:{slug}:steps")],
            [InlineKeyboardButton("← Назад",             callback_data=f"view:{slug}")],
        ])
        await query.edit_message_text(
            _client_summary(client) + "\n\n<i>Що змінюємо?</i>",
            parse_mode="HTML", reply_markup=kb,
        )

    elif data.startswith("ef:"):
        _, slug, field = data.split(":", 2)
        client = _get_client_by_slug(slug)
        if not client:
            await query.edit_message_text("Клієнта не знайдено.")
            return
        ctx.user_data["edit_slug"]  = slug
        ctx.user_data["edit_field"] = field

        if field == "daily":
            d = client["daily"]
            await query.edit_message_text(
                f"Поточне КБЖВ: <code>{d['kcal']} {d['protein']} {d['fat']} {d['carbs']}</code>\n\n"
                "Введи нові значення:\n<code>ккал білок жир вуглеводи</code>",
                parse_mode="HTML",
            )
        elif field == "steps":
            await query.edit_message_text(
                f"Поточні кроки: <b>{client.get('steps', 7000)}</b>\n\nВведи нову кількість:",
                parse_mode="HTML",
            )
        elif field == "meals":
            lines = []
            for i, m in enumerate(client.get("meals", []), 1):
                lines.append(f"{i}. <b>{m['name']}</b> — {m['kcal']} ккал | Б{m['p']} Ж{m['f']} В{m['c']}")
            ctx.user_data["edit_field"] = "meal_select"
            await query.edit_message_text(
                "\n".join(lines) + "\n\nВведи <b>номер прийому</b>:",
                parse_mode="HTML",
            )

    # ── Керування доступом ────────────────────────────────────────────────────
    elif data.startswith("access:"):
        _, action, slug = data.split(":", 2)
        client = _get_client_by_slug(slug)
        if not client:
            await query.edit_message_text("Клієнта не знайдено.")
            return

        if action == "toggle":
            client["active"] = not client.get("active", True)
            _save_client(client)
            status = "включено ✅" if client["active"] else "відключено 🔴"
            await query.answer(f"Доступ {status}", show_alert=True)
            # оновити view
            has_access, _ = _access_status(client)
            toggle_label = "🔴 Відключити" if has_access else "🟢 Включити"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Редагувати", callback_data=f"editclient:{slug}"),
                 InlineKeyboardButton("📤 Надіслати",  callback_data=f"send:{slug}")],
                [InlineKeyboardButton("🍽 Варіанти",   callback_data=f"vc:client:{slug}")],
                [InlineKeyboardButton("📅 Встановити термін", callback_data=f"access:setexp:{slug}"),
                 InlineKeyboardButton("♾ Без терміну",        callback_data=f"access:noexp:{slug}")],
                [InlineKeyboardButton(toggle_label,            callback_data=f"access:toggle:{slug}"),
                 InlineKeyboardButton("🗑 Видалити",           callback_data=f"access:delete:{slug}")],
                [InlineKeyboardButton("← Назад",               callback_data="back:clients")],
            ])
            await query.edit_message_text(_client_summary(client), parse_mode="HTML", reply_markup=kb)

        elif action == "noexp":
            client["expires_at"] = None
            _save_client(client)
            await query.answer("Термін знятий ♾", show_alert=False)
            has_access, _ = _access_status(client)
            toggle_label = "🔴 Відключити" if has_access else "🟢 Включити"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Редагувати", callback_data=f"editclient:{slug}"),
                 InlineKeyboardButton("📤 Надіслати",  callback_data=f"send:{slug}")],
                [InlineKeyboardButton("🍽 Варіанти",   callback_data=f"vc:client:{slug}")],
                [InlineKeyboardButton("📅 Встановити термін", callback_data=f"access:setexp:{slug}"),
                 InlineKeyboardButton("♾ Без терміну",        callback_data=f"access:noexp:{slug}")],
                [InlineKeyboardButton(toggle_label,            callback_data=f"access:toggle:{slug}"),
                 InlineKeyboardButton("🗑 Видалити",           callback_data=f"access:delete:{slug}")],
                [InlineKeyboardButton("← Назад",               callback_data="back:clients")],
            ])
            await query.edit_message_text(_client_summary(client), parse_mode="HTML", reply_markup=kb)

        elif action == "setexp":
            ctx.user_data["edit_slug"]  = slug
            ctx.user_data["edit_field"] = "expires_at"
            exp = client.get("expires_at")
            suggest_3m = (date.today() + timedelta(days=90)).isoformat()
            await query.edit_message_text(
                f"Поточний термін: <b>{exp or 'без терміну'}</b>\n\n"
                f"Введи дату закінчення у форматі <code>РРРР-ММ-ДД</code>\n"
                f"Наприклад: <code>{suggest_3m}</code> (3 місяці)",
                parse_mode="HTML",
            )

        elif action == "delete":
            ctx.user_data["delete_confirm"] = slug
            ctx.user_data.pop("vg_state", None)
            await query.edit_message_text(
                f"⚠️ Видалити <b>{_display(client)}</b> з бази?\n\n"
                f"Напиши <code>так</code> щоб підтвердити або /cancel для скасування.",
                parse_mode="HTML",
            )

    # ── Назад до списку ───────────────────────────────────────────────────────
    elif data == "back:clients":
        clients = _all_clients()
        rows = []
        for c in clients:
            rows.append([
                InlineKeyboardButton(f"👤 {_display(c)}", callback_data=f"view:{c['slug']}"),
                InlineKeyboardButton("✏️", callback_data=f"editclient:{c['slug']}"),
                InlineKeyboardButton("📤", callback_data=f"send:{c['slug']}"),
            ])
        await query.edit_message_text(
            f"👥 <b>Клієнти ({len(clients)}):</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # ГЛОБАЛЬНА БАЗА ВАРІАНТІВ
    # ══════════════════════════════════════════════════════════════════════════

    elif data == "vg:menu":
        rows = []
        for mt in MEAL_TYPES:
            count = len(_get_global_variants(mt))
            rows.append([InlineKeyboardButton(
                f"{MEAL_EMOJI[mt]} {mt.capitalize()} ({count})",
                callback_data=f"vg:type:{mt}",
            )])
        rows.append([InlineKeyboardButton("← Назад", callback_data="variants:main")])
        await query.edit_message_text(
            "📚 <b>Глобальна база варіантів</b>\n\nОбери тип прийому:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif data.startswith("vg:type:"):
        mt       = data[8:]
        variants = _get_global_variants(mt)
        rows     = []
        for i, v in enumerate(variants):
            rows.append([
                InlineKeyboardButton(f"📄 {v['name']}", callback_data=f"vg:view:{mt}:{i}"),
                InlineKeyboardButton("🗑", callback_data=f"vg:del:{mt}:{i}"),
            ])
        rows.append([InlineKeyboardButton(f"➕ Додати варіант", callback_data=f"vg:add:{mt}")])
        rows.append([InlineKeyboardButton("← Назад", callback_data="vg:menu")])
        text = (
            f"{MEAL_EMOJI[mt]} <b>{mt.capitalize()}</b> — {len(variants)} варіантів\n\n"
            + ("\n".join(f"{i+1}. {v['name']}" for i, v in enumerate(variants)) or "Поки порожньо.")
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("vg:view:"):
        _, _, mt, idx_str = data.split(":", 3)
        idx      = int(idx_str)
        variants = _get_global_variants(mt)
        if idx >= len(variants):
            await query.edit_message_text("Варіант не знайдено.")
            return
        v    = variants[idx]
        ings = "\n".join(f"  • {i}" for i in v.get("ingredients", []))
        steps_text = ""
        if v.get("steps"):
            steps_text = "\n\n<b>Рецепт:</b>\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(v["steps"]))
        note_text = f"\n\n<i>{v['note']}</i>" if v.get("note") else ""
        text = (
            f"<b>{v['name']}</b>\n"
            f"{kcal_line(v)}\n\n"
            f"<b>Інгредієнти:</b>\n{ings}"
            f"{steps_text}{note_text}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Рецепт", callback_data=f"vg:editsteps:{mt}:{idx}"),
             InlineKeyboardButton("📝 Нотатка", callback_data=f"vg:editnote:{mt}:{idx}")],
            [InlineKeyboardButton("🗑 Видалити", callback_data=f"vg:del:{mt}:{idx}")],
            [InlineKeyboardButton("← Назад", callback_data=f"vg:type:{mt}")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

    elif data.startswith("vg:del:"):
        _, _, mt, idx_str = data.split(":", 3)
        idx = int(idx_str)
        variants = _get_global_variants(mt)
        name = variants[idx]["name"] if idx < len(variants) else "?"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Так, видалити", callback_data=f"vg:delconfirm:{mt}:{idx}"),
            InlineKeyboardButton("← Назад",          callback_data=f"vg:type:{mt}"),
        ]])
        await query.edit_message_text(
            f"Видалити варіант <b>{name}</b>?", parse_mode="HTML", reply_markup=kb
        )

    elif data.startswith("vg:delconfirm:"):
        _, _, mt, idx_str = data.split(":", 3)
        _delete_global_variant(mt, int(idx_str))
        await query.answer("Видалено", show_alert=False)
        # Повертаємось до списку
        variants = _get_global_variants(mt)
        rows = []
        for i, v in enumerate(variants):
            rows.append([
                InlineKeyboardButton(f"📄 {v['name']}", callback_data=f"vg:view:{mt}:{i}"),
                InlineKeyboardButton("🗑", callback_data=f"vg:del:{mt}:{i}"),
            ])
        rows.append([InlineKeyboardButton(f"➕ Додати варіант", callback_data=f"vg:add:{mt}")])
        rows.append([InlineKeyboardButton("← Назад", callback_data="vg:menu")])
        await query.edit_message_text(
            f"{MEAL_EMOJI[mt]} <b>{mt.capitalize()}</b> — {len(variants)} варіантів",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
        )

    elif data.startswith("vg:add:"):
        mt = data[7:]
        ctx.user_data["vg_meal_type"] = mt
        ctx.user_data["vg_state"]     = "text"
        await query.edit_message_text(
            f"➕ <b>Новий варіант для {mt}</b>\n\n"
            f"Вставте блок варіанту у форматі:\n\n"
            f"<code>Варіант 1: Назва страви\n"
            f"інгредієнт 1\n"
            f"інгредієнт 2\n"
            f"Разом: 550 ккал | 45Б / 18Ж / 53В</code>",
            parse_mode="HTML",
        )

    elif data.startswith("vg:editsteps:"):
        _, _, mt, idx_str = data.split(":", 3)
        ctx.user_data["vg_edit_mt"]  = mt
        ctx.user_data["vg_edit_idx"] = int(idx_str)
        ctx.user_data["vg_state"]    = "editsteps"
        await query.edit_message_text(
            "Введи кроки рецепту — кожен з нового рядка:\n\n"
            "<i>Якщо хочеш очистити рецепт — напиши: прочистити</i>",
            parse_mode="HTML",
        )

    elif data.startswith("vg:editnote:"):
        _, _, mt, idx_str = data.split(":", 3)
        ctx.user_data["vg_edit_mt"]  = mt
        ctx.user_data["vg_edit_idx"] = int(idx_str)
        ctx.user_data["vg_state"]    = "editnote"
        await query.edit_message_text(
            "Введи нотатку до варіанту:\n\n"
            "<i>Щоб прибрати нотатку — напиши: прочистити</i>",
            parse_mode="HTML",
        )

    elif data.startswith("vg:skipsteps:"):
        _, _, mt, idx_str = data.split(":", 3)
        ctx.user_data.pop("vg_state", None)
        ctx.user_data["vg_edit_mt"]  = mt
        ctx.user_data["vg_edit_idx"] = int(idx_str)
        # Питаємо нотатку
        ctx.user_data["vg_state"] = "editnote"
        await query.edit_message_text(
            "Нотатка до варіанту (необов'язково):\n\n"
            "<i>Натисни Пропустити або введи текст</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Пропустити", callback_data=f"vg:skipnote:{mt}:{idx_str}")
            ]]),
        )

    elif data.startswith("vg:skipnote:"):
        _, _, mt, idx_str = data.split(":", 3)
        ctx.user_data.pop("vg_state", None)
        await query.edit_message_text(
            "✅ Варіант збережено!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"← До списку {mt}", callback_data=f"vg:type:{mt}")
            ]]),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # ВАРІАНТИ ДЛЯ КЛІЄНТА
    # ══════════════════════════════════════════════════════════════════════════

    elif data == "vc:menu":
        clients = _all_clients()
        if not clients:
            await query.edit_message_text("Немає клієнтів.")
            return
        rows = [[InlineKeyboardButton(f"👤 {_display(c)}", callback_data=f"vc:client:{c['slug']}")] for c in clients]
        rows.append([InlineKeyboardButton("← Назад", callback_data="variants:main")])
        await query.edit_message_text(
            "👤 <b>Варіанти для клієнта</b>\n\nОбери клієнта:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
        )

    elif data.startswith("vc:client:"):
        slug   = data[10:]
        client = _get_client_by_slug(slug)
        if not client:
            await query.edit_message_text("Клієнта не знайдено.")
            return
        ctx.user_data["vc_slug"]  = slug
        ctx.user_data["vg_state"] = "vc_text"
        await query.edit_message_text(
            f"👤 <b>{_display(client)}</b>\n\n"
            f"Вставте повний текст варіантів по всіх прийомах.\n\n"
            f"<b>Формат:</b>\n"
            f"<code>СНІДАНОК\n"
            f"---\n"
            f"Варіант 1: Назва\n"
            f"інгредієнт 1\n"
            f"Разом: 550 ккал | 45Б / 18Ж / 53В\n"
            f"---\n"
            f"Варіант 2: Назва\n"
            f"...\n\n"
            f"ОБІД №1\n"
            f"---\n"
            f"...</code>",
            parse_mode="HTML",
        )

    elif data == "variants:main":
        await cmd_variants_menu(update, ctx)

    # ── Підтвердження рецепту після додавання варіанту ────────────────────────
    elif data.startswith("vg:asksteps:"):
        _, _, mt, idx_str = data.split(":", 3)
        ctx.user_data["vg_edit_mt"]  = mt
        ctx.user_data["vg_edit_idx"] = int(idx_str)
        ctx.user_data["vg_state"]    = "editsteps"
        await query.edit_message_text(
            "Введи кроки рецепту приготування — кожен з нового рядка:\n\n"
            "<i>Або натисни Пропустити</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Пропустити", callback_data=f"vg:skipsteps:{mt}:{idx_str}")
            ]]),
        )

# ── Текстові відповіді (редагування + варіанти) ───────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return

    text  = update.message.text.strip()

    # Перевірка видалення — найвищий пріоритет
    if ctx.user_data.get("delete_confirm"):
        await _handle_edit_input(update, ctx)
        return

    state = ctx.user_data.get("vg_state")

    # ── Додавання варіанту до глобальної бази ────────────────────────────────
    if state == "text":
        mt = ctx.user_data.get("vg_meal_type")
        result = _parse_variants_text(f"{mt.upper()}\n---\n{text}")
        variants_list = result["variants"].get(mt, []) if result else []
        if not variants_list:
            # Спробуємо простіший парсинг — один варіант без заголовку
            single = _parse_single_variant(text)
            if single:
                variants_list = [single]
        if not variants_list:
            await update.message.reply_text(
                "❌ Не вдалося розпізнати варіант.\n\n"
                "Перевір формат:\n"
                "<code>Варіант 1: Назва\nінгредієнт\nРазом: 550 ккал | 45Б / 18Ж / 53В</code>",
                parse_mode="HTML",
            )
            return

        db = _load_variants()
        if mt not in db:
            db[mt] = []
        start_idx = len(db[mt])
        for v in variants_list:
            db[mt].append(v)
        _save_variants(db)
        ctx.user_data.pop("vg_state", None)

        last_idx = start_idx + len(variants_list) - 1
        ctx.user_data["vg_edit_mt"]  = mt
        ctx.user_data["vg_edit_idx"] = last_idx
        ctx.user_data["vg_state"]    = "askphoto"
        await update.message.reply_text(
            f"✅ Збережено {len(variants_list)} варіант(ів) для <b>{mt}</b>.\n\n"
            f"Надішли фото для останнього варіанту або пропусти:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Без фото", callback_data=f"vg:asksteps:{mt}:{last_idx}"),
            ]]),
        )

    # ── Редагування кроків рецепту ────────────────────────────────────────────
    elif state == "editsteps":
        mt  = ctx.user_data.get("vg_edit_mt")
        idx = ctx.user_data.get("vg_edit_idx")
        db  = _load_variants()
        if text.lower() == "прочистити":
            db[mt][idx]["steps"] = []
        else:
            steps = [s.strip() for s in text.splitlines() if s.strip()]
            db[mt][idx]["steps"] = steps
        _save_variants(db)
        ctx.user_data.pop("vg_state", None)
        await update.message.reply_text(
            "✅ Рецепт збережено! Додати нотатку?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Додати нотатку", callback_data=f"vg:editnote:{mt}:{idx}"),
                InlineKeyboardButton("⏭ Пропустити",     callback_data=f"vg:skipnote:{mt}:{idx}"),
            ]]),
        )

    # ── Редагування нотатки ───────────────────────────────────────────────────
    elif state == "editnote":
        mt  = ctx.user_data.get("vg_edit_mt")
        idx = ctx.user_data.get("vg_edit_idx")
        db  = _load_variants()
        db[mt][idx]["note"] = None if text.lower() == "прочистити" else text
        _save_variants(db)
        ctx.user_data.pop("vg_state", None)
        await update.message.reply_text(
            "✅ Нотатку збережено!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"← До списку {mt}", callback_data=f"vg:type:{mt}")
            ]]),
        )

    # ── Очікування фото (текст замість фото — ігноруємо) ─────────────────────
    elif state == "askphoto":
        await update.message.reply_text(
            "Очікую фото. Надішли фото або натисни '⏭ Без фото'.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Без фото", callback_data=f"vg:asksteps:{ctx.user_data.get('vg_edit_mt')}:{ctx.user_data.get('vg_edit_idx')}")
            ]]),
        )

    # ── Варіанти для клієнта ──────────────────────────────────────────────────
    elif state == "vc_text":
        slug   = ctx.user_data.get("vc_slug")
        client = _get_client_by_slug(slug)
        if not client:
            await update.message.reply_text("Клієнта не знайдено.")
            ctx.user_data.pop("vg_state", None)
            return
        result = _parse_variants_text(text)
        if not result:
            await update.message.reply_text(
                "❌ Не вдалося розпізнати варіанти.\n\nПеревір формат.",
                parse_mode="HTML",
            )
            return

        # Підтягуємо steps/note/photo з глобальної бази по назві варіанту
        global_db = _load_variants()
        global_by_name = {}
        for mt_list in global_db.values():
            for gv in mt_list:
                global_by_name[gv["name"].strip().lower()] = gv

        for mt_key, var_list in result["variants"].items():
            for v in var_list:
                gv = global_by_name.get(v["name"].strip().lower())
                if gv:
                    if gv.get("steps"): v["steps"] = gv["steps"]
                    if gv.get("note"):  v["note"]  = gv["note"]
                    if gv.get("photo"): v["photo"]  = gv["photo"]

        client["variants"] = result["variants"]

        # Оновлюємо meals згідно з порядком прийомів з тексту
        meal_defs = MEAL_DEFAULTS.get(len(result["meals_order"]), {})
        new_meals = []
        for mo in result["meals_order"]:
            # Час беремо зі старих meals якщо є збіг по назві, або з MEAL_DEFAULTS
            old_meal = next(
                (m for m in client.get("meals", [])
                 if m["name"].lower() == mo["label"].lower()), None
            )
            time = old_meal["time"] if old_meal else ""
            if not time:
                # шукаємо по індексу в MEAL_DEFAULTS
                idx = result["meals_order"].index(mo)
                n = len(result["meals_order"])
                if n in MEAL_DEFAULTS and idx < len(MEAL_DEFAULTS[n]):
                    time = MEAL_DEFAULTS[n][idx][1]
            new_meals.append({
                "name": mo["label"],
                "time": time,
                "kcal": mo["kcal"],
                "p":    mo["p"],
                "f":    mo["f"],
                "c":    mo["c"],
            })
        client["meals"] = new_meals

        # Оновлюємо daily якщо є ПІДСУМОК
        if result["daily"]:
            client["daily"] = result["daily"]

        _save_client(client, push=True)
        ctx.user_data.pop("vg_state", None)

        total = sum(len(v) for v in result["variants"].values())
        meals_list = ", ".join(f"{mo['label']} ({len(result['variants'].get(mo['key'], []))})" for mo in result["meals_order"])
        daily_info = ""
        if result["daily"]:
            d = result["daily"]
            daily_info = f"\n📊 КБЖВ оновлено: {d['kcal']} ккал | Б{d['protein']} Ж{d['fat']} В{d['carbs']}г"
        await update.message.reply_text(
            f"✅ Збережено <b>{total}</b> варіантів для <b>{_display(client)}</b>:\n"
            f"{meals_list}{daily_info}\n\n"
            f"Надіслати оновлений план клієнту?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📤 Надіслати план", callback_data=f"send:{slug}"),
                InlineKeyboardButton("⏭ Пізніше",        callback_data=f"view:{slug}"),
            ]]),
        )

    # ── Редагування полів клієнта ─────────────────────────────────────────────
    else:
        await _handle_edit_input(update, ctx)


def _parse_single_variant(text: str) -> dict | None:
    totals = re.search(
        r"Разом:\s*(\d+)\s*ккал\s*\|\s*([\d.]+)\s*Б\s*/\s*([\d.]+)\s*Ж\s*/\s*([\d.]+)\s*В",
        text, re.IGNORECASE,
    )
    if not totals:
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    name_match = re.match(r"^Варіант\s*\d+:\s*(.+)", lines[0], re.IGNORECASE)
    name = name_match.group(1).strip() if name_match else lines[0]
    totals_line_idx = next(
        (i for i, l in enumerate(lines)
         if re.search(r"Разом:", l, re.IGNORECASE)), len(lines)
    )
    return {
        "name":        name,
        "ingredients": lines[1:totals_line_idx],
        "kcal":        int(totals.group(1)),
        "p":           round(float(totals.group(2))),
        "f":           round(float(totals.group(3))),
        "c":           round(float(totals.group(4))),
        "steps":       [],
        "note":        None,
    }


def kcal_line(v: dict) -> str:
    return f"{v['kcal']} ккал | Б {v['p']}г Ж {v['f']}г В {v['c']}г"


async def _handle_edit_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # ── Підтвердження видалення ───────────────────────────────────────────────
    del_slug = ctx.user_data.get("delete_confirm")
    if del_slug:
        if update.message.text.strip().lower() == "так":
            db = _load_db()
            db["clients"] = [c for c in db["clients"] if c["slug"] != del_slug]
            _save_db(db)
            ctx.user_data.pop("delete_confirm", None)
            await update.message.reply_text("🗑 Клієнта видалено.")
        else:
            await update.message.reply_text("Скасовано. Напиши <code>так</code> щоб підтвердити або /cancel", parse_mode="HTML")
        return

    slug  = ctx.user_data.get("edit_slug")
    field = ctx.user_data.get("edit_field")
    if not slug or not field:
        return
    client = _get_client_by_slug(slug)
    if not client:
        await update.message.reply_text("Клієнта не знайдено.")
        ctx.user_data.clear()
        return
    text = update.message.text.strip()

    if field == "daily":
        try:
            kcal, p, f, c = map(int, text.split())
        except ValueError:
            await update.message.reply_text("❌ Формат: <code>2160 181 75 191</code>", parse_mode="HTML")
            return
        client["daily"] = {"kcal": kcal, "protein": p, "fat": f, "carbs": c}
        _save_client(client)
        await update.message.reply_text(
            f"✅ КБЖВ <b>{_display(client)}</b> оновлено: {kcal} ккал | Б{p} Ж{f} В{c}г",
            parse_mode="HTML",
        )
        ctx.user_data.clear()

    elif field == "steps":
        try:
            steps = int(text)
        except ValueError:
            await update.message.reply_text("❌ Введи число.", parse_mode="HTML")
            return
        client["steps"] = steps
        _save_client(client)
        await update.message.reply_text(
            f"✅ Кроки <b>{_display(client)}</b>: {steps}", parse_mode="HTML"
        )
        ctx.user_data.clear()

    elif field == "meal_select":
        try:
            idx = int(text) - 1
            if not (0 <= idx < len(client.get("meals", []))):
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Введи номер зі списку.")
            return
        ctx.user_data["edit_meal_idx"] = idx
        ctx.user_data["edit_field"]    = "meal_data"
        m = client["meals"][idx]
        await update.message.reply_text(
            f"<b>{m['name']}</b> — зараз: <code>{m['kcal']} {m['p']} {m['f']} {m['c']}</code>\n\n"
            "Введи нові значення: <code>ккал білок жир вуглеводи</code>",
            parse_mode="HTML",
        )

    elif field == "meal_data":
        try:
            kcal, p, f, c = map(int, text.split())
        except ValueError:
            await update.message.reply_text("❌ Формат: <code>735 52 23 81</code>", parse_mode="HTML")
            return
        idx = ctx.user_data["edit_meal_idx"]
        client["meals"][idx].update({"kcal": kcal, "p": p, "f": f, "c": c})
        _save_client(client)
        m = client["meals"][idx]
        await update.message.reply_text(
            f"✅ <b>{m['name']}</b>: {kcal} ккал | Б{p} Ж{f} В{c}г", parse_mode="HTML"
        )
        ctx.user_data.clear()

    elif field == "expires_at":
        try:
            date.fromisoformat(text)
        except ValueError:
            await update.message.reply_text("❌ Формат: <code>РРРР-ММ-ДД</code>, наприклад <code>2026-08-03</code>", parse_mode="HTML")
            return
        client["expires_at"] = text
        client["active"] = True
        _save_client(client)
        days_left = (date.fromisoformat(text) - date.today()).days
        await update.message.reply_text(
            f"✅ Термін <b>{_display(client)}</b> встановлено до <b>{text}</b> ({days_left} днів)",
            parse_mode="HTML",
        )
        ctx.user_data.clear()


# ── /newclient wizard ─────────────────────────────────────────────────────────

async def cmd_newclient(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return ConversationHandler.END
    ctx.user_data.clear()
    await update.message.reply_text("➕ <b>Новий клієнт</b>\n\nКрок 1: Ім'я клієнта:", parse_mode="HTML")
    return NC_NAME

async def nc_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["display_name"] = update.message.text.strip()
    ctx.user_data["slug"] = re.sub(r"[^a-zA-Z0-9_]", "_", ctx.user_data["display_name"].lower())
    await update.message.reply_text(
        "Крок 2: Telegram ID клієнта?\n<i>Введи 0 якщо не знаєш</i>", parse_mode="HTML"
    )
    return NC_TG_ID

async def nc_tg_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        tg_id = int(update.message.text.strip())
    except ValueError:
        tg_id = 0
    ctx.user_data["telegram_id"] = tg_id or None
    await update.message.reply_text("Крок 3: Кількість прийомів — <b>3</b>, <b>4</b> або <b>5</b>:", parse_mode="HTML")
    return NC_MEALS_COUNT

async def nc_meals_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        n = int(update.message.text.strip())
        if n not in (3, 4, 5):
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введи 3, 4 або 5:")
        return NC_MEALS_COUNT
    ctx.user_data["meals_count"] = n
    ctx.user_data["meals"] = []
    await update.message.reply_text(
        "Крок 4: КБЖВ за день.\nФормат: <code>ккал білок жир вуглеводи</code>", parse_mode="HTML"
    )
    return NC_DAILY

async def nc_daily(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        kcal, p, f, c = map(int, update.message.text.strip().split())
    except ValueError:
        await update.message.reply_text("❌ Формат: <code>2160 181 75 191</code>", parse_mode="HTML")
        return NC_DAILY
    ctx.user_data["daily"] = {"kcal": kcal, "protein": p, "fat": f, "carbs": c}
    await update.message.reply_text("Крок 5: Кроки на день. Приклад: <code>7000</code>", parse_mode="HTML")
    return NC_STEPS

async def nc_steps(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        steps = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи число.", parse_mode="HTML")
        return NC_STEPS
    ctx.user_data["steps"] = steps
    meal_defs = MEAL_DEFAULTS[ctx.user_data["meals_count"]]
    ctx.user_data["meal_defs"] = meal_defs
    name, time = meal_defs[0]
    await update.message.reply_text(
        f"<b>{name}</b> ({time})\nФормат: <code>ккал білок жир вуглеводи</code>", parse_mode="HTML"
    )
    return NC_MEAL_0

async def _nc_meal(update: Update, ctx: ContextTypes.DEFAULT_TYPE, idx: int, next_state: int):
    try:
        kcal, p, f, c = map(int, update.message.text.strip().split())
    except ValueError:
        await update.message.reply_text("❌ Формат: <code>735 52 23 81</code>", parse_mode="HTML")
        return NC_MEAL_0 + idx
    meal_defs = ctx.user_data["meal_defs"]
    name, time = meal_defs[idx]
    ctx.user_data["meals"].append({"name": name, "time": time, "kcal": kcal, "p": p, "f": f, "c": c})
    if idx + 1 < len(meal_defs):
        n, t = meal_defs[idx + 1]
        await update.message.reply_text(f"<b>{n}</b> ({t})\n<code>ккал білок жир вуглеводи</code>", parse_mode="HTML")
        return next_state
    return await _finish_newclient(update, ctx)

async def _finish_newclient(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    display_name = ctx.user_data["display_name"]
    slug         = ctx.user_data["slug"]
    client = {
        "slug":         slug,
        "name":         slug,
        "display_name": display_name,
        "telegram_id":  ctx.user_data.get("telegram_id"),
        "daily":        ctx.user_data["daily"],
        "steps":        ctx.user_data["steps"],
        "meals":        ctx.user_data["meals"],
    }
    _save_client(client)
    invite = _build_invite_link(slug)
    d = client["daily"]
    meals_text = "\n".join(
        f"  {m['name']}: {m['kcal']} ккал | Б{m['p']} Ж{m['f']} В{m['c']}"
        for m in client["meals"]
    )
    await update.message.reply_text(
        f"✅ <b>{display_name}</b> створено!\n\n"
        f"КБЖВ: {d['kcal']} ккал | Б{d['protein']} Ж{d['fat']} В{d['carbs']}г\n"
        f"Кроки: {client['steps']}\n\n"
        f"Прийоми:\n{meals_text}\n\n"
        f"Invite для клієнта:\n<code>{invite}</code>",
        parse_mode="HTML",
    )
    ctx.user_data.clear()
    return ConversationHandler.END

async def nc_meal_0(u, c): return await _nc_meal(u, c, 0, NC_MEAL_1)
async def nc_meal_1(u, c): return await _nc_meal(u, c, 1, NC_MEAL_2)
async def nc_meal_2(u, c): return await _nc_meal(u, c, 2, NC_MEAL_3)
async def nc_meal_3(u, c): return await _nc_meal(u, c, 3, NC_MEAL_4)
async def nc_meal_4(u, c): return await _nc_meal(u, c, 4, ConversationHandler.END)

async def nc_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("Скасовано.", reply_markup=ADMIN_KB)
    return ConversationHandler.END

# ── Розрахунок КБЖВ (формула Міффліна-Сан Жеора) ────────────────────────────

ACTIVITY_FACTORS = {
    "1": ("Сидяча робота, мінімум руху",           1.3),
    "2": ("Середня активність / ~10 000 кроків",   1.4),
}

GOAL_ADJUSTMENTS = {
    "scheme_300": ("Схуднення (дефіцит 300 ккал)",  -300),
    "scheme_500": ("Схуднення (дефіцит 500 ккал)",  -500),
    "keep":       ("Підтримка",                         0),
    "gain":       ("Набір маси (+300 ккал)",         +300),
}

# База продуктів (на 100г): ккал, білок, жир, вуглеводи
FOOD_DB = {
    "Вівсяна крупа":        (340, 12, 6,    60),
    "Рис (сухий)":          (340,  7, 0.7,  76),
    "Гречка (суха)":        (330, 12, 3,    62),
    "Макарони (сухі)":      (344, 12, 1.5,  71),
    "Хліб цільнозерновий":  (230,  8, 2,    45),
    "Картопля заморожена":  (125,  2, 3,    22),
    "Банан":                ( 90, 1.5, 0.2, 21),
    "Куряче філе":          (110, 23, 1.5,   0),
    "Індичий фарш 7%":      (140, 19, 7,     0),
    "Тунець у власному соку":(100, 22, 1,    0),
    "Лосось слабосолоний":  (200, 20, 13,    0),
    "Яловичина вирізка":    (120, 22, 3.5,   0),
    "Бекон":                (500, 15, 45,  0.5),
    "Яйце ціле (1шт ~55г)": ( 70,  6, 5,   0.5),
    "Яєчний білок (1шт)":   ( 17, 3.5, 0,  0.3),
    "Творог 5%":            (120, 16, 5,     3),
    "Творог 0%":            ( 70, 16, 0.2,  3.3),
    "Грецький йогурт 2%":   ( 60,  9, 2,     4),
    "Грецький йогурт 0%":   ( 50, 10, 0,   3.5),
    "Молоко 1.5%":          ( 45,  3, 1.5,  4.7),
    "Протеїн 75%":          (380, 75, 6,     8),
    "Арахісова паста":      (600, 24, 50,   14),
    "Авокадо":              (160,  2, 15,    6),
    "Творожний сир":        (230,  6, 21,    4),
    "Оливкова олія":        (900,  0, 100,   0),
    "Мед / Джем":           (310, 0.5, 0,   75),
}

def _calc_kbzv(sex: str, age: int, weight: float, height: float,
               activity: str, goal: str, target_weight: float = None) -> dict:
    # BMR — формула Міффліна-Сан Жеора
    if sex == "m":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161

    factor = ACTIVITY_FACTORS[activity][1]
    tdee   = bmr * factor
    adj    = GOAL_ADJUSTMENTS[goal][1]
    kcal   = round(tdee + adj)

    # Білок: 2г на кг цільової/сухої маси (або поточної якщо не вказана)
    ref_weight = target_weight if target_weight else weight
    protein = round(ref_weight * 2)

    # Жир: 1г на кг ваги (мінімум 60г, максимум 80г для сушки)
    if "scheme" in goal:
        fat = max(60, min(80, round(weight * 1)))
    else:
        fat = round(weight * 1)

    # Вуглеводи: залишок
    carbs = round((kcal - protein * 4 - fat * 9) / 4)
    if carbs < 0:
        carbs = 0

    return {"kcal": kcal, "protein": protein, "fat": fat, "carbs": carbs}

def _distribute_meals(daily: dict, meals_count: int) -> list[dict]:
    """Розподіляє добові КБЖВ між прийомами і підбирає варіанти з бази."""
    meal_defs = MEAL_DEFAULTS.get(meals_count, MEAL_DEFAULTS[3])
    # Рівномірний розподіл з корекцією на сніданок і вечерю
    ratios = {
        3: [0.30, 0.40, 0.30],
        4: [0.25, 0.30, 0.25, 0.20],
        5: [0.20, 0.15, 0.30, 0.15, 0.20],
    }.get(meals_count, [1/meals_count] * meals_count)

    # Типи для підбору страв з бази
    type_map = {
        3: ["сніданок", "обід", "вечеря"],
        4: ["сніданок", "обід", "обід", "вечеря"],
        5: ["сніданок", "перекус", "обід", "перекус", "вечеря"],
    }.get(meals_count, ["обід"] * meals_count)

    global_db = _load_variants()
    meals = []
    for i, (name, time) in enumerate(meal_defs):
        target_kcal = round(daily["kcal"] * ratios[i])
        mt = type_map[i]
        variants_pool = global_db.get(mt, [])
        # Підбираємо варіант найближчий по калоріям (або перший якщо база порожня)
        best = None
        if variants_pool:
            best = min(variants_pool, key=lambda v: abs(v.get("kcal", 0) - target_kcal))
        if best:
            meals.append({
                "name": name, "time": time,
                "kcal": best["kcal"], "p": best["p"], "f": best["f"], "c": best["c"],
            })
        else:
            p = round(daily["protein"] * ratios[i])
            f = round(daily["fat"]     * ratios[i])
            c = round(daily["carbs"]   * ratios[i])
            meals.append({"name": name, "time": time, "kcal": target_kcal, "p": p, "f": f, "c": c})
    return meals

# ── Smart wizard ──────────────────────────────────────────────────────────────

async def cmd_smart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return ConversationHandler.END
    ctx.user_data.clear()
    await update.message.reply_text(
        "➕ <b>Новий клієнт</b>\n\nКрок 1: Ім'я клієнта:", parse_mode="HTML"
    )
    return SM_NAME

async def sm_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    ctx.user_data["display_name"] = name
    ctx.user_data["slug"] = re.sub(r"[^a-zA-Z0-9_]", "_", name.lower())
    await update.message.reply_text(
        "Крок 2: Telegram ID клієнта?\n<i>Введи 0 якщо не знаєш</i>", parse_mode="HTML"
    )
    return SM_TG_ID

async def sm_tg_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        tg_id = int(update.message.text.strip())
    except ValueError:
        tg_id = 0
    ctx.user_data["telegram_id"] = tg_id or None
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("♂ Чоловік", callback_data="sm:sex:m"),
         InlineKeyboardButton("♀ Жінка",  callback_data="sm:sex:f")],
    ])
    await update.message.reply_text("Крок 3: Стать клієнта:", reply_markup=kb)
    return SM_SEX

async def sm_sex_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["sex"] = query.data.split(":")[2]
    await query.edit_message_text("Крок 4: Вік (повних років):")
    return SM_AGE

async def sm_age(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["age"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи число (наприклад: 28):")
        return SM_AGE
    await update.message.reply_text("Крок 5: Поточна вага (кг, наприклад: 72.5):")
    return SM_WEIGHT

async def sm_weight(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["weight"] = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Введи число (наприклад: 72.5):")
        return SM_WEIGHT
    await update.message.reply_text("Крок 6: Зріст (см, наприклад: 175):")
    return SM_HEIGHT

async def sm_height(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["height"] = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Введи число (наприклад: 175):")
        return SM_HEIGHT
    rows = [[InlineKeyboardButton(f"{k}. {v[0]}", callback_data=f"sm:act:{k}")]
            for k, v in ACTIVITY_FACTORS.items()]
    await update.message.reply_text("Крок 7: Рівень активності:", reply_markup=InlineKeyboardMarkup(rows))
    return SM_ACTIVITY

async def sm_activity_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["activity"] = query.data.split(":")[2]
    rows = [
        [InlineKeyboardButton("📉 Схуднення -300 ккал",  callback_data="sm:goal:scheme_300")],
        [InlineKeyboardButton("📉 Схуднення -500 ккал",  callback_data="sm:goal:scheme_500")],
        [InlineKeyboardButton("⚖️ Підтримка",             callback_data="sm:goal:keep")],
        [InlineKeyboardButton("📈 Набір маси +300 ккал",  callback_data="sm:goal:gain")],
    ]
    await query.edit_message_text("Крок 8: Ціль клієнта:", reply_markup=InlineKeyboardMarkup(rows))
    return SM_GOAL

async def sm_goal_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["goal"] = query.data.split(":")[2]
    if "scheme" in ctx.user_data["goal"]:
        await query.edit_message_text(
            "Крок 9: Цільова вага клієнта (кг) — для розрахунку білка.\n"
            "<i>Введи 0 якщо рахувати від поточної ваги</i>",
            parse_mode="HTML",
        )
        return SM_TARGET_WEIGHT
    else:
        ctx.user_data["target_weight"] = None
        rows = [[InlineKeyboardButton(f"{n} прийоми", callback_data=f"sm:meals:{n}") for n in (3, 4, 5)]]
        await query.edit_message_text("Крок 9: Кількість прийомів їжі:", reply_markup=InlineKeyboardMarkup(rows))
        return SM_MEALS_COUNT

async def sm_target_weight(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        tw = float(update.message.text.strip().replace(",", "."))
        ctx.user_data["target_weight"] = tw if tw > 0 else None
    except ValueError:
        ctx.user_data["target_weight"] = None
    rows = [[InlineKeyboardButton(f"{n} прийоми", callback_data=f"sm:meals:{n}") for n in (3, 4, 5)]]
    await update.message.reply_text("Крок 10: Кількість прийомів їжі:", reply_markup=InlineKeyboardMarkup(rows))
    return SM_MEALS_COUNT

async def sm_meals_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["meals_count"] = int(query.data.split(":")[2])

    d    = ctx.user_data
    calc = _calc_kbzv(d["sex"], d["age"], d["weight"], d["height"],
                      d["activity"], d["goal"], d.get("target_weight"))
    ctx.user_data["calc"] = calc

    goal_label     = GOAL_ADJUSTMENTS[d["goal"]][0]
    activity_label = ACTIVITY_FACTORS[d["activity"]][0]
    meals          = _distribute_meals(calc, d["meals_count"])
    ctx.user_data["meals_preview"] = meals

    meals_text = "\n".join(
        f"  • {m['name']}: <b>{m['kcal']}</b> ккал | Б{m['p']} Ж{m['f']} В{m['c']}"
        for m in meals
    )
    await query.edit_message_text(
        f"📊 <b>Розрахунок для {d['display_name']}</b>\n\n"
        f"Вік: {d['age']} р · Вага: {d['weight']} кг · Зріст: {d['height']} см\n"
        f"Активність: {activity_label}\n"
        f"Ціль: {goal_label}\n\n"
        f"<b>КБЖВ:</b> {calc['kcal']} ккал | Б{calc['protein']} Ж{calc['fat']} В{calc['carbs']}г\n\n"
        f"<b>Прийоми:</b>\n{meals_text}\n\n"
        f"Підтвердити або змінити калорії?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Підтвердити", callback_data="sm:confirm")],
            [InlineKeyboardButton("✏️ Змінити ккал", callback_data="sm:editkcal")],
        ]),
    )
    return SM_CONFIRM

async def sm_confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "sm:editkcal":
        await query.edit_message_text(
            f"Введи свою кількість калорій (наприклад: <code>2200</code>):",
            parse_mode="HTML",
        )
        return SM_CONFIRM
    # підтверджено — питаємо кроки
    await query.edit_message_text(
        "Крок 10: Ціль по кроках на день (наприклад: <code>8000</code>):",
        parse_mode="HTML",
    )
    return SM_STEPS

async def sm_edit_kcal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        new_kcal = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи число (наприклад: 2200):")
        return SM_CONFIRM
    old = ctx.user_data["calc"]
    ratio = new_kcal / old["kcal"] if old["kcal"] else 1
    ctx.user_data["calc"] = {
        "kcal":    new_kcal,
        "protein": round(old["protein"] * ratio),
        "fat":     round(old["fat"]     * ratio),
        "carbs":   round(old["carbs"]   * ratio),
    }
    meals = _distribute_meals(ctx.user_data["calc"], ctx.user_data["meals_count"])
    ctx.user_data["meals_preview"] = meals
    meals_text = "\n".join(
        f"  • {m['name']}: <b>{m['kcal']}</b> ккал | Б{m['p']} Ж{m['f']} В{m['c']}"
        for m in meals
    )
    d = ctx.user_data["calc"]
    await update.message.reply_text(
        f"<b>Оновлено:</b> {d['kcal']} ккал | Б{d['protein']} Ж{d['fat']} В{d['carbs']}г\n\n"
        f"<b>Прийоми:</b>\n{meals_text}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Підтвердити", callback_data="sm:confirm")],
            [InlineKeyboardButton("✏️ Змінити ккал", callback_data="sm:editkcal")],
        ]),
    )
    return SM_CONFIRM

async def sm_steps(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        steps = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи число (наприклад: 8000):")
        return SM_STEPS

    d    = ctx.user_data
    calc = d["calc"]
    client = {
        "slug":         d["slug"],
        "name":         d["slug"],
        "display_name": d["display_name"],
        "telegram_id":  d.get("telegram_id"),
        "daily":        calc,
        "steps":        steps,
        "meals":        d["meals_preview"],
    }
    _save_client(client, push=True)
    invite = _build_invite_link(d["slug"])
    meals_text = "\n".join(
        f"  {m['name']}: {m['kcal']} ккал | Б{m['p']} Ж{m['f']} В{m['c']}"
        for m in d["meals_preview"]
    )
    await update.message.reply_text(
        f"✅ <b>{d['display_name']}</b> створено!\n\n"
        f"КБЖВ: {calc['kcal']} ккал | Б{calc['protein']} Ж{calc['fat']} В{calc['carbs']}г\n"
        f"Кроки: {steps}\n\n"
        f"Прийоми:\n{meals_text}\n\n"
        f"Invite:\n<code>{invite}</code>",
        parse_mode="HTML",
        reply_markup=ADMIN_KB,
    )
    ctx.user_data.clear()
    return ConversationHandler.END

async def sm_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("Скасовано.", reply_markup=ADMIN_KB)
    return ConversationHandler.END

# ── Покрокове додавання страви в базу ────────────────────────────────────────

async def cmd_add_dish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return ConversationHandler.END
    ctx.user_data.clear()
    rows = [[InlineKeyboardButton(
        f"{MEAL_EMOJI[mt]} {mt.capitalize()}", callback_data=f"da:type:{mt}"
    )] for mt in MEAL_TYPES]
    await update.message.reply_text(
        "🍽 <b>Нова страва</b>\n\nОбери тип прийому:", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return DA_TYPE

async def da_type_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["da_type"] = query.data.split(":")[2]
    await query.edit_message_text("Назва страви (наприклад: <i>Вівсянка з ягодами</i>):", parse_mode="HTML")
    return DA_NAME

async def da_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["da_name"] = update.message.text.strip()
    await update.message.reply_text("Калорійність (ккал, тільки число):")
    return DA_KCAL

async def da_kcal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["da_kcal"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи число (наприклад: 450):")
        return DA_KCAL
    await update.message.reply_text("Білок (г):")
    return DA_P

async def da_p(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["da_p"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи число:")
        return DA_P
    await update.message.reply_text("Жир (г):")
    return DA_F

async def da_f(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["da_f"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи число:")
        return DA_F
    await update.message.reply_text("Вуглеводи (г):")
    return DA_C

async def da_c(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["da_c"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи число:")
        return DA_C
    await update.message.reply_text(
        "Інгредієнти — кожен з нового рядка:\n\n"
        "<i>Наприклад:\n150г вівсянки\n100г ягід\n1 ч.л. меду</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Пропустити", callback_data="da:skip:ingredients")
        ]]),
    )
    return DA_INGREDIENTS

async def da_ingredients(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = [l.strip() for l in update.message.text.splitlines() if l.strip()]
    ctx.user_data["da_ingredients"] = lines
    await update.message.reply_text(
        "Кроки рецепту — кожен з нового рядка:\n\n"
        "<i>Наприклад:\n1. Зварити вівсянку\n2. Додати ягоди</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Пропустити", callback_data="da:skip:steps")
        ]]),
    )
    return DA_STEPS

async def da_steps(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = [l.strip() for l in update.message.text.splitlines() if l.strip()]
    ctx.user_data["da_steps"] = lines
    await update.message.reply_text(
        "Нотатка (необов'язково):",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Пропустити", callback_data="da:skip:note")
        ]]),
    )
    return DA_NOTE

async def da_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["da_note"] = update.message.text.strip()
    await update.message.reply_text(
        "Надішли фото страви:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Без фото", callback_data="da:skip:photo")
        ]]),
    )
    return DA_PHOTO

async def da_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    photo  = update.message.photo[-1]
    file   = await ctx.bot.get_file(photo.file_id)
    mt     = ctx.user_data.get("da_type", "обід")
    db     = _load_variants()
    idx    = len(db.get(mt, []))
    fname  = f"{mt}_{idx}_{photo.file_unique_id}.jpg"
    await file.download_to_drive(Path(fname))
    ctx.user_data["da_photo"] = fname
    return await _da_save(update, ctx)

async def da_skip_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split(":")[2]
    if field == "ingredients":
        ctx.user_data["da_ingredients"] = []
        await query.edit_message_text(
            "Кроки рецепту — кожен з нового рядка:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Пропустити", callback_data="da:skip:steps")
            ]]),
        )
        return DA_STEPS
    elif field == "steps":
        ctx.user_data["da_steps"] = []
        await query.edit_message_text(
            "Нотатка (необов'язково):",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Пропустити", callback_data="da:skip:note")
            ]]),
        )
        return DA_NOTE
    elif field == "note":
        ctx.user_data["da_note"] = None
        await query.edit_message_text(
            "Надішли фото страви:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Без фото", callback_data="da:skip:photo")
            ]]),
        )
        return DA_PHOTO
    elif field == "photo":
        ctx.user_data["da_photo"] = None
        return await _da_save(update, ctx)

async def _da_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d  = ctx.user_data
    mt = d.get("da_type", "обід")
    variant = {
        "name":        d["da_name"],
        "kcal":        d["da_kcal"],
        "p":           d["da_p"],
        "f":           d["da_f"],
        "c":           d["da_c"],
        "ingredients": d.get("da_ingredients", []),
        "steps":       d.get("da_steps", []),
        "note":        d.get("da_note"),
        "photo":       d.get("da_photo"),
    }
    db = _load_variants()
    if mt not in db:
        db[mt] = []
    db[mt].append(variant)
    _save_variants(db)
    ctx.user_data.clear()

    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(
        f"✅ <b>{variant['name']}</b> додано до бази ({mt})!\n"
        f"{variant['kcal']} ккал | Б{variant['p']} Ж{variant['f']} В{variant['c']}г",
        parse_mode="HTML",
        reply_markup=ADMIN_KB,
    )
    return ConversationHandler.END

async def da_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("Скасовано.", reply_markup=ADMIN_KB)
    return ConversationHandler.END

# ── /fast wizard ──────────────────────────────────────────────────────────────

async def cmd_fast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return ConversationHandler.END
    ctx.user_data.clear()
    await update.message.reply_text(
        "➕ <b>Новий клієнт</b>\n\nКрок 1: Ім'я клієнта:", parse_mode="HTML"
    )
    return FAST_NAME

async def fast_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["display_name"] = update.message.text.strip()
    ctx.user_data["slug"] = re.sub(r"[^a-zA-Z0-9_]", "_", ctx.user_data["display_name"].lower())
    await update.message.reply_text(
        "Крок 2: Telegram ID клієнта?\n<i>Введи 0 якщо не знаєш</i>", parse_mode="HTML"
    )
    return FAST_TG_ID

async def fast_tg_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        tg_id = int(update.message.text.strip())
    except ValueError:
        tg_id = 0
    ctx.user_data["telegram_id"] = tg_id or None
    await update.message.reply_text(
        "Крок 3: Вставте повний текст варіантів з підсумком.\n\n"
        "<b>Формат:</b>\n"
        "<code>СНІДАНОК\n---\nВаріант 1: Назва\nінгредієнт\nРазом: 550 ккал | 45Б / 18Ж / 53В\n---\n\n"
        "ОБІД\n---\n...\n\n"
        "ПІДСУМОК (ДЛЯ ВАРІАНТІВ №1):\n"
        "Калорії: 2400 ккал | Б: 188 г | Ж: 80 г | В: 227 г</code>",
        parse_mode="HTML",
    )
    return FAST_RATION

async def fast_steps(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pass  # не використовується

async def fast_ration(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    result = _parse_variants_text(text)
    if not result:
        await update.message.reply_text(
            "❌ Не вдалося розпізнати варіанти. Перевір формат і спробуй ще раз або /cancel",
            parse_mode="HTML",
        )
        return FAST_RATION

    display_name = ctx.user_data["display_name"]
    slug         = ctx.user_data["slug"]

    # Підтягуємо steps/note/photo з глобальної бази
    global_db = _load_variants()
    global_by_name = {}
    for mt_list in global_db.values():
        for gv in mt_list:
            global_by_name[gv["name"].strip().lower()] = gv
    for var_list in result["variants"].values():
        for v in var_list:
            gv = global_by_name.get(v["name"].strip().lower())
            if gv:
                if gv.get("steps"): v["steps"] = gv["steps"]
                if gv.get("note"):  v["note"]  = gv["note"]
                if gv.get("photo"): v["photo"]  = gv["photo"]

    # Будуємо meals з meals_order
    new_meals = []
    for i, mo in enumerate(result["meals_order"]):
        n = len(result["meals_order"])
        time = MEAL_DEFAULTS[n][i][1] if n in MEAL_DEFAULTS and i < len(MEAL_DEFAULTS[n]) else ""
        new_meals.append({
            "name": mo["label"], "time": time,
            "kcal": mo["kcal"], "p": mo["p"], "f": mo["f"], "c": mo["c"],
        })

    daily = result["daily"] or {
        "kcal":    sum(m["kcal"] for m in new_meals),
        "protein": sum(m["p"]    for m in new_meals),
        "fat":     sum(m["f"]    for m in new_meals),
        "carbs":   sum(m["c"]    for m in new_meals),
    }

    client = {
        "slug":         slug,
        "name":         slug,
        "display_name": display_name,
        "telegram_id":  ctx.user_data.get("telegram_id"),
        "daily":        daily,
        "steps":        10000,
        "meals":        new_meals,
        "variants":     result["variants"],
    }
    _save_client(client, push=True)
    invite = _build_invite_link(slug)
    d = client["daily"]
    meals_text = "\n".join(
        f"  {m['name']}: {m['kcal']} ккал | Б{m['p']} Ж{m['f']} В{m['c']}"
        for m in new_meals
    )
    total_vars = sum(len(v) for v in result["variants"].values())
    await update.message.reply_text(
        f"✅ <b>{display_name}</b> створено!\n\n"
        f"КБЖВ: {d['kcal']} ккал | Б{d['protein']} Ж{d['fat']} В{d['carbs']}г\n"
        f"Прийоми:\n{meals_text}\n"
        f"Варіантів: {total_vars}\n\n"
        f"Invite для клієнта:\n<code>{invite}</code>",
        parse_mode="HTML",
    )
    ctx.user_data.clear()
    return ConversationHandler.END

# ── Кнопки клавіатури ─────────────────────────────────────────────────────────

async def handle_menu_buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    text = update.message.text
    if text == "👥 Клієнти":
        return await cmd_clients(update, ctx)
    elif text == "📤 Надіслати план":
        return await cmd_send(update, ctx)
    elif text == "✏️ Редагувати":
        return await cmd_edit(update, ctx)
    elif text == "🍽 Варіанти страв":
        return await cmd_variants_menu(update, ctx)

# ── Фото варіанту ────────────────────────────────────────────────────────────

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    if ctx.user_data.get("vg_state") != "askphoto":
        return
    mt  = ctx.user_data.get("vg_edit_mt")
    idx = ctx.user_data.get("vg_edit_idx")
    if not mt or idx is None:
        return

    photo = update.message.photo[-1]  # найбільший розмір
    file = await ctx.bot.get_file(photo.file_id)
    filename = f"{mt}_{idx}_{photo.file_unique_id}.jpg"
    filepath = Path(filename)
    await file.download_to_drive(filepath)

    db = _load_variants()
    db[mt][idx]["photo"] = filename
    _save_variants(db)
    ctx.user_data.pop("vg_state", None)

    await update.message.reply_text(
        f"✅ Фото збережено! Додати рецепт?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Додати рецепт", callback_data=f"vg:asksteps:{mt}:{idx}"),
            InlineKeyboardButton("⏭ Пропустити",    callback_data=f"vg:type:{mt}"),
        ]]),
    )

# ── Error handler ─────────────────────────────────────────────────────────────

async def _error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error("Помилка: %s", ctx.error, exc_info=ctx.error)

# ── Bootstrap ─────────────────────────────────────────────────────────────────

async def _daily_expiry_check(ctx: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    lines = []
    for c in _all_clients():
        exp = c.get("expires_at")
        if not exp:
            continue
        days_left = (date.fromisoformat(exp) - today).days
        name = _display(c)
        if days_left == 0:
            lines.append(f"🔴 <b>{name}</b> — термін закінчується сьогодні!")
            c["active"] = False
            _save_client(c)
        elif days_left < 0:
            lines.append(f"⛔ <b>{name}</b> — термін вийшов {exp}, доступ відключено")
            c["active"] = False
            _save_client(c)
        elif days_left <= 3:
            lines.append(f"🟡 <b>{name}</b> — залишилось {days_left} дн. (до {exp})")
    if lines:
        await ctx.bot.send_message(
            OWNER_ID,
            "⏰ <b>Нагадування про терміни доступу:</b>\n\n" + "\n".join(lines),
            parse_mode="HTML",
        )


def _start_dummy_server():
    """Відкриває порт щоб Render не вбивав Web Service."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args): pass
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Dummy HTTP server started on port %s", port)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("NUTRITION_BOT_TOKEN не задано в .env")
    _start_dummy_server()

    # Зачищаємо вебхук і чекаємо поки старий інстанс відпустить getUpdates
    import time
    try:
        _requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook",
            json={"drop_pending_updates": True},
            timeout=10,
        )
    except Exception:
        pass
    time.sleep(15)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )
    app.add_error_handler(_error_handler)

    nc_conv = ConversationHandler(
        entry_points=[CommandHandler("newclient", cmd_newclient)],
        states={
            NC_NAME:        [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_name)],
            NC_TG_ID:       [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_tg_id)],
            NC_MEALS_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_meals_count)],
            NC_DAILY:       [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_daily)],
            NC_STEPS:       [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_steps)],
            NC_MEAL_0:      [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_meal_0)],
            NC_MEAL_1:      [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_meal_1)],
            NC_MEAL_2:      [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_meal_2)],
            NC_MEAL_3:      [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_meal_3)],
            NC_MEAL_4:      [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_meal_4)],
        },
        fallbacks=[CommandHandler("cancel", nc_cancel)],
        per_chat=True, per_message=False,
    )

    fast_conv = ConversationHandler(
        entry_points=[
            CommandHandler("fast", cmd_fast),
        ],
        states={
            FAST_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, fast_name)],
            FAST_TG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, fast_tg_id)],
            FAST_RATION:[MessageHandler(filters.TEXT & ~filters.COMMAND, fast_ration)],
        },
        fallbacks=[CommandHandler("cancel", nc_cancel)],
        per_chat=True, per_message=False,
    )

    smart_conv = ConversationHandler(
        entry_points=[
            CommandHandler("smart", cmd_smart),
            MessageHandler(filters.TEXT & filters.Regex(r"^➕ Новий клієнт$"), cmd_smart),
        ],
        states={
            SM_NAME:        [MessageHandler(filters.TEXT & ~filters.COMMAND, sm_name)],
            SM_TG_ID:       [MessageHandler(filters.TEXT & ~filters.COMMAND, sm_tg_id)],
            SM_SEX:         [CallbackQueryHandler(sm_sex_cb, pattern=r"^sm:sex:")],
            SM_AGE:         [MessageHandler(filters.TEXT & ~filters.COMMAND, sm_age)],
            SM_WEIGHT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, sm_weight)],
            SM_HEIGHT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, sm_height)],
            SM_ACTIVITY:      [CallbackQueryHandler(sm_activity_cb,   pattern=r"^sm:act:")],
            SM_GOAL:          [CallbackQueryHandler(sm_goal_cb,       pattern=r"^sm:goal:")],
            SM_TARGET_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, sm_target_weight)],
            SM_MEALS_COUNT:   [CallbackQueryHandler(sm_meals_cb,      pattern=r"^sm:meals:")],
            SM_CONFIRM:     [
                CallbackQueryHandler(sm_confirm_cb, pattern=r"^sm:confirm$"),
                CallbackQueryHandler(sm_confirm_cb, pattern=r"^sm:editkcal$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, sm_edit_kcal),
            ],
            SM_STEPS: [MessageHandler(filters.TEXT & ~filters.COMMAND, sm_steps)],
        },
        fallbacks=[CommandHandler("cancel", sm_cancel)],
        per_chat=True, per_message=False, per_user=True,
    )

    add_dish_conv = ConversationHandler(
        entry_points=[CommandHandler("adddish", cmd_add_dish)],
        states={
            DA_TYPE:        [CallbackQueryHandler(da_type_cb,   pattern=r"^da:type:")],
            DA_NAME:        [MessageHandler(filters.TEXT & ~filters.COMMAND, da_name)],
            DA_KCAL:        [MessageHandler(filters.TEXT & ~filters.COMMAND, da_kcal)],
            DA_P:           [MessageHandler(filters.TEXT & ~filters.COMMAND, da_p)],
            DA_F:           [MessageHandler(filters.TEXT & ~filters.COMMAND, da_f)],
            DA_C:           [MessageHandler(filters.TEXT & ~filters.COMMAND, da_c)],
            DA_INGREDIENTS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, da_ingredients),
                CallbackQueryHandler(da_skip_cb, pattern=r"^da:skip:ingredients$"),
            ],
            DA_STEPS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, da_steps),
                CallbackQueryHandler(da_skip_cb, pattern=r"^da:skip:steps$"),
            ],
            DA_NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, da_note),
                CallbackQueryHandler(da_skip_cb, pattern=r"^da:skip:note$"),
            ],
            DA_PHOTO: [
                MessageHandler(filters.PHOTO, da_photo),
                CallbackQueryHandler(da_skip_cb, pattern=r"^da:skip:photo$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", da_cancel)],
        per_chat=True, per_message=False, per_user=True,
    )

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("myid",      cmd_myid))
    app.add_handler(CommandHandler("setid",     cmd_setid))
    app.add_handler(CommandHandler("clients",   cmd_clients))
    app.add_handler(CommandHandler("send",      cmd_send))
    app.add_handler(CommandHandler("edit",      cmd_edit))

    app.add_handler(nc_conv)
    app.add_handler(fast_conv)
    app.add_handler(smart_conv)
    app.add_handler(add_dish_conv)
    app.add_handler(CallbackQueryHandler(cb_handler))

    menu_filter = filters.TEXT & filters.Regex(
        r"^(👥 Клієнти|📤 Надіслати план|✏️ Редагувати|🍽 Варіанти страв)$"
    )
    app.add_handler(MessageHandler(menu_filter, handle_menu_buttons))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Щоденна перевірка термінів о 09:00
    app.job_queue.run_daily(
        _daily_expiry_check,
        time=__import__("datetime").time(9, 0),
        name="expiry_check",
    )

    logger.info("Бот запущено. Owner ID: %s", OWNER_ID)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
