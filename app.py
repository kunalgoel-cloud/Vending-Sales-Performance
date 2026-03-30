import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
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
        st.error("Database Secret 'DB_URL' not found.")
        return None

# --- HELPER: DATA CLEANING ---
def exclude_totals(df, column_name):
    """Removes rows where the specified column contains 'total' (case-insensitive)"""
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

        # 1. Clean Sales Summary
        sales = all_sheets[s_map['sales summary']].iloc[1:, 0:3].copy()
        sales.columns = ['City', 'Product', 'Sales_Qty']
        sales = exclude_totals(sales, 'City')
        sales = exclude_totals(sales, 'Product')
        
        # 2. Clean SOH
        soh_raw = all_sheets[s_map['soh']].iloc[1:, [0, 1, 4]].copy()
        soh_raw.columns = ['Loc', 'Product', 'Total_SOH']
        soh_raw = exclude_totals(soh_raw, 'Loc')
        soh_raw = exclude_totals(soh_raw, 'Product')
        soh_raw['City'] = soh_raw['Loc'].astype(str).str.split(' ').str[0]
        soh = soh_raw.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

        # 3. Clean Machine Placement
        mach = all_sheets[s_map['machine placement']].iloc[1:, 0:3].copy()
        mach.columns = ['City', 'Product', 'Machine_Count']
        mach = exclude_totals(mach, 'City')
        mach = exclude_totals(mach, 'Product')

        # 4. Merge (Using 'outer' to ensure we don't lose machines with 0 sales/stock)
        df = pd.merge(mach, sales, on=['City', 'Product'], how='left')
        df = pd.merge(df, soh, on=['City', 'Product'], how='left')
        
        # Numeric cleanup - Fill NaNs with 0 for calculations
        for c in ['Sales_Qty', 'Total_SOH', 'Machine_Count']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

        # 5. Performance Metrics
        df['drr'] = df['Sales_Qty'] / 30  
        df['str_pct'] = np.where((df['Sales_Qty'] + df['Total_SOH']) > 0, 
                                 (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100), 0)
        
        # Velocity calculation: Sales / Machines (Safe against 0 machines)
        df['velocity'] = np.where(df['Machine_Count'] > 0, df['Sales_Qty'] / df['Machine_Count'], 0)
        
        # Days of Cover (Safe against 0 Sales/DRR)
        df['days_of_cover'] = np.where(df['drr'] > 0, df['Total_SOH'] / df['drr'], 999)

        # 6. Movement Bucketing
        c_list = [
            (df['str_pct'] > 40) & (df['days_of_cover'] < 10),
            (df['days_of_cover'] > 45),
            (df['Sales_Qty'] == 0) & (df['Total_SOH'] > 0)
        ]
        df['movement_bucket'] = np.select(c_list, ['Fast Mover', 'Slow Mover', 'Liquidate'], default='Steady')
        
        # 7. ABC Ranking
        df['rank'] = df['velocity'].rank(pct=True)
        df['abc_class'] = np.where(df['rank'] > 0.8, 'A', np.where(df['rank'] > 0.5, 'B', 'C'))
        
        return df.drop(columns=['rank'])
    except Exception as e:
        st.error(f"Processing Error: {e}")
        return None

# --- UI TABS ---
t1, t2, t3, t4 = st.tabs(["📊 Monthly Upload", "👤 Customer Master", "📈 Trend Analysis", "🛠 Admin & History"])

# --- TAB 1: MONTHLY UPLOAD ---
with t1:
    st.header("Upload Monthly Performance")
    engine = get_engine()
    
    customer_options = []
    if engine:
        try:
            customer_options = pd.read_sql("SELECT customer_name FROM dim_customers", engine)['customer_name'].tolist()
        except: pass

    if not customer_options:
        st.warning("Please add a customer in the 'Customer Master' tab first.")
    else:
        col_c, col_m, col_y = st.columns([2, 1, 1])
        target_cust = col_c.selectbox("Select Customer", customer_options)
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        sel_month = col_m.selectbox("Month", months, index=datetime.datetime.now().month - 1)
        sel_year = col_y.selectbox("Year", range(2024, 2031), index=2) 
        
        report_date = datetime.datetime.strptime(f"{sel_month} {sel_year}", "%b %Y").date()
        file = st.file_uploader("Upload Excel Workbook", type="xlsx")
        
        if file:
            results = process_master_file(file)
            if results is not None:
                results['customer'] = target_cust
                results['month'] = pd.to_datetime(report_date)
                
                st.markdown("---")
                st.subheader("🔍 Analysis Filters")
                f_col1, f_col2 = st.columns(2)
                
                city_list = sorted(results['City'].unique())
                prod_list = sorted(results['Product'].unique())
                
                selected_cities = f_col1.multiselect("Filter by City", city_list, default=city_list)
                selected_prods = f_col2.multiselect("Filter by Product", prod_list, default=prod_list)
                
                # Apply Filters
                filtered_df = results[
                    (results['City'].isin(selected_cities)) & 
                    (results['Product'].isin(selected_prods))
                ]

                if filtered_df.empty:
                    st.warning("No data matches the selected filters.")
                else:
                    # --- SUMMARY METRIC ROW ---
                    st.subheader(f"Summary: {target_cust} ({sel_month} {sel_year})")
                    m1, m2, m3, m4 = st.columns(4)
                    
                    total_sales = filtered_df['Sales_Qty'].sum()
                    # Avg velocity only on machines that actually sold something to keep the metric useful
                    active_sales_df = filtered_df[filtered_df['Sales_Qty'] > 0]
                    avg_velocity = active_sales_df['velocity'].mean() if not active_sales_df.empty else 0
                    
                    top_city = filtered_df.groupby('City')['Sales_Qty'].sum().idxmax()
                    
                    # TOTAL MACHINE COUNT (Including 0 sales/stock)
                    total_machines = filtered_df['Machine_Count'].sum()
                    
                    m1.metric("Total Sales", f"{total_sales:,.0f}")
                    m2.metric("Avg Velocity (Active)", f"{avg_velocity:.1f}")
                    m3.metric("Top City", top_city)
                    m4.metric("Total Machine Count", f"{total_machines:,.0f}")

                    # Formatting
                    format_map = {
                        'drr': '{:.1f}', 'str_pct': '{:.1f}%', 'velocity': '{:.1f}',
                        'days_of_cover': '{:.1f}', 'Sales_Qty': '{:,.0f}',
                        'Total_SOH': '{:,.0f}', 'Machine_Count': '{:,.0f}'
                    }

                    # --- INVENTORY ---
                    st.markdown("### 📦 Inventory Level Analysis")
                    inv_cols = ['City', 'Product', 'Total_SOH', 'drr', 'str_pct', 'days_of_cover', 'movement_bucket']
                    st.dataframe(filtered_df[inv_cols].style.format(format_map).background_gradient(subset=['str_pct'], cmap='RdYlGn'), use_container_width=True)

                    # --- MACHINE (Now includes ALL machines including 0 sales) ---
                    st.markdown("### 🤖 Machine Level Performance")
                    mach_cols = ['City', 'Product', 'Sales_Qty', 'Machine_Count', 'velocity', 'abc_class']
                    st.dataframe(filtered_df[mach_cols].style.format(format_map).background_gradient(subset=['velocity'], cmap='YlGn'), use_container_width=True)
                
                st.markdown("---")
                if st.button("Archive Full Upload to History"):
                    results.to_sql('vending_performance', engine, if_exists='append', index=False)
                    st.success("Successfully saved to history.")

# (Rest of the tabs remain the same...)
