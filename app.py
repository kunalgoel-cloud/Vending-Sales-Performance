import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from sqlalchemy import create_engine, text
import datetime

# --- APP CONFIG ---
st.set_page_config(page_title="Vending Performance Hub", layout="wide")

# --- SIDEBAR: FINANCIALS ---
st.sidebar.header("💰 Financial Settings")
unit_price = st.sidebar.number_input("Average Unit Price (₹)", min_value=0.0, value=20.0, step=1.0)

# --- DB CONNECTION ---
def get_engine():
    try:
        url = st.secrets["DB_URL"]
        return create_engine(url)
    except Exception:
        return None

# --- HELPER: DATA CLEANING ---
def exclude_totals(df, column_name):
    return df[~df[column_name].astype(str).str.lower().str.contains('total', na=False)]

# --- CORE PROCESSING ENGINE ---
def process_master_file(uploaded_file):
    try:
        all_sheets = pd.read_excel(uploaded_file, sheet_name=None)
        s_map = {k.lower().strip(): k for k in all_sheets.keys()}
        
        req = ['sales summary', 'soh', 'machine placement']
        if not all(r in s_map for r in req):
            st.error(f"Excel must contain sheets: {req}")
            return None

        # 1. Clean Sheets
        sales = exclude_totals(all_sheets[s_map['sales summary']].iloc[1:, 0:3], 'City')
        sales.columns = ['City', 'Product', 'Sales_Qty']
        
        soh_raw = exclude_totals(all_sheets[s_map['soh']].iloc[1:, [0, 1, 4]], 'Loc')
        soh_raw.columns = ['Loc', 'Product', 'Total_SOH']
        soh_raw['City'] = soh_raw['Loc'].astype(str).str.split(' ').str[0]
        soh = soh_raw.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

        mach = exclude_totals(all_sheets[s_map['machine placement']].iloc[1:, 0:3], 'City')
        mach.columns = ['City', 'Product', 'Machine_Count']

        # 2. Merge (Preserving Machine Count)
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

# --- TAB 1: MONTHLY UPLOAD ---
with t1:
    st.header("Upload Monthly Performance")
    engine = get_engine()
    
    # Load customers
    customer_options = []
    if engine:
        try:
            customer_options = pd.read_sql("SELECT customer_name FROM dim_customers", engine)['customer_name'].tolist()
        except: pass

    if not customer_options:
        st.warning("Please add a customer in 'Customer Master' first.")
    else:
        col_c, col_m, col_y = st.columns([2, 1, 1])
        target_cust = col_c.selectbox("Select Customer", customer_options)
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        sel_month = col_m.selectbox("Month", months, index=datetime.datetime.now().month - 1)
        sel_year = col_y.selectbox("Year", range(2024, 2031), index=2) 
        
        file = st.file_uploader("Upload Excel Workbook", type="xlsx")
        
        if file:
            results = process_master_file(file)
            if results is not None:
                # Filter Logic
                st.markdown("---")
                f_col1, f_col2 = st.columns(2)
                selected_cities = f_col1.multiselect("Filter City", sorted(results['City'].unique()), default=results['City'].unique())
                selected_prods = f_col2.multiselect("Filter Product", sorted(results['Product'].unique()), default=results['Product'].unique())
                
                filtered_df = results[(results['City'].isin(selected_cities)) & (results['Product'].isin(selected_prods))]

                # --- SUMMARY METRIC ROW ---
                st.subheader(f"Dashboard Summary: {target_cust}")
                m1, m2, m3, m4 = st.columns(4)
                
                # Calculations for Metrics
                total_sales_units = filtered_df['Sales_Qty'].sum()
                total_soh_units = filtered_df['Total_SOH'].sum()
                total_machines = filtered_df['Machine_Count'].sum()
                
                # Financial conversion
                val_sales = total_sales_units * unit_price
                val_soh = total_soh_units * unit_price
                
                # Display Metrics
                m1.metric("Total Sales (Qty)", f"{total_sales_units:,.0f}")
                m1.caption(f"Value: **₹{val_sales:,.0f}**")
                
                active_sales_df = filtered_df[filtered_df['Sales_Qty'] > 0]
                avg_daily_vel = active_sales_df['velocity'].mean() if not active_sales_df.empty else 0
                m2.metric("Avg Daily Velocity", f"{avg_daily_vel:.2f}")
                
                m3.metric("Total Stock on Hand", f"{total_soh_units:,.0f}")
                m3.caption(f"Value: **₹{val_soh:,.0f}**")
                
                m4.metric("Total Machines", f"{total_machines:,.0f}")

                # --- DATA TABLES ---
                format_map = {
                    'drr': '{:.1f}', 'str_pct': '{:.1f}%', 'velocity': '{:.2f}',
                    'days_of_cover': '{:.1f}', 'Sales_Qty': '{:,.0f}',
                    'Total_SOH': '{:,.0f}', 'Machine_Count': '{:,.0f}'
                }

                st.markdown("### 📦 Inventory Analysis")
                st.dataframe(filtered_df[['City', 'Product', 'Total_SOH', 'drr', 'str_pct', 'days_of_cover']].style.format(format_map), use_container_width=True)

                st.markdown("### 🤖 Machine Performance")
                st.dataframe(filtered_df[['City', 'Product', 'Sales_Qty', 'Machine_Count', 'velocity', 'abc_class']].style.format(format_map), use_container_width=True)

                if st.button("Archive to Neon DB"):
                    if engine:
                        results['customer'] = target_cust
                        results['month'] = pd.to_datetime(f"1 {sel_month} {sel_year}")
                        results.to_sql('vending_performance', engine, if_exists='append', index=False)
                        st.success("Archived successfully.")
