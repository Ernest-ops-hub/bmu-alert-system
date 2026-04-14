from flask import Flask, request, jsonify
from twilio.rest import Client
import os
import re
import threading
import time
from datetime import datetime

app = Flask(__name__)

# ─── CONFIGURATION ─────────────────────────────────────────────────────────────

# Twilio credentials (set these as environment variables in Railway)
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE       = os.environ.get("TWILIO_PHONE")  # Your Twilio number e.g. +12345678900

# On-call rotation — update this every week
ON_CALL = [
    {"name": "Person 1", "phone": os.environ.get("ONCALL_1_PHONE")},  # e.g. +17135550001
    {"name": "Person 2", "phone": os.environ.get("ONCALL_2_PHONE")},  # e.g. +17135550002
]

# Emergency error codes that trigger SMS + call
EMERGENCY_CODES = {"1001", "1002", "2011", "2013"}

# Only alert on Sundays (0=Monday ... 6=Sunday)
ONCALL_DAY = 6

# Minutes to wait for ACK before escalating to phone call
ACK_WAIT_MINUTES = 5

# ─── STATE TRACKING ────────────────────────────────────────────────────────────
# Tracks pending alerts waiting for ACK
# key: alert_id, value: {"person": ..., "phone": ..., "acked": False}
pending_alerts = {}

# ─── HELPERS ───────────────────────────────────────────────────────────────────

def is_oncall_day():
    """Returns True if today is Sunday."""
    return datetime.now().weekday() == ONCALL_DAY

def extract_error_code(subject: str):
    """Pulls error code number from subject line like 'WARNING: CAL-MAINE#3 7T; Error #2013'"""
    match = re.search(r"Error\s*#(\d+)", subject, re.IGNORECASE)
    return match.group(1) if match else None

def extract_system_name(subject: str):
    """Pulls system name from subject line."""
    match = re.search(r"(?:WARNING|CAUTION):\s*([^;]+)", subject, re.IGNORECASE)
    return match.group(1).strip() if match else "Unknown System"

def send_sms(to_phone, message):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    client.messages.create(body=message, from_=TWILIO_PHONE, to=to_phone)
    print(f"SMS sent to {to_phone}")

def make_call(to_phone, message):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    twiml = f"<Response><Say voice='alice'>{message}</Say><Say voice='alice'>This is an automated emergency alert. Please check your system immediately.</Say></Response>"
    client.calls.create(
        twiml=twiml,
        from_=TWILIO_PHONE,
        to=to_phone
    )
    print(f"Call made to {to_phone}")

def escalate_if_no_ack(alert_id, person_index, error_code, system_name):
    """Wait ACK_WAIT_MINUTES, then call if not acknowledged. Then escalate to next person."""
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

    # Escalate to next person if available
    next_index = person_index + 1
    if next_index < len(ON_CALL):
        next_person = ON_CALL[next_index]
        sms_message = (
            f"⚠️ EMERGENCY ESCALATION: {system_name}\n"
            f"Error #{error_code}\n"
            f"{person['name']} did not respond.\n"
            f"Reply ACK to confirm. Alert ID: {alert_id}"
        )
        send_sms(next_person["phone"], sms_message)
        pending_alerts[alert_id]["person"] = next_person["name"]
        pending_alerts[alert_id]["phone"] = next_person["phone"]

        # Start escalation timer for next person
        t = threading.Thread(
            target=escalate_if_no_ack,
            args=(alert_id, next_index, error_code, system_name)
        )
        t.daemon = True
        t.start()

# ─── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    """Receives email data from Zapier and processes it."""
    data = request.form or request.json or {}

    subject = data.get("subject", "") or data.get("Subject", "")
    sender  = data.get("from", "")    or data.get("From", "")

    print(f"Received email — From: {sender} | Subject: {subject}")

    # Only process emails from BMU monitoring
    if "bmu.monitoring@orbem.ai" not in sender.lower():
        return jsonify({"status": "ignored", "reason": "not from BMU monitoring"}), 200

    # Only alert on Sundays
    if not is_oncall_day():
        return jsonify({"status": "ignored", "reason": "not on-call day"}), 200

    error_code = extract_error_code(subject)
    if not error_code:
        return jsonify({"status": "ignored", "reason": "no error code found"}), 200

    if error_code not in EMERGENCY_CODES:
        print(f"Error #{error_code} is not an emergency. Ignoring.")
        return jsonify({"status": "ignored", "reason": f"error #{error_code} is not an emergency"}), 200

    # It's an emergency — alert on-call person #1
    system_name = extract_system_name(subject)
    alert_id = f"{error_code}-{int(time.time())}"
    person = ON_CALL[0]

    sms_message = (
        f"⚠️ EMERGENCY: {system_name}\n"
        f"Error #{error_code}\n"
        f"Immediate action required.\n"
        f"Reply ACK to confirm. Alert ID: {alert_id}"
    )

    send_sms(person["phone"], sms_message)

    # Track this alert
    pending_alerts[alert_id] = {
        "person": person["name"],
        "phone": person["phone"],
        "acked": False,
        "error_code": error_code,
        "system_name": system_name,
    }

    # Start escalation timer in background
    t = threading.Thread(
        target=escalate_if_no_ack,
        args=(alert_id, 0, error_code, system_name)
    )
    t.daemon = True
    t.start()

    return jsonify({"status": "alert_sent", "alert_id": alert_id}), 200


@app.route("/ack", methods=["POST"])
def ack():
    """Receives ACK reply from Twilio when on-call person replies to SMS."""
    body    = request.form.get("Body", "").strip().upper()
    from_   = request.form.get("From", "")

    print(f"SMS reply received from {from_}: {body}")

    if body == "ACK":
        # Find the matching pending alert for this phone number
        for alert_id, alert in pending_alerts.items():
            if alert["phone"] == from_ and not alert["acked"]:
                pending_alerts[alert_id]["acked"] = True
                print(f"Alert {alert_id} acknowledged by {alert['person']}")
                return (
                    "<Response><Message>✅ ACK received. Alert acknowledged.</Message></Response>",
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
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
