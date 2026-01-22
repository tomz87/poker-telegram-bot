import os
import time
import hmac
import hashlib
import requests
from flask import Flask, request, abort

app = Flask(__name__)

# ===== ENV =====
TOKEN = os.environ.get("8518103041:AAGwbs3RfKSZRly39cNH-pXEpKlvDAhYW1A", "")
API_URL = f"https://api.telegram.org/bot{8518103041:AAGwbs3RfKSZRly39cNH-pXEpKlvDAhYW1A}"
GROUP_ID = int(os.environ.get("-1003587001321", "0"))
ADMIN_ID = int(os.environ.get("TELEGRAM_ADMIN_ID", "0"))
TRIGGER_SECRET = os.environ.get("TRIGGER_SECRET", "")  # random long string

DEFAULT_TIME = "21:00"

# ===== In-memory store (MVP) =====
# NOTE: On redeploy/restart, memory resets. For MVP OK.
HOSTS = {}  # host_user_id -> {"name": str}
# availability: key = f"{week_key()}|{window}" ; value: host_user_id -> set(day_label)
AVAIL = {}

def tg(method: str, payload: dict):
    return requests.post(f"{API_URL}/{method}", json=payload, timeout=15)

def week_key():
    # ISO week key
    return time.strftime("%G-W%V")

def get_avail_key(window: str):
    return f"{week_key()}|{window}"

def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID

def is_group_chat(chat_id: int) -> bool:
    return chat_id == GROUP_ID

def verify_trigger(req) -> bool:
    # We require header: X-Trigger-Token = HMAC_SHA256(secret, body)
    if not TRIGGER_SECRET:
        return False
    sig = req.headers.get("X-Trigger-Token", "")
    body = req.get_data() or b""
    mac = hmac.new(TRIGGER_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, mac)

def days_for_window(window: str):
    # WS = Wed-Thu-Fri-Sat, SU = Sun-Mon-Tue
    if window == "WS":
        return ["ד׳", "ה׳", "ו׳", "שבת"]
    if window == "SU":
        return ["א׳", "ב׳", "ג׳"]
    return []

def build_days_keyboard(window: str):
    # Each day button toggles selection; plus Done/Cancel
    rows = []
    for d in days_for_window(window):
        rows.append([{"text": d, "callback_data": f"DAY|{window}|{d}"}])
    rows.append([
        {"text": "✅ סיימתי", "callback_data": f"DONE|{window}"},
        {"text": "❌ לא יכול השבוע", "callback_data": f"CANCEL|{window}"},
    ])
    return {"inline_keyboard": rows}

def host_prompt_text(host_name: str, window: str):
    if window == "WS":
        win_text = "רביעי–שבת"
    else:
        win_text = "ראשון–שלישי"
    return (
        f"שלום {host_name} 👋\n"
        f"האם אתה יכול לארח השבוע? ({win_text})\n"
        f"שעה ברירת מחדל: {DEFAULT_TIME}\n"
        f"בחר ימים בכפתורים למטה:"
    )

@app.get("/")
def health():
    return "OK"

@app.post("/webhook")
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    print("UPDATE:", update)

    # 1) Handle callback_query (button presses)
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        cb_id = cq.get("id")
        from_user = cq.get("from", {}) or {}
        user_id = int(from_user.get("id", 0))

        if not user_id:
            tg("answerCallbackQuery", {"callback_query_id": cb_id, "text": "שגיאה"})
            return "OK", 200

        # Parse
        # DAY|WS|ד׳
        # DONE|WS
        # CANCEL|WS
        parts = data.split("|")
        action = parts[0] if parts else ""
        window = parts[1] if len(parts) >= 2 else ""
        key = get_avail_key(window)

        # init
        AVAIL.setdefault(key, {})
        AVAIL[key].setdefault(user_id, set())

        if action == "DAY" and len(parts) == 3:
            day = parts[2]
            AVAIL[key][user_id].add(day)
            tg("answerCallbackQuery", {"callback_query_id": cb_id, "text": f"נרשם: {day}"})
            return "OK", 200

        if action == "CANCEL":
            # clear selections
            AVAIL[key][user_id] = set()
            tg("answerCallbackQuery", {"callback_query_id": cb_id, "text": "סומן: לא יכול השבוע"})
            return "OK", 200

        if action == "DONE":
            chosen = sorted(list(AVAIL[key][user_id]))
            msg = "קיבלתי 🙏 ימים שנבחרו: " + (", ".join(chosen) if chosen else "—")
            # Notify user in private (callback message has chat_id, but easiest: send to user_id)
            tg("sendMessage", {"chat_id": user_id, "text": msg})
            tg("answerCallbackQuery", {"callback_query_id": cb_id, "text": "תודה!"})
            return "OK", 200

        tg("answerCallbackQuery", {"callback_query_id": cb_id, "text": "לא זוהה"})
        return "OK", 200

    # 2) Handle normal messages
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = int(chat.get("id", 0))
        chat_type = chat.get("type", "")
        text = (msg.get("text") or "").strip()

        from_user = msg.get("from", {}) or {}
        user_id = int(from_user.get("id", 0))
        full_name = ((from_user.get("first_name", "") + " " + from_user.get("last_name", "")).strip()
                     or from_user.get("first_name", "") or "חבר")

        # /start in private: just welcome
        if text == "/start" and chat_type == "private":
            tg("sendMessage", {"chat_id": chat_id, "text": "היי! כדי להירשם כמארח, תן לאדמין לעשות Forward של הודעה שלך לבוט."})
            return "OK", 200

        # /whoami
        if text == "/whoami":
            tg("sendMessage", {"chat_id": chat_id, "text": f"user_id שלך: {user_id}"})
            return "OK", 200

        # Admin flow: add host by forwarding any message from them (in private)
        if chat_type == "private" and is_admin(user_id) and "forward_from" in msg:
            f = msg["forward_from"]
            hid = int(f.get("id", 0))
            hn = ((f.get("first_name", "") + " " + f.get("last_name", "")).strip()
                  or f.get("first_name", "") or "מארח")
            if hid:
                HOSTS[hid] = {"name": hn}
                tg("sendMessage", {"chat_id": chat_id, "text": f"✅ נוסף מארח: {hn} (id={hid})"})
            return "OK", 200

        # Admin commands (private)
        if chat_type == "private" and is_admin(user_id):
            if text == "/listhosts":
                if not HOSTS:
                    tg("sendMessage", {"chat_id": chat_id, "text": "אין מארחים עדיין. Forward הודעה ממארח לבוט כדי להוסיף."})
                else:
                    lines = [f"- {meta['name']} (id={hid})" for hid, meta in HOSTS.items()]
                    tg("sendMessage", {"chat_id": chat_id, "text": "מארחים:\n" + "\n".join(lines)})
                return "OK", 200

            if text in ("/ask_ws", "/ask_su"):
                window = "WS" if text == "/ask_ws" else "SU"
                sent = send_host_prompts(window)
                tg("sendMessage", {"chat_id": chat_id, "text": f"נשלח למארחים. חלון {window}. נשלח ל-{sent} מארחים."})
                return "OK", 200

            if text in ("/poll_ws", "/poll_su"):
                window = "WS" if text == "/poll_ws" else "SU"
                ok = post_group_poll(window)
                tg("sendMessage", {"chat_id": chat_id, "text": "✅ סקר פורסם בקבוצה" if ok else "לא היה מה לפרסם (אין הצעות מארחים)"} )
                return "OK", 200

        # Group ping test
        if text == "/ping" and is_group_chat(chat_id):
            tg("sendMessage", {"chat_id": chat_id, "text": "🏓 pong"})
            return "OK", 200

    return "OK", 200

def send_host_prompts(window: str) -> int:
    if not HOSTS:
        return 0
    kb = build_days_keyboard(window)
    count = 0
    for hid, meta in HOSTS.items():
        tg("sendMessage", {
            "chat_id": hid,
            "text": host_prompt_text(meta["name"], window),
            "reply_markup": kb
        })
        count += 1
    return count

def build_options_from_avail(window: str):
    key = get_avail_key(window)
    if key not in AVAIL:
        return []
    options = []
    for hid, days in AVAIL[key].items():
        if not days:
            continue
        host_name = HOSTS.get(hid, {}).get("name", f"Host {hid}")
        for d in sorted(days):
            options.append(f"{d} {DEFAULT_TIME} – {host_name}")
    # Telegram poll allows 2–10 options
    return options[:10]

def post_group_poll(window: str) -> bool:
    options = build_options_from_avail(window)
    if len(options) < 2:
        return False

    question = "מי יכול להגיע למשחק הקרוב? (אפשר לבחור כמה)"  # multi choice
    tg("sendPoll", {
        "chat_id": GROUP_ID,
        "question": question,
        "options": options,
        "is_anonymous": False,
        "allows_multiple_answers": True
    })
    return True

# ===== Trigger endpoints for GitHub Actions =====

@app.post("/trigger/ask/<window>")
def trigger_ask(window: str):
    if not verify_trigger(request):
        abort(403)
    window = window.upper()
    if window not in ("WS", "SU"):
        abort(400)
    send_host_prompts(window)
    return "OK"

@app.post("/trigger/poll/<window>")
def trigger_poll(window: str):
    if not verify_trigger(request):
        abort(403)
    window = window.upper()
    if window not in ("WS", "SU"):
        abort(400)
    post_group_poll(window)
    return "OK"
