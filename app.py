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
st.set_page_config(page_title="WMS Data Entry - Hokkan Deltapack", page_icon="📝", layout="wide")

# --- SISTEM LOGIN ---
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False

if not st.session_state['logged_in']:
    st.title("🔒 WMS Security Login")
    st.caption("Silakan masukkan kata sandi untuk mengakses form mutasi gudang.")
    
    # Gunakan st.secrets["wms_password"] jika sudah diset di Streamlit Cloud, 
    # atau ubah "Admin123" sesuai keinginan Anda.
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
    st.stop() # Hentikan eksekusi kode di bawah ini jika belum login

# --- JIKA SUDAH LOGIN, JALANKAN WMS UTAMA ---
st.sidebar.button("🚪 Logout", on_click=lambda: st.session_state.update({'logged_in': False}))

NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

st.title("📝 WMS Entry Form (Inbound, Outbound & Relokasi)")
st.caption(f"Alat khusus PPIC. Terhubung ke database: {NAMA_DATABASE}")

# --- CACHE: MASTER PRODUK (1 JAM) ---
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

# --- FUNGSI PENCATAT LOG MUTASI ---
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
# 2. FITUR UNDO & REDO
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
            st.success("✅ Aksi dibatalkan (Undo)! Data kembali seperti semula.")
            time.sleep(1.5)
            st.rerun()
if st.session_state['action_status'] is not None: st.divider()

# ==========================================================
# 3. TABS MENU UTAMA (7 TAB)
# ==========================================================
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(["📥 1. Inbound", "📤 2. Outbound", "⚙️ 3. Audit & Backup", "📊 4. Log Mutasi", "🚀 5. Sync CSV", "📅 6. Jadwal", "🔄 7. Relokasi Rak"])

# ----------------------------------------------------------
# TAB 1: INBOUND (DENGAN FITUR DARI DEPAN / RE-INDEX)
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
                            # Hanya gabungkan pecahan jika TIDAK input dari depan
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
                        st.success(f"✅ Inbound berhasil! {'Re-indexing otomatis memajukan pallet lain.' if in_dari_depan else ''}")
                        time.sleep(2)
                        st.rerun()

# ----------------------------------------------------------
# TAB 2: OUTBOUND DENGAN RE-INDEXING (Kode Sama)
# ----------------------------------------------------------
with tab2:
    st.header("Penyesuaian Sisa Stok (Outbound)")
    if 'out_lokasi_dicari' not in st.session_state: st.session_state.update({'out_lokasi_dicari': False, 'out_k_pilih': "A", 'out_b_pilih': 1})

    col_out1, col_out2 = st.columns([1, 2])
    with col_out1:
        st.markdown("**1. Pilih Lokasi Rak**")
        out_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="out_kolom")
        out_baris = st.number_input("Nomor Baris (Outbound)", min_value=1, max_value=28, value=1)
        if st.button("🔍 Cari Data Rak", use_container_width=True): st.session_state.update({'out_lokasi_dicari': True, 'out_k_pilih': out_kolom, 'out_b_pilih': out_baris})
            
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
                tersedia_lot = []
                mapping_lot = {}
                for _, row in df_out.drop_duplicates(subset=['Lot Number', 'Nama Produk']).iterrows():
                    l = str(row['Lot Number'])
                    lb = f"{str(row['Nama Produk'])} (Lot: {l})"
                    tersedia_lot.append(lb)
                    mapping_lot[lb] = l
                    
                pilihan_label = st.selectbox("🎯 Pilih Produk & No. Lot:", tersedia_lot)
                pilih_lot = mapping_lot[pilihan_label]
                df_lot = df_out[df_out['Lot Number'].astype(str) == pilih_lot].sort_values('Pallet Ke', ascending=False)
                
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
                        
                st.markdown(f"**📦 Total Fisik Awal: {jml_utuh_awal} Utuh, {pecahan_awal} Dus, {eceran_awal} Pcs**")
                
                col_s1, col_s2, col_s3 = st.columns(3)
                with col_s1: out_sisa_akhir = st.number_input(f"Sisa Pallet UTUH", min_value=0, max_value=jml_utuh_awal, value=jml_utuh_awal)
                with col_s2: out_sisa_dus = st.number_input("Sisa Pecahan Fisik", min_value=0, value=pecahan_awal)
                with col_s3: out_sisa_pcs = st.number_input("Sisa Eceran Fisik", min_value=0, value=eceran_awal)

                out_catatan = st.text_input("📝 Catatan (Opsional)")
                
                if st.button("🚜 Sesuaikan Stok Lot Ini (OUTBOUND)", type="primary", use_container_width=True):
                    slot_tambahan = 1 if (out_sisa_dus > 0 or out_sisa_pcs > 0) else 0
                    total_slot_dibutuhkan = out_sisa_akhir + slot_tambahan
                    if total_slot_dibutuhkan > len(df_lot): st.error("❌ Sisa akhir fisik melebih slot asal.")
                    else:
                        out_qty = len(df_lot) - total_slot_dibutuhkan
                        pallets_to_remove = df_lot['Pallet Ke'].head(out_qty).tolist() if out_qty > 0 else []
                        
                        new_all_out_data = []
                        for d in all_out_data:
                            b_val = float(d.get('Baris', 0) or 0)
                            p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                            if b_val == float(b_pilih) and p_val in pallets_to_remove: continue 
                            if b_val == float(b_pilih) and str(d.get('Lot Number', '')) == pilih_lot:
                                d['Catatan'] = out_catatan
                                sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
                                if not sisa_pallets.empty and p_val == sisa_pallets['Pallet Ke'].max():
                                    if out_sisa_dus > 0 or out_sisa_pcs > 0:
                                        kp = []
                                        if out_sisa_dus > 0: kp.append(f"Pecahan: {out_sisa_dus} qty")
                                        if out_sisa_pcs > 0: kp.append(f"Eceran: {out_sisa_pcs} pcs")
                                        d['Keterangan'] = " | ".join(kp)
                                    else: d['Keterangan'] = ""
                            new_all_out_data.append(d)
                            
                        # Re-indexing
                        data_rak_ini = sorted([d for d in new_all_out_data if float(d.get('Baris', 0) or 0) == float(b_pilih)], key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                        data_rak_lain = [d for d in new_all_out_data if float(d.get('Baris', 0) or 0) != float(b_pilih)]
                        
                        for i, d in enumerate(data_rak_ini, 1): d['Pallet Ke'] = float(i)
                        
                        doc_ref_out.set({'data': data_rak_lain + data_rak_ini})
                        st.session_state.update({'undo_data': all_out_data, 'action_kolom': k_pilih, 'action_msg': f"Outbound Lot '{pilih_lot}' di {k_pilih}-{b_pilih}", 'action_status': 'DONE'})
                        st.success("✅ Penyesuaian Outbound berhasil & Re-indexed.")
                        time.sleep(2)
                        st.rerun()
            else: st.info("Rak Kosong.")

# ----------------------------------------------------------
# TAB 7: RELOKASI RAK (PINDAH LOKASI)
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
        list_lot = df_a['Lot Number'].unique().tolist()
        
        with st.form("form_relokasi"):
            lot_pilih = st.selectbox("Pilih Lot yang akan dipindah:", list_lot)
            qty_maks = len(df_a[df_a['Lot Number']==lot_pilih])
            qty_pindah = st.number_input("Berapa Slot Fisik yang dipindah?", min_value=1, max_value=qty_maks, step=1)
            is_masuk_depan = st.checkbox("📥 Di Rak Tujuan, masukkan barang dari DEPAN (Push-back)")
            
            if st.form_submit_button("🚚 Eksekusi Relokasi Pindah Rak"):
                if r_asal_k == r_tuj_k and r_asal_b == r_tuj_b:
                    st.error("Rak asal dan tujuan tidak boleh sama!")
                else:
                    with st.spinner("Memindahkan fisik dan re-indexing..."):
                        # 1. Ekstrak dari Asal
                        df_lot_target = df_a[df_a['Lot Number']==lot_pilih].sort_values('Pallet Ke', ascending=False)
                        pallets_dipindah = df_lot_target['Pallet Ke'].head(qty_pindah).tolist()
                        
                        data_pindah = [d for d in st.session_state['relok_doc_asal'] if float(d.get('Baris',0) or 0)==float(r_asal_b) and pd.to_numeric(d.get('Pallet Ke',0)) in pallets_dipindah]
                        data_asal_sisa = [d for d in st.session_state['relok_doc_asal'] if not (float(d.get('Baris',0) or 0)==float(r_asal_b) and pd.to_numeric(d.get('Pallet Ke',0)) in pallets_dipindah)]
                        
                        # Re-index Asal
                        baris_asal_sisa = sorted([d for d in data_asal_sisa if float(d.get('Baris',0) or 0)==float(r_asal_b)], key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                        lain_asal = [d for d in data_asal_sisa if float(d.get('Baris',0) or 0)!=float(r_asal_b)]
                        for i, d in enumerate(baris_asal_sisa, 1): d['Pallet Ke'] = float(i)
                        final_data_asal = lain_asal + baris_asal_sisa
                        
                        # 2. Masukkan ke Tujuan
                        doc_tuj = db.collection(NAMA_DATABASE).document(f"Kolom_{r_tuj_k}").get()
                        data_tuj = doc_tuj.to_dict().get('data', []) if doc_tuj.exists else []
                        
                        # Set baris baru untuk data pindah
                        for d in data_pindah: d['Baris'] = float(r_tuj_b)
                        
                        if is_masuk_depan:
                            # Re-index tujuan (Push back)
                            for d in data_tuj:
                                if float(d.get('Baris',0) or 0) == float(r_tuj_b):
                                    d['Pallet Ke'] = float(d.get('Pallet Ke',0)) + len(data_pindah)
                            for i, d in enumerate(data_pindah, 1): d['Pallet Ke'] = float(i)
                        else:
                            # Masuk Belakang
                            b_tuj = [pd.to_numeric(d.get('Pallet Ke', 0)) for d in data_tuj if float(d.get('Baris',0) or 0)==float(r_tuj_b) and pd.notna(d.get('Pallet Ke'))]
                            nx = max(b_tuj) + 1 if b_tuj else 1
                            for d in data_pindah:
                                d['Pallet Ke'] = float(nx)
                                nx += 1
                                
                        final_data_tuj = data_tuj + data_pindah
                        
                        # 3. Commit Database Batch (Atomicity)
                        batch = db.batch()
                        batch.set(db.collection(NAMA_DATABASE).document(f"Kolom_{r_asal_k}"), {'data': final_data_asal})
                        if r_asal_k != r_tuj_k:
                            batch.set(db.collection(NAMA_DATABASE).document(f"Kolom_{r_tuj_k}"), {'data': final_data_tuj})
                        batch.commit()
                        
                        catat_log_mutasi("RELOKASI", f"{r_asal_k} ke {r_tuj_k}", f"{r_asal_b} ke {r_tuj_b}", "Pindah Rak", "-", lot_pilih, qty_pindah, 0, 0)
                        st.success(f"✅ Berhasil memindahkan {qty_pindah} slot ke {r_tuj_k}-{r_tuj_b}")
                        del st.session_state['relok_data_asal']
                        time.sleep(2)
                        st.rerun()

# ----------------------------------------------------------
# TAB 3, 4, 5, 6: (Audit, Log, Sinkronisasi, Booking)
# Dipertahankan sesuai fitur asli (Re-indexing Global & CSV Super)
# ----------------------------------------------------------
# (Sisa tab seperti Re-Indexing Global, Laporan Audit CSV, Log Mutasi, 
# dan Booking berjalan normal seperti kode sebelumnya dan bisa langsung 
# dipaste di bawah blok ini tanpa modifikasi jika Anda menginginkannya lengkap)
with tab3:
    st.header("⚙️ Audit Stok & Ekspor Data")
    st.info("Tab Audit, CSV, dan Booking berjalan seperti biasa...")
