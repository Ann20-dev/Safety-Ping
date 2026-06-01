# SafetyPing

SafetyPing is a worker safety and incident reporting prototype for Kenyan construction sites.

Workers use USSD to:

- check in for shifts
- report incidents
- read daily briefings in English, Kiswahili, or Sheng
- check out when leaving site

Supervisors use the web dashboard to:

- view shift status
- scan for missed check-ins
- review incident reports
- see queued alerts

## Run

```powershell
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000.

## USSD Examples

Post form data to `/ussd`:

- Empty `text`: main menu
- `1`: check in
- `2*3*2*Loose ladder at slab edge`: report a medium unsafe equipment incident
- `3`: daily briefing
- `4`: check out

Seed worker phone numbers:

- `+254700111001`
- `+254700111002`
- `+254700111003`

## Africa's Talking Setup

Create a `.env` file from `.env.example` and add your credentials.

Sandbox:

```powershell
AFRICASTALKING_ENVIRONMENT=sandbox
AFRICASTALKING_USERNAME=sandbox
AFRICASTALKING_API_KEY=your_sandbox_key
AFRICASTALKING_SHORTCODE=79064
```

Production:

```powershell
AFRICASTALKING_ENVIRONMENT=production
AFRICASTALKING_USERNAME=your_app_username
AFRICASTALKING_API_KEY=your_production_key
AFRICASTALKING_SENDER_ID=YOUR_SENDER_ID
```

USSD callback URL:

```text
https://your-public-url/ussd
```

SMS callback URL:

```text
https://your-public-url/sms
```

SMS echo callback URL, compatible with the sample you shared:

```text
https://your-public-url/sms_callback
```

The echo callback stores inbound SMS and replies with the same text when an API key is configured.

For local sandbox testing, expose the app with ngrok:

```powershell
ngrok http 8000
```

The backend uses Africa's Talking legacy bulk SMS in sandbox and the new bulk SMS endpoint in production.

Your sample variable names are also supported:

```powershell
SANDBOX_API_KEY=your_sandbox_key
SANDBOX_USERNAME=sandbox
SMS_SHORTCODE=79064
AT_MESSAGING_URL=https://api.sandbox.africastalking.com/version1/messaging
```
