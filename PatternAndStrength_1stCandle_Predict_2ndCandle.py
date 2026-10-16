
import os
import time as time_module
import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pyotp
from SmartApi import SmartConnect


# ============================================================
# CONFIGURATION
# ============================================================

# Set these as environment variables before running the script.
API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"

EXCHANGE = "NSE"
SYMBOL_TOKEN = "99926000"  # NIFTY 50
INTERVAL = "FIVE_MINUTE"

IST = ZoneInfo("Asia/Kolkata")
SELECTED_YEAR = 2026
SELECTED_MONTH = 10

# Candle classification thresholds
DOJI_BODY_PERCENT = 10.0
STRONG_BODY_PERCENT = 60.0
STRONG_MAX_WICK_PERCENT = 20.0

# Pattern thresholds
MARUBOZU_BODY_PERCENT = 90.0
MARUBOZU_MAX_WICK_PERCENT = 5.0
PIN_WICK_PERCENT = 60.0
PIN_OTHER_WICK_MAX_PERCENT = 10.0
PIN_BODY_MAX_PERCENT = 30.0

# API retry settings
MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

OUTPUT_CSV = (
    f"nifty_0915_0920_{SELECTED_YEAR}_{SELECTED_MONTH:02d}.csv"
)

# Result counters
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
    elif row["close"] < row["open"]:
        return "Red"
    return "Doji"


# ============================================================
# CANDLE CLASSIFICATION
# ============================================================

def classify_candle(row):
    candle_range = row["range"]

    if candle_range <= 0:
        return "Doji"

    body_pct = row["body_percent"]
    upper_pct = row["upper_wick_percent"]
    lower_pct = row["lower_wick_percent"]
    color = row["color"]

    if body_pct <= DOJI_BODY_PERCENT or color == "Doji":
        return "Doji"

    strong = (
        body_pct >= STRONG_BODY_PERCENT
        and upper_pct <= STRONG_MAX_WICK_PERCENT
        and lower_pct <= STRONG_MAX_WICK_PERCENT
    )

    if color == "Green":
        return "Strong Bullish" if strong else "Weak Bullish"

    if color == "Red":
        return "Strong Bearish" if strong else "Weak Bearish"

    return "Doji"


# ============================================================
# PATTERN DETECTION
# ============================================================

def detect_candle_pattern(row):
    candle_range = row["range"]

    if candle_range <= 0:
        return "None"

    color = row["color"]

    if color == "Doji":
        return "None"

    body = row["body"]
    body_pct = row["body_percent"]
    upper_wick = row["upper_wick"]
    lower_wick = row["lower_wick"]
    upper_pct = row["upper_wick_percent"]
    lower_pct = row["lower_wick_percent"]

    # 1. MARUBOZU
    if (
        body_pct >= MARUBOZU_BODY_PERCENT
        and upper_pct <= MARUBOZU_MAX_WICK_PERCENT
        and lower_pct <= MARUBOZU_MAX_WICK_PERCENT
    ):
        if color == "Green":
            return "Bullish Marubozu"
        return "Bearish Marubozu"

    # 2. PIN BAR: LONG LOWER WICK
    if (
        lower_pct >= PIN_WICK_PERCENT
        and upper_pct <= PIN_OTHER_WICK_MAX_PERCENT
        and body_pct <= PIN_BODY_MAX_PERCENT
    ):
        if color == "Green":
            return "Bullish Pin Bar"
        return "Bearish Pin Bar"

    # PIN BAR: LONG UPPER WICK
    if (
        upper_pct >= PIN_WICK_PERCENT
        and lower_pct <= PIN_OTHER_WICK_MAX_PERCENT
        and body_pct <= PIN_BODY_MAX_PERCENT
    ):
        if color == "Green":
            return "Bullish Pin Bar"
        return "Bearish Pin Bar"

    # 3. HAMMER / HANGING MAN
    if (
        lower_wick >= 2 * body
        and lower_pct >= 40
        and upper_pct <= 15
        and body_pct <= 35
    ):
        if color == "Green":
            return "Hammer"
        return "Hanging Man"

    # 4. INVERTED HAMMER / SHOOTING STAR
    if (
        upper_wick >= 2 * body
        and upper_pct >= 40
        and lower_pct <= 15
        and body_pct <= 35
    ):
        if color == "Green":
            return "Inverted Hammer"
        return "Shooting Star"

    return "None"


# ============================================================
# CALCULATE CANDLE METRICS
# ============================================================

def add_candle_metrics(df):
    df = df.copy()

    for column in ["open", "high", "low", "close"]:
        df[column] = pd.to_numeric(
            df[column], errors="coerce"
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).copy()

    df["range"] = df["high"] - df["low"]
    df["body"] = (df["close"] - df["open"]).abs()

    df["upper_wick"] = (
        df["high"] - df[["open", "close"]].max(axis=1)
    ).clip(lower=0)

    df["lower_wick"] = (
        df[["open", "close"]].min(axis=1) - df["low"]
    ).clip(lower=0)

    valid_range = df["range"] > 0

    df["body_percent"] = 0.0
    df["upper_wick_percent"] = 0.0
    df["lower_wick_percent"] = 0.0

    df.loc[valid_range, "body_percent"] = (
        df.loc[valid_range, "body"]
        / df.loc[valid_range, "range"] * 100
    )

    df.loc[valid_range, "upper_wick_percent"] = (
        df.loc[valid_range, "upper_wick"]
        / df.loc[valid_range, "range"] * 100
    )

    df.loc[valid_range, "lower_wick_percent"] = (
        df.loc[valid_range, "lower_wick"]
        / df.loc[valid_range, "range"] * 100
    )

    df["color"] = df.apply(candle_color, axis=1)

    df["classification"] = df.apply(
        classify_candle, axis=1
    )

    df["pattern"] = df.apply(
        detect_candle_pattern, axis=1
    )

    return df


# ============================================================
# PREDICTION LOGIC
# PATTERN FIRST; OTHERWISE USE STRENGTH
# ============================================================

def predict_second_candle(first):
    pattern = first["pattern"]
    classification = first["classification"]

    bullish_patterns = {
        "Hammer",
        "Inverted Hammer",
        "Bullish Marubozu",
        "Bullish Pin Bar",
    }

    bearish_patterns = {
        "Bearish Marubozu",
        "Shooting Star",
        "Hanging Man",
        "Bearish Pin Bar",
    }

    # Priority 1: recognized pattern
    if pattern in bullish_patterns:
        return "Green", f"Pattern: {pattern}"

    if pattern in bearish_patterns:
        return "Red", f"Pattern: {pattern}"

    # Priority 2: strength classification
    strength_predictions = {
        "Strong Bullish": "Green",
        "Weak Bullish": "Red",
        "Strong Bearish": "Red",
        "Weak Bearish": "Green",
    }

    predicted_color = strength_predictions.get(
        classification
    )

    if predicted_color is not None:
        return predicted_color, f"Strength: {classification}"

    return "No Prediction", "No pattern or usable strength"


# ============================================================
# SMARTAPI LOGIN
# ============================================================

def login_to_smartapi():
    smart_api = SmartConnect(api_key=API_KEY)

    totp = pyotp.TOTP(TOTP_SECRET).now()

    session_data = smart_api.generateSession(
        CLIENT_CODE,
        PASSWORD,
        totp
    )

    if (
        not session_data
        or not session_data.get("status")
    ):
        raise RuntimeError(
            f"SmartAPI login failed: {session_data}"
        )

    return smart_api


# ============================================================
# FETCH HISTORICAL CANDLES WITH RETRIES
# ============================================================

def fetch_historical_data(smart_api, from_date, to_date):
    params = {
        "exchange": EXCHANGE,
        "symboltoken": SYMBOL_TOKEN,
        "interval": INTERVAL,
        "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
        "todate": to_date.strftime("%Y-%m-%d %H:%M"),
    }

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = smart_api.getCandleData(params)

            if (
                response
                and response.get("status")
                and response.get("data") is not None
            ):
                rows = response["data"]

                columns = [
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                ]

                return pd.DataFrame(
                    rows, columns=columns
                )

            last_error = RuntimeError(
                f"Historical API returned: {response}"
            )

        except Exception as exc:
            last_error = exc

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_SECONDS * (
                2 ** (attempt - 1)
            )

            print(
                f"Attempt {attempt}/{MAX_RETRIES} failed: "
                f"{last_error}. Retrying in {delay}s..."
            )

            time_module.sleep(delay)

    raise RuntimeError(
        f"Failed to fetch historical data after "
        f"{MAX_RETRIES} attempts: {last_error}"
    )


# ============================================================
# SELECTED MONTH DATE RANGE
# ============================================================

def month_start_end(year, month):
    last_day = calendar.monthrange(year, month)[1]

    start = datetime(
        year, month, 1, 9, 0, tzinfo=IST
    )

    end = datetime(
        year, month, last_day, 15, 30, tzinfo=IST
    )

    return start, end


# ============================================================
# MAIN ANALYSIS
# ============================================================

def main():
    global valid_counter
    global invalid_counter
    global doji_counter
    global incomplete_counter
    global unclassified_first_counter
    global daily_results

    smart_api = None

    try:
        start_dt, end_dt = month_start_end(
            SELECTED_YEAR,
            SELECTED_MONTH
        )

        print("Logging in to SmartAPI...")
        smart_api = login_to_smartapi()
        print("Login successful.")

        print(
            f"Fetching NIFTY 50 candles for "
            f"{SELECTED_YEAR}-{SELECTED_MONTH:02d}..."
        )

        raw_df = fetch_historical_data(
            smart_api,
            start_dt,
            end_dt
        )

        if raw_df.empty:
            print("No historical data returned.")
            return

        # Parse timestamps and convert to Indian time.
        raw_df["timestamp"] = pd.to_datetime(
            raw_df["timestamp"],
            errors="coerce"
        )

        raw_df = raw_df.dropna(
            subset=["timestamp"]
        ).copy()

        if raw_df["timestamp"].dt.tz is None:
            raw_df["timestamp"] = (
                raw_df["timestamp"].dt.tz_localize(IST)
            )
        else:
            raw_df["timestamp"] = (
                raw_df["timestamp"].dt.tz_convert(IST)
            )

        raw_df["candle_time"] = (
            raw_df["timestamp"].dt.strftime("%H:%M")
        )

        # Only analyze the 09:15 and 09:20 candles.
        raw_df = raw_df[
            raw_df["candle_time"].isin(
                ["09:15", "09:20"]
            )
        ].copy()

        if raw_df.empty:
            print("No 09:15 or 09:20 candles found.")
            return

        raw_df["trading_date"] = (
            raw_df["timestamp"].dt.date
        )

        df = add_candle_metrics(raw_df)

        df = df.sort_values(
            "timestamp"
        ).reset_index(drop=True)

        # ----------------------------------------------------
        # PROCESS EACH TRADING DAY
        # ----------------------------------------------------

        for trading_date, day_df in df.groupby(
            "trading_date"
        ):
            day_df = day_df.sort_values("timestamp")

            first_rows = day_df[
                day_df["candle_time"] == "09:15"
            ]

            second_rows = day_df[
                day_df["candle_time"] == "09:20"
            ]

            # Handle missing candles.
            if first_rows.empty or second_rows.empty:
                incomplete_counter += 1

                first = (
                    first_rows.iloc[0]
                    if not first_rows.empty else None
                )

                second = (
                    second_rows.iloc[0]
                    if not second_rows.empty else None
                )

                daily_results.append({
                    "date": trading_date,
                    "result": "Incomplete",

                    "09:15_color": (
                        first["color"]
                        if first is not None else "Missing"
                    ),

                    "09:15_classification": (
                        first["classification"]
                        if first is not None else "Missing"
                    ),

                    "09:15_pattern": (
                        first["pattern"]
                        if first is not None else "Missing"
                    ),

                    "prediction_source": "N/A",
                    "predicted_09:20_color": "N/A",

                    "09:20_color": (
                        second["color"]
                        if second is not None else "Missing"
                    ),

                    "09:20_classification": (
                        second["classification"]
                        if second is not None else "Missing"
                    ),

                    "09:20_pattern": (
                        second["pattern"]
                        if second is not None else "Missing"
                    ),
                })

                continue

            first = first_rows.iloc[0]
            second = second_rows.iloc[0]

            # Predict using the first candle.
            predicted_color, prediction_source = (
                predict_second_candle(first)
            )

            second_color = second["color"]

            # Evaluate prediction against actual 09:20 candle.
            if second_color == "Doji":
                doji_counter += 1
                result = "Doji"

            elif predicted_color == "No Prediction":
                unclassified_first_counter += 1
                result = "No Prediction"

            elif second_color == predicted_color:
                valid_counter += 1
                result = "Valid"

            else:
                invalid_counter += 1
                result = "Invalid"

            daily_results.append({
                "date": trading_date,
                "result": result,

                "09:15_color": first["color"],
                "09:15_classification": first["classification"],
                "09:15_pattern": first["pattern"],

                "prediction_source": prediction_source,
                "predicted_09:20_color": predicted_color,

                "09:20_color": second_color,
                "09:20_classification": second["classification"],
                "09:20_pattern": second["pattern"],

                "09:15_open": first["open"],
                "09:15_high": first["high"],
                "09:15_low": first["low"],
                "09:15_close": first["close"],

                "09:20_open": second["open"],
                "09:20_high": second["high"],
                "09:20_low": second["low"],
                "09:20_close": second["close"],
            })

        # ----------------------------------------------------
        # DAILY RESULTS
        # ----------------------------------------------------

        results_df = pd.DataFrame(daily_results)

        if results_df.empty:
            print("No daily results to display.")
            return

        display_columns = [
            "date",
            "result",
            "09:15_color",
            "09:15_classification",
            "09:15_pattern",
            # "prediction_source",
            # "predicted_09:20_color",
            "09:20_color",
            # "09:20_classification",
            # "09:20_pattern",
        ]

        print("\n========== DAILY PREDICTION SUMMARY ==========")

        print(
            results_df[display_columns].to_string(
                index=False
            )
        )

        # Save results to CSV.
        results_df.to_csv(
            OUTPUT_CSV,
            index=False
        )

        print(
            f"\nCSV saved to: {os.path.abspath(OUTPUT_CSV)}"
        )

        # ----------------------------------------------------
        # SUMMARY STATISTICS
        # ----------------------------------------------------

        print("\n========== SUMMARY STATISTICS ==========")

        print(
            f"Month analyzed: "
            f"{SELECTED_YEAR}-{SELECTED_MONTH:02d}"
        )

        print(f"Valid predictions: {valid_counter}")
        print(f"Invalid predictions: {invalid_counter}")
        print(f"Actual 09:20 Doji candles: {doji_counter}")
        print(f"Incomplete trading days: {incomplete_counter}")

        print(
            f"No-prediction days: "
            f"{unclassified_first_counter}"
        )

        scored_total = valid_counter + invalid_counter

        # ----------------------------------------------------
        # ACCURACY AND TOTAL POINTS
        # ----------------------------------------------------

        if scored_total > 0:
            accuracy = (
                valid_counter / scored_total
            ) * 100

            # NEW: TOTAL POINTS CALCULATION
            # Include only valid and invalid predictions.
            points_data = results_df[
                results_df["result"].isin(
                    ["Valid", "Invalid"]
                )
            ].copy()

            # Actual price movement of the 09:20 candle.
            points_data["points"] = (
                points_data["09:20_close"]
                - points_data["09:20_open"]
            )

            # Green prediction: profit/loss follows close - open.
            # Red prediction: profit/loss follows open - close.
            # This produces positive points when the predicted
            # direction matches the actual candle direction.
            points_data["signed_points"] = (
                points_data.apply(
                    lambda row: (
                        row["points"]
                        if row["predicted_09:20_color"] == "Green"
                        else -row["points"]
                    ),
                    axis=1
                )
            )

            total_points = points_data[
                "signed_points"
            ].sum()

            print(
                f"Accuracy (excluding doji, incomplete, "
                f"and no-prediction days): {accuracy:.2f}%"
            )

            print(
                f"Total points on predicted candles: "
                f"{total_points:.2f}"
            )

        else:
            print(
                "Accuracy: N/A (no predictions were scored)"
            )

            print(
                "Total points on predicted candles: 0.00"
            )

    except Exception as exc:
        print(f"Error: {exc}")

    finally:
        if smart_api is not None:
            try:
                smart_api.terminateSession(CLIENT_CODE)
                print("\nSmartAPI session terminated.")
            except Exception as exc:
                print(
                    "Could not terminate SmartAPI session cleanly: "
                    f"{exc}"
                )


if __name__ == "__main__":
    main()
