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

# --- DATA PROCESSING ENGINE ---
def process_master_file(uploaded_file):
    try:
        all_sheets = pd.read_excel(uploaded_file, sheet_name=None)
        s_map = {k.lower().strip(): k for k in all_sheets.keys()}
        
        req = ['sales summary', 'soh', 'machine placement']
        if not all(r in s_map for r in req):
            st.error(f"Excel must contain sheets: {req}")
            return None

        # Cleaning logic based on the specific template structure
        sales = all_sheets[s_map['sales summary']].iloc[1:, 0:3].copy()
        sales.columns = ['City', 'Product', 'Sales_Qty']
        
        soh_raw = all_sheets[s_map['soh']].iloc[1:, [0, 1, 4]].copy()
        soh_raw.columns = ['Loc', 'Product', 'Total_SOH']
        soh_raw['City'] = soh_raw['Loc'].astype(str).str.split(' ').str[0]
        soh = soh_raw.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

        mach = all_sheets[s_map['machine placement']].iloc[1:, 0:3].copy()
        mach.columns = ['City', 'Product', 'Machine_Count']

        df = pd.merge(sales, soh, on=['City', 'Product'], how='outer')
        df = pd.merge(df, mach, on=['City', 'Product'], how='left')
        
        for c in ['Sales_Qty', 'Total_SOH', 'Machine_Count']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0 if c != 'Machine_Count' else 1)
        df['Machine_Count'] = df['Machine_Count'].replace(0, 1)

        # Calculations
        df['str_pct'] = (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100).fillna(0)
        df['velocity'] = df['Sales_Qty'] / df['Machine_Count']
        df['days_of_cover'] = np.where(df['Sales_Qty'] > 0, df['Total_SOH'] / (df['Sales_Qty'] / 30), 999)

        # Buckets
        c_list = [
            (df['str_pct'] > 40) & (df['days_of_cover'] < 10),
            (df['days_of_cover'] > 45),
            (df['Sales_Qty'] == 0) & (df['Total_SOH'] > 0)
        ]
        df['movement_bucket'] = np.select(c_list, ['Fast Mover', 'Slow Mover', 'Liquidate'], default='Steady')
        
        # ABC Class
        df['rank'] = df['velocity'].rank(pct=True)
        df['abc_class'] = np.where(df['rank'] > 0.8, 'A', np.where(df['rank'] > 0.5, 'B', 'C'))
        
        return df.drop(columns=['rank'])
    except Exception as e:
        st.error(f"Processing Error: {e}")
        return None

# --- TABS ---
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
    st.header("Channel Performance Upload")
    engine = get_engine()
    
    customer_options = []
    if engine:
        try:
            customer_options = pd.read_sql("SELECT customer_name FROM dim_customers", engine)['customer_name'].tolist()
        except: pass

    if not customer_options:
        st.warning("Please add a customer in the 'Customer Master' tab first.")
    else:
        # Date Selection (Month & Year Only)
        col_c, col_m, col_y = st.columns([2, 1, 1])
        target_cust = col_c.selectbox("Select Customer", customer_options)
        
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        sel_month = col_m.selectbox("Month", months, index=datetime.datetime.now().month - 1)
        sel_year = col_y.selectbox("Year", range(2024, 2031), index=2) 
        
        report_date = datetime.datetime.strptime(f"{sel_month} {sel_year}", "%b %Y").date()
        
        file = st.file_uploader("Upload Master Excel", type="xlsx")
        
        if file:
            results = process_master_file(file)
            if results is not None:
                results['customer'] = target_cust
                results['month'] = pd.to_datetime(report_date)
                
                st.markdown("---")
                
                # --- SUMMARY METRIC ROW ---
                st.subheader(f"Monthly Summary: {target_cust} ({sel_month} {sel_year})")
                m1, m2, m3, m4 = st.columns(4)
                
                total_sales = results['Sales_Qty'].sum()
                avg_velocity = results['velocity'].mean()
                top_city = results.groupby('City')['Sales_Qty'].sum().idxmax()
                total_machines = results['Machine_Count'].nunique() if 'City' in results.columns else 0 # Approximation based on data rows
                
                m1.metric("Total Sales (Units)", f"{total_sales:,.0f}")
                m2.metric("Avg Velocity", f"{avg_velocity:.2f}")
                m3.metric("Top City", top_city)
                m4.metric("Active SKUs", len(results))

                # --- SPLIT VIEW: INVENTORY ---
                st.markdown("### 📦 Inventory Levels")
                st.caption("Focus: Stock health, Sell-through rate (STR), and replenishment needs.")
                inv_cols = ['City', 'Product', 'Total_SOH', 'str_pct', 'days_of_cover', 'movement_bucket']
                inv_view = results[inv_cols]
                try:
                    st.dataframe(inv_view.style.background_gradient(subset=['str_pct'], cmap='RdYlGn'), use_container_width=True)
                except: st.dataframe(inv_view, use_container_width=True)

                # --- SPLIT VIEW: MACHINE ---
                st.markdown("### 🤖 Machine Performance")
                st.caption("Focus: Sales output per machine and SKU classification.")
                mach_cols = ['City', 'Product', 'Sales_Qty', 'Machine_Count', 'velocity', 'abc_class']
                mach_view = results[mach_cols]
                try:
                    st.dataframe(mach_view.style.background_gradient(subset=['velocity'], cmap='YlGn'), use_container_width=True)
                except: st.dataframe(mach_view, use_container_width=True)
                
                if st.button("Confirm & Save to Neon DB"):
                    results.to_sql('vending_performance', engine, if_exists='append', index=False)
                    st.success("Data successfully archived!")

# --- TAB 3: TREND ANALYSIS ---
with t3:
    st.header("Historical Trends")
    engine = get_engine()
    if engine:
        try:
            hist = pd.read_sql("SELECT * FROM vending_performance ORDER BY month", engine)
            if not hist.empty:
                c_sel = st.selectbox("Select Customer", hist['customer'].unique(), key="trend_c")
                city_sel = st.selectbox("Select City", hist[hist['customer']==c_sel]['city'].unique())
                
                plot_df = hist[(hist['customer']==c_sel) & (hist['city']==city_sel)]
                fig = px.line(plot_df, x='month', y='velocity', color='product', markers=True, title="Velocity Trend")
                st.plotly_chart(fig, use_container_width=True)
            else: st.info("No data available.")
        except: st.write("Waiting for data upload...")

# --- TAB 4: ADMIN ---
with t4:
    st.header("History Management")
    engine = get_engine()
    if engine:
        if st.button("Delete All Records", type="primary"):
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM vending_performance"))
                conn.commit()
            st.rerun()
        
        try:
            full = pd.read_sql("SELECT * FROM vending_performance", engine)
            st.dataframe(full)
        except: st.write("Empty database.")
