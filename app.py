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
# 3. TABS MENU UTAMA
# ==========================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📥 1. Inbound", "📤 2. Outbound", "⚙️ 3. Audit & Backup", "📊 4. Log Mutasi", "🔎 5. Pelacak FIFO"])

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
                                is_all_box_actual = False; break
                                
                        max_slot_actual = 32 if (is_all_box_actual and b_pilih <= 16) else (46 if is_all_box_actual else (31 if b_pilih <= 16 else 44))
                        
                        filled_pallets = [int(float(d.get('Pallet Ke', 0) or 0)) for d in baris_data_actual if pd.notna(d.get('Pallet Ke')) and str(d.get('Pallet Ke')).strip() != '']
                        current_qty_actual = len(filled_pallets)
                        
                        slot_tambahan_in = 1 if (in_pecahan > 0 or in_eceran > 0) else 0
                        total_inbound_slots = in_qty + slot_tambahan_in
                        
                        if current_qty_actual + total_inbound_slots > max_slot_actual:
                            st.error(f"❌ **Kapasitas Penuh!** Rak maksimal muat {max_slot_actual}. Sisa ruang {max_slot_actual - current_qty_actual} slot.")
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
                                
                            if in_pecahan > 0 or in_eceran > 0:
                                while next_pallet in filled_pallets: next_pallet += 1
                                
                                ket_parts = []
                                if in_pecahan > 0: ket_parts.append(f"Pecahan: {in_pecahan} qty")
                                if in_eceran > 0: ket_parts.append(f"Eceran: {in_eceran} pcs")
                                
                                new_records.append({
                                    'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': float(next_pallet),
                                    'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_produk,
                                    'In': 1, 'Out': 0, 'Sisa': 1, 'Status': 'Terisi',
                                    'Keterangan': " | ".join(ket_parts)
                                })
                                filled_pallets.append(next_pallet)
                                
                            all_data.extend(new_records)
                            
                            st.session_state['undo_data'] = data_sebelum
                            st.session_state['redo_data'] = copy.deepcopy(all_data)
                            st.session_state['action_kolom'] = k_pilih
                            st.session_state['action_msg'] = f"Inbound {total_inbound_slots} Slot Fisik '{in_produk}' ke Rak {k_pilih}-{b_pilih}"
                            st.session_state['action_status'] = 'DONE'
                            
                            doc_ref.set({'data': all_data})
                            
                            catat_log_mutasi("INBOUND", k_pilih, b_pilih, in_produk, in_part, in_lot, in_qty, in_pecahan, in_eceran)
                            
                            st.success(f"✅ Berhasil menyuntikkan data! Memakan {total_inbound_slots} Slot Fisik di rak {k_pilih}-{b_pilih}.")
                            st.session_state['in_lokasi_dicari'] = False
                            time.sleep(2)
                            st.rerun()
        else:
            st.info("👈 Silakan tentukan Lokasi Kolom dan Baris di sebelah kiri, lalu klik 'Cek Status Rak'.")

# ----------------------------------------------------------
# TAB 2: OUTBOUND (PENYESUAIAN SISA BERDASARKAN LOT)
# ----------------------------------------------------------
with tab2:
    st.header("Penyesuaian Sisa Stok (Berdasarkan No. Lot)")
    st.caption("Pilih No. Lot yang dibongkar, lalu masukkan SISA AKHIR fisik rak.")
    
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
                    
                    jml_utuh_awal = df_lot[~df_lot['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                    pecahan_rows = df_lot[df_lot['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
                    
                    pecahan_awal = 0
                    eceran_awal = 0
                    if not pecahan_rows.empty:
                        ket_str = str(pecahan_rows['Keterangan'].iloc[0]).lower()
                        match_pec = re.search(r'pecahan:\s*(\d+)', ket_str)
                        if match_pec: pecahan_awal = int(match_pec.group(1))
                        else:
                            numbers = re.findall(r'\d+', ket_str)
                            if numbers and 'pecahan' in ket_str: pecahan_awal = int(numbers[0])
                            
                        match_ec = re.search(r'eceran:\s*(\d+)', ket_str)
                        if match_ec: eceran_awal = int(match_ec.group(1))
                    
                    st.markdown(f"**📦 Total Lot `{pilih_lot}` di Rak ini: `{current_qty_lot}` Slot Fisik**")
                    st.caption(f"*(Fisik asli: {jml_utuh_awal} Pallet Utuh, {pecahan_awal} Dus/Sak, {eceran_awal} Pcs Eceran)*")
                    
                    kolom_tampil = ['Pallet Ke', 'Part Number', 'Nama Produk', 'Lot Number', 'Keterangan']
                    kolom_tersedia = [c for c in kolom_tampil if c in df_lot.columns]
                    st.dataframe(df_lot[kolom_tersedia].head(5), hide_index=True)
                    
                    col_sisa1, col_sisa2, col_sisa3 = st.columns(3)
                    with col_sisa1:
                        out_sisa_akhir = st.number_input(f"Sisa Pallet UTUH Fisik", min_value=0, max_value=jml_utuh_awal, step=1, value=jml_utuh_awal)
                    with col_sisa2:
                        out_sisa_dus = st.number_input("Sisa Pecahan Fisik (Dus/Sak)", min_value=0, step=1, value=pecahan_awal)
                    with col_sisa3:
                        out_sisa_pcs = st.number_input("Sisa Eceran Fisik (Pcs)", min_value=0, step=1, value=eceran_awal)
                
                    if st.button("🚜 Sesuaikan Stok Lot Ini (OUTBOUND)", type="primary", use_container_width=True):
                        slot_tambahan = 1 if (out_sisa_dus > 0 or out_sisa_pcs > 0) else 0
                        total_slot_dibutuhkan = out_sisa_akhir + slot_tambahan
                        
                        if total_slot_dibutuhkan > current_qty_lot:
                            st.error(f"❌ Tidak logis! Anda mengklaim sisa fisik {total_slot_dibutuhkan} Slot, padahal di sistem hanya ada {current_qty_lot} Pallet.")
                        elif out_sisa_akhir == jml_utuh_awal and out_sisa_dus == pecahan_awal and out_sisa_pcs == eceran_awal:
                            st.info("Sisa stok persis sama dengan di sistem. Tidak ada pergerakan Outbound yang dicatat.")
                        else:
                            keluar_utuh = jml_utuh_awal - out_sisa_akhir
                            keluar_pecahan = pecahan_awal - out_sisa_dus
                            keluar_eceran = eceran_awal - out_sisa_pcs
                            
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
                                
                                if b_val == float(b_pilih) and l_str == pilih_lot:
                                    sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
                                    if not sisa_pallets.empty and p_val == sisa_pallets['Pallet Ke'].max():
                                        if out_sisa_dus > 0 or out_sisa_pcs > 0:
                                            ket_parts = []
                                            if out_sisa_dus > 0: ket_parts.append(f"Pecahan: {out_sisa_dus} qty")
                                            if out_sisa_pcs > 0: ket_parts.append(f"Eceran: {out_sisa_pcs} pcs")
                                            d['Keterangan'] = " | ".join(ket_parts)
                                        else:
                                            if 'Pecahan' in str(d.get('Keterangan', '')) or 'Eceran' in str(d.get('Keterangan', '')):
                                                d['Keterangan'] = ""
                                    else:
                                        if 'Pecahan' in str(d.get('Keterangan', '')) or 'Eceran' in str(d.get('Keterangan', '')):
                                            d['Keterangan'] = ""
                                            
                                new_all_out_data.append(d)
                            
                            st.session_state['undo_data'] = data_sebelum_out
                            st.session_state['redo_data'] = copy.deepcopy(new_all_out_data)
                            st.session_state['action_kolom'] = k_pilih
                            st.session_state['action_msg'] = f"Penyesuaian Outbound Lot '{pilih_lot}' di Rak {k_pilih}-{b_pilih}"
                            st.session_state['action_status'] = 'DONE'
                            
                            doc_ref_out.set({'data': new_all_out_data})
                            
                            nama_prod_keluar = str(df_lot['Nama Produk'].iloc[0])
                            part_num_keluar = str(df_lot['Part Number'].iloc[0])
                            catat_log_mutasi("OUTBOUND (Penyesuaian)", k_pilih, b_pilih, nama_prod_keluar, part_num_keluar, pilih_lot, keluar_utuh, keluar_pecahan, keluar_eceran)
                            
                            st.success(f"✅ Selesai! Sisa di rak menjadi {total_slot_dibutuhkan} Slot Fisik.")
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
# TAB 3: PENGATURAN, AUDIT, & BACKUP DATA (CSV SUPER)
# ----------------------------------------------------------
with tab3:
    st.header("⚙️ Audit Stok & Ekspor Data")
    st.subheader("📥 Tarik Laporan Audit (CSV Super)")
    st.write("Sistem akan otomatis menghitung total pcs tiap baris rak menggunakan referensi Master Produk. Bebas angka desimal (koma nol)!")
    
    if st.button("Racik Laporan Audit Stok Sekarang", type="primary"):
        with st.spinner("Membaca database dan mengkalkulasi Pcs..."):
            docs = db.collection(NAMA_DATABASE).stream()
            semua_data = []
            for doc in docs:
                doc_data = doc.to_dict()
                if 'data' in doc_data:
                    semua_data.extend(doc_data['data'])
                    
            if semua_data:
                df_export = pd.DataFrame(semua_data)
                
                # --- LOAD MASTER PRODUK UNTUK KALKULASI ---
                try:
                    try: df_master = pd.read_csv("Master_Produk.csv", sep=";")
                    except: df_master = pd.read_csv("Master_Produk.csv", sep=",")
                    df_master['Part Number Clean'] = df_master['Part Number'].astype(str).str.strip().str.upper()
                except:
                    df_master = pd.DataFrame()
                    st.warning("Master_Produk.csv tidak ditemukan, konversi Pcs mungkin bernilai 0.")

                # --- PROSES KALKULASI PER BARIS (CSV SUPER) ---
                qty_utuh_list = []
                qty_dus_list = []
                qty_pcs_list = []
                grand_total_list = []
                
                for idx, row in df_export.iterrows():
                    # Abaikan data hantu
                    if pd.isna(row.get('Part Number')) or str(row.get('Part Number')).strip() == '':
                        qty_utuh_list.append(0); qty_dus_list.append(0); qty_pcs_list.append(0); grand_total_list.append(0)
                        continue
                        
                    ket = str(row.get('Keterangan', '')).lower()
                    is_pecahan = 'pecahan' in ket or 'eceran' in ket
                    
                    q_utuh = 0 if is_pecahan else 1
                    q_dus = 0
                    q_pcs = 0
                    
                    if is_pecahan:
                        match_pec = re.search(r'pecahan:\s*(\d+)', ket)
                        if match_pec: q_dus = int(match_pec.group(1))
                        else:
                            nums = re.findall(r'\d+', ket)
                            if nums and 'pecahan' in ket: q_dus = int(nums[0])
                            
                        match_ec = re.search(r'eceran:\s*(\d+)', ket)
                        if match_ec: q_pcs = int(match_ec.group(1))
                        
                    qty_utuh_list.append(q_utuh)
                    qty_dus_list.append(q_dus)
                    qty_pcs_list.append(q_pcs)
                    
                    part_num_clean = str(row.get('Part Number', '')).strip().upper()
                    isi_pallet = 0; pcs_per_satuan = 0
                    
                    if not df_master.empty and part_num_clean in df_master['Part Number Clean'].values:
                        m_row = df_master[df_master['Part Number Clean'] == part_num_clean].iloc[0]
                        isi_pallet = pd.to_numeric(m_row.get('Isi 1 Pallet (pcs)', 0), errors='coerce')
                        isi_pallet = 0 if pd.isna(isi_pallet) else isi_pallet
                        
                        is_box = part_num_clean.endswith('D')
                        val_satuan = pd.to_numeric(m_row.get('Box / Pallet' if is_box else 'Sac / Pallet', 0), errors='coerce')
                        pcs_per_satuan = 0 if pd.isna(val_satuan) else val_satuan
                        
                    tot_pcs = (q_utuh * isi_pallet) + (q_dus * pcs_per_satuan) + q_pcs
                    grand_total_list.append(tot_pcs)
                    
                df_export['Qty Utuh'] = qty_utuh_list
                df_export['Qty Dus/Sak'] = qty_dus_list
                df_export['Qty Pcs Eceran'] = qty_pcs_list
                df_export['Grand Total Pcs'] = grand_total_list
                
                # --- KOLOM FINAL CSV AUDIT ---
                kolom_harapan = ['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk', 'Keterangan', 'Status', 'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs']
                
                for c in kolom_harapan:
                    if c not in df_export.columns:
                        df_export[c] = ""
                        
                # Menghilangkan angka desimal (.0)
                for col in ['Baris', 'Pallet Ke', 'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs']:
                    df_export[col] = pd.to_numeric(df_export[col], errors='coerce').fillna(0).astype(int)
                    if col in ['Baris', 'Pallet Ke']:
                        df_export[col] = df_export[col].replace(0, "") # Biarkan kosong jika 0
                        
                df_export = df_export[kolom_harapan]
                
                csv_buffer = io.StringIO()
                df_export.to_csv(csv_buffer, index=False, sep=";")
                csv_data = csv_buffer.getvalue()
                
                st.success("✅ CSV Super berhasil diracik! Kolom 'In' dan 'Out' yang usang sudah diganti dengan kalkulasi stok akurat.")
                st.download_button(label="⬇️ Unduh CSV Super (Untuk Pivot Excel)", data=csv_data, file_name=f"Audit_Stok_WMS_{(datetime.utcnow() + timedelta(hours=7)).strftime('%Y%m%d')}.csv", mime="text/csv")
            else:
                st.warning("Database saat ini kosong. Tidak ada data yang bisa diunduh.")

# ----------------------------------------------------------
# TAB 4: LAPORAN MUTASI HARIAN
# ----------------------------------------------------------
with tab4:
    st.header("📊 Buku Laporan Mutasi (Log Keluar/Masuk)")
    st.caption("Semua histori transaksi Inbound dan Outbound yang Anda lakukan di aplikasi ini akan tercatat rapi di sini secara otomatis.")
    
    if st.button("🔄 Segarkan Data Mutasi", type="primary"):
        st.rerun()
        
    with st.spinner("Memuat buku catatan..."):
        try:
            log_docs = db.collection(DB_LOG_MUTASI).order_by("Timestamp_System", direction=firestore.Query.DESCENDING).limit(100).stream()
            log_list = []
            for d in log_docs: log_list.append(d.to_dict())
                
            if log_list:
                df_log = pd.DataFrame(log_list)
                ordered_cols = ["Waktu Transaksi", "Jenis Transaksi", "Lokasi Rak", "Nama Produk", "No Lot", "Mutasi Pallet", "Mutasi Pecahan (Dus/Sak)", "Mutasi Eceran (Pcs)", "Part Number", "Keterangan"]
                avail_cols = [c for c in ordered_cols if c in df_log.columns]
                df_log = df_log[avail_cols]
                
                st.dataframe(df_log, use_container_width=True, hide_index=True)
                
                csv_log_buffer = io.StringIO()
                df_log.to_csv(csv_log_buffer, index=False, sep=";")
                csv_log_data = csv_log_buffer.getvalue()
                
                st.download_button(label="⬇️ Unduh Laporan Mutasi (CSV)", data=csv_log_data, file_name=f"Mutasi_WMS_{(datetime.utcnow() + timedelta(hours=7)).strftime('%Y%m%d')}.csv", mime="text/csv")
            else:
                st.info("Belum ada transaksi mutasi yang tercatat hari ini.")
        except Exception as e:
            st.error(f"Gagal memuat log mutasi: {e}")

# ----------------------------------------------------------
# TAB 5: PELACAK LOKASI (FIFO) - FITUR BARU
# ----------------------------------------------------------
with tab5:
    st.header("🔎 Pelacak Lokasi Rak & FIFO")
    st.caption("Gunakan alat ini untuk mencari di rak mana saja sebuah produk tersebar. Hasil otomatis diurutkan dari Lot yang tertua.")
    
    with st.spinner("Memindai seluruh isi gudang..."):
        docs = db.collection(NAMA_DATABASE).stream()
        all_fifo_data = []
        for doc in docs:
            d_data = doc.to_dict().get('data', [])
            all_fifo_data.extend(d_data)
            
    df_fifo_raw = pd.DataFrame(all_fifo_data)
    
    if not df_fifo_raw.empty and 'Nama Produk' in df_fifo_raw.columns:
        # Buang data hantu
        df_fifo_raw = df_fifo_raw[df_fifo_raw['Part Number'].astype(str).str.strip() != ""]
        
        if not df_fifo_raw.empty:
            daftar_produk_fifo = sorted(df_fifo_raw['Nama Produk'].astype(str).unique().tolist())
            pilih_produk_fifo = st.selectbox("Pilih Produk yang dicari:", ["-- Pilih Produk --"] + daftar_produk_fifo)
            
            if pilih_produk_fifo != "-- Pilih Produk --":
                df_prod_fifo = df_fifo_raw[df_fifo_raw['Nama Produk'] == pilih_produk_fifo]
                
                summary_fifo = []
                # Kelompokkan berdasarkan No Lot dan Lokasi Rak
                for (lot, k, b), group in df_prod_fifo.groupby(['Lot Number', 'Kolom', 'Baris']):
                    jml_utuh = 0
                    pecahan_val = 0
                    eceran_val = 0
                    
                    for _, row in group.iterrows():
                        ket = str(row.get('Keterangan', '')).lower()
                        is_pecahan = 'pecahan' in ket or 'eceran' in ket
                        if not is_pecahan:
                            jml_utuh += 1
                        else:
                            match_pec = re.search(r'pecahan:\s*(\d+)', ket)
                            if match_pec: pecahan_val += int(match_pec.group(1))
                            else:
                                nums = re.findall(r'\d+', ket)
                                if nums and 'pecahan' in ket: pecahan_val += int(nums[0])
                            
                            match_ec = re.search(r'eceran:\s*(\d+)', ket)
                            if match_ec: eceran_val += int(match_ec.group(1))
                            
                    summary_fifo.append({
                        'Lot Number (Urut FIFO)': str(lot),
                        'Lokasi Rak': f"{k}-{int(float(b))}",
                        'Qty Pallet Utuh': jml_utuh,
                        'Qty Dus/Sak': pecahan_val,
                        'Qty Eceran (Pcs)': eceran_val
                    })
                    
                df_summary = pd.DataFrame(summary_fifo)
                # Mengurutkan berdasarkan No Lot (Alphanumeric dari yang terkecil/tertua)
                df_summary = df_summary.sort_values(by=['Lot Number (Urut FIFO)', 'Lokasi Rak'])
                
                st.success(f"📍 Ditemukan **{len(df_summary)} lokasi penyimpanan** untuk produk ini.")
                st.dataframe(df_summary, use_container_width=True, hide_index=True)
        else:
            st.info("Gudang saat ini kosong (tidak ada data produk riil).")
    else:
        st.info("Gudang saat ini kosong.")
