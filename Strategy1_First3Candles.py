
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

# Set these environment variables:
# SMARTAPI_API_KEY
# SMARTAPI_CLIENT_CODE
# SMARTAPI_PASSWORD
# SMARTAPI_TOTP_SECRET

API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"

EXCHANGE = "NSE"
SYMBOL_TOKEN = "99926000"  # NIFTY 50
INTERVAL = "FIVE_MINUTE"

IST = ZoneInfo("Asia/Kolkata")

SELECTED_YEAR = 2020
SELECTED_MONTH = 12

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

OUTPUT_CSV = (
    f"nifty_4th_candle_pattern_"
    f"{SELECTED_YEAR}_{SELECTED_MONTH:02d}.csv"
)

FIRST_THREE_TIMES = ["09:15", "09:20", "09:25"]
FOURTH_CANDLE_TIME = "09:30"


# ============================================================
# CANDLE COLOR AND METRICS
# ============================================================

def candle_color(row):
    if row["close"] > row["open"]:
        return "Green"
    elif row["close"] < row["open"]:
        return "Red"
    return "Doji"


def add_candle_metrics(df):
    df = df.copy()

    for column in ["open", "high", "low", "close", "volume"]:
        if column in df.columns:
            df[column] = pd.to_numeric(
                df[column], errors="coerce"
            )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).copy()

    df["range"] = df["high"] - df["low"]
    df["body"] = (df["close"] - df["open"]).abs()
    df["color"] = df.apply(candle_color, axis=1)

    return df


# ============================================================
# THREE-CANDLE PATTERN PREDICTION
# ============================================================

def predict_fourth_candle(first, second, third):

    rules = {
        ("Red", "Red", "Green"): "Green",
        ("Green", "Green", "Red"): "Red",
        ("Red", "Green", "Red"): "Red",
        ("Green", "Red", "Green"): "Green",
    }

    return rules.get(
        (first, second, third),
        "No Prediction"
    )


# ============================================================
# SMARTAPI LOGIN
# ============================================================

def login_to_smartapi():

    if not all([
        API_KEY,
        CLIENT_CODE,
        PASSWORD,
        TOTP_SECRET
    ]):
        raise ValueError(
            "Set SMARTAPI_API_KEY, SMARTAPI_CLIENT_CODE, "
            "SMARTAPI_PASSWORD, and SMARTAPI_TOTP_SECRET "
            "environment variables."
        )

    smart_api = SmartConnect(api_key=API_KEY)

    totp = pyotp.TOTP(TOTP_SECRET).now()

    session_data = smart_api.generateSession(
        CLIENT_CODE,
        PASSWORD,
        totp
    )

    if not session_data or not session_data.get("status"):
        raise RuntimeError(
            f"SmartAPI login failed: {session_data}"
        )

    return smart_api


# ============================================================
# FETCH HISTORICAL DATA WITH RETRIES
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
                columns = [
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                ]

                return pd.DataFrame(
                    response["data"],
                    columns=columns
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
        f"Failed to fetch data after {MAX_RETRIES} attempts: "
        f"{last_error}"
    )


# ============================================================
# MONTH DATE RANGE
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
# ANALYZE ONE TRADING DAY
# ============================================================

def analyze_day(trading_date, day_df):

    day_df = day_df.sort_values("timestamp").copy()

    # Find the first three expected candles.
    first_three = (
        day_df[
            day_df["candle_time"].isin(FIRST_THREE_TIMES)
        ]
        .drop_duplicates(
            subset=["candle_time"],
            keep="first"
        )
        .set_index("candle_time")
        .reindex(FIRST_THREE_TIMES)
    )

    colors = []

    for candle_time in FIRST_THREE_TIMES:
        color = first_three.loc[candle_time, "color"]

        if pd.isna(color):
            color = "Missing"

        colors.append(color)

    color1, color2, color3 = colors

    result_row = {
        "date": trading_date,
        "candle_1_color": color1,
        "candle_2_color": color2,
        "candle_3_color": color3,
        "predicted_4th_color": "No Prediction",
        "actual_4th_color": "Missing",
        "result": "Incomplete",
        "4th_open": None,
        "4th_high": None,
        "4th_low": None,
        "4th_close": None,
        "points": None,
    }

    # Do not predict if any of the first three candles is missing.
    if "Missing" in colors:
        return result_row

    predicted = predict_fourth_candle(
        color1,
        color2,
        color3
    )

    result_row["predicted_4th_color"] = predicted

    # The fourth candle starts at 09:30 IST.
    fourth_rows = day_df[
        day_df["candle_time"] == FOURTH_CANDLE_TIME
    ]

    if fourth_rows.empty:
        return result_row

    fourth = fourth_rows.iloc[0]
    actual = fourth["color"]

    result_row.update({
        "actual_4th_color": actual,
        "4th_open": float(fourth["open"]),
        "4th_high": float(fourth["high"]),
        "4th_low": float(fourth["low"]),
        "4th_close": float(fourth["close"]),
    })

    # Patterns without a rule are not scored.
    if predicted == "No Prediction":
        result_row["result"] = "No Prediction"
        return result_row

    # Doji candles are excluded from accuracy.
    if actual == "Doji":
        result_row["result"] = "Doji"
        result_row["points"] = 0.0
        return result_row

    # Directional points:
    # Green prediction: close - open
    # Red prediction:   open - close
    #
    # Correct direction gives positive points.
    # Wrong direction gives negative points.

    candle_move = float(
        fourth["close"] - fourth["open"]
    )

    if predicted == "Green":
        points = candle_move
    else:
        points = -candle_move

    result_row["points"] = points

    if predicted == actual:
        result_row["result"] = "Correct"
    else:
        result_row["result"] = "Incorrect"

    return result_row


# ============================================================
# MAIN ANALYSIS
# ============================================================

def main():

    smart_api = None

    try:
        start_dt, end_dt = month_start_end(
            SELECTED_YEAR,
            SELECTED_MONTH
        )

        print("Logging in to SmartAPI...")

        smart_api = login_to_smartapi()

        print("Login successful.")

        raw_df = fetch_historical_data(
            smart_api,
            start_dt,
            end_dt
        )

        if raw_df.empty:
            print("No historical data returned.")
            return

        # Parse timestamps.
        raw_df["timestamp"] = pd.to_datetime(
            raw_df["timestamp"],
            errors="coerce"
        )

        raw_df = raw_df.dropna(
            subset=["timestamp"]
        ).copy()

        # Convert timestamps to Indian Standard Time.
        if raw_df["timestamp"].dt.tz is None:
            raw_df["timestamp"] = (
                raw_df["timestamp"].dt.tz_localize(IST)
            )
        else:
            raw_df["timestamp"] = (
                raw_df["timestamp"].dt.tz_convert(IST)
            )

        df = add_candle_metrics(raw_df)

        df = df.sort_values("timestamp").copy()

        df["candle_time"] = (
            df["timestamp"].dt.strftime("%H:%M")
        )

        df["trading_date"] = (
            df["timestamp"].dt.date
        )

        # Analyze each trading day.
        daily_results = []

        for trading_date, day_df in df.groupby(
            "trading_date"
        ):
            result = analyze_day(
                trading_date,
                day_df
            )

            daily_results.append(result)

        results_df = pd.DataFrame(daily_results)

        if results_df.empty:
            print("No daily results available.")
            return

        # Save all daily results.
        results_df.to_csv(
            OUTPUT_CSV,
            index=False
        )

        # ----------------------------------------------------
        # DAILY SUMMARY
        # ----------------------------------------------------

        print(
            "\n========== DAILY PREDICTION SUMMARY =========="
        )

        display_columns = [
            "date",
            "candle_1_color",
            "candle_2_color",
            "candle_3_color",
            "predicted_4th_color",
            "actual_4th_color",
            "result",
            "points",
        ]

        print(
            results_df[display_columns].to_string(
                index=False
            )
        )

        # ----------------------------------------------------
        # MONTHLY ACCURACY
        # ----------------------------------------------------

        scored = results_df[
            results_df["result"].isin([
                "Correct",
                "Incorrect"
            ])
        ]

        correct_count = int(
            (scored["result"] == "Correct").sum()
        )

        incorrect_count = int(
            (scored["result"] == "Incorrect").sum()
        )

        doji_count = int(
            (results_df["result"] == "Doji").sum()
        )

        incomplete_count = int(
            (results_df["result"] == "Incomplete").sum()
        )

        no_prediction_count = int(
            (results_df["result"] == "No Prediction").sum()
        )

        scored_total = (
            correct_count + incorrect_count
        )

        if scored_total > 0:
            accuracy = (
                correct_count / scored_total
            ) * 100
        else:
            accuracy = 0.0

        # Sum points only for scored predictions.
        total_points = float(
            scored["points"].fillna(0).sum()
        )

        # ----------------------------------------------------
        # MONTHLY SUMMARY
        # ----------------------------------------------------

        print(
            "\n========== MONTHLY SUMMARY =========="
        )

        print(
            f"Month analyzed: "
            f"{SELECTED_YEAR}-{SELECTED_MONTH:02d}"
        )

        print(
            "Strategy: Predict 4th candle "
            "from first 3 candle colors"
        )

        print(f"Correct predictions: {correct_count}")
        print(f"Incorrect predictions: {incorrect_count}")
        print(f"Doji candles excluded: {doji_count}")
        print(f"Incomplete days: {incomplete_count}")
        print(f"No-rule predictions: {no_prediction_count}")
        print(f"Scored predictions: {scored_total}")

        print(
            f"Prediction accuracy: {accuracy:.2f}%"
        )

        print(
            f"Total directional points: {total_points:.2f}"
        )

        print(
            f"CSV saved to: {os.path.abspath(OUTPUT_CSV)}"
        )

    except Exception as exc:
        print(f"Error: {exc}")

    finally:
        if smart_api is not None:
            try:
                smart_api.terminateSession(CLIENT_CODE)

                print(
                    "\nSmartAPI session terminated."
                )

            except Exception as exc:
                print(
                    f"Could not terminate session: {exc}"
                )


if __name__ == "__main__":
    main()
