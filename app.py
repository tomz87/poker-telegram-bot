import os
import time
import requests
import sys
from flask import Flask, request, jsonify

app = Flask(__name__)

# =========================
# Config
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("8518103041:AAGwbs3RfKSZRly39cNH-pXEpKlvDAhYW1A")
TELEGRAM_GROUP_ID = os.getenv("-1003587001321")
TRIGGER_SECRET = os.getenv("shalmanimPoker2026")
ADMIN_USER_ID = int(os.environ.get("841949601", "0") or "0")

TOKEN = os.environ.get("8518103041:AAGwbs3RfKSZRly39cNH-pXEpKlvDAhYW1A", "").strip()
if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env var")

API_URL = f"https://api.telegram.org/bot{TOKEN}"

# הגנה לטריגרים (GitHub Actions / Cron)
TRIGGER_SECRET = os.environ.get("shalmanimPoker2026", "").strip()

# אם יש לך קבוצה קבועה שאתה רוצה לעבוד איתה, שים פה את ה-ID שלה (מספר שלילי)
GROUP_ID = int(os.environ.get("-1003587001321", "0"))
#ADMIN_USER_ID = int(os.environ.get("841949601", "0") or "0")

# רשימת הימים (לפי מה שביקשת: רביעי מושב + רביעי אביב)
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
# In-memory "DB" (Render Free resets on restart)
# =========================
HOSTS = {}  # user_id -> {"username": str, "first_name": str, "days": set(str), "added_at": int}
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
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    payload["show_alert"] = show_alert
    return requests.post(f"{API_URL}/answerCallbackQuery", json=payload, timeout=20)

def tg_edit_message(chat_id: int, message_id: int, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return requests.post(f"{API_URL}/editMessageText", json=payload, timeout=20)

def build_days_keyboard(selected_days: set):
    # Toggle buttons + "סיימתי"
    rows = []
    for day in DAY_OPTIONS:
        is_on = day in selected_days
        label = f"✅ {day}" if is_on else f"⬜ {day}"
        rows.append([{
            "text": label,
            "callback_data": f"hostday|toggle|{day}"
        }])
    rows.append([{
        "text": "✅ סיימתי",
        "callback_data": "hostday|done"
    }])
    return {"inline_keyboard": rows}

def normalize_username(u: str):
    if not u:
        return ""
    return u.strip().lstrip("@")

# =========================
# Core logic
# =========================
def handle_addhost_private(msg):
    user = msg.get("from", {}) or {}
    chat = msg.get("chat", {}) or {}
    chat_id = chat.get("id")

    user_id = user.get("id")
    username = normalize_username(user.get("username", ""))
    first_name = user.get("first_name", "")

    # רישום בסיסי בזיכרון
    if user_id not in HOSTS:
        HOSTS[user_id] = {
            "username": username,
            "first_name": first_name,
            "days": set(),
            "added_at": int(time.time()),
        }

    # פותחים תהליך בחירת ימים
    PENDING[user_id] = {"days": set(HOSTS[user_id]["days"])}

    text = (
        "מעולה! נרשמת כמארח.\n\n"
        "בחר ימים שנוחים לך לארח (אפשר לבחור כמה). ואז לחץ ✅ סיימתי."
    )
    keyboard = build_days_keyboard(PENDING[user_id]["days"])
    tg_send_message(chat_id, text, reply_markup=keyboard)

def handle_message(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    chat = msg.get("chat", {}) or {}
    chat_id = chat.get("id")
    chat_type = chat.get("type", "")
    text = (msg.get("text") or "").strip()

    # DEBUG
    print("MESSAGE:", {"chat_id": chat_id, "type": chat_type, "text": text})

    # /addhost רק בפרטי
    if text.startswith("/addhost") and chat_type == "private":
        handle_addhost_private(msg)
        return

    # דוגמה: פינג בקבוצה בלבד
    if text == "/ping" and chat_type in ("group", "supergroup"):
        tg_send_message(chat_id, "🏓 pong (קבוצה)")
        return

    # ברירת מחדל
    if chat_type == "private" and text:
        tg_send_message(chat_id, "קיבלתי ✅\nרשום /addhost כדי להירשם כמארח.")

def handle_callback(update):
    cb = update.get("callback_query") or {}
    cb_id = cb.get("id")
    data = (cb.get("data") or "").strip()
    msg = cb.get("message") or {}
    chat = msg.get("chat", {}) or {}
    chat_id = chat.get("id")
    message_id = msg.get("message_id")
    from_user = cb.get("from", {}) or {}
    user_id = from_user.get("id")

    # DEBUG
    print("CALLBACK:", {"user_id": user_id, "data": data})

    if not cb_id or not data:
        return

    parts = data.split("|")
    if len(parts) < 2:
        tg_answer_callback_query(cb_id)
        return

    if parts[0] != "hostday":
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
        text = (
            "בחר ימים שנוחים לך לארח (אפשר לבחור כמה).\n"
            "בסוף לחץ ✅ סיימתי."
        )
        tg_edit_message(chat_id, message_id, text, reply_markup=keyboard)
        tg_answer_callback_query(cb_id)
        return

    if parts[1] == "done":
        chosen = PENDING[user_id]["days"]
        # שמירה ל-hosts
        if user_id not in HOSTS:
            HOSTS[user_id] = {"username": "", "first_name": "", "days": set(), "added_at": int(time.time())}
        HOSTS[user_id]["days"] = set(chosen)

        # סגירת pending
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

    # handle callback clicks
    if "callback_query" in update:
        handle_callback(update)
        return "OK", 200

    # handle messages
    handle_message(update)
    return "OK", 200

# טריגרים מה־GitHub Actions (ללא DB)
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

    # כרגע רק דוגמה — פה תשים את הלוגיקה שלך
    # לדוגמה: לשלוח לכל המארחים בפרטי כפתורי ימים / או לפרסם סקר בקבוצה וכו'
    print("TRIGGER ACTION:", action)

    return jsonify({"ok": True, "action": action}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
