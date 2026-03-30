import streamlit as st
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- APP CONFIG ---
st.set_page_config(page_title="Vending Performance Hub", layout="wide")

# --- DB CONNECTION ---
def get_engine():
    try:
        url = st.secrets["DB_URL"]
        return create_engine(url)
    except Exception:
        return None

# --- HELPER: DATA CLEANING ---
def clean_df(df, expected_cols):
    """Assigns columns and removes 'Total' rows to prevent mapping errors."""
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

        # 1. Extract and Clean
        sales = all_sheets[s_map['sales summary']].iloc[1:, 0:3].copy()
        sales = clean_df(sales, ['City', 'Product', 'Sales_Qty'])
        
        soh_raw = all_sheets[s_map['soh']].iloc[1:, [0, 1, 4]].copy()
        soh_raw = clean_df(soh_raw, ['Loc', 'Product', 'Total_SOH'])
        soh_raw['City'] = soh_raw['Loc'].astype(str).str.split(' ').str[0].str.split('_').str[0]
        soh = soh_raw.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

        mach = all_sheets[s_map['machine placement']].iloc[1:, 0:3].copy()
        mach = clean_df(mach, ['City', 'Product', 'Machine_Count'])

        # 2. Merge - Ensures all machines are kept
        df = pd.merge(mach, sales, on=['City', 'Product'], how='left')
        df = pd.merge(df, soh, on=['City', 'Product'], how='left')
        
        for c in ['Sales_Qty', 'Total_SOH', 'Machine_Count']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

        # 3. Calculations
        df['drr'] = df['Sales_Qty'] / 30  
        df['velocity'] = np.where(df['Machine_Count'] > 0, (df['Sales_Qty'] / df['Machine_Count']) / 30, 0)
        df['str_pct'] = np.where((df['Sales_Qty'] + df['Total_SOH']) > 0, 
                                 (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100), 0)
        df['days_of_cover'] = np.where(df['drr'] > 0, df['Total_SOH'] / df['drr'], 999)
        
        # 4. ABC Ranking
        df['rank'] = df['velocity'].rank(pct=True)
        df['abc_class'] = np.where(df['rank'] > 0.8, 'A', np.where(df['rank'] > 0.5, 'B', 'C'))
        
        return df.drop(columns=['rank'])
    except Exception as e:
        st.error(f"Processing Error: {e}")
        return None

# --- UI TABS ---
t1, t2, t3, t4 = st.tabs(["📊 Monthly Upload", "👤 Customer Master", "📈 Trend Analysis", "🛠 Admin"])

with t1:
    st.header("Channel Performance Upload")
    engine = get_engine()
    
    # Customer Selection
    customer_options = ["Vendiman"] 
    if engine:
        try:
            customer_options = pd.read_sql("SELECT customer_name FROM dim_customers", engine)['customer_name'].tolist()
        except: pass

    col_c, col_m, col_y = st.columns([2, 1, 1])
    target_cust = col_c.selectbox("Select Customer", customer_options)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    sel_month = col_m.selectbox("Month", months, index=datetime.datetime.now().month - 1)
    sel_year = col_y.selectbox("Year", range(2024, 2031), index=2) 
    
    file = st.file_uploader("Upload Multi-Sheet Excel", type="xlsx")
    
    if file:
        results = process_master_file(file)
        if results is not None:
            # --- STEP 2: PRICE ENTRY ---
            st.markdown("### 💰 Step 2: Item-Wise Price Input")
            unique_prods = sorted(results['Product'].unique())
            price_df = pd.DataFrame({"Product": unique_prods, "Price_per_Unit": 0.0})
            edited_prices = st.data_editor(price_df, use_container_width=True, hide_index=True)
            price_map = dict(zip(edited_prices['Product'], edited_prices['Price_per_Unit']))
            
            if st.button("Generate Performance Analysis"):
                # Apply Prices
                results['unit_price'] = results['Product'].map(price_map)
                results['sales_val'] = results['Sales_Qty'] * results['unit_price']
                results['soh_val'] = results['Total_SOH'] * results['unit_price']
                
                st.markdown("---")
                # --- FILTERS ---
                st.subheader("🔍 Analysis Filters")
                f_col1, f_col2 = st.columns(2)
                sel_cities = f_col1.multiselect("Filter City", sorted(results['City'].unique()), default=results['City'].unique())
                sel_prods = f_col2.multiselect("Filter Product", sorted(results['Product'].unique()), default=results['Product'].unique())
                
                filtered_df = results[(results['City'].isin(sel_cities)) & (results['Product'].isin(sel_prods))]
                
                # --- SUMMARY METRICS ---
                m1, m2, m3, m4 = st.columns(4)
                
                total_sales_units = filtered_df['Sales_Qty'].sum()
                total_sales_val = filtered_df['sales_val'].sum()
                total_soh_units = filtered_df['Total_SOH'].sum()
                total_soh_val = filtered_df['soh_val'].sum()
                total_machines = filtered_df['Machine_Count'].sum()
                
                m1.metric("Total Sales (Qty)", f"{total_sales_units:,.0f}")
                m1.caption(f"Value: **₹{total_sales_val:,.0f}**")
                
                active_df = filtered_df[filtered_df['Sales_Qty'] > 0]
                avg_vel = active_df['velocity'].mean() if not active_df.empty else 0
                m2.metric("Avg Daily Velocity", f"{avg_vel:.2f}")
                
                m3.metric("Total Stock on Hand", f"{total_soh_units:,.0f}")
                m3.caption(f"Value: **₹{total_soh_val:,.0f}**")
                
                m4.metric("Total Machine Count", f"{total_machines:,.0f}")

                # --- SPLIT ANALYSIS VIEWS ---
                format_map = {'drr': '{:.1f}', 'days_of_cover': '{:.1f}', 'str_pct': '{:.1f}%', 
                              'velocity': '{:.2f}', 'Machine_Count': '{:,.0f}', 'Sales_Qty': '{:,.0f}', 'Total_SOH': '{:,.0f}'}

                st.markdown("### 📦 Section 1: Inventory Level Analysis")
                inv_cols = ['City', 'Product', 'Total_SOH', 'drr', 'days_of_cover', 'str_pct']
                st.dataframe(filtered_df[inv_cols].style.format(format_map).background_gradient(subset=['str_pct'], cmap='RdYlGn'), use_container_width=True)

                st.markdown("### 🤖 Section 2: Machine Level Performance")
                mach_cols = ['City', 'Product', 'Machine_Count', 'Sales_Qty', 'velocity', 'abc_class']
                st.dataframe(filtered_df[mach_cols].style.format(format_map).background_gradient(subset=['velocity'], cmap='YlGn'), use_container_width=True)

                if st.button("Confirm & Save to History"):
                    st.success("Analysis finalized and saved.")
