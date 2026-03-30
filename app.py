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

        # 2. Merge
        df = pd.merge(mach, sales, on=['City', 'Product'], how='left')
        df = pd.merge(df, soh, on=['City', 'Product'], how='left')
        
        for c in ['Sales_Qty', 'Total_SOH', 'Machine_Count']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

        # 3. Basic Calculations
        df['drr'] = df['Sales_Qty'] / 30  
        df['velocity'] = np.where(df['Machine_Count'] > 0, (df['Sales_Qty'] / df['Machine_Count']) / 30, 0)
        df['str_pct'] = np.where((df['Sales_Qty'] + df['Total_SOH']) > 0, 
                                 (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100), 0)
        df['days_of_cover'] = np.where(df['drr'] > 0, df['Total_SOH'] / df['drr'], 999)
        
        # ABC Ranking
        df['rank'] = df['velocity'].rank(pct=True)
        df['abc_class'] = np.where(df['rank'] > 0.8, 'A', np.where(df['rank'] > 0.5, 'B', 'C'))
        
        return df.drop(columns=['rank'])
    except Exception as e:
        st.error(f"Processing Error: {e}")
        return None

# --- UI TABS ---
t1, t2, t3, t4 = st.tabs(["📊 Monthly Upload", "👤 Customer Master", "📈 Trend Analysis", "🛠 Admin"])

with t1:
    st.header("Upload Monthly Performance")
    engine = get_engine()
    
    # Customer Selection
    customer_options = ["Vendiman"] # Fallback if DB not connected
    if engine:
        try:
            customer_options = pd.read_sql("SELECT customer_name FROM dim_customers", engine)['customer_name'].tolist()
        except: pass

    col_c, col_m, col_y = st.columns([2, 1, 1])
    target_cust = col_c.selectbox("Select Customer", customer_options)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    sel_month = col_m.selectbox("Month", months, index=datetime.datetime.now().month - 1)
    sel_year = col_y.selectbox("Year", range(2024, 2031), index=2) 
    
    file = st.file_uploader("Upload Excel Workbook", type="xlsx")
    
    if file:
        results = process_master_file(file)
        if results is not None:
            # --- NEW: PRICE INPUT SECTION ---
            st.markdown("### 💰 Step 2: Item Wise Price Entry")
            unique_products = sorted(results['Product'].unique())
            
            # Create a dictionary to hold user inputs
            price_map = {}
            with st.expander("Click to set unit prices for this month", expanded=True):
                # Use a data editor for a clean table-like input
                price_df = pd.DataFrame({"Product": unique_products, "Unit_Price_INR": 0.0})
                edited_price_df = st.data_editor(price_df, use_container_width=True, hide_index=True)
                
                # Convert edited dataframe back to a dictionary
                price_map = dict(zip(edited_price_df['Product'], edited_price_df['Unit_Price_INR']))
                
            if st.button("Calculate Financials & Publish Output"):
                # Apply prices to results
                results['unit_price'] = results['Product'].map(price_map)
                results['sales_val'] = results['Sales_Qty'] * results['unit_price']
                results['soh_val'] = results['Total_SOH'] * results['unit_price']
                
                st.markdown("---")
                # --- SUMMARY METRIC ROW ---
                st.subheader(f"Results: {target_cust} - {sel_month} {sel_year}")
                m1, m2, m3, m4 = st.columns(4)
                
                total_sales_units = results['Sales_Qty'].sum()
                total_sales_val = results['sales_val'].sum()
                total_soh_units = results['Total_SOH'].sum()
                total_soh_val = results['soh_val'].sum()
                total_machines = results['Machine_Count'].sum()
                
                m1.metric("Total Sales (Qty)", f"{total_sales_units:,.0f}")
                m1.caption(f"Value: **₹{total_sales_val:,.0f}**")
                
                active_sales_df = results[results['Sales_Qty'] > 0]
                avg_daily_vel = active_sales_df['velocity'].mean() if not active_sales_df.empty else 0
                m2.metric("Avg Daily Velocity", f"{avg_daily_vel:.2f}")
                
                m3.metric("Total Stock on Hand", f"{total_soh_units:,.0f}")
                m3.caption(f"Value: **₹{total_soh_val:,.0f}**")
                
                m4.metric("Total Machine Count", f"{total_machines:,.0f}")

                # --- TABLES ---
                format_map = {
                    'drr': '{:.1f}', 'str_pct': '{:.1f}%', 'velocity': '{:.2f}',
                    'days_of_cover': '{:.1f}', 'Sales_Qty': '{:,.0f}',
                    'Total_SOH': '{:,.0f}', 'Machine_Count': '{:,.0f}'
                }

                st.markdown("### 📦 Performance Table")
                st.dataframe(results[['City', 'Product', 'Sales_Qty', 'Total_SOH', 'Machine_Count', 'velocity', 'abc_class']].style.format(format_map), use_container_width=True)

                if st.button("Confirm & Save to History"):
                    if engine:
                        results['month'] = pd.to_datetime(f"1 {sel_month} {sel_year}")
                        results['customer'] = target_cust
                        results.to_sql('vending_performance', engine, if_exists='append', index=False)
                        st.success("Successfully saved!")
