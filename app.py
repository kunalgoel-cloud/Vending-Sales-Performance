import streamlit as st
import pandas as pd
import numpy as np
import datetime

# --- APP CONFIG ---
st.set_page_config(page_title="Vending Performance Hub", layout="wide")

# --- SESSION STATE (To prevent data loss on filtering) ---
if 'analysis_generated' not in st.session_state:
    st.session_state.analysis_generated = False
if 'processed_df' not in st.session_state:
    st.session_state.processed_df = None
if 'price_map' not in st.session_state:
    st.session_state.price_map = {}

# --- HELPER: ROBUST CLEANING ---
def robust_clean(df, expected_cols):
    """Forces column names and ensures we only drop actual 'Total' summary rows."""
    df.columns = expected_cols
    # Only drop if the ENTIRE cell is 'Total' (case-insensitive), not if 'Total' is part of a name
    df = df[df[expected_cols[0]].astype(str).str.strip().lower() != 'total']
    return df

# --- CORE PROCESSING ENGINE ---
def process_master_file(uploaded_file):
    try:
        all_sheets = pd.read_excel(uploaded_file, sheet_name=None)
        s_map = {k.lower().strip(): k for k in all_sheets.keys()}
        
        # 1. Clean Sales Summary
        sales = robust_clean(all_sheets[s_map['sales summary']].iloc[1:, 0:3].copy(), ['City', 'Product', 'Sales_Qty'])
        
        # 2. Clean SOH (The likely culprit for your 1768 vs 55 unit issue)
        soh_raw = robust_clean(all_sheets[s_map['soh']].iloc[1:, [0, 1, 4]].copy(), ['Loc', 'Product', 'Total_SOH'])
        # Improved City extraction: Strip whitespace and handle various separators
        soh_raw['City'] = soh_raw['Loc'].astype(str).str.replace('-', ' ').str.split(' ').str[0].str.strip().str.upper()
        soh = soh_raw.groupby(['City', 'Product'])['Total_SOH'].sum().reset_index()

        # 3. Clean Machine Placement
        mach = robust_clean(all_sheets[s_map['machine placement']].iloc[1:, 0:3].copy(), ['City', 'Product', 'Machine_Count'])
        mach['City'] = mach['City'].astype(str).str.strip().str.upper()

        # 4. Merge - Using OUTER join to ensure no data is lost from any sheet
        # First merge Machine and Sales
        df = pd.merge(mach, sales, on=['City', 'Product'], how='outer')
        # Then merge with the aggregated SOH
        df = pd.merge(df, soh, on=['City', 'Product'], how='outer')
        
        # Fill missing values with 0
        for c in ['Sales_Qty', 'Total_SOH', 'Machine_Count']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

        # 5. Calculations
        df['drr'] = df['Sales_Qty'] / 30  
        df['velocity'] = np.where(df['Machine_Count'] > 0, (df['Sales_Qty'] / df['Machine_Count']) / 30, 0)
        df['str_pct'] = np.where((df['Sales_Qty'] + df['Total_SOH']) > 0, 
                                 (df['Sales_Qty'] / (df['Sales_Qty'] + df['Total_SOH']) * 100), 0)
        df['days_of_cover'] = np.where(df['drr'] > 0, df['Total_SOH'] / df['drr'], 999)
        
        # ABC Ranking
        df['rank'] = df['velocity'].rank(pct=True)
        df['abc_class'] = np.where(df['rank'] > 0.8, 'A', np.where(df['rank'] > 0.5, 'B', 'C'))
        
        return df.drop(columns=['rank'])
    except Exception as e:
        st.error(f"Processing Error: {e}")
        return None

# --- MAIN UI ---
st.title("📊 Vending Performance Hub")

file = st.file_uploader("Step 1: Upload Excel Workbook", type="xlsx")

if file:
    if st.session_state.processed_df is None:
        st.session_state.processed_df = process_master_file(file)

    if st.session_state.processed_df is not None:
        st.subheader("💰 Step 2: Item-Wise Price Entry")
        unique_prods = sorted(st.session_state.processed_df['Product'].unique())
        price_init = pd.DataFrame({"Product": unique_prods, "Price_per_Unit": 0.0})
        edited_prices = st.data_editor(price_init, use_container_width=True, hide_index=True, key="price_editor")
        
        if st.button("Generate Combined Analysis"):
            st.session_state.analysis_generated = True
            st.session_state.price_map = dict(zip(edited_prices['Product'], edited_prices['Price_per_Unit']))

        if st.session_state.analysis_generated:
            df = st.session_state.processed_df.copy()
            df['unit_price'] = df['Product'].map(st.session_state.price_map)
            df['sales_val'] = df['Sales_Qty'] * df['unit_price']
            df['soh_val'] = df['Total_SOH'] * df['unit_price']

            # Analysis Filters
            f_col1, f_col2 = st.columns(2)
            sel_cities = f_col1.multiselect("Filter City", sorted(df['City'].unique()), default=df['City'].unique())
            sel_prods = f_col2.multiselect("Filter Product", sorted(df['Product'].unique()), default=df['Product'].unique())
            
            filtered_df = df[(df['City'].isin(sel_cities)) & (df['Product'].isin(sel_prods))]

            # Metrics
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Sales (Qty)", f"{filtered_df['Sales_Qty'].sum():,.0f}")
            m1.caption(f"Value: ₹{filtered_df['sales_val'].sum():,.0f}")
            
            m2.metric("Avg Daily Velocity", f"{filtered_df[filtered_df['Sales_Qty']>0]['velocity'].mean():.2f}")
            
            m3.metric("Total Stock on Hand", f"{filtered_df['Total_SOH'].sum():,.0f}")
            m3.caption(f"Value: ₹{filtered_df['soh_val'].sum():,.0f}")
            
            m4.metric("Total Machines", f"{filtered_df['Machine_Count'].sum():,.0f}")

            # Combined View
            st.markdown("### 📈 Combined Performance & Inventory Analysis")
            format_map = {'drr': '{:.1f}', 'days_of_cover': '{:.1f}', 'str_pct': '{:.1f}%', 'velocity': '{:.2f}', 'Machine_Count': '{:,.0f}', 'Sales_Qty': '{:,.0f}', 'Total_SOH': '{:,.0f}'}
            cols = ['City', 'Product', 'Machine_Count', 'Sales_Qty', 'velocity', 'abc_class', 'Total_SOH', 'drr', 'days_of_cover', 'str_pct']
            st.dataframe(filtered_df[cols].style.format(format_map).background_gradient(subset=['velocity'], cmap='YlGn'), use_container_width=True)
