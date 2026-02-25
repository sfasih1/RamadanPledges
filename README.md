# Ramadan Pledges — Stripe Donations (Flask + Checkout)

A Flask app for collecting Ramadan pledges with flexible payment options. Donors can pledge units (each worth $1,000) and choose to pay all at once, weekly, or monthly. Recurring payments continue automatically until completed.

## Features

- **80 Units** available at **$1,000 per unit**
- **Three payment frequencies:**
  - Pay all at once
  - Weekly installments
  - Monthly installments
- **Automatic recurring billing** for weekly/monthly pledges
- Donor name and email collection
- Multiple currency support (USD, GBP, EUR, AED)
- Webhook integration for order tracking
- Responsive design with Ramadan theme

## Local Setup

1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   # macOS/Linux:
   source .venv/bin/activate
   # Windows PowerShell:
   # .\.venv\Scripts\Activate.ps1

   pip install -r requirements.txt
   ```

2. Create `.env` by copying `.env.example` and **paste your STRIPE_SECRET_KEY**:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and add your test/live Stripe Secret Key:
   ```
   STRIPE_SECRET_KEY=sk_test_YOUR_KEY_HERE
   ```

3. Run the app:
   ```bash
   python app.py
   ```

4. In another terminal, run Stripe CLI to forward webhooks:
   ```bash
   stripe login         # once
   stripe listen --forward-to localhost:4242/webhook
   ```
   Copy the printed `whsec_...` into `.env` as `STRIPE_WEBHOOK_SECRET` and restart `python app.py`.

5. Visit `http://localhost:4242/` and test pledges using Stripe test cards.

## Stripe Test Cards

- **Success:** 4242 4242 4242 4242
- **Decline:** 4000 0000 0000 0002
- **Exp:** Any future date
- **CVC:** Any 3 digits

## How Recurring Payments Work

- **Weekly:** Donor is charged weekly until pledge is completed or cancelled
- **Monthly:** Donor is charged monthly until pledge is completed or cancelled
- **Automatic:** Stripe handles collection automatically on the scheduled date
- **Webhooks:** The app receives `invoice.paid` events for each recurring payment

## Deployment (example: Render)

1. Push to GitHub and create a new Web Service on Render pointing at the repo.
2. Set environment variables in Render's dashboard:
   - `STRIPE_SECRET_KEY` — your **live** Stripe key
   - `STRIPE_WEBHOOK_SECRET` — obtained after setting up webhook endpoint
   - `SUCCESS_URL` — e.g., `https://yourdomain.com/thank-you`
   - `CANCEL_URL` — e.g., `https://yourdomain.com/error`

3. After deploy, go to Stripe Dashboard → Developers → Webhooks → **Add endpoint**
   - Endpoint URL: `https://<your-service>.onrender.com/webhook`
   - Subscribe to: `checkout.session.completed`, `invoice.paid`
   - Copy the **live** `whsec_...` and add to Render env vars

4. Link your site's "Pledge" button to your deployed app URL.

## Configuration

Edit `app.py` to customize:
- `TOTAL_UNITS` — number of units (default: 80)
- `UNIT_PRICE` — price per unit in dollars (default: 1000)
- `SUCCESS_URL` / `CANCEL_URL` — redirect URLs

## Notes

- Recurring payments continue until the pledge is fully paid or manually cancelled
- Metadata is stored with each session for tracking pledges
- Webhook events are logged to stdout
- For production, add database storage to track pledge status and donor information
