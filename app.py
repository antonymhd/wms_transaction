import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import time
import io
import copy
import re
from datetime import datetime, timedelta

# ==========================================================
# 1. KONFIGURASI WEB & DATABASE
# ==========================================================
st.set_page_config(
    page_title="WMS Data Entry - Hokkan Deltapack",
    page_icon="📝",
    layout="wide"
)

NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

st.title("📝 WMS Entry Form (Inbound & Outbound)")
st.caption(f"Alat khusus PPIC. Terhubung ke database: {NAMA_DATABASE}")

# --- FUNGSI PENCATAT LOG MUTASI OTOMATIS (+ ECERAN) ---
def catat_log_mutasi(jenis, kolom, baris, produk, part_num, no_lot, pallet_utuh, pecahan, eceran, keterangan=""):
    waktu_sekarang = (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
    
    log_data = {
        "Waktu Transaksi": waktu_sekarang,
        "Jenis Transaksi": jenis,
        "Lokasi Rak": f"{kolom}-{baris}",
        "Nama Produk": produk,
        "Part Number": part_num,
        "No Lot": no_lot,
        "Mutasi Pallet": float(pallet_utuh),
        "Mutasi Pecahan (Dus/Sak)": float(pecahan),
        "Mutasi Eceran (Pcs)": float(eceran),
        "Keterangan": keterangan,
        "Timestamp_System": firestore.SERVER_TIMESTAMP
    }
    try:
        db.collection(DB_LOG_MUTASI).add(log_data)
    except Exception as e:
        st.toast(f"⚠️ Peringatan: Gagal mencatat log mutasi ({e})", icon="⚠️")

# ==========================================================
# 2. FITUR UNDO & REDO
# ==========================================================
if 'undo_data' not in st.session_state:
    st.session_state['undo_data'] = None
    st.session_state['redo_data'] = None
    st.session_state['action_msg'] = ""
    st.session_state['action_kolom'] = ""
    st.session_state['action_status'] = None 

if st.session_state['action_status'] == 'DONE':
    st.info(f"🔄 **Aksi Terakhir:** {st.session_state['action_msg']}")
    if st.button("🔙 BATALKAN AKSI TERAKHIR (UNDO)", type="primary"):
        with st.spinner("Mengembalikan data seperti semula..."):
            doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state['action_kolom']}")
            doc_ref.set({'data': st.session_state['undo_data']})
            st.session_state['action_status'] = 'UNDONE'
            catat_log_mutasi("PEMBATALAN (UNDO)", st.session_state['action_kolom'], "ALL", "Koreksi Sistem", "-", "-", 0, 0, 0, f"Membatalkan aksi: {st.session_state['action_msg']}")
            st.success("✅ Aksi dibatalkan (Undo)! Data kembali seperti semula.")
            time.sleep(1.5)
            st.rerun()

if st.session_state['action_status'] is not None:
    st.divider()

# ==========================================================
# 3. TABS MENU UTAMA (TANPA FIFO, DITAMBAH SINKRONISASI)
# ==========================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📥 1. Inbound", "📤 2. Outbound", "⚙️ 3. Audit & Backup", "📊 4. Log Mutasi", "🚀 5. Sinkronisasi CSV"])

# ----------------------------------------------------------
# TAB 1: INBOUND BORONGAN
# ----------------------------------------------------------
with tab1:
    st.header("Input Data Kertas (Borongan)")
    st.caption("Cek status rak terlebih dahulu, lalu masukkan data barang masuk.")
    
    if 'in_lokasi_dicari' not in st.session_state:
        st.session_state['in_lokasi_dicari'] = False
        st.session_state['in_k_pilih'] = "A"
        st.session_state['in_b_pilih'] = 1

    col_in1, col_in2 = st.columns([1, 2])
    
    with col_in1:
        st.markdown("**1. Pilih Lokasi Rak Tujuan**")
        in_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="in_kolom")
        in_baris = st.number_input("Nomor Baris (Inbound)", min_value=1, max_value=28, value=1)
        
        if st.button("🔍 Cek Status Rak", use_container_width=True):
            st.session_state['in_lokasi_dicari'] = True
            st.session_state['in_k_pilih'] = in_kolom
            st.session_state['in_b_pilih'] = in_baris
            
    with col_in2:
        if st.session_state['in_lokasi_dicari']:
            k_pilih = st.session_state['in_k_pilih']
            b_pilih = st.session_state['in_b_pilih']
            
            doc_ref_in = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            doc_in = doc_ref_in.get()
            all_data_raw = doc_in.to_dict().get('data', []) if doc_in.exists else []
            
            baris_data_preview = []
            is_all_box_existing = True
            has_items = False
            
            for d in all_data_raw:
                if float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '':
                    baris_data_preview.append(d)
                    has_items = True
                    pn = str(d.get('Part Number', '')).strip().upper()
                    if not pn.endswith(('D', 'G')): is_all_box_existing = False
                        
            if not has_items: is_all_box_existing = False
                
            max_slot_display = 32 if (is_all_box_existing and b_pilih <= 16) else (46 if is_all_box_existing else (31 if b_pilih <= 16 else 44))
            current_qty = len(baris_data_preview)
            sisa_ruang = max_slot_display - current_qty
            
            st.markdown(f"**📦 Status Rak {k_pilih}-{b_pilih} | Terisi: `{current_qty}` / `{max_slot_display}` (Sisa Ruang: `{sisa_ruang}` Slot)**")
            
            df_baris = pd.DataFrame(baris_data_preview)
            if not df_baris.empty:
                df_baris['Pallet Ke'] = pd.to_numeric(df_baris['Pallet Ke'], errors='coerce')
                df_baris = df_baris.sort_values('Pallet Ke', ascending=True)
                kolom_tampil = ['Pallet Ke', 'Part Number', 'Nama Produk', 'Lot Number', 'Keterangan']
                kolom_tersedia = [c for c in kolom_tampil if c in df_baris.columns]
                st.dataframe(df_baris[kolom_tersedia], hide_index=True, height=150)
            else:
                st.info("Rak ini masih kosong. Siap diisi penuh!")
                
            st.divider()
            st.markdown(f"**2. Eksekusi Inbound (Tujuan: {k_pilih}-{b_pilih})**")
            
            try:
                try: df_master = pd.read_csv("Master_Produk.csv", sep=";")
                except: df_master = pd.read_csv("Master_Produk.csv", sep=",")
                daftar_produk = df_master['Nama Barang'].dropna().astype(str).unique().tolist()
            except FileNotFoundError:
                df_master = pd.DataFrame(); daftar_produk = []
                st.warning("⚠️ File 'Master_Produk.csv' tidak ditemukan. Mode manual.")
                
            pilihan_barang = st.selectbox("Pilih Produk", ["-- Ketik Manual --"] + daftar_produk)
            
            if pilihan_barang != "-- Ketik Manual --" and not df_master.empty:
                in_produk = pilihan_barang
                match_part = df_master[df_master['Nama Barang'] == pilihan_barang]['Part Number'].values
                val_part = str(match_part[0]) if len(match_part) > 0 else ""
                in_part = st.text_input("Part Number (Auto-fill)", value=val_part, disabled=True)
            else:
                in_produk = st.text_input("Nama Produk (Ketik Manual)")
                in_part = st.text_input("Part Number (Ketik Manual)")
                
            in_lot = st.text_input("No. Lot Produksi / Mesin Shift")
            
            col_qty1, col_qty2, col_qty3 = st.columns(3)
            with col_qty1:
                in_qty = st.number_input("Pallet Masuk UTUH", min_value=0, step=1, value=1)
            with col_qty2:
                in_pecahan = st.number_input("Sisa Pecahan (Dus/Sak)", min_value=0, step=1, value=0)
            with col_qty3:
                in_eceran = st.number_input("Eceran (Pcs)", min_value=0, step=1, value=0)
                
            if st.button("🚀 Simpan Inbound", type="primary", use_container_width=True):
                if not in_produk or not in_part:
                    st.error("⚠️ Nama Produk dan Part Number wajib diisi!")
                elif in_qty == 0 and in_pecahan == 0 and in_eceran == 0:
                    st.error("⚠️ Masukkan minimal 1 Qty (Pallet/Pecahan/Eceran)!")
                else:
                    with st.spinner("Menyusun data ke dalam rak..."):
                        doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                        doc = doc_ref.get()
                        all_data_raw2 = doc.to_dict().get('data', []) if doc.exists else []
                        data_sebelum = copy.deepcopy(all_data_raw2)
                        
                        all_data = [d for d in all_data_raw2 if not (float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() == '')]
                        baris_data_actual = [d for d in all_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
                        
                        is_all_box_actual = in_part.strip().upper().endswith(('D', 'G'))
                        for d in baris_data_actual:
                            pn = str(d.get('Part Number', '')).strip().upper()
                            if pn and not pn.endswith(('D', 'G')):
