import os
import time
import hmac
import hashlib
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ===== ENV =====
GROUP_ID = int(os.environ.get("-1003587001321", "0"))
TOKEN = os.environ.get("8518103041:AAGwbs3RfKSZRly39cNH-pXEpKlvDAhYW1A", "")
TRIGGER_SECRET = os.environ.get("shalmanimPoker2026", "")
API_URL = f"https://api.telegram.org/bot{TOKEN}"

# --- RAM storage (Option 1) ---
HOSTS = set()  # user_ids who registered as hosts
HOST_AVAIL = {
    "WS": {},  # user_id -> set(days)
    "SU": {},
}
# Put your Telegram user id here to allow admin commands like /listhosts
ADMIN_USER_ID = int(os.environ.get("841949601", "0"))  # set in Render env

# Days per window
DAYS = {
    "WS": [("WED", "ד׳"), ("THU", "ה׳"), ("FRI", "ו׳"), ("SAT", "ש׳")],
    "SU": [("SUN", "א׳"), ("MON", "ב׳"), ("TUE", "ג׳")],
}

# ---------- helpers ----------
def tg_send_message(chat_id: int, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return requests.post(f"{API_URL}/sendMessage", json=payload, timeout=15)

def make_inline_days_keyboard(window_key: str, user_id: int):
    # Each button toggles a day: hostavail|WS|WED
    rows = []
    selected = HOST_AVAIL.get(window_key, {}).get(user_id, set())

    for day_key, day_label in DAYS[window_key]:
        mark = "✅ " if day_key in selected else ""
        rows.append([{
            "text": f"{mark}{day_label}",
            "callback_data": f"hostavail|{window_key}|{day_key}"
        }])

    # Done button
    rows.append([{
        "text": "סיימתי",
        "callback_data": f"hostdone|{window_key}"
    }])

    return {"inline_keyboard": rows}

def verify_trigger(req) -> bool:
    """
    Verify X-Trigger-Token = HMAC_SHA256(secret, body="{}")
    This matches the GitHub Action we set (body is "{}").
    """
    if not TRIGGER_SECRET:
        return False

    sig = req.headers.get("X-Trigger-Token", "")
    raw = req.get_data()  # bytes
    expected = hmac.new(TRIGGER_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)

def is_private_chat(update: dict) -> bool:
    try:
        return update["message"]["chat"]["type"] == "private"
    except Exception:
        return False

# ---------- web routes ----------
@app.get("/")
def health():
    return "OK", 200

@app.post("/webhook")
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    print("UPDATE:", update)

    # Handle callback buttons
    if "callback_query" in update:
        cb = update["callback_query"]
        data = cb.get("data", "")
        from_user = cb.get("from", {})
        user_id = from_user.get("id")

        # Answer callback (prevents loading spinner)
        cb_id = cb.get("id")
        if cb_id:
            requests.post(f"{API_URL}/answerCallbackQuery", json={"callback_query_id": cb_id}, timeout=10)

        if not user_id:
            return "OK", 200

        parts = data.split("|")
        if len(parts) >= 2 and parts[0] == "hostdone":
            window_key = parts[1]
            chosen = sorted(list(HOST_AVAIL.get(window_key, {}).get(user_id, set())))
            chosen_txt = ", ".join(chosen) if chosen else "לא נבחר כלום"
            tg_send_message(user_id, f"נרשם! ({window_key}) ימים שבחרת: {chosen_txt}")
            return "OK", 200

        if len(parts) == 3 and parts[0] == "hostavail":
            window_key, day_key = parts[1], parts[2]
            if window_key in HOST_AVAIL:
                user_set = HOST_AVAIL[window_key].setdefault(user_id, set())
                if day_key in user_set:
                    user_set.remove(day_key)
                else:
                    user_set.add(day_key)

                # Edit message markup to reflect ✅
                msg = cb.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                message_id = msg.get("message_id")
                if chat_id and message_id:
                    reply_markup = make_inline_days_keyboard(window_key, user_id)
                    requests.post(
                        f"{API_URL}/editMessageReplyMarkup",
                        json={"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
                        timeout=15
                    )
            return "OK", 200

        return "OK", 200

    # Handle text messages (commands)
    if "message" in update and "text" in update["message"]:
        chat = update["message"]["chat"]
        chat_id = chat["id"]
        chat_type = chat.get("type")
        text = update["message"]["text"].strip()
        user_id = update["message"].get("from", {}).get("id")

        # Basic ping in group (optional)
        if text == "/ping" and chat_type != "private":
            tg_send_message(chat_id, "🏓 pong (בקבוצה)")
            return "OK", 200

        # Commands in private
        if chat_type == "private":
            if text == "/addhost":
                if user_id:
                    HOSTS.add(int(user_id))
                    tg_send_message(chat_id, "✅ נרשמת כמארח! (זמני – עד ריסט של השרת)")
                return "OK", 200

            if text == "/myhost":
                ws = sorted(list(HOST_AVAIL["WS"].get(int(user_id), set()))) if user_id else []
                su = sorted(list(HOST_AVAIL["SU"].get(int(user_id), set()))) if user_id else []
                tg_send_message(chat_id, f"WS: {ws or '—'}\nSU: {su or '—'}")
                return "OK", 200

            if text == "/listhosts":
                if ADMIN_USER_ID and user_id == ADMIN_USER_ID:
                    tg_send_message(chat_id, f"HOSTS ({len(HOSTS)}): {sorted(list(HOSTS))}")
                else:
                    tg_send_message(chat_id, "⛔ אין הרשאה")
                return "OK", 200

    return "OK", 200


# --- Triggers from GitHub Actions ---
@app.post("/trigger/ask/<window_key>")
def trigger_ask(window_key: str):
    # window_key is WS or SU
    if window_key not in ("WS", "SU"):
        return jsonify({"ok": False, "error": "bad_window"}), 400

    if not verify_trigger(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    # send DM to all hosts
    sent = 0
    for uid in list(HOSTS):
        kb = make_inline_days_keyboard(window_key, uid)
        tg_send_message(uid, f"🃏 מי יכול לארח השבוע? (חלון {window_key})\nבחר ימים:", reply_markup=kb)
        sent += 1
        time.sleep(0.1)  # small throttle

    return jsonify({"ok": True, "sent": sent}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
