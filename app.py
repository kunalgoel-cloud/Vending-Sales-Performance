import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from sqlalchemy import create_engine
import datetime

# --- CONFIGURATION ---
st.set_page_config(page_title="Vending Performance Analytics", layout="wide")

# --- DATABASE CONNECTION ---
st.sidebar.header("Database Configuration")
DB_URL = st.sidebar.text_input("Neon DB URL", type="password", help="postgresql://user:password@endpoint.neon.tech/neondb")

@st.cache_resource
def get_engine(url):
    if not url:
        return None
    return create_engine(url)

# --- CORE PROCESSING LOGIC ---
def process_single_workbook(uploaded_file):
    try:
        # Load all sheets into a dictionary
        all_sheets = pd.read_excel(uploaded_file, sheet_name=None)
        
        # Identify sheets (case-insensitive search)
        sheet_keys = {k.lower().strip(): k for k in all_sheets.keys()}
        
        sales_df = all_sheets.get(sheet_keys.get('sales summary'))
        soh_df = all_sheets.get(sheet_keys.get('soh'))
        mach_df = all_sheets.get(sheet_keys.get('machine placement'))

        if sales_df is None or soh_df is None or mach_df is None:
            st.error("Missing required sheets! Ensure your file has: 'Sales Summary', 'SOH', and 'Machine Placement'")
            return None

        # 1. Clean Sales Summary
        sales = sales_df.iloc[1:, 0:3].copy()
        sales.columns = ['City', 'Product', 'Sales_Qty']
        sales['Sales_Qty'] = pd.to_numeric(sales['Sales_Qty'], errors='coerce').fillna(0)
        
        # 2. Clean SOH
        soh = soh_df.iloc[1:, [0, 1, 4]].copy()
        soh.columns = ['Location', 'Product', 'Total_SOH']
        soh['Total_SOH'] = pd.to_numeric(soh['Total_SOH'], errors='coerce').fillna(0)
        soh['City'] = soh['Location'].astype(str).str.split(' ').str[0]
        soh_agg = soh.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

        # 3. Clean Machine Placement
        machines = mach_df.iloc[1:, 0:3].copy()
        machines.columns = ['City', 'Product', 'Machine_Count']
        machines['Machine_Count'] = pd.to_numeric(machines['Machine_Count'], errors='coerce').fillna(1)
        machines['Machine_Count'] = machines['Machine_Count'].replace(0, 1)

        # 4. Merge
        merged = pd.merge(sales, soh_agg, on=['City', 'Product'], how='outer')
        merged = pd.merge(merged, machines, on=['City', 'Product'], how='left')
        
        return merged.fillna({'Sales_Qty': 0, 'Total_SOH': 0, 'Machine_Count': 1})
    
    except Exception as e:
        st.error(f"Processing Error: {e}")
        return None

def calculate_metrics(df, days_in_month=30):
    df['STR_%'] = (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100).fillna(0)
    df['Velocity'] = df['Sales_Qty'] / df['Machine_Count']
    daily_sales = df['Sales_Qty'] / days_in_month
    df['Days_of_Cover'] = np.where(daily_sales > 0, df['Total_SOH'] / daily_sales, 999)
    
    # Bucketing
    conditions = [
        (df['STR_%'] > 40) & (df['Days_of_Cover'] < 10),
        (df['Days_of_Cover'] > 45),
        (df['Sales_Qty'] == 0) & (df['Total_SOH'] > 0)
    ]
    choices = ['Fast Mover', 'Slow Mover', 'Liquidate']
    df['Movement_Bucket'] = np.select(conditions, choices, default='Steady')
    
    # ABC Class
    df['Rank'] = df['Velocity'].rank(pct=True)
    df['ABC_Class'] = np.where(df['Rank'] > 0.8, 'A', np.where(df['Rank'] > 0.5, 'B', 'C'))
    
    return df

# --- UI TABS ---
tab1, tab2, tab3, tab4 = st.tabs(["Monthly Upload", "Customers", "Trends", "Admin"])

with tab1:
    st.header("Upload Monthly Workbook")
    uploaded_file = st.file_uploader("Upload Mama Nourish Master Excel", type=["xlsx"])
    report_month = st.date_input("Reporting Month", datetime.date.today().replace(day=1))

    if uploaded_file:
        data = process_single_workbook(uploaded_file)
        if data is not None:
            final_df = calculate_metrics(data)
            final_df['Month'] = pd.to_datetime(report_month)
            
            st.subheader(f"Analysis for {report_month.strftime('%B %Y')}")
            st.dataframe(final_df.drop(columns=['Rank']).style.background_gradient(subset=['STR_%', 'Velocity'], cmap='RdYlGn'))
            
            if st.button("Archive to Neon DB"):
                engine = get_engine(DB_URL)
                if engine:
                    final_df.to_sql('vending_performance', engine, if_exists='append', index=False)
                    st.success("Archived successfully.")
                else:
                    st.error("Please provide DB URL in sidebar.")

with tab3:
    st.header("Historical Trend Lines")
    engine = get_engine(DB_URL)
    if engine:
        try:
            history = pd.read_sql("SELECT * FROM vending_performance ORDER BY \"Month\" ASC", engine)
            history['Month'] = pd.to_datetime(history['Month'])
            
            city = st.selectbox("Select Region/City", history['City'].unique())
            city_data = history[history['City'] == city]
            
            fig = px.line(city_data, x='Month', y='Velocity', color='Product', markers=True, 
                          title=f"SKU Velocity Trend in {city}")
            st.plotly_chart(fig, use_container_width=True)
        except:
            st.info("No data in DB yet.")

with tab4:
    st.header("Maintenance")
    if st.button("Download Data Backup"):
        engine = get_engine(DB_URL)
        if engine:
            df = pd.read_sql("SELECT * FROM vending_performance", engine)
            st.download_button("Download CSV", df.to_csv(index=False), "backup.csv")
    
    if st.button("Clear History", type="primary"):
        engine = get_engine(DB_URL)
        if engine:
            with engine.connect() as conn:
                conn.execute("DROP TABLE IF EXISTS vending_performance")
                st.success("History cleared.")
