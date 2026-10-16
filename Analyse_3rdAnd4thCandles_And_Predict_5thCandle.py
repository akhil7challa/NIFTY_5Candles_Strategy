
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

# Set these environment variables before running the script.
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

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

OUTPUT_CSV = (
    f"nifty_3rd_4th_predict_5th_"
    f"{SELECTED_YEAR}_{SELECTED_MONTH:02d}.csv"
)


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
    if row["range"] <= 0 or row["color"] == "Doji":
        return "Doji"

    if row["color"] == "Green":
        return "Strong Bullish"

    if row["color"] == "Red":
        return "Strong Bearish"

    return "Doji"


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

    df["color"] = df.apply(candle_color, axis=1)

    df["classification"] = df.apply(
        classify_candle, axis=1
    )

    return df


# ============================================================
# PREDICTION LOGIC
#
# Use the 3rd and 4th candles to predict the 5th candle.
# If both input candles have colors, follow the 4th candle.
# If either input candle is a Doji, skip the prediction.
# ============================================================

def predict_fifth_candle(third, fourth):
    third_color = third["color"]
    fourth_color = fourth["color"]

    third_body = abs(float(third["close"]) - float(third["open"]))
    fourth_body = abs(float(fourth["close"]) - float(fourth["open"]))

    if third_body == 0 or third_color == "Doji" or fourth_color == "Doji":
        return "No Prediction", "3rd candle has zero body or an input candle is Doji"

    body_ratio = (fourth_body / third_body) * 100

    if body_ratio < 50:
        predicted_color = "Red" if third_color == "Green" else "Green"
        reason = f"4th body is {body_ratio:.2f}% of 3rd body (<50%); opposite of 3rd candle"

    elif body_ratio > 50:
        predicted_color = third_color
        reason = f"4th body is {body_ratio:.2f}% of 3rd body (>50%); same as 3rd candle"

    else:
        return "No Prediction", "4th candle body is exactly 50% of 3rd candle body"

    return predicted_color, reason


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
            "Missing SmartAPI credentials. Set the environment "
            "variables SMARTAPI_API_KEY, SMARTAPI_CLIENT_CODE, "
            "SMARTAPI_PASSWORD, and SMARTAPI_TOTP_SECRET."
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
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))

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
    valid_counter = 0
    invalid_counter = 0
    doji_counter = 0
    incomplete_counter = 0
    no_prediction_counter = 0

    daily_results = []
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

        # ----------------------------------------------------
        # PARSE TIMESTAMPS
        # ----------------------------------------------------

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

        raw_df["trading_date"] = (
            raw_df["timestamp"].dt.date
        )

        # ----------------------------------------------------
        # SELECT THE FIRST FIVE CANDLES OF EACH DAY
        # ----------------------------------------------------

        # Typical five-minute candle timestamps:
        # 1st: 09:15
        # 2nd: 09:20
        # 3rd: 09:25
        # 4th: 09:30
        # 5th: 09:35

        df = add_candle_metrics(raw_df)

        df = df.sort_values(
            "timestamp"
        ).reset_index(drop=True)

        # ----------------------------------------------------
        # PROCESS EACH TRADING DAY
        # ----------------------------------------------------

        for trading_date, day_df in df.groupby("trading_date"):

            day_df = day_df.sort_values("timestamp")

            # Select the five candles by their timestamps.
            third_rows = day_df[
                day_df["candle_time"] == "09:25"
            ]

            fourth_rows = day_df[
                day_df["candle_time"] == "09:30"
            ]

            fifth_rows = day_df[
                day_df["candle_time"] == "09:35"
            ]

            # Skip the day if any required candle is missing.
            if (
                third_rows.empty
                or fourth_rows.empty
                or fifth_rows.empty
            ):
                incomplete_counter += 1

                third = (
                    third_rows.iloc[0]
                    if not third_rows.empty else None
                )

                fourth = (
                    fourth_rows.iloc[0]
                    if not fourth_rows.empty else None
                )

                fifth = (
                    fifth_rows.iloc[0]
                    if not fifth_rows.empty else None
                )

                daily_results.append({
                    "date": trading_date,
                    "result": "Incomplete",

                    "3rd_color": (
                        third["color"]
                        if third is not None else "Missing"
                    ),

                    "3rd_classification": (
                        third["classification"]
                        if third is not None else "Missing"
                    ),

                    "4th_color": (
                        fourth["color"]
                        if fourth is not None else "Missing"
                    ),

                    "4th_classification": (
                        fourth["classification"]
                        if fourth is not None else "Missing"
                    ),

                    "prediction_source": "N/A",
                    "predicted_5th_color": "N/A",

                    "5th_color": (
                        fifth["color"]
                        if fifth is not None else "Missing"
                    ),

                    "5th_classification": (
                        fifth["classification"]
                        if fifth is not None else "Missing"
                    ),

                    "3rd_open": (
                        third["open"] if third is not None else None
                    ),
                    "3rd_close": (
                        third["close"] if third is not None else None
                    ),

                    "4th_open": (
                        fourth["open"] if fourth is not None else None
                    ),
                    "4th_close": (
                        fourth["close"] if fourth is not None else None
                    ),

                    "5th_open": (
                        fifth["open"] if fifth is not None else None
                    ),
                    "5th_high": (
                        fifth["high"] if fifth is not None else None
                    ),
                    "5th_low": (
                        fifth["low"] if fifth is not None else None
                    ),
                    "5th_close": (
                        fifth["close"] if fifth is not None else None
                    ),
                })

                continue

            third = third_rows.iloc[0]
            fourth = fourth_rows.iloc[0]
            fifth = fifth_rows.iloc[0]

            # Predict the 5th candle using the 3rd and 4th.
            predicted_color, prediction_source = (
                predict_fifth_candle(third, fourth)
            )

            fifth_color = fifth["color"]

            # ------------------------------------------------
            # EVALUATE PREDICTION
            # ------------------------------------------------

            if fifth_color == "Doji":
                doji_counter += 1
                result = "Doji"

            elif predicted_color == "No Prediction":
                no_prediction_counter += 1
                result = "No Prediction"

            elif fifth_color == predicted_color:
                valid_counter += 1
                result = "Valid"

            else:
                invalid_counter += 1
                result = "Invalid"

            daily_results.append({
                "date": trading_date,
                "result": result,

                "3rd_color": third["color"],
                "3rd_classification": third["classification"],

                "4th_color": fourth["color"],
                "4th_classification": fourth["classification"],

                "prediction_source": prediction_source,
                "predicted_5th_color": predicted_color,

                "5th_color": fifth_color,
                "5th_classification": fifth["classification"],

                "3rd_open": third["open"],
                "3rd_high": third["high"],
                "3rd_low": third["low"],
                "3rd_close": third["close"],

                "4th_open": fourth["open"],
                "4th_high": fourth["high"],
                "4th_low": fourth["low"],
                "4th_close": fourth["close"],

                "5th_open": fifth["open"],
                "5th_high": fifth["high"],
                "5th_low": fifth["low"],
                "5th_close": fifth["close"],
            })

        # ----------------------------------------------------
        # DISPLAY DAILY RESULTS
        # ----------------------------------------------------

        results_df = pd.DataFrame(daily_results)

        if results_df.empty:
            print("No daily results to display.")
            return

        display_columns = [
            "date",
            "result",
            "3rd_color",
            "4th_color",
            "predicted_5th_color",
            "5th_color",
        ]

        print("\n========== DAILY PREDICTION SUMMARY ==========")

        print(
            results_df[display_columns].to_string(index=False)
        )

        # ----------------------------------------------------
        # SAVE CSV
        # ----------------------------------------------------

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
        print(f"Actual 5th-candle Doji days: {doji_counter}")
        print(f"Incomplete trading days: {incomplete_counter}")
        print(f"No-prediction days: {no_prediction_counter}")

        # ----------------------------------------------------
        # ACCURACY AND TOTAL POINTS
        # ----------------------------------------------------

        scored_total = valid_counter + invalid_counter

        if scored_total > 0:
            accuracy = valid_counter / scored_total * 100

            points_data = results_df[
                results_df["result"].isin(["Valid", "Invalid"])
            ].copy()

            # Actual open-to-close movement of the 5th candle.
            points_data["points"] = (
                points_data["5th_close"]
                - points_data["5th_open"]
            )

            # Green prediction: close - open.
            # Red prediction: open - close.
            points_data["signed_points"] = points_data.apply(
                lambda row: (
                    row["points"]
                    if row["predicted_5th_color"] == "Green"
                    else -row["points"]
                ),
                axis=1
            )

            total_points = points_data["signed_points"].sum()

            print(f"Prediction accuracy: {accuracy:.2f}%")
            print(f"Total points: {total_points:.2f}")

        else:
            print("Prediction accuracy: N/A")
            print("Total points: 0.00")

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
