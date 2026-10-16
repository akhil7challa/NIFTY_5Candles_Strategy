from datetime import datetime, date, time
from zoneinfo import ZoneInfo
import calendar
import os
import time as time_module

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


# ============================================================
# INSTRUMENT SETTINGS
# ============================================================
EXCHANGE = "NSE"
SYMBOL_TOKEN = "99926000"  # NIFTY 50
INTERVAL = "FIVE_MINUTE"

IST = ZoneInfo("Asia/Kolkata")

# Select month and year for historical analysis.
SELECTED_YEAR = 2026
SELECTED_MONTH = 10


# ============================================================
# CANDLE CLASSIFICATION SETTINGS
# ============================================================
STRONG_BODY_PERCENT = 70


# ============================================================
# COUNTERS
# ============================================================
valid_counter = 0
invalid_counter = 0
doji_counter = 0
incomplete_counter = 0
unclassified_first_counter = 0

daily_results = []


# ============================================================
# CANDLE COLOR
# ============================================================
def candle_color(row):
    if row["close"] > row["open"]:
        return "Green"

    if row["close"] < row["open"]:
        return "Red"

    return "Doji"


# ============================================================
# CANDLE CLASSIFICATION
# ============================================================
def classify_candle(row):
    """
    Classify candles using body, upper-wick and lower-wick
    percentages of the full high-low range.
    """

    if row["range"] <= 0:
        return "Doji"

    body = row["body_percent"]
    upper = row["upper_wick_percent"]
    lower = row["lower_wick_percent"]

    # --------------------------------------------------------
    # GREEN CANDLE
    # --------------------------------------------------------
    if row["close"] > row["open"]:

        # Rule 1: Strong Bullish
        if (
            body >= 70
            and upper <= 15
            and lower <= 15
        ):
            return "Strong Bullish"

        # Rule 2: Weak Bullish
        if (
            body >= 30
            and body < 70
            and upper > 10
            and lower > 25
        ):
            return "Weak Bullish"

        # Rule 3: Strong Bullish
        if (
            body >= 30
            and upper <= 15
            and lower >= 35
        ):
            return "Strong Bullish"

    # --------------------------------------------------------
    # RED CANDLE
    # --------------------------------------------------------
    elif row["close"] < row["open"]:

        # Rule 4: Strong Bearish
        if (
            body >= 70
            and upper <= 15
            and lower <= 15
        ):
            return "Strong Bearish"

        # Rule 5: Weak Bearish
        if (
            body >= 30
            and body < 70
            and upper > 10
            and lower > 25
        ):
            return "Weak Bearish"

        # Rule 6: Strong Bearish
        if (
            body >= 30
            and upper >= 35
            and lower <= 15
        ):
            return "Strong Bearish"

    else:
        return "Doji"

    return "Unclassified"


# ============================================================
# SMARTAPI HISTORICAL DATA WITH RETRIES
# ============================================================
def get_historical_candles_with_retry(
    api,
    params,
    max_attempts=4
):
    """
    Retry temporary failures with increasing delays.
    A retry cannot bypass a server-side rate limit.
    """

    last_error = "Unknown API error"

    for attempt in range(1, max_attempts + 1):

        try:
            response = api.getCandleData(params)

            if response and response.get("status"):
                return response

            last_error = str(response)

        except Exception as exc:
            last_error = str(exc)

        error_lower = last_error.lower()

        is_rate_limit = (
            "rate" in error_lower
            or "access denied" in error_lower
            or "couldn't parse the json response" in error_lower
            or "jsondecodeerror" in error_lower
        )

        # Do not retry unrelated errors.
        if not is_rate_limit:
            raise RuntimeError(
                f"Historical candle request failed: {last_error}"
            )

        if attempt == max_attempts:
            raise RuntimeError(
                "SmartAPI rejected the historical-data request "
                f"after {max_attempts} attempts.\n"
                f"Last error: {last_error}\n"
                "Wait for the rate limit to clear before rerunning."
            )

        delay_seconds = 5 * (2 ** (attempt - 1))

        print(
            f"Historical request failed on attempt "
            f"{attempt}/{max_attempts}.\n"
            f"Error: {last_error}\n"
            f"Waiting {delay_seconds} seconds before retry..."
        )

        time_module.sleep(delay_seconds)

    raise RuntimeError(
        f"Unable to retrieve historical candles: {last_error}"
    )


# ============================================================
# SELECT MONTH AND YEAR
# ============================================================
if not 1 <= SELECTED_MONTH <= 12:
    raise ValueError(
        "SELECTED_MONTH must be between 1 and 12"
    )

start_date = date(
    SELECTED_YEAR,
    SELECTED_MONTH,
    1
)

last_day = calendar.monthrange(
    SELECTED_YEAR,
    SELECTED_MONTH
)[1]

end_date = date(
    SELECTED_YEAR,
    SELECTED_MONTH,
    last_day
)

today = datetime.now(IST).date()

if start_date > today:
    raise ValueError("Selected month is in the future")

if end_date > today:
    end_date = today

from_date = datetime.combine(
    start_date,
    time(9, 0)
).strftime("%Y-%m-%d %H:%M")

to_date = datetime.combine(
    end_date,
    time(23, 59)
).strftime("%Y-%m-%d %H:%M")


# ============================================================
# SMARTAPI LOGIN AND ANALYSIS
# ============================================================
smart_api = SmartConnect(api_key=API_KEY)
logged_in = False

try:

    # --------------------------------------------------------
    # LOGIN
    # --------------------------------------------------------
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

    # --------------------------------------------------------
    # FETCH HISTORICAL CANDLES
    # --------------------------------------------------------
    params = {
        "exchange": EXCHANGE,
        "symboltoken": SYMBOL_TOKEN,
        "interval": INTERVAL,
        "fromdate": from_date,
        "todate": to_date,
    }

    print(
        f"Fetching historical data from {from_date} "
        f"to {to_date}"
    )

    response = get_historical_candles_with_retry(
        smart_api,
        params
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

    df = pd.DataFrame(
        candles,
        columns=columns
    )

    if df.empty:
        print("No candle data returned for this period.")

    else:

        # ----------------------------------------------------
        # CLEAN DATA
        # ----------------------------------------------------
        df["datetime"] = pd.to_datetime(
            df["datetime"]
        )

        numeric_columns = [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

        for col in numeric_columns:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df = df.dropna(
            subset=[
                "datetime",
                "open",
                "high",
                "low",
                "close",
            ]
        )

        df = (
            df.sort_values("datetime")
            .drop_duplicates(subset=["datetime"])
            .reset_index(drop=True)
        )

        # ----------------------------------------------------
        # KEEP ONLY 09:15 AND 09:20 CANDLES
        # ----------------------------------------------------
        df["time"] = df["datetime"].dt.strftime(
            "%H:%M"
        )

        df = df[
            df["time"].isin(["09:15", "09:20"])
        ].copy()

        if df.empty:
            print(
                "No 09:15 or 09:20 candles found "
                "in the API response."
            )

        else:

            # ------------------------------------------------
            # CALCULATE CANDLE BODY AND RANGE
            # ------------------------------------------------
            df["body"] = (
                df["close"] - df["open"]
            ).abs()

            df["range"] = (
                df["high"] - df["low"]
            )

            df["body_percent"] = 0.0

            valid_range = df["range"] > 0

            df.loc[
                valid_range,
                "body_percent"
            ] = (
                df.loc[valid_range, "body"]
                / df.loc[valid_range, "range"]
            ) * 100

            # ------------------------------------------------
            # CALCULATE UPPER AND LOWER WICKS
            # ------------------------------------------------
            df["upper_wick"] = (
                df["high"]
                - df[["open", "close"]].max(axis=1)
            ).clip(lower=0)

            df["lower_wick"] = (
                df[["open", "close"]].min(axis=1)
                - df["low"]
            ).clip(lower=0)

            df["upper_wick_percent"] = 0.0
            df["lower_wick_percent"] = 0.0

            df.loc[
                valid_range,
                "upper_wick_percent"
            ] = (
                df.loc[valid_range, "upper_wick"]
                / df.loc[valid_range, "range"]
            ) * 100

            df.loc[
                valid_range,
                "lower_wick_percent"
            ] = (
                df.loc[valid_range, "lower_wick"]
                / df.loc[valid_range, "range"]
            ) * 100

            # ------------------------------------------------
            # CLASSIFY EACH CANDLE
            # ------------------------------------------------
            df["color"] = df.apply(
                candle_color,
                axis=1
            )

            df["classification"] = df.apply(
                classify_candle,
                axis=1
            )

            # ------------------------------------------------
            # GROUP CANDLES BY TRADING DAY
            # ------------------------------------------------
            df["trading_date"] = (
                df["datetime"].dt.date
            )

            # First-candle classification determines the
            # expected second-candle color.
            expected_second_color = {
                "Strong Bullish": "Green",
                "Weak Bullish": "Red",
                "Strong Bearish": "Red",
                "Weak Bearish": "Green",
            }

            for trading_date, group in df.groupby(
                "trading_date",
                sort=True
            ):

                candle_915 = group[
                    group["time"] == "09:15"
                ]

                candle_920 = group[
                    group["time"] == "09:20"
                ]

                # --------------------------------------------
                # HANDLE MISSING CANDLES
                # --------------------------------------------
                if (
                    candle_915.empty
                    or candle_920.empty
                ):
                    incomplete_counter += 1

                    daily_results.append({
                        "date": trading_date,
                        "09:15_color": (
                            candle_915.iloc[0]["color"]
                            if not candle_915.empty
                            else "Missing"
                        ),
                        "09:15_classification": (
                            candle_915.iloc[0]["classification"]
                            if not candle_915.empty
                            else "Missing"
                        ),
                        "09:20_color": (
                            candle_920.iloc[0]["color"]
                            if not candle_920.empty
                            else "Missing"
                        ),
                        "09:20_classification": (
                            candle_920.iloc[0]["classification"]
                            if not candle_920.empty
                            else "Missing"
                        ),
                        "expected_09:20_color": "N/A",
                        "relationship": "Incomplete",
                    })

                    continue

                # --------------------------------------------
                # GET FIRST AND SECOND CANDLE
                # --------------------------------------------
                first = candle_915.iloc[0]
                second = candle_920.iloc[0]

                first_classification = (
                    first["classification"]
                )

                second_color = second["color"]

                expected_color = (
                    expected_second_color.get(
                        first_classification
                    )
                )

                # --------------------------------------------
                # VALIDATE SECOND CANDLE
                # --------------------------------------------
                if second_color == "Doji":

                    doji_counter += 1
                    result = "Doji"

                elif expected_color is None:

                    unclassified_first_counter += 1
                    result = "First candle unclassified"

                elif second_color == expected_color:

                    valid_counter += 1
                    result = "Valid"

                else:

                    invalid_counter += 1
                    result = "Invalid"

                # --------------------------------------------
                # STORE DAILY RESULT
                # --------------------------------------------
                daily_results.append({
                    "date": trading_date,
                    "09:15_color": first["color"],
                    "09:15_classification": (
                        first_classification
                    ),
                    "09:20_color": second_color,
                    "09:20_classification": (
                        second["classification"]
                    ),
                    "expected_09:20_color": (
                        expected_color or "N/A"
                    ),
                    "relationship": result,
                })

            # =================================================
            # SAVE CANDLE DATA CSV
            # =================================================
            filename = (
                f"candles_{EXCHANGE}_{SYMBOL_TOKEN}_"
                f"{start_date:%Y_%m}_5min.csv"
            )

            candle_export = df.drop(
                columns=[
                    "time",
                    "trading_date"
                ],
                errors="ignore"
            )

            candle_export.to_csv(
                filename,
                index=False
            )

            
            # =================================================
            # PRINT SIMPLE DAILY RESULTS
            # =================================================
            summary_output = []

            for item in daily_results:
                first_color = item["09:15_color"]
                second_color = item["09:20_color"]
                relationship = item["relationship"]

                # Convert candle colors to Bullish/Bearish
                first_candle = (
                    "Bullish" if first_color == "Green"
                    else "Bearish" if first_color == "Red"
                    else first_color
                )

                second_candle = (
                    "Bullish" if second_color == "Green"
                    else "Bearish" if second_color == "Red"
                    else second_color
                )

                # Convert validation result
                if relationship == "Valid":
                    result = "Success"
                elif relationship == "Invalid":
                    result = "Failed"
                else:
                    result = relationship

                summary_output.append({
                    "Date": item["date"],
                    "Result": result,
                    "First Candle (09:15)": first_candle,
                    "Second Candle (09:20)": second_candle
                })

            summary_print_df = pd.DataFrame(summary_output)

            print("\nDAILY CANDLE RESULTS")
            print(summary_print_df.to_string(index=False))

            # Save simplified results
            simple_filename = (
                f"simple_results_{EXCHANGE}_{SYMBOL_TOKEN}_"
                f"{start_date:%Y_%m}.csv"
            )

            summary_print_df.to_csv(simple_filename, index=False)

            print(f"\nSimple results saved: {simple_filename}")


            print(
                f"\nCandle CSV saved: {filename}"
            )

            # =================================================
            # SAVE DAILY SUMMARY CSV
            # =================================================
            summary_columns = [
                "date",
                "09:15_color",
                "09:15_classification",
                "09:20_color",
                "09:20_classification",
                "expected_09:20_color",
                "relationship",
            ]

            summary_df = pd.DataFrame(
                daily_results,
                columns=summary_columns
            )

            summary_filename = (
                f"daily_summary_{EXCHANGE}_"
                f"{SYMBOL_TOKEN}_{start_date:%Y_%m}.csv"
            )

            summary_df.to_csv(
                summary_filename,
                index=False
            )

            print(
                f"Daily summary saved: {summary_filename}"
            )

            # =================================================
            # PRINT STATISTICS
            # =================================================
            total_validated = (
                valid_counter + invalid_counter
            )

            print("\n" + "=" * 55)
            print("       NIFTY 50 CANDLE STATISTICS")
            print("=" * 55)

            print(
                f"Strong body threshold            : "
                f"{STRONG_BODY_PERCENT}%"
            )

            print(
                f"Second candle validation valid   : "
                f"{valid_counter}"
            )

            print(
                f"Second candle validation invalid : "
                f"{invalid_counter}"
            )

            print(
                f"Second candle Doji days          : "
                f"{doji_counter}"
            )

            print(
                f"Incomplete trading days          : "
                f"{incomplete_counter}"
            )

            print(
                f"First candle unclassified        : "
                f"{unclassified_first_counter}"
            )

            print(
                f"Total validated days             : "
                f"{total_validated}"
            )

            if total_validated > 0:

                print(
                    f"Validation success percentage   : "
                    f"{valid_counter / total_validated * 100:.2f}%"
                )

                print(
                    f"Validation failure percentage   : "
                    f"{invalid_counter / total_validated * 100:.2f}%"
                )

            print("=" * 55)

finally:

    # ========================================================
    # LOGOUT
    # ========================================================
    if logged_in:
        try:
            smart_api.terminateSession(
                CLIENT_CODE
            )

            print("SmartAPI session terminated.")

        except Exception as logout_error:
            print(
                f"Logout warning: {logout_error}"
            )