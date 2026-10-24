
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

# Change the year you want to analyze.
# The current year is automatically capped at today's date/time.
SELECTED_YEAR = 2026

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

OUTPUT_CSV = f"nifty_4th_candle_daily_{SELECTED_YEAR}.csv"
MONTHLY_CSV = f"nifty_4th_candle_monthly_{SELECTED_YEAR}.csv"
RULES_CSV = f"nifty_4th_candle_rules_{SELECTED_YEAR}.csv"
FETCH_STATUS_CSV = f"nifty_fetch_status_{SELECTED_YEAR}.csv"

FIRST_THREE_TIMES = ["09:15", "09:20", "09:25"]
FOURTH_CANDLE_TIME = "09:30"

CANDLE_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume"
]


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
# THREE-CANDLE PREDICTION RULES
# ============================================================

RULES = {
    ("Red", "Red", "Green"): "Red",
    ("Green", "Green", "Red"): "Red",
    ("Red", "Green", "Red"): "Red",
    ("Green", "Red", "Green"): "Green",
    ("Red", "Red", "Red"): "Red",
    ("Green", "Green", "Green"): "Red",
}


def predict_fourth_candle(first, second, third):
    return RULES.get(
        (first, second, third),
        "No Prediction"
    )


def describe_rule(first, second, third, predicted):
    pattern = f"{first} -> {second} -> {third}"

    if predicted == "No Prediction":
        return f"{pattern} = No Rule"

    return f"{pattern} = {predicted}"


# ============================================================
# SMARTAPI LOGIN
# ============================================================

def login_to_smartapi():

    if not all([
        API_KEY, CLIENT_CODE, PASSWORD, TOTP_SECRET
    ]):
        raise ValueError(
            "Missing credentials. Set these environment variables:\n"
            "SMARTAPI_API_KEY\n"
            "SMARTAPI_CLIENT_CODE\n"
            "SMARTAPI_PASSWORD\n"
            "SMARTAPI_TOTP_SECRET"
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
# TIMESTAMP CONVERSION
# ============================================================

def convert_to_ist(value):

    timestamp = pd.Timestamp(value)

    if timestamp.tzinfo is None:
        return timestamp.tz_localize(IST)

    return timestamp.tz_convert(IST)


# ============================================================
# FETCH ONE DATE RANGE WITH RETRIES
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
                data = response["data"]

                if not data:
                    return pd.DataFrame(columns=CANDLE_COLUMNS)

                return pd.DataFrame(
                    data,
                    columns=CANDLE_COLUMNS
                )

            last_error = RuntimeError(
                f"Historical API response: {response}"
            )

        except Exception as exc:
            last_error = exc

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))

            print(
                f"  Attempt {attempt}/{MAX_RETRIES} failed: "
                f"{last_error}"
            )
            print(f"  Retrying in {delay} seconds...")

            time_module.sleep(delay)

    raise RuntimeError(
        f"Failed after {MAX_RETRIES} attempts: {last_error}"
    )


# ============================================================
# FETCH SELECTED YEAR MONTH BY MONTH
# SKIP FUTURE MONTHS AND FUTURE DATES
# ============================================================

def fetch_year_historical_data(smart_api, year):

    all_data = []
    fetch_status = []

    now_ist = datetime.now(IST)
    today = now_ist.date()

    print("\n" + "=" * 85)
    print(f"FETCHING HISTORICAL DATA FOR {year}")
    print(f"Current IST date/time: {now_ist}")
    print("=" * 85)

    # A completely future year cannot be fetched.
    if year > today.year:
        print(
            f"Year {year} is in the future. "
            f"Current year is {today.year}; no requests made."
        )

        for month in range(1, 13):
            fetch_status.append({
                "month": month,
                "month_name": calendar.month_name[month],
                "fetch_status": "FUTURE - SKIPPED",
                "candles_received": 0,
                "error": "",
            })

        status_df = pd.DataFrame(fetch_status)
        status_df.to_csv(FETCH_STATUS_CSV, index=False)

        return pd.DataFrame(columns=CANDLE_COLUMNS), status_df

    # Historical years: all months.
    # Current year: January through the current month.
    last_month = 12 if year < today.year else today.month

    for month in range(1, 13):

        month_name = calendar.month_name[month]

        # Skip months after the current month.
        if month > last_month:
            print(f"\nSkipping {month_name} {year}: future month.")

            fetch_status.append({
                "month": month,
                "month_name": month_name,
                "fetch_status": "FUTURE - SKIPPED",
                "candles_received": 0,
                "error": "",
            })
            continue

        last_day = calendar.monthrange(year, month)[1]

        start_dt = datetime(
            year, month, 1, 9, 0, tzinfo=IST
        )

        end_dt = datetime(
            year, month, last_day, 15, 30, tzinfo=IST
        )

        # Cap the end of the current month at the current time.
        if year == today.year and month == today.month:
            end_dt = min(end_dt, now_ist)

        # If the month has not started, do not fetch it.
        if start_dt > now_ist:
            print(f"\nSkipping {month_name} {year}: future period.")

            fetch_status.append({
                "month": month,
                "month_name": month_name,
                "fetch_status": "FUTURE - SKIPPED",
                "candles_received": 0,
                "error": "",
            })
            continue

        print(f"\nFetching {month_name} {year}...")
        print(f"  From: {start_dt}")
        print(f"  To  : {end_dt}")

        try:
            month_df = fetch_historical_data(
                smart_api, start_dt, end_dt
            )

            if month_df.empty:
                print("  WARNING: API returned zero candles.")

                fetch_status.append({
                    "month": month,
                    "month_name": month_name,
                    "fetch_status": "NO DATA",
                    "candles_received": 0,
                    "error": "",
                })
                continue

            month_df["timestamp"] = pd.to_datetime(
                month_df["timestamp"], errors="coerce"
            )

            month_df = month_df.dropna(
                subset=["timestamp"]
            ).copy()

            month_df["timestamp"] = month_df[
                "timestamp"
            ].apply(convert_to_ist)

            # Keep only candles belonging to this month and
            # within the requested cutoff.
            month_df = month_df[
                (month_df["timestamp"].dt.year == year)
                & (month_df["timestamp"].dt.month == month)
                & (month_df["timestamp"] <= end_dt)
            ].copy()

            if month_df.empty:
                print("  No candles remained after date filtering.")

                fetch_status.append({
                    "month": month,
                    "month_name": month_name,
                    "fetch_status": "NO DATA",
                    "candles_received": 0,
                    "error": "",
                })
                continue

            count = len(month_df)
            print(f"  Candles received: {count}")

            all_data.append(month_df)

            fetch_status.append({
                "month": month,
                "month_name": month_name,
                "fetch_status": "SUCCESS",
                "candles_received": count,
                "error": "",
            })

        except Exception as exc:
            error_text = str(exc)

            print(f"  ERROR: {error_text}")

            fetch_status.append({
                "month": month,
                "month_name": month_name,
                "fetch_status": "FAILED",
                "candles_received": 0,
                "error": error_text,
            })

    status_df = pd.DataFrame(fetch_status).sort_values(
        "month"
    ).reset_index(drop=True)

    status_df.to_csv(FETCH_STATUS_CSV, index=False)

    print("\n" + "=" * 85)
    print("HISTORICAL DATA FETCH STATUS")
    print("=" * 85)
    print(status_df.to_string(index=False))

    print(
        f"\nFetch status saved to: "
        f"{os.path.abspath(FETCH_STATUS_CSV)}"
    )

    if not all_data:
        return pd.DataFrame(columns=CANDLE_COLUMNS), status_df

    combined_df = pd.concat(
        all_data, ignore_index=True
    )

    combined_df = combined_df.drop_duplicates(
        subset=["timestamp"], keep="first"
    )

    combined_df = combined_df.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    combined_df["timestamp"] = combined_df[
        "timestamp"
    ].apply(convert_to_ist)

    # Final safeguard against future candles.
    combined_df = combined_df[
        combined_df["timestamp"] <= now_ist
    ].copy()

    print("\nCombined historical data:")
    print(f"Total candles: {len(combined_df)}")

    if not combined_df.empty:
        print(f"First candle: {combined_df['timestamp'].min()}")
        print(f"Last candle : {combined_df['timestamp'].max()}")

    return combined_df, status_df


# ============================================================
# ANALYZE ONE TRADING DAY
# ============================================================

def analyze_day(trading_date, day_df):

    day_df = day_df.sort_values("timestamp").copy()

    first_three = (
        day_df[
            day_df["candle_time"].isin(FIRST_THREE_TIMES)
        ]
        .drop_duplicates(
            subset=["candle_time"], keep="first"
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
        "month": trading_date.month,
        "candle_1_color": color1,
        "candle_2_color": color2,
        "candle_3_color": color3,
        "rule_applied": "Incomplete: missing first three candles",
        "predicted_4th_color": "No Prediction",
        "actual_4th_color": "Missing",
        "result": "Incomplete",
        "4th_open": None,
        "4th_high": None,
        "4th_low": None,
        "4th_close": None,
        "points": None,
    }

    if "Missing" in colors:
        return result_row

    predicted = predict_fourth_candle(
        color1, color2, color3
    )

    result_row["predicted_4th_color"] = predicted
    result_row["rule_applied"] = describe_rule(
        color1, color2, color3, predicted
    )

    fourth_rows = day_df[
        day_df["candle_time"] == FOURTH_CANDLE_TIME
    ]

    if fourth_rows.empty:
        result_row["result"] = "Missing Fourth Candle"
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

    if predicted == "No Prediction":
        result_row["result"] = "No Prediction"
        return result_row

    # Exclude doji candles from accuracy and points.
    if actual == "Doji":
        result_row["result"] = "Doji"
        result_row["points"] = 0.0
        return result_row

    candle_move = float(fourth["close"] - fourth["open"])

    if predicted == "Green":
        points = candle_move
    else:
        points = -candle_move

    result_row["points"] = points

    result_row["result"] = (
        "Correct" if predicted == actual else "Incorrect"
    )

    return result_row


# ============================================================
# BUILD MONTHLY SUMMARY
# ============================================================

def build_monthly_summary(results_df, fetch_status_df, year):

    monthly_rows = []

    for month in range(1, 13):

        month_df = results_df[
            results_df["month"] == month
        ]

        scored = month_df[
            month_df["result"].isin(["Correct", "Incorrect"])
        ]

        correct_count = int(
            (scored["result"] == "Correct").sum()
        )

        incorrect_count = int(
            (scored["result"] == "Incorrect").sum()
        )

        scored_total = correct_count + incorrect_count

        accuracy = (
            correct_count / scored_total * 100
            if scored_total else 0.0
        )

        total_points = float(
            scored["points"].fillna(0).sum()
        )

        status_row = fetch_status_df[
            fetch_status_df["month"] == month
        ]

        if not status_row.empty:
            status = status_row.iloc[0]["fetch_status"]
            candles_received = int(
                status_row.iloc[0]["candles_received"]
            )
        else:
            status = "UNKNOWN"
            candles_received = 0

        monthly_rows.append({
            "year": year,
            "month": month,
            "month_name": calendar.month_name[month],
            "fetch_status": status,
            "candles_received": candles_received,
            "trading_days_analyzed": len(month_df),
            "correct": correct_count,
            "incorrect": incorrect_count,
            "scored_predictions": scored_total,
            "accuracy_percent": round(accuracy, 2),
            "total_points": round(total_points, 2),
            "doji": int(
                (month_df["result"] == "Doji").sum()
            ),
            "incomplete": int(
                (month_df["result"] == "Incomplete").sum()
            ),
            "missing_fourth_candle": int(
                (month_df["result"] == "Missing Fourth Candle").sum()
            ),
            "no_prediction": int(
                (month_df["result"] == "No Prediction").sum()
            ),
        })

    return pd.DataFrame(monthly_rows)


# ============================================================
# MAIN
# ============================================================

def main():

    smart_api = None

    try:
        print(
            f"\nAnalyzing NIFTY 50 for selected year "
            f"{SELECTED_YEAR}"
        )

        smart_api = login_to_smartapi()
        print("SmartAPI login successful.")

        raw_df, fetch_status_df = fetch_year_historical_data(
            smart_api, SELECTED_YEAR
        )

        if raw_df.empty:
            print(
                "\nNo historical candles retrieved. "
                "Check credentials, API permissions, and data availability."
            )
            return

        raw_df["timestamp"] = raw_df[
            "timestamp"
        ].apply(convert_to_ist)

        df = add_candle_metrics(raw_df)

        df = df.sort_values("timestamp").copy()

        # Restrict to the requested year and current cutoff.
        now_ist = datetime.now(IST)

        df = df[
            (df["timestamp"].dt.year == SELECTED_YEAR)
            & (df["timestamp"] <= now_ist)
        ].copy()

        df["candle_time"] = df["timestamp"].dt.strftime("%H:%M")
        df["trading_date"] = df["timestamp"].dt.date

        daily_results = []

        for trading_date, day_df in df.groupby("trading_date"):
            daily_results.append(
                analyze_day(trading_date, day_df)
            )

        results_df = pd.DataFrame(daily_results)

        if results_df.empty:
            print("No daily analysis results available.")
            return

        results_df["date"] = pd.to_datetime(results_df["date"])

        results_df = results_df.sort_values(
            "date"
        ).reset_index(drop=True)

        # ----------------------------------------------------
        # SAVE DAILY RESULTS
        # ----------------------------------------------------

        results_df.to_csv(OUTPUT_CSV, index=False)

        # ----------------------------------------------------
        # PRINT EVERY DATE AND RULE
        # ----------------------------------------------------

        rule_columns = [
            "date",
            "candle_1_color",
            "candle_2_color",
            "candle_3_color",
            "rule_applied",
            "predicted_4th_color",
            "actual_4th_color",
            "result",
        ]

        results_df[rule_columns].to_csv(
            RULES_CSV, index=False
        )

        # ----------------------------------------------------
        # MONTHLY SUMMARY
        # ----------------------------------------------------

        monthly_df = build_monthly_summary(
            results_df,
            fetch_status_df,
            SELECTED_YEAR
        )

        monthly_df.to_csv(MONTHLY_CSV, index=False)

        print("\n" + "=" * 150)
        print(f"MONTHLY ACCURACY AND POINTS - {SELECTED_YEAR}")
        print("=" * 150)

        monthly_columns = [
            "month_name",
            "candles_received",
            "trading_days_analyzed",
            "correct",
            "incorrect",
            "total_points",
        ]

        print(
            monthly_df[monthly_columns].to_string(index=False)
        )

        # ----------------------------------------------------
        # YEARLY SUMMARY
        # ----------------------------------------------------

        scored = results_df[
            results_df["result"].isin(["Correct", "Incorrect"])
        ]

        correct_count = int(
            (scored["result"] == "Correct").sum()
        )

        incorrect_count = int(
            (scored["result"] == "Incorrect").sum()
        )

        scored_total = correct_count + incorrect_count

        yearly_accuracy = (
            correct_count / scored_total * 100
            if scored_total else 0.0
        )

        yearly_points = float(
            scored["points"].fillna(0).sum()
        )

        print("\n" + "=" * 80)
        print(f"YEARLY SUMMARY - {SELECTED_YEAR}")
        print("=" * 80)
        print(f"Correct predictions      : {correct_count}")
        print(f"Incorrect predictions    : {incorrect_count}")
        print(f"Scored predictions       : {scored_total}")
        print(f"Yearly accuracy          : {yearly_accuracy:.2f}%")
        print(f"Total directional points : {yearly_points:.2f}")
        print(f"Trading dates analyzed   : {len(results_df)}")

        # ----------------------------------------------------
        # WARN ABOUT MISSING DATA
        # ----------------------------------------------------

        failed_months = monthly_df[
            monthly_df["fetch_status"].isin(["FAILED", "NO DATA"])
        ]

        if not failed_months.empty:
            print("\nWARNING: Some months have missing historical data:")
            print(
                failed_months[
                    ["month_name", "fetch_status", "candles_received"]
                ].to_string(index=False)
            )
            print(
                "The yearly accuracy is not necessarily a complete "
                "full-year result until missing data is resolved."
            )

        # ----------------------------------------------------
        # OUTPUT FILES
        # ----------------------------------------------------

        print("\n" + "=" * 80)
        print("OUTPUT FILES")
        print("=" * 80)
        print(f"Daily results : {os.path.abspath(OUTPUT_CSV)}")
        print(f"Monthly report: {os.path.abspath(MONTHLY_CSV)}")
        print(f"Rules report  : {os.path.abspath(RULES_CSV)}")
        print(f"Fetch status  : {os.path.abspath(FETCH_STATUS_CSV)}")

    except Exception as exc:
        print(f"\nERROR: {exc}")

    finally:
        if smart_api is not None:
            try:
                smart_api.terminateSession(CLIENT_CODE)
                print("\nSmartAPI session terminated.")
            except Exception as exc:
                print(f"Could not terminate session: {exc}")


if __name__ == "__main__":
    main()
