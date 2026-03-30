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

# --- HELPER: EXCLUDE TOTAL ROWS ---
def exclude_totals(df, column_name):
    """Filters out rows where the specified column contains 'total' (case-insensitive)"""
    return df[~df[column_name].astype(str).str.lower().str.contains('total', na=False)]

# --- DATA PROCESSING ENGINE ---
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
        # Extract City
        soh_raw['City'] = soh_raw['Loc'].astype(str).str.split(' ').str[0]
        soh = soh_raw.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

        # 3. Clean Machine Placement
        mach = all_sheets[s_map['machine placement']].iloc[1:, 0:3].copy()
        mach.columns = ['City', 'Product', 'Machine_Count']
        mach = exclude_totals(mach, 'City')
        mach = exclude_totals(mach, 'Product')

        # 4. Merge & Calculate
        df = pd.merge(sales, soh, on=['City', 'Product'], how='outer')
        df = pd.merge(df, mach, on=['City', 'Product'], how='left')
        
        # Numeric values
        for c in ['Sales_Qty', 'Total_SOH', 'Machine_Count']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0 if c != 'Machine_Count' else 1)
        df['Machine_Count'] = df['Machine_Count'].replace(0, 1)

        # Performance Metrics
        df['str_pct'] = (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100).fillna(0)
        df['velocity'] = df['Sales_Qty'] / df['Machine_Count']
        df['days_of_cover'] = np.where(df['Sales_Qty'] > 0, df['Total_SOH'] / (df['Sales_Qty'] / 30), 999)

        # Movement Buckets
        c_list = [
            (df['str_pct'] > 40) & (df['days_of_cover'] < 10),
            (df['days_of_cover'] > 45),
            (df['Sales_Qty'] == 0) & (df['Total_SOH'] > 0)
        ]
        df['movement_bucket'] = np.select(c_list, ['Fast Mover', 'Slow Mover', 'Liquidate'], default='Steady')
        
        # ABC Ranking
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
    st.header("Upload Monthly Data")
    engine = get_engine()
    
    # Load customers for dropdown
    customer_options = []
    if engine:
        try:
            customer_options = pd.read_sql("SELECT customer_name FROM dim_customers", engine)['customer_name'].tolist()
        except: pass

    if not customer_options:
        st.warning("Please add a customer in the 'Customer Master' tab first.")
    else:
        # Date and Customer selection
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
                
                # --- SUMMARY METRIC ROW ---
                st.markdown("---")
                st.subheader(f"Summary: {target_cust} - {sel_month} {sel_year}")
                m1, m2, m3, m4 = st.columns(4)
                
                # Exclude any remaining 'Total' from metrics just in case
                total_sales = results['Sales_Qty'].sum()
                avg_velocity = results['velocity'].mean()
                top_city = results.groupby('City')['Sales_Qty'].sum().idxmax()
                
                m1.metric("Total Sales", f"{total_sales:,.0f}")
                m2.metric("Avg Velocity", f"{avg_velocity:.1f}")
                m3.metric("Top City", top_city)
                m4.metric("Active SKUs", len(results))

                # Display Formatting (1 Decimal Place)
                format_map = {
                    'str_pct': '{:.1f}%',
                    'velocity': '{:.1f}',
                    'days_of_cover': '{:.1f}',
                    'Sales_Qty': '{:,.0f}',
                    'Total_SOH': '{:,.0f}',
                    'Machine_Count': '{:,.0f}'
                }

                # --- SPLIT VIEW: INVENTORY ---
                st.markdown("### 📦 Inventory Level Analysis")
                inv_df = results[['City', 'Product', 'Total_SOH', 'str_pct', 'days_of_cover', 'movement_bucket']]
                st.dataframe(inv_df.style.format(format_map).background_gradient(subset=['str_pct'], cmap='RdYlGn'), use_container_width=True)

                # --- SPLIT VIEW: MACHINE ---
                st.markdown("### 🤖 Machine Level Performance")
                mach_df = results[['City', 'Product', 'Sales_Qty', 'Machine_Count', 'velocity', 'abc_class']]
                st.dataframe(mach_df.style.format(format_map).background_gradient(subset=['velocity'], cmap='YlGn'), use_container_width=True)
                
                if st.button("Archive Data to Neon DB"):
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
                city_sel = st.selectbox("City", hist[hist['customer']==c_sel]['city'].unique())
                plot_data = hist[(hist['customer']==c_sel) & (hist['city']==city_sel)]
                fig = px.line(plot_data, x='month', y='velocity', color='product', markers=True)
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
