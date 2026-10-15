from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import sqlite3
import os
import hashlib
import hmac
import secrets
from http.cookies import SimpleCookie
import urllib.parse
import urllib.request
from signalrcore.hub_connection_builder import HubConnectionBuilder
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ===== GEMiN USER SYSTEM =====

def register_user(username, password):
    users = load_users()

    for u in users:
        if u.get("username") == username:
            return {"ok": False, "message": "Username tayari ipo"}

    user = {
        "username": username,
        "password": hash_password(password),
        "status": "PENDING",
        "plan": "GEMiN Premium 14 Days",
        "price": 20000,
        "expiry": None
    }

    users.append(user)
    save_users(users)

    return {"ok": True, "message": "Account imeundwa, subiri approval"}

def activate_user(username):
    users = load_users()

    for u in users:
        if u.get("username") == username:
            u["status"] = "ACTIVE"
            u["expiry"] = (
                datetime.now(timezone.utc) + timedelta(days=14)
            ).isoformat()

            save_users(users)
            return True

    return False


def login_user(username, password):
    if not isinstance(username, str) or not isinstance(password, str):
        return None

    users = load_users()

    for u in users:
        if u.get("username") != username:
            continue

        stored = str(u.get("password") or "")

        if stored.startswith("pbkdf2_sha256$"):
            try:
                scheme, rounds, salt, expected = stored.split("$", 3)
                if scheme != "pbkdf2_sha256":
                    return None
                actual = hashlib.pbkdf2_hmac(
                    "sha256",
                    password.encode("utf-8"),
                    bytes.fromhex(salt),
                    int(rounds)
                ).hex()
                if hmac.compare_digest(actual, expected):
                    return u
                return None
            except (ValueError, TypeError):
                return None

        # Migrate a legacy plaintext password after a successful login.
        if hmac.compare_digest(stored, password):
            u["password"] = hash_password(password)
            save_users(users)
            return u

    return None



DB_FILE = "gemin.db"

def hash_password(password):
    salt = secrets.token_hex(16)
    rounds = 310000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        rounds
    ).hex()
    return f"pbkdf2_sha256${rounds}${salt}${digest}"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT,
        status TEXT,
        plan TEXT,
        price INTEGER,
        expiry TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()

def migrate_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    try:
        cur.execute("ALTER TABLE users ADD COLUMN transaction_id TEXT")
    except:
        pass

    try:
        cur.execute("ALTER TABLE users ADD COLUMN payer_name TEXT")
    except:
        pass

    conn.commit()
    conn.close()

migrate_db()

# Server-side sessions: only a hash of each random token is stored.
SESSION_HOURS = 12
ADMIN_USERNAME = os.environ.get("GEMIN_ADMIN_USERNAME", "").strip()

def init_sessions_db():
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()

def create_session(username):
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + (timedelta(days=365) if username == ADMIN_USERNAME else timedelta(hours=SESSION_HOURS))
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute(
            "INSERT INTO sessions (token_hash, username, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token_hash, username, expires.isoformat(), now.isoformat())
        )
        conn.commit()
    finally:
        conn.close()

    return token, expires

def get_session_user(token):
    if not token or not isinstance(token, str):
        return None

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT username, expires_at FROM sessions WHERE token_hash = ?",
            (token_hash,)
        ).fetchone()

        if not row:
            return None

        username, expires_at = row
        if expires_at <= now:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            conn.commit()
            return None
    finally:
        conn.close()

    return next(
        (u for u in load_users() if u.get("username") == username),
        None
    )

def delete_session(token):
    if not token or not isinstance(token, str):
        return

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        conn.commit()
    finally:
        conn.close()

init_sessions_db()

USERS_FILE = Path("users.json")

def load_users():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("SELECT username,password,status,plan,price,expiry,transaction_id,payer_name FROM users")
    rows = cur.fetchall()

    conn.close()

    return [
        {
            "username": r[0],
            "password": r[1],
            "status": r[2],
            "plan": r[3],
            "price": r[4],
            "expiry": r[5],
            "transaction_id": r[6],
            "payer_name": r[7]
        }
        for r in rows
    ]


def save_users(users):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    for u in users:
        cur.execute("""
        INSERT OR REPLACE INTO users
        (username,password,status,plan,price,expiry,transaction_id,payer_name)
        VALUES (?,?,?,?,?,?,?,?)
        """, (
            u.get("username"),
            u.get("password"),
            u.get("status"),
            u.get("plan"),
            u.get("price"),
            u.get("expiry"),
            u.get("transaction_id"),
            u.get("payer_name")
        ))

    conn.commit()
    conn.close()


def bootstrap_admin():
    username = ADMIN_USERNAME
    password = os.environ.get("GEMIN_ADMIN_PASSWORD", "")

    if not username:
        print("GEMiN ADMIN: GEMIN_ADMIN_USERNAME haijawekwa.")
        return

    if len(password) < 12 or len(password) > 256:
        print("GEMiN ADMIN: Weka GEMIN_ADMIN_PASSWORD yenye herufi/namba 12 au zaidi.")
        return

    conn = sqlite3.connect(DB_FILE)
    try:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()

        if exists:
            conn.execute(
                "UPDATE users SET password = ? WHERE username = ?",
                (hash_password(password), username),
            )
            conn.commit()
            print("GEMiN ADMIN: Password ya admin imesasishwa.")
            return

        conn.execute(
            """INSERT INTO users
               (username, password, status, plan, price, expiry)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                username,
                hash_password(password),
                "ACTIVE",
                "GEMiN Admin",
                0,
                None,
            ),
        )
        conn.commit()
        print("GEMiN ADMIN: Akaunti ya admin imeundwa.")
    finally:
        conn.close()


bootstrap_admin()

def check_premium(user):
    if user.get("status") != "ACTIVE":
        return False

    expiry = user.get("expiry")
    if not expiry:
        return False

    try:
        expiry_date = datetime.fromisoformat(expiry)
        if expiry_date.tzinfo is None:
            expiry_date = expiry_date.replace(tzinfo=timezone.utc)

        if datetime.now(timezone.utc) < expiry_date:
            return True

        conn = sqlite3.connect(DB_FILE)
        try:
            conn.execute(
                "UPDATE users SET status = ? WHERE username = ? AND status = ?",
                ("EXPIRED", user.get("username"), "ACTIVE")
            )
            conn.commit()
        finally:
            conn.close()

        user["status"] = "EXPIRED"
        return False
    except (ValueError, TypeError):
        return False


def update_pending_results(candles):
    import json
    from pathlib import Path

    path = Path("history.json")

    try:
        history = json.loads(path.read_text())
    except Exception:
        return

    if not history or not candles:
        return

    candle_map = {
        int(c["time"]): float(c["close"])
        for c in candles
        if "time" in c and "close" in c
    }

    changed = False

    for record in history:
        if record.get("result") != "PENDING":
            continue

        signal = record.get("signal")
        entry_price = record.get("entry_price")
        entry_time = record.get("entry_time")

        if signal not in ("CALL", "PUT"):
            continue

        if entry_price is None or not entry_time:
            continue

        try:
            entry_timestamp = datetime.fromisoformat(
                entry_time.replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            continue

        # M1 result = candle inayofuata kabisa baada ya entry candle.
        next_timestamp = int(entry_timestamp) + 60

        if next_timestamp not in candle_map:
            continue

        next_close = candle_map[next_timestamp]

        if signal == "CALL":
            if next_close > entry_price:
                record["result"] = "WIN"
            elif next_close < entry_price:
                record["result"] = "LOSS"
            else:
                record["result"] = "DRAW"

        elif signal == "PUT":
            if next_close < entry_price:
                record["result"] = "WIN"
            elif next_close > entry_price:
                record["result"] = "LOSS"
            else:
                record["result"] = "DRAW"

        record["result_time"] = datetime.fromtimestamp(
            next_timestamp, timezone.utc
        ).isoformat()
        record["result_price"] = next_close
        changed = True

    if changed:
        path.write_text(json.dumps(history, indent=2))


def calculate_ema(prices, period):
    if len(prices) < period:
        return None

    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period

    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema

    return round(ema, 5)


def calculate_rsi(prices, period=14):
    if len(prices) <= period:
        return None

    gains = []
    losses = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def get_ohlc(symbol="EURUSD", limit=100):
    import urllib.request

    url = f"https://biquote.io/api/{symbol}/ohlc?interval=1m&limit={limit}"

    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            data = json.loads(response.read().decode())
    except Exception as e:
        raise Exception("Biquote OHLC imeshindwa: " + str(e))

    bars = data.get("bars", [])
    if not bars:
        raise Exception(f"Biquote haijarudisha candles za {symbol}")

    candles = []
    for b in reversed(bars):
        candles.append({
            "time": int(datetime.fromisoformat(
                b["openTime"].replace("Z", "+00:00")
            ).timestamp()),
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
            "volume": float(b.get("tickVolume", 0)),
            "isOpen": bool(b.get("isOpen", False))
        })

    prices = [c["close"] for c in candles]

    last_time = datetime.fromtimestamp(
        candles[-1]["time"], timezone.utc
    ).isoformat()

    return prices, candles, last_time

def candle_confirmation(candles):
    if len(candles) < 4:
        return "NEUTRAL"

    cs = candles[-4:-1]
    bodies = [float(c["close"]) - float(c["open"]) for c in cs]
    total = sum(abs(x) for x in bodies)

    if total == 0:
        return "NEUTRAL"

    bullish_count = sum(1 for x in bodies if x > 0)
    bearish_count = sum(1 for x in bodies if x < 0)
    bullish_body = sum(max(x, 0) for x in bodies)
    bearish_body = sum(abs(min(x, 0)) for x in bodies)

    last_bullish = bodies[-1] > 0
    last_bearish = bodies[-1] < 0

    if bullish_count >= 2 and last_bullish and bullish_body / total >= 0.55:
        return "BULLISH"

    if bearish_count >= 2 and last_bearish and bearish_body / total >= 0.55:
        return "BEARISH"

    return "NEUTRAL"

def save_history(data):
    import json
    from pathlib import Path
    from datetime import datetime, timezone

    path = Path("history.json")

    try:
        history = json.loads(path.read_text())
    except Exception:
        history = []

    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "symbol": data.get("symbol"),
        "signal": data.get("signal"),
        "confidence": data.get("confidence"),
        "rsi": data.get("rsi"),
        "ema9": data.get("ema9"),
        "ema21": data.get("ema21"),
        "trend_strength": data.get("trend_strength"),
        "score": data.get("score"),
        "adx": data.get("adx"),
        "atr": data.get("atr"),
        "candle_confirmation": data.get("candle_confirmation"),

        # Professional candle behavior — informational layer.
        "candle_behavior": data.get("candle_behavior", {}),
        "candle_pattern": data.get("candle_behavior", {}).get("pattern"),
        "candle_direction": data.get("candle_behavior", {}).get("direction"),
        "candle_body_pct": data.get("candle_behavior", {}).get("body_pct"),
        "candle_upper_wick_pct": data.get("candle_behavior", {}).get("upper_wick_pct"),
        "candle_lower_wick_pct": data.get("candle_behavior", {}).get("lower_wick_pct"),
        "candle_strength": data.get("candle_behavior", {}).get("strength"),
        "candle_rejection": data.get("candle_behavior", {}).get("rejection"),
        "candle_momentum": data.get("candle_behavior", {}).get("momentum"),
        "candle_consecutive": data.get("candle_behavior", {}).get("consecutive"),

        "data_status": data.get("data_status"),
        "entry_price": data.get("close"),
        "entry_time": data.get("last_candle"),
        "result": (
            "PENDING"
            if data.get("signal") in ("CALL", "PUT")
            else "NO TRADE"
        )
    }

    # Zuia duplicate ya analysis ileile kwenye candle ileile.
    duplicate = any(
        x.get("symbol") == record.get("symbol")
        and x.get("entry_time") == record.get("entry_time")
        and x.get("signal") == record.get("signal")
        for x in history
    )

    if not duplicate:
        history.append(record)
        path.write_text(json.dumps(history, indent=2))


def calculate_atr(candles, period=14):
    if len(candles) <= period:
        return None

    true_ranges = []

    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        prev_close = float(candles[i - 1]["close"])

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    atr = sum(true_ranges[:period]) / period

    for tr in true_ranges[period:]:
        atr = ((atr * (period - 1)) + tr) / period

    return atr


def calculate_adx(candles, period=14):
    if len(candles) <= period * 2:
        return None

    trs = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        prev_high = float(candles[i - 1]["high"])
        prev_low = float(candles[i - 1]["low"])
        prev_close = float(candles[i - 1]["close"])

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        up_move = high - prev_high
        down_move = prev_low - low

        plus = up_move if up_move > down_move and up_move > 0 else 0
        minus = down_move if down_move > up_move and down_move > 0 else 0

        trs.append(tr)
        plus_dm.append(plus)
        minus_dm.append(minus)

    if len(trs) < period * 2:
        return None

    atr = sum(trs[:period]) / period
    plus = sum(plus_dm[:period]) / period
    minus = sum(minus_dm[:period]) / period

    dx_values = []

    for i in range(period, len(trs)):
        atr = ((atr * (period - 1)) + trs[i]) / period
        plus = ((plus * (period - 1)) + plus_dm[i]) / period
        minus = ((minus * (period - 1)) + minus_dm[i]) / period

        if atr == 0:
            continue

        plus_di = (plus / atr) * 100
        minus_di = (minus / atr) * 100
        di_sum = plus_di + minus_di

        if di_sum == 0:
            continue

        dx = abs(plus_di - minus_di) / di_sum * 100
        dx_values.append(dx)

    if len(dx_values) < period:
        return None

    adx = sum(dx_values[:period]) / period

    for dx in dx_values[period:]:
        adx = ((adx * (period - 1)) + dx) / period

    return round(adx, 2)



def analyze_candle_behavior(candles):
    """
    Professional candle behavior analysis.
    Hii ni informational layer tu — haibadilishi signal logic.
    """
    if not candles:
        return {
            "direction": "NEUTRAL",
            "pattern": "NO_DATA",
            "body_pct": 0,
            "upper_wick_pct": 0,
            "lower_wick_pct": 0,
            "strength": "NONE",
            "rejection": "NONE",
            "momentum": "NONE",
            "consecutive": 0
        }

    c = candles[-1]

    o = float(c["open"])
    h = float(c["high"])
    l = float(c["low"])
    close = float(c["close"])

    rng = h - l

    if rng <= 0:
        return {
            "direction": "DOJI",
            "pattern": "DOJI",
            "body_pct": 0,
            "upper_wick_pct": 0,
            "lower_wick_pct": 0,
            "strength": "NONE",
            "rejection": "NONE",
            "momentum": "NONE",
            "consecutive": 0
        }

    body = abs(close - o)
    upper = h - max(o, close)
    lower = min(o, close) - l

    body_pct = body / rng * 100
    upper_pct = upper / rng * 100
    lower_pct = lower / rng * 100

    direction = (
        "BULLISH" if close > o
        else "BEARISH" if close < o
        else "DOJI"
    )

    # Candle strength
    if body_pct >= 70:
        strength = "VERY_STRONG"
    elif body_pct >= 50:
        strength = "STRONG"
    elif body_pct >= 30:
        strength = "MODERATE"
    else:
        strength = "WEAK"

    # Rejection
    if lower_pct >= 50 and lower > body * 1.5:
        rejection = "BULLISH_REJECTION"
    elif upper_pct >= 50 and upper > body * 1.5:
        rejection = "BEARISH_REJECTION"
    else:
        rejection = "NONE"

    # Momentum
    if body_pct >= 70:
        momentum = "HIGH"
    elif body_pct >= 45:
        momentum = "MEDIUM"
    else:
        momentum = "LOW"

    # Basic candle patterns
    pattern = "NORMAL"

    if body_pct <= 10:
        pattern = "DOJI"
    elif lower > body * 2 and upper < body:
        pattern = "HAMMER" if direction == "BULLISH" else "HAMMER_LIKE"
    elif upper > body * 2 and lower < body:
        pattern = "SHOOTING_STAR" if direction == "BEARISH" else "SHOOTING_STAR_LIKE"
    elif lower > body * 2:
        pattern = "LONG_LOWER_WICK"
    elif upper > body * 2:
        pattern = "LONG_UPPER_WICK"

    # Consecutive candles
    consecutive = 1

    for i in range(len(candles) - 2, -1, -1):
        x = candles[i]
        xo = float(x["open"])
        xc = float(x["close"])

        if direction == "BULLISH" and xc > xo:
            consecutive += 1
        elif direction == "BEARISH" and xc < xo:
            consecutive += 1
        else:
            break

    # Engulfing
    if len(candles) >= 2:
        prev = candles[-2]

        po = float(prev["open"])
        pc = float(prev["close"])

        if pc < po and close > o and o <= pc and close >= po:
            pattern = "BULLISH_ENGULFING"

        elif pc > po and close < o and o >= pc and close <= po:
            pattern = "BEARISH_ENGULFING"

    return {
        "direction": direction,
        "pattern": pattern,
        "body_pct": round(body_pct, 2),
        "upper_wick_pct": round(upper_pct, 2),
        "lower_wick_pct": round(lower_pct, 2),
        "strength": strength,
        "rejection": rejection,
        "momentum": momentum,
        "consecutive": consecutive
    }


def analyze_prices(prices, candles=None):
    """GEMiN M1: EMA 9/21 na RSI 7 pekee ndizo huamua signal."""
    closed_candles = [
        c for c in (candles or [])
        if not c.get("isOpen", False)
        and all(c.get(k) is not None for k in ("open", "high", "low", "close"))
    ]

    closes = [float(c["close"]) for c in closed_candles]
    if not closes:
        closes = [float(x) for x in (prices or []) if x is not None]

    rsi = calculate_rsi(closes, 7) if len(closes) >= 8 else None
    ema9 = calculate_ema(closes, 9) if len(closes) >= 9 else None
    ema21 = calculate_ema(closes, 21) if len(closes) >= 21 else None

    reasons = []
    votes = []

    if ema9 is not None and ema21 is not None:
        if ema9 > ema21:
            votes.append(("EMA 9/21", "CALL"))
        elif ema9 < ema21:
            votes.append(("EMA 9/21", "PUT"))

    if rsi is not None:
        if rsi > 50:
            votes.append(("RSI 7", "CALL"))
        elif rsi < 50:
            votes.append(("RSI 7", "PUT"))

    call_votes = sum(side == "CALL" for _, side in votes)
    put_votes = sum(side == "PUT" for _, side in votes)

    signal = "WAIT"
    confidence = 0

    if len(closes) < 21 or ema9 is None or ema21 is None or rsi is None:
        reasons.append("WAIT: data haitoshi kwa EMA 9/21 na RSI 7.")
    elif call_votes == 2:
        signal = "CALL"
        confidence = 60
        reasons.append("CALL: EMA 9/21 na RSI 7 zimekubaliana.")
    elif put_votes == 2:
        signal = "PUT"
        confidence = 60
        reasons.append("PUT: EMA 9/21 na RSI 7 zimekubaliana.")
    else:
        reasons.append("WAIT: EMA 9/21 na RSI 7 hazijakubaliana au hazina mwelekeo wazi.")

    reasons.extend(f"{name}: {side}" for name, side in votes)

    trend_strength = (
        abs(ema9 - ema21) / abs(ema21) * 100
        if ema9 is not None and ema21 not in (None, 0) else 0.0
    )

    score = call_votes - put_votes
    candle_behavior = {
        "direction": "NEUTRAL", "pattern": "NOT_USED",
        "body_pct": 0, "upper_wick_pct": 0, "lower_wick_pct": 0,
        "strength": "NOT_USED", "rejection": "NOT_USED",
        "momentum": "NOT_USED", "consecutive": 0
    }

    return {
        "candle_confirmation": "NOT_USED",
        "candle_behavior": candle_behavior,
        "signal": signal,
        "confidence": confidence,
        "rsi": round(rsi, 2) if rsi is not None else None,
        "ema9": ema9,
        "ema21": ema21,
        "atr": None,
        "adx": None,
        "trend_strength": round(trend_strength, 5),
        "score": score,
        "call_votes": call_votes,
        "put_votes": put_votes,
        "call_score": float(call_votes),
        "put_score": float(put_votes),
        "adaptive_score": float(score),
        "candidate_rule": signal in ("CALL", "PUT"),
        "bollinger_upper": None,
        "bollinger_middle": None,
        "bollinger_lower": None,
        "stochastic_k": None,
        "stochastic_d": None,
        "reasons": reasons
    }


live_tick = None
live_prices = []
hub = HubConnectionBuilder().with_url("https://biquote.io/hubs/tick").build()

def on_live_tick(data):
    global live_tick, live_prices
    tick = data[0] if isinstance(data, list) else data
    live_tick = tick
    if tick.get("mid") is not None:
        live_prices.append(float(tick["mid"]))
        if len(live_prices) > 100:
            live_prices.pop(0)
hub.on("ReceiveTick", on_live_tick)
class Handler(SimpleHTTPRequestHandler):

    def session_token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            morsel = cookie.get("gemin_session")
            return morsel.value if morsel else None
        except Exception:
            return None

    def current_user(self):
        return get_session_user(self.session_token())

    def is_admin(self, user):
        return bool(
            user
            and user.get("status") == "ACTIVE"
            and user.get("plan") == "GEMiN Admin"
            and user.get("username") in {
                ADMIN_USERNAME,
                "admin2",
            }
        )

    def send_json(self, data, status=200, set_cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def session_cookie(self, token, expires):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
        return (
            f"gemin_session={token}; Path=/; HttpOnly; SameSite=Lax"
            f"; Expires={expires.strftime('%a, %d %b %Y %H:%M:%S GMT')}{secure}"
        )

    def clear_session_cookie(self):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
        return f"gemin_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{secure}"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path

        # Old admin HTML and GET activation are disabled.
        if route in ("/admin", "/admin/activate"):
            self.send_json(
                {"ok": False, "message": "Tumia admin session"},
                status=403
            )
            return

        # State changes must never happen through GET.
        if route == "/api/robot-toggle":
            self.send_json(
                {"ok": False, "message": "Tumia POST"},
                status=405
            )
            return

        if parsed.path == "/api/test":
            data = {
                "status": "ok",
                "message": "OTC Signal Dashboard backend iko tayari"
            }

        elif parsed.path == "/api/live-tick":
            self.send_json(
                {"ok": False, "message": "Endpoint haipatikani"},
                status=404
            )
            return

        elif parsed.path == "/api/live-analyze":
            query = urllib.parse.parse_qs(parsed.query)
            symbol = query.get("symbol", ["EURUSD"])[0]
            try:
                prices, candles, last_open_time = get_ohlc(symbol, 100)
                data = analyze_prices(prices, candles)
                data["symbol"] = symbol
                data["candles"] = len(prices)
                data["source"] = "Biquote Forex"

                # Entry lazima itumie candle ya mwisho iliyofungwa.
                closed_candles = [
                    c for c in candles
                    if not c.get("isOpen", False)
                ]

                if closed_candles:
                    entry_candle = closed_candles[-1]
                    entry_time = datetime.fromtimestamp(
                        entry_candle["time"], timezone.utc
                    ).isoformat()

                    data["last_candle"] = entry_time
                    data["close"] = float(entry_candle["close"])
                else:
                    data["last_candle"] = None
                    data["close"] = None

                update_pending_results(candles)

                if data["last_candle"]:
                    candle_time = datetime.fromisoformat(
                        data["last_candle"].replace("Z", "+00:00")
                    )
                    if candle_time.tzinfo is None:
                        candle_time = candle_time.replace(tzinfo=timezone.utc)
                    age_seconds = (datetime.now(timezone.utc) - candle_time).total_seconds()
                    data["age_seconds"] = max(0, round(age_seconds))
                    if age_seconds > 300:
                        data["signal"] = "WAIT"
                        data["confidence"] = 0
                        data["data_status"] = "STALE"
                        data["reasons"] = ["Data ya soko ni ya zamani.", f"Candle ya mwisho: {last_open_time}"]
                    else:
                        data["data_status"] = "FRESH"

                save_history(data)

            except Exception as e:
                data = {"error": str(e), "symbol": symbol}

        elif parsed.path == "/api/robot-status":
            try:
                state = json.loads(Path("robot_state.json").read_text())
            except Exception:
                state = {"enabled": False}

            data = {
                "enabled": bool(state.get("enabled", False))
            }

        elif parsed.path == "/api/pending-users":
            users = load_users()
            data = [
                {k: v for k, v in u.items() if k != "password"}
                for u in users
                if u.get("status") == "PAYMENT_SENT"
            ]

        elif parsed.path == "/api/users":
            users = load_users()
            data = [
                {k: v for k, v in u.items() if k != "password"}
                for u in users
            ]

        elif parsed.path == "/api/history":
            try:
                path = Path("history.json")

                try:
                    history = json.loads(path.read_text())
                except Exception:
                    history = []

                wins = sum(1 for x in history if x.get("result") == "WIN")
                losses = sum(1 for x in history if x.get("result") == "LOSS")
                pending = sum(1 for x in history if x.get("result") == "PENDING")
                draws = sum(1 for x in history if x.get("result") == "DRAW")

                # Only real trades count in Trading Stats.
                # NO TRADE and empty results are excluded.
                total_trades = wins + losses + draws + pending
                completed = wins + losses + draws
                win_rate = round((wins / completed) * 100, 2) if completed else 0

                data = {
                    "total": total_trades,
                    "wins": wins,
                    "losses": losses,
                    "pending": pending,
                    "draws": draws,
                    "completed": completed,
                    "win_rate": win_rate,
                    "history": history[-50:]
                }

            except Exception as e:
                data = {"error": str(e)}

        elif parsed.path == "/api/analyze":
            query = urllib.parse.parse_qs(parsed.query)
            raw_prices = query.get("prices", [""])[0]

            try:
                prices = [float(x) for x in raw_prices.split(",") if x]
                data = analyze_prices(prices)
            except ValueError:
                data = {
                    "error": "Prices si sahihi."
                }

        else:
            super().do_GET()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())



    def do_POST(self):
        status_code = 200
        set_cookie = None

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 100000:
                self.send_json(
                    {"ok": False, "message": "Ombi si sahihi"},
                    status=400
                )
                return

            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body) if body else {}
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(
                {"ok": False, "message": "Taarifa hazisomeki"},
                status=400
            )
            return

        path = urllib.parse.urlsplit(self.path).path

        if path == "/api/register":
            username = payload.get("username")
            password = payload.get("password")

            if not isinstance(username, str) or not isinstance(password, str):
                data = {"ok": False, "message": "Jaza taarifa zote"}
                status_code = 400
            elif not username.strip() or not password:
                data = {"ok": False, "message": "Jaza taarifa zote"}
                status_code = 400
            elif len(username) > 80 or len(password) > 256:
                data = {"ok": False, "message": "Taarifa ni ndefu sana"}
                status_code = 400
            else:
                data = register_user(username.strip(), password)

        elif path == "/api/login":
            username = payload.get("username")
            password = payload.get("password")
            user = login_user(username, password)

            if not user:
                data = {"ok": False, "message": "Login imekataa"}
                status_code = 401
            else:
                token, expires = create_session(user["username"])
                set_cookie = self.session_cookie(token, expires)

                premium = check_premium(user)
                days_left = 0

                if premium and user.get("expiry"):
                    expiry_date = datetime.fromisoformat(user["expiry"])
                    if expiry_date.tzinfo is None:
                        expiry_date = expiry_date.replace(tzinfo=timezone.utc)
                    days_left = max(
                        0,
                        (expiry_date - datetime.now(timezone.utc)).days
                    )

                data = {
                    "ok": True,
                    "username": user["username"],
                    "status": user["status"],
                    "premium": premium,
                    "days_left": days_left,
                    "is_admin": self.is_admin(user)
                }

        elif path == "/api/me":
            user = self.current_user()

            if not user:
                data = {"ok": False, "message": "Ingia kwanza"}
                status_code = 401
            else:
                premium = check_premium(user)
                days_left = 0

                if premium and user.get("expiry"):
                    expiry_date = datetime.fromisoformat(user["expiry"])
                    if expiry_date.tzinfo is None:
                        expiry_date = expiry_date.replace(tzinfo=timezone.utc)
                    days_left = max(
                        0,
                        (expiry_date - datetime.now(timezone.utc)).days
                    )

                data = {
                    "ok": True,
                    "username": user["username"],
                    "status": user["status"],
                    "premium": premium,
                    "days_left": days_left,
                    "is_admin": self.is_admin(user)
                }

        elif path == "/api/logout":
            delete_session(self.session_token())
            set_cookie = self.clear_session_cookie()
            data = {"ok": True, "message": "Umetoka kwenye akaunti"}

        elif path == "/api/activate":
            user = self.current_user()

            if not self.is_admin(user):
                data = {"ok": False, "message": "Admin access inahitajika"}
                status_code = 403
            else:
                username = payload.get("username")

                if isinstance(username, str) and activate_user(username):
                    data = {"ok": True, "message": "User activated"}
                else:
                    data = {"ok": False, "message": "User hajapatikana"}
                    status_code = 404

        elif path == "/api/robot-toggle":
            user = self.current_user()

            if not user:
                data = {"ok": False, "message": "Ingia kwanza"}
                status_code = 401
            elif not self.is_admin(user):
                data = {"ok": False, "message": "Admin pekee anaweza kubadilisha robot"}
                status_code = 403
            else:
                try:
                    state = json.loads(Path("robot_state.json").read_text())
                except Exception:
                    state = {"enabled": False}

                state["enabled"] = not bool(state.get("enabled", False))
                Path("robot_state.json").write_text(
                    json.dumps(state, indent=2)
                )
                data = {"ok": True, "enabled": state["enabled"]}

        elif path == "/api/payment":
            user = self.current_user()

            if not user:
                data = {"ok": False, "message": "Ingia kwanza"}
                status_code = 401
            else:
                transaction_id = payload.get("transaction_id")
                payer_name = payload.get("payer_name")

                if (
                    not isinstance(transaction_id, str)
                    or not transaction_id.strip()
                    or len(transaction_id) > 200
                    or not isinstance(payer_name, str)
                    or not payer_name.strip()
                    or len(payer_name) > 120
                ):
                    data = {"ok": False, "message": "Jaza taarifa za malipo"}
                    status_code = 400
                else:
                    users = load_users()
                    found = False

                    for item in users:
                        if item.get("username") == user.get("username"):
                            item["status"] = "PAYMENT_SENT"
                            item["transaction_id"] = transaction_id.strip()
                            item["payer_name"] = payer_name.strip()
                            found = True
                            break

                    if found:
                        save_users(users)
                        data = {
                            "ok": True,
                            "message": "Ombi la malipo limetumwa"
                        }
                    else:
                        data = {"ok": False, "message": "Akaunti haijapatikana"}
                        status_code = 404

        else:
            data = {"ok": False, "message": "Endpoint haijapatikana"}
            status_code = 404

        self.send_json(data, status=status_code, set_cookie=set_cookie)

hub.start()
hub.send("Subscribe", [["EURUSD", "GBPUSD_otc", "USDJPY_otc", "AUDUSD_otc", "BTCUSD_otc"]])
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    server = HTTPServer(("0.0.0.0", port), Handler)

    print(f"GEMiN M1 Signal Bot server running on port {port}")

    server.serve_forever()
