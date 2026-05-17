"""
TradeEasy WhatsApp Bot
======================
Handles 4 trader types:
1. Street Seller   - voice/text cash logging
2. POS Trader      - auto SMS capture (Moniepoint/OPay)
3. Transfer Trader - auto SMS capture (GTBank/Access etc)
4. Hybrid          - all three combined

Stack:
- Flask (web server)
- Twilio (WhatsApp + SMS)
- Firebase (database)
- OpenAI Whisper (voice transcription)

Deploy on Render.com (free tier)
"""

from flask import Flask, request, jsonify
import os, re, json
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, db
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import requests

app = Flask(__name__)

# ── CONFIG (set these in Render environment variables) ──
TWILIO_SID    = os.environ.get("TWILIO_SID")
TWILIO_TOKEN  = os.environ.get("TWILIO_TOKEN")
TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER")   # Your Twilio WhatsApp number
FIREBASE_URL  = os.environ.get("FIREBASE_URL")     # Your Firebase database URL
OPENAI_KEY    = os.environ.get("OPENAI_KEY")       # For voice transcription

twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)

# ── FIREBASE SETUP ──
if not firebase_admin._apps:
    cred = credentials.Certificate(json.loads(os.environ.get("FIREBASE_CREDS", "{}")))
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_URL})


# ══════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════

def get_trader(phone):
    """Get trader data from Firebase"""
    ref = db.reference(f"traders/{clean_phone(phone)}")
    return ref.get() or {}

def save_trader(phone, data):
    """Save trader data to Firebase"""
    ref = db.reference(f"traders/{clean_phone(phone)}")
    ref.update(data)

def log_transaction(phone, txn_type, amount, item="", source="manual"):
    """Log a transaction to Firebase"""
    ref = db.reference(f"transactions/{clean_phone(phone)}")
    today = datetime.now().strftime("%Y-%m-%d")
    txn = {
        "type":   txn_type,   # "sale" or "expense"
        "amount": amount,
        "item":   item,
        "source": source,     # "voice", "text", "sms_pos", "sms_transfer"
        "time":   datetime.now().isoformat(),
        "date":   today
    }
    ref.push(txn)

def get_today_summary(phone):
    """Calculate today's P&L for a trader"""
    today = datetime.now().strftime("%Y-%m-%d")
    ref = db.reference(f"transactions/{clean_phone(phone)}")
    all_txns = ref.get() or {}
    sales, expenses = 0, 0
    breakdown = {"cash": 0, "pos": 0, "transfer": 0}
    for txn in all_txns.values():
        if txn.get("date") != today:
            continue
        amount = txn.get("amount", 0)
        if txn["type"] == "sale":
            sales += amount
            src = txn.get("source", "manual")
            if "pos" in src:       breakdown["pos"]      += amount
            elif "transfer" in src: breakdown["transfer"] += amount
            else:                   breakdown["cash"]     += amount
        else:
            expenses += amount
    return {
        "sales":     sales,
        "expenses":  expenses,
        "profit":    sales - expenses,
        "breakdown": breakdown
    }

def clean_phone(phone):
    """Normalize phone number for Firebase key"""
    return re.sub(r"[^\d]", "", phone)

def format_naira(amount):
    return f"₦{amount:,.0f}"

def generate_dashboard_html(phone, trader_name, summary):
    """Generate a beautiful HTML dashboard for the trader"""
    profit = summary["profit"]
    profit_color = "#2a6b2a" if profit >= 0 else "#b83232"
    profit_emoji = "📈" if profit >= 0 else "📉"
    today = datetime.now().strftime("%A, %d %B %Y")
    b = summary["breakdown"]

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TradeEasy — {trader_name}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family: Arial, sans-serif; background: #f5f0e8; padding: 20px; }}
  .card {{ background: #fff; border-radius: 16px; padding: 24px; margin-bottom: 16px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  .header {{ background: #1a1208; border-radius: 16px; padding: 24px; margin-bottom: 16px; color: #fff; }}
  .logo {{ font-size: 22px; font-weight: 900; color: #7fcc7f; margin-bottom: 4px; }}
  .date {{ font-size: 12px; color: rgba(255,255,255,0.5); margin-bottom: 16px; }}
  .profit {{ font-size: 40px; font-weight: 900; color: {profit_color}; }}
  .profit-label {{ font-size: 12px; color: rgba(255,255,255,0.5); text-transform: uppercase; letter-spacing: 2px; }}
  .row {{ display: flex; gap: 12px; margin-bottom: 16px; }}
  .stat {{ flex: 1; background: #f5f0e8; border-radius: 12px; padding: 16px; text-align: center; }}
  .stat-val {{ font-size: 20px; font-weight: 700; }}
  .stat-lbl {{ font-size: 11px; color: #8c7a62; margin-top: 4px; }}
  .green {{ color: #2a6b2a; }} .red {{ color: #b83232; }}
  .section-title {{ font-size: 14px; font-weight: 700; margin-bottom: 12px; color: #3d2f1a; }}
  .breakdown-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #f0ebe4; font-size: 13px; }}
  .footer {{ text-align: center; font-size: 11px; color: #8c7a62; margin-top: 8px; }}
</style>
</head>
<body>
  <div class="header">
    <div class="logo">TradeEasy ⚡</div>
    <div class="date">{today}</div>
    <div class="profit-label">Today's Profit {profit_emoji}</div>
    <div class="profit">{format_naira(profit)}</div>
  </div>

  <div class="row">
    <div class="stat">
      <div class="stat-val green">{format_naira(summary['sales'])}</div>
      <div class="stat-lbl">Total Sales</div>
    </div>
    <div class="stat">
      <div class="stat-val red">{format_naira(summary['expenses'])}</div>
      <div class="stat-lbl">Total Expenses</div>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Sales Breakdown</div>
    <div class="breakdown-row"><span>💵 Cash Sales</span><span class="green">{format_naira(b['cash'])}</span></div>
    <div class="breakdown-row"><span>💳 POS Sales (Moniepoint/OPay)</span><span class="green">{format_naira(b['pos'])}</span></div>
    <div class="breakdown-row" style="border:none;"><span>📲 Transfer Sales (GTBank etc)</span><span class="green">{format_naira(b['transfer'])}</span></div>
  </div>

  <div class="footer">
    Powered by TradeEasy · {trader_name} · {today}
  </div>
</body>
</html>"""


# ══════════════════════════════════════════════
# SMS PARSERS — Auto-capture POS & Transfer SMS
# ══════════════════════════════════════════════

def parse_moniepoint_sms(text):
    """Extract amount from Moniepoint transaction SMS"""
    # Example: "Moniepoint: You have received NGN 15,000.00 from..."
    patterns = [
        r"received\s+NGN\s*([\d,]+\.?\d*)",
        r"credit\s+NGN\s*([\d,]+\.?\d*)",
        r"NGN\s*([\d,]+\.?\d*)\s+credited",
        r"transaction.*?NGN\s*([\d,]+\.?\d*)",
    ]
    for p in patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match:
            amount_str = match.group(1).replace(",", "")
            return float(amount_str)
    return None

def parse_opay_sms(text):
    """Extract amount from OPay transaction SMS"""
    patterns = [
        r"received\s+N([\d,]+\.?\d*)",
        r"credit.*?N([\d,]+\.?\d*)",
        r"N([\d,]+\.?\d*)\s+has been credited",
    ]
    for p in patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", ""))
    return None

def parse_bank_transfer_sms(text):
    """Extract amount from GTBank/Access/Zenith transfer SMS"""
    patterns = [
        r"Cr\s+NGN\s*([\d,]+\.?\d*)",
        r"credit.*?NGN\s*([\d,]+\.?\d*)",
        r"NGN([\d,]+\.?\d*)\s+credited",
        r"transfer.*?NGN\s*([\d,]+\.?\d*)",
        r"received.*?NGN\s*([\d,]+\.?\d*)",
    ]
    for p in patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", ""))
    return None

def classify_sms(text):
    """Detect what kind of transaction SMS this is"""
    text_lower = text.lower()
    if "moniepoint" in text_lower:
        amount = parse_moniepoint_sms(text)
        return ("pos", amount, "Moniepoint")
    elif "opay" in text_lower:
        amount = parse_opay_sms(text)
        return ("pos", amount, "OPay")
    elif any(bank in text_lower for bank in ["gtbank", "guaranty", "access bank", "zenith", "uba", "first bank", "fidelity"]):
        amount = parse_bank_transfer_sms(text)
        return ("transfer", amount, "Bank Transfer")
    return (None, None, None)


# ══════════════════════════════════════════════
# MESSAGE PARSER — What did the trader say?
# ══════════════════════════════════════════════

def parse_trader_message(text):
    """Parse a trader's WhatsApp text message"""
    text = text.strip().lower()

    # Check for daily summary request
    if text in ["profit", "summary", "report", "today", "balance"]:
        return {"action": "summary"}

    # Check for weekly report
    if text in ["week", "weekly", "this week"]:
        return {"action": "weekly"}

    # Check for help
    if text in ["help", "commands", "options"]:
        return {"action": "help"}

    # Parse SALE: handles typed and voice variations
    # "sold onion 5000", "sold 5000", "I sold onion for five thousand"
    # "sale onion 5000", "onion 5000 sale", "5000 for onion"
    sale_patterns = [
        r"^(?:sold?|sale|income|received?|got|collect(?:ed)?)\s+(?:(\w+)\s+)?([\d,]+)",
        r"^([\d,]+)\s+(?:sale|sold|income)",
        r"^(?:sold?|sale)\s+([\d,]+)",           # "sold 5000" no item
        r"(\w+)\s+([\d,]+)\s+(?:sale|sold)",     # "onion 5000 sold"
    ]
    for p in sale_patterns:
        m = re.search(p, text)
        if m:
            groups = [g for g in m.groups() if g is not None]
            if len(groups) == 2:
                # figure out which is item and which is amount
                g0, g1 = groups
                if re.match(r"^\d+$", g0) and not re.match(r"^\d+$", g1):
                    item, amount_str = g1, g0
                elif re.match(r"^\d+$", g1):
                    item, amount_str = g0, g1
                else:
                    item, amount_str = "Sale", g0
            elif len(groups) == 1:
                item, amount_str = "Sale", groups[0]
            else:
                continue
            try:
                amount = float(amount_str.replace(",", ""))
                return {"action": "sale", "amount": amount, "item": item.title()}
            except:
                continue

    # Parse EXPENSE: handles typed and voice variations
    # "expense transport 500", "bought rice 5000", "spent 2000 on transport"
    expense_patterns = [
        r"^(?:expense|exp|spent?|bought?|paid?|cost|buy)\s+(?:(\w+)\s+)?([\d,]+)",
        r"^([\d,]+)\s+(?:expense|spent|cost)",
        r"^(?:expense|exp)\s+([\d,]+)",           # "expense 2000" no item
        r"spent?\s+([\d,]+)\s+(?:on\s+)?(\w+)",  # "spent 2000 on transport"
    ]
    for p in expense_patterns:
        m = re.search(p, text)
        if m:
            groups = [g for g in m.groups() if g is not None]
            if len(groups) == 2:
                g0, g1 = groups
                if re.match(r"^\d+$", g0):
                    item, amount_str = g1, g0
                elif re.match(r"^\d+$", g1):
                    item, amount_str = g0, g1
                else:
                    item, amount_str = "Expense", g0
            elif len(groups) == 1:
                item, amount_str = "Expense", groups[0]
            else:
                continue
            try:
                amount = float(amount_str.replace(",", ""))
                return {"action": "expense", "amount": amount, "item": item.title()}
            except:
                continue

    # Parse DEBT: "debt mama chioma 12000"
    debt_match = re.search(r"^(?:debt|owe|owes?)\s+(.+?)\s+([\d,]+)", text)
    if debt_match:
        return {
            "action": "debt",
            "name":   debt_match.group(1).title(),
            "amount": float(debt_match.group(2).replace(",", ""))
        }

    # Parse UNDO LAST: "undo", "cancel", "remove last"
    if text in ["undo", "cancel", "remove last", "delete last", "mistake"]:
        return {"action": "undo"}

    # Parse REMOVE SALE: "remove sale 5000", "delete sale 3000", "cancel sale 5000"
    remove_sale_match = re.search(r"^(?:remove|delete|cancel)\s+sale\s+([\d,]+)", text)
    if remove_sale_match:
        return {
            "action": "remove_sale",
            "amount": float(remove_sale_match.group(1).replace(",", ""))
        }

    # Parse REMOVE EXPENSE: "remove expense 2000", "delete expense 500"
    remove_exp_match = re.search(r"^(?:remove|delete|cancel)\s+(?:expense|exp)\s+([\d,]+)", text)
    if remove_exp_match:
        return {
            "action": "remove_expense",
            "amount": float(remove_exp_match.group(1).replace(",", ""))
        }

    return {"action": "unknown", "raw": text}


# ══════════════════════════════════════════════
# VOICE TRANSCRIPTION
# ══════════════════════════════════════════════

def undo_last_transaction(phone):
    """Remove the most recent transaction for a trader"""
    today = datetime.now().strftime("%Y-%m-%d")
    ref = db.reference(f"transactions/{clean_phone(phone)}")
    all_txns = ref.get() or {}

    # Find today's transactions sorted by time
    todays = [(k, v) for k, v in all_txns.items() if v.get("date") == today]
    if not todays:
        return None

    # Sort by time descending, get most recent
    todays.sort(key=lambda x: x[1].get("time", ""), reverse=True)
    last_key, last_txn = todays[0]

    # Delete it
    ref.child(last_key).delete()
    return last_txn

def remove_transaction_by_amount(phone, txn_type, amount):
    """Remove the most recent transaction matching type and amount"""
    today = datetime.now().strftime("%Y-%m-%d")
    ref = db.reference(f"transactions/{clean_phone(phone)}")
    all_txns = ref.get() or {}

    # Find matching transactions today
    matches = [
        (k, v) for k, v in all_txns.items()
        if v.get("date") == today
        and v.get("type") == txn_type
        and abs(v.get("amount", 0) - amount) < 1
    ]
    if not matches:
        return None

    # Delete the most recent match
    matches.sort(key=lambda x: x[1].get("time", ""), reverse=True)
    key, txn = matches[0]
    ref.child(key).delete()
    return txn


def transcribe_voice(media_url):
    """Transcribe a WhatsApp voice note using OpenAI Whisper"""
    try:
        auth = (TWILIO_SID, TWILIO_TOKEN)
        audio_response = requests.get(media_url, auth=auth)
        headers = {"Authorization": f"Bearer {OPENAI_KEY}"}
        files   = {"file": ("audio.ogg", audio_response.content, "audio/ogg")}
        data    = {"model": "whisper-1", "language": "en"}
        response = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers=headers, files=files, data=data
        )
        result = response.json()
        raw = result.get("text", "")
        print(f"[WHISPER RAW]: {raw}")   # logs what Whisper heard — check Render logs
        return normalize_voice_text(raw)
    except Exception as e:
        print(f"Voice transcription error: {e}")
        return ""


def normalize_voice_text(text):
    """
    Clean up Whisper transcription so the parser can understand it.
    Handles: word numbers, filler words, punctuation, Nigerian speech patterns.
    """
    if not text:
        return ""

    t = text.lower().strip()

    # ── Remove punctuation ──
    t = re.sub(r"[.,!?;:]", " ", t)

    # ── Remove common filler/prefix words ──
    # e.g. "I sold", "so I sold", "please record", "um sold", "okay sold"
    fillers = [
        r"^(so\s+)?(i\s+)?",
        r"^(please\s+)?(record\s+)?",
        r"^(um+\s+)?(uh+\s+)?",
        r"^(okay\s+)?(ok\s+)?",
        r"^(just\s+)?",
    ]
    for f in fillers:
        t = re.sub(f, "", t).strip()

    # ── Convert word numbers to digits ──
    word_numbers = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
        "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
        "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
        "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
        "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
        "eighty": "80", "ninety": "90", "hundred": "100",
        # Nigerian common amounts
        "thousand": "000", "k": "000",
        "five thousand": "5000", "ten thousand": "10000",
        "twenty thousand": "20000", "fifty thousand": "50000",
        "hundred thousand": "100000", "one thousand": "1000",
        "two thousand": "2000", "three thousand": "3000",
        "four thousand": "4000", "six thousand": "6000",
        "seven thousand": "7000", "eight thousand": "8000",
        "nine thousand": "9000", "fifteen thousand": "15000",
    }
    # Replace multi-word numbers first (longest first)
    for word, digit in sorted(word_numbers.items(), key=lambda x: -len(x[0])):
        t = re.sub(r"\b" + word + r"\b", digit, t)

    # ── Fix "5 000" → "5000" (Whisper sometimes adds space in numbers) ──
    t = re.sub(r"(\d+)\s+000", r"\g<1>000", t)

    # ── Remove commas in numbers: "5,000" → "5000" ──
    t = re.sub(r"(\d),(\d)", r"\1\2", t)

    # ── Normalize Nigerian speech patterns ──
    # "na" = "is/it's" in Pidgin, often said before amounts
    t = re.sub(r"\bna\b", "", t)
    # "for" before amount e.g. "sold onion for 5000"
    t = re.sub(r"\bfor\b\s+(\d)", r"\1", t)
    # "naira" after amount e.g. "5000 naira"
    t = re.sub(r"(\d+)\s+naira", r"\1", t)

    # ── Clean up extra spaces ──
    t = re.sub(r"\s+", " ", t).strip()

    print(f"[NORMALIZED]: {t}")   # check Render logs
    return t


# ══════════════════════════════════════════════
# RESPONSE BUILDER
# ══════════════════════════════════════════════

def build_response(action_result, summary):
    """Build a friendly WhatsApp reply message"""
    action = action_result.get("action")

    if action == "sale":
        amount = action_result["amount"]
        item   = action_result.get("item", "Sale")
        heard  = action_result.get("heard", "")
        heard_line = f"🎤 _Heard: \"{heard}\"_\n\n" if heard else ""
        return (
            f"✅ *Sale recorded!*\n\n"
            f"{heard_line}"
            f"📦 {item}: {format_naira(amount)}\n"
            f"🕐 {datetime.now().strftime('%I:%M %p')}\n\n"
            f"*Today so far:*\n"
            f"Sales: {format_naira(summary['sales'])}\n"
            f"Expenses: {format_naira(summary['expenses'])}\n"
            f"*Profit: {format_naira(summary['profit'])}* 📈\n\n"
            f"_Reply HELP for all commands_"
        )

    elif action == "expense":
        amount = action_result["amount"]
        item   = action_result.get("item", "Expense")
        heard  = action_result.get("heard", "")
        heard_line = f"🎤 _Heard: \"{heard}\"_\n\n" if heard else ""
        return (
            f"✅ *Expense recorded!*\n\n"
            f"{heard_line}"
            f"🛒 {item}: {format_naira(amount)}\n"
            f"🕐 {datetime.now().strftime('%I:%M %p')}\n\n"
            f"*Updated profit: {format_naira(summary['profit'])}* 📊"
        )

    elif action == "summary":
        b = summary["breakdown"]
        return (
            f"📊 *Today's Summary*\n"
            f"{datetime.now().strftime('%A, %d %B %Y')}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💵 Cash:     {format_naira(b['cash'])}\n"
            f"💳 POS:      {format_naira(b['pos'])}\n"
            f"📲 Transfer: {format_naira(b['transfer'])}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 Total Sales: {format_naira(summary['sales'])}\n"
            f"🛒 Expenses:    {format_naira(summary['expenses'])}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"*NET PROFIT: {format_naira(summary['profit'])}* 🎉"
        )

    elif action == "auto_pos":
        return (
            f"✅ *POS payment auto-captured!*\n\n"
            f"💳 {action_result.get('source', 'POS')}: {format_naira(action_result['amount'])}\n"
            f"🕐 {datetime.now().strftime('%I:%M %p')}\n\n"
            f"*Today's sales: {format_naira(summary['sales'])}*"
        )

    elif action == "auto_transfer":
        return (
            f"✅ *Transfer auto-captured!*\n\n"
            f"📲 {action_result.get('source', 'Bank')}: {format_naira(action_result['amount'])}\n"
            f"🕐 {datetime.now().strftime('%I:%M %p')}\n\n"
            f"*Today's sales: {format_naira(summary['sales'])}*"
        )

    elif action == "help":
        return (
            "*TradeEasy Commands* ⚡\n\n"
            "💰 *Log a Sale:*\n"
            "`sold 5000`\n"
            "`sold tomatoes 3500`\n\n"
            "🛒 *Log an Expense:*\n"
            "`expense 2000`\n"
            "`bought rice 5000`\n\n"
            "❌ *Remove a Mistake:*\n"
            "`undo` — removes last entry\n"
            "`remove sale 5000` — removes that sale\n"
            "`remove expense 2000` — removes that expense\n\n"
            "📊 *Check Today:*\n"
            "`profit` or `summary`\n\n"
            "📅 *Weekly Report:*\n"
            "`week`\n\n"
            "🎤 *Voice:* Just send a voice note!\n\n"
            "_POS & transfers are captured automatically_"
        )

    else:
        return (
            "I didn't understand that. Try:\n\n"
            "• `sold 5000` — to log a sale\n"
            "• `expense 2000` — to log a cost\n"
            "• `profit` — to see today's summary\n"
            "• `help` — for all commands\n\n"
            "_Or send a voice note_ 🎤"
        )


# ══════════════════════════════════════════════
# MAIN WEBHOOK — WhatsApp & SMS messages arrive here
# ══════════════════════════════════════════════

@app.route("/webhook", methods=["POST"])
def webhook():
    """Main entry point — all WhatsApp messages come here"""

    phone      = request.form.get("From", "")
    body       = request.form.get("Body", "").strip()
    num_media  = int(request.form.get("NumMedia", 0))
    media_type = request.form.get("MediaContentType0", "")
    media_url  = request.form.get("MediaUrl0", "")

    resp = MessagingResponse()
    msg  = resp.message()

    # ── Get or create trader ──
    trader = get_trader(phone)
    if not trader:
        save_trader(phone, {
            "phone":    phone,
            "name":     "Trader",
            "type":     "hybrid",
            "joined":   datetime.now().isoformat()
        })
        msg.body(
            "👋 *Welcome to TradeEasy!*\n\n"
            "I'll help you track your sales and profit automatically.\n\n"
            "To start, just send:\n"
            "`sold [amount]` — e.g. `sold 5000`\n\n"
            "Or send a *voice note* to log a sale! 🎤\n\n"
            "Reply *help* for all commands."
        )
        return str(resp)

    trader_name = trader.get("name", "Trader")

    # ── Handle voice note ──
    if num_media > 0 and "audio" in media_type:
        transcribed = transcribe_voice(media_url)
        if transcribed:
            body = transcribed
        else:
            msg.body(
                "❌ Sorry, I couldn't hear that clearly.\n\n"
                "Try again or type it:\n"
                "`sold onion 5000`\n"
                "`expense transport 500`"
            )
            return str(resp)

    # ── Check if this is a POS/Transfer SMS ──
    sms_type, sms_amount, sms_source = classify_sms(body)
    if sms_type and sms_amount:
        log_transaction(phone, "sale", sms_amount, sms_source, f"sms_{sms_type}")
        summary = get_today_summary(phone)
        action_result = {"action": f"auto_{sms_type}", "amount": sms_amount, "source": sms_source}
        msg.body(build_response(action_result, summary))
        return str(resp)

    # ── Parse normal message ──
    parsed = parse_trader_message(body)
    # If from voice, attach what was heard for display
    if num_media > 0 and "audio" in media_type:
        parsed["heard"] = body
    action = parsed.get("action")

    if action == "sale":
        log_transaction(phone, "sale", parsed["amount"], parsed.get("item", ""), "manual")
    elif action == "expense":
        log_transaction(phone, "expense", parsed["amount"], parsed.get("item", ""), "manual")
    elif action == "debt":
        db.reference(f"debts/{clean_phone(phone)}").push({
            "name":   parsed["name"],
            "amount": parsed["amount"],
            "date":   datetime.now().isoformat()
        })
    elif action == "undo":
        removed = undo_last_transaction(phone)
        if removed:
            summary = get_today_summary(phone)
            txn_type = "Sale" if removed["type"] == "sale" else "Expense"
            msg.body(
                f"↩️ *Last entry removed!*\n\n"
                f"{txn_type}: {format_naira(removed['amount'])} ({removed.get('item','')})\n"
                f"has been deleted.\n\n"
                f"*Updated profit: {format_naira(summary['profit'])}*"
            )
        else:
            msg.body("❌ No transactions found today to remove.")
        return str(resp)
    elif action == "remove_sale":
        removed = remove_transaction_by_amount(phone, "sale", parsed["amount"])
        if removed:
            summary = get_today_summary(phone)
            msg.body(
                f"↩️ *Sale removed!*\n\n"
                f"Sale of {format_naira(parsed['amount'])} deleted.\n\n"
                f"*Updated profit: {format_naira(summary['profit'])}*"
            )
        else:
            msg.body(
                f"❌ Could not find a sale of {format_naira(parsed['amount'])} today.\n\n"
                f"Try `undo` to remove the last entry instead."
            )
        return str(resp)
    elif action == "remove_expense":
        removed = remove_transaction_by_amount(phone, "expense", parsed["amount"])
        if removed:
            summary = get_today_summary(phone)
            msg.body(
                f"↩️ *Expense removed!*\n\n"
                f"Expense of {format_naira(parsed['amount'])} deleted.\n\n"
                f"*Updated profit: {format_naira(summary['profit'])}*"
            )
        else:
            msg.body(
                f"❌ Could not find an expense of {format_naira(parsed['amount'])} today.\n\n"
                f"Try `undo` to remove the last entry instead."
            )
        return str(resp)

    summary = get_today_summary(phone)
    msg.body(build_response(parsed, summary))
    return str(resp)


# ══════════════════════════════════════════════
# DAILY REPORT — Called by a scheduler at 8pm
# ══════════════════════════════════════════════

@app.route("/send-daily-reports", methods=["POST"])
def send_daily_reports():
    """Send daily HTML dashboard to all traders at 8pm"""
    traders_ref = db.reference("traders")
    traders = traders_ref.get() or {}

    sent = 0
    for phone_key, trader in traders.items():
        phone       = trader.get("phone", "")
        name        = trader.get("name", "Trader")
        summary     = get_today_summary(phone)

        if summary["sales"] == 0 and summary["expenses"] == 0:
            continue  # Skip traders with no activity today

        # Generate dashboard HTML
        dashboard = generate_dashboard_html(phone, name, summary)

        # Save dashboard to Firebase (accessible via link)
        today = datetime.now().strftime("%Y-%m-%d")
        dash_ref = db.reference(f"dashboards/{clean_phone(phone)}/{today}")
        dash_ref.set({"html": dashboard, "generated": datetime.now().isoformat()})

        # Send SMS with link
        dashboard_link = f"https://your-render-url.com/dashboard/{clean_phone(phone)}/{today}"
        sms_body = (
            f"TradeEasy Daily Report 📊\n"
            f"{datetime.now().strftime('%a %d %b %Y')}\n\n"
            f"Sales: {format_naira(summary['sales'])}\n"
            f"Expenses: {format_naira(summary['expenses'])}\n"
            f"PROFIT: {format_naira(summary['profit'])}\n\n"
            f"Full report: {dashboard_link}"
        )

        twilio_client.messages.create(
            body=sms_body,
            from_=TWILIO_NUMBER,
            to=phone
        )
        sent += 1

    return jsonify({"status": "ok", "sent": sent})


# ══════════════════════════════════════════════
# DASHBOARD ENDPOINT — Trader clicks their daily link
# ══════════════════════════════════════════════

@app.route("/dashboard/<phone>/<date>")
def serve_dashboard(phone, date):
    """Serve the HTML dashboard when trader clicks their link"""
    from flask import Response
    ref  = db.reference(f"dashboards/{phone}/{date}")
    data = ref.get()
    if data and "html" in data:
        return Response(data["html"], mimetype="text/html")
    return "Dashboard not found", 404


# ══════════════════════════════════════════════
# ADMIN ENDPOINT — You monitor all traders
# ══════════════════════════════════════════════

@app.route("/admin")
def admin():
    """Simple admin view of all traders"""
    traders_ref = db.reference("traders")
    traders = traders_ref.get() or {}
    today   = datetime.now().strftime("%Y-%m-%d")

    rows = ""
    total_sales = 0
    for phone_key, trader in traders.items():
        phone   = trader.get("phone", "")
        name    = trader.get("name", "Unknown")
        summary = get_today_summary(phone)
        total_sales += summary["sales"]
        profit_color = "#2a6b2a" if summary["profit"] >= 0 else "#b83232"
        rows += f"""
        <tr>
          <td>{name}</td>
          <td>{phone}</td>
          <td style="color:#2a6b2a">₦{summary['sales']:,.0f}</td>
          <td style="color:#b83232">₦{summary['expenses']:,.0f}</td>
          <td style="color:{profit_color};font-weight:bold">₦{summary['profit']:,.0f}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head>
  <title>TradeEasy Admin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: Arial, sans-serif; background: #f5f0e8; padding: 20px; }}
    h1 {{ font-size: 24px; margin-bottom: 4px; }}
    .total {{ font-size: 32px; font-weight: 900; color: #2a6b2a; margin-bottom: 20px; }}
    table {{ width: 100%; background: white; border-radius: 12px; border-collapse: collapse; overflow: hidden; }}
    th {{ background: #1a1208; color: white; padding: 12px; text-align: left; font-size: 12px; }}
    td {{ padding: 12px; border-bottom: 1px solid #f0ebe4; font-size: 13px; }}
    tr:last-child td {{ border-bottom: none; }}
  </style>
</head>
<body>
  <h1>TradeEasy Admin 📊</h1>
  <p style="color:#8c7a62; margin-bottom:8px;">{today} · {len(traders)} traders</p>
  <div class="total">Total: ₦{total_sales:,.0f}</div>
  <table>
    <tr><th>Trader</th><th>Phone</th><th>Sales</th><th>Expenses</th><th>Profit</th></tr>
    {rows}
  </table>
</body>
</html>"""


# ── START ──
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
