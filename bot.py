"""
Telegram Bot die Claude gebruikt om berichten te beantwoorden.
Ondersteunt multi-turn gesprekken met geheugen per gebruiker.
"""

import os
import json
import logging
import httpx
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import anthropic
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatAction

# Laad omgevingsvariabelen
load_dotenv()

# Logging instellen
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Configuratie
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))
FLEXBOT_SERVER = os.getenv("FLEXBOT_SERVER", "https://flexbot-qpf2.onrender.com")
FLEXBOT_KEY = os.getenv("FLEXBOT_KEY", "Tanger2026@")
COMMUNITY_CHAT_ID = int(os.getenv("COMMUNITY_CHAT_ID", "-1003611276978"))

# Milestone tracking bestand
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

# Anthropic client aanmaken
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Gespreksgeheugen per gebruiker opslaan: {user_id: [messages]}
conversation_history: dict[int, list[dict]] = {}

SYSTEM_PROMPT = """Je bent Flexbot, de AI-assistent van de Flexbot trading community.

VEELGESTELDE VRAGEN — gebruik deze antwoorden als basis:

1. Wat is Flexbot?
→ Flexbot is een volledig automatische Expert Advisor (EA) voor MetaTrader 5, speciaal ontwikkeld voor het halen van FTMO challenges. De bot handelt XAUUSD (goud), analyseert live de charts en opent en sluit trades volledig zelfstandig.

2. Wat kost het?
→ Neem contact op met de admin voor de actuele prijzen en beschikbare pakketten. Betaling gaat via USDT op het ERC-20 netwerk. Na betaling ontvang je het EA-bestand en hulp bij de installatie.

3. Hoe installeer ik de EA?
→ 1) Download en installeer MetaTrader 5 bij je broker. 2) Kopieer het Flexbot EA-bestand naar de map: Bestand → Data Map Openen → MQL5 → Experts. 3) Herstart MT5. 4) Sleep de EA vanuit de Navigator op een XAUUSD chart. 5) Zorg dat AutoTrading aan staat (groene knop bovenin). De admin helpt je met de juiste instellingen als je er niet uitkomt.

4. Welke broker?
→ Flexbot werkt op elke broker die MetaTrader 5 aanbiedt. Voor FTMO challenges gebruik je uiteraard het FTMO platform. Om te testen raden we Vantage aan vanwege lage spreads op goud.

5. Welke pairs?
→ Flexbot is specifiek ontwikkeld en geoptimaliseerd voor XAUUSD (goud). Dit is de enige pair die ondersteund wordt.

6. Resultaten/winst?
→ De bot is geoptimaliseerd voor FTMO challenges met een slagingspercentage van 79% in backtests (1 jaar data, realistisch met spread en slippage). Resultaten variëren — trading brengt altijd risico met zich mee. Begin altijd eerst op een demo account.

7. Is het veilig voor FTMO?
→ Ja. De bot is speciaal gebouwd met alle FTMO regels in gedachten. Hij riskeert maximaal 0.5% per trade, vermijdt high-impact nieuws, doet geen one-side betting, en voorkomt andere patronen waar FTMO op let. De EA draait lokaal op jouw MT5, jij houdt 100% controle.

8. Werkt het op MT4?
→ Nee, Flexbot is exclusief gebouwd voor MetaTrader 5 (MT5).

9. Kan ik de instellingen aanpassen?
→ De standaard instellingen zijn zorgvuldig geoptimaliseerd voor FTMO challenges. We raden aan om deze niet te wijzigen. Neem contact op met de admin als je toch iets wilt aanpassen.

10. Hoe krijg ik support?
→ Stel je vraag hier in de groep of stuur een DM naar de admin.

11. Hoeveel startkapitaal heb ik nodig?
→ Voor FTMO kies je zelf je challenge grootte (bijv. $10K, $25K, $50K, $100K). De bot schaalt automatisch mee met de accountgrootte en riskeert altijd max 0.5% per trade.

12. Moet mijn PC altijd aan staan?
→ Ja, de EA moet actief draaien op MT5. We raden een VPS aan zodat de bot 24/5 draait zonder onderbrekingen. Vooral bij een FTMO challenge wil je geen gemiste trades. De admin kan je hierbij helpen.

13. Hoeveel trades plaatst de bot per dag?
→ Dat hangt af van de markt. De bot wacht geduldig op de juiste condities en forceert nooit een trade. Kwaliteit boven kwantiteit — belangrijk voor het halen van je challenge.

14. Hoe gaat de bot om met nieuws?
→ De bot vermijdt automatisch traden rond high-impact nieuwsmomenten. Dit beschermt je tegen onvoorspelbare marktbewegingen en is in lijn met wat FTMO verwacht van verantwoord risicobeheer.

15. Wat als mijn internet wegvalt?
→ Openstaande trades blijven staan bij je broker met hun SL en TP, dus je bent altijd beschermd. Bij herverbinding gaat de bot verder. Een VPS voorkomt dit probleem.

16. Heeft de bot een stop-loss?
→ Ja, elke trade heeft een stop-loss en take-profit. Maximaal 0.5% risico per trade. Zo blijf je ruim binnen de FTMO drawdown limieten.

17. Kan ik de bot op meerdere accounts draaien?
→ Dat hangt af van je licentie. Neem contact op met de admin voor de mogelijkheden.

18. Wordt de bot ge-update?
→ Ja, Flexbot wordt continu verbeterd en geoptimaliseerd. Updates worden via de community gedeeld.

19. Werkt de bot ook in het weekend?
→ Nee, de forexmarkt is gesloten in het weekend. De bot handelt alleen wanneer de markt open is.

20. Wat is one-side betting en doet de bot dat?
→ One-side betting is steeds dezelfde richting traden (alleen buy of alleen sell). FTMO let hierop. Flexbot handelt beide richtingen op basis van de marktanalyse, dus dit is geen probleem.

21. Hoe lang duurt het om een challenge te halen?
→ Dat verschilt per marktperiode. De bot haalt het target niet in één dag — hij handelt consistent en veilig. Gemiddeld is het realistisch binnen de challenge periode als de markt meewerkt.

22. Wat als de bot verlies maakt?
→ Verlies hoort bij trading. De bot houdt verliezen klein (max 0.5% per trade) zodat je ruim binnen de FTMO daily loss en max drawdown limieten blijft.

23. Hoe zet ik de bot uit?
→ Klik op "AutoTrading" in MT5 (wordt rood) of verwijder de EA van de chart. Openstaande trades blijven staan met hun SL/TP.

24. Kan ik de bot eerst testen?
→ Ja, open een gratis demo account en draai de bot daarop. Zo kun je zonder risico zien hoe hij werkt voordat je een FTMO challenge start.

25. Wat als ik de FTMO challenge niet haal?
→ Geen enkele bot kan 100% slagingsgarantie geven. De bot is geoptimaliseerd voor het beste slagingspercentage, maar marktomstandigheden spelen altijd een rol. Je kunt altijd een nieuwe challenge starten.

REGELS:
- Beantwoord ALLEEN vragen over: Flexbot EA, trading (forex/XAUUSD), FTMO, en support.
- Alles buiten scope: "Ik ben er alleen voor Flexbot, trading en support! 🤖📈"
- Antwoord EXTREEM KORT: max 1-2 zinnen. NOOIT meer dan 3 zinnen. Geen opsommingen, geen bullet points, geen uitleg tenzij gevraagd.
- Gebruik de taal van de gebruiker (NL of EN).
- Praat menselijk en chill, zoals een vriend in een groepschat. Korte zinnen. Geen stijve AI-taal. Af en toe een emoji maar niet overdrijven.
- VERBODEN: lange paragrafen, meerdere alinea's, disclaimers, "als je meer wilt weten". Gewoon kort antwoorden en klaar.
- Bij vragen over nieuws: leg uit wat het event betekent en hoe goud daar historisch op reageert. Zeg NOOIT dat je de markt kunt voorspellen — geef alleen context en historische tendensen.
- Als de bot niet handelt vanwege news blackout, leg uit welk event eraan komt.
- Als iemand scheldt, spam stuurt, of scam-achtige berichten plaatst (bijv. "stuur crypto naar...", "gegarandeerde winst", verdachte links), reageer streng maar kort: "⚠️ Dit soort berichten zijn hier niet welkom. Houd het respectvol en on-topic."
"""


async def fetch_live_data() -> str:
    """Haal live trade data op van de Flexbot server."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Haal recente trades, server state en nieuws op
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
                    parts.append("LAATSTE TRADES:")
                    for t in trades[:10]:
                        direction = t.get("direction", "?")
                        sl = t.get("sl", "?")
                        tp = t.get("tp", "?")
                        status = t.get("status", "?")
                        outcome = t.get("close_outcome", "?")
                        profit = t.get("close_result", "?")
                        created = t.get("created_at_ms", 0)
                        closed = t.get("closed_at_ms", 0)
                        # Converteer timestamps naar leesbare tijd
                        from datetime import datetime, timezone
                        open_str = datetime.fromtimestamp(created / 1000, tz=timezone.utc).strftime("%d-%m %H:%M") if created else "?"
                        close_str = datetime.fromtimestamp(closed / 1000, tz=timezone.utc).strftime("%d-%m %H:%M") if closed else "?"
                        parts.append(
                            f"- {direction} | SL:{sl} TP:{tp} | "
                            f"status:{status} | uitkomst:{outcome} | resultaat:{profit} | "
                            f"geopend:{open_str} gesloten:{close_str}"
                        )

            if state_resp.status_code == 200:
                state = state_resp.json()

                # EA posities (equity, balance, open trades)
                ea_pos = state.get("ea_positions", [])
                if ea_pos:
                    parts.append("\nACCOUNT STATUS:")
                    for p in ea_pos:
                        has_pos = "JA" if p.get("has_position") else "NEE"
                        parts.append(
                            f"- Account:{p.get('account_login')} | Equity:${p.get('equity')} | "
                            f"Balance:${p.get('balance')} | Open positie:{has_pos}"
                        )

                # Markt status
                market = state.get("market", {})
                blocked = market.get("blocked", False)
                parts.append(f"\nMARKT: {'GEBLOKKEERD - ' + str(market.get('reason', '')) if blocked else 'OPEN'}")

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
                        f"Consecutive losses:{losses} | News blackout:{'NEE' if news_pass else 'JA'}"
                    )

                # Signal prep
                prep = state.get("signal_prep", {})
                if prep:
                    price = prep.get("price", "?")
                    trend = prep.get("trend", "?")
                    parts.append(f"HUIDIGE PRIJS: {price} | TREND: {trend}")

            if news_resp.status_code == 200:
                news_data = news_resp.json()
                events = news_data.get("events", [])
                if events:
                    # Filter alleen USD events (relevant voor goud)
                    usd_events = [e for e in events if e.get("currency") == "USD"]
                    if usd_events:
                        parts.append("\nUSD NIEUWS EVENTS (high impact):")
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

            return "\n".join(parts) if parts else "Geen live data beschikbaar."

    except Exception as e:
        logger.warning(f"Kon live data niet ophalen: {e}")
        return "Live data tijdelijk niet beschikbaar."


def get_history(user_id: int) -> list[dict]:
    """Geef de gespreksgeschiedenis van een gebruiker terug."""
    return conversation_history.get(user_id, [])


def add_to_history(user_id: int, role: str, content: str) -> None:
    """Voeg een bericht toe aan de gespreksgeschiedenis."""
    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({"role": role, "content": content})

    # Begrens de geschiedenis tot MAX_HISTORY berichten (altijd in paren)
    history = conversation_history[user_id]
    if len(history) > MAX_HISTORY:
        # Verwijder de oudste twee berichten (user + assistant paar)
        conversation_history[user_id] = history[-MAX_HISTORY:]


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start commando handler."""
    user = update.effective_user
    await update.message.reply_text(
        f"Hallo {user.first_name}! 👋\n\n"
        "Ik ben Flexbot, de AI-assistent van deze community. "
        "Stel me gerust een vraag!\n\n"
        "Gebruik /help voor meer informatie of /reset om het gesprek opnieuw te beginnen."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help commando handler."""
    await update.message.reply_text(
        "🤖 *Flexbot*\n\n"
        "*Commando's:*\n"
        "/start - Start de bot\n"
        "/help - Toon dit helpbericht\n"
        "/reset - Wis het gespreksgeheugen\n\n"
        "*Gebruik:*\n"
        "Stuur gewoon een bericht en ik antwoord met behulp van Claude AI. "
        "Ik onthoud de context van ons gesprek!\n\n"
        f"_Maximaal {MAX_HISTORY} berichten worden onthouden._",
        parse_mode="Markdown",
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reset commando handler – wist de gespreksgeschiedenis."""
    user_id = update.effective_user.id
    conversation_history.pop(user_id, None)
    await update.message.reply_text(
        "🔄 Gespreksgeheugen gewist! We beginnen opnieuw."
    )


import re

# Scheldwoorden en scam patronen
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
    r"t\.me/[a-zA-Z0-9_]+",  # Telegram links (mogelijke spam groepen)
    r"bit\.ly/",  # Verkorte links
    r"wa\.me/",  # WhatsApp links
]


async def moderate_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check bericht op scheldwoorden en scam. Returns True als bericht geblokkeerd is."""
    if not update.message or not update.message.text:
        return False

    text = update.message.text.lower()
    chat_id = update.effective_chat.id
    user = update.effective_user

    # Check scheldwoorden
    for word in BANNED_WORDS:
        if word in text:
            try:
                await update.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ @{user.username or user.first_name}, scheldwoorden zijn hier niet welkom. Houd het respectvol!",
            )
            logger.info(f"Moderatie: scheldwoord van {user.id} verwijderd")
            return True

    # Check scam patronen
    for pattern in SCAM_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            try:
                await update.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🚫 Spam/scam gedetecteerd en verwijderd. Dit soort berichten zijn hier niet toegestaan.",
            )
            logger.info(f"Moderatie: scam/spam van {user.id} verwijderd")
            return True

    return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verwerk inkomende berichten en beantwoord via Claude."""
    user_id = update.effective_user.id
    user_text = update.message.text

    logger.info(f"Bericht van gebruiker {user_id}: {user_text[:50]}...")

    # Moderatie: check op scheldwoorden, scam, spam
    if await moderate_message(update, context):
        return  # Bericht is al afgehandeld door moderatie

    # Toon 'typing...' indicator
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )

    # Haal live trade data op van de server
    live_data = await fetch_live_data()

    # Voeg het gebruikersbericht toe aan de geschiedenis
    add_to_history(user_id, "user", user_text)

    try:
        # Bouw system prompt met live data
        system_with_data = (
            SYSTEM_PROMPT
            + "\n\nLIVE DATA VAN DE SERVER (REAL-TIME, ALTIJD ACTUEEL):\n"
            + live_data
            + "\n\nBELANGRIJK: Deze live data is ALTIJD de meest recente stand. "
            "Negeer trade info uit eerdere berichten in het gesprek — die is verouderd. "
            "Als iemand vraagt over 'de laatste trade', gebruik ALLEEN de bovenstaande live data."
        )

        # Roep Claude aan met streaming voor responsiviteit
        with claude.messages.stream(
            model="claude-haiku-4-5",
            max_tokens=150,
            system=system_with_data,
            messages=get_history(user_id),
        ) as stream:
            response = stream.get_final_message()

        # Haal de tekst op uit het antwoord
        assistant_text = next(
            block.text for block in response.content if block.type == "text"
        )

        # Sla het antwoord op in de geschiedenis
        add_to_history(user_id, "assistant", assistant_text)

        # Stuur het antwoord naar de gebruiker
        await update.message.reply_text(assistant_text)

        logger.info(f"Antwoord verstuurd aan gebruiker {user_id}")

    except anthropic.AuthenticationError:
        logger.error("Ongeldige Anthropic API sleutel")
        await update.message.reply_text(
            "❌ Configuratiefout: ongeldige API sleutel. Neem contact op met de beheerder."
        )
    except anthropic.RateLimitError:
        logger.warning("API-limiet bereikt")
        await update.message.reply_text(
            "⏳ Te veel verzoeken. Probeer het over een moment opnieuw."
        )
    except anthropic.APIConnectionError:
        logger.error("Verbindingsfout met Anthropic API")
        await update.message.reply_text(
            "🌐 Verbindingsfout. Controleer de internetverbinding en probeer opnieuw."
        )
    except Exception as e:
        logger.error(f"Onverwachte fout: {e}")
        await update.message.reply_text(
            "😕 Er is iets misgegaan. Probeer het opnieuw of gebruik /reset."
        )


async def send_daily_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stuur dagelijkse poll: goud omhoog of omlaag? Sla openingsprijs op."""
    now = datetime.now(timezone.utc)
    # Alleen doordeweeks (ma=0 t/m vr=4)
    if now.weekday() >= 5:
        logger.info("Weekend — poll overgeslagen")
        return
    day_str = now.strftime("%A %d %B")

    # Haal huidige goudprijs op als openingsprijs
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
        logger.warning(f"Kon openingsprijs niet ophalen: {e}")

    msg = await context.bot.send_poll(
        chat_id=COMMUNITY_CHAT_ID,
        question=f"📊 {day_str} — Wat denk je, goud omhoog of omlaag vandaag?",
        options=["🟢 Omhoog (bullish)", "🔴 Omlaag (bearish)", "⚪ Zijwaarts"],
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
    user_name = answer.user.first_name or "Onbekend"

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
        actual_text = "OMHOOG"
    elif diff < -5:
        actual = "bearish"
        actual_emoji = "🔴"
        actual_text = "OMLAAG"
    else:
        actual = "sideways"
        actual_emoji = "⚪"
        actual_text = "ZIJWAARTS"

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
        f"📊 **POLL RESULTAAT VAN VANDAAG**\n",
        f"Goud open: ${open_price:.2f} → close: ${close_price:.2f}",
        f"Beweging: {actual_emoji} {actual_text} ({pct:+.2f}%)\n",
        f"Stemmen: 🟢 {bull_count} | 🔴 {bear_count} | ⚪ {side_count}",
    ]

    if winner_names:
        lines.append(f"\n🎯 Goed voorspeld: {', '.join(winner_names)}")
    elif total_votes > 0:
        lines.append(f"\n😅 Niemand had het goed vandaag!")

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
        text="☀️ Goedemorgen! De markt is open, Flexbot is actief. Let's get it! 💰",
    )
    logger.info("Markt open melding verstuurd")


async def send_market_opening_soon(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stuur zondagavond melding dat de markt straks opengaat."""
    if datetime.now(timezone.utc).weekday() != 6:  # alleen zondag
        return
    await context.bot.send_message(
        chat_id=COMMUNITY_CHAT_ID,
        text="🔔 De markt gaat zo open! Maak je klaar voor een nieuwe handelsweek. 💪",
    )
    logger.info("Markt gaat straks open melding verstuurd (zondag)")


async def send_market_close(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stuur markt dicht melding (vrijdag)."""
    if datetime.now(timezone.utc).weekday() != 4:  # alleen vrijdag
        return
    await context.bot.send_message(
        chat_id=COMMUNITY_CHAT_ID,
        text="🌙 Markt gaat zo dicht, Flexbot stopt voor vandaag. Rust lekker uit, tot morgen! 👋",
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
            250: "💰 $250 winst vandaag! Lekker bezig Flexbot! 🔥",
            500: "🔥 $500 winst vandaag! Halve K op één dag!",
            1000: "🚀 $1,000 winst vandaag! EEN K OP ÉÉN DAG! 💎",
            2000: "🏆 $2,000 winst vandaag! Flexbot is niet te stoppen!",
            5000: "👑 $5,000 winst vandaag! INSANE dag! 🚀🚀🚀",
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
                text=f"🔥 {day_wins} wins op rij vandaag! Flexbot is on fire!",
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


def main() -> None:
    """Start de Telegram bot."""
    logger.info("Bot wordt gestart...")

    # Maak de applicatie aan
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Registreer commando handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset_command))

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
