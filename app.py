import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from sqlalchemy import create_engine, text
import datetime

# --- CONFIGURATION ---
st.set_page_config(page_title="Vending Channel Analytics", layout="wide")

# --- DB CONNECTION ---
st.sidebar.header("Database Config")
DB_URL = st.sidebar.text_input("Neon DB URL", type="password")

@st.cache_resource
def get_engine(url):
    if not url: return None
    return create_engine(url)

# --- PROCESSING LOGIC ---
def clean_and_process(uploaded_file):
    try:
        all_sheets = pd.read_excel(uploaded_file, sheet_name=None)
        sheet_map = {k.lower().strip(): k for k in all_sheets.keys()}
        
        # 1. Extraction
        sales_df = all_sheets.get(sheet_map.get('sales summary'))
        soh_df = all_sheets.get(sheet_map.get('soh'))
        mach_df = all_sheets.get(sheet_map.get('machine placement'))

        if any(x is None for x in [sales_df, soh_df, mach_df]):
            st.error("Ensure sheets: 'Sales Summary', 'SOH', and 'Machine Placement' exist.")
            return None

        # 2. Cleaning (Matching your uploaded file structure)
        sales = sales_df.iloc[1:, 0:3].copy()
        sales.columns = ['City', 'Product', 'Sales_Qty']
        
        soh = soh_df.iloc[1:, [0, 1, 4]].copy()
        soh.columns = ['Location', 'Product', 'Total_SOH']
        soh['City'] = soh['Location'].astype(str).str.split(' ').str[0]
        soh_agg = soh.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

        mach = mach_df.iloc[1:, 0:3].copy()
        mach.columns = ['City', 'Product', 'Machine_Count']

        # 3. Merging
        df = pd.merge(sales, soh_agg, on=['City', 'Product'], how='outer')
        df = pd.merge(df, mach, on=['City', 'Product'], how='left')
        
        # Numeric Conversion
        cols = ['Sales_Qty', 'Total_SOH', 'Machine_Count']
        for col in cols:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0 if col != 'Machine_Count' else 1)
        df['Machine_Count'] = df['Machine_Count'].replace(0, 1)

        # 4. Calculations
        df['str_pct'] = (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100).replace([np.inf, -np.inf], 0).fillna(0)
        df['velocity'] = df['Sales_Qty'] / df['Machine_Count']
        df['days_of_cover'] = np.where(df['Sales_Qty'] > 0, df['Total_SOH'] / (df['Sales_Qty'] / 30), 999)

        # 5. Bucketing & ABC
        conds = [(df['str_pct'] > 40) & (df['days_of_cover'] < 10), (df['days_of_cover'] > 45), (df['Sales_Qty'] == 0)]
        df['movement_bucket'] = np.select(conds, ['Fast Mover', 'Slow Mover', 'Liquidate'], default='Steady')
        
        df['rank'] = df['velocity'].rank(pct=True)
        df['abc_class'] = np.where(df['rank'] > 0.8, 'A', np.where(df['rank'] > 0.5, 'B', 'C'))
        
        return df.drop(columns=['rank'])
    except Exception as e:
        st.error(f"Error: {e}")
        return None

# --- UI TABS ---
tab1, tab2, tab3, tab4 = st.tabs(["Upload Data", "Define Customers", "View Trends", "Data History"])

# --- TAB 1: UPLOAD ---
with tab1:
    st.header("Monthly Channel Analysis")
    engine = get_engine(DB_URL)
    
    # Load Customers for Dropdown
    cust_options = ["Add Customer First"]
    if engine:
        try:
            cust_options = pd.read_sql("SELECT customer_name FROM dim_customers", engine)['customer_name'].tolist()
        except: pass

    col_a, col_b = st.columns(2)
    selected_cust = col_a.selectbox("Select Customer", cust_options)
    report_date = col_b.date_input("Reporting Month", datetime.date.today().replace(day=1))
    
    upl = st.file_uploader("Upload Master Excel Workbook", type="xlsx")
    
    if upl:
        processed_df = clean_and_process(upl)
        if processed_df is not None:
            processed_df['customer'] = selected_cust
            processed_df['month'] = pd.to_datetime(report_date)
            
            st.subheader(f"Results: {selected_cust} ({report_date.strftime('%B %Y')})")
            st.dataframe(processed_df.style.background_gradient(subset=['velocity', 'str_pct'], cmap='YlGn'))
            
            if st.button("Save to Neon DB"):
                if engine:
                    processed_df.to_sql('vending_performance', engine, if_exists='append', index=False)
                    st.success("Successfully archived to history.")
                else: st.error("No DB Connection.")

# --- TAB 2: DEFINE CUSTOMERS ---
with tab2:
    st.header("Customer Management")
    with st.form("new_cust"):
        c_name = st.text_input("Customer Name")
        c_reg = st.text_input("Region")
        if st.form_submit_button("Register Customer"):
            engine = get_engine(DB_URL)
            if engine:
                with engine.connect() as conn:
                    conn.execute(text(f"INSERT INTO dim_customers (customer_name, region) VALUES ('{c_name}', '{c_reg}')"))
                    st.success("Customer Registered!")

# --- TAB 3: TRENDS ---
with tab3:
    st.header("Performance Trends")
    engine = get_engine(DB_URL)
    if engine:
        try:
            hist = pd.read_sql("SELECT * FROM vending_performance", engine)
            hist['month'] = pd.to_datetime(hist['month'])
            c_sel = st.selectbox("Filter Customer", hist['customer'].unique())
            city_sel = st.selectbox("Filter City", hist[hist['customer'] == c_sel]['city'].unique())
            
            plot_data = hist[(hist['customer'] == c_sel) & (hist['city'] == city_sel)]
            fig = px.line(plot_data, x='month', y='velocity', color='product', markers=True, title="Machine Velocity Over Time")
            st.plotly_chart(fig, use_container_width=True)
        except: st.info("Upload data to see trends.")

# --- TAB 4: HISTORY & BACKUP ---
with tab4:
    st.header("Data Management")
    engine = get_engine(DB_URL)
    if engine:
        if st.button("Clear All History"):
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM vending_performance"))
                st.warning("All history cleared.")
        
        try:
            full_data = pd.read_sql("SELECT * FROM vending_performance", engine)
            st.download_button("Download Full Backup (CSV)", full_data.to_csv(index=False), "vending_backup.csv")
            st.dataframe(full_data)
        except: st.write("Database empty.")
