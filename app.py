import os, math
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
import stripe

# Load environment variables
load_dotenv()

# === REQUIRED: Your live/test secret key must be set in .env as STRIPE_SECRET_KEY ===
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Default redirect URLs (can be overridden via env)
SUCCESS_URL = os.getenv("SUCCESS_URL", "http://localhost:4242/thank-you")
CANCEL_URL  = os.getenv("CANCEL_URL",  "http://localhost:4242/error")

# Ramadan Pledges Configuration
TOTAL_UNITS = 80
UNIT_PRICE = 1000  # $1,000 per unit
MIN_UNITS = 1
MAX_UNITS = TOTAL_UNITS

ZERO_DECIMAL_CURRENCIES = {"bif","clp","djf","gnf","jpy","kmf","krw","mga","pyg","rwf","ugx","vnd","vuv","xaf","xof","xpf"}

def to_unit_amount(amount: float, currency: str) -> int:
    a = float(amount)
    if not math.isfinite(a) or a <= 0:
        raise ValueError("Invalid amount")
    if currency.lower() in ZERO_DECIMAL_CURRENCIES:
        return int(round(a))  # e.g., JPY
    return int(round(a * 100)) # e.g., USD

@app.post("/create-checkout-session")
def create_checkout_session():
    try:
        data = request.get_json(force=True)
        units = int(data.get("units", 1))
        currency = (data.get("currency") or "usd").lower()
        frequency = (data.get("frequency") or "once").lower()  # once | weekly | monthly
        donor_name = data.get("donor_name", "Anonymous")
        donor_email = data.get("donor_email", "")

        # Validate units
        if units < MIN_UNITS or units > MAX_UNITS:
            return jsonify({"error": f"Units must be between {MIN_UNITS} and {MAX_UNITS}."}), 400

        # Calculate total amount
        total_amount = units * UNIT_PRICE
        unit_amount = to_unit_amount(total_amount, currency)

        # Guardrails
        MIN = 100 if currency not in ZERO_DECIMAL_CURRENCIES else 1
        MAX = 100000000
        if unit_amount < MIN or unit_amount > MAX:
            return jsonify({"error": "Amount out of allowed range."}), 400

        is_recurring = frequency in ("weekly", "monthly")
        price_data = {
            "currency": currency,
            "unit_amount": unit_amount,
            "product_data": {"name": f"Ramadan Pledge - {units} Unit(s)"}
        }
        
        if is_recurring:
            price_data["recurring"] = {
                "interval": "week" if frequency == "weekly" else "month"
            }

        # Build session parameters
        session_params = {
            "mode": "subscription" if is_recurring else "payment",
            "line_items": [{"price_data": price_data, "quantity": 1}],
            "success_url": SUCCESS_URL,
            "cancel_url": CANCEL_URL,
            "metadata": {
                "units": str(units),
                "donor_name": donor_name,
                "frequency": frequency
            }
        }

        # Add customer email if provided
        if donor_email:
            session_params["customer_email"] = donor_email

        session = stripe.checkout.Session.create(**session_params)
        return jsonify({"url": session.url})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.post("/webhook")
def webhook():
    # Raw body required for signature verification
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")
    endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    # During first runs, you may not have a webhook secret set yet.
    if not endpoint_secret:
        return "Webhook endpoint not yet configured.", 200

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception as e:
        return f"Invalid signature or payload: {e}", 400

    # Handle important events
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata", {})
        # One-time: session["payment_intent"]
        # Recurring: session["subscription"]
        # TODO: record pledge; send thank-you email; update CRM
        print(f"✓ Pledge completed: {metadata.get('donor_name')} for {metadata.get('units')} units")
    
    elif event["type"] == "invoice.paid":
        invoice = event["data"]["object"]
        # Recurring pledge payment received
        print(f"✓ Recurring payment received for subscription {invoice.get('subscription')}")

    return "", 200

@app.get("/")
def donor_page():
    return send_from_directory("static", "index.html")

@app.get("/thank-you")
def thank_you():
    return send_from_directory("static", "thank-you.html")

@app.get("/error")
def error():
    return send_from_directory("static", "error.html")

if __name__ == "__main__":
    app.run(port=4242, debug=True)
