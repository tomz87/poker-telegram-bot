import os
import time
import hmac
import hashlib
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ========= ENV (Render -> Environment Variables) =========
# TELEGRAM_BOT_TOKEN   (חובה)  לדוגמה: 123:AA...
# TELEGRAM_GROUP_ID    (חובה)  לדוגמה: -1003587001321
# TRIGGER_SECRET       (חובה)  מחרוזת ארוכה זהה גם ב-GitHub secrets
# ADMIN_USER_ID        (מומלץ) ה-user_id שלך (כדי שפקודות אדמין יעבדו)

TOKEN = str(os.environ.get("8518103041:AAGwbs3RfKSZRly39cNH-pXEpKlvDAhYW1A", "")).strip()
GROUP_ID = int(os.environ.get("-1003587001321", "0") or "0")
TRIGGER_SECRET = str(os.environ.get("shalmanimPoker2026", "")).strip()
ADMIN_USER_ID = int(os.environ.get("841949601", "0") or "0")

API_URL = "https://api.telegram.org/bot" + TOKEN

DEFAULT_TIME = "21:00"

# ========= RAM storage (Option 1) =========
HOSTS = set()  # user_ids of hosts (registered via /addhost in private)
HOST_AVAIL = {
    "WS": {},  # user_id -> set(day_keys)
    "SU": {},
}

# Active poll tracking in RAM
ACTIVE_POLL = None
# ACTIVE_POLL = {"window":"WS"/"SU", "poll_id": "...", "options":[...]}
POLL_VOTES = {}
# POLL_VOTES[poll_id][user_id] = set(option_ids)

# ========= Days (Hebrew full names) =========
# WS = רביעי-שבת, SU = ראשון-שלישי
DAYS = {
    "WS": [("WED", "רביעי"), ("THU", "חמישי"), ("FRI", "שישי"), ("SAT", "שבת")],
    "SU": [("SUN", "ראשון"), ("MON", "שני"), ("TUE", "שלישי")],
}


# ================= Helpers =================
def tg(method: str, payload: dict):
    return requests.post(f"{API_URL}/{method}", json=payload, timeout=15)

def tg_send_message(chat_id: int, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg("sendMessage", payload)

def daykey_to_hebrew(window_key: str, day_key: str) -> str:
    mapping = {k: heb for (k, heb) in DAYS[window_key]}
    return mapping.get(day_key, day_key)

def make_inline_days_keyboard(window_key: str, user_id: int):
    selected = HOST_AVAIL.get(window_key, {}).get(user_id, set())
    rows = []

    for day_key, day_he in DAYS[window_key]:
        mark = "✅ " if day_key in selected else ""
        rows.append([{
            "text": f"{mark}{day_he}",
            "callback_data": f"hostavail|{window_key}|{day_key}",
        }])

    rows.append([{
        "text": "סיימתי",
        "callback_data": f"hostdone|{window_key}",
    }])

    return {"inline_keyboard": rows}

def verify_trigger(req) -> bool:
    """
    Verify header X-Trigger-Token = HMAC_SHA256(TRIGGER_SECRET, raw_body)
    GitHub sends raw body: {}
    """
    if not TRIGGER_SECRET:
        return False

    sig = req.headers.get("X-Trigger-Token", "")
    raw = req.get_data()  # bytes
    expected = hmac.new(TRIGGER_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)

def build_poll_options(window_key: str):
    """
    Build options like: 'רביעי 21:00 – מארח 123456'
    (בלי DB: אין לנו שם מארח אמיתי כרגע, אפשר לשדרג בהמשך)
    """
    options = []
    for host_id, day_keys in HOST_AVAIL.get(window_key, {}).items():
        if not day_keys:
            continue
        host_label = f"מארח {host_id}"
        for day_key in sorted(list(day_keys)):
            day_he = daykey_to_hebrew(window_key, day_key)
            options.append(f"{day_he} {DEFAULT_TIME} – {host_label}")

    # טלגרם: עד 10 אופציות בפול
    return options[:10]

def compute_summary_text():
    if not ACTIVE_POLL:
        return None

    poll_id = ACTIVE_POLL["poll_id"]
    options = ACTIVE_POLL["options"]
    votes = POLL_VOTES.get(poll_id, {})

    counts = [0] * len(options)
    for _, chosen in votes.items():
        for oid in chosen:
            if 0 <= oid < len(counts):
                counts[oid] += 1

    lines = [f"{counts[i]} — {options[i]}" for i in range(len(options))]
    best = max(counts) if counts else 0
    winners = [options[i] for i, c in enumerate(counts) if c == best and best > 0]

    msg_txt = "📊 סיכום הצבעות:\n" + "\n".join(lines)
    if winners:
        msg_txt += "\n\n🏆 המוביל/ים כרגע:\n" + "\n".join(winners)

    return msg_txt


# ================= Routes =================
@app.get("/")
def health():
    return "OK", 200


@app.post("/webhook")
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    print("UPDATE:", update)

    # 1) Votes updates (poll_answer)
    if "poll_answer" in update:
        pa = update["poll_answer"]
        poll_id = pa.get("poll_id")
        user = pa.get("user", {}) or {}
        user_id = user.get("id")
        option_ids = pa.get("option_ids", [])

        if poll_id:
            POLL_VOTES.setdefault(poll_id, {})
            if user_id:
                POLL_VOTES[poll_id][int(user_id)] = set(option_ids)

        return "OK", 200

    # 2) Inline buttons (callback_query)
    if "callback_query" in update:
        cb = update["callback_query"]
        data = (cb.get("data") or "").strip()
        cb_id = cb.get("id")
        from_user = cb.get("from", {}) or {}
        user_id = from_user.get("id")

        # stop spinner
        if cb_id:
            tg("answerCallbackQuery", {"callback_query_id": cb_id})

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

                # update keyboard checkmarks
                msg = cb.get("message", {}) or {}
                chat_id = msg.get("chat", {}).get("id")
                message_id = msg.get("message_id")
                if chat_id and message_id:
                    kb = make_inline_days_keyboard(window_key, uid)
                    tg("editMessageReplyMarkup", {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "reply_markup": kb
                    })

            return "OK", 200

        return "OK", 200

    # 3) Text messages
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat = msg.get("chat", {}) or {}
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        text = (msg.get("text") or "").strip()
        user_id = (msg.get("from") or {}).get("id")

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
                tg_send_message(
                    chat_id,
                    "ימים שבחרת:\n"
                    f"WS (רביעי–שבת): {', '.join(ws) if ws else '—'}\n"
                    f"SU (ראשון–שלישי): {', '.join(su) if su else '—'}"
                )
                return "OK", 200

            if text == "/listhosts":
                if ADMIN_USER_ID and user_id == ADMIN_USER_ID:
                    tg_send_message(chat_id, f"👥 מארחים ({len(HOSTS)}):\n" + "\n".join([str(x) for x in sorted(list(HOSTS))]))
                else:
                    tg_send_message(chat_id, "⛔ אין הרשאה")
                return "OK", 200

            if text == "/whoami":
                tg_send_message(chat_id, f"user_id שלך: {user_id}")
                return "OK", 200

        # Group commands
        if chat_type in ("group", "supergroup"):
            if text == "/summary":
                # allow only admin (optional)
                if ADMIN_USER_ID and user_id != ADMIN_USER_ID:
                    tg_send_message(chat_id, "⛔ רק אדמין יכול להציג סיכום")
                    return "OK", 200

                summary = compute_summary_text()
                tg_send_message(chat_id, summary if summary else "אין סקר פעיל כרגע.")
                return "OK", 200

            if text == "/ping":
                tg_send_message(chat_id, "🏓 pong")
                return "OK", 200

        return "OK", 200

    return "OK", 200


# ================= Triggers from GitHub Actions =================

@app.post("/trigger/ask/<window_key>")
def trigger_ask(window_key: str):
    window_key = (window_key or "").upper()
    if window_key not in ("WS", "SU"):
        return jsonify({"ok": False, "error": "bad_window"}), 400

    if not verify_trigger(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    sent = 0
    title = "רביעי–שבת" if window_key == "WS" else "ראשון–שלישי"

    for uid in list(HOSTS):
        kb = make_inline_days_keyboard(window_key, uid)
        tg_send_message(uid, f"🃏 מי יכול לארח השבוע? ({title})\nבחר ימים:", reply_markup=kb)
        sent += 1
        time.sleep(0.12)

    return jsonify({"ok": True, "sent": sent}), 200


@app.post("/trigger/poll/<window_key>")
def trigger_poll(window_key: str):
    global ACTIVE_POLL

    window_key = (window_key or "").upper()
    if window_key not in ("WS", "SU"):
        return jsonify({"ok": False, "error": "bad_window"}), 400

    if not verify_trigger(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    options = build_poll_options(window_key)
    if len(options) < 2:
        return jsonify({"ok": False, "error": "not_enough_options", "count": len(options)}), 200

    res = tg("sendPoll", {
        "chat_id": GROUP_ID,
        "question": "מי יכול להגיע למשחק הקרוב? (אפשר לבחור כמה)",
        "options": options,
        "is_anonymous": False,
        "allows_multiple_answers": True
    }).json()

    if not res.get("ok"):
        return jsonify({"ok": False, "error": "sendPoll_failed", "telegram": res}), 200

    poll_id = res["result"]["poll"]["id"]
    ACTIVE_POLL = {"window": window_key, "poll_id": poll_id, "options": options}
    POLL_VOTES.setdefault(poll_id, {})

    return jsonify({"ok": True, "poll_id": poll_id, "options": options}), 200


@app.post("/trigger/summary")
def trigger_summary():
    if not verify_trigger(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    summary = compute_summary_text()
    if not summary:
        return jsonify({"ok": False, "error": "no_active_poll"}), 200

    tg_send_message(GROUP_ID, "📌 סיכום אוטומטי:\n" + summary)
    return jsonify({"ok": True}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
