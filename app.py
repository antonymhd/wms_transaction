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

# 💡 NAMA DATABASE UTAMA & LOG MUTASI
NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

st.title("📝 WMS Entry Form (Inbound & Outbound)")
st.caption(f"Alat khusus PPIC. Terhubung ke database: {NAMA_DATABASE}")

# --- FUNGSI PENCATAT LOG MUTASI OTOMATIS (Tanpa pytz) ---
def catat_log_mutasi(jenis, kolom, baris, produk, part_num, no_lot, pallet_utuh, pecahan, keterangan=""):
    # Menggunakan UTC+7 untuk Waktu Indonesia Barat (WIB)
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
    st.session_state['last_log_id'] = None

if st.session_state['action_status'] == 'DONE':
    st.info(f"🔄 **Aksi Terakhir:** {st.session_state['action_msg']}")
    if st.button("🔙 BATALKAN AKSI TERAKHIR (UNDO)", type="primary"):
        with st.spinner("Mengembalikan data seperti semula..."):
            doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state['action_kolom']}")
            doc_ref.set({'data': st.session_state['undo_data']})
            st.session_state['action_status'] = 'UNDONE'
            
            # Catat log pembalikan
            catat_log_mutasi("PEMBATALAN (UNDO)", st.session_state['action_kolom'], "ALL", "Koreksi Sistem", "-", "-", 0, 0, f"Membatalkan aksi: {st.session_state['action_msg']}")
            
            st.success("✅ Aksi dibatalkan (Undo)! Data kembali seperti semula.")
            time.sleep(1.5)
            st.rerun()

if st.session_state['action_status'] is not None:
    st.divider()

# ==========================================================
# 3. TABS MENU UTAMA
# ==========================================================
tab1, tab2, tab3, tab4 = st.tabs(["📥 1. Inbound Borongan", "📤 2. Penyesuaian Outbound", "⚙️ 3. Pengaturan & Backup", "📊 4. Laporan Mutasi Harian"])

# ----------------------------------------------------------
# TAB 1: INBOUND BORONGAN (DENGAN TOMBOL CEK RAK)
# ----------------------------------------------------------
with tab1:
    st.header("Input Data Kertas (Borongan)")
    st.caption("Cek status rak terlebih dahulu, lalu masukkan data barang masuk.")
    
    # State untuk tombol Cari Rak di Inbound
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
            
            # Tarik data khusus untuk ditampilkan di tabel preview
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
                    if not pn.endswith(('D', 'G')):
                        is_all_box_existing = False
                        
            if not has_items:
                is_all_box_existing = False
                
            max_slot_display = 32 if (is_all_box_existing and b_pilih <= 16) else (46 if is_all_box_existing else (31 if b_pilih <= 16 else 44))
            current_qty = len(baris_data_preview)
            if current_qty > max_slot_display: max_slot_display = current_qty
            sisa_ruang = max_slot_display - current_qty
            
            # --- TAMPILAN STATUS RAK ---
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
            
            # --- FORM INPUT ---
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
            
            col_qty1, col_qty2 = st.columns(2)
            with col_qty1:
                in_qty = st.number_input("Pallet Masuk UTUH", min_value=0, step=1, value=1)
            with col_qty2:
                in_pecahan = st.number_input("Sisa Pecahan (Qty)", min_value=0, step=1, value=0)
                
            if st.button("🚀 Simpan Inbound", type="primary", use_container_width=True):
                if not in_produk or not in_part:
                    st.error("⚠️ Nama Produk dan Part Number wajib diisi!")
                elif in_qty == 0 and in_pecahan == 0:
                    st.error("⚠️ Masukkan minimal 1 Pallet Utuh atau Pecahan!")
                else:
                    with st.spinner("Menyusun data ke dalam rak..."):
                        doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                        doc = doc_ref.get()
                        all_data_raw2 = doc.to_dict().get('data', []) if doc.exists else []
                        
                        data_sebelum = copy.deepcopy(all_data_raw2)
                        
                        # Pembersihan data hantu
                        all_data = []
                        for d in all_data_raw2:
                            is_baris_sama = float(d.get('Baris', 0) or 0) == float(b_pilih)
                            is_hantu = str(d.get('Part Number', '')).strip() == ''
                            if is_baris_sama and is_hantu:
                                continue 
                            all_data.append(d)
                        
                        baris_data_actual = [d for d in all_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
                        
                        is_all_box_actual = in_part.strip().upper().endswith(('D', 'G'))
                        for d in baris_data_actual:
                            pn = str(d.get('Part Number', '')).strip().upper()
                            if pn and not pn.endswith(('D', 'G')):
                                is_all_box_actual = False 
                                break
                                
                        max_slot_actual = 32 if (is_all_box_actual and b_pilih <= 16) else (46 if is_all_box_actual else (31 if b_pilih <= 16 else 44))
                        
                        filled_pallets = [int(float(d.get('Pallet Ke', 0) or 0)) for d in baris_data_actual if pd.notna(d.get('Pallet Ke')) and str(d.get('Pallet Ke')).strip() != '']
                        current_qty_actual = len(filled_pallets)
                        if current_qty_actual > max_slot_actual: max_slot_actual = current_qty_actual 
                        
                        total_inbound_slots = in_qty + (1 if in_pecahan > 0 else 0)
                        
                        if current_qty_actual + total_inbound_slots > max_slot_actual:
                            st.error(f"❌ **Kapasitas Penuh!** Anda memasukkan {total_inbound_slots} Slot Fisik. Rak maksimal muat {max_slot_actual}. Terisi {current_qty_actual}. Sisa ruang {max_slot_actual - current_qty_actual} slot.")
                        else:
                            next_pallet = max(filled_pallets) + 1 if filled_pallets else 1
                            new_records = []
                            
                            for _ in range(int(in_qty)):
                                while next_pallet in filled_pallets: next_pallet += 1
                                new_records.append({
                                    'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': float(next_pallet),
                                    'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_produk,
                                    'In': 1, 'Out': 0, 'Sisa': 1, 'Status': 'Terisi', 'Keterangan': ''
                                })
                                filled_pallets.append(next_pallet)
                                next_pallet += 1
                                
                            if in_pecahan > 0:
                                while next_pallet in filled_pallets: next_pallet += 1
                                new_records.append({
                                    'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': float(next_pallet),
                                    'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_produk,
                                    'In': 1, 'Out': 0, 'Sisa': 1, 'Status': 'Terisi',
                                    'Keterangan': f"Pecahan: {in_pecahan} qty"
                                })
                                filled_pallets.append(next_pallet)
                                
                            all_data.extend(new_records)
                            
                            st.session_state['undo_data'] = data_sebelum
                            st.session_state['redo_data'] = copy.deepcopy(all_data)
                            st.session_state['action_kolom'] = k_pilih
                            st.session_state['action_msg'] = f"Inbound {total_inbound_slots} Slot Fisik '{in_produk}' ke Rak {k_pilih}-{b_pilih}"
                            st.session_state['action_status'] = 'DONE'
                            
                            doc_ref.set({'data': all_data})
                            
                            # --- MENULIS LOG INBOUND ---
                            catat_log_mutasi("INBOUND", k_pilih, b_pilih, in_produk, in_part, in_lot, in_qty, in_pecahan)
                            
                            st.success(f"✅ Berhasil menyuntikkan data! Memakan {total_inbound_slots} Slot Fisik di rak {k_pilih}-{b_pilih}.")
                            st.session_state['in_lokasi_dicari'] = False # Reset UI
                            time.sleep(2)
                            st.rerun()
        else:
            st.info("👈 Silakan tentukan Lokasi Kolom dan Baris di sebelah kiri, lalu klik 'Cek Status Rak'.")

# ----------------------------------------------------------
# TAB 2: OUTBOUND (PENYESUAIAN SISA BERDASARKAN LOT)
# ----------------------------------------------------------
with tab2:
    st.header("Penyesuaian Sisa Stok (Berdasarkan No. Lot)")
    st.caption("Pilih No. Lot yang dibongkar, lalu masukkan SISA AKHIR pallet sesuai coretan di kertas.")
    
    if 'out_lokasi_dicari' not in st.session_state:
        st.session_state['out_lokasi_dicari'] = False
        st.session_state['out_k_pilih'] = "A"
        st.session_state['out_b_pilih'] = 1

    col_out1, col_out2 = st.columns([1, 2])
    with col_out1:
        st.markdown("**1. Pilih Lokasi Rak**")
        out_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="out_kolom")
        out_baris = st.number_input("Nomor Baris (Outbound)", min_value=1, max_value=28, value=1)
        
        if st.button("🔍 Cari Data Rak", use_container_width=True):
            st.session_state['out_lokasi_dicari'] = True
            st.session_state['out_k_pilih'] = out_kolom
            st.session_state['out_b_pilih'] = out_baris
            
    with col_out2:
        if st.session_state['out_lokasi_dicari']:
            k_pilih = st.session_state['out_k_pilih']
            b_pilih = st.session_state['out_b_pilih']
            
            doc_ref_out = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            doc_out = doc_ref_out.get()
            all_out_data = doc_out.to_dict().get('data', []) if doc_out.exists else []
            
            baris_out_data = [d for d in all_out_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
            
            if baris_out_data:
                df_out = pd.DataFrame(baris_out_data)
                df_out['Pallet Ke'] = pd.to_numeric(df_out['Pallet Ke'], errors='coerce')
                df_out = df_out[df_out['Part Number'].astype(str).str.strip() != ""]
                
                if not df_out.empty and 'Lot Number' in df_out.columns:
                    tersedia_lot = []
                    mapping_lot = {}
                    
                    for _, row in df_out.drop_duplicates(subset=['Lot Number', 'Nama Produk']).iterrows():
                        lot_asli = str(row['Lot Number'])
                        nama_prod = str(row['Nama Produk'])
                        label_dropdown = f"{nama_prod} (Lot: {lot_asli})"
                        
                        tersedia_lot.append(label_dropdown)
                        mapping_lot[label_dropdown] = lot_asli
                        
                    st.markdown(f"**2. Eksekusi Pembongkaran di Rak {k_pilih}-{b_pilih}**")
                    pilihan_label = st.selectbox("🎯 Pilih Produk & No. Lot yang dikeluarkan:", tersedia_lot)
                    
                    pilih_lot = mapping_lot[pilihan_label]
                    df_lot = df_out[df_out['Lot Number'].astype(str) == pilih_lot].sort_values('Pallet Ke', ascending=False)
                    current_qty_lot = len(df_lot)
                    
                    jml_utuh_awal = df_lot[~df_lot['Keterangan'].astype(str).str.contains('Pecahan', case=False, na=False)].shape[0]
                    pecahan_rows = df_lot[df_lot['Keterangan'].astype(str).str.contains('Pecahan', case=False, na=False)]
                    pecahan_awal = 0
                    if not pecahan_rows.empty:
                        ket_str = str(pecahan_rows['Keterangan'].iloc[0])
                        numbers = re.findall(r'\d+', ket_str)
                        if numbers: pecahan_awal = int(numbers[0])
                    
                    st.markdown(f"**📦 Total Lot `{pilih_lot}` di Rak ini: `{current_qty_lot}` Slot Fisik**")
                    st.caption(f"*(Fisik asli: {jml_utuh_awal} Pallet Utuh, {pecahan_awal} Pecahan)*")
                    
                    kolom_tampil = ['Pallet Ke', 'Part Number', 'Nama Produk', 'Lot Number', 'Keterangan']
                    kolom_tersedia = [c for c in kolom_tampil if c in df_lot.columns]
                    st.dataframe(df_lot[kolom_tersedia].head(5), hide_index=True)
                    
                    col_sisa1, col_sisa2 = st.columns(2)
                    with col_sisa1:
                        out_sisa_akhir = st.number_input(f"Sisa Pallet UTUH Fisik", min_value=0, max_value=jml_utuh_awal, step=1, value=jml_utuh_awal)
                    with col_sisa2:
                        out_sisa_dus = st.number_input("Sisa Pecahan Fisik Akhir (Dus/Sak)", min_value=0, step=1, value=pecahan_awal)
                
                    if st.button("🚜 Sesuaikan Stok Lot Ini (OUTBOUND)", type="primary", use_container_width=True):
                        slot_tambahan = 1 if out_sisa_dus > 0 else 0
                        total_slot_dibutuhkan = out_sisa_akhir + slot_tambahan
                        
                        if total_slot_dibutuhkan > current_qty_lot:
                            st.error(f"❌ Tidak logis! Anda mengklaim sisa fisik {total_slot_dibutuhkan} Slot, padahal di sistem hanya ada {current_qty_lot} Pallet.")
                        elif out_sisa_akhir == jml_utuh_awal and out_sisa_dus == pecahan_awal:
                            st.info("Sisa pallet persis sama dengan di sistem. Tidak ada pergerakan Outbound yang dicatat.")
                        else:
                            keluar_utuh = jml_utuh_awal - out_sisa_akhir
                            keluar_pecahan = pecahan_awal - out_sisa_dus
                            
                            data_sebelum_out = copy.deepcopy(all_out_data)
                            
                            out_qty = current_qty_lot - total_slot_dibutuhkan
                            pallets_to_remove = df_lot['Pallet Ke'].head(out_qty).tolist() if out_qty > 0 else []
                            
                            new_all_out_data = []
                            for d in all_out_data:
                                b_val = float(d.get('Baris', 0) or 0)
                                p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                                l_str = str(d.get('Lot Number', ''))
                                
                                if b_val == float(b_pilih) and p_val in pallets_to_remove:
                                    continue 
                                
                                if out_sisa_dus > 0 and b_val == float(b_pilih) and l_str == pilih_lot:
                                    sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
                                    if not sisa_pallets.empty and p_val == sisa_pallets['Pallet Ke'].max():
                                        d['Keterangan'] = f"Pecahan: {out_sisa_dus} qty"
                                
                                elif out_sisa_dus == 0 and b_val == float(b_pilih) and l_str == pilih_lot:
                                    if 'Pecahan' in str(d.get('Keterangan', '')):
                                        d['Keterangan'] = ""
                                        
                                new_all_out_data.append(d)
                            
                            st.session_state['undo_data'] = data_sebelum_out
                            st.session_state['redo_data'] = copy.deepcopy(new_all_out_data)
                            st.session_state['action_kolom'] = k_pilih
                            st.session_state['action_msg'] = f"Penyesuaian Outbound Lot '{pilih_lot}' di Rak {k_pilih}-{b_pilih}"
                            st.session_state['action_status'] = 'DONE'
                            
                            doc_ref_out.set({'data': new_all_out_data})
                            
                            # --- MENULIS LOG OUTBOUND ---
                            nama_prod_keluar = str(df_lot['Nama Produk'].iloc[0])
                            part_num_keluar = str(df_lot['Part Number'].iloc[0])
                            catat_log_mutasi("OUTBOUND (Penyesuaian)", k_pilih, b_pilih, nama_prod_keluar, part_num_keluar, pilih_lot, keluar_utuh, keluar_pecahan)
                            
                            st.success(f"✅ Selesai! Mengeluarkan {keluar_utuh} Pallet & {keluar_pecahan} Pecahan. Sisa di rak menjadi {total_slot_dibutuhkan} Slot Fisik.")
                            
                            st.session_state['out_lokasi_dicari'] = False
                            time.sleep(2)
                            st.rerun()
                else:
                    st.info(f"📍 Rak {k_pilih}-{b_pilih} masih kosong (Hanya berisi slot kosong / hantu).")
            else:
                st.info(f"📍 Rak {k_pilih}-{b_pilih} belum ada data sama sekali.")
        else:
            st.info("👈 Silakan tentukan Lokasi Kolom dan Baris di sebelah kiri, lalu klik 'Cari Data Rak'.")

# ----------------------------------------------------------
# TAB 3: PENGATURAN & BACKUP DATA
# ----------------------------------------------------------
with tab3:
    st.header("⚙️ Ekspor dan Hapus Data")
    st.subheader("📥 Tarik Laporan Master (Export to CSV)")
    
    if st.button("Persiapkan Data Racking Terkini untuk Diunduh"):
        with st.spinner("Menarik data dari Firebase..."):
            docs = db.collection(NAMA_DATABASE).stream()
            semua_data = []
            for doc in docs:
                doc_data = doc.to_dict()
                if 'data' in doc_data:
                    semua_data.extend(doc_data['data'])
                    
            if semua_data:
                df_export = pd.DataFrame(semua_data)
                kolom_harapan = ['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk', 'Saldo Awal', 'In', 'Out', 'Sisa', 'Keterangan', 'Status']
                
                for c in kolom_harapan:
                    if c not in df_export.columns:
                        df_export[c] = ""
                        
                df_export = df_export[kolom_harapan]
                
                csv_buffer = io.StringIO()
                df_export.to_csv(csv_buffer, index=False, sep=";")
                csv_data = csv_buffer.getvalue()
                
                st.success("✅ Data berhasil ditarik! Klik tombol di bawah untuk mengunduh.")
                st.download_button(
                    label="⬇️ Unduh Data (CSV)",
                    data=csv_data,
                    file_name="Backup_Racking_Hokkan.csv",
                    mime="text/csv"
                )
            else:
                st.warning("Database saat ini kosong. Tidak ada data yang bisa diunduh.")

# ----------------------------------------------------------
# TAB 4: LAPORAN MUTASI HARIAN (FITUR BARU)
# ----------------------------------------------------------
with tab4:
    st.header("📊 Buku Laporan Mutasi (Log Keluar/Masuk)")
    st.caption("Semua histori transaksi Inbound dan Outbound yang Anda lakukan di aplikasi ini akan tercatat rapi di sini secara otomatis.")
    
    if st.button("🔄 Segarkan Data Mutasi", type="primary"):
        st.rerun()
        
    with st.spinner("Memuat buku catatan..."):
        try:
            # --- PERBAIKAN: Menggunakan Timestamp_System agar tidak error spasi ---
            log_docs = db.collection(DB_LOG_MUTASI).order_by("Timestamp_System", direction=firestore.Query.DESCENDING).limit(100).stream()
            
            log_list = []
            for d in log_docs:
                log_list.append(d.to_dict())
                
            if log_list:
                df_log = pd.DataFrame(log_list)
                
                ordered_cols = ["Waktu Transaksi", "Jenis Transaksi", "Lokasi Rak", "Nama Produk", "No Lot", "Mutasi Pallet", "Mutasi Pecahan (Dus/Sak)", "Part Number", "Keterangan"]
                avail_cols = [c for c in ordered_cols if c in df_log.columns]
                df_log = df_log[avail_cols]
                
                st.dataframe(df_log, use_container_width=True, hide_index=True)
                
                csv_log_buffer = io.StringIO()
                df_log.to_csv(csv_log_buffer, index=False, sep=";")
                csv_log_data = csv_log_buffer.getvalue()
                
                st.download_button(
                    label="⬇️ Unduh Laporan Mutasi (CSV)",
                    data=csv_log_data,
                    file_name=f"Mutasi_WMS_{(datetime.utcnow() + timedelta(hours=7)).strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    help="Unduh catatan In/Out untuk dilaporkan ke manajemen."
                )
            else:
                st.info("Belum ada transaksi mutasi yang tercatat hari ini.")
        except Exception as e:
            st.error(f"Gagal memuat log mutasi: {e}")
