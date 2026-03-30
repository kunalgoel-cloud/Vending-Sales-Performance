import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from sqlalchemy import create_engine
import datetime
import io

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

# --- FILE LOADING HELPER ---
def load_data(uploaded_file):
    """Helper to read both CSV and Excel formats"""
    if uploaded_file is None:
        return None
    
    file_details = uploaded_file.name.split('.')[-1].lower()
    
    if file_details == 'csv':
        return pd.read_csv(uploaded_file)
    elif file_details in ['xlsx', 'xls']:
        return pd.read_excel(uploaded_file)
    return None

# --- CORE PROCESSING LOGIC ---
def clean_mama_nourish_data(sales_df, soh_df, machine_df):
    try:
        # 1. Clean Sales Summary
        sales = sales_df.iloc[1:, 0:3].copy()
        sales.columns = ['City', 'Product', 'Sales_Qty']
        sales['Sales_Qty'] = pd.to_numeric(sales['Sales_Qty'], errors='coerce').fillna(0)
        
        # 2. Clean SOH (Stock on Hand)
        # Note: In Excel/CSV, SOH usually has Location in Col 0, Product in Col 1, Total Stock in Col 4
        soh = soh_df.iloc[1:, [0, 1, 4]].copy()
        soh.columns = ['Location', 'Product', 'Total_SOH']
        soh['Total_SOH'] = pd.to_numeric(soh['Total_SOH'], errors='coerce').fillna(0)
        soh['City'] = soh['Location'].astype(str).str.split(' ').str[0]
        soh_agg = soh.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

        # 3. Clean Machine Placement
        machines = machine_df.iloc[1:, 0:3].copy()
        machines.columns = ['City', 'Product', 'Machine_Count']
        machines['Machine_Count'] = pd.to_numeric(machines['Machine_Count'], errors='coerce').fillna(1)
        machines['Machine_Count'] = machines['Machine_Count'].apply(lambda x: 1 if x <= 0 else x)

        # 4. Merge Data
        merged = pd.merge(sales, soh_agg, on=['City', 'Product'], how='outer')
        merged = pd.merge(merged, machines, on=['City', 'Product'], how='left')
        
        merged['Sales_Qty'] = merged['Sales_Qty'].fillna(0)
        merged['Total_SOH'] = merged['Total_SOH'].fillna(0)
        merged['Machine_Count'] = merged['Machine_Count'].fillna(1)
        
        return merged
    except Exception as e:
        st.error(f"Error cleaning data: {e}. Please check if the file format matches the 'Mama Nourish' template.")
        return None

def calculate_metrics(df, days_in_month=30):
    # STR: Sales / (Sales + SOH)
    df['STR_%'] = (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100).fillna(0)
    
    # Velocity: Sales / Machines
    df['Velocity'] = df['Sales_Qty'] / df['Machine_Count']
    
    # Days of Cover
    daily_sales = df['Sales_Qty'] / days_in_month
    df['Days_of_Cover'] = np.where(daily_sales > 0, df['Total_SOH'] / daily_sales, 999)
    
    # Movement Bucketing
    conditions = [
        (df['STR_%'] > 40) & (df['Days_of_Cover'] < 10),
        (df['Days_of_Cover'] > 45),
        (df['Sales_Qty'] == 0) & (df['Total_SOH'] > 0)
    ]
    choices = ['Fast Mover', 'Slow Mover', 'Liquidate']
    df['Movement_Bucket'] = np.select(conditions, choices, default='Steady')
    
    # ABC Class
    df['Velocity_Rank'] = df['Velocity'].rank(pct=True)
    df['ABC_Class'] = np.where(df['Velocity_Rank'] > 0.8, 'A',
                               np.where(df['Velocity_Rank'] > 0.5, 'B', 'C'))
    
    return df

# --- UI TABS ---
tab1, tab2, tab3, tab4 = st.tabs(["Monthly Analysis", "Customer Master", "History & Trends", "Data Management"])

# --- TAB 1: UPLOAD & ANALYZE ---
with tab1:
    st.header("Monthly Performance Upload")
    st.write("Upload CSV or Excel files for the current month.")
    
    c1, c2, c3 = st.columns(3)
    # Updated allowed types
    f_sales = c1.file_uploader("Sales Summary", type=["csv", "xlsx"])
    f_soh = c2.file_uploader("SOH Data", type=["csv", "xlsx"])
    f_mach = c3.file_uploader("Machine Placement", type=["csv", "xlsx"])
    
    report_month = st.date_input("Reporting Month", datetime.date.today().replace(day=1))

    if f_sales and f_soh and f_mach:
        df_sales = load_data(f_sales)
        df_soh = load_data(f_soh)
        df_mach = load_data(f_mach)
        
        raw_processed = clean_mama_nourish_data(df_sales, df_soh, df_mach)
        
        if raw_processed is not None:
            final_metrics = calculate_metrics(raw_processed)
            final_metrics['Month'] = pd.to_datetime(report_month)
            
            st.subheader(f"Analysis Summary - {report_month.strftime('%B %Y')}")
            
            m1, m2, m3 = st.columns(3)
            m1.metric("Avg Velocity", f"{final_metrics['Velocity'].mean():.2f}")
            m2.metric("Avg STR %", f"{final_metrics['STR_%'].mean():.1f}%")
            m3.metric("Stock Value (Units)", f"{final_metrics['Total_SOH'].sum():,.0f}")
            
            st.dataframe(final_metrics.drop(columns=['Velocity_Rank']).style.background_gradient(subset=['STR_%', 'Velocity'], cmap='RdYlGn'))
            
            if st.button("Commit to Neon Database"):
                engine = get_engine(DB_URL)
                if engine:
                    try:
                        final_metrics.to_sql('vending_performance', engine, if_exists='append', index=False)
                        st.success("Data successfully archived!")
                    except Exception as e:
                        st.error(f"DB Error: {e}")
                else:
                    st.error("Please enter a database URL in the sidebar.")

# --- TAB 3: HISTORY & TRENDS ---
with tab3:
    st.header("Historical Trend Analysis")
    engine = get_engine(DB_URL)
    if engine:
        try:
            history = pd.read_sql("SELECT * FROM vending_performance ORDER BY \"Month\" ASC", engine)
            history['Month'] = pd.to_datetime(history['Month'])
            
            selected_city = st.selectbox("Select City", history['City'].unique())
            city_hist = history[history['City'] == selected_city]
            
            fig = px.line(city_hist, x='Month', y='Velocity', color='Product', 
                          title=f"Machine Velocity Trend: {selected_city}", markers=True)
            st.plotly_chart(fig, use_container_width=True)
            
        except:
            st.info("Upload data and commit to DB to see trends.")
    else:
        st.info("Connect to database via sidebar to view history.")

# --- TAB 4: DATA MANAGEMENT ---
with tab4:
    st.header("Admin Controls")
    if st.button("Export All Data (CSV)"):
        engine = get_engine(DB_URL)
        if engine:
            history = pd.read_sql("SELECT * FROM vending_performance", engine)
            st.download_button("Download Now", history.to_csv(index=False), "backup.csv")
            
    if st.button("Purge All Records", type="primary"):
        engine = get_engine(DB_URL)
        if engine:
            with engine.connect() as conn:
                conn.execute("DROP TABLE IF EXISTS vending_performance")
                st.warning("Database reset.")
