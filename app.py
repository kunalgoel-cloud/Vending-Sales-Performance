import streamlit as st
import pandas as pd
import numpy as np
import datetime

# --- APP CONFIG ---
st.set_page_config(page_title="Vending Performance Hub", layout="wide")

# --- SESSION STATE INITIALIZATION ---
# This ensures that once you generate the analysis, it doesn't disappear when filtering
if 'analysis_generated' not in st.session_state:
    st.session_state.analysis_generated = False
if 'processed_df' not in st.session_state:
    st.session_state.processed_df = None
if 'price_map' not in st.session_state:
    st.session_state.price_map = {}

# --- HELPER: DATA CLEANING ---
def clean_df(df, expected_cols):
    """Forces column names and removes rows containing 'total' to prevent mapping errors."""
    df.columns = expected_cols
    df = df[~df[expected_cols[0]].astype(str).str.lower().str.contains('total', na=False)]
    return df

# --- CORE PROCESSING ENGINE ---
def process_master_file(uploaded_file):
    try:
        all_sheets = pd.read_excel(uploaded_file, sheet_name=None)
        s_map = {k.lower().strip(): k for k in all_sheets.keys()}
        
        req = ['sales summary', 'soh', 'machine placement']
        if not all(r in s_map for r in req):
            st.error(f"Missing required sheets: {req}")
            return None

        # 1. Extract and Clean (Using iloc to handle potential header shifts)
        sales = all_sheets[s_map['sales summary']].iloc[1:, 0:3].copy()
        sales = clean_df(sales, ['City', 'Product', 'Sales_Qty'])
        
        soh_raw = all_sheets[s_map['soh']].iloc[1:, [0, 1, 4]].copy()
        soh_raw = clean_df(soh_raw, ['Loc', 'Product', 'Total_SOH'])
        # Extract City from Location string
        soh_raw['City'] = soh_raw['Loc'].astype(str).str.split(' ').str[0].str.split('_').str[0]
        soh = soh_raw.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

        mach = all_sheets[s_map['machine placement']].iloc[1:, 0:3].copy()
        mach = clean_df(mach, ['City', 'Product', 'Machine_Count'])

        # 2. Merge - Starting with Machine Placement to ensure full machine count (711+)
        df = pd.merge(mach, sales, on=['City', 'Product'], how='left')
        df = pd.merge(df, soh, on=['City', 'Product'], how='left')
        
        # Numeric cleanup
        for c in ['Sales_Qty', 'Total_SOH', 'Machine_Count']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

        # 3. Calculations
        df['drr'] = df['Sales_Qty'] / 30  
        # Daily Velocity: (Sales / Machines) / 30 Days
        df['velocity'] = np.where(df['Machine_Count'] > 0, (df['Sales_Qty'] / df['Machine_Count']) / 30, 0)
        
        df['str_pct'] = np.where((df['Sales_Qty'] + df['Total_SOH']) > 0, 
                                 (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100), 0)
        
        # Days of Cover (Safe against 0 DRR)
        df['days_of_cover'] = np.where(df['drr'] > 0, df['Total_SOH'] / df['drr'], 999)
        
        # ABC Ranking based on velocity
        df['rank'] = df['velocity'].rank(pct=True)
        df['abc_class'] = np.where(df['rank'] > 0.8, 'A', np.where(df['rank'] > 0.5, 'B', 'C'))
        
        return df.drop(columns=['rank'])
    except Exception as e:
        st.error(f"Processing Error: {e}")
        return None

# --- MAIN UI ---
st.title("📊 Vending Performance Hub")

# Inputs
col_c, col_m, col_y = st.columns([2, 1, 1])
target_cust = col_c.selectbox("Select Customer", ["Vendiman", "External Partner"])
sel_month = col_m.selectbox("Month", ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], index=datetime.datetime.now().month - 1)
sel_year = col_y.selectbox("Year", range(2024, 2031), index=2)

file = st.file_uploader("Step 1: Upload Excel Workbook", type="xlsx")

if file:
    # Process only if not already in session state or if file changes
    if st.session_state.processed_df is None:
        st.session_state.processed_df = process_master_file(file)

    if st.session_state.processed_df is not None:
        st.markdown("---")
        st.subheader("💰 Step 2: Item-Wise Price Entry")
        st.info("Set the price for each product. These will be used for financial metrics.")
        
        unique_prods = sorted(st.session_state.processed_df['Product'].unique())
        price_init = pd.DataFrame({"Product": unique_prods, "Price_per_Unit": 0.0})
        
        # Prices entered here are remembered via key="price_editor"
        edited_prices = st.data_editor(price_init, use_container_width=True, hide_index=True, key="price_editor")
        
        # Trigger Analysis
        if st.button("Generate Performance Analysis"):
            st.session_state.analysis_generated = True
            st.session_state.price_map = dict(zip(edited_prices['Product'], edited_prices['Price_per_Unit']))

        # --- PERSISTENT OUTPUT SECTION ---
        if st.session_state.analysis_generated:
            st.markdown("---")
            
            # Map the prices and calculate values
            df = st.session_state.processed_df.copy()
            df['unit_price'] = df['Product'].map(st.session_state.price_map)
            df['sales_val'] = df['Sales_Qty'] * df['unit_price']
            df['soh_val'] = df['Total_SOH'] * df['unit_price']

            # --- FILTERS ---
            st.subheader("🔍 Analysis Filters")
            f_col1, f_col2 = st.columns(2)
            
            # These filters are now stable because they are outside the "if button" block
            all_cities = sorted(df['City'].unique())
            all_prods = sorted(df['Product'].unique())
            
            sel_cities = f_col1.multiselect("Filter by City", all_cities, default=all_cities)
            sel_prods = f_col2.multiselect("Filter by Product", all_prods, default=all_prods)
            
            # Apply Filter
            filtered_df = df[(df['City'].isin(sel_cities)) & (df['Product'].isin(sel_prods))]
            
            if filtered_df.empty:
                st.warning("No data found for selected filters.")
            else:
                # --- SUMMARY METRICS ---
                st.subheader(f"Results Summary: {target_cust} ({sel_month} {sel_year})")
                m1, m2, m3, m4 = st.columns(4)
                
                total_sales_units = filtered_df['Sales_Qty'].sum()
                total_sales_val = filtered_df['sales_val'].sum()
                total_soh_units = filtered_df['Total_SOH'].sum()
                total_soh_val = filtered_df['soh_val'].sum()
                total_machines = filtered_df['Machine_Count'].sum()
                
                # Metric 1: Sales
                m1.metric("Total Sales (Qty)", f"{total_sales_units:,.0f}")
                m1.caption(f"Value: **₹{total_sales_val:,.0f}**")
                
                # Metric 2: Velocity
                active_sales_df = filtered_df[filtered_df['Sales_Qty'] > 0]
                avg_vel = active_sales_df['velocity'].mean() if not active_sales_df.empty else 0
                m2.metric("Avg Daily Velocity", f"{avg_vel:.2f}")
                
                # Metric 3: Total SOH
                m3.metric("Total Stock on Hand", f"{total_soh_units:,.0f}")
                m3.caption(f"Value: **₹{total_soh_val:,.0f}**")
                
                # Metric 4: Machine Count
                m4.metric("Total Machines", f"{total_machines:,.0f}")

                # --- DATA VIEWS ---
                format_map = {
                    'drr': '{:.1f}', 'days_of_cover': '{:.1f}', 'str_pct': '{:.1f}%', 
                    'velocity': '{:.2f}', 'Machine_Count': '{:,.0f}', 
                    'Sales_Qty': '{:,.0f}', 'Total_SOH': '{:,.0f}'
                }

                st.markdown("### 📦 Section 1: Inventory Level Analysis")
                inv_cols = ['City', 'Product', 'Total_SOH', 'drr', 'days_of_cover', 'str_pct']
                st.dataframe(filtered_df[inv_cols].style.format(format_map).background_gradient(subset=['str_pct'], cmap='RdYlGn'), use_container_width=True)

                st.markdown("### 🤖 Section 2: Machine Level Performance")
                mach_cols = ['City', 'Product', 'Machine_Count', 'Sales_Qty', 'velocity', 'abc_class']
                st.dataframe(filtered_df[mach_cols].style.format(format_map).background_gradient(subset=['velocity'], cmap='YlGn'), use_container_width=True)
            
            # Reset Button
            if st.sidebar.button("🗑 Reset & Upload New File"):
                st.session_state.analysis_generated = False
                st.session_state.processed_df = None
                st.rerun()

else:
    st.info("Please upload your vending report (xlsx) to begin.")
    # Reset states if file is removed
    st.session_state.analysis_generated = False
    st.session_state.processed_df = None
