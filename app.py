import os
import time
import hmac
import hashlib
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ====== ENV (Render -> Environment Variables) ======
# TELEGRAM_BOT_TOKEN  = "123456:ABC..."   (חובה)
# TRIGGER_SECRET      = "מחרוזת ארוכה"    (חובה)
# ADMIN_USER_ID       = "841949601"       (מומלץ - כדי ש-/listhosts יעבוד רק לך)
GROUP_ID = int(os.environ.get("-1003587001321", "0"))
TOKEN = (os.environ.get("8518103041:AAGwbs3RfKSZRly39cNH-pXEpKlvDAhYW1A") or "").strip()
TRIGGER_SECRET = (os.environ.get("shalmanimPoker2026") or "").strip()
ADMIN_USER_ID = int(os.environ.get("841949601", "0") or "0")

if not TOKEN:
    print("❌ Missing TELEGRAM_BOT_TOKEN environment variable")

API_URL = f"https://api.telegram.org/bot{TOKEN}"

# ====== RAM storage (Option 1) ======
HOSTS = set()  # set of user_ids that registered as hosts
HOST_AVAIL = {
    "WS": {},  # user_id -> set(day_keys)
    "SU": {},
}

# Days per window (buttons are Hebrew)
DAYS = {
    "WS": [("WED", "רביעי"), ("THU", "חמישי"), ("FRI", "שישי"), ("SAT", "מוצש")],
    "SU": [("SUN", "ראשון"), ("MON", "שני"), ("TUE", "שלישי")],
}

# =====================================
# Helpers
# =====================================
def tg_send_message(chat_id: int, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return requests.post(f"{API_URL}/sendMessage", json=payload, timeout=15)

def tg_answer_callback(callback_query_id: str):
    return requests.post(
        f"{API_URL}/answerCallbackQuery",
        json={"callback_query_id": callback_query_id},
        timeout=10,
    )

def tg_edit_reply_markup(chat_id: int, message_id: int, reply_markup):
    return requests.post(
        f"{API_URL}/editMessageReplyMarkup",
        json={"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
        timeout=15,
    )

def make_inline_days_keyboard(window_key: str, user_id: int):
    rows = []
    selected = HOST_AVAIL.get(window_key, {}).get(user_id, set())

    for day_key, day_label in DAYS[window_key]:
        mark = "✅ " if day_key in selected else ""
        rows.append([{
            "text": f"{mark}{day_label}",
            "callback_data": f"hostavail|{window_key}|{day_key}",
        }])

    rows.append([{
        "text": "סיימתי",
        "callback_data": f"hostdone|{window_key}",
    }])

    return {"inline_keyboard": rows}

def verify_trigger(req) -> bool:
    """
    Verify X-Trigger-Token header equals HMAC_SHA256(TRIGGER_SECRET, raw_body).
    GitHub Actions should send body: {} (exact).
    """
    if not TRIGGER_SECRET:
        return False

    sig = req.headers.get("X-Trigger-Token", "")
    raw = req.get_data()  # bytes
    expected = hmac.new(TRIGGER_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)

def daykey_to_hebrew(window_key: str, day_key: str) -> str:
    mapping = {k: heb for (k, heb) in DAYS[window_key]}
    return mapping.get(day_key, day_key)

# =====================================
# Routes
# =====================================
@app.get("/")
def health():
    return "OK", 200

@app.post("/webhook")
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    print("UPDATE:", update)

    # ---------- Callback buttons ----------
    if "callback_query" in update:
        cb = update["callback_query"]
        data = cb.get("data", "") or ""
        cb_id = cb.get("id", "")
        from_user = cb.get("from", {}) or {}
        user_id = from_user.get("id")

        if cb_id:
            tg_answer_callback(cb_id)

        if not user_id:
            return "OK", 200

        parts = data.split("|")

        # hostdone|WS
        if len(parts) >= 2 and parts[0] == "hostdone":
            window_key = parts[1]
            chosen_keys = HOST_AVAIL.get(window_key, {}).get(int(user_id), set())
            chosen_he = [daykey_to_hebrew(window_key, k) for k in chosen_keys]
            chosen_txt = ", ".join(chosen_he) if chosen_he else "לא נבחר כלום"
            tg_send_message(int(user_id), f"✅ נקלט! ימים שבחרת: {chosen_txt}")
            return "OK", 200

        # hostavail|WS|WED
        if len(parts) == 3 and parts[0] == "hostavail":
            window_key, day_key = parts[1], parts[2]
            if window_key in HOST_AVAIL:
                uid = int(user_id)
                user_set = HOST_AVAIL[window_key].setdefault(uid, set())
                if day_key in user_set:
                    user_set.remove(day_key)
                else:
                    user_set.add(day_key)

                # Update the buttons (✅)
                msg = cb.get("message", {}) or {}
                chat_id = msg.get("chat", {}).get("id")
                message_id = msg.get("message_id")
                if chat_id and message_id:
                    reply_markup = make_inline_days_keyboard(window_key, uid)
                    tg_edit_reply_markup(chat_id, message_id, reply_markup)

            return "OK", 200

        return "OK", 200

    # ---------- Text messages ----------
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat = msg.get("chat", {}) or {}
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        text = (msg.get("text") or "").strip()
        user_id = (msg.get("from") or {}).get("id")

        # Optional: group ping
        if text == "/ping" and chat_type != "private":
            tg_send_message(chat_id, "🏓 pong (בקבוצה)")
            return "OK", 200

        # Private commands
        if chat_type == "private":
            if text == "/addhost":
                if user_id:
                    HOSTS.add(int(user_id))
                    tg_send_message(chat_id, "✅ נרשמת כמארח! (זמני – עד ריסט של השרת)")
                return "OK", 200

            if text == "/myhost":
                uid = int(user_id) if user_id else 0
                ws = [daykey_to_hebrew("WS", k) for k in sorted(list(HOST_AVAIL["WS"].get(uid, set())))]
                su = [daykey_to_hebrew("SU", k) for k in sorted(list(HOST_AVAIL["SU"].get(uid, set())))]
                tg_send_message(chat_id, f"ימים שבחרת:\nWS (ד׳–ש׳): {', '.join(ws) if ws else '—'}\nSU (א׳–ג׳): {', '.join(su) if su else '—'}")
                return "OK", 200

            if text == "/listhosts":
                if ADMIN_USER_ID and user_id == ADMIN_USER_ID:
                    tg_send_message(chat_id, f"👥 מארחים רשומים ({len(HOSTS)}):\n" + "\n".join([str(x) for x in sorted(list(HOSTS))]) )
                else:
                    tg_send_message(chat_id, "⛔ אין הרשאה")
                return "OK", 200

        return "OK", 200

    return "OK", 200


# =====================================
# Triggers from GitHub Actions
# POST /trigger/ask/WS  -> DM hosts with Wed-Thu-Fri-Sat buttons (עברית)
# POST /trigger/ask/SU  -> DM hosts with Sun-Mon-Tue buttons (עברית)
# =====================================
@app.post("/trigger/ask/<window_key>")
def trigger_ask(window_key: str):
    window_key = (window_key or "").upper()
    if window_key not in ("WS", "SU"):
        return jsonify({"ok": False, "error": "bad_window"}), 400

    if not verify_trigger(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    sent = 0
    for uid in list(HOSTS):
        kb = make_inline_days_keyboard(window_key, uid)
        title = "ד׳–ש׳" if window_key == "WS" else "א׳–ג׳"
        tg_send_message(uid, f"🃏 מי יכול לארח השבוע? ({title})\nבחר ימים:", reply_markup=kb)
        sent += 1
        time.sleep(0.12)  # throttle קטן

    return jsonify({"ok": True, "sent": sent}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

