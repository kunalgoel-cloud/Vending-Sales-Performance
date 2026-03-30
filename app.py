import streamlit as st
import pandas as pd
import numpy as np
import datetime
import json
import os
import plotly.express as px
import plotly.graph_objects as go

# --- APP CONFIG ---
st.set_page_config(page_title="Vending Performance Hub", layout="wide", page_icon="📊")

DB_PATH = "vending_database.json"

# --- SESSION STATE ---
for key, val in {
    'analysis_generated': False,
    'processed_df': None,
    'price_map': {},
    'db': None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = val

# --- DATABASE (JSON-based persistence) ---
def load_db():
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "r") as f:
            return json.load(f)
    return {}

def save_db(db):
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=2)

def db_key(customer, month, year):
    return f"{customer}__{month}__{year}"

# --- NORMALIZATION HELPERS ---
CITY_ALIASES = {
    "gurgaon": "Gurgaon",
    "cochin airport": "Cochin Airport",
    "goa airport": "Goa Airport",
    "hyderabad airport": "Hyderabad Airport",
    "mumbai airport": "Mumbai Airport",
    "new delhi": "New Delhi",
    "bangalore": "Bangalore",
    "chandigarh": "Chandigarh",
    "chennai": "Chennai",
    "cochin": "Cochin",
    "dolvi": "Dolvi",
    "gurgaon": "Gurgaon",
    "hyderabad": "Hyderabad",
    "mumbai": "Mumbai",
    "noida": "Noida",
    "pune": "Pune",
}

def normalize_city(city_str):
    """Normalize city name to canonical form."""
    return CITY_ALIASES.get(str(city_str).strip().lower(), str(city_str).strip())

def normalize_product(prod_str):
    """
    Strips leading 'Airport ' prefix caused by data entry errors
    where city and product were accidentally merged in source data.
    e.g. 'Airport Coconut Laddubar...' -> 'Coconut Laddubar...'
    """
    p = str(prod_str).strip()
    if p.lower().startswith("airport "):
        p = p[8:]
    return p

def extract_city_from_location(loc_str, known_cities):
    """
    Match a warehouse/zone location string to the best-known city.
    Uses longest-match to prioritise 'Hyderabad Airport' over 'Hyderabad'.
    Falls back to the raw (title-cased) first token.
    """
    loc_l = str(loc_str).lower()
    for city in sorted(known_cities, key=len, reverse=True):
        if city.lower() in loc_l:
            return city
    # If no match, extract first word as fallback and normalise
    fallback = str(loc_str).strip().split()[0].title()
    return normalize_city(fallback)

# --- CORE PROCESSING ---
def process_master_file(uploaded_file):
    try:
        all_sheets = pd.read_excel(uploaded_file, sheet_name=None)
        s_map = {k.lower().strip(): k for k in all_sheets.keys()}

        req = ['sales summary', 'soh', 'machine placement']
        missing = [r for r in req if r not in s_map]
        if missing:
            st.error(f"Missing required sheets: {missing}")
            return None

        # ── SALES SUMMARY ──────────────────────────────────────────────
        # Row 0 is a header row; actual data starts at row 1 (iloc offset)
        # Columns: City(0), Product(1), Qty(2)
        sales_raw = all_sheets[s_map['sales summary']].iloc[1:, 0:3].copy()
        sales_raw.columns = ['City', 'Product', 'Sales_Qty']
        # Drop totals / blank rows
        sales_raw = sales_raw[
            sales_raw['City'].notna() &
            ~sales_raw['City'].astype(str).str.lower().str.contains('total', na=False)
        ].copy()
        sales_raw['City'] = sales_raw['City'].apply(normalize_city)
        # Fix "Airport <product>" data-entry errors (e.g. row 4 in sample)
        sales_raw['Product'] = sales_raw['Product'].apply(normalize_product)
        sales_raw['Sales_Qty'] = pd.to_numeric(sales_raw['Sales_Qty'], errors='coerce').fillna(0)
        # Aggregate in case normalisation created duplicates
        sales = sales_raw.groupby(['City', 'Product'])['Sales_Qty'].sum().reset_index()

        # ── MACHINE PLACEMENT ──────────────────────────────────────────
        mach_raw = all_sheets[s_map['machine placement']].iloc[1:, 0:3].copy()
        mach_raw.columns = ['City', 'Product', 'Machine_Count']
        mach_raw = mach_raw[
            mach_raw['City'].notna() &
            ~mach_raw['City'].astype(str).str.lower().str.contains('total', na=False)
        ].copy()
        # FIX: normalise case (e.g. 'gurgaon' -> 'Gurgaon')
        mach_raw['City'] = mach_raw['City'].apply(normalize_city)
        mach_raw['Product'] = mach_raw['Product'].apply(normalize_product)
        mach_raw['Machine_Count'] = pd.to_numeric(mach_raw['Machine_Count'], errors='coerce').fillna(0)
        mach = mach_raw.groupby(['City', 'Product'])['Machine_Count'].sum().reset_index()

        # ── SOH ────────────────────────────────────────────────────────
        # Columns: Location(0), Product(1), MRP(2), StockValue(3), TotalStock(4)
        soh_raw = all_sheets[s_map['soh']].iloc[1:, [0, 1, 4]].copy()
        soh_raw.columns = ['Loc', 'Product', 'Total_SOH']
        soh_raw = soh_raw[
            soh_raw['Loc'].notna() &
            ~soh_raw['Loc'].astype(str).str.lower().str.contains('total', na=False)
        ].copy()
        soh_raw['Product'] = soh_raw['Product'].apply(normalize_product)
        soh_raw['Total_SOH'] = pd.to_numeric(soh_raw['Total_SOH'], errors='coerce').fillna(0)

        # Build canonical city list from sales + machine sheets for matching
        known_cities = list(set(sales['City'].tolist() + mach['City'].tolist()))

        # FIX: use longest-match city extraction instead of first-word split
        soh_raw['City'] = soh_raw['Loc'].apply(
            lambda x: extract_city_from_location(x, known_cities)
        )
        # "Goa Zone 1" doesn't appear in sales/mach → map SOH to Goa Airport
        # Also any standalone 'Goa' fallback should go to Goa Airport since all Goa activity is at Airport
        soh_raw['City'] = soh_raw['City'].replace({'Goa Zone 1(Mw)': 'Goa Airport', 'Goa Zone 1(MW)': 'Goa Airport', 'Goa': 'Goa Airport'})
        # Aggregate all zones / warehouses per city+product
        soh = soh_raw.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

        # ── MERGE ─────────────────────────────────────────────────────
        # Use OUTER merge so every city/product combo from ALL three sheets
        # is preserved — a left join on machine placement would silently drop
        # SOH rows for products not listed in that sheet (e.g. Chandigarh
        # has Patal Poha in SOH but NOT in Machine Placement).
        df = pd.merge(mach, sales, on=['City', 'Product'], how='outer')
        df = pd.merge(df,   soh,   on=['City', 'Product'], how='outer')

        for c in ['Sales_Qty', 'Total_SOH', 'Machine_Count']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

        # ── METRICS ───────────────────────────────────────────────────
        days = 30
        df['drr'] = df['Sales_Qty'] / days
        df['velocity'] = np.where(
            df['Machine_Count'] > 0,
            (df['Sales_Qty'] / df['Machine_Count']) / days,
            0
        )
        df['str_pct'] = np.where(
            (df['Sales_Qty'] + df['Total_SOH']) > 0,
            df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100,
            0
        )
        df['days_of_cover'] = np.where(df['drr'] > 0, df['Total_SOH'] / df['drr'], 999)

        df['rank'] = df['velocity'].rank(pct=True)
        df['abc_class'] = np.where(df['rank'] > 0.8, 'A',
                          np.where(df['rank'] > 0.5, 'B', 'C'))
        df = df.drop(columns=['rank'])

        return df

    except Exception as e:
        st.error(f"Processing Error: {e}")
        import traceback; st.code(traceback.format_exc())
        return None

# ── SIDEBAR ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📊 Vending Hub")
    st.markdown("---")

    if st.button("🗑 Reset & Upload New File", use_container_width=True):
        st.session_state.analysis_generated = False
        st.session_state.processed_df = None
        st.rerun()

    # Saved analyses
    db = load_db()
    st.session_state.db = db
    if db:
        st.markdown("### 📁 Saved Analyses")
        keys = list(db.keys())
        labels = [k.replace("__", " | ") for k in keys]
        chosen_label = st.selectbox("Open saved analysis", ["— select —"] + labels)
        if chosen_label and chosen_label != "— select —":
            chosen_key = keys[labels.index(chosen_label)]
            if st.button("📂 Load", use_container_width=True):
                saved = db[chosen_key]
                st.session_state.processed_df = pd.DataFrame(saved['data'])
                st.session_state.price_map = saved['price_map']
                st.session_state.analysis_generated = True
                st.rerun()

# ── MAIN UI ────────────────────────────────────────────────────────────────────
st.title("📊 Vending Performance Hub")

col_c, col_m, col_y = st.columns([2, 1, 1])
target_cust = col_c.selectbox("Select Customer", ["Vendiman", "External Partner"])
months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
sel_month = col_m.selectbox("Month", months, index=datetime.datetime.now().month - 1)
sel_year = col_y.selectbox("Year", range(2024, 2032), index=datetime.datetime.now().year - 2024)

file = st.file_uploader("Step 1: Upload Excel Workbook", type="xlsx")

if file:
    if st.session_state.processed_df is None:
        with st.spinner("Processing file..."):
            st.session_state.processed_df = process_master_file(file)

    if st.session_state.processed_df is not None:
        st.markdown("---")
        st.subheader("💰 Step 2: Item-Wise Price Entry")
        st.info("Set the MRP for each product. Used for value-based metrics.")

        unique_prods = sorted(st.session_state.processed_df['Product'].unique())
        price_init = pd.DataFrame({"Product": unique_prods, "Price_per_Unit": 0.0})
        edited_prices = st.data_editor(
            price_init, use_container_width=True, hide_index=True, key="price_editor"
        )

        if st.button("🚀 Generate Performance Analysis", type="primary"):
            st.session_state.analysis_generated = True
            st.session_state.price_map = dict(
                zip(edited_prices['Product'], edited_prices['Price_per_Unit'])
            )

# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.analysis_generated and st.session_state.processed_df is not None:
    st.markdown("---")
    df = st.session_state.processed_df.copy()
    df['unit_price'] = df['Product'].map(st.session_state.price_map).fillna(0)
    df['sales_val'] = df['Sales_Qty'] * df['unit_price']
    df['soh_val'] = df['Total_SOH'] * df['unit_price']

    # ── FILTERS ─────────────────────────────────────────────────────────
    st.subheader("🔍 Analysis Filters")
    f1, f2 = st.columns(2)
    all_cities = sorted(df['City'].unique())
    all_prods = sorted(df['Product'].unique())
    sel_cities = f1.multiselect("Filter by City", all_cities, default=all_cities)
    sel_prods  = f2.multiselect("Filter by Product", all_prods,  default=all_prods)
    fdf = df[(df['City'].isin(sel_cities)) & (df['Product'].isin(sel_prods))]

    if fdf.empty:
        st.warning("No data found for selected filters.")
    else:
        st.subheader(f"📈 Results — {target_cust} ({sel_month} {sel_year})")

        # ── KPI CARDS ────────────────────────────────────────────────────
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Total Sales (Qty)",   f"{fdf['Sales_Qty'].sum():,.0f}")
        m1.caption(f"₹{fdf['sales_val'].sum():,.0f}")
        active = fdf[fdf['Sales_Qty'] > 0]
        avg_vel = active['velocity'].mean() if not active.empty else 0
        m2.metric("Avg Daily Velocity", f"{avg_vel:.2f}")
        m3.metric("Total SOH (Qty)",    f"{fdf['Total_SOH'].sum():,.0f}")
        m3.caption(f"₹{fdf['soh_val'].sum():,.0f}")
        m4.metric("Total Machines",     f"{fdf['Machine_Count'].sum():,.0f}")
        avg_doc = fdf[fdf['days_of_cover'] < 999]['days_of_cover'].mean()
        m5.metric("Avg Days of Cover",  f"{avg_doc:.1f}" if not np.isnan(avg_doc) else "∞")

        fmt = {
            'drr': '{:.1f}', 'days_of_cover': '{:.1f}', 'str_pct': '{:.1f}',
            'velocity': '{:.2f}', 'Machine_Count': '{:,.0f}',
            'Sales_Qty': '{:,.0f}', 'Total_SOH': '{:,.0f}',
        }

        tab1, tab2, tab3, tab4 = st.tabs([
            "📦 Inventory Analysis",
            "🤖 Machine Performance",
            "📊 Charts",
            "📈 Trend Lines",
        ])

        with tab1:
            inv_cols = ['City', 'Product', 'Total_SOH', 'drr', 'days_of_cover', 'str_pct']
            st.dataframe(
                fdf[inv_cols].style
                    .format(fmt)
                    .background_gradient(subset=['str_pct'], cmap='RdYlGn'),
                use_container_width=True
            )

        with tab2:
            mach_cols = ['City', 'Product', 'Machine_Count', 'Sales_Qty', 'velocity', 'abc_class']
            st.dataframe(
                fdf[mach_cols].style
                    .format(fmt)
                    .background_gradient(subset=['velocity'], cmap='YlGn'),
                use_container_width=True
            )

        with tab3:
            c1, c2 = st.columns(2)
            with c1:
                city_sales = fdf.groupby('City')['Sales_Qty'].sum().reset_index().sort_values('Sales_Qty', ascending=False)
                fig = px.bar(city_sales, x='City', y='Sales_Qty', title='Sales by City', color='Sales_Qty', color_continuous_scale='Blues')
                st.plotly_chart(fig, use_container_width=True)

            with c2:
                prod_soh = fdf.groupby('Product')['Total_SOH'].sum().reset_index()
                fig2 = px.pie(prod_soh, names='Product', values='Total_SOH', title='SOH Distribution by Product')
                st.plotly_chart(fig2, use_container_width=True)

            c3, c4 = st.columns(2)
            with c3:
                abc_dist = fdf['abc_class'].value_counts().reset_index()
                abc_dist.columns = ['Class', 'Count']
                fig3 = px.bar(abc_dist, x='Class', y='Count', title='ABC Classification Distribution',
                              color='Class', color_discrete_map={'A': '#2ecc71', 'B': '#f39c12', 'C': '#e74c3c'})
                st.plotly_chart(fig3, use_container_width=True)

            with c4:
                vel_data = fdf[fdf['velocity'] > 0].sort_values('velocity', ascending=False).head(15)
                fig4 = px.bar(vel_data, x='velocity', y='Product', orientation='h',
                              title='Top 15 Products by Velocity', color='velocity', color_continuous_scale='Greens')
                st.plotly_chart(fig4, use_container_width=True)

        with tab4:
            st.markdown("### 📈 Trend Line Analysis")
            st.info("Select historical data points to plot trends. Add multiple months below.")

            db = load_db() if st.session_state.db is None else st.session_state.db

            # Build history from saved DB
            history_records = []
            for key, saved in db.items():
                parts = key.split("__")
                if len(parts) == 3:
                    cust, mo, yr = parts
                    hist_df = pd.DataFrame(saved['data'])
                    hist_df['_customer'] = cust
                    hist_df['_month'] = mo
                    hist_df['_year'] = int(yr)
                    history_records.append(hist_df)

            if len(history_records) < 2:
                st.warning("Trend lines need at least **2 saved analyses**. Save this month's data first, then upload and save data for other months.")
            else:
                all_hist = pd.concat(history_records, ignore_index=True)
                month_order = {m: i for i, m in enumerate(months)}
                all_hist['_sort'] = all_hist['_year'] * 100 + all_hist['_month'].map(month_order)

                # Trend filters
                tf1, tf2, tf3 = st.columns(3)
                hist_cities = sorted(all_hist['City'].unique())
                hist_prods  = sorted(all_hist['Product'].unique())
                hist_metrics = ['Sales_Qty', 'Total_SOH', 'velocity', 'str_pct', 'days_of_cover']

                t_cities = tf1.multiselect("Cities", hist_cities, default=hist_cities[:3])
                t_prods  = tf2.multiselect("Products", hist_prods, default=hist_prods[:2])
                t_metric = tf3.selectbox("Metric to Plot", hist_metrics)

                # Select data points (months)
                available_pts = sorted(all_hist[['_year','_month']].drop_duplicates().apply(
                    lambda r: f"{r['_month']} {r['_year']}", axis=1).tolist(),
                    key=lambda x: int(x.split()[1]) * 100 + month_order.get(x.split()[0], 0)
                )
                selected_pts = st.multiselect("Select data points to include", available_pts, default=available_pts)

                if selected_pts and t_cities and t_prods:
                    trend_df = all_hist[
                        all_hist['City'].isin(t_cities) &
                        all_hist['Product'].isin(t_prods)
                    ].copy()
                    trend_df['period'] = trend_df['_month'] + ' ' + trend_df['_year'].astype(str)
                    trend_df = trend_df[trend_df['period'].isin(selected_pts)]
                    trend_df = trend_df.sort_values('_sort')
                    trend_df[t_metric] = pd.to_numeric(trend_df[t_metric], errors='coerce')
                    agg = trend_df.groupby(['period', '_sort', 'City', 'Product'])[t_metric].sum().reset_index()
                    agg = agg.sort_values('_sort')

                    fig_trend = px.line(
                        agg, x='period', y=t_metric,
                        color='City', line_dash='Product',
                        title=f"Trend: {t_metric} over time",
                        markers=True,
                    )
                    fig_trend.update_layout(xaxis_title="Period", yaxis_title=t_metric)
                    st.plotly_chart(fig_trend, use_container_width=True)
                else:
                    st.info("Select at least one city, product, and data point to plot.")

        # ── SAVE TO DATABASE ─────────────────────────────────────────────
        st.markdown("---")
        save_col1, save_col2 = st.columns([3, 1])
        save_col1.markdown(f"**Save this analysis:** `{target_cust} | {sel_month} {sel_year}`")
        if save_col2.button("💾 Save to Database", type="primary", use_container_width=True):
            db = load_db()
            key = db_key(target_cust, sel_month, sel_year)
            db[key] = {
                "customer": target_cust,
                "month": sel_month,
                "year": sel_year,
                "saved_at": datetime.datetime.now().isoformat(),
                "price_map": st.session_state.price_map,
                "data": st.session_state.processed_df.to_dict(orient='records'),
            }
            save_db(db)
            st.session_state.db = db
            st.success(f"✅ Saved: {target_cust} — {sel_month} {sel_year}")
            st.rerun()

elif not file:
    st.info("Please upload your vending report (.xlsx) to begin.")
    st.session_state.analysis_generated = False
    st.session_state.processed_df = None
