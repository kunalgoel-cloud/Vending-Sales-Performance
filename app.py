import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from sqlalchemy import create_engine, text
import datetime

# --- APP CONFIG ---
st.set_page_config(page_title="Vending Performance Hub", layout="wide")

# --- DB CONNECTION (AUTOMATED VIA SECRETS) ---
def get_engine():
    try:
        # Pulls from .streamlit/secrets.toml or Cloud Secrets
        url = st.secrets["DB_URL"]
        return create_engine(url)
    except Exception:
        st.error("Database Secret 'DB_URL' not found. Check your secrets.toml.")
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

        df['str_pct'] = (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100).fillna(0)
        df['velocity'] = df['Sales_Qty'] / df['Machine_Count']
        df['days_of_cover'] = np.where(df['Sales_Qty'] > 0, df['Total_SOH'] / (df['Sales_Qty'] / 30), 999)

        c_list = [
            (df['str_pct'] > 40) & (df['days_of_cover'] < 10),
            (df['days_of_cover'] > 45),
            (df['Sales_Qty'] == 0) & (df['Total_SOH'] > 0)
        ]
        df['movement_bucket'] = np.select(c_list, ['Fast Mover', 'Slow Mover', 'Liquidate'], default='Steady')
        
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

    st.subheader("Active Customer List")
    engine = get_engine()
    if engine:
        try:
            cust_df = pd.read_sql("SELECT * FROM dim_customers", engine)
            st.dataframe(cust_df, use_container_width=True)
        except: st.info("No customers registered yet.")

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
        col1, col2 = st.columns(2)
        target_cust = col1.selectbox("Select Customer", customer_options)
        target_month = col2.date_input("Reporting Month", datetime.date.today().replace(day=1))
        
        file = st.file_uploader("Upload Multi-Sheet Excel", type="xlsx")
        
        if file:
            results = process_master_file(file)
            if results is not None:
                results['customer'] = target_cust
                results['month'] = pd.to_datetime(target_month)
                
                st.write(f"### Preview: {target_cust} - {target_month.strftime('%B %Y')}")
                
                # Safe Styling: Checks for matplotlib before applying gradient
                try:
                    st.dataframe(results.style.background_gradient(subset=['velocity', 'str_pct'], cmap='RdYlGn'))
                except ImportError:
                    st.dataframe(results)
                    st.info("Tip: Install 'matplotlib' to see color-coded performance gradients.")
                
                if st.button("Confirm & Save to History"):
                    results.to_sql('vending_performance', engine, if_exists='append', index=False)
                    st.success("Data Archived!")

# --- TAB 3: TREND ANALYSIS ---
with t3:
    st.header("Historical Analysis")
    engine = get_engine()
    if engine:
        try:
            hist = pd.read_sql("SELECT * FROM vending_performance ORDER BY month", engine)
            if not hist.empty:
                c_sel = st.selectbox("Select Customer", hist['customer'].unique(), key="trend_c")
                city_sel = st.selectbox("Select City", hist[hist['customer']==c_sel]['city'].unique())
                
                plot_df = hist[(hist['customer']==c_sel) & (hist['city']==city_sel)]
                fig = px.line(plot_df, x='month', y='velocity', color='product', markers=True, title=f"Machine Velocity Trend - {city_sel}")
                st.plotly_chart(fig, use_container_width=True)
            else: st.info("No historical data found.")
        except: st.info("Upload data to see trends.")

# --- TAB 4: ADMIN ---
with t4:
    st.header("History Management")
    engine = get_engine()
    if engine:
        col_purge, col_export = st.columns(2)
        if col_purge.button("Purge All Sales History", type="primary"):
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM vending_performance"))
                conn.commit()
            st.rerun()
        
        try:
            full = pd.read_sql("SELECT * FROM vending_performance", engine)
            col_export.download_button("Export History to CSV", full.to_csv(index=False), "vending_history.csv")
            st.dataframe(full)
        except: st.write("No records.")
