import streamlit as st
import yfinance as yf
import pandas as pd
import time
import json
import os
from datetime import datetime, timedelta

# --- Configuration & Styling ---
st.set_page_config(page_title="Weekly Stock Surge Screener", page_icon="📈", layout="wide")

# Custom CSS for aesthetics
st.markdown("""
<style>
    .main {
        background-color: #0E1117;
    }
    .stButton>button {
        background-color: #00C805;
        color: white;
        border-radius: 8px;
        font-weight: bold;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        background-color: #00A004;
        transform: scale(1.02);
    }
    h1 {
        color: #00C805;
        font-family: 'Inter', sans-serif;
    }
    .metric-card {
        background: #1E2329;
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
</style>
""", unsafe_allow_html=True)

CACHE_FILE = "cache.json"
CACHE_EXPIRY_HOURS = 24

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_cache(cache_data):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache_data, f)

def parse_tickers(text_input, uploaded_file):
    tickers = set()
    if text_input:
        for t in text_input.replace(',', ' ').split():
            t = t.strip().upper()
            if t: tickers.add(t)
    
    if uploaded_file is not None:
        try:
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file)
                # Try to find a column named ticker or symbol
                col = next((c for c in df.columns if c.lower() in ['ticker', 'symbol']), None)
                if col:
                    tickers.update(df[col].dropna().astype(str).str.upper().tolist())
                else:
                    # Assume first column
                    tickers.update(df.iloc[:, 0].dropna().astype(str).str.upper().tolist())
            elif uploaded_file.name.endswith('.json'):
                data = json.load(uploaded_file)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, str):
                            tickers.add(item.strip().upper())
                        elif isinstance(item, dict) and ('ticker' in item or 'symbol' in item):
                            key = 'ticker' if 'ticker' in item else 'symbol'
                            tickers.add(str(item[key]).strip().upper())
                elif isinstance(data, dict):
                    # Assume keys are tickers, or there's a list inside
                    for key, val in data.items():
                        if isinstance(val, list):
                            for item in val:
                                if isinstance(item, str):
                                    tickers.add(item.strip().upper())
                        else:
                            tickers.add(str(key).strip().upper())
            else:
                content = uploaded_file.read().decode('utf-8')
                for t in content.replace(',', ' ').split():
                    t = t.strip().upper()
                    if t: tickers.add(t)
        except Exception as e:
            st.error(f"Error parsing file: {e}")
    
    return list(tickers)

def scan_tickers_generator(tickers, lookback, force_refresh):
    """
    Generator that processes tickers in batches.
    Yields (current_batch_index, total_batches, batch_results, batch_failed).
    """
    cache = load_cache()
    now = datetime.now()
    
    to_fetch = []
    results = []
    failed = []
    
    # Check cache
    for ticker in tickers:
        if not force_refresh and ticker in cache:
            cached_time = datetime.fromisoformat(cache[ticker]['timestamp'])
            if now - cached_time < timedelta(hours=CACHE_EXPIRY_HOURS) and 'current_open' in cache[ticker]:
                results.append(cache[ticker])
                continue
        to_fetch.append(ticker)
        
    # Yield cached results in batches so the UI updates live
    if results:
        total_cached_batches = (len(results) + 49) // 50
        for i in range(0, len(results), 50):
            yield (i + 1, total_cached_batches + (len(to_fetch) // 50), results[i:i+50], [])

    if not to_fetch:
        return

    # Batch fetching
    batch_size = 50
    total_batches = (len(to_fetch) + batch_size - 1) // batch_size
    
    for i in range(0, len(to_fetch), batch_size):
        batch = to_fetch[i:i+batch_size]
        batch_results = []
        batch_failed = []
        
        try:
            # period="1mo" ensures we have enough trading days
            data = yf.download(batch, period="1mo", progress=False)
            
            close_prices = data['Close']
            open_prices = data['Open']
            volumes = data['Volume']
            if isinstance(close_prices, pd.Series):
                close_prices = close_prices.to_frame(name=batch[0])
                open_prices = open_prices.to_frame(name=batch[0])
                volumes = volumes.to_frame(name=batch[0])
                
            for ticker in batch:
                if ticker in close_prices.columns and ticker in open_prices.columns:
                    series = close_prices[ticker].dropna()
                    series_open = open_prices[ticker].dropna()
                    vol_series = volumes[ticker].dropna()
                    
                    if len(series) >= lookback + 1 and len(series_open) >= 1:
                        current = float(series.iloc[-1])
                        current_open = float(series_open.iloc[-1])
                        past = float(series.iloc[-1 - lookback])
                        vol = float(vol_series.iloc[-1])
                        
                        if current <= 0.1 or current_open <= 0.1:
                            batch_failed.append({"ticker": ticker, "reason": "Penny Stock (Price <= $0.10)"})
                        elif past <= 0:
                            batch_failed.append({"ticker": ticker, "reason": "Past price is zero or invalid"})
                        else:
                            ratio = current / past
                            pct_change = (ratio - 1) * 100
                            intraday_change = (current - current_open) / current_open * 100 if current_open > 0 else 0
                            
                            ticker_data = {
                                "ticker": ticker,
                                "current_price": round(current, 2),
                                "current_open": round(current_open, 2),
                                "week_ago_price": round(past, 2),
                                "ratio": round(ratio, 2),
                                "pct_change": round(pct_change, 2),
                                "intraday_change": round(intraday_change, 2),
                                "volume": vol,
                                "timestamp": now.isoformat()
                            }
                            cache[ticker] = ticker_data
                            batch_results.append(ticker_data)
                    else:
                        batch_failed.append({"ticker": ticker, "reason": "Insufficient history (Needs at least lookback window)"})
                else:
                    batch_failed.append({"ticker": ticker, "reason": "No data returned (likely delisted or invalid)"})
                    
        except Exception as e:
            for t in batch:
                batch_failed.append({"ticker": t, "reason": f"YFinance API Error: {str(e)}"})
            
        save_cache(cache)
        yield (i // batch_size + 1, total_batches, batch_results, batch_failed)
        time.sleep(0.5) # Rate limiting


def main():
    st.title("📈 Weekly Stock Surge Screener")
    st.markdown("Identify stocks that have surged over a specific timeframe.")
    
    with st.sidebar:
        st.header("⚙️ Configuration")
        threshold = st.slider("Surge Threshold (Ratio)", min_value=1.1, max_value=3.0, value=1.5, step=0.1, help="1.5x means 50%+ gain")
        lookback = st.number_input("Lookback Window (Trading Days)", min_value=1, max_value=20, value=5)
        force_refresh = st.checkbox("Force Refresh Data", value=False)
        
        st.markdown("---")
        st.header("📥 Input Tickers")
        text_input = st.text_area("Paste comma-separated tickers", "AAPL, MSFT, TSLA, NVDA, GME, AMC")
        uploaded_file = st.file_uploader("Or upload a CSV/TXT/JSON file", type=['csv', 'txt', 'json'])
        
        start_scan = st.button("🚀 Start Scan", use_container_width=True)

    if start_scan:
        tickers = parse_tickers(text_input, uploaded_file)
        if not tickers:
            st.warning("Please provide at least one valid ticker symbol.")
            return
            
        # Initialize state lists
        all_results = []
        all_failed = []
        surged_data = [] # Stores rows that meet threshold and have Market Cap populated

        # Create Placeholders for incremental UI
        st.markdown(f"**Processing {len(tickers)} tickers...**")
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        col1, col2, col3 = st.columns(3)
        with col1:
            metric_scanned = st.empty()
        with col2:
            metric_surged = st.empty()
        with col3:
            metric_failed = st.empty()
            
        st.markdown("<br>", unsafe_allow_html=True)
        
        st.subheader("📋 Surge Results (Live)")
        table_placeholder = st.empty()
        
        st.subheader("⚠️ Recent Failures (Live)")
        failed_placeholder = st.empty()
        
        # Generator Loop
        for batch_idx, total_batches, batch_res, batch_fail in scan_tickers_generator(tickers, lookback, force_refresh):
            all_results.extend(batch_res)
            all_failed.extend(batch_fail)
            
            # Print failures to the terminal console
            for f in batch_fail:
                print(f"Failed [{f['ticker']}]: {f['reason']}")
            
            # Update Progress
            if total_batches > 0:
                progress = min(1.0, batch_idx / total_batches)
                progress_bar.progress(progress)
                status_text.text(f"Scanning batch {batch_idx}/{total_batches}...")
            
            # Identify newly surged stocks in this batch
            new_surged = [r for r in batch_res if r['pct_change'] >= (threshold - 1) * 100]
            
            # Add new surged stocks immediately to display
            if new_surged:
                surged_data.extend(new_surged)
                
                # LIVE SUB-BATCH UPDATE
                metric_surged.markdown(f"<div class='metric-card'><h3>🚀 Surged (>{threshold}x)</h3><h2>{len(surged_data)}</h2></div>", unsafe_allow_html=True)
                df = pd.DataFrame(surged_data)
                df = df.sort_values(by='pct_change', ascending=False)
                display_df = df[['ticker', 'current_open', 'current_price', 'week_ago_price', 'intraday_change', 'pct_change', 'ratio']]
                display_df.columns = ['Ticker', 'Open Price ($)', 'Close Price ($)', f'Price {lookback} Days Ago ($)', 'Intraday Change %', 'Lookback Change %', 'Ratio']
                table_placeholder.dataframe(display_df, use_container_width=True)
            
            # Update Metrics (Total Scanned and Failed)
            metric_scanned.markdown(f"<div class='metric-card'><h3>📊 Total Scanned</h3><h2>{len(all_results)}</h2></div>", unsafe_allow_html=True)
            metric_failed.markdown(f"<div class='metric-card'><h3>⚠️ Failed/Invalid</h3><h2>{len(all_failed)}</h2></div>", unsafe_allow_html=True)
            
            if not surged_data:
                table_placeholder.info(f"No stocks found meeting the {threshold}x threshold yet.")
                
            if all_failed:
                failed_df = pd.DataFrame(all_failed)
                # Show the most recent failures (up to 500) to keep the UI responsive
                failed_placeholder.dataframe(failed_df.tail(500), use_container_width=True)
            else:
                failed_placeholder.info("No failures yet.")

        # Finalize
        progress_bar.empty()
        status_text.success("Scan Complete!")
        
        if all_failed:
            st.markdown("---")
            st.subheader("Detailed Failure Logs")
            failed_df = pd.DataFrame(all_failed)
            st.dataframe(failed_df, use_container_width=True)
            failed_csv = failed_df.to_csv(index=False).encode('utf-8')
            st.download_button("⬇️ Download Failure Logs CSV", failed_csv, "failed_tickers.csv", "text/csv")
            
        if surged_data:
            st.markdown("---")
            final_df = pd.DataFrame(surged_data)
            final_df = final_df.sort_values(by='pct_change', ascending=False)
            display_df = final_df[['ticker', 'current_open', 'current_price', 'week_ago_price', 'intraday_change', 'pct_change', 'ratio']]
            display_df.columns = ['Ticker', 'Open Price ($)', 'Close Price ($)', f'Price {lookback} Days Ago ($)', 'Intraday Change %', 'Lookback Change %', 'Ratio']
            csv = display_df.to_csv(index=False).encode('utf-8')
            st.download_button("⬇️ Download Surge Results CSV", csv, "surge_results.csv", "text/csv")

if __name__ == "__main__":
    main()
