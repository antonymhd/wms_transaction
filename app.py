import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import json

st.set_page_config(page_title="WMS Transaksi", page_icon="📝", layout="wide")

# ==========================================================
# FIREBASE INIT
# ==========================================================
if not firebase_admin._apps:
    cert_dict = json.loads(st.secrets["textkey"])
    cred = credentials.Certificate(cert_dict)
    firebase_admin.initialize_app(cred)
db = firestore.client()

@st.cache_data(ttl=900)
def fetch_all_data():
    docs = db.collection('uji_coba_rak').stream()
    return pd.DataFrame([doc.to_dict() for doc in docs])

st.title("📝 Data Entry WMS & Sinkronisasi")

# TAB DIPERBARUI: FIFO DIHAPUS, DIGANTI UNDUH BACKUP
tab1, tab2, tab3, tab4 = st.tabs(["📥 Inbound", "📤 Outbound", "📊 Unduh Backup CSV", "⚙️ Sinkronisasi Upload"])

# --- TAB 1: INBOUND ---
with tab1:
    st.subheader("Input Barang Masuk")
    with st.form("inbound_form"):
        k = st.selectbox("Kolom", ['A','B','C','D','E','F','G'])
        b = st.number_input("Baris", min_value=1, max_value=28, value=1)
        p = st.number_input("Pallet Ke", min_value=1, value=1)
        pn = st.text_input("Part Number")
        ln = st.text_input("Lot Number")
        np = st.text_input("Nama Produk")
        ket = st.text_input("Keterangan (Isi 'Pecahan/Eceran' jika bukan utuh)")
        
        if st.form_submit_button("Simpan Inbound"):
            doc_ref = db.collection('uji_coba_rak').document()
            doc_ref.set({
                'Kolom': k, 'Baris': b, 'Pallet Ke': p, 'Part Number': pn,
                'Lot Number': ln, 'Nama Produk': np, 'Keterangan': ket
            })
            st.cache_data.clear() # Reset cache
            st.success("✅ Inbound Berhasil Disimpan!")

# --- TAB 2: OUTBOUND ---
with tab2:
    st.subheader("Tarik Barang Keluar (Outbound)")
    df_out = fetch_all_data()
    if not df_out.empty:
        df_out = df_out[~df_out['Part Number'].astype(str).str.lower().isin(["", "nan"])]
        search_out = st.text_input("Cari Part Number / Nama Produk untuk dikeluarkan:")
        if search_out:
            mask = df_out.astype(str).apply(lambda x: x.str.contains(search_out, case=False, na=False)).any(axis=1)
            df_out = df_out[mask]
            
        if not df_out.empty:
            for idx, row in df_out.head(10).iterrows():
                col1, col2 = st.columns([4, 1])
                col1.write(f"Rak {row.get('Kolom','')}-{row.get('Baris','')} | Pallet {row.get('Pallet Ke','')} | {row.get('Part Number','')} | {row.get('Lot Number','')}")
                if col2.button("Keluarkan", key=f"out_{idx}"):
                    docs_to_delete = db.collection('uji_coba_rak').where('Kolom', '==', row.get('Kolom')).where('Baris', '==', row.get('Baris')).where('Pallet Ke', '==', row.get('Pallet Ke')).where('Part Number', '==', row.get('Part Number')).limit(1).stream()
                    for d in docs_to_delete:
                        d.reference.delete()
                    st.cache_data.clear()
                    st.success("✅ Barang berhasil dikeluarkan!")
                    st.rerun()

# --- TAB 3: UNDUH BACKUP (PENGGANTI FIFO) ---
with tab3:
    st.subheader("Unduh Database untuk Audit & Pelacakan Excel")
    st.info("Gunakan fitur ini pada pukul 14:00 - 16:00 untuk menarik data terbaru sebagai bahan pelacakan FIFO secara manual di Excel.")
    
    if st.button("Siapkan Data Unduhan"):
        df_download = fetch_all_data()
        if not df_download.empty:
            # Rapikan kolom agar berurutan enak dibaca di Excel
            kolom_urutan = ['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk', 'Keterangan']
            df_download = df_download[[c for c in kolom_urutan if c in df_download.columns]]
            df_download = df_download.sort_values(by=['Kolom', 'Baris', 'Pallet Ke'])
            
            csv = df_download.to_csv(index=False, sep=";").encode('utf-8')
            st.download_button(
                label="⬇️ Download Data WMS (CSV)",
                data=csv,
                file_name="Backup_Data_WMS_Untuk_FIFO.csv",
                mime="text/csv",
                type="primary"
            )
        else:
            st.warning("Data masih kosong.")

# --- TAB 4: SINKRONISASI CSV MANUAL ---
with tab4:
    st.header("⚙️ Unggah Hasil Kerja Manual Excel")
    st.warning("⚠️ Menyinkronkan akan menghapus data Firebase lama & menggantinya dengan isi file yang diunggah. Pastikan file Valid!")
    
    uploaded_file = st.file_uploader("Upload file 'Audit_Stok_WMS.csv'", type=['csv'])
    if uploaded_file is not None:
        try:
            try: df_sync = pd.read_csv(uploaded_file, sep=";")
            except: df_sync = pd.read_csv(uploaded_file, sep=",")
            
            for c in df_sync.columns:
                if c.strip().lower() in ['palet ke', 'pallet ke']: df_sync.rename(columns={c: 'Pallet Ke'}, inplace=True)
            
            df_sync = df_sync.fillna("")
            st.write("Pratinjau Data:")
            st.dataframe(df_sync.head(5))
            
            if st.button("🚀 MULAI SINKRONISASI KE FIREBASE", type="primary"):
                with st.spinner("Menghapus data Firebase lama..."):
                    docs = db.collection('uji_coba_rak').stream()
                    batch = db.batch()
                    del_count = 0
                    for doc in docs:
                        batch.delete(doc.reference)
                        del_count += 1
                        if del_count % 400 == 0: batch.commit(); batch = db.batch()
                    if del_count % 400 != 0: batch.commit()
                
                with st.spinner("Menyuntikkan data CSV baru ke Firebase..."):
                    batch = db.batch()
                    ins_count = 0
                    for _, row in df_sync.iterrows():
                        doc_ref = db.collection('uji_coba_rak').document()
                        batch.set(doc_ref, row.to_dict())
                        ins_count += 1
                        if ins_count % 400 == 0: batch.commit(); batch = db.batch()
                    if ins_count % 400 != 0: batch.commit()
                
                st.cache_data.clear()
                st.success(f"✅ Berhasil! {del_count} data lama dihapus, {ins_count} data baru disuntikkan. Sistem siap bekerja normal!")
        except Exception as e:
            st.error(f"Gagal membaca file: {e}")
