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
# 1. KONFIGURASI WEB & SISTEM LOGIN
# ==========================================================
st.set_page_config(page_title="WMS Data Entry - Hokkan Deltapack", page_icon="📝", layout="wide")

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False

if not st.session_state['logged_in']:
    st.title("🔒 WMS Security Login")
    st.caption("Silakan masukkan kata sandi untuk mengakses form mutasi gudang.")
    
    correct_password = st.secrets.get("wms_password", "Admin123")
    
    with st.form("login_form"):
        pwd_input = st.text_input("Kata Sandi", type="password")
        submit_btn = st.form_submit_button("Login", type="primary")
        
        if submit_btn:
            if pwd_input == correct_password:
                st.session_state['logged_in'] = True
                st.rerun()
            else:
                st.error("Kata sandi salah!")
    st.stop()

st.sidebar.button("🚪 Logout", on_click=lambda: st.session_state.update({'logged_in': False}))

# ==========================================================
# GLOBAL TOAST NOTIFICATION HANDLER (BUBBLE DARI BAWAH)
# ==========================================================
# Ini akan menangkap pesan sukses dari aksi sebelumnya dan menampilkannya sebagai Bubble
if 'toast_msg' not in st.session_state:
    st.session_state['toast_msg'] = None
if 'toast_icon' not in st.session_state:
    st.session_state['toast_icon'] = "✅"

if st.session_state['toast_msg']:
    st.toast(st.session_state['toast_msg'], icon=st.session_state['toast_icon'])
    # Reset setelah ditampilkan
    st.session_state['toast_msg'] = None 
    st.session_state['toast_icon'] = "✅"

# ==========================================================
# 2. INIT DATABASE (FIREBASE)
# ==========================================================
NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

st.title("📝 WMS Entry Form (Inbound, Outbound & Relokasi)")
st.caption(f"Alat khusus PPIC. Terhubung ke database: {NAMA_DATABASE}")

# ==========================================================
# 3. HELPER FUNCTIONS (CACHE MASTER & CATAT LOG)
# ==========================================================
@st.cache_data(ttl=3600)
def load_master_produk():
    try:
        try: df = pd.read_csv("Master_Produk.csv", sep=";", encoding='utf-8-sig')
        except: df = pd.read_csv("Master_Produk.csv", sep=",", encoding='utf-8-sig')
        df.columns = df.columns.str.strip()
        if 'Part Number' in df.columns:
            df['Part Number Clean'] = df['Part Number'].astype(str).str.strip().str.upper()
        return df
    except Exception as e:
        st.toast(f"Gagal memuat master produk: {e}", icon="🚨")
        return pd.DataFrame()

def catat_log_mutasi(jenis, kolom, baris, produk, part_num, no_lot, pallet_utuh, pecahan, eceran, keterangan=""):
    waktu_sekarang = (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
    no_lot_final = no_lot if str(no_lot).strip() != "" else "-"
    log_data = {
        "Waktu Transaksi": waktu_sekarang, "Jenis Transaksi": jenis, "Lokasi Rak": f"{kolom}-{baris}",
        "Nama Produk": produk, "Part Number": part_num, "No Lot": no_lot_final,
        "Mutasi Pallet": float(pallet_utuh), "Mutasi Pecahan (Dus/Sak)": float(pecahan),
        "Mutasi Eceran (Pcs)": float(eceran), "Keterangan": keterangan,
        "Timestamp_System": firestore.SERVER_TIMESTAMP
    }
    try: db.collection(DB_LOG_MUTASI).add(log_data)
    except Exception as e: st.toast(f"⚠️ Peringatan: Gagal mencatat log mutasi ({e})", icon="⚠️")

# ==========================================================
# 4. FITUR UNDO & REDO
# ==========================================================
if 'undo_data' not in st.session_state:
    st.session_state.update({'undo_data': None, 'redo_data': None, 'action_msg': "", 'action_kolom': "", 'action_status': None})

if st.session_state['action_status'] == 'DONE':
    st.info(f"🔄 **Aksi Terakhir:** {st.session_state['action_msg']}")
    if st.button("🔙 BATALKAN AKSI TERAKHIR (UNDO)", type="primary"):
        with st.spinner("Mengembalikan data seperti semula..."):
            doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state['action_kolom']}")
            doc_ref.set({'data': st.session_state['undo_data']})
            st.session_state['action_status'] = 'UNDONE'
            catat_log_mutasi("PEMBATALAN (UNDO)", st.session_state['action_kolom'], "ALL", "Koreksi Sistem", "-", "-", 0, 0, 0, f"Membatalkan aksi: {st.session_state['action_msg']}")
            
            # TRIGGER TOAST BUBBLE
            st.session_state['toast_msg'] = "Aksi dibatalkan (Undo)! Data kembali seperti semula."
            st.session_state['toast_icon'] = "⏪"
            st.rerun() # Langsung rerun tanpa time.sleep
if st.session_state['action_status'] is not None: st.divider()

# ==========================================================
# 5. TABS MENU UTAMA
# ==========================================================
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📥 1. Inbound", "📤 2. Outbound", "⚙️ 3. Audit & Backup", 
    "📊 4. Log Mutasi", "🚀 5. Sync CSV", "📅 6. Jadwal", "🔄 7. Relokasi Rak"
])

# ----------------------------------------------------------
# TAB 1: INBOUND
# ----------------------------------------------------------
with tab1:
    st.header("Input Barang Masuk")
    if 'in_lokasi_dicari' not in st.session_state:
        st.session_state.update({'in_lokasi_dicari': False, 'in_k_pilih': "A", 'in_b_pilih': 1})

    col_in1, col_in2 = st.columns([1, 2])
    with col_in1:
        st.markdown("**1. Pilih Lokasi Rak Tujuan**")
        in_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="in_kolom")
        in_baris = st.number_input("Nomor Baris (Inbound)", min_value=1, max_value=28, value=1)
        if st.button("🔍 Cek Status Rak", use_container_width=True):
            st.session_state.update({'in_lokasi_dicari': True, 'in_k_pilih': in_kolom, 'in_b_pilih': in_baris})
            
    with col_in2:
        if st.session_state['in_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['in_k_pilih'], st.session_state['in_b_pilih']
            doc_ref_in = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            doc_in = doc_ref_in.get()
            all_data_raw = doc_in.to_dict().get('data', []) if doc_in.exists else []
            
            baris_data_preview = [d for d in all_data_raw if float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
            is_all_box_existing = all(str(d.get('Part Number', '')).strip().upper().endswith(('D', 'G')) for d in baris_data_preview) if baris_data_preview else False
            max_slot_display = 32 if (is_all_box_existing and b_pilih <= 16) else (46 if is_all_box_existing else (31 if b_pilih <= 16 else 44))
            current_qty = len(baris_data_preview)
            sisa_ruang = max_slot_display - current_qty
            
            st.markdown(f"**📦 Status Rak {k_pilih}-{b_pilih} | Terisi: `{current_qty}` / `{max_slot_display}` (Sisa Ruang: `{sisa_ruang}` Slot)**")
            
            df_baris = pd.DataFrame(baris_data_preview)
            if not df_baris.empty:
                df_baris['Pallet Ke'] = pd.to_numeric(df_baris['Pallet Ke'], errors='coerce')
                df_baris = df_baris.sort_values('Pallet Ke', ascending=True)
                kolom_tersedia = [c for c in ['Pallet Ke', 'Part Number', 'Nama Produk', 'Lot Number', 'Keterangan'] if c in df_baris.columns]
                st.dataframe(df_baris[kolom_tersedia], hide_index=True, height=150)
            else:
                st.info("Rak ini masih kosong. Siap diisi penuh!")
                
            st.divider()
            st.markdown(f"**2. Eksekusi Inbound (Tujuan: {k_pilih}-{b_pilih})**")
            
            df_master = load_master_produk()
            daftar_produk = df_master['Nama Barang'].dropna().astype(str).unique().tolist() if not df_master.empty else []
            pilihan_barang = st.selectbox("Pilih Produk", ["-- Ketik Manual --"] + daftar_produk)
            
            if pilihan_barang != "-- Ketik Manual --" and not df_master.empty:
                in_produk = pilihan_barang
                match_part = df_master[df_master['Nama Barang'] == pilihan_barang]['Part Number'].values
                in_part = st.text_input("Part Number (Auto-fill)", value=str(match_part[0]) if len(match_part) > 0 else "", disabled=True)
            else:
                in_produk = st.text_input("Nama Produk (Ketik Manual)")
                in_part = st.text_input("Part Number (Ketik Manual)")
                
            in_lot = st.text_input("No. Lot Produksi / Mesin Shift", placeholder="Kosongkan jika tidak ada")
            
            col_qty1, col_qty2, col_qty3 = st.columns(3)
            with col_qty1: in_qty = st.number_input("Pallet Masuk UTUH", min_value=0, step=1, value=1)
            with col_qty2: in_pecahan = st.number_input("Sisa Pecahan (Dus/Sak)", min_value=0, step=1, value=0)
            with col_qty3: in_eceran = st.number_input("Eceran (Pcs)", min_value=0, step=1, value=0)
            
            in_dari_depan = st.checkbox("📥 Masukkan dari DEPAN Rak (Geser/Dorong pallet lama ke belakang)")
                
            if st.button("🚀 Simpan Inbound", type="primary", use_container_width=True):
                in_part_clean = in_part.strip().upper() if in_part else ""
                in_produk_clean = in_produk.strip() if in_produk else ""
                in_lot_final = in_lot.strip() if in_lot and in_lot.strip() != "" else "-"

                if not in_produk_clean or not in_part_clean:
                    st.error("⚠️ Nama Produk dan Part Number wajib diisi!")
                elif in_qty == 0 and in_pecahan == 0 and in_eceran == 0:
                    st.error("⚠️ Masukkan minimal 1 Qty!")
                else:
                    with st.spinner("Menyusun data ke dalam rak..."):
                        all_data = [d for d in all_data_raw if not (float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() == '')]
                        baris_data_actual = [d for d in all_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
                        
                        is_all_box_actual = in_part_clean.endswith(('D', 'G'))
                        existing_fraction_row = None
                        
                        for d in baris_data_actual:
                            pn = str(d.get('Part Number', '')).strip().upper()
                            if pn and not pn.endswith(('D', 'G')): is_all_box_actual = False
                            if not in_dari_depan and pn == in_part_clean and str(d.get('Lot Number', '')).strip() == in_lot_final:
                                ket = str(d.get('Keterangan', ''))
                                if 'Pecahan' in ket or 'Eceran' in ket: existing_fraction_row = d
                                    
                        max_slot_actual = 32 if (is_all_box_actual and b_pilih <= 16) else (46 if is_all_box_actual else (31 if b_pilih <= 16 else 44))
                        
                        slot_tambahan_in = 0
                        if (in_pecahan > 0 or in_eceran > 0) and not existing_fraction_row: slot_tambahan_in = 1
                        total_inbound_slots = in_qty + slot_tambahan_in
                        
                        new_records = []
                        
                        # --- LOGIKA RE-INDEXING JIKA DARI DEPAN ---
                        if in_dari_depan and baris_data_actual:
                            for d in all_data:
                                if float(d.get('Baris', 0) or 0) == float(b_pilih):
                                    d['Pallet Ke'] = float(d.get('Pallet Ke', 0)) + total_inbound_slots
                            next_pallet = 1
                            filled_pallets = []
                        else:
                            filled_pallets = [int(float(d.get('Pallet Ke', 0) or 0)) for d in baris_data_actual if pd.notna(d.get('Pallet Ke'))]
                            next_pallet = max(filled_pallets) + 1 if filled_pallets else 1
                            
                        # Buat Pallet Utuh
                        for _ in range(int(in_qty)):
                            while not in_dari_depan and next_pallet in filled_pallets: next_pallet += 1
                            is_bayangan = next_pallet > max_slot_actual
                            new_records.append({
                                'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': float(next_pallet),
                                'Part Number': in_part_clean, 'Lot Number': in_lot_final, 'Nama Produk': in_produk_clean,
                                'Status': 'Terisi', 'Keterangan': '', 'Catatan': "[BAYANGAN - OVERCAPACITY]" if is_bayangan else ""
                            })
                            filled_pallets.append(next_pallet)
                            next_pallet += 1
                            
                        # Buat/Update Pecahan
                        if in_pecahan > 0 or in_eceran > 0:
                            if existing_fraction_row and not in_dari_depan:
                                ket_str = str(existing_fraction_row.get('Keterangan', '')).lower()
                                ex_pecahan, ex_eceran = 0, 0
                                match_pec = re.search(r'pecahan:\s*(\d+)', ket_str)
                                if match_pec: ex_pecahan = int(match_pec.group(1))
                                else:
                                    nums = re.findall(r'\d+', ket_str)
                                    if nums and 'pecahan' in ket_str: ex_pecahan = int(nums[0])
                                match_ec = re.search(r'eceran:\s*(\d+)', ket_str)
                                if match_ec: ex_eceran = int(match_ec.group(1))
                                
                                new_pec, new_ec = ex_pecahan + in_pecahan, ex_eceran + in_eceran
                                ket_parts = []
                                if new_pec > 0: ket_parts.append(f"Pecahan: {new_pec} qty")
                                if new_ec > 0: ket_parts.append(f"Eceran: {new_ec} pcs")
                                existing_fraction_row['Keterangan'] = " | ".join(ket_parts)
                            else:
                                while not in_dari_depan and next_pallet in filled_pallets: next_pallet += 1
                                ket_parts = []
                                if in_pecahan > 0: ket_parts.append(f"Pecahan: {in_pecahan} qty")
                                if in_eceran > 0: ket_parts.append(f"Eceran: {in_eceran} pcs")
                                new_records.append({
                                    'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': float(next_pallet),
                                    'Part Number': in_part_clean, 'Lot Number': in_lot_final, 'Nama Produk': in_produk_clean,
                                    'Status': 'Terisi', 'Keterangan': " | ".join(ket_parts), 'Catatan': "[BAYANGAN - OVERCAPACITY]" if next_pallet > max_slot_actual else ""
                                })
                                
                        all_data.extend(new_records)
                        st.session_state.update({'undo_data': all_data_raw, 'redo_data': copy.deepcopy(all_data), 'action_kolom': k_pilih, 'action_msg': f"Inbound {total_inbound_slots} Slot '{in_produk_clean}' ke Rak {k_pilih}-{b_pilih} ({'Dari Depan' if in_dari_depan else 'Dari Belakang'})", 'action_status': 'DONE'})
                        doc_ref_in.set({'data': all_data})
                        catat_log_mutasi("INBOUND", k_pilih, b_pilih, in_produk_clean, in_part_clean, in_lot_final, in_qty, in_pecahan, in_eceran)
                        
                        # TRIGGER TOAST BUBBLE
                        st.session_state['toast_msg'] = f"Inbound berhasil! {'Re-indexing otomatis.' if in_dari_depan else ''}"
                        st.session_state['toast_icon'] = "📥"
                        st.rerun()

# ----------------------------------------------------------
# TAB 2: OUTBOUND MULTI-LOT
# ----------------------------------------------------------
with tab2:
    st.header("Penyesuaian Sisa Stok (Outbound Multi-Lot)")
    st.caption("Ubah angka sisa fisik pada tabel di bawah, lalu klik Simpan untuk mengeksekusi banyak Lot sekaligus.")
    
    if 'out_lokasi_dicari' not in st.session_state: 
        st.session_state.update({'out_lokasi_dicari': False, 'out_k_pilih': "A", 'out_b_pilih': 1})

    col_out1, col_out2 = st.columns([1, 2.5])
    with col_out1:
        st.markdown("**1. Pilih Lokasi Rak**")
        out_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="out_kolom_2")
        out_baris = st.number_input("Nomor Baris (Outbound)", min_value=1, max_value=28, value=1, key="out_baris_2")
        if st.button("🔍 Cari Data Rak", use_container_width=True): 
            st.session_state.update({'out_lokasi_dicari': True, 'out_k_pilih': out_kolom, 'out_b_pilih': out_baris})
            
    with col_out2:
        if st.session_state['out_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['out_k_pilih'], st.session_state['out_b_pilih']
            doc_ref_out = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            doc_out = doc_ref_out.get()
            all_out_data = doc_out.to_dict().get('data', []) if doc_out.exists else []
            baris_out_data = [d for d in all_out_data if float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
            
            if baris_out_data:
                df_out = pd.DataFrame(baris_out_data)
                df_out['Pallet Ke'] = pd.to_numeric(df_out['Pallet Ke'], errors='coerce')
                
                rekap_lot = []
                for _, row in df_out.drop_duplicates(subset=['Lot Number', 'Nama Produk']).iterrows():
                    l = str(row['Lot Number'])
                    nama_prod = str(row['Nama Produk'])
                    part_num = str(row['Part Number'])
                    df_lot = df_out[df_out['Lot Number'].astype(str) == l]
                    
                    jml_utuh_awal = df_lot[~df_lot['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                    pecahan_rows = df_lot[df_lot['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
                    pecahan_awal, eceran_awal = 0, 0
                    
                    for _, r in pecahan_rows.iterrows():
                        ks = str(r['Keterangan']).lower()
                        m1 = re.search(r'pecahan:\s*(\d+)', ks)
                        if m1: pecahan_awal += int(m1.group(1))
                        else:
                            ns = re.findall(r'\d+', ks)
                            if ns and 'pecahan' in ks: pecahan_awal += int(ns[0])
                        m2 = re.search(r'eceran:\s*(\d+)', ks)
                        if m2: eceran_awal += int(m2.group(1))
                    
                    rekap_lot.append({
                        'Produk': nama_prod,
                        'Part Number': part_num,
                        'Lot Number': l,
                        'Awal Utuh': int(jml_utuh_awal),
                        'Awal Dus': int(pecahan_awal),
                        'Awal Pcs': int(eceran_awal),
                        'Sisa Utuh Baru': int(jml_utuh_awal),
                        'Sisa Dus Baru': int(pecahan_awal),
                        'Sisa Pcs Baru': int(eceran_awal),
                        'Catatan Outbound': ""
                    })
                
                df_rekap = pd.DataFrame(rekap_lot)
                
                st.markdown(f"**2. Edit Sisa Fisik Rak {k_pilih}-{b_pilih}**")
                
                config_kolom = {
                    'Produk': st.column_config.TextColumn("Produk", disabled=True),
                    'Part Number': st.column_config.TextColumn("Part", disabled=True),
                    'Lot Number': st.column_config.TextColumn("Lot", disabled=True),
                    'Awal Utuh': st.column_config.NumberColumn("Sistem Utuh", disabled=True),
                    'Awal Dus': st.column_config.NumberColumn("Sistem Dus", disabled=True),
                    'Awal Pcs': st.column_config.NumberColumn("Sistem Pcs", disabled=True),
                    'Sisa Utuh Baru': st.column_config.NumberColumn("SISA UTUH BARU", min_value=0, required=True),
                    'Sisa Dus Baru': st.column_config.NumberColumn("SISA DUS BARU", min_value=0, required=True),
                    'Sisa Pcs Baru': st.column_config.NumberColumn("SISA PCS BARU", min_value=0, required=True),
                    'Catatan Outbound': st.column_config.TextColumn("Catatan", max_chars=100)
                }
                
                edited_df = st.data_editor(
                    df_rekap,
                    column_config=config_kolom,
                    hide_index=True,
                    use_container_width=True,
                    num_rows="fixed"
                )
                
                if st.button("🚜 Simpan Penyesuaian Massal (OUTBOUND)", type="primary", use_container_width=True):
                    has_changes = False
                    error_msg = ""
                    new_all_out_data = []
                    
                    data_rak_ini = [d for d in all_out_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
                    data_rak_lain = [d for d in all_out_data if float(d.get('Baris', 0) or 0) != float(b_pilih)]
                    
                    for index, row in edited_df.iterrows():
                        l = str(row['Lot Number'])
                        sisa_utuh = int(row['Sisa Utuh Baru'])
                        sisa_dus = int(row['Sisa Dus Baru'])
                        sisa_pcs = int(row['Sisa Pcs Baru'])
                        catatan_out = str(row['Catatan Outbound'])
                        
                        awal_utuh = int(row['Awal Utuh'])
                        awal_dus = int(row['Awal Dus'])
                        awal_pcs = int(row['Awal Pcs'])
                        
                        if sisa_utuh != awal_utuh or sisa_dus != awal_dus or sisa_pcs != awal_pcs or catatan_out != "":
                            has_changes = True
                            
                            df_lot = pd.DataFrame([d for d in data_rak_ini if str(d.get('Lot Number', '')) == l])
                            if not df_lot.empty:
                                df_lot['Pallet Ke'] = pd.to_numeric(df_lot['Pallet Ke'])
                                df_lot = df_lot.sort_values('Pallet Ke', ascending=False)
                                
                                slot_tambahan = 1 if (sisa_dus > 0 or sisa_pcs > 0) else 0
                                total_slot_dibutuhkan = sisa_utuh + slot_tambahan
                                
                                if total_slot_dibutuhkan > len(df_lot):
                                    error_msg = f"❌ Sisa akhir Lot {l} melebihi kapasitas awal."
                                    break
                                
                                out_qty = len(df_lot) - total_slot_dibutuhkan
                                pallets_to_remove = df_lot['Pallet Ke'].head(out_qty).tolist() if out_qty > 0 else []
                                
                                updated_rak_ini = []
                                for d in data_rak_ini:
                                    p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                                    if str(d.get('Lot Number', '')) == l:
                                        if p_val in pallets_to_remove: continue 
                                        d['Catatan'] = catatan_out
                                        sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
                                        
                                        if not sisa_pallets.empty and p_val == sisa_pallets['Pallet Ke'].max():
                                            if sisa_dus > 0 or sisa_pcs > 0:
                                                kp = []
                                                if sisa_dus > 0: kp.append(f"Pecahan: {sisa_dus} qty")
                                                if sisa_pcs > 0: kp.append(f"Eceran: {sisa_pcs} pcs")
                                                d['Keterangan'] = " | ".join(kp)
                                            else:
                                                d['Keterangan'] = ""
                                        else:
                                            if 'Pecahan' in str(d.get('Keterangan', '')) or 'Eceran' in str(d.get('Keterangan', '')): 
                                                d['Keterangan'] = ""
                                                
                                    updated_rak_ini.append(d)
                                data_rak_ini = updated_rak_ini
                                
                                keluar_utuh = awal_utuh - sisa_utuh
                                keluar_pecahan = awal_dus - sisa_dus
                                keluar_eceran = awal_pcs - sisa_pcs
                                catat_log_mutasi("OUTBOUND (Massal)", k_pilih, b_pilih, str(row['Produk']), str(row['Part Number']), l, keluar_utuh, keluar_pecahan, keluar_eceran, catatan_out)

                    if error_msg: st.error(error_msg)
                    elif has_changes:
                        data_rak_ini = sorted([d for d in data_rak_ini if float(d.get('Baris', 0) or 0) == float(b_pilih)], key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                        for i, d in enumerate(data_rak_ini, 1): d['Pallet Ke'] = float(i)
                        
                        doc_ref_out.set({'data': data_rak_lain + data_rak_ini})
                        st.session_state.update({'undo_data': all_out_data, 'action_kolom': k_pilih, 'action_msg': f"Outbound Massal di {k_pilih}-{b_pilih}", 'action_status': 'DONE'})
                        
                        # TRIGGER TOAST BUBBLE
                        st.session_state['toast_msg'] = "Penyesuaian Outbound Massal berhasil & Re-indexed."
                        st.session_state['toast_icon'] = "🚜"
                        st.rerun()
                    else: st.info("Tidak ada perubahan angka pada tabel. Outbound dibatalkan.")
            else: st.info("Rak Kosong.")

# ----------------------------------------------------------
# TAB 3: AUDIT STOK & BACKUP
# ----------------------------------------------------------
with tab3:
    st.header("⚙️ Audit Stok & Ekspor Data")
    st.subheader("🔧 Maintenance: Re-Indexing Global")
    st.write("Mengurutkan ulang urutan palet dari nomor 1 di seluruh gudang.")
    
    if 'last_reindex' not in st.session_state: st.session_state['last_reindex'] = "Belum pernah"
    st.caption(f"🕒 Terakhir Re-Index: **{st.session_state['last_reindex']}**")
    
    if st.button("⚙️ Jalankan Re-Indexing Semua Rak Sekarang", type="secondary"):
        with st.spinner("Memperbaiki urutan index pallet..."):
            try:
                docs = db.collection(NAMA_DATABASE).stream()
                batch = db.batch()
                total_koreksi = 0
                
                for doc in docs:
                    kolom_id = doc.id
                    doc_data = doc.to_dict().get('data', [])
                    if not doc_data: continue
                    
                    df_col = pd.DataFrame(doc_data)
                    if 'Baris' not in df_col.columns or 'Pallet Ke' not in df_col.columns: continue
                    
                    df_col['Baris'] = pd.to_numeric(df_col['Baris'], errors='coerce')
                    df_col['Pallet Ke'] = pd.to_numeric(df_col['Pallet Ke'], errors='coerce')
                    new_doc_data = []
                    
                    for baris, group in df_col.groupby('Baris'):
                        group = group.sort_values('Pallet Ke', ascending=True)
                        valid_group = group[~group['Part Number'].astype(str).str.strip().str.lower().isin(["", "nan", "none", "<na>"])]
                        is_all_box = False
                        if not valid_group.empty:
                            is_all_box = bool(valid_group['Part Number'].astype(str).str.upper().str.endswith(('D', 'G')).all())
                            
                        max_slot_actual = int(32 if baris <= 16 else 46) if is_all_box else int(31 if baris <= 16 else 44)
                        
                        for i, (idx, row) in enumerate(group.iterrows(), start=1):
                            row_dict = row.to_dict()
                            row_dict['Pallet Ke'] = float(i)
                            catatan_lama = str(row_dict.get('Catatan', ''))
                            if i <= max_slot_actual and 'BAYANGAN' in catatan_lama.upper():
                                row_dict['Catatan'] = catatan_lama.replace("[BAYANGAN - OVERCAPACITY]", "").strip()
                            elif i > max_slot_actual and 'BAYANGAN' not in catatan_lama.upper():
                                row_dict['Catatan'] = ("[BAYANGAN - OVERCAPACITY] " + catatan_lama).strip()
                            new_doc_data.append(row_dict)
                    
                    doc_ref = db.collection(NAMA_DATABASE).document(kolom_id)
                    batch.set(doc_ref, {'data': new_doc_data})
                    total_koreksi += 1
                
                batch.commit()
                st.session_state['last_reindex'] = (datetime.utcnow() + timedelta(hours=7)).strftime("%d-%m-%Y %H:%M:%S")
                
                # BUBBLE & BALLOONS!
                st.balloons()
                st.session_state['toast_msg'] = f"Re-Indexing Selesai! {total_koreksi} Kolom diperbarui."
                st.session_state['toast_icon'] = "✨"
                time.sleep(1) # Jeda sebentar agar balon terlihat sblm rerun
                st.rerun()
            except Exception as e: st.error(f"Gagal Re-Indexing: {e}")

    st.divider()
    st.subheader("📥 Tarik Laporan Audit (CSV Super)")
    if st.button("Racik Laporan Audit Stok Sekarang", type="primary"):
        with st.spinner("Membaca database..."):
            docs = db.collection(NAMA_DATABASE).stream()
            semua_data = []
            for doc in docs:
                doc_data = doc.to_dict()
                if 'data' in doc_data: semua_data.extend(doc_data['data'])
                    
            if semua_data:
                df_export = pd.DataFrame(semua_data)
                df_master = load_master_produk()
                
                qty_utuh_list, qty_dus_list, qty_pcs_list, grand_total_list = [], [], [], []
                for idx, row in df_export.iterrows():
                    if pd.isna(row.get('Part Number')) or str(row.get('Part Number')).strip() == '':
                        qty_utuh_list.append(0); qty_dus_list.append(0); qty_pcs_list.append(0); grand_total_list.append(0)
                        continue
                        
                    ket = str(row.get('Keterangan', '')).lower()
                    is_pecahan = 'pecahan' in ket or 'eceran' in ket
                    q_utuh = 0 if is_pecahan else 1
                    q_dus, q_pcs = 0, 0
                    
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
                    isi_pallet, pcs_per_satuan = 0, 0
                    if not df_master.empty and part_num_clean in df_master['Part Number Clean'].values:
                        m_row = df_master[df_master['Part Number Clean'] == part_num_clean].iloc[0]
                        isi_pallet = pd.to_numeric(m_row.get('Isi 1 Pallet (pcs)', 0), errors='coerce')
                        isi_pallet = 0 if pd.isna(isi_pallet) else isi_pallet
                        val_satuan = pd.to_numeric(m_row.get('Box / Pallet' if part_num_clean.endswith('D') else 'Sac / Pallet', 0), errors='coerce')
                        pcs_per_satuan = 0 if pd.isna(val_satuan) else val_satuan
                        
                    grand_total_list.append((q_utuh * isi_pallet) + (q_dus * pcs_per_satuan) + q_pcs)
                    
                df_export['Qty Utuh'] = qty_utuh_list
                df_export['Qty Dus/Sak'] = qty_dus_list
                df_export['Qty Pcs Eceran'] = qty_pcs_list
                df_export['Grand Total Pcs'] = grand_total_list
                
                kolom_harapan = ['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk', 'Keterangan', 'Catatan', 'Status', 'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs']
                for c in kolom_harapan:
                    if c not in df_export.columns: df_export[c] = ""
                        
                for col in ['Baris', 'Pallet Ke', 'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs']:
                    df_export[col] = pd.to_numeric(df_export[col], errors='coerce').fillna(0).astype(int)
                    if col in ['Baris', 'Pallet Ke']: df_export[col] = df_export[col].replace(0, "")
                        
                df_export = df_export[kolom_harapan]
                csv_buffer = io.StringIO()
                df_export.to_csv(csv_buffer, index=False, sep=";")
                st.success("✅ CSV Super berhasil diracik!")
                st.download_button(label="⬇️ Unduh CSV Super", data=csv_buffer.getvalue(), file_name=f"Audit_Stok_WMS_{(datetime.utcnow() + timedelta(hours=7)).strftime('%Y%m%d')}.csv", mime="text/csv")
            else: st.warning("Database kosong.")

# ----------------------------------------------------------
# TAB 4: LAPORAN MUTASI HARIAN
# ----------------------------------------------------------
with tab4:
    st.header("📊 Laporan Mutasi Harian (24 Jam Terakhir)")
    if st.button("Racik Laporan Mutasi Sekarang", type="primary"):
        with st.spinner("Menarik data log mutasi..."):
            try:
                waktu_24_jam_lalu_utc = datetime.utcnow() - timedelta(hours=24)
                log_docs = db.collection(DB_LOG_MUTASI).where("Timestamp_System", ">=", waktu_24_jam_lalu_utc).order_by("Timestamp_System", direction=firestore.Query.DESCENDING).stream()
                log_list = [d.to_dict() for d in log_docs]
                    
                if log_list:
                    df_log = pd.DataFrame(log_list)
                    avail_cols = [c for c in ["Waktu Transaksi", "Jenis Transaksi", "Lokasi Rak", "Nama Produk", "No Lot", "Mutasi Pallet", "Mutasi Pecahan (Dus/Sak)", "Mutasi Eceran (Pcs)", "Part Number", "Keterangan"] if c in df_log.columns]
                    df_log = df_log[avail_cols]
                    
                    csv_log_buffer = io.StringIO()
                    df_log.to_csv(csv_log_buffer, index=False, sep=";")
                    st.success("✅ Laporan Mutasi berhasil diracik!")
                    st.download_button(label="⬇️ Unduh Laporan Mutasi", data=csv_log_buffer.getvalue(), file_name=f"Mutasi_WMS_{(datetime.utcnow() + timedelta(hours=7)).strftime('%Y%m%d')}.csv", mime="text/csv")
                else: st.info("Belum ada mutasi dalam 24 jam terakhir.")
            except Exception as e: st.error(f"Gagal memuat log mutasi: {e}")

# ----------------------------------------------------------
# TAB 5: SYNC CSV
# ----------------------------------------------------------
with tab5:
    st.header("🚀 Sinkronisasi CSV / Master")
    st.info("Fitur Upload Master/Sinkronisasi awal sistem. (Masih dalam tahap pengembangan).")

# ----------------------------------------------------------
# TAB 6: PENJADWALAN KIRIM
# ----------------------------------------------------------
with tab6:
    st.header("📅 Penjadwalan Pengiriman (Booking Pallet)")
    if 'book_lokasi_dicari' not in st.session_state: st.session_state.update({'book_lokasi_dicari': False, 'book_k_pilih': "A", 'book_b_pilih': 1})

    col_b1, col_b2 = st.columns([1, 2])
    with col_b1:
        st.markdown("**1. Pilih Lokasi Rak**")
        book_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="book_kolom_3")
        book_baris = st.number_input("Nomor Baris (Booking)", min_value=1, max_value=28, value=1, key="book_baris_3")
        if st.button("🔍 Cari Data Rak (Booking)", use_container_width=True): st.session_state.update({'book_lokasi_dicari': True, 'book_k_pilih': book_kolom, 'book_b_pilih': book_baris})
            
    with col_b2:
        if st.session_state['book_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['book_k_pilih'], st.session_state['book_b_pilih']
            doc_ref_book = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            doc_book = doc_ref_book.get()
            all_book_data = doc_book.to_dict().get('data', []) if doc_book.exists else []
            baris_book_data = [d for d in all_book_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
            
            if baris_book_data:
                df_book = pd.DataFrame(baris_book_data)
                df_book['Pallet Ke'] = pd.to_numeric(df_book['Pallet Ke'], errors='coerce')
                df_book = df_book[df_book['Part Number'].astype(str).str.strip() != ""]
                
                if not df_book.empty and 'Lot Number' in df_book.columns:
                    tersedia_lot = []
                    mapping_lot = {}
                    for _, row in df_book.drop_duplicates(subset=['Lot Number', 'Nama Produk']).iterrows():
                        l = str(row['Lot Number'])
                        lb = f"{str(row['Nama Produk'])} (Lot: {l})"
                        tersedia_lot.append(lb)
                        mapping_lot[lb] = l
                        
                    st.markdown(f"**2. Pilih Produk & Lot untuk di-Booking di {k_pilih}-{b_pilih}**")
                    pilihan_label = st.selectbox("🎯 Pilih Produk:", tersedia_lot, key="book_lot")
                    pilih_lot = mapping_lot[pilihan_label]
                    
                    df_lot = df_book[df_book['Lot Number'].astype(str) == pilih_lot].sort_values('Pallet Ke', ascending=True)
                    df_belum_book = df_lot[~df_lot['Catatan'].astype(str).str.contains('BOOKED', case=False, na=False)]
                    qty_tersedia = len(df_belum_book)
                    
                    st.markdown(f"**📦 Tersedia untuk di-Booking: `{qty_tersedia}` Slot Fisik**")
                    
                    kolom_tersedia = [c for c in ['Pallet Ke', 'Part Number', 'Lot Number', 'Catatan'] if c in df_lot.columns]
                    st.dataframe(df_lot[kolom_tersedia], hide_index=True, height=150)
                    
                    if qty_tersedia > 0:
                        book_qty = st.number_input("Jumlah Pallet UTUH yang akan di-Booking", min_value=1, max_value=qty_tersedia, step=1, value=qty_tersedia)
                        book_do = st.text_input("📝 Terjadwal Untuk (Cth: DO-123 / Tgl 28)")
                        
                        if st.button("🔒 Kunci / Booking Pallet Sekarang", type="primary", use_container_width=True):
                            if book_do.strip() == "": st.error("❌ Keterangan jadwal wajib diisi!")
                            else:
                                with st.spinner("Menyematkan label booking..."):
                                    pallets_to_book = df_belum_book['Pallet Ke'].head(book_qty).tolist()
                                    new_all_book_data = []
                                    for d in all_book_data:
                                        b_val = float(d.get('Baris', 0) or 0)
                                        p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                                        if b_val == float(b_pilih) and p_val in pallets_to_book:
                                            catatan_lama = str(d.get('Catatan', ''))
                                            d['Catatan'] = (f"[BOOKED: {book_do.strip()}] " + catatan_lama).strip()
                                        new_all_book_data.append(d)
                                        
                                    doc_ref_book.set({'data': new_all_book_data})
                                    st.session_state.update({'undo_data': all_book_data, 'action_kolom': k_pilih, 'action_msg': f"Booking {book_qty} Pallet Lot '{pilih_lot}' untuk '{book_do}'", 'action_status': 'DONE'})
                                    
                                    # TRIGGER TOAST BUBBLE
                                    st.session_state['toast_msg'] = f"Berhasil! {book_qty} pallet dikunci untuk pengiriman."
                                    st.session_state['toast_icon'] = "🔒"
                                    st.rerun()
                    else: st.warning("⚠️ Semua pallet pada Lot ini sudah ter-booking.")
            else: st.info("Rak Kosong.")

# ----------------------------------------------------------
# TAB 7: RELOKASI RAK
# ----------------------------------------------------------
with tab7:
    st.header("🔄 Relokasi Pallet (Pindah Rak / Subsidi Silang)")
    st.caption("Pindahkan item dari Rak Asal ke Rak Tujuan (Re-indexing otomatis).")
    
    col_r1, col_r2 = st.columns(2)
    with col_r1:
        st.markdown("**Rak ASAL (Sumber)**")
        r_asal_k = st.selectbox("Kolom Asal", ["A", "B", "C", "D", "E", "F", "G"], key="r_ak")
        r_asal_b = st.number_input("Baris Asal", min_value=1, max_value=28, value=1, key="r_ab")
    with col_r2:
        st.markdown("**Rak TUJUAN (Destinasi)**")
        r_tuj_k = st.selectbox("Kolom Tujuan", ["A", "B", "C", "D", "E", "F", "G"], key="r_tk")
        r_tuj_b = st.number_input("Baris Tujuan", min_value=1, max_value=28, value=1, key="r_tb")

    if st.button("🔍 Cek Rak Asal", type="primary"):
        doc_asal = db.collection(NAMA_DATABASE).document(f"Kolom_{r_asal_k}").get()
        data_asal = doc_asal.to_dict().get('data', []) if doc_asal.exists else []
        baris_asal = [d for d in data_asal if float(d.get('Baris', 0) or 0) == float(r_asal_b) and str(d.get('Part Number', '')) != ""]
        
        if baris_asal:
            df_asal = pd.DataFrame(baris_asal)
            df_asal['Pallet Ke'] = pd.to_numeric(df_asal['Pallet Ke'], errors='coerce')
            st.session_state['relok_data_asal'] = df_asal
            st.session_state['relok_doc_asal'] = data_asal
        else:
            st.warning("Rak Asal Kosong!")
            if 'relok_data_asal' in st.session_state: del st.session_state['relok_data_asal']
            
    if 'relok_data_asal' in st.session_state:
        df_a = st.session_state['relok_data_asal']
        
        tersedia_lot_relokasi = []
        mapping_lot_relokasi = {}
        for _, row in df_a.drop_duplicates(subset=['Lot Number', 'Nama Produk']).iterrows():
            lot_asli = str(row['Lot Number'])
            nama_produk = str(row.get('Nama Produk', 'Unknown Product'))
            label_dropdown = f"{nama_produk} (Lot: {lot_asli})"
            tersedia_lot_relokasi.append(label_dropdown)
            mapping_lot_relokasi[label_dropdown] = (nama_produk, lot_asli)
            
        st.divider()
        
        pilihan_label_relokasi = st.selectbox("Pilih Produk & Lot yang akan dipindah:", tersedia_lot_relokasi)
        nama_pilih, lot_pilih = mapping_lot_relokasi[pilihan_label_relokasi]
        
        df_lot_target_all = df_a[(df_a['Lot Number'].astype(str) == lot_pilih) & (df_a['Nama Produk'].astype(str) == nama_pilih)]
        qty_maks = len(df_lot_target_all)
        
        if qty_maks > 0:
            st.info(f"📦 Total Slot Fisik untuk item ini di rak {r_asal_k}-{r_asal_b}: **{qty_maks} Slot**")
            
            qty_pindah = st.number_input(f"Berapa Slot Fisik yang dipindah? (Maks: {qty_maks})", min_value=1, max_value=qty_maks, step=1)
            is_masuk_depan = st.checkbox("📥 Di Rak Tujuan, masukkan barang dari DEPAN (Push-back)")
            
            if st.button("🚚 Eksekusi Relokasi Pindah Rak", type="primary", use_container_width=True):
                if r_asal_k == r_tuj_k and r_asal_b == r_tuj_b:
                    st.error("Rak asal dan tujuan tidak boleh sama!")
                else:
                    with st.spinner("Memindahkan fisik dan re-indexing..."):
                        df_lot_target = df_lot_target_all.sort_values('Pallet Ke', ascending=False)
                        pallets_dipindah = df_lot_target['Pallet Ke'].head(qty_pindah).tolist()
                        
                        data_pindah = [d for d in st.session_state['relok_doc_asal'] if float(d.get('Baris',0) or 0)==float(r_asal_b) and pd.to_numeric(d.get('Pallet Ke',0)) in pallets_dipindah]
                        data_asal_sisa = [d for d in st.session_state['relok_doc_asal'] if not (float(d.get('Baris',0) or 0)==float(r_asal_b) and pd.to_numeric(d.get('Pallet Ke',0)) in pallets_dipindah)]
                        
                        baris_asal_sisa = sorted([d for d in data_asal_sisa if float(d.get('Baris',0) or 0)==float(r_asal_b)], key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                        lain_asal = [d for d in data_asal_sisa if float(d.get('Baris',0) or 0)!=float(r_asal_b)]
                        for i, d in enumerate(baris_asal_sisa, 1): d['Pallet Ke'] = float(i)
                        final_data_asal = lain_asal + baris_asal_sisa
                        
                        doc_tuj = db.collection(NAMA_DATABASE).document(f"Kolom_{r_tuj_k}").get()
                        data_tuj = doc_tuj.to_dict().get('data', []) if doc_tuj.exists else []
                        for d in data_pindah: d['Baris'] = float(r_tuj_b)
                        
                        if is_masuk_depan:
                            for d in data_tuj:
                                if float(d.get('Baris',0) or 0) == float(r_tuj_b):
                                    d['Pallet Ke'] = float(d.get('Pallet Ke',0)) + len(data_pindah)
                            for i, d in enumerate(data_pindah, 1): d['Pallet Ke'] = float(i)
                        else:
                            b_tuj = [pd.to_numeric(d.get('Pallet Ke', 0)) for d in data_tuj if float(d.get('Baris',0) or 0)==float(r_tuj_b) and pd.notna(d.get('Pallet Ke'))]
                            nx = max(b_tuj) + 1 if b_tuj else 1
                            for d in data_pindah:
                                d['Pallet Ke'] = float(nx)
                                nx += 1
                                
                        final_data_tuj = data_tuj + data_pindah
                        
                        batch = db.batch()
                        batch.set(db.collection(NAMA_DATABASE).document(f"Kolom_{r_asal_k}"), {'data': final_data_asal})
                        if r_asal_k != r_tuj_k: batch.set(db.collection(NAMA_DATABASE).document(f"Kolom_{r_tuj_k}"), {'data': final_data_tuj})
                        batch.commit()
                        
                        catat_log_mutasi("RELOKASI", f"{r_asal_k} ke {r_tuj_k}", f"{r_asal_b} ke {r_tuj_b}", str(data_pindah[0].get('Nama Produk', 'Pindah Rak')), str(data_pindah[0].get('Part Number', '-')), lot_pilih, qty_pindah, 0, 0)
                        del st.session_state['relok_data_asal']
                        
                        # TRIGGER TOAST BUBBLE
                        st.session_state['toast_msg'] = f"Berhasil memindahkan {qty_pindah} slot ke {r_tuj_k}-{r_tuj_b}"
                        st.session_state['toast_icon'] = "🚚"
                        st.rerun()
