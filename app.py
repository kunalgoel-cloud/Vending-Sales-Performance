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

        # 4. Merge
        df = pd.merge(sales, soh, on=['City', 'Product'], how='outer')
        df = pd.merge(df, mach, on=['City', 'Product'], how='left')
        
        # Numeric cleanup
        for c in ['Sales_Qty', 'Total_SOH', 'Machine_Count']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0 if c != 'Machine_Count' else 1)
        df['Machine_Count'] = df['Machine_Count'].replace(0, 1)

        # Exclude 0-stock locations to avoid skewing metrics
        df = df[df['Total_SOH'] > 0]

        # 5. Performance Metrics
        df['drr'] = df['Sales_Qty'] / 30  
        df['str_pct'] = (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100).fillna(0)
        df['velocity'] = df['Sales_Qty'] / df['Machine_Count']
        df['days_of_cover'] = np.where(df['Sales_Qty'] > 0, df['Total_SOH'] / df['drr'], 999)

        # 6. Movement Bucketing
        c_list = [
            (df['str_pct'] > 40) & (df['days_of_cover'] < 10),
            (df['days_of_cover'] > 45),
            (df['Sales_Qty'] == 0)
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

# --- TAB 2: CUSTOMER MASTER ---
with t2:
    st.header("Register New Customer")
    with st.form("add_cust", clear_on_submit=True):
        name = st.text_input("Customer Name")
        reg = st.text_input("Region")
        if st.form_submit_button("Save Customer"):
            engine = get_engine()
            if engine and name:
                with engine.connect() as conn:
                    conn.execute(text("INSERT INTO dim_customers (customer_name, region) VALUES (:n, :r)"), {"n":name, "r":reg})
                    conn.commit()
                st.success(f"Registered {name}")
                st.rerun()

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
                    avg_velocity = filtered_df['velocity'].mean()
                    top_city = filtered_df.groupby('City')['Sales_Qty'].sum().idxmax()
                    
                    # NEW: Calculating unique machine count based on city/product combinations in filter
                    total_machines = filtered_df['Machine_Count'].sum()
                    
                    m1.metric("Total Sales", f"{total_sales:,.0f}")
                    m2.metric("Avg Velocity", f"{avg_velocity:.1f}")
                    m3.metric("Top City", top_city)
                    m4.metric("Total Machines", f"{total_machines:,.0f}")

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

                    # --- MACHINE ---
                    st.markdown("### 🤖 Machine Level Performance")
                    mach_view = filtered_df[filtered_df['Sales_Qty'] > 0][['City', 'Product', 'Sales_Qty', 'Machine_Count', 'velocity', 'abc_class']]
                    st.dataframe(mach_view.style.format(format_map).background_gradient(subset=['velocity'], cmap='YlGn'), use_container_width=True)
                
                st.markdown("---")
                if st.button("Archive Full Upload to History"):
                    results.to_sql('vending_performance', engine, if_exists='append', index=False)
                    st.success("Successfully saved to history.")

# --- TAB 3: TREND ANALYSIS ---
with t3:
    st.header("Performance Trends")
    engine = get_engine()
    if engine:
        try:
            hist = pd.read_sql("SELECT * FROM vending_performance ORDER BY month", engine)
            if not hist.empty:
                c_sel = st.selectbox("Customer", hist['customer'].unique())
                cust_hist = hist[hist['customer'] == c_sel]
                
                t_col1, t_col2 = st.columns(2)
                t_cities = t_col1.multiselect("Cities", cust_hist['city'].unique(), default=cust_hist['city'].unique()[0])
                t_prods = t_col2.multiselect("Products", cust_hist['product'].unique(), default=cust_hist['product'].unique()[:3])
                
                plot_data = cust_hist[(cust_hist['city'].isin(t_cities)) & (cust_hist['product'].isin(t_prods))]
                
                if not plot_data.empty:
                    fig = px.line(plot_data, x='month', y='velocity', color='product', facet_col='city', markers=True)
                    st.plotly_chart(fig, use_container_width=True)
        except: st.info("Upload data to see trends.")

# --- TAB 4: ADMIN ---
with t4:
    st.header("Database Maintenance")
    if st.button("Clear All History", type="primary"):
        engine = get_engine()
        if engine:
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM vending_performance"))
                conn.commit()
            st.rerun()
