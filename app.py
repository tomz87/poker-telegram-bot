import os
import time
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# =========================
# Config (ENV VARS)---
# =========================
TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env var")

API_URL = f"https://api.telegram.org/bot{TOKEN}"

GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID", "0") or "0")         # לדוגמה: -1003587001321
TRIGGER_SECRET = (os.getenv("TRIGGER_SECRET") or "").strip()       # לדוגמה: shalmanimPoker2026
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0") or "0")        # אופציונלי

# רשימת הימים (כמו שביקשת)
DAY_OPTIONS = [
    "ראשון",
    "שני",
    "שלישי",
    "רביעי מושב",
    "רביעי אביב",
    "חמישי",
    "שישי",
    "שבת",
]

# =========================
# In-memory "DB" (ללא DB / ללא דיסק)
# Render Free יתאפס בריסטארט
# =========================
HOSTS = {}    # user_id -> {"username": str, "first_name": str, "days": set(str), "added_at": int}
PENDING = {}  # user_id -> {"days": set(str)}

# =========================
# Helpers: Telegram
# =========================
def tg_send_message(chat_id: int, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return requests.post(f"{API_URL}/sendMessage", json=payload, timeout=20)

def tg_answer_callback_query(callback_query_id: str, text: str = "", show_alert: bool = False):
    payload = {"callback_query_id": callback_query_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    return requests.post(f"{API_URL}/answerCallbackQuery", json=payload, timeout=20)

def tg_edit_message(chat_id: int, message_id: int, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return requests.post(f"{API_URL}/editMessageText", json=payload, timeout=20)

def build_days_keyboard(selected_days: set):
    rows = []
    for day in DAY_OPTIONS:
        is_on = day in selected_days
        label = f"✅ {day}" if is_on else f"⬜ {day}"
        rows.append([{"text": label, "callback_data": f"hostday|toggle|{day}"}])
    rows.append([{"text": "✅ סיימתי", "callback_data": "hostday|done"}])
    return {"inline_keyboard": rows}

def normalize_username(u: str):
    return (u or "").strip().lstrip("@")

# =========================
# Core logic
# =========================
def handle_addhost_private(msg):
    user = msg.get("from") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")

    user_id = user.get("id")
    username = normalize_username(user.get("username"))
    first_name = user.get("first_name") or ""

    if not user_id or not chat_id:
        return

    # רישום בסיסי בזיכרון
    if user_id not in HOSTS:
        HOSTS[user_id] = {
            "username": username,
            "first_name": first_name,
            "days": set(),
            "added_at": int(time.time()),
        }
    else:
        # עדכון שם/יוזר אם השתנה
        HOSTS[user_id]["username"] = username
        HOSTS[user_id]["first_name"] = first_name

    # פתיחת תהליך בחירת ימים
    PENDING[user_id] = {"days": set(HOSTS[user_id]["days"])}

    text = (
        "מעולה! נרשמת כמארח ✅\n\n"
        "בחר ימים שנוחים לך לארח (אפשר לבחור כמה), ואז לחץ ✅ סיימתי."
    )
    keyboard = build_days_keyboard(PENDING[user_id]["days"])
    tg_send_message(chat_id, text, reply_markup=keyboard)

def handle_message(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    chat_type = chat.get("type", "")
    text = (msg.get("text") or "").strip()

    print("MESSAGE:", {"chat_id": chat_id, "type": chat_type, "text": text})

    # /addhost רק בפרטי
    if text.startswith("/addhost") and chat_type == "private":
        handle_addhost_private(msg)
        return

    # פינג בקבוצה
    if text == "/ping" and chat_type in ("group", "supergroup"):
        tg_send_message(chat_id, "🏓 pong (קבוצה)")
        return

    # ברירת מחדל בפרטי
    if chat_type == "private" and text:
        tg_send_message(chat_id, "קיבלתי ✅\nרשום /addhost כדי להירשם כמארח.")

def handle_callback(update):
    cb = update.get("callback_query") or {}
    cb_id = cb.get("id")
    data = (cb.get("data") or "").strip()
    msg = cb.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    message_id = msg.get("message_id")
    from_user = cb.get("from") or {}
    user_id = from_user.get("id")

    print("CALLBACK:", {"user_id": user_id, "data": data})

    if not cb_id or not data or not user_id or not chat_id or not message_id:
        return

    parts = data.split("|")
    if len(parts) < 2 or parts[0] != "hostday":
        tg_answer_callback_query(cb_id)
        return

    # ודא שיש pending
    if user_id not in PENDING:
        PENDING[user_id] = {"days": set(HOSTS.get(user_id, {}).get("days", set()))}

    if parts[1] == "toggle" and len(parts) == 3:
        day = parts[2]
        if day in PENDING[user_id]["days"]:
            PENDING[user_id]["days"].remove(day)
        else:
            PENDING[user_id]["days"].add(day)

        keyboard = build_days_keyboard(PENDING[user_id]["days"])
        text = "בחר ימים שנוחים לך לארח (אפשר לבחור כמה).\nבסוף לחץ ✅ סיימתי."
        tg_edit_message(chat_id, message_id, text, reply_markup=keyboard)
        tg_answer_callback_query(cb_id)
        return

    if parts[1] == "done":
        chosen = PENDING[user_id]["days"]

        if user_id not in HOSTS:
            HOSTS[user_id] = {"username": "", "first_name": "", "days": set(), "added_at": int(time.time())}
        HOSTS[user_id]["days"] = set(chosen)

        PENDING.pop(user_id, None)

        summary = "אין ימים שנבחרו" if not chosen else "הימים שנבחרו:\n- " + "\n- ".join(sorted(chosen))
        tg_edit_message(chat_id, message_id, "נשמר ✅\n\n" + summary)
        tg_answer_callback_query(cb_id, "נשמר!")
        return

    tg_answer_callback_query(cb_id)

# =========================
# Routes
# =========================
@app.get("/")
def health():
    return "OK"

@app.post("/webhook")
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    print("UPDATE:", update)

    if "callback_query" in update:
        handle_callback(update)
        return "OK", 200

    handle_message(update)
    return "OK", 200

# טריגרים מה־GitHub Actions / Cron
@app.post("/trigger/<action>")
def trigger(action: str):
    if not TRIGGER_SECRET:
        return jsonify({"ok": False, "error": "TRIGGER_SECRET not set"}), 500

    token = request.headers.get("X-Trigger-Token", "")
    if token != TRIGGER_SECRET:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    action = (action or "").strip().lower()
    if action not in ("ask_ws", "ask_su", "poll_ws", "poll_su"):
        return jsonify({"ok": False, "error": "unknown action"}), 400

    print("TRIGGER ACTION:", action)
    return jsonify({"ok": True, "action": action}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
