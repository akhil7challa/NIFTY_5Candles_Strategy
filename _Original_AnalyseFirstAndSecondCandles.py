
from datetime import datetime, date, time
from zoneinfo import ZoneInfo

import pandas as pd
import pyotp
from SmartApi import SmartConnect


# =========================
# SMARTAPI CREDENTIALS
# =========================
API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"


# =========================
# INSTRUMENT SETTINGS
# =========================
EXCHANGE = "NSE"
SYMBOL_TOKEN = "99926000"  # NIFTY 50 index



# =========================
# SELECT MONTH AND YEAR
# =========================

IST = ZoneInfo("Asia/Kolkata")

# Change these two values whenever you want
SELECTED_YEAR = 2026
SELECTED_MONTH = 5

if SELECTED_MONTH < 1 or SELECTED_MONTH > 12:
    raise ValueError("SELECTED_MONTH must be between 1 and 12")

start_date = date(SELECTED_YEAR, SELECTED_MONTH, 1)

# Last calendar day of selected month
import calendar
last_day = calendar.monthrange(
    SELECTED_YEAR, SELECTED_MONTH
)[1]

end_date = date(SELECTED_YEAR, SELECTED_MONTH, last_day)

# Do not request future data
today = datetime.now(IST).date()
if start_date > today:
    raise ValueError("Selected month is in the future")

if end_date > today:
    end_date = today

from_date = datetime.combine(
    start_date, time(9, 0)
).strftime("%Y-%m-%d %H:%M")

to_date = datetime.combine(
    end_date, time(23, 59)
).strftime("%Y-%m-%d %H:%M")


# =========================
# COUNTERS
# =========================
same_counter = 0
different_counter = 0
doji_counter = 0
incomplete_counter = 0


# =========================
# SMARTAPI LOGIN
# =========================
smart_api = SmartConnect(api_key=API_KEY)
logged_in = False

try:
    current_totp = pyotp.TOTP(TOTP_SECRET).now()

    session = smart_api.generateSession(
        CLIENT_CODE,
        PASSWORD,
        current_totp
    )

    if not session or not session.get("status"):
        raise RuntimeError(
            f"SmartAPI login failed: {session}"
        )

    logged_in = True
    print("SmartAPI login successful.")

    # =========================
    # FETCH HISTORICAL CANDLES
    # =========================
    params = {
        "exchange": EXCHANGE,
        "symboltoken": SYMBOL_TOKEN,
        "interval": "FIVE_MINUTE",
        "fromdate": from_date,
        "todate": to_date,
    }

    response = smart_api.getCandleData(params)

    if not response or not response.get("status"):
        raise RuntimeError(
            f"Historical API error: {response}"
        )

    candles = response.get("data") or []

    columns = [
        "datetime",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    df = pd.DataFrame(candles, columns=columns)

    # =========================
    # CLEAN AND FILTER DATA
    # =========================
    if not df.empty:
        df["datetime"] = pd.to_datetime(df["datetime"])

        numeric_columns = [
            "open", "high", "low", "close", "volume"
        ]

        for col in numeric_columns:
            df[col] = pd.to_numeric(
                df[col], errors="coerce"
            )

        df = (
            df.sort_values("datetime")
            .drop_duplicates(subset=["datetime"])
            .reset_index(drop=True)
        )

        # Keep only 09:15 and 09:20 candles
        df["time"] = df["datetime"].dt.strftime("%H:%M")

        df = df[
            df["time"].isin(["09:15", "09:20"])
        ].copy()

        # Classify each candle
        df["trend"] = "Doji"

        df.loc[
            df["close"] > df["open"], "trend"
        ] = "Bullish"

        df.loc[
            df["close"] < df["open"], "trend"
        ] = "Bearish"

        df = df.sort_values("datetime").reset_index(
            drop=True
        )

        # =========================
        # COUNT CANDLE RELATIONSHIPS
        # =========================
        df["trading_date"] = df["datetime"].dt.date

        daily_results = []

        for trading_date, group in df.groupby(
            "trading_date", sort=True
        ):
            group = group.copy()

            group["time"] = group[
                "datetime"
            ].dt.strftime("%H:%M")

            candle_915 = group[
                group["time"] == "09:15"
            ]

            candle_920 = group[
                group["time"] == "09:20"
            ]

            if candle_915.empty or candle_920.empty:
                incomplete_counter += 1
                continue

            trend_915 = candle_915.iloc[0]["trend"]
            trend_920 = candle_920.iloc[0]["trend"]

            # Exclude Doji days from same/different counts
            if (
                trend_915 == "Doji"
                or trend_920 == "Doji"
            ):
                doji_counter += 1
                result = "Doji"
            elif trend_915 == trend_920:
                same_counter += 1
                result = "Same"
            else:
                different_counter += 1
                result = "Different"

            daily_results.append({
                "date": trading_date,
                "09:15_trend": trend_915,
                "09:20_trend": trend_920,
                "relationship": result,
            })

        # =========================
        # SAVE CANDLE CSV
        # =========================
        filename = (
            f"candles_{EXCHANGE}_{SYMBOL_TOKEN}_"
            f"{start_date:%Y_%m}_5min.csv"
        )

        df.drop(
            columns=["time", "trading_date"],
            errors="ignore"
        ).to_csv(filename, index=False)

        print("\nLatest candle data:")
        print(df.tail(10).to_string(index=False))

        print(f"\nCandle CSV saved: {filename}")

        # =========================
        # SAVE DAILY SUMMARY CSV
        # =========================
        summary_df = pd.DataFrame(
            daily_results,
            columns=[
                "date",
                "09:15_trend",
                "09:20_trend",
                "relationship",
            ],
        )

        summary_filename = (
            f"daily_summary_{EXCHANGE}_"
            f"{SYMBOL_TOKEN}_{start_date:%Y_%m}.csv"
        )

        summary_df.to_csv(summary_filename, index=False)

        print(f"Daily summary saved: {summary_filename}")

    else:
        print("No candle data returned for this period.")

    # =========================
    # PRINT COUNTERS
    # =========================
    total_valid = same_counter + different_counter

    print("\n" + "=" * 42)
    print("     NIFTY 50 CANDLE STATISTICS")
    print("=" * 42)

    print(f"Same direction counter      : {same_counter}")
    print(f"Different direction counter : {different_counter}")
    print(f"Doji days                   : {doji_counter}")
    print(f"Incomplete trading days     : {incomplete_counter}")
    print(f"Valid same/different days   : {total_valid}")

    if total_valid > 0:
        print(
            f"Same direction percentage      : "
            f"{same_counter / total_valid * 100:.2f}%"
        )
        print(
            f"Different direction percentage : "
            f"{different_counter / total_valid * 100:.2f}%"
        )

    print("=" * 42)

finally:
    if logged_in:
        try:
            smart_api.terminateSession(CLIENT_CODE)
            print("SmartAPI session terminated.")
        except Exception as logout_error:
            print(f"Logout warning: {logout_error}")
