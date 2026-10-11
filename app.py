import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import time

# ==========================================================
# 1. KONFIGURASI WEB
# ==========================================================
st.set_page_config(
    page_title="WMS Data Entry - Hokkan Deltapack",
    page_icon="📝",
    layout="wide"
)

# ==========================================================
# 2. INISIALISASI FIREBASE
# ==========================================================
if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

st.title("📝 WMS Entry Form (Inbound & Outbound)")
st.caption("Alat khusus PPIC untuk konversi laporan kertas lapangan ke sistem visualisasi WMS.")

# ==========================================================
# 3. TABS MENU (INBOUND & OUTBOUND)
# ==========================================================
tab1, tab2 = st.tabs(["📥 1. Inbound Borongan (Masuk)", "📤 2. Penyesuaian Outbound (Keluar)"])

# ----------------------------------------------------------
# TAB 1: INBOUND BORONGAN (DENGAN MASTER DATA)
# ----------------------------------------------------------
with tab1:
    st.header("Input Data Kertas (Borongan)")
    st.info("Pilih rak dan produk. Sistem otomatis melacak sisa slot kosong dan mengisi barisnya.")
    
    col_in1, col_in2 = st.columns(2)
    with col_in1:
        in_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"])
        in_baris = st.number_input("Nomor Baris", min_value=1, max_value=28, value=1)
        in_qty = st.number_input("Jumlah Pallet Masuk", min_value=1, step=1)
    
    with col_in2:
        # BACA MASTER DATA
        try:
            try:
                df_master = pd.read_csv("Master_Produk.csv", sep=";")
            except:
                df_master = pd.read_csv("Master_Produk.csv", sep=",")
            daftar_produk = df_master['Nama Barang'].dropna().astype(str).unique().tolist()
        except FileNotFoundError:
            df_master = pd.DataFrame()
            daftar_produk = []
            st.warning("⚠️ File 'Master_Produk.csv' tidak ditemukan. Mode ketik manual aktif.")
            
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
        
    if st.button("🚀 Simpan Inbound", type="primary", use_container_width=True):
        if not in_produk or not in_part:
            st.error("⚠️ Nama Produk dan Part Number wajib diisi!")
        else:
            with st.spinner("Menyusun data ke dalam rak..."):
                doc_ref = db.collection('visualisasi_rak').document(f"Kolom_{in_kolom}")
                doc = doc_ref.get()
                all_data = doc.to_dict().get('data', []) if doc.exists else []
                
                baris_data = [d for d in all_data if str(d.get('Baris')) == str(in_baris)]
                
                # --- LOGIKA KAPASITAS MELAR (BOX D/G) ---
                is_all_box = in_part.strip().upper().endswith(('D', 'G'))
                for d in baris_data:
                    pn = str(d.get('Part Number', '')).strip().upper()
                    if pn and not pn.endswith(('D', 'G')):
                        is_all_box = False 
                        break
                        
                if is_all_box:
                    max_slot = 32 if in_baris <= 16 else 46
                else:
                    max_slot = 31 if in_baris <= 16 else 44
                
                # Sabuk Pengaman Fisik
                filled_pallets = [int(float(d.get('Pallet Ke', 0))) for d in baris_data if str(d.get('Pallet Ke', '')) != '']
                current_qty = len(filled_pallets)
                if current_qty > max_slot: max_slot = current_qty 
                
                if current_qty + in_qty > max_slot:
                    st.error(f"❌ **Kapasitas Penuh!** Baris ini maksimal muat {max_slot} pallet. Terisi {current_qty}. Sisa ruang {max_slot - current_qty} slot.")
                else:
                    next_pallet = max(filled_pallets) + 1 if filled_pallets else 1
                    new_records = []
                    added = 0
                    curr_p = next_pallet
                    
                    while added < in_qty:
                        if curr_p not in filled_pallets:
                            new_records.append({
                                'Kolom': in_kolom,
                                'Baris': in_baris,
                                'Pallet Ke': curr_p,
                                'Part Number': in_part,
                                'Lot Number': in_lot,
                                'Nama Produk': in_produk,
                                'In': 1, 'Out': 0, 'Sisa': 1, 'Status': 'Terisi',
                                'Keterangan': ''
                            })
                            added += 1
                        curr_p += 1
                        
                    all_data.extend(new_records)
                    doc_ref.set({'data': all_data})
                    st.success(f"✅ Berhasil menyuntikkan {in_qty} pallet ke rak {in_kolom}-{in_baris}!")
                    time.sleep(1)
                    st.rerun()

# ----------------------------------------------------------
# TAB 2: OUTBOUND (PENYESUAIAN SISA BERDASARKAN LOT)
# ----------------------------------------------------------
with tab2:
    st.header("Penyesuaian Sisa Stok (Berdasarkan No. Lot)")
    st.warning("Pilih No. Lot yang dibongkar, lalu masukkan SISA AKHIR pallet sesuai coretan di kertas.")
    
    col_out1, col_out2 = st.columns([1, 2])
    with col_out1:
        out_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="outk")
        out_baris = st.number_input("Nomor Baris", min_value=1, max_value=28, value=1, key="outb")
        
    doc_ref_out = db.collection('visualisasi_rak').document(f"Kolom_{out_kolom}")
    doc_out = doc_ref_out.get()
    all_out_data = doc_out.to_dict().get('data', []) if doc_out.exists else []
    
    baris_out_data = [d for d in all_out_data if str(d.get('Baris')) == str(out_baris)]
    
    with col_out2:
        if baris_out_data:
            df_out = pd.DataFrame(baris_out_data)
            df_out['Pallet Ke'] = pd.to_numeric(df_out['Pallet Ke'], errors='coerce')
            
            # Deteksi Lot di baris tersebut
            if 'Lot Number' in df_out.columns:
                tersedia_lot = df_out['Lot Number'].dropna().astype(str).unique().tolist()
            else:
                tersedia_lot = ["Tidak ada data Lot"]
                
            pilih_lot = st.selectbox("🎯 Pilih No. Lot yang dikeluarkan:", tersedia_lot)
            
            # Filter hanya data Lot yang dipilih (Urutkan dari terluar/LIFO)
            df_lot = df_out[df_out['Lot Number'].astype(str) == pilih_lot].sort_values('Pallet Ke', ascending=False)
            current_qty_lot = len(df_lot)
            
            st.markdown(f"**📦 Total Lot `{pilih_lot}` di Rak ini: `{current_qty_lot}` Pallet**")
            st.dataframe(df_lot[['Pallet Ke', 'Nama Produk', 'Lot Number', 'Keterangan']].head(3), hide_index=True)
            
            col_sisa1, col_sisa2 = st.columns(2)
            with col_sisa1:
                out_sisa_akhir = st.number_input(f"Sisa Akhir Pallet (0 - {current_qty_lot})", min_value=0, max_value=current_qty_lot, step=1, value=current_qty_lot)
            with col_sisa2:
                out_sisa_dus = st.number_input("Sisa Pecahan (Dus/Box/Sak)", min_value=0, step=1, value=0, help="Opsional: Pecahan yang tertinggal di rak")
            
            if st.button("🚜 Sesuaikan Stok Lot Ini", type="primary", use_container_width=True):
                if out_sisa_akhir == current_qty_lot and out_sisa_dus == 0:
                    st.info("Sisa pallet sama dengan sistem. Tidak ada yang diubah.")
                else:
                    out_qty = current_qty_lot - out_sisa_akhir 
                    
                    # Target Pallet yang dihapus (dari posisi paling luar KHUSUS Lot ini)
                    pallets_to_remove = df_lot['Pallet Ke'].head(out_qty).tolist()
                    
                    new_all_out_data = []
                    for d in all_out_data:
                        b_str = str(d.get('Baris'))
                        p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                        l_str = str(d.get('Lot Number', ''))
                        
                        # 1. Hapus jika masuk daftar bongkar
                        if b_str == str(out_baris) and p_val in pallets_to_remove:
                            continue 
                        
                        # 2. Sisipkan keterangan pecahan dus ke pallet terluar yang tersisa
                        if out_sisa_dus > 0 and b_str == str(out_baris) and l_str == pilih_lot:
                            sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
                            if not sisa_pallets.empty and p_val == sisa_pallets['Pallet Ke'].max():
                                d['Keterangan'] = f"Sisa pecahan: {out_sisa_dus} qty"
                                
                        new_all_out_data.append(d)
                    
                    doc_ref_out.set({'data': new_all_out_data})
                    st.success(f"✅ Menghapus {out_qty} Pallet Lot '{pilih_lot}'. Sisa sekarang: {out_sisa_akhir} Pallet.")
                    time.sleep(1.5)
                    st.rerun()
        else:
            st.info(f"📍 Rak {out_kolom}-{out_baris} kosong melompong.")
