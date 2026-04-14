# BMU Alert System

Automated on-call alerting system for Bruker BMU MRI monitoring emails.

## How it works
1. Gmail receives an alert from bmu.monitoring@orbem.ai
2. Zapier detects the new email and sends it to this app via webhook
3. The app checks if it's Sunday and if the error code is an emergency
4. If yes, it sends an SMS to the on-call person
5. If no ACK reply within 5 minutes, it calls them
6. If still no response, it escalates to person #2

## Emergency Error Codes
- #1001 — Power Outage
- #1002 — Internal Communication Failure
- #2011 — Compressor Not Operating
- #2013 — Cooling Water Out of Range

## Environment Variables (set in Railway)
| Variable | Description |
|---|---|
| TWILIO_ACCOUNT_SID | Your Twilio Account SID |
| TWILIO_AUTH_TOKEN | Your Twilio Auth Token |
| TWILIO_PHONE | Your Twilio phone number (e.g. +12345678900) |
| ONCALL_1_PHONE | On-call person #1 phone number |
| ONCALL_2_PHONE | On-call person #2 phone number |

## Updating On-Call Rotation
Update ONCALL_1_PHONE and ONCALL_2_PHONE in Railway environment variables each week.
