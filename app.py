import os, math, logging, json, html
from datetime import datetime, timezone
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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Unit tracking file
UNITS_FILE = os.path.join(BASE_DIR, "units_data.json")
INITIAL_UNITS = {
    "aminah": 10,
    "dreamers": 78,
}

ZERO_DECIMAL_CURRENCIES = {"bif","clp","djf","gnf","jpy","kmf","krw","mga","pyg","rwf","ugx","vnd","vuv","xaf","xof","xpf"}
MAX_START_DATE = datetime(2026, 4, 20, tzinfo=timezone.utc)

def default_units_data():
    return {
        "remaining_units": INITIAL_UNITS.copy(),
        "processed_sessions": []
    }

# Initialize units file if it doesn't exist
def init_units():
    if not os.path.exists(UNITS_FILE):
        with open(UNITS_FILE, 'w') as f:
            json.dump(default_units_data(), f)

def load_units_data():
    init_units()
    try:
        with open(UNITS_FILE, 'r') as f:
            data = json.load(f)
    except Exception:
        data = default_units_data()
        with open(UNITS_FILE, 'w') as f:
            json.dump(data, f)
        return data

    # Migrate legacy flat structure into the current schema.
    if "remaining_units" not in data:
        data = {
            "remaining_units": {
                "aminah": data.get("aminah", INITIAL_UNITS["aminah"]),
                "dreamers": data.get("dreamers", INITIAL_UNITS["dreamers"]),
            },
            "processed_sessions": data.get("processed_sessions", [])
        }
        with open(UNITS_FILE, 'w') as f:
            json.dump(data, f)

    return data

def save_units_data(data):
    with open(UNITS_FILE, 'w') as f:
        json.dump(data, f)

def store_pledge(pledge_record):
    """Persist a completed/scheduled pledge record to units_data.json."""
    data = load_units_data()
    pledges = data.setdefault("pledges", [])
    if not any(p.get("session_id") == pledge_record["session_id"] for p in pledges):
        pledges.append(pledge_record)
        save_units_data(data)

def session_already_processed(session_id):
    data = load_units_data()
    return session_id in data.get("processed_sessions", [])

def mark_session_processed(session_id):
    data = load_units_data()
    processed_sessions = data.setdefault("processed_sessions", [])
    if session_id not in processed_sessions:
        processed_sessions.append(session_id)
        save_units_data(data)

def get_remaining_units(organization="aminah"):
    """Get remaining units for a specific organization"""
    data = load_units_data()
    remaining_units = data.get("remaining_units", {})

    if organization == "both":
        return min(
            remaining_units.get("aminah", INITIAL_UNITS["aminah"]),
            remaining_units.get("dreamers", INITIAL_UNITS["dreamers"])
        )

    try:
        return remaining_units.get(organization, INITIAL_UNITS[organization])
    except Exception:
        return INITIAL_UNITS.get(organization, 0)

def decrement_units(organization, units_to_decrement):
    """Decrement units for a specific organization"""
    data = load_units_data()
    try:
        remaining_units = data.setdefault("remaining_units", INITIAL_UNITS.copy())
        
        # Handle "both" organization - decrement from both aminah and dreamers
        if organization == "both":
            for org in ["aminah", "dreamers"]:
                remaining = remaining_units.get(org, INITIAL_UNITS[org])
                remaining = max(0, remaining - units_to_decrement)
                remaining_units[org] = remaining
        else:
            remaining = remaining_units.get(organization, INITIAL_UNITS[organization])
            remaining = max(0, remaining - units_to_decrement)
            remaining_units[organization] = remaining
        
        save_units_data(data)
        return data
    except Exception:
        return data

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
        organization = (data.get("organization") or "aminah").lower()
        donation_type = (data.get("donation_type") or "units").lower()
        currency = (data.get("currency") or "usd").lower()
        frequency = (data.get("frequency") or "monthly").lower()  # once | weekly | monthly
        duration = int(data.get("duration", 1))  # weeks or months
        donor_name = data.get("donor_name", "Anonymous")
        donor_email = data.get("donor_email", "")
        includes_zakat = data.get("includes_zakat", False)
        zakat_amount = float(data.get("zakat_amount", 0))
        is_dedicated = data.get("is_dedicated", False)
        dedication_names = data.get("dedication_names", "")
        start_date_str = data.get("start_date", "").strip()  # YYYY-MM-DD, optional

        # Determine amount based on donation type
        if donation_type == "units":
            units = int(data.get("units", 1))
            # Validate units
            if units < MIN_UNITS or units > MAX_UNITS:
                return jsonify({"error": f"Units must be between {MIN_UNITS} and {MAX_UNITS}."}), 400
            per_installment_amount = units * UNIT_PRICE
            product_name = f"Ramadan Pledge - {units} Unit(s)"
        else:  # custom
            units = None
            custom_amount = float(data.get("custom_amount", 0))
            if custom_amount <= 0:
                return jsonify({"error": "Custom amount must be greater than 0."}), 400
            per_installment_amount = custom_amount
            product_name = "Ramadan Pledge - Custom Donation"

        # Validate duration
        if frequency == "weekly" and (duration < 1 or duration > 26):
            return jsonify({"error": "Duration must be between 1 and 26 weeks."}), 400
        if frequency == "monthly" and (duration < 1 or duration > 6):
            return jsonify({"error": "Duration must be between 1 and 6 months."}), 400

    # For recurring with duration > 1, this becomes a payment plan (divide
    # the pledge across the selected periods). A one-time pledge with a
    # future start date will later be converted into a one-cycle
    # subscription so Stripe can delay the single collection.
        if frequency == "once":
            total_amount = per_installment_amount * duration
            per_period_amount = total_amount  # charge it all at once
        elif duration > 1:
            # Payment plan: charge per_installment_amount total, split across duration periods
            total_amount = per_installment_amount
            per_period_amount = total_amount / duration
        else:
            # True recurring: charge per_installment_amount every period
            total_amount = per_installment_amount
            per_period_amount = total_amount

        unit_amount = to_unit_amount(per_period_amount, currency)

        # Guardrails
        MIN = 100 if currency not in ZERO_DECIMAL_CURRENCIES else 1
        MAX = 100000000
        if unit_amount < MIN or unit_amount > MAX:
            return jsonify({"error": "Amount out of allowed range."}), 400

        is_recurring = frequency in ("weekly", "monthly")
        price_data = {
            "currency": currency,
            "unit_amount": unit_amount,
            "product_data": {"name": product_name}
        }
        
        if is_recurring:
            price_data["recurring"] = {
                "interval": "week" if frequency == "weekly" else "month"
            }

        # Resolve start date for recurring and scheduled one-time pledges.
        start_timestamp = None
        if start_date_str:
            try:
                start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                today_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                if start_dt < today_utc:
                    return jsonify({"error": "Start date cannot be in the past."}), 400
                if start_dt > MAX_START_DATE:
                    return jsonify({"error": "Start date cannot be later than 2026-04-20."}), 400
                if start_dt > today_utc:
                    start_timestamp = int(start_dt.timestamp())
            except ValueError:
                return jsonify({"error": "Invalid start_date format. Use YYYY-MM-DD."}), 400

        scheduled_one_time = False  # one-time payments are always charged immediately

        # Build metadata
        metadata = {
            "organization": organization,
            "donation_type": donation_type,
            "donor_name": donor_name,
            "donor_email": donor_email,
            "frequency": frequency,
            "duration": str(duration),
            "includes_zakat": str(includes_zakat),
            "zakat_amount": str(zakat_amount),
            "is_dedicated": str(is_dedicated),
            "dedication_names": dedication_names
        }
        if start_timestamp:
            metadata["start_date"] = start_date_str
        
        # Include units only if donation type is units
        if donation_type == "units":
            metadata["units"] = str(units)

        # Build session parameters
        session_params = {
            "mode": "subscription" if is_recurring else "payment",
            "line_items": [{"price_data": price_data, "quantity": 1}],
            "success_url": SUCCESS_URL,
            "cancel_url": CANCEL_URL,
            "metadata": metadata
        }

        # For recurring pledges, attach metadata to the subscription so the webhook
        # can read duration and cancel once fully collected.
        # If a future start date was chosen, set trial_end to delay the first charge.
        if is_recurring:
            subscription_data = {"metadata": metadata}
            if start_timestamp:
                subscription_data["trial_end"] = start_timestamp
            session_params["subscription_data"] = subscription_data

        # Add customer email if provided
        if donor_email:
            session_params["customer_email"] = donor_email

        session = stripe.checkout.Session.create(**session_params)

        return jsonify({"url": session.url})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.get("/get-units")
def get_units():
    organization = request.args.get("organization", "aminah").lower()
    remaining = get_remaining_units(organization)
    return jsonify({"organization": organization, "remaining_units": remaining})

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
        session_id = session.get("id")
        # One-time: session["payment_intent"]
        # Recurring: session["subscription"]
        org_value = metadata.get('organization', 'aminah')
        if org_value == 'both':
            org_name = "Both Organizations (Aminah Islamic Center & Muslim Dreamers Learning Center)"
        elif org_value == 'dreamers':
            org_name = "Muslim Dreamers Learning Center"
        else:
            org_name = "Aminah Islamic Center"
        donation_info = f"{metadata.get('units')} units" if metadata.get('units') else "custom donation"
        zakat_info = ""
        if metadata.get('includes_zakat') == 'True':
            zakat_info = f" | Zakat Amount: ${metadata.get('zakat_amount')}"
        dedication_info = ""
        if metadata.get('is_dedicated') == 'True' and metadata.get('dedication_names'):
            dedication_info = f" | Dedicated to: {metadata.get('dedication_names')}"

        if metadata.get('donation_type') == 'units' and session_id and not session_already_processed(session_id):
            units_value = int(metadata.get('units', '0') or '0')
            if units_value > 0:
                decrement_units(org_value, units_value)
            mark_session_processed(session_id)

        logger.info(f"Pledge completed: {metadata.get('donor_name')} for {donation_info} to {org_name} | Frequency: {metadata.get('frequency')} | Duration: {metadata.get('duration')}{zakat_info}{dedication_info} | Session: {session['id']}")
        # Persist pledge locally for admin review
        pledge_record = {
            "session_id": session_id,
            "donor_name": metadata.get("donor_name", "Anonymous"),
            "donor_email": metadata.get("donor_email", ""),
            "organization": org_value,
            "donation_type": metadata.get("donation_type", ""),
            "units": metadata.get("units", ""),
            "frequency": metadata.get("frequency", ""),
            "duration": metadata.get("duration", ""),
            "includes_zakat": metadata.get("includes_zakat", "False") == "True",
            "zakat_amount": metadata.get("zakat_amount", "0"),
            "is_dedicated": metadata.get("is_dedicated", "False") == "True",
            "dedication_names": metadata.get("dedication_names", ""),
            "start_date": metadata.get("start_date", ""),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "scheduled": bool(metadata.get("start_date")),
        }
        store_pledge(pledge_record)
    
    elif event["type"] == "invoice.paid":
        invoice = event["data"]["object"]
        subscription_id = invoice.get("subscription")
        logger.info(f"Recurring payment received: {invoice.get('id')} | Subscription: {subscription_id}")
        # TODO: record recurring payment

        # Cancel the subscription once the full pledge has been collected.
        if subscription_id:
            try:
                subscription = stripe.Subscription.retrieve(subscription_id)
                sub_metadata = subscription.get("metadata", {})
                duration = int(sub_metadata.get("duration", 0))
                frequency = sub_metadata.get("frequency", "")

                if duration > 0 and frequency in ("weekly", "monthly", "once"):
                    # Count all paid invoices for this subscription (max duration is 26).
                    # Exclude $0 trial-start invoices that Stripe generates when trial_end is used.
                    paid_invoices = stripe.Invoice.list(
                        subscription=subscription_id,
                        status="paid",
                        limit=50
                    )
                    paid_count = sum(1 for inv in paid_invoices.data if inv.get("amount_paid", 0) > 0)
                    logger.info(f"Subscription {subscription_id}: {paid_count}/{duration} payment(s) collected")

                    if paid_count >= duration:
                        stripe.Subscription.cancel(subscription_id)
                        logger.info(f"Subscription {subscription_id} cancelled — full pledge of {duration} payment(s) collected")
            except Exception as e:
                logger.error(f"Error checking/cancelling subscription {subscription_id}: {e}")

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

@app.get("/admin/pledges")
def admin_pledges():
    admin_token = os.getenv("ADMIN_TOKEN")
    if not admin_token or request.args.get("token", "") != admin_token:
        return "Unauthorized. Set ADMIN_TOKEN env var and pass ?token=YOUR_TOKEN in the URL.", 401

    data = load_units_data()
    pledges = data.get("pledges", [])
    scheduled = [p for p in pledges if p.get("scheduled")]
    active    = [p for p in pledges if not p.get("scheduled")]

    def esc(s):
        return html.escape(str(s) if s is not None else "")

    def pledge_row(p):
        zakat = f"Yes &mdash; ${esc(p.get('zakat_amount', '0'))}" if p.get("includes_zakat") else "No"
        ded   = f"Yes &mdash; {esc(p.get('dedication_names', ''))}" if p.get("is_dedicated") else "No"
        return (
            "<tr>"
            f"<td>{esc(p.get('recorded_at', '')[:10])}</td>"
            f"<td>{esc(p.get('donor_name', ''))}</td>"
            f"<td>{esc(p.get('donor_email', ''))}</td>"
            f"<td>{esc(p.get('organization', ''))}</td>"
            f"<td>{esc(p.get('donation_type', ''))}</td>"
            f"<td>{esc(p.get('units', ''))}</td>"
            f"<td>{esc(p.get('frequency', ''))} &times; {esc(p.get('duration', ''))}</td>"
            f"<td>{zakat}</td>"
            f"<td>{ded}</td>"
            f"<td>{esc(p.get('start_date', '') or 'Immediate')}</td>"
            "</tr>"
        )

    def table_rows(lst):
        if not lst:
            return "<tr><td colspan='10' style='text-align:center;color:#999;padding:1rem'>None yet</td></tr>"
        return "".join(pledge_row(p) for p in reversed(lst))

    headers = (
        "<tr><th>Date</th><th>Donor</th><th>Email</th><th>Org</th>"
        "<th>Type</th><th>Units</th><th>Schedule</th>"
        "<th>Zakat</th><th>Dedicated To</th><th>Start Date</th></tr>"
    )
    page = f"""<!doctype html>
<html><head>
  <meta charset="utf-8">
  <title>Pledge Admin \u2014 Ramadan Pledges</title>
  <style>
    body{{font-family:system-ui,sans-serif;padding:2rem;background:#f5f5f5;margin:0}}
    h1{{color:#764ba2}} h2{{color:#444;margin-top:0}}
    .card{{background:#fff;border-radius:.75rem;padding:1.5rem;margin-bottom:2rem;box-shadow:0 2px 8px rgba(0,0,0,.1)}}
    table{{border-collapse:collapse;width:100%;font-size:.88rem;overflow-x:auto;display:block}}
    th,td{{border:1px solid #e0e0e0;padding:.45rem .7rem;text-align:left;white-space:nowrap}}
    th{{background:#f0f4ff;font-weight:600}} tr:hover{{background:#fafafa}}
    .badge{{display:inline-block;padding:2px 10px;border-radius:12px;font-size:.8rem;margin-left:.4rem}}
    .sch{{background:#fff3cd;color:#856404}} .act{{background:#d1e7dd;color:#0f5132}}
  </style>
</head><body>
  <h1>\U0001f319 Ramadan Pledges \u2014 Admin</h1>
  <div class="card">
    <h2>\u23f0 Scheduled (Future-Dated) Pledges <span class="badge sch">{len(scheduled)}</span></h2>
    <table>{headers}{table_rows(scheduled)}</table>
  </div>
  <div class="card">
    <h2>\u2705 Active / Immediate Pledges <span class="badge act">{len(active)}</span></h2>
    <table>{headers}{table_rows(active)}</table>
  </div>
</body></html>"""
    return page

if __name__ == "__main__":
    port = int(os.getenv("PORT", 4242))
    app.run(port=port, debug=not PROD_MODE)
