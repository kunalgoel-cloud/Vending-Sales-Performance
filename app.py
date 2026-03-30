import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from sqlalchemy import create_engine
import datetime

# --- CONFIGURATION ---
st.set_page_config(page_title="Vending Performance Analytics", layout="wide")

# --- DATABASE CONNECTION ---
# Replace with your actual Neon Tech connection string or use st.secrets
# Format: postgresql://user:password@endpoint.neon.tech/neondb
DB_URL = st.sidebar.text_input("Neon DB URL", type="password", help="Enter your Neon Tech connection string")

def get_engine():
    if not DB_URL:
        return None
    return create_engine(DB_URL)

# --- CORE PROCESSING LOGIC ---
def clean_mama_nourish_data(sales_df, soh_df, machine_df):
    """
    Cleans the specific Mama Nourish file format derived from the provided CSVs.
    """
    try:
        # 1. Clean Sales Summary
        # Header is in the first row after the title
        sales = sales_df.iloc[1:, 0:3].copy()
        sales.columns = ['City', 'Product', 'Sales_Qty']
        sales['Sales_Qty'] = pd.to_numeric(sales['Sales_Qty'], errors='coerce').fillna(0)
        
        # 2. Clean SOH (Stock on Hand)
        soh = soh_df.iloc[1:, [0, 1, 4]].copy()
        soh.columns = ['Location', 'Product', 'Total_SOH']
        soh['Total_SOH'] = pd.to_numeric(soh['Total_SOH'], errors='coerce').fillna(0)
        # Extract City from Location (assuming City is the first word)
        soh['City'] = soh['Location'].str.split(' ').str[0]
        soh_agg = soh.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

        # 3. Clean Machine Placement
        machines = machine_df.iloc[1:, 0:3].copy()
        machines.columns = ['City', 'Product', 'Machine_Count']
        machines['Machine_Count'] = pd.to_numeric(machines['Machine_Count'], errors='coerce').fillna(1)
        # Ensure count is at least 1 to avoid division by zero
        machines['Machine_Count'] = machines['Machine_Count'].apply(lambda x: 1 if x <= 0 else x)

        # 4. Merge Data
        merged = pd.merge(sales, soh_agg, on=['City', 'Product'], how='outer')
        merged = pd.merge(merged, machines, on=['City', 'Product'], how='left')
        
        # Fill missing values for products that might exist in SOH but not Sales, or vice versa
        merged['Sales_Qty'] = merged['Sales_Qty'].fillna(0)
        merged['Total_SOH'] = merged['Total_SOH'].fillna(0)
        merged['Machine_Count'] = merged['Machine_Count'].fillna(1)
        
        return merged
    except Exception as e:
        st.error(f"Error cleaning data: {e}")
        return None

def calculate_metrics(df, days_in_month=30):
    """
    Calculates Sell Through, Velocity, DOC, and performs Bucketing.
    """
    # STR: Sales / (Sales + SOH)
    df['STR_%'] = (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100).fillna(0)
    
    # Velocity: Sales / Machines
    df['Velocity'] = df['Sales_Qty'] / df['Machine_Count']
    
    # Days of Cover: SOH / (Sales / Days)
    daily_sales = df['Sales_Qty'] / days_in_month
    df['Days_of_Cover'] = np.where(daily_sales > 0, df['Total_SOH'] / daily_sales, 999)
    
    # --- Movement Bucketing ---
    conditions = [
        (df['STR_%'] > 40) & (df['Days_of_Cover'] < 10), # Fast
        (df['Days_of_Cover'] > 45),                     # Slow
        (df['Sales_Qty'] == 0) & (df['Total_SOH'] > 0)  # Liquidate
    ]
    choices = ['Fast Mover', 'Slow Mover', 'Liquidate']
    df['Movement_Bucket'] = np.select(conditions, choices, default='Steady')
    
    # --- ABC Classification (by Velocity) ---
    df['Velocity_Rank'] = df['Velocity'].rank(pct=True)
    df['ABC_Class'] = np.where(df['Velocity_Rank'] > 0.8, 'A',
                               np.where(df['Velocity_Rank'] > 0.5, 'B', 'C'))
    
    return df

# --- UI TABS ---
tab1, tab2, tab3, tab4 = st.tabs(["Monthly Analysis", "Customer Master", "History & Trends", "Data Management"])

# --- TAB 1: UPLOAD & ANALYZE ---
with tab1:
    st.header("Upload Monthly Channel Data")
    st.info("Please upload the specific CSV files for Mama Nourish performance.")
    
    c1, c2, c3 = st.columns(3)
    f_sales = c1.file_uploader("Upload 'Sales Summary' CSV", type="csv")
    f_soh = c2.file_uploader("Upload 'SOH' CSV", type="csv")
    f_mach = c3.file_uploader("Upload 'Machine Placement' CSV", type="csv")
    
    report_month = st.date_input("Reporting Month", datetime.date.today().replace(day=1))

    if f_sales and f_soh and f_mach:
        df_sales = pd.read_csv(f_sales)
        df_soh = pd.read_csv(f_soh)
        df_mach = pd.read_csv(f_mach)
        
        raw_processed = clean_mama_nourish_data(df_sales, df_soh, df_mach)
        
        if raw_processed is not None:
            final_metrics = calculate_metrics(raw_processed)
            final_metrics['Month'] = report_month
            
            st.subheader(f"Performance Metrics - {report_month.strftime('%B %Y')}")
            
            # Display Summary Metrics
            m1, m2, m3 = st.columns(3)
            m1.metric("Avg Velocity", f"{final_metrics['Velocity'].mean():.2f}")
            m2.metric("Avg STR %", f"{final_metrics['STR_%'].mean():.1f}%")
            m3.metric("Total Sales", f"{final_metrics['Sales_Qty'].sum():,.0f}")
            
            st.dataframe(final_metrics.drop(columns=['Velocity_Rank']).style.background_gradient(subset=['STR_%', 'Velocity'], cmap='Greens'))
            
            if st.button("Save Data to History (Neon DB)"):
                engine = get_engine()
                if engine:
                    final_metrics.to_sql('vending_performance', engine, if_exists='append', index=False)
                    st.success("Data successfully pushed to Neon Tech!")
                else:
                    st.error("Please provide a valid DB URL in the sidebar.")

# --- TAB 2: CUSTOMER DEFINITIONS ---
with tab2:
    st.header("Customer & Channel Master")
    st.write("Define regions, channel names, and machine types here.")
    # Placeholder for a CRUD editor for customers
    customer_data = pd.DataFrame({
        "Customer_ID": ["CUST001"],
        "Channel": ["Mama Nourish"],
        "Region": ["Pan India"],
        "Contact": ["Operations Team"]
    })
    st.data_editor(customer_data, num_rows="dynamic")

# --- TAB 3: HISTORY & TRENDS ---
with tab3:
    st.header("Historic Performance Trends")
    engine = get_engine()
    if engine:
        try:
            history = pd.read_sql("SELECT * FROM vending_performance ORDER BY \"Month\" ASC", engine)
            history['Month'] = pd.to_datetime(history['Month'])
            
            filter_city = st.multiselect("Filter Cities", history['City'].unique(), default=history['City'].unique())
            plot_df = history[history['City'].isin(filter_city)]
            
            st.subheader("Velocity Trend over Time")
            fig = px.line(plot_df.groupby(['Month', 'City'])['Velocity'].mean().reset_index(), 
                          x='Month', y='Velocity', color='City', markers=True)
            st.plotly_chart(fig, use_container_width=True)
            
            st.subheader("STR % Trend")
            fig2 = px.bar(plot_df.groupby(['Month', 'ABC_Class'])['STR_%'].mean().reset_index(), 
                          x='Month', y='STR_%', color='ABC_Class', barmode='group')
            st.plotly_chart(fig2, use_container_width=True)
            
        except Exception as e:
            st.warning("No historical data found. Please upload and save data in the first tab.")
    else:
        st.info("Connect to Neon DB to view historical trends.")

# --- TAB 4: DATA MANAGEMENT ---
with tab4:
    st.header("Admin Settings & Backup")
    if st.button("Download Full History as CSV"):
        engine = get_engine()
        if engine:
            history = pd.read_sql("SELECT * FROM vending_performance", engine)
            st.download_button("Download CSV", history.to_csv(index=False), "vending_history_backup.csv", "text/csv")
            
    if st.button("Clear All Historic Data", type="primary"):
        engine = get_engine()
        if engine:
            with engine.connect() as conn:
                conn.execute("DROP TABLE IF EXISTS vending_performance")
                st.success("Database cleared.")
