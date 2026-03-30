import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from sqlalchemy import create_engine
import datetime

# --- CONFIGURATION & DB CONNECTION ---
st.set_page_config(page_title="Vending Performance Analytics", layout="wide")
# Neon Tech Connection String (Replace with your actual credentials or use st.secrets)
DB_URL = "postgresql://user:password@endpoint.neon.tech/neondb" 

@st.cache_resource
def get_engine():
    return create_engine(DB_URL)

# --- HELPER FUNCTIONS ---
def clean_vending_data(sales_df, soh_df, machine_df):
    """Specific cleaning logic for Mama Nourish format"""
    # Clean Sales
    sales = sales_df.iloc[1:, 0:3].copy()
    sales.columns = ['City', 'Product', 'Sales_Qty']
    sales['Sales_Qty'] = pd.to_numeric(sales['Sales_Qty'], errors='coerce').fillna(0)
    
    # Clean SOH
    soh = soh_df.iloc[1:, [0, 1, 4]].copy()
    soh.columns = ['Location', 'Product', 'Total_SOH']
    soh['Total_SOH'] = pd.to_numeric(soh['Total_SOH'], errors='coerce').fillna(0)
    # Extract City from Location (e.g., 'Bangalore Zone 3' -> 'Bangalore')
    soh['City'] = soh['Location'].str.split(' ').str[0]
    soh_agg = soh.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

    # Clean Machine Placement
    machines = machine_df.iloc[1:, 0:3].copy()
    machines.columns = ['City', 'Product', 'Machine_Count']
    machines['Machine_Count'] = pd.to_numeric(machines['Machine_Count'], errors='coerce').fillna(1)

    # Merge Data
    merged = pd.merge(sales, soh_agg, on=['City', 'Product'], how='left')
    merged = pd.merge(merged, machines, on=['City', 'Product'], how='left')
    return merged.fillna(0)

def calculate_metrics(df, days=30):
    # 1. Sell Through Rate
    df['STR'] = (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH'])) * 100
    
    # 2. Machine Velocity
    df['Velocity'] = df['Sales_Qty'] / df['Machine_Count']
    
    # 3. Days of Cover
    df['Daily_Sales'] = df['Sales_Qty'] / days
    df['DOC'] = np.where(df['Daily_Sales'] > 0, df['Total_SOH'] / df['Daily_Sales'], 999)

    # 4. Bucketing Logic
    conditions = [
        (df['STR'] > 25) & (df['DOC'] < 15),
        (df['DOC'] > 45) | (df['STR'] < 5),
        (df['Sales_Qty'] == 0) & (df['Total_SOH'] > 0)
    ]
    choices = ['Fast Mover', 'Slow Mover', 'Liquidate']
    df['Movement_Bucket'] = np.select(conditions, choices, default='Steady')

    # 5. ABC Classification (Velocity)
    df['Rank'] = df['Velocity'].rank(pct=True)
    df['ABC_Class'] = np.where(df['Rank'] > 0.8, 'A', np.where(df['Rank'] > 0.5, 'B', 'C'))
    
    return df

# --- UI TABS ---
tab1, tab2, tab3, tab4 = st.tabs(["Upload & Analyze", "Customer Definitions", "History & Trends", "Admin & Backup"])

with tab1:
    st.header("Monthly Performance Upload")
    col1, col2, col3 = st.columns(3)
    f_sales = col1.file_uploader("Upload Sales Summary", type="csv")
    f_soh = col2.file_uploader("Upload SOH Data", type="csv")
    f_mach = col3.file_uploader("Upload Machine Placement", type="csv")
    
    target_month = st.date_input("Select Month for Data", datetime.date.today().replace(day=1))

    if f_sales and f_soh and f_mach:
        raw_sales = pd.read_csv(f_sales)
        raw_soh = pd.read_csv(f_soh)
        raw_mach = pd.read_csv(f_mach)
        
        processed_data = clean_vending_data(raw_sales, raw_soh, raw_mach)
        final_df = calculate_metrics(processed_data)
        final_df['Month'] = target_month
        
        st.subheader(f"Analysis for {target_month.strftime('%B %Y')}")
        st.dataframe(final_df.style.background_gradient(subset=['STR', 'Velocity'], cmap='RdYlGn'))
        
        if st.button("Save to Neon DB"):
            try:
                engine = get_engine()
                final_df.to_sql('vending_performance', engine, if_exists='append', index=False)
                st.success("Data successfully pushed to Neon Tech!")
            except Exception as e:
                st.error(f"Database Error: {e}")

with tab3:
    st.header("Historical Trend Analysis")
    # Fetch data from DB
    try:
        engine = get_engine()
        history_df = pd.read_sql("SELECT * FROM vending_performance", engine)
        
        cities = history_df['City'].unique()
        selected_city = st.selectbox("Select City for Trend Analysis", cities)
        
        city_data = history_df[history_df['City'] == selected_city].sort_values('Month')
        
        fig = px.line(city_data, x='Month', y='Velocity', color='Product', title=f"Machine Velocity Trend - {selected_city}")
        st.plotly_chart(fig, use_container_width=True)
        
    except:
        st.info("No historical data found in database. Please upload data first.")

# (Remaining Tabs for Customer Defs and Admin would follow similar CRUD logic)
