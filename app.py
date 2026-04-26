from flask import Flask, request, jsonify
from twilio.rest import Client
import os
import re
import threading
import time
from datetime import datetime

app = Flask(__name__)

# ─── CONFIGURATION ─────────────────────────────────────────────────────────────

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE       = os.environ.get("TWILIO_PHONE")

ON_CALL = [
    {"name": "Person 1", "phone": os.environ.get("ONCALL_1_PHONE")},
]

EMERGENCY_CODES = {"1001", "1002", "2011", "2013"}

ONCALL_DAY = 6  # Sunday

ACK_WAIT_MINUTES = 5

TESTING_MODE = os.environ.get("TESTING_MODE", "false").lower() == "true"

# ─── STATE TRACKING ────────────────────────────────────────────────────────────
pending_alerts = {}

# ─── HELPERS ───────────────────────────────────────────────────────────────────

def is_oncall_day():
    return datetime.now().weekday() == ONCALL_DAY

def extract_error_code(subject: str):
    match = re.search(r"Error\s*#(\d+)", subject, re.IGNORECASE)
    return match.group(1) if match else None

def extract_system_name(subject: str):
    match = re.search(r"(?:WARNING|CAUTION):\s*([^;]+)", subject, re.IGNORECASE)
    return match.group(1).strip() if match else "Unknown System"

def send_sms(to_phone, message):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    client.messages.create(body=message, from_=TWILIO_PHONE, to=to_phone)
    print(f"SMS sent to {to_phone}")

def make_call(to_phone, message):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    twiml = f"<Response><Say voice='alice'>{message}</Say><Say voice='alice'>This is an automated emergency alert. Please check your system immediately.</Say></Response>"
    client.calls.create(twiml=twiml, from_=TWILIO_PHONE, to=to_phone)
    print(f"Call made to {to_phone}")

def escalate_if_no_ack(alert_id, person_index, error_code, system_name):
    time.sleep(ACK_WAIT_MINUTES * 60)
    alert = pending_alerts.get(alert_id)
    if not alert or alert.get("acked"):
        print(f"Alert {alert_id} was acknowledged. No escalation needed.")
        return
    person = ON_CALL[person_index]
    print(f"No ACK received from {person['name']}. Calling...")
    call_message = (
        f"Emergency alert for {system_name}. "
        f"Error code {error_code}. "
        f"No acknowledgment received. Immediate action required."
    )
    make_call(person["phone"], call_message)

# ─── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.form or request.json or {}

    subject = data.get("subject", "") or data.get("Subject", "")
    sender  = data.get("from", "")    or data.get("From", "")

    print(f"Received — From: {sender} | Subject: {subject} | Testing: {TESTING_MODE}")

    # Skip sender check in testing mode
    if not TESTING_MODE:
        if "bmu.monitoring@orbem.ai" not in sender.lower():
            return jsonify({"status": "ignored", "reason": "not from BMU monitoring"}), 200

    # Skip day check in testing mode
    if not TESTING_MODE and not is_oncall_day():
        return jsonify({"status": "ignored", "reason": "not on-call day"}), 200

    error_code = extract_error_code(subject)
    if not error_code:
        return jsonify({"status": "ignored", "reason": "no error code found"}), 200

    if error_code not in EMERGENCY_CODES:
        return jsonify({"status": "ignored", "reason": f"error #{error_code} is not an emergency"}), 200

    # It's an emergency!
    system_name = extract_system_name(subject)
    alert_id = f"{error_code}-{int(time.time())}"
    person = ON_CALL[0]

    sms_message = (
        f"EMERGENCY: {system_name}\n"
        f"Error #{error_code}\n"
        f"Immediate action required.\n"
        f"Reply ACK to confirm. Alert ID: {alert_id}"
    )

    send_sms(person["phone"], sms_message)

    pending_alerts[alert_id] = {
        "person": person["name"],
        "phone": person["phone"],
        "acked": False,
        "error_code": error_code,
        "system_name": system_name,
    }

    t = threading.Thread(
        target=escalate_if_no_ack,
        args=(alert_id, 0, error_code, system_name)
    )
    t.daemon = True
    t.start()

    return jsonify({"status": "alert_sent", "alert_id": alert_id}), 200


@app.route("/ack", methods=["POST"])
def ack():
    body  = request.form.get("Body", "").strip().upper()
    from_ = request.form.get("From", "")

    print(f"SMS reply from {from_}: {body}")

    if body == "ACK":
        for alert_id, alert in pending_alerts.items():
            if alert["phone"] == from_ and not alert["acked"]:
                pending_alerts[alert_id]["acked"] = True
                print(f"Alert {alert_id} acknowledged by {alert['person']}")
                return (
                    "<Response><Message>ACK received. Alert acknowledged.</Message></Response>",
                    200,
                    {"Content-Type": "text/xml"},
                )

    return (
        "<Response><Message>Reply ACK to acknowledge an emergency alert.</Message></Response>",
        200,
        {"Content-Type": "text/xml"},
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "testing_mode": TESTING_MODE}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
