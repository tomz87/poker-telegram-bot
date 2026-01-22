import os
import requests
from flask import Flask, request

app = Flask(__name__)

TOKEN = os.environ.get("8518103041:AAGwbs3RfKSZRly39cNH-pXEpKlvDAhYW1A")
GROUP_ID = int(os.environ.get("-1003587001321", "0"))
API_URL = f"https://api.telegram.org/bot{TOKEN}"

@app.get("/")
def health():
    return "OK"

@app.post("/webhook")
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    print("UPDATE:", update)

    # בדיקה שיש הודעת טקסט
    if "message" in update and "text" in update["message"]:
        chat_id = update["message"]["chat"]["id"]
        text = update["message"]["text"]
        is_group = (chat_id == GROUP_ID)


        # תגובת בדיקה
        if text.lower() == "שלום":
            requests.post(
                f"{API_URL}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "היי 👋 הבוט מחובר ועובד!"
                }
            )
        if text == "/ping" and is_group:
            requests.post(
                f"{API_URL}/sendMessage",
                json={"chat_id": chat_id, "text": "🏓 pong (הבוט חי בקבוצה)"},
            )


    return "OK", 200
