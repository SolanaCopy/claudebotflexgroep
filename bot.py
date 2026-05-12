"""
Telegram Bot that uses Claude to answer messages.
Supports multi-turn conversations with per-user memory.
"""

import os
import json
import logging
import httpx
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    ChatMemberHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatAction

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))
FLEXBOT_SERVER = os.getenv("FLEXBOT_SERVER", "https://flexbot-qpf2.onrender.com")
FLEXBOT_KEY = os.getenv("FLEXBOT_KEY", "Tanger2026@")
COMMUNITY_CHAT_ID = int(os.getenv("COMMUNITY_CHAT_ID", "-1003611276978"))
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "8210317741"))

# Milestone tracking file
MILESTONE_FILE = os.path.join(os.path.dirname(__file__), "data", "milestones.json")

def load_milestones() -> dict:
    try:
        with open(MILESTONE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"total_trades": 0, "total_wins": 0, "best_streak": 0, "notified": []}

def save_milestones(data: dict) -> None:
    os.makedirs(os.path.dirname(MILESTONE_FILE), exist_ok=True)
    with open(MILESTONE_FILE, "w") as f:
        json.dump(data, f)

# Poll/sentiment tracking
POLL_FILE = os.path.join(os.path.dirname(__file__), "data", "poll_state.json")

def load_poll_state() -> dict:
    try:
        with open(POLL_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_poll_state(data: dict) -> None:
    os.makedirs(os.path.dirname(POLL_FILE), exist_ok=True)
    with open(POLL_FILE, "w") as f:
        json.dump(data, f)

# Create Anthropic client
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Per-user conversation memory: {user_id: [messages]}
conversation_history: dict[int, list[dict]] = {}

SYSTEM_PROMPT = """You are Flexbot, the AI assistant of the Flexbot trading community.

FREQUENTLY ASKED QUESTIONS — use these answers as a basis:

1. What is Flexbot?
→ Flexbot is a fully automated Expert Advisor (EA) for MetaTrader 5, specifically built for passing FTMO challenges. The bot trades XAUUSD (gold), analyzes charts live and opens and closes trades fully on its own.

2. What does it cost?
→ Contact the admin for current pricing and available packages. Payment is via USDT on the ERC-20 network. After payment you receive the EA file and help with installation.

3. How do I install the EA?
→ 1) Download and install MetaTrader 5 with your broker. 2) Copy the Flexbot EA file to: File → Open Data Folder → MQL5 → Experts. 3) Restart MT5. 4) Drag the EA from the Navigator onto an XAUUSD chart. 5) Make sure AutoTrading is enabled (green button at the top). The admin will help you with the right settings if needed.

4. Which broker?
→ Flexbot works on any broker that offers MetaTrader 5. For FTMO challenges you obviously use the FTMO platform. For testing we recommend Vantage for its low spreads on gold.

5. Which pairs?
→ Flexbot is specifically built and optimized for XAUUSD (gold). This is the only pair supported.

6. Results/profit?
→ The bot is optimized for FTMO challenges with a 79% success rate in backtests (1 year of data, realistic with spread and slippage). Results vary — trading always carries risk. Always start on a demo account first.

7. Is it safe for FTMO?
→ Yes. The bot is built with all FTMO rules in mind. It risks max 0.5% per trade, avoids high-impact news, doesn't do one-side betting, and prevents other patterns FTMO watches for. The EA runs locally on your MT5 — you stay in 100% control.

8. Does it work on MT4?
→ No, Flexbot is built exclusively for MetaTrader 5 (MT5).

9. Can I change the settings?
→ The default settings are carefully optimized for FTMO challenges. We recommend not changing them. Contact the admin if you still want to adjust something.

10. How do I get support?
→ Ask your question here in the group or DM the admin.

11. How much starting capital do I need?
→ For FTMO you choose your challenge size (e.g. $10K, $25K, $50K, $100K). The bot scales automatically with the account size and always risks max 0.5% per trade.

12. Does my PC always have to be on?
→ Yes, the EA needs to be running on MT5. We recommend a VPS so the bot runs 24/5 without interruptions. Especially during an FTMO challenge you don't want missed trades. The admin can help with this.

13. How many trades does the bot place per day?
→ Depends on the market. The bot patiently waits for the right conditions and never forces a trade. Quality over quantity — important for passing your challenge.

14. How does the bot handle news?
→ The bot automatically avoids trading around high-impact news events. This protects you from unpredictable market moves and aligns with what FTMO expects of responsible risk management.

15. What if my internet drops?
→ Open trades stay with your broker with their SL and TP, so you're always protected. On reconnect the bot continues. A VPS prevents this issue.

16. Does the bot have a stop-loss?
→ Yes, every trade has a stop-loss and take-profit. Max 0.5% risk per trade. That keeps you well within the FTMO drawdown limits.

17. Can I run the bot on multiple accounts?
→ Depends on your license. Contact the admin for options.

18. Is the bot updated?
→ Yes, Flexbot is continuously improved and optimized. Updates are shared via the community.

19. Does the bot work on weekends?
→ No, the forex market is closed on weekends. The bot only trades when the market is open.

20. What is one-side betting and does the bot do that?
→ One-side betting is always trading the same direction (only buy or only sell). FTMO watches for this. Flexbot trades both directions based on market analysis, so this is not an issue.

21. How long does it take to pass a challenge?
→ It varies per market period. The bot doesn't hit the target in one day — it trades consistently and safely. On average it's realistic within the challenge period if the market cooperates.

22. What if the bot makes a loss?
→ Losses are part of trading. The bot keeps losses small (max 0.5% per trade) so you stay well within the FTMO daily loss and max drawdown limits.

23. How do I turn the bot off?
→ Click "AutoTrading" in MT5 (turns red) or remove the EA from the chart. Open trades remain with their SL/TP.

24. Can I test the bot first?
→ Yes, open a free demo account and run the bot on it. That way you can see how it works risk-free before starting an FTMO challenge.

25. What if I don't pass the FTMO challenge?
→ No bot can give a 100% guarantee. The bot is optimized for the best success rate, but market conditions always play a role. You can always start a new challenge.

RULES:
- ALWAYS reply in English, regardless of the user's language.
- Only answer questions about: Flexbot EA, trading (forex/XAUUSD), FTMO, and support.
- Anything out of scope: "I'm only here for Flexbot, trading and support! 🤖📈"
- Answer EXTREMELY SHORT: max 1-2 sentences. NEVER more than 3 sentences. No lists, no bullet points, no explanations unless asked.
- Talk human and chill, like a friend in a group chat. Short sentences. No stiff AI-speak. The occasional emoji but don't overdo it.
- FORBIDDEN: long paragraphs, multiple paragraphs, disclaimers, "if you'd like to know more". Just short answers and done.
- For news questions: explain what the event means and how gold has historically reacted. NEVER claim you can predict the market — only give context and historical tendencies.
- If the bot isn't trading due to news blackout, explain which event is coming up.
- If someone curses, spams, or posts scam-like messages (e.g. "send crypto to...", "guaranteed profit", suspicious links), respond firmly but short: "⚠️ This kind of message is not welcome here. Keep it respectful and on-topic."
"""


async def fetch_live_data() -> str:
    """Fetch live trade data from the Flexbot server."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Fetch recent trades, server state and news
            trades_resp = await client.get(
                f"{FLEXBOT_SERVER}/api/mc/trades",
                params={"key": FLEXBOT_KEY, "limit": 10},
            )
            state_resp = await client.get(
                f"{FLEXBOT_SERVER}/api/mc/state",
                params={"key": FLEXBOT_KEY},
            )
            news_resp = await client.get(
                f"{FLEXBOT_SERVER}/ff/red",
                params={"key": FLEXBOT_KEY},
            )

            parts = []

            if trades_resp.status_code == 200:
                data = trades_resp.json()
                trades = data.get("trades", data) if isinstance(data, dict) else data
                if trades:
                    parts.append("LATEST TRADES:")
                    for t in trades[:10]:
                        direction = t.get("direction", "?")
                        sl = t.get("sl", "?")
                        tp = t.get("tp", "?")
                        status = t.get("status", "?")
                        outcome = t.get("close_outcome", "?")
                        profit = t.get("close_result", "?")
                        created = t.get("created_at_ms", 0)
                        closed = t.get("closed_at_ms", 0)
                        # Convert timestamps to readable time
                        from datetime import datetime, timezone
                        open_str = datetime.fromtimestamp(created / 1000, tz=timezone.utc).strftime("%d-%m %H:%M") if created else "?"
                        close_str = datetime.fromtimestamp(closed / 1000, tz=timezone.utc).strftime("%d-%m %H:%M") if closed else "?"
                        parts.append(
                            f"- {direction} | SL:{sl} TP:{tp} | "
                            f"status:{status} | outcome:{outcome} | result:{profit} | "
                            f"opened:{open_str} closed:{close_str}"
                        )

            if state_resp.status_code == 200:
                state = state_resp.json()

                # EA positions (equity, balance, open trades)
                ea_pos = state.get("ea_positions", [])
                if ea_pos:
                    parts.append("\nACCOUNT STATUS:")
                    for p in ea_pos:
                        has_pos = "YES" if p.get("has_position") else "NO"
                        parts.append(
                            f"- Account:{p.get('account_login')} | Equity:${p.get('equity')} | "
                            f"Balance:${p.get('balance')} | Open position:{has_pos}"
                        )

                # Market status
                market = state.get("market", {})
                blocked = market.get("blocked", False)
                parts.append(f"\nMARKET: {'BLOCKED - ' + str(market.get('reason', '')) if blocked else 'OPEN'}")

                # Trade gates
                gates = state.get("trade_gates", {})
                if gates:
                    verdict = gates.get("verdict", "?")
                    dd = gates.get("daily_loss", {})
                    dd_pct = dd.get("dd_pct", 0)
                    consec = gates.get("consec_losses", {})
                    losses = consec.get("losses", 0)
                    news = gates.get("news_blackout", {})
                    news_pass = news.get("pass", True)
                    parts.append(
                        f"GATES: {verdict} | Daily DD:{dd_pct}% | "
                        f"Consecutive losses:{losses} | News blackout:{'NO' if news_pass else 'YES'}"
                    )

                # Signal prep
                prep = state.get("signal_prep", {})
                if prep:
                    price = prep.get("price", "?")
                    trend = prep.get("trend", "?")
                    parts.append(f"CURRENT PRICE: {price} | TREND: {trend}")

            if news_resp.status_code == 200:
                news_data = news_resp.json()
                events = news_data.get("events", [])
                if events:
                    # Only USD events (relevant for gold)
                    usd_events = [e for e in events if e.get("currency") == "USD"]
                    if usd_events:
                        parts.append("\nUSD NEWS EVENTS (high impact):")
                        for e in usd_events:
                            title = e.get("title", "?")
                            ts = e.get("ts", 0)
                            from datetime import datetime, timezone
                            utc_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%d-%m %H:%M UTC") if ts else "?"
                            forecast = e.get("forecast", "-")
                            previous = e.get("previous", "-")
                            actual = e.get("actual", "-")
                            parts.append(
                                f"- {title} | {utc_str} | forecast:{forecast} | "
                                f"previous:{previous} | actual:{actual or '-'}"
                            )

            return "\n".join(parts) if parts else "No live data available."

    except Exception as e:
        logger.warning(f"Could not fetch live data: {e}")
        return "Live data temporarily unavailable."


def get_history(user_id: int) -> list[dict]:
    """Return the conversation history for a user."""
    return conversation_history.get(user_id, [])


def add_to_history(user_id: int, role: str, content: str) -> None:
    """Append a message to the conversation history."""
    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({"role": role, "content": content})

    # Cap history at MAX_HISTORY messages (always in pairs)
    history = conversation_history[user_id]
    if len(history) > MAX_HISTORY:
        # Drop the oldest two messages (user + assistant pair)
        conversation_history[user_id] = history[-MAX_HISTORY:]


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == ADMIN_USER_ID)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start command handler — admin only."""
    if not _is_admin(update):
        logger.info(f"Ignored /start from non-admin user {update.effective_user.id if update.effective_user else '?'}")
        return
    user = update.effective_user
    await update.message.reply_text(
        f"Hi {user.first_name}! 👋\n\n"
        "I'm Flexbot, the AI assistant of this community. "
        "Feel free to ask me a question!\n\n"
        "Use /help for more info or /reset to start the conversation over."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help command handler — admin only."""
    if not _is_admin(update):
        logger.info(f"Ignored /help from non-admin user {update.effective_user.id if update.effective_user else '?'}")
        return
    await update.message.reply_text(
        "🤖 *Flexbot*\n\n"
        "*Commands:*\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/reset - Clear the conversation memory\n\n"
        "*Usage:*\n"
        "Just send a message and I'll reply using Claude AI. "
        "I remember the context of our conversation!\n\n"
        f"_Up to {MAX_HISTORY} messages are remembered._",
        parse_mode="Markdown",
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reset command handler — admin only."""
    if not _is_admin(update):
        logger.info(f"Ignored /reset from non-admin user {update.effective_user.id if update.effective_user else '?'}")
        return
    user_id = update.effective_user.id
    conversation_history.pop(user_id, None)
    await update.message.reply_text(
        "🔄 Conversation memory cleared! Starting fresh."
    )


# ============================================================
# REFERRAL PROGRAM — public commands (everyone in the group can use)
# ============================================================
async def toprefs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/toprefs — show current month's referral leaderboard."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{FLEXBOT_SERVER}/api/leaderboard")
            data = r.json()
        if not data.get("ok"):
            await update.message.reply_text("Leaderboard unavailable right now, try again later.")
            return
        board = data.get("leaderboard") or []
        month = data.get("month", "this month")
        if not board:
            await update.message.reply_text(
                f"🏆 *Referral leaderboard ({month})*\n\nNo invites yet this month — be the first!\n\nUse /myref to grab your link.",
                parse_mode="Markdown",
            )
            return
        lines = [f"🏆 *Referral leaderboard ({month})*\n"]
        medals = ["🥇", "🥈", "🥉"]
        for entry in board[:10]:
            rank = entry.get("rank", 0)
            badge = medals[rank - 1] if rank <= 3 else f"{rank}."
            name = entry.get("name", "??")
            invites = entry.get("invites", 0)
            plural = "invite" if invites == 1 else "invites"
            lines.append(f"{badge}  *{name}*  —  {invites} {plural}")
        lines.append("\n💰 Top 1 at month end wins 1 month free of Flexbot.")
        lines.append("Use /myref to see your stats and link.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        logger.warning(f"toprefs failed: {e}")
        await update.message.reply_text("Couldn't fetch leaderboard right now.")


async def myref_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/myref <api_key> — show YOUR referral stats + invite link.

    Customers DM the bot with their api_key from the install preset, or paste it
    after the command in the group (will be deleted to keep it private).
    """
    args = context.args or []
    api_key = args[0].strip() if args else ""
    if not api_key:
        await update.message.reply_text(
            "Send your invite stats privately:\n\n"
            "DM me with:\n"
            "  /myref <your_api_key>\n\n"
            "Your api_key is in the .set preset you got with the installer "
            "(InpEaApiKey, starts with `fb_`).",
            parse_mode="Markdown",
        )
        return
    if not api_key.startswith("fb_"):
        await update.message.reply_text("That doesn't look like a valid api_key (should start with `fb_`).")
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{FLEXBOT_SERVER}/api/myref", params={"api_key": api_key})
            data = r.json()
        if not data.get("ok"):
            await update.message.reply_text(f"Couldn't load your stats: {data.get('error', 'unknown')}")
            return
        link = data.get("ref_link", "—")
        month = data.get("invites_this_month", 0)
        total = data.get("total_invites", 0)
        rank = data.get("rank_this_month", "—")
        msg = (
            f"🎯 *Your referral stats*\n\n"
            f"📎 Your invite link:\n`{link}`\n\n"
            f"📊 This month: *{month}* invites  (rank #{rank})\n"
            f"📈 All-time: *{total}* invites\n\n"
            f"💰 Top 1 at month end wins 1 month free!\n"
            f"Share your link, climb the leaderboard, win.\n\n"
            f"See full board: /toprefs"
        )
        await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        logger.warning(f"myref failed: {e}")
        await update.message.reply_text("Couldn't fetch your stats right now.")


import re

# Profanity and scam patterns
BANNED_WORDS = [
    "kanker", "tering", "tyfus", "hoer", "kut", "fuck", "shit", "nigger",
    "nigga", "flikker", "mongool", "retard", "bitch", "asshole", "dick",
]

SCAM_PATTERNS = [
    r"send\s+\d+\s*(btc|eth|usdt|crypto)",
    r"guaranteed\s+(profit|return|income)",
    r"gegarandeerde\s+(winst|inkomsten)",
    r"dm\s+me\s+for\s+(profit|signals|investment)",
    r"invest\s+\$?\d+.*get\s+\$?\d+",
    r"double\s+your\s+(money|crypto|investment)",
    r"free\s+(signals|money|crypto)",
    r"t\.me/[a-zA-Z0-9_]+",  # Telegram links (potential spam groups)
    r"bit\.ly/",  # Shortened links
    r"wa\.me/",  # WhatsApp links
]


async def moderate_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check message for profanity and scam. Returns True if the message was blocked."""
    if not update.message or not update.message.text:
        return False

    text = update.message.text.lower()
    chat_id = update.effective_chat.id
    user = update.effective_user

    # Check profanity
    for word in BANNED_WORDS:
        if word in text:
            try:
                await update.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ @{user.username or user.first_name}, profanity is not welcome here. Keep it respectful!",
            )
            logger.info(f"Moderation: profanity from {user.id} removed")
            return True

    # Check scam patterns
    for pattern in SCAM_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            try:
                await update.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🚫 Spam/scam detected and removed. This kind of message is not allowed here.",
            )
            logger.info(f"Moderation: scam/spam from {user.id} removed")
            return True

    return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process incoming messages and reply via Claude."""
    user_id = update.effective_user.id
    user_text = update.message.text

    logger.info(f"Message from user {user_id}: {user_text[:50]}...")

    # Moderation: check for profanity, scam, spam
    if await moderate_message(update, context):
        return  # Message already handled by moderation

    # Show 'typing...' indicator
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )

    # Fetch live trade data from the server
    live_data = await fetch_live_data()

    # Add the user message to the history
    add_to_history(user_id, "user", user_text)

    try:
        # Build system prompt with live data
        system_with_data = (
            SYSTEM_PROMPT
            + "\n\nLIVE SERVER DATA (REAL-TIME, ALWAYS CURRENT):\n"
            + live_data
            + "\n\nIMPORTANT: This live data is ALWAYS the most recent state. "
            "Ignore trade info from earlier messages in the conversation — that is outdated. "
            "If someone asks about 'the latest trade', use ONLY the live data above."
        )

        # Call Claude with streaming for responsiveness
        with claude.messages.stream(
            model="claude-haiku-4-5",
            max_tokens=150,
            system=system_with_data,
            messages=get_history(user_id),
        ) as stream:
            response = stream.get_final_message()

        # Extract the text from the response
        assistant_text = next(
            block.text for block in response.content if block.type == "text"
        )

        # Save the response in the history
        add_to_history(user_id, "assistant", assistant_text)

        # Send the response to the user
        await update.message.reply_text(assistant_text)

        logger.info(f"Reply sent to user {user_id}")

    except anthropic.AuthenticationError:
        logger.error("Invalid Anthropic API key")
        await update.message.reply_text(
            "❌ Configuration error: invalid API key. Please contact the admin."
        )
    except anthropic.BadRequestError as e:
        msg = str(e).lower()
        if "credit" in msg or "billing" in msg or "balance" in msg:
            logger.error(f"Anthropic credit/billing issue — staying silent: {e}")
            return
        logger.error(f"Bad request to Anthropic API: {e}")
        return
    except anthropic.PermissionDeniedError as e:
        logger.error(f"Anthropic permission denied — staying silent: {e}")
        return
    except anthropic.RateLimitError:
        logger.warning("API rate limit reached — staying silent")
        return
    except anthropic.APIStatusError as e:
        if getattr(e, "status_code", None) in (402, 429):
            logger.error(f"Anthropic status {e.status_code} — staying silent: {e}")
            return
        logger.error(f"Anthropic API error: {e}")
        return
    except anthropic.APIConnectionError:
        logger.error("Connection error with Anthropic API — staying silent")
        return
    except Exception as e:
        logger.error(f"Unexpected error — staying silent: {e}")
        return


async def send_daily_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send daily poll: gold up or down? Store the opening price."""
    now = datetime.now(timezone.utc)
    # Weekdays only (Mon=0 .. Fri=4)
    if now.weekday() >= 5:
        logger.info("Weekend — poll skipped")
        return
    days_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    months_en = ["January", "February", "March", "April", "May", "June",
                 "July", "August", "September", "October", "November", "December"]
    day_str = f"{days_en[now.weekday()]} {now.day} {months_en[now.month - 1]}"

    # Fetch current gold price as opening price
    open_price = None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{FLEXBOT_SERVER}/api/mc/state",
                params={"key": FLEXBOT_KEY},
            )
            if resp.status_code == 200:
                state = resp.json()
                prep = state.get("signal_prep", {})
                open_price = prep.get("price")
    except Exception as e:
        logger.warning(f"Could not fetch opening price: {e}")

    msg = await context.bot.send_poll(
        chat_id=COMMUNITY_CHAT_ID,
        question=f"📊 {day_str} — What's your call, gold up or down today?",
        options=["🟢 Up (bullish)", "🔴 Down (bearish)", "⚪ Sideways"],
        is_anonymous=False,
    )

    # Sla poll state op
    poll_state = {
        "date": now.strftime("%Y-%m-%d"),
        "poll_id": msg.poll.id,
        "message_id": msg.message_id,
        "open_price": open_price,
        "votes": {"bullish": [], "bearish": [], "sideways": []},
    }
    save_poll_state(poll_state)
    logger.info(f"Dagelijkse poll verstuurd, openingsprijs: {open_price}")


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sla poll stemmen op per gebruiker."""
    answer = update.poll_answer
    poll_state = load_poll_state()

    if not poll_state or answer.poll_id != poll_state.get("poll_id"):
        return

    user_id = str(answer.user.id)
    user_name = answer.user.first_name or "Unknown"

    # Verwijder oude stem van deze gebruiker
    for category in ["bullish", "bearish", "sideways"]:
        poll_state["votes"][category] = [
            v for v in poll_state["votes"][category] if v.get("id") != user_id
        ]

    # Voeg nieuwe stem toe
    if answer.option_ids:
        option = answer.option_ids[0]
        categories = {0: "bullish", 1: "bearish", 2: "sideways"}
        cat = categories.get(option, "sideways")
        poll_state["votes"][cat].append({"id": user_id, "name": user_name})

    save_poll_state(poll_state)
    logger.info(f"Poll stem van {user_name}: optie {answer.option_ids}")


async def send_poll_result(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sluit de poll, vergelijk met echte koers, en stuur resultaat."""
    # Alleen doordeweeks (ma=0 t/m vr=4)
    if datetime.now(timezone.utc).weekday() >= 5:
        return
    poll_state = load_poll_state()
    if not poll_state or not poll_state.get("open_price"):
        return

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if poll_state.get("date") != today_str:
        return

    # Haal huidige prijs op
    close_price = None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{FLEXBOT_SERVER}/api/mc/state",
                params={"key": FLEXBOT_KEY},
            )
            if resp.status_code == 200:
                state = resp.json()
                prep = state.get("signal_prep", {})
                close_price = prep.get("price")
    except Exception as e:
        logger.warning(f"Kon slotprijs niet ophalen: {e}")
        return

    if not close_price:
        return

    open_price = poll_state["open_price"]
    diff = close_price - open_price
    pct = (diff / open_price) * 100

    # Bepaal echte richting
    if diff > 5:
        actual = "bullish"
        actual_emoji = "🟢"
        actual_text = "UP"
    elif diff < -5:
        actual = "bearish"
        actual_emoji = "🔴"
        actual_text = "DOWN"
    else:
        actual = "sideways"
        actual_emoji = "⚪"
        actual_text = "SIDEWAYS"

    votes = poll_state.get("votes", {})
    bull_count = len(votes.get("bullish", []))
    bear_count = len(votes.get("bearish", []))
    side_count = len(votes.get("sideways", []))
    total_votes = bull_count + bear_count + side_count

    # Wie had gelijk?
    winners = votes.get(actual, [])
    winner_names = [w["name"] for w in winners]

    # Bouw het bericht
    lines = [
        f"📊 **TODAY'S POLL RESULT**\n",
        f"Gold open: ${open_price:.2f} → close: ${close_price:.2f}",
        f"Move: {actual_emoji} {actual_text} ({pct:+.2f}%)\n",
        f"Votes: 🟢 {bull_count} | 🔴 {bear_count} | ⚪ {side_count}",
    ]

    if winner_names:
        lines.append(f"\n🎯 Called it right: {', '.join(winner_names)}")
    elif total_votes > 0:
        lines.append(f"\n😅 Nobody got it right today!")

    # Sluit de poll
    try:
        await context.bot.stop_poll(
            chat_id=COMMUNITY_CHAT_ID,
            message_id=poll_state["message_id"],
        )
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=COMMUNITY_CHAT_ID,
        text="\n".join(lines),
        parse_mode="Markdown",
    )
    logger.info(f"Poll resultaat verstuurd: {actual_text} ({pct:+.2f}%)")


async def send_market_open(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stuur markt open melding (maandag)."""
    if datetime.now(timezone.utc).weekday() != 0:  # alleen maandag
        return
    await context.bot.send_message(
        chat_id=COMMUNITY_CHAT_ID,
        text="☀️ Good morning! The market is open, Flexbot is live. Let's get it! 💰",
    )
    logger.info("Markt open melding verstuurd")


async def send_market_opening_soon(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stuur zondagavond melding dat de markt straks opengaat."""
    if datetime.now(timezone.utc).weekday() != 6:  # alleen zondag
        return
    await context.bot.send_message(
        chat_id=COMMUNITY_CHAT_ID,
        text="🔔 The market is about to open! Get ready for a new trading week. 💪",
    )
    logger.info("Markt gaat straks open melding verstuurd (zondag)")


async def send_market_close(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stuur markt dicht melding (vrijdag)."""
    if datetime.now(timezone.utc).weekday() != 4:  # alleen vrijdag
        return
    await context.bot.send_message(
        chat_id=COMMUNITY_CHAT_ID,
        text="🌙 Market is closing soon, Flexbot is wrapping up for today. Have a great weekend! 👋",
    )
    logger.info("Markt dicht melding verstuurd")


async def check_milestones(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check dagelijkse profit milestones en stuur melding."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{FLEXBOT_SERVER}/api/mc/trades",
                params={"key": FLEXBOT_KEY, "limit": 200},
            )
            if resp.status_code != 200:
                return
            data = resp.json()
            trades = data.get("trades", [])

        # Filter trades van vandaag (UTC)
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_ms = int(today_start.timestamp() * 1000)

        day_profit = 0.0
        day_wins = 0
        day_trades = 0
        for t in trades:
            closed_ms = t.get("closed_at_ms", 0)
            if closed_ms < today_start_ms:
                continue
            result_str = t.get("close_result", "")
            if "USD" in result_str:
                try:
                    amount = float(result_str.replace("USD", "").strip())
                    day_profit += amount
                    day_trades += 1
                    if amount > 0:
                        day_wins += 1
                except ValueError:
                    pass

        ms = load_milestones()
        today_str = now.strftime("%Y-%m-%d")
        notified = ms.get("notified_today", {})

        # Reset als het een nieuwe dag is
        if notified.get("date") != today_str:
            notified = {"date": today_str, "tags": []}

        tags = notified.get("tags", [])

        # Dagelijkse profit milestones
        daily_milestones = {
            250: "💰 $250 profit today! Nice work Flexbot! 🔥",
            500: "🔥 $500 profit today! Half a K in one day!",
            1000: "🚀 $1,000 profit today! ONE K IN A SINGLE DAY! 💎",
            2000: "🏆 $2,000 profit today! Flexbot is unstoppable!",
            5000: "👑 $5,000 profit today! INSANE day! 🚀🚀🚀",
        }

        for amount, message in daily_milestones.items():
            tag = f"day_profit_{amount}"
            if day_profit >= amount and tag not in tags:
                await context.bot.send_message(
                    chat_id=COMMUNITY_CHAT_ID,
                    text=message,
                )
                tags.append(tag)
                logger.info(f"Daily milestone: {tag} (${day_profit:.2f})")

        # Win streak milestone (3+ wins op rij vandaag)
        if day_wins >= 3 and "win_streak_3" not in tags:
            await context.bot.send_message(
                chat_id=COMMUNITY_CHAT_ID,
                text=f"🔥 {day_wins} wins in a row today! Flexbot is on fire!",
            )
            tags.append("win_streak_3")
            logger.info(f"Win streak milestone: {day_wins} wins")

        notified["tags"] = tags
        ms["notified_today"] = notified
        ms["day_profit"] = day_profit
        ms["day_trades"] = day_trades
        save_milestones(ms)

    except Exception as e:
        logger.warning(f"Milestone check fout: {e}")


# ============================================================
# ANTI-BOT VERIFICATION FOR NEW MEMBERS
# ============================================================
# Strategy:
#   1. New member joins -> bot mutes them (can't post)
#   2. Bot asks a trading/general-knowledge question with 4 answer buttons
#   3. They pick the correct one within 3 min -> permissions restored
#   4. Wrong pick OR no pick -> kicked (ban+unban so real users can retry)
#
# A question beats a single "click here" button: bots that auto-click
# inline buttons would have to also guess correctly out of 4 options.

import asyncio
import random

VERIFY_TIMEOUT_SEC = 180  # 3 minutes

# Pool of trading-leaning questions. Each is (question, correct_answer, wrong_options[]).
VERIFY_QUESTIONS = [
    # Simple math
    ("What is 2 + 2?",                 "4",        ["3", "5", "22"]),
    ("What is 5 + 3?",                 "8",        ["7", "9", "12"]),
    ("What is 10 - 4?",                "6",        ["5", "7", "14"]),
    ("What is 3 x 3?",                 "9",        ["6", "12", "33"]),
    ("What is 7 + 1?",                 "8",        ["6", "9", "71"]),
    # General knowledge — obvious
    ("How many days in a week?",       "7",        ["5", "10", "12"]),
    ("How many minutes in an hour?",   "60",       ["30", "45", "100"]),
    ("What color is the sky on a clear day?", "Blue", ["Red", "Green", "Purple"]),
    ("How many legs does a dog have?", "4",        ["2", "3", "6"]),
    ("Which one is a fruit?",          "Apple",    ["Chair", "Pencil", "Computer"]),
    ("What do bees make?",             "Honey",    ["Milk", "Bread", "Cheese"]),
    # Trading basics — universally known
    ("FlexBot trades which asset?",    "Gold",     ["Stocks", "Bitcoin", "Oil"]),
    ("Which platform does this bot use?", "MetaTrader 5", ["TradingView", "Excel", "Notepad"]),
]

_pending_verify = {}  # key=(chat_id, user_id) -> {"task": Task, "correct": str, "username": str, "msg_id": int}


async def _kick_unverified(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Kick a member who didn't verify in time."""
    try:
        await asyncio.sleep(VERIFY_TIMEOUT_SEC)
        key = (chat_id, user_id)
        if key not in _pending_verify:
            return  # already verified or left
        pending = _pending_verify.pop(key, None)
        try:
            await context.bot.ban_chat_member(chat_id, user_id)
            await context.bot.unban_chat_member(chat_id, user_id)
            logger.info(f"Kicked unverified member {user_id} (timeout)")
        except Exception as e:
            logger.warning(f"Failed to kick unverified {user_id}: {e}")
        # Clean up the verification message
        if pending and pending.get("msg_id"):
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=pending["msg_id"],
                    text=f"⏱ Verification timed out — @{pending.get('username','user')} was removed.",
                )
            except Exception:
                pass
    except asyncio.CancelledError:
        pass


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Triggered when someone joins/leaves/changes status in the group."""
    cm = update.chat_member
    if not cm:
        return
    old = cm.old_chat_member.status
    new = cm.new_chat_member.status
    user = cm.new_chat_member.user

    # Skip bots — admins add them intentionally.
    if user.is_bot:
        return

    # New member just joined (or was readded after a kick)
    if old in ("left", "kicked") and new in ("member", "restricted"):
        chat_id = cm.chat.id
        user_id = user.id
        username = user.username or user.first_name or "newcomer"

        logger.info(f"New member joined: {username} ({user_id}) in chat {chat_id}")

        # Mute them: can't send messages until they answer correctly
        try:
            await context.bot.restrict_chat_member(
                chat_id, user_id,
                ChatPermissions(can_send_messages=False),
            )
        except Exception as e:
            logger.warning(f"Could not restrict {user_id}: {e}")

        # Pick a random question and shuffle answers
        question, correct, wrong = random.choice(VERIFY_QUESTIONS)
        options = [correct] + list(wrong)
        random.shuffle(options)

        # Build inline keyboard (one button per row for readability)
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(opt, callback_data=f"verify:{user_id}:{i}")] for i, opt in enumerate(options)]
        )
        try:
            msg = await context.bot.send_message(
                chat_id,
                f"👋 Welcome @{username}!\n\n"
                f"Quick check before you can post — pick the right answer within 3 minutes:\n\n"
                f"❓ *{question}*",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Could not send verify message: {e}")
            return

        # Schedule the kick if no correct answer within timeout
        key = (chat_id, user_id)
        task = asyncio.create_task(_kick_unverified(context, chat_id, user_id))
        _pending_verify[key] = {
            "task": task,
            "username": username,
            "msg_id": msg.message_id,
            "correct": correct,
            "options": options,
            "question": question,
        }


async def on_verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the answer button click."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("verify:"):
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Bad button", show_alert=True)
        return

    try:
        target_user_id = int(parts[1])
        choice_idx = int(parts[2])
    except Exception:
        await query.answer("Bad button", show_alert=True)
        return

    clicker_id = query.from_user.id
    if clicker_id != target_user_id:
        await query.answer("This question is not for you 🙂", show_alert=True)
        return

    chat_id = query.message.chat.id
    key = (chat_id, target_user_id)
    pending = _pending_verify.get(key)
    if not pending:
        await query.answer("Already verified or expired", show_alert=True)
        return

    chosen = pending["options"][choice_idx] if 0 <= choice_idx < len(pending["options"]) else None
    username = pending.get("username") or query.from_user.username or query.from_user.first_name or "trader"

    # WRONG answer -> kick immediately
    if chosen != pending["correct"]:
        _pending_verify.pop(key, None)
        try:
            pending["task"].cancel()
        except Exception:
            pass
        try:
            await context.bot.ban_chat_member(chat_id, target_user_id)
            await context.bot.unban_chat_member(chat_id, target_user_id)
        except Exception as e:
            logger.warning(f"Failed to kick after wrong answer: {e}")
        try:
            await query.edit_message_text(
                f"❌ Wrong answer — @{username} was removed.\nIf you're human, you can re-join and try again."
            )
        except Exception:
            pass
        await query.answer("Wrong answer — kicked", show_alert=True)
        logger.info(f"Kicked {target_user_id} after wrong answer")
        return

    # CORRECT answer -> lift restrictions
    _pending_verify.pop(key, None)
    try:
        pending["task"].cancel()
    except Exception:
        pass
    try:
        await context.bot.restrict_chat_member(
            chat_id, target_user_id,
            ChatPermissions(
                can_send_messages=True,
                can_send_audios=True, can_send_documents=True,
                can_send_photos=True, can_send_videos=True,
                can_send_video_notes=True, can_send_voice_notes=True,
                can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
    except Exception as e:
        logger.warning(f"Could not lift restriction for {target_user_id}: {e}")

    try:
        await query.edit_message_text(
            f"✅ Welcome @{username} — verified! 🚀\n\nCheck the pinned message for live stats and your invite link."
        )
    except Exception:
        pass
    await query.answer("Verified! Welcome 🚀")
    logger.info(f"Verified {target_user_id} via correct answer")


def main() -> None:
    """Start de Telegram bot."""
    logger.info("Bot wordt gestart...")

    # Maak de applicatie aan
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Registreer commando handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset_command))
    # Referral program - open to all group members
    app.add_handler(CommandHandler("toprefs", toprefs_command))
    app.add_handler(CommandHandler("myref", myref_command))
    app.add_handler(CommandHandler("leaderboard", toprefs_command))

    # Anti-bot verification for new members
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_verify_callback, pattern=r"^verify:"))

    # Registreer berichtenhandler (alleen tekstberichten)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Registreer poll answer handler
    app.add_handler(PollAnswerHandler(handle_poll_answer))

    # Scheduled jobs (tijden in UTC)
    job_queue = app.job_queue

    # Dagelijkse poll om 07:00 UTC (ma-vr)
    job_queue.run_daily(
        send_daily_poll,
        time=datetime.strptime("07:00", "%H:%M").time(),
        days=(0, 1, 2, 3, 4),  # ma-vr
        name="daily_poll",
    )

    # Zondagavond: markt gaat straks open (21:30 UTC = 22:30/23:30 NL)
    job_queue.run_daily(
        send_market_opening_soon,
        time=datetime.strptime("21:30", "%H:%M").time(),
        days=(6,),  # zondag
        name="market_opening_soon_sunday",
    )

    # Maandagochtend: markt is open
    job_queue.run_daily(
        send_market_open,
        time=datetime.strptime("00:05", "%H:%M").time(),
        days=(0,),  # maandag
        name="market_open_monday",
    )

    # Vrijdagavond: markt gaat dicht
    job_queue.run_daily(
        send_market_close,
        time=datetime.strptime("21:50", "%H:%M").time(),
        days=(4,),  # vrijdag
        name="market_close_friday",
    )

    # Poll resultaat om 21:00 UTC (ma-vr)
    job_queue.run_daily(
        send_poll_result,
        time=datetime.strptime("21:00", "%H:%M").time(),
        days=(0, 1, 2, 3, 4),  # ma-vr
        name="poll_result",
    )

    # Milestone check elke 30 minuten
    job_queue.run_repeating(
        check_milestones,
        interval=1800,
        first=10,
        name="milestone_check",
    )

    logger.info("Scheduled jobs geregistreerd: poll, markt meldingen, milestones")

    # Start de bot (polling)
    logger.info("Bot is actief! Druk op Ctrl+C om te stoppen.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
