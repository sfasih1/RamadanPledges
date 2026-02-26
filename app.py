import os, math, logging
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
import stripe

# Load environment variables
load_dotenv()

# Setup logging for production
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === REQUIRED: Your live/test secret key must be set in .env as STRIPE_SECRET_KEY ===
stripe_key = os.getenv("STRIPE_SECRET_KEY")
if not stripe_key:
    logger.error("STRIPE_SECRET_KEY not set in environment variables!")
    raise ValueError("STRIPE_SECRET_KEY environment variable is required")

stripe.api_key = stripe_key

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Production mode
PROD_MODE = os.getenv("FLASK_ENV") == "production"
app.config['DEBUG'] = not PROD_MODE

# Default redirect URLs (can be overridden via env)
BASE_URL = os.getenv("BASE_URL", "http://localhost:4242")
SUCCESS_URL = os.getenv("SUCCESS_URL", f"{BASE_URL}/thank-you")
CANCEL_URL  = os.getenv("CANCEL_URL",  f"{BASE_URL}/error")

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
        duration = int(data.get("duration", 1))  # weeks or months
        donor_name = data.get("donor_name", "Anonymous")
        donor_email = data.get("donor_email", "")
        includes_zakat = data.get("includes_zakat", False)
        zakat_percentage = int(data.get("zakat_percentage", 0))

        # Validate units
        if units < MIN_UNITS or units > MAX_UNITS:
            return jsonify({"error": f"Units must be between {MIN_UNITS} and {MAX_UNITS}."}), 400

        # Validate duration
        if frequency == "weekly" and (duration < 1 or duration > 26):
            return jsonify({"error": "Duration must be between 1 and 26 weeks."}), 400
        if frequency == "monthly" and (duration < 1 or duration > 6):
            return jsonify({"error": "Duration must be between 1 and 6 months."}), 400

        # Calculate per-installment amount
        per_installment_amount = units * UNIT_PRICE
        
        # For one-time, charge the full amount based on duration if specified
        if frequency == "once":
            total_amount = per_installment_amount * duration
        else:
            # For recurring, charge per-installment per period
            total_amount = per_installment_amount

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

        # Calculate zakat amount if applicable
        zakat_amount = 0
        if includes_zakat and zakat_percentage > 0:
            zakat_amount = (total_amount * zakat_percentage) / 100

        # Build session parameters
        session_params = {
            "mode": "subscription" if is_recurring else "payment",
            "line_items": [{"price_data": price_data, "quantity": 1}],
            "success_url": SUCCESS_URL,
            "cancel_url": CANCEL_URL,
            "metadata": {
                "units": str(units),
                "donor_name": donor_name,
                "frequency": frequency,
                "duration": str(duration),
                "includes_zakat": str(includes_zakat),
                "zakat_percentage": str(zakat_percentage),
                "zakat_amount": str(zakat_amount)
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
        zakat_info = ""
        if metadata.get('includes_zakat') == 'True':
            zakat_info = f" | Zakat: {metadata.get('zakat_percentage')}% (${metadata.get('zakat_amount')})"
        logger.info(f"Pledge completed: {metadata.get('donor_name')} for {metadata.get('units')} units | Frequency: {metadata.get('frequency')} | Duration: {metadata.get('duration')}{zakat_info} | Session: {session['id']}")
        # TODO: record pledge; send thank-you email; update CRM
    
    elif event["type"] == "invoice.paid":
        invoice = event["data"]["object"]
        # Recurring pledge payment received
        logger.info(f"Recurring payment received: {invoice.get('id')} | Subscription: {invoice.get('subscription')}")
        # TODO: record recurring payment

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
    port = int(os.getenv("PORT", 4242))
    app.run(port=port, debug=not PROD_MODE)
