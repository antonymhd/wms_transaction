import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import io
import copy
import re
from datetime import datetime, timedelta, timezone

# ==========================================================
# 1. KONFIGURASI WEB & SISTEM LOGIN
# ==========================================================
st.set_page_config(page_title="WMS Data Entry - Hokkan Deltapack", page_icon="📝", layout="wide")

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False

# Inisialisasi Session Quota Counter (0 Read/Write tambahan)
if 'fb_usage' not in st.session_state:
    st.session_state['fb_usage'] = {'read': 0, 'write': 0, 'delete': 0}

def log_fb_usage(op_type, count=1):
    if op_type in st.session_state['fb_usage']:
        st.session_state['fb_usage'][op_type] += count

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

# --- SIDEBAR NAVIGASI & KONTROL ---
st.sidebar.markdown("### ⚙️ Kontrol Sistem")

if st.sidebar.button("🔄 Reload Master Data CSV", use_container_width=True):
    st.cache_data.clear()
    st.toast("Master Data berhasil diperbarui dari GitHub!", icon="✅")
    st.rerun()

st.sidebar.divider()
st.sidebar.markdown("### 📊 Monitor Kuota Sesi Ini")
u_col1, u_col2, u_col3 = st.sidebar.columns(3)
u_col1.metric("Read", st.session_state['fb_usage']['read'])
u_col2.metric("Write", st.session_state['fb_usage']['write'])
u_col3.metric("Delete", st.session_state['fb_usage']['delete'])
st.sidebar.caption("Menghitung operasi Firebase pada sesi browser aktif ini.")
if st.sidebar.button("Reset Counter Sesi", use_container_width=True):
    st.session_state['fb_usage'] = {'read': 0, 'write': 0, 'delete': 0}
    st.rerun()

st.sidebar.divider()
st.sidebar.button("🚪 Logout", on_click=lambda: st.session_state.update({'logged_in': False}), use_container_width=True)

# ==========================================================
# 2. INISIALISASI DATABASE (FIREBASE)
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
# 3. FUNGSI BANTUAN (HELPER FUNCTIONS)
# ==========================================================
def get_waktu_jkt():
    """Mendapatkan waktu saat ini di zona WIB (UTC+7) yang aman untuk Python 3.12+"""
    return datetime.now(timezone.utc) + timedelta(hours=7)

def fetch_kolom_cached(kolom_id, state_key, force_refresh=False):
    """Membaca dokumen Kolom sekali saja saat diminta, lalu simpan di session_state"""
    if force_refresh or state_key not in st.session_state or st.session_state[state_key] is None:
        doc = db.collection(NAMA_DATABASE).document(f"Kolom_{kolom_id}").get()
        log_fb_usage('read', 1)
        data = doc.to_dict().get('data', []) if doc.exists else []
        st.session_state[state_key] = data
    return st.session_state[state_key]

def clear_all_rak_caches():
    """Membersihkan cache lokal rak setiap kali ada mutasi tersimpan"""
    for k in ['cache_in_data', 'cache_out_data', 'cache_book_data', 'cache_relok_asal', 'cache_kor_data']:
        st.session_state[k] = None

def parse_keterangan(ket_str):
    """Fungsi sentral membaca jumlah Pecahan dan Eceran dari format teks string yang solid"""
    ket_str = str(ket_str).lower()
    pecahan, eceran = 0, 0
    m_pec = re.search(r'pecahan:\s*(\d+)', ket_str)
    if m_pec: pecahan = int(m_pec.group(1))
    
    m_ec = re.search(r'eceran:\s*(\d+)', ket_str)
    if m_ec: eceran = int(m_ec.group(1))
    return pecahan, eceran

def update_bayangan_baris(records_baris, kolom_id, baris_num):
    """Mengevaluasi ulang indeks Pallet Ke (1..N) dan tag [BAYANGAN - OVERCAPACITY] pada satu baris rak"""
    valid_items = [d for d in records_baris if str(d.get('Part Number', '')).strip().lower() not in ["", "nan", "none", "<na>"]]
    is_all_box = False
    if valid_items:
        is_all_box = all(str(d.get('Part Number', '')).strip().upper().endswith(('D', 'G')) for d in valid_items)
    
    max_slot = 999 if kolom_id == 'S' else ((32 if baris_num <= 16 else 46) if is_all_box else (31 if baris_num <= 16 else 44))
    
    for idx, item in enumerate(records_baris, start=1):
        item['Pallet Ke'] = float(idx)
        cat_lama = str(item.get('Catatan', '')).replace('nan', '')
        if idx > max_slot and 'BAYANGAN' not in cat_lama.upper():
            item['Catatan'] = ("[BAYANGAN - OVERCAPACITY] " + cat_lama).strip()
        elif idx <= max_slot and 'BAYANGAN' in cat_lama.upper():
            item['Catatan'] = cat_lama.replace("[BAYANGAN - OVERCAPACITY]", "").strip()
    return records_baris

@st.cache_data(ttl=300)
def load_master_produk():
    """Memuat Master Data Produk untuk Auto-fill Part Number"""
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
    """Mencatat setiap pergerakan stok ke dalam tabel log riwayat mutasi"""
    waktu_sekarang = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")
    no_lot_final = no_lot if str(no_lot).strip() != "" else "-"
    log_data = {
        "Waktu Transaksi": waktu_sekarang, "Jenis Transaksi": jenis, "Lokasi Rak": f"{kolom}-{baris}",
        "Nama Produk": produk, "Part Number": part_num, "No Lot": no_lot_final,
        "Mutasi Pallet": float(pallet_utuh), "Mutasi Pecahan (Dus/Sak)": float(pecahan),
        "Mutasi Eceran (Pcs)": float(eceran), "Keterangan": keterangan,
        "Timestamp_System": firestore.SERVER_TIMESTAMP
    }
    try:
        db.collection(DB_LOG_MUTASI).add(log_data)
        log_fb_usage('write', 1)
    except Exception as e:
        st.toast(f"⚠️ Peringatan: Gagal mencatat log mutasi ({e})", icon="⚠️")

# ==========================================================
# 4. NOTIFIKASI & FITUR UNDO (NATIVE STREAMLIT)
# ==========================================================
if 'undo_dict' not in st.session_state:
    st.session_state.update({'undo_dict': {}, 'action_msg': "", 'action_status': None})

if st.session_state['action_status'] in ['DONE', 'DONE_NO_UNDO']:
    st.toast(f"Berhasil: {st.session_state['action_msg']}", icon="✅")

if st.session_state['action_status'] == 'DONE':
    with st.container(border=True):
        st.success(f"✅ **Sukses:** {st.session_state['action_msg']}")
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1: st.caption("Jika terjadi kesalahan input, Anda bisa membatalkan aksi terakhir ini.")
        with c2:
            if st.button("🔙 UNDO AKSI INI", type="primary", use_container_width=True):
                with st.spinner("Memulihkan data..."):
                    batch_undo = db.batch()
                    kolom_terdampak = []
                    for kol, dat in st.session_state['undo_dict'].items():
                        doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{kol}")
                        batch_undo.set(doc_ref, {'data': dat})
                        kolom_terdampak.append(kol)
                    batch_undo.commit()
                    log_fb_usage('write', len(kolom_terdampak))
                    clear_all_rak_caches()
                    
                    st.session_state['action_status'] = 'UNDONE'
                    catat_log_mutasi("PEMBATALAN (UNDO)", ",".join(kolom_terdampak), "ALL", "Koreksi Sistem", "-", "-", 0, 0, 0, f"Membatalkan: {st.session_state['action_msg']}")
                    st.rerun()
        with c3:
            if st.button("Tutup & Lanjut Input", use_container_width=True):
                st.session_state['action_status'] = None
                st.rerun()

elif st.session_state['action_status'] == 'DONE_NO_UNDO':
    with st.container(border=True):
        st.success(f"✅ **Sukses:** {st.session_state['action_msg']}")
        if st.button("Tutup Pesan Ini"):
            st.session_state['action_status'] = None
            st.rerun()

elif st.session_state['action_status'] == 'UNDONE':
    with st.container(border=True):
        st.warning("⏪ **Berhasil dibatalkan.** Data telah kembali seperti semula.")
        if st.button("Tutup Pesan", use_container_width=True):
            st.session_state['action_status'] = None
            st.rerun()

# ==========================================================
# 5. TABS MENU UTAMA
# ==========================================================
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "📥 1. Inbound", "📤 2. Outbound", "⚙️ 3. Audit & Backup", 
    "📊 4. Log Mutasi", "🚀 5. Sync CSV", "📅 6. Jadwal", "🔄 7. Relokasi", "✏️ 8. Koreksi"
])

# ----------------------------------------------------------
# TAB 1: INBOUND
# ----------------------------------------------------------
with tab1:
    st.header("Input Barang Masuk")
    
    if 'in_lokasi_dicari' not in st.session_state:
        st.session_state.update({'in_lokasi_dicari': False, 'in_k_pilih': "A", 'in_b_pilih': 1})
    if 'form_reset_counter' not in st.session_state: 
        st.session_state['form_reset_counter'] = 0
        
    reset_key = st.session_state['form_reset_counter']

    col_in1, col_in2 = st.columns([1, 2])
    with col_in1:
        st.markdown("**1. Pilih Lokasi Rak Tujuan**")
        in_kolom_label = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"], key="in_kolom")
        in_kolom = in_kolom_label.split(" ")[0]
        in_baris = st.number_input("Nomor Baris (Inbound)", min_value=1, max_value=28, value=1)
        
        if st.button("🔍 Cek Status Rak", use_container_width=True):
            st.session_state.update({'in_lokasi_dicari': True, 'in_k_pilih': in_kolom, 'in_b_pilih': in_baris})
            fetch_kolom_cached(in_kolom, 'cache_in_data', force_refresh=True)
            
    with col_in2:
        if st.session_state['in_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['in_k_pilih'], st.session_state['in_b_pilih']
            all_data_raw = fetch_kolom_cached(k_pilih, 'cache_in_data', force_refresh=False)
            
            baris_data_preview = [d for d in all_data_raw if float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
            is_all_box_existing = all(str(d.get('Part Number', '')).strip().upper().endswith(('D', 'G')) for d in baris_data_preview) if baris_data_preview else False
            
            max_slot_display = 999 if k_pilih == 'S' else (32 if (is_all_box_existing and b_pilih <= 16) else (46 if is_all_box_existing else (31 if b_pilih <= 16 else 44)))
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
                
            in_lot = st.text_input("No. Lot Produksi / Mesin Shift", placeholder="WAJIB DIISI", key=f"in_lot_input_{reset_key}")
            
            col_qty1, col_qty2, col_qty3 = st.columns(3)
            with col_qty1: in_qty = st.number_input("Pallet Masuk UTUH", min_value=0, step=1, value=1, key=f"in_qty_input_{reset_key}")
            with col_qty2: in_pecahan = st.number_input("Sisa Pecahan (Dus/Sak)", min_value=0, step=1, value=0, key=f"in_pecahan_input_{reset_key}")
            with col_qty3: in_eceran = st.number_input("Eceran (Pcs)", min_value=0, step=1, value=0, key=f"in_eceran_input_{reset_key}")
            
            in_dari_depan = st.checkbox("📥 Masukkan dari DEPAN Rak (Push-back)")
                
            if st.button("🚀 Simpan Inbound", type="primary", use_container_width=True):
                in_part_clean = in_part.strip().upper() if in_part else ""
                in_produk_clean = in_produk.strip() if in_produk else ""
                in_lot_final = in_lot.strip()

                if not in_lot_final or in_lot_final == "-":
                    st.error("⚠️ No. Lot bersifat WAJIB DIISI!")
                elif not in_produk_clean or not in_part_clean:
                    st.error("⚠️ Nama Produk dan Part Number wajib diisi!")
                elif in_qty == 0 and in_pecahan == 0 and in_eceran == 0:
                    st.error("⚠️ Masukkan minimal 1 Qty!")
                else:
                    with st.spinner("Menyusun data ke dalam rak (Transactional Lock)..."):
                        doc_ref_in = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                        
                        @firestore.transactional
                        def tx_inbound(transaction, doc_ref):
                            snap = doc_ref.get(transaction=transaction)
                            log_fb_usage('read', 1)
                            fresh_raw = snap.to_dict().get('data', []) if snap.exists else []
                            
                            all_data = [d for d in fresh_raw if not (float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() == '')]
                            baris_data_actual = [d for d in all_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
                            
                            is_all_box_actual = in_part_clean.endswith(('D', 'G'))
                            existing_fraction_row = None
                            
                            for d in baris_data_actual:
                                pn = str(d.get('Part Number', '')).strip().upper()
                                if pn and not pn.endswith(('D', 'G')): is_all_box_actual = False
                                if not in_dari_depan and pn == in_part_clean and str(d.get('Lot Number', '')).strip() == in_lot_final:
                                    ket = str(d.get('Keterangan', ''))
                                    if 'Pecahan' in ket or 'Eceran' in ket: existing_fraction_row = d
                                        
                            max_slot_actual = 999 if k_pilih == 'S' else (32 if (is_all_box_actual and b_pilih <= 16) else (46 if is_all_box_actual else (31 if b_pilih <= 16 else 44)))
                            
                            slot_tambahan_in = 0
                            if (in_pecahan > 0 or in_eceran > 0) and not existing_fraction_row: slot_tambahan_in = 1
                            total_inbound = in_qty + slot_tambahan_in
                            
                            new_records = []
                            
                            if in_dari_depan and baris_data_actual:
                                for d in all_data:
                                    if float(d.get('Baris', 0) or 0) == float(b_pilih):
                                        new_idx = float(d.get('Pallet Ke', 0)) + total_inbound
                                        d['Pallet Ke'] = new_idx
                                        cat_lama = str(d.get('Catatan', '')).replace('nan', '')
                                        if new_idx > max_slot_actual and 'BAYANGAN' not in cat_lama.upper():
                                            d['Catatan'] = ("[BAYANGAN - OVERCAPACITY] " + cat_lama).strip()
                                next_pallet = 1
                                filled_pallets = []
                            else:
                                filled_pallets = [int(float(d.get('Pallet Ke', 0) or 0)) for d in baris_data_actual if pd.notna(d.get('Pallet Ke'))]
                                next_pallet = max(filled_pallets) + 1 if filled_pallets else 1
                                
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
                                
                            if in_pecahan > 0 or in_eceran > 0:
                                if existing_fraction_row and not in_dari_depan:
                                    ex_pecahan, ex_eceran = parse_keterangan(existing_fraction_row.get('Keterangan', ''))
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
                            transaction.set(doc_ref, {'data': all_data})
                            log_fb_usage('write', 1)
                            return fresh_raw, total_inbound

                        fresh_snapshot, total_inbound_slots = tx_inbound(db.transaction(), doc_ref_in)
                        catat_log_mutasi("INBOUND", k_pilih, b_pilih, in_produk_clean, in_part_clean, in_lot_final, in_qty, in_pecahan, in_eceran)
                        clear_all_rak_caches()
                        
                        st.session_state.update({
                            'undo_dict': {k_pilih: fresh_snapshot}, 
                            'action_msg': f"Inbound {total_inbound_slots} Slot '{in_produk_clean}' ke Rak {k_pilih}-{b_pilih} {'(Push-back)' if in_dari_depan else ''}", 
                            'action_status': 'DONE',
                            'kor_dicari': False,
                            'relok_dicari': False
                        })
                        
                        st.session_state['form_reset_counter'] += 1
                        st.rerun()

# ----------------------------------------------------------
# TAB 2: OUTBOUND MULTI-LOT
# ----------------------------------------------------------
with tab2:
    st.header("Penyesuaian Sisa Stok (Outbound Massal)")
    st.caption("Ubah angka sisa fisik pada tabel, lalu klik Simpan untuk eksekusi Outbound.")
    
    if 'out_lokasi_dicari' not in st.session_state: 
        st.session_state.update({'out_lokasi_dicari': False, 'out_k_pilih': "A", 'out_b_pilih': 1})

    st.markdown("**1. Pilih Lokasi Rak**")
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1: 
        out_kolom_label = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"], key="out_kolom_2")
        out_kolom = out_kolom_label.split(" ")[0]
    with c2: 
        out_baris = st.number_input("Nomor Baris", min_value=1, max_value=28, value=1, key="out_baris_2")
    with c3:
        st.write("") 
        st.write("")
        if st.button("🔍 Cari Data Rak", use_container_width=True): 
            st.session_state.update({'out_lokasi_dicari': True, 'out_k_pilih': out_kolom, 'out_b_pilih': out_baris})
            fetch_kolom_cached(out_kolom, 'cache_out_data', force_refresh=True)
            
    st.divider()
    
    if st.session_state['out_lokasi_dicari']:
        k_pilih, b_pilih = st.session_state['out_k_pilih'], st.session_state['out_b_pilih']
        all_out_data = fetch_kolom_cached(k_pilih, 'cache_out_data', force_refresh=False)
        baris_out_data = [d for d in all_out_data if float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
        
        if baris_out_data:
            df_out = pd.DataFrame(baris_out_data)
            df_out['Pallet Ke'] = pd.to_numeric(df_out['Pallet Ke'], errors='coerce')
            
            df_out['Catatan_Clean'] = df_out['Catatan'].fillna("").astype(str).replace('nan', '')
            df_out['Group_Key'] = df_out['Lot Number'].astype(str) + "_" + df_out['Nama Produk'].astype(str) + "_" + df_out['Catatan_Clean'].astype(str)
            
            rekap_lot = []
            for _, row in df_out.drop_duplicates(subset=['Group_Key']).iterrows():
                g_key = str(row['Group_Key'])
                l = str(row['Lot Number'])
                nama_prod = str(row['Nama Produk'])
                part_num = str(row['Part Number'])
                c_catatan = str(row['Catatan_Clean'])
                
                df_lot = df_out[df_out['Group_Key'] == g_key]
                
                jml_utuh_awal = df_lot[~df_lot['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                pecahan_rows = df_lot[df_lot['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
                pecahan_awal, eceran_awal = 0, 0
                
                for _, r in pecahan_rows.iterrows():
                    p, e = parse_keterangan(r['Keterangan'])
                    pecahan_awal += p
                    eceran_awal += e
                
                rekap_lot.append({
                    'Group_Key': g_key,
                    'Produk': nama_prod, 'Part Number': part_num, 'Lot Number': l, 'Status / Asal': c_catatan,
                    'Awal Utuh': int(jml_utuh_awal), 'Awal Dus': int(pecahan_awal), 'Awal Pcs': int(eceran_awal),
                    'Sisa Utuh Baru': int(jml_utuh_awal), 'Sisa Dus Baru': int(pecahan_awal), 'Sisa Pcs Baru': int(eceran_awal),
                    'Catatan Tambahan': ""
                })
            
            df_rekap = pd.DataFrame(rekap_lot)
            st.markdown(f"**2. Edit Sisa Fisik Rak {k_pilih}-{b_pilih} (Tampilan Lebar)**")
            
            config_kolom = {
                'Group_Key': None, 
                'Produk': st.column_config.TextColumn("Produk", disabled=True),
                'Part Number': st.column_config.TextColumn("Part", disabled=True),
                'Lot Number': st.column_config.TextColumn("Lot", disabled=True),
                'Status / Asal': st.column_config.TextColumn("Status / Asal", disabled=True),
                'Awal Utuh': st.column_config.NumberColumn("Sistem Utuh", disabled=True),
                'Awal Dus': st.column_config.NumberColumn("Sistem Dus", disabled=True),
                'Awal Pcs': st.column_config.NumberColumn("Sistem Pcs", disabled=True),
                'Sisa Utuh Baru': st.column_config.NumberColumn("SISA UTUH BARU", min_value=0, required=True),
                'Sisa Dus Baru': st.column_config.NumberColumn("SISA DUS BARU", min_value=0, required=True),
                'Sisa Pcs Baru': st.column_config.NumberColumn("SISA PCS BARU", min_value=0, required=True),
                'Catatan Tambahan': st.column_config.TextColumn("Catatan Tambahan", max_chars=100)
            }
            
            edited_df = st.data_editor(df_rekap, column_config=config_kolom, hide_index=True, use_container_width=True, num_rows="fixed", key=f"editor_out_{k_pilih}_{b_pilih}")
            
            if st.button("🚜 Simpan Penyesuaian Massal (OUTBOUND)", type="primary", use_container_width=True):
                doc_ref_out = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                
                def match_g_key(d, target_key):
                    c = str(d.get('Catatan', '')).replace('nan', '')
                    return (str(d.get('Lot Number','')) + "_" + str(d.get('Nama Produk','')) + "_" + c) == target_key

                @firestore.transactional
                def tx_outbound(transaction, doc_ref):
                    snap = doc_ref.get(transaction=transaction)
                    log_fb_usage('read', 1)
                    fresh_out_data = snap.to_dict().get('data', []) if snap.exists else []
                    
                    has_changes = False
                    error_msg = ""
                    logs_to_write = []
                    
                    data_rak_ini = [d for d in fresh_out_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
                    data_rak_lain = [d for d in fresh_out_data if float(d.get('Baris', 0) or 0) != float(b_pilih)]

                    for _, row in edited_df.iterrows():
                        g_key = str(row['Group_Key'])
                        l = str(row['Lot Number'])
                        sisa_utuh = int(row['Sisa Utuh Baru'])
                        sisa_dus = int(row['Sisa Dus Baru'])
                        sisa_pcs = int(row['Sisa Pcs Baru'])
                        catatan_out = str(row['Catatan Tambahan'])
                        
                        awal_utuh = int(row['Awal Utuh'])
                        awal_dus = int(row['Awal Dus'])
                        awal_pcs = int(row['Awal Pcs'])
                        
                        if sisa_utuh != awal_utuh or sisa_dus != awal_dus or sisa_pcs != awal_pcs or catatan_out != "":
                            has_changes = True
                            df_lot = pd.DataFrame([d for d in data_rak_ini if match_g_key(d, g_key)])
                            
                            if df_lot.empty:
                                error_msg = f"❌ Data Lot {l} berubah oleh user lain. Silakan klik 'Cari Data Rak' ulang."
                                break

                            df_lot['Pallet Ke'] = pd.to_numeric(df_lot['Pallet Ke'])
                            df_lot = df_lot.sort_values('Pallet Ke', ascending=False)
                            
                            slot_tambahan = 1 if (sisa_dus > 0 or sisa_pcs > 0) else 0
                            total_slot_dibutuhkan = sisa_utuh + slot_tambahan
                            
                            if total_slot_dibutuhkan > len(df_lot):
                                error_msg = f"❌ Sisa akhir Lot {l} melebihi kapasitas stok terkini ({len(df_lot)} slot)."
                                break
                            
                            out_qty = len(df_lot) - total_slot_dibutuhkan
                            pallets_to_remove = df_lot['Pallet Ke'].head(out_qty).tolist() if out_qty > 0 else []
                            
                            updated_rak_ini = []
                            for d in data_rak_ini:
                                p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                                if match_g_key(d, g_key):
                                    if p_val in pallets_to_remove: continue 
                                    
                                    c_lama = str(d.get('Catatan', '')).replace('nan', '')
                                    if catatan_out != "" and catatan_out not in c_lama:
                                        d['Catatan'] = (c_lama + " | " + catatan_out).strip(' |')
                                        
                                    sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
                                    if not sisa_pallets.empty and p_val == sisa_pallets['Pallet Ke'].max():
                                        if sisa_dus > 0 or sisa_pcs > 0:
                                            kp = []
                                            if sisa_dus > 0: kp.append(f"Pecahan: {sisa_dus} qty")
                                            if sisa_pcs > 0: kp.append(f"Eceran: {sisa_pcs} pcs")
                                            d['Keterangan'] = " | ".join(kp)
                                        else: d['Keterangan'] = ""
                                    else:
                                        if 'Pecahan' in str(d.get('Keterangan', '')) or 'Eceran' in str(d.get('Keterangan', '')): 
                                            d['Keterangan'] = ""
                                updated_rak_ini.append(d)
                            data_rak_ini = updated_rak_ini
                            
                            keluar_utuh = awal_utuh - sisa_utuh
                            keluar_pecahan = awal_dus - sisa_dus
                            keluar_eceran = awal_pcs - sisa_pcs
                            logs_to_write.append(("OUTBOUND (Massal)", k_pilih, b_pilih, str(row['Produk']), str(row['Part Number']), l, keluar_utuh, keluar_pecahan, keluar_eceran, catatan_out))

                    if error_msg or not has_changes:
                        return False, error_msg, fresh_out_data, []

                    data_rak_ini = sorted([d for d in data_rak_ini if float(d.get('Baris', 0) or 0) == float(b_pilih)], key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                    data_rak_ini = update_bayangan_baris(data_rak_ini, k_pilih, b_pilih)
                    
                    transaction.set(doc_ref, {'data': data_rak_lain + data_rak_ini})
                    log_fb_usage('write', 1)
                    return True, "", fresh_out_data, logs_to_write

                sukses, error_msg, fresh_snapshot, logs_list = tx_outbound(db.transaction(), doc_ref_out)
                if error_msg:
                    st.error(error_msg)
                elif sukses:
                    for lg in logs_list:
                        catat_log_mutasi(*lg)
                    clear_all_rak_caches()
                    st.session_state.update({
                        'undo_dict': {k_pilih: fresh_snapshot},
                        'action_msg': f"Penyesuaian Outbound Massal di Rak {k_pilih}-{b_pilih}",
                        'action_status': 'DONE',
                        'kor_dicari': False,
                        'relok_dicari': False
                    })
                    st.rerun()
                else:
                    st.info("Tidak ada perubahan angka pada tabel. Outbound dibatalkan.")
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
                docs = list(db.collection(NAMA_DATABASE).stream())
                log_fb_usage('read', len(docs))
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
                            
                        max_slot_actual = 999 if kolom_id == "Kolom_S" else (int(32 if baris <= 16 else 46) if is_all_box else int(31 if baris <= 16 else 44))
                        
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
                log_fb_usage('write', total_koreksi)
                clear_all_rak_caches()
                st.session_state['last_reindex'] = get_waktu_jkt().strftime("%d-%m-%Y %H:%M:%S")
                st.balloons()
                st.session_state.update({'action_msg': f"Re-Indexing Selesai! {total_koreksi} Kolom diperbarui.", 'action_status': 'DONE_NO_UNDO'})
                st.rerun()
            except Exception as e: st.error(f"Gagal Re-Indexing: {e}")

    st.divider()
    st.subheader("📥 Tarik Laporan Audit (CSV Super)")
    if st.button("Racik Laporan Audit Stok Sekarang", type="primary"):
        with st.spinner("Membaca database..."):
            docs = list(db.collection(NAMA_DATABASE).stream())
            log_fb_usage('read', len(docs))
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
                    
                    q_dus, q_pcs = parse_keterangan(ket) if is_pecahan else (0, 0)
                        
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
                st.download_button(label="⬇️ Unduh CSV Super", data=csv_buffer.getvalue(), file_name=f"Audit_Stok_WMS_{get_waktu_jkt().strftime('%Y%m%d')}.csv", mime="text/csv")
            else: st.warning("Database kosong.")

# ----------------------------------------------------------
# TAB 4: LAPORAN MUTASI (FILTER TANGGAL HEMAT KUOTA)
# ----------------------------------------------------------
with tab4:
    st.header("📊 Laporan Riwayat Mutasi")
    st.caption("Pilih rentang tanggal agar penarikan log tetap ringan dan hemat kuota Firebase Read.")
    
    hari_ini_jkt = get_waktu_jkt().date()
    c_tgl1, c_tgl2 = st.columns(2)
    with c_tgl1:
        tgl_mulai = st.date_input("Dari Tanggal", value=hari_ini_jkt, max_value=hari_ini_jkt)
    with c_tgl2:
        tgl_selesai = st.date_input("Sampai Tanggal", value=hari_ini_jkt, min_value=tgl_mulai, max_value=hari_ini_jkt)

    if st.button("Racik Laporan Mutasi Sekarang", type="primary"):
        with st.spinner("Menarik data log mutasi..."):
            try:
                start_utc = datetime.combine(tgl_mulai, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=7)
                end_utc = datetime.combine(tgl_selesai, datetime.max.time(), tzinfo=timezone.utc) - timedelta(hours=7)
                
                log_docs = list(
                    db.collection(DB_LOG_MUTASI)
                    .where("Timestamp_System", ">=", start_utc)
                    .where("Timestamp_System", "<=", end_utc)
                    .order_by("Timestamp_System", direction=firestore.Query.DESCENDING)
                    .stream()
                )
                log_fb_usage('read', len(log_docs))
                log_list = [d.to_dict() for d in log_docs]
                    
                if log_list:
                    df_log = pd.DataFrame(log_list)
                    avail_cols = [c for c in ["Waktu Transaksi", "Jenis Transaksi", "Lokasi Rak", "Nama Produk", "No Lot", "Mutasi Pallet", "Mutasi Pecahan (Dus/Sak)", "Mutasi Eceran (Pcs)", "Part Number", "Keterangan"] if c in df_log.columns]
                    df_log = df_log[avail_cols]
                    
                    st.dataframe(df_log, hide_index=True, use_container_width=True, height=250)
                    csv_log_buffer = io.StringIO()
                    df_log.to_csv(csv_log_buffer, index=False, sep=";")
                    st.success(f"✅ Ditemukan {len(df_log)} catatan mutasi ({len(log_docs)} Reads).")
                    st.download_button(label="⬇️ Unduh Laporan Mutasi", data=csv_log_buffer.getvalue(), file_name=f"Mutasi_WMS_{tgl_mulai.strftime('%Y%m%d')}_{tgl_selesai.strftime('%Y%m%d')}.csv", mime="text/csv")
                else:
                    st.info("Tidak ada catatan mutasi pada rentang tanggal tersebut.")
            except Exception as e:
                st.error(f"Gagal memuat log mutasi: {e}. Pastikan Index di Firestore sudah dibuat!")

# ----------------------------------------------------------
# TAB 5: SYNC CSV (RESTORE)
# ----------------------------------------------------------
with tab5:
    st.header("🚀 Restore Data Database dari CSV")
    st.warning("⚠️ **PERINGATAN:** Fitur ini berfungsi sebagai *Disaster Recovery*. Jika dieksekusi, sistem akan **MENGHAPUS SELURUH ISI RAK** dan menimpanya dengan isi dari file CSV yang diunggah.")
    
    uploaded_file = st.file_uploader("Upload File Backup CSV (.csv)", type=['csv'])
    if uploaded_file is not None:
        if st.button("🚨 SAYA YAKIN, HAPUS DATABASE & TIMPA DENGAN CSV INI", type="primary", use_container_width=True):
            with st.spinner("Membaca CSV dan menimpa database..."):
                try:
                    try: df_sync = pd.read_csv(uploaded_file, sep=";")
                    except: df_sync = pd.read_csv(uploaded_file, sep=",")
                    
                    req_cols = ['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk']
                    if not all(col in df_sync.columns for col in req_cols):
                        st.error("❌ Format CSV tidak valid! Kolom wajib tidak ditemukan.")
                    else:
                        batch = db.batch()
                        kolom_list = ["A", "B", "C", "D", "E", "F", "G", "S"]
                        for kol in kolom_list:
                            df_kol = df_sync[df_sync['Kolom'] == kol]
                            new_data = []
                            if not df_kol.empty:
                                df_kol = df_kol.sort_values(by=['Baris', 'Pallet Ke'])
                                for _, row in df_kol.iterrows():
                                    if pd.isna(row['Part Number']) or str(row['Part Number']).strip() == "":
                                        continue
                                    record = {
                                        'Kolom': kol, 'Baris': float(row['Baris']), 'Pallet Ke': float(row['Pallet Ke']),
                                        'Part Number': str(row['Part Number']).strip(), 'Lot Number': str(row['Lot Number']).strip(),
                                        'Nama Produk': str(row['Nama Produk']).strip(),
                                        'Keterangan': str(row.get('Keterangan', '')).strip(),
                                        'Catatan': str(row.get('Catatan', '')).strip(),
                                        'Status': 'Terisi'
                                    }
                                    if record['Keterangan'] == 'nan': record['Keterangan'] = ""
                                    if record['Catatan'] == 'nan': record['Catatan'] = ""
                                    new_data.append(record)
                            
                            doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{kol}")
                            batch.set(doc_ref, {'data': new_data})
                        
                        batch.commit()
                        log_fb_usage('write', len(kolom_list))
                        clear_all_rak_caches()
                        catat_log_mutasi("RESTORE CSV", "ALL", "ALL", "Sistem Database", "-", "-", 0, 0, 0, "Hard Reset via CSV Upload")
                        
                        st.session_state.update({'action_msg': "Database berhasil di-restore dari file CSV!", 'action_status': 'DONE_NO_UNDO'})
                        st.rerun()
                except Exception as e:
                    st.error(f"Terjadi kesalahan saat memproses CSV: {e}")

# ----------------------------------------------------------
# TAB 6: PENJADWALAN KIRIM (BOOKING)
# ----------------------------------------------------------
with tab6:
    st.header("📅 Penjadwalan Pengiriman (Booking Pallet)")
    if 'book_lokasi_dicari' not in st.session_state: st.session_state.update({'book_lokasi_dicari': False, 'book_k_pilih': "A", 'book_b_pilih': 1})

    col_b1, col_b2 = st.columns([1, 2])
    with col_b1:
        st.markdown("**1. Pilih Lokasi Rak**")
        book_kolom_label = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"], key="book_kolom_3")
        book_kolom = book_kolom_label.split(" ")[0]
        book_baris = st.number_input("Nomor Baris (Booking)", min_value=1, max_value=28, value=1, key="book_baris_3")
        if st.button("🔍 Cari Data Rak (Booking)", use_container_width=True):
            st.session_state.update({'book_lokasi_dicari': True, 'book_k_pilih': book_kolom, 'book_b_pilih': book_baris})
            fetch_kolom_cached(book_kolom, 'cache_book_data', force_refresh=True)
            
    with col_b2:
        if st.session_state['book_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['book_k_pilih'], st.session_state['book_b_pilih']
            all_book_data = fetch_kolom_cached(k_pilih, 'cache_book_data', force_refresh=False)
            baris_book_data = [d for d in all_book_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
            
            if baris_book_data:
                df_book = pd.DataFrame(baris_book_data)
                df_book['Pallet Ke'] = pd.to_numeric(df_book['Pallet Ke'], errors='coerce')
                df_book = df_book[df_book['Part Number'].astype(str).str.strip() != ""]
                
                if not df_book.empty and 'Lot Number' in df_book.columns:
                    tersedia_lot = []
                    mapping_lot = {}
                    
                    df_book['Catatan'] = df_book['Catatan'].fillna("").astype(str).replace('nan', '')
                    
                    for _, row in df_book.drop_duplicates(subset=['Lot Number', 'Nama Produk', 'Catatan']).iterrows():
                        l = str(row['Lot Number'])
                        c = str(row['Catatan'])
                        lb = f"{str(row['Nama Produk'])} (Lot: {l})"
                        if c: lb += f" - {c}"
                        tersedia_lot.append(lb)
                        mapping_lot[lb] = (l, c)
                        
                    st.markdown(f"**2. Pilih Produk & Lot untuk di-Booking di {k_pilih}-{b_pilih}**")
                    pilihan_label = st.selectbox("🎯 Pilih Produk:", tersedia_lot, key="book_lot")
                    pilih_lot, pilih_cat = mapping_lot[pilihan_label]
                    
                    df_lot = df_book[(df_book['Lot Number'].astype(str) == pilih_lot) & (df_book['Catatan'].astype(str) == pilih_cat)].sort_values('Pallet Ke', ascending=True)
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
                                with st.spinner("Menyematkan label booking (Transactional Lock)..."):
                                    doc_ref_book = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                                    pallets_to_book = df_belum_book['Pallet Ke'].head(book_qty).tolist()

                                    @firestore.transactional
                                    def tx_booking(transaction, doc_ref):
                                        snap = doc_ref.get(transaction=transaction)
                                        log_fb_usage('read', 1)
                                        fresh_book = snap.to_dict().get('data', []) if snap.exists else []
                                        new_all_book_data = []
                                        for d in fresh_book:
                                            b_val = float(d.get('Baris', 0) or 0)
                                            p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                                            if b_val == float(b_pilih) and p_val in pallets_to_book:
                                                catatan_lama = str(d.get('Catatan', '')).replace('nan', '')
                                                if 'BOOKED' not in catatan_lama.upper():
                                                    d['Catatan'] = (f"[BOOKED: {book_do.strip()}] " + catatan_lama).strip()
                                            new_all_book_data.append(d)
                                        transaction.set(doc_ref, {'data': new_all_book_data})
                                        log_fb_usage('write', 1)
                                        return fresh_book

                                    fresh_snapshot = tx_booking(db.transaction(), doc_ref_book)
                                    clear_all_rak_caches()
                                    st.session_state.update({
                                        'undo_dict': {k_pilih: fresh_snapshot}, 
                                        'action_msg': f"Booking {book_qty} Pallet Lot '{pilih_lot}' untuk '{book_do}'", 
                                        'action_status': 'DONE'
                                    })
                                    st.rerun()
                    else: st.warning("⚠️ Semua pallet pada Lot ini sudah ter-booking.")
            else: st.info("Rak Kosong.")

# ----------------------------------------------------------
# TAB 7: RELOKASI RAK (SUPPORT PUSH-BACK & ROTASI LORONG SAMA)
# ----------------------------------------------------------
with tab7:
    st.header("🔄 Relokasi Pallet (Pindah Rak / Rotasi Push-Back)")
    st.caption("Pindahkan item antar rak secara presisi (Pallet, Dus, Pcs), atau tarik Lot ke posisi terdepan (Pallet Ke-1) dengan mode Push-Back.")
    
    col_r1, col_r2 = st.columns(2)
    with col_r1:
        st.markdown("**Rak ASAL (Sumber)**")
        r_asal_k_label = st.selectbox("Kolom Asal", ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"], key="r_ak")
        r_asal_k = r_asal_k_label.split(" ")[0]
        r_asal_b = st.number_input("Baris Asal", min_value=1, max_value=28, value=1, key="r_ab")
    with col_r2:
        st.markdown("**Rak TUJUAN (Destinasi)**")
        r_tuj_k_label = st.selectbox("Kolom Tujuan", ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"], key="r_tk")
        r_tuj_k = r_tuj_k_label.split(" ")[0]
        r_tuj_b = st.number_input("Baris Tujuan", min_value=1, max_value=28, value=1, key="r_tb")

    if st.button("🔍 Cari Data Rak Asal", type="primary", use_container_width=True):
        st.session_state.update({'relok_dicari': True, 'relok_ak': r_asal_k, 'relok_ab': r_asal_b, 'relok_tk': r_tuj_k, 'relok_tb': r_tuj_b})
        fetch_kolom_cached(r_asal_k, 'cache_relok_asal', force_refresh=True)
        
    if st.session_state.get('relok_dicari'):
        ak, ab = st.session_state['relok_ak'], st.session_state['relok_ab']
        # Izinkan user mengubah tujuan setelah klik cari tanpa kehilangan tabel asal
        tk, tb = r_tuj_k, r_tuj_b
        is_same_rack = (ak == tk and ab == tb)
        
        all_asal_data = fetch_kolom_cached(ak, 'cache_relok_asal', force_refresh=False)
        baris_asal_data = [d for d in all_asal_data if float(d.get('Baris', 0) or 0) == float(ab) and str(d.get('Part Number', '')).strip() != '']
        
        if baris_asal_data:
            df_asal = pd.DataFrame(baris_asal_data)
            df_asal['Pallet Ke'] = pd.to_numeric(df_asal['Pallet Ke'], errors='coerce')
            df_asal['Catatan'] = df_asal['Catatan'].fillna("").astype(str).replace('nan', '')
            df_asal['Keterangan'] = df_asal['Keterangan'].fillna("").astype(str).replace('nan', '')
            df_asal['Group_Key'] = df_asal['Lot Number'].astype(str) + "_" + df_asal['Nama Produk'].astype(str) + "_" + df_asal['Catatan'].astype(str)
            
            rekap_relok = []
            for _, row in df_asal.drop_duplicates(subset=['Group_Key']).iterrows():
                g_key = str(row['Group_Key'])
                l, nama_prod = str(row['Lot Number']), str(row['Nama Produk'])
                part_num, catatan = str(row['Part Number']), str(row['Catatan'])
                
                df_lot = df_asal[df_asal['Group_Key'] == g_key]
                jml_utuh_awal = df_lot[~df_lot['Keterangan'].str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                
                pecahan_awal, eceran_awal = 0, 0
                pecahan_rows = df_lot[df_lot['Keterangan'].str.contains('Pecahan|Eceran', case=False, na=False)]
                for _, r in pecahan_rows.iterrows():
                    p, e = parse_keterangan(r['Keterangan'])
                    pecahan_awal += p
                    eceran_awal += e
                    
                rekap_relok.append({
                    'Group_Key': g_key, 'Produk': nama_prod, 'Lot Number': l, 'Catatan': catatan, 'Part Number': part_num,
                    'Tersedia Utuh': int(jml_utuh_awal), 'Tersedia Dus': int(pecahan_awal), 'Tersedia Pcs': int(eceran_awal),
                    'PINDAH UTUH': 0, 'PINDAH DUS': 0, 'PINDAH PCS': 0
                })
                
            df_rekap_relok = pd.DataFrame(rekap_relok)
            
            st.divider()
            st.markdown(f"**2. Tentukan Jumlah yang Dipindah dari `{ak}-{ab}` ke `{tk}-{tb}`**")
            
            config_kolom = {
                'Group_Key': None, 'Part Number': None,
                'Produk': st.column_config.TextColumn("Produk", disabled=True),
                'Lot Number': st.column_config.TextColumn("Lot", disabled=True),
                'Catatan': st.column_config.TextColumn("Tag / Catatan", disabled=True),
                'Tersedia Utuh': st.column_config.NumberColumn("Stok Utuh", disabled=True),
                'Tersedia Dus': st.column_config.NumberColumn("Stok Dus", disabled=True),
                'Tersedia Pcs': st.column_config.NumberColumn("Stok Pcs", disabled=True),
                'PINDAH UTUH': st.column_config.NumberColumn("🚚 PINDAH UTUH", min_value=0),
                'PINDAH DUS': st.column_config.NumberColumn("📦 PINDAH DUS", min_value=0),
                'PINDAH PCS': st.column_config.NumberColumn("🧩 PINDAH PCS", min_value=0)
            }
            
            edited_relok = st.data_editor(df_rekap_relok, column_config=config_kolom, hide_index=True, use_container_width=True, key=f"editor_relok_{ak}_{ab}_{tk}_{tb}")
            
            relok_dari_depan = st.checkbox(
                "📥 Masukkan ke Rak Tujuan dari DEPAN (Push-back)", 
                value=is_same_rack,
                key="relok_dari_depan"
            )
            
            if is_same_rack:
                if relok_dari_depan:
                    st.info(f"🔄 **Mode Rotasi Lorong ({ak}-{ab}):** Item yang dipilih akan ditarik keluar dan dimasukkan kembali dari pintu depan (**Pallet Ke-1**), mendorong slot lainnya ke belakang.")
                else:
                    st.warning("⚠️ Rak Asal dan Tujuan sama. Centang opsi **Push-back** di atas jika ingin memindahkan item ke posisi terdepan (Pallet Ke-1).")
            
            if st.button("🚀 Eksekusi Pindah Rak", type="primary", use_container_width=True):
                if is_same_rack and not relok_dari_depan:
                    st.error("❌ Rak asal dan tujuan sama! Aktifkan checkbox Push-back jika ingin memutar posisi Lot ke depan.")
                else:
                    doc_ref_asal = db.collection(NAMA_DATABASE).document(f"Kolom_{ak}")
                    doc_ref_tuj = db.collection(NAMA_DATABASE).document(f"Kolom_{tk}") if ak != tk else None

                    def match_g_key(d, target_key):
                        c = str(d.get('Catatan', '')).replace('nan', '')
                        return (str(d.get('Lot Number','')) + "_" + str(d.get('Nama Produk','')) + "_" + c) == target_key

                    @firestore.transactional
                    def tx_relokasi(transaction):
                        snap_asal = doc_ref_asal.get(transaction=transaction)
                        log_fb_usage('read', 1)
                        fresh_asal = snap_asal.to_dict().get('data', []) if snap_asal.exists else []

                        if ak != tk:
                            snap_tuj = doc_ref_tuj.get(transaction=transaction)
                            log_fb_usage('read', 1)
                            fresh_tuj = snap_tuj.to_dict().get('data', []) if snap_tuj.exists else []
                        else:
                            fresh_tuj = fresh_asal

                        has_changes = False
                        error_msg = ""
                        logs_to_write = []

                        data_rak_gabungan = copy.deepcopy(fresh_asal) if ak == tk else None
                        data_rak_asal = copy.deepcopy(fresh_asal) if ak != tk else data_rak_gabungan
                        data_rak_tujuan = copy.deepcopy(fresh_tuj) if ak != tk else data_rak_gabungan
                        
                        undo_dict_setup = {ak: fresh_asal}
                        if ak != tk: undo_dict_setup[tk] = fresh_tuj

                        all_moved_records = []

                        for _, row in edited_relok.iterrows():
                            pindah_utuh = int(row['PINDAH UTUH'])
                            pindah_dus = int(row['PINDAH DUS'])
                            pindah_pcs = int(row['PINDAH PCS'])
                            if pindah_utuh == 0 and pindah_dus == 0 and pindah_pcs == 0: continue
                                
                            g_key, l, nama_prod = str(row['Group_Key']), str(row['Lot Number']), str(row['Produk'])
                            part_num, catatan_awal = str(row['Part Number']), str(row['Catatan'])
                            
                            awal_utuh, awal_dus, awal_pcs = int(row['Tersedia Utuh']), int(row['Tersedia Dus']), int(row['Tersedia Pcs'])
                            sisa_utuh = awal_utuh - pindah_utuh
                            sisa_dus, sisa_pcs = awal_dus - pindah_dus, awal_pcs - pindah_pcs
                            
                            if sisa_utuh < 0 or sisa_dus < 0 or sisa_pcs < 0:
                                error_msg = f"❌ Jumlah pindah Lot {l} melebihi stok tersedia!"
                                break
                                
                            has_changes = True
                            df_lot = pd.DataFrame([d for d in data_rak_asal if match_g_key(d, g_key) and float(d.get('Baris',0) or 0)==float(ab)])
                            if df_lot.empty:
                                error_msg = f"❌ Data Lot {l} di rak asal sudah berubah. Silakan klik 'Cari Data Rak Asal' ulang."
                                break
                            df_lot = df_lot.sort_values('Pallet Ke', ascending=False)
                            
                            # --- FASE 1: CABUT DARI RAK ASAL ---
                            slot_tambahan = 1 if (sisa_dus > 0 or sisa_pcs > 0) else 0
                            total_slot_sisa = sisa_utuh + slot_tambahan
                            out_qty = len(df_lot) - total_slot_sisa
                            if out_qty < 0:
                                error_msg = f"❌ Stok terkini Lot {l} tidak mencukupi untuk dipindahkan."
                                break
                            pallets_to_remove = df_lot['Pallet Ke'].head(out_qty).tolist() if out_qty > 0 else []
                            
                            updated_rak_asal = []
                            for d in data_rak_asal:
                                if match_g_key(d, g_key) and float(d.get('Baris', 0) or 0) == float(ab):
                                    p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                                    if p_val in pallets_to_remove: continue 
                                    
                                    sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
                                    if not sisa_pallets.empty and p_val == sisa_pallets['Pallet Ke'].max():
                                        if sisa_dus > 0 or sisa_pcs > 0:
                                            kp = []
                                            if sisa_dus > 0: kp.append(f"Pecahan: {sisa_dus} qty")
                                            if sisa_pcs > 0: kp.append(f"Eceran: {sisa_pcs} pcs")
                                            d['Keterangan'] = " | ".join(kp)
                                        else: d['Keterangan'] = ""
                                    else:
                                        if 'Pecahan' in str(d.get('Keterangan', '')) or 'Eceran' in str(d.get('Keterangan', '')): 
                                            d['Keterangan'] = ""
                                updated_rak_asal.append(d)
                                
                            data_rak_asal = updated_rak_asal
                            if ak == tk: data_rak_tujuan = data_rak_asal
                            
                            # --- SIAPKAN RECORD PINDAHAN ---
                            catatan_baru = catatan_awal.replace("[BAYANGAN - OVERCAPACITY]", "").strip()
                            if tk == 'S' and ak != 'S':
                                cat_bersih = re.sub(r'\[Asal:.*?\]\s*', '', catatan_baru)
                                catatan_baru = (f"[Asal: {ak}-{ab}] " + cat_bersih).strip()
                            elif tk != 'S':
                                catatan_baru = re.sub(r'\[Asal:.*?\]\s*', '', catatan_baru).strip()
                                
                            for _ in range(pindah_utuh):
                                all_moved_records.append({
                                    'Kolom': tk, 'Baris': float(tb), 'Pallet Ke': 0.0,
                                    'Part Number': part_num, 'Lot Number': l, 'Nama Produk': nama_prod,
                                    'Status': 'Terisi', 'Keterangan': '', 'Catatan': catatan_baru
                                })
                                
                            if pindah_dus > 0 or pindah_pcs > 0:
                                kp = []
                                if pindah_dus > 0: kp.append(f"Pecahan: {pindah_dus} qty")
                                if pindah_pcs > 0: kp.append(f"Eceran: {pindah_pcs} pcs")
                                all_moved_records.append({
                                    'Kolom': tk, 'Baris': float(tb), 'Pallet Ke': 0.0,
                                    'Part Number': part_num, 'Lot Number': l, 'Nama Produk': nama_prod,
                                    'Status': 'Terisi', 'Keterangan': " | ".join(kp), 'Catatan': catatan_baru
                                })
                            
                            jenis_log = f"RELOKASI{' (Push-Back)' if relok_dari_depan else ''}"
                            logs_to_write.append((jenis_log, f"{ak} ke {tk}", f"{ab} ke {tb}", nama_prod, part_num, l, pindah_utuh, pindah_dus, pindah_pcs))

                        if error_msg or not has_changes:
                            return False, error_msg, {}, []

                        # --- FASE 2: SUSUN ULANG RAK ASAL & SISIPKAN KE RAK TUJUAN ---
                        if ak == tk and ab == tb:
                            # Rotasi di baris yang sama: item pindahan ditaruh di depan, sisa penghuni lama di belakangnya
                            existing_tb = sorted(
                                [d for d in data_rak_tujuan if float(d.get('Baris', 0) or 0) == float(tb) and str(d.get('Part Number', '')).strip() != ''],
                                key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))
                            )
                            lain_tb = [d for d in data_rak_tujuan if float(d.get('Baris', 0) or 0) != float(tb)]
                            
                            combined_tb = (all_moved_records + existing_tb) if relok_dari_depan else (existing_tb + all_moved_records)
                            combined_tb = update_bayangan_baris(combined_tb, tk, tb)
                            
                            transaction.set(doc_ref_asal, {'data': lain_tb + combined_tb})
                            log_fb_usage('write', 1)

                        elif ak == tk and ab != tb:
                            # Beda baris tapi dalam satu dokumen Kolom yang sama
                            baris_asal_sisa = sorted(
                                [d for d in data_rak_asal if float(d.get('Baris', 0) or 0) == float(ab) and str(d.get('Part Number', '')).strip() != ''],
                                key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))
                            )
                            baris_asal_sisa = update_bayangan_baris(baris_asal_sisa, ak, ab)

                            existing_tb = sorted(
                                [d for d in data_rak_asal if float(d.get('Baris', 0) or 0) == float(tb) and str(d.get('Part Number', '')).strip() != ''],
                                key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))
                            )
                            combined_tb = (all_moved_records + existing_tb) if relok_dari_depan else (existing_tb + all_moved_records)
                            combined_tb = update_bayangan_baris(combined_tb, tk, tb)

                            lain_bars = [d for d in data_rak_asal if float(d.get('Baris', 0) or 0) not in (float(ab), float(tb))]
                            transaction.set(doc_ref_asal, {'data': lain_bars + baris_asal_sisa + combined_tb})
                            log_fb_usage('write', 1)

                        else:
                            # Beda dokumen Kolom (ak != tk)
                            baris_asal_sisa = sorted(
                                [d for d in data_rak_asal if float(d.get('Baris', 0) or 0) == float(ab) and str(d.get('Part Number', '')).strip() != ''],
                                key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))
                            )
                            baris_asal_sisa = update_bayangan_baris(baris_asal_sisa, ak, ab)
                            lain_asal = [d for d in data_rak_asal if float(d.get('Baris', 0) or 0) != float(ab)]
                            transaction.set(doc_ref_asal, {'data': lain_asal + baris_asal_sisa})

                            existing_tb = sorted(
                                [d for d in data_rak_tujuan if float(d.get('Baris', 0) or 0) == float(tb) and str(d.get('Part Number', '')).strip() != ''],
                                key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))
                            )
                            combined_tb = (all_moved_records + existing_tb) if relok_dari_depan else (existing_tb + all_moved_records)
                            combined_tb = update_bayangan_baris(combined_tb, tk, tb)
                            lain_tuj = [d for d in data_rak_tujuan if float(d.get('Baris', 0) or 0) != float(tb)]
                            transaction.set(doc_ref_tuj, {'data': lain_tuj + combined_tb})
                            log_fb_usage('write', 2)

                        return True, "", undo_dict_setup, logs_to_write

                    sukses, error_msg, undo_dict_setup, logs_list = tx_relokasi(db.transaction())
                    if error_msg:
                        st.error(error_msg)
                    elif sukses:
                        for lg in logs_list:
                            catat_log_mutasi(*lg)
                        clear_all_rak_caches()
                        mode_txt = " (Push-Back ke Depan)" if relok_dari_depan else ""
                        st.session_state.update({
                            'undo_dict': undo_dict_setup,
                            'action_msg': f"Berhasil memindahkan item dari Rak {ak}-{ab} ke {tk}-{tb}{mode_txt}",
                            'action_status': 'DONE',
                            'relok_dicari': False,
                            'kor_dicari': False
                        })
                        st.rerun()
                    else:
                        st.info("Tidak ada jumlah yang dipindah.")
        else:
            st.warning("Rak Asal Kosong!")
            st.session_state['relok_dicari'] = False

# ----------------------------------------------------------
# TAB 8: KOREKSI DATA (EDIT LANGSUNG)
# ----------------------------------------------------------
with tab8:
    st.header("✏️ Koreksi Data Rak (Manual Edit)")
    st.caption("Gunakan tab ini untuk memperbaiki kesalahan input seperti salah Tipe Produk, salah No Lot, salah Part Number, atau tag Catatan secara instan.")
    
    if 'kor_dicari' not in st.session_state: st.session_state.update({'kor_dicari': False, 'kor_k': "A", 'kor_b': 1})

    c_kor1, c_kor2, c_kor3 = st.columns([1, 1, 2])
    with c_kor1: 
        kor_kolom_label = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"], key="kor_kolom")
        kor_kolom = kor_kolom_label.split(" ")[0]
    with c_kor2: 
        kor_baris = st.number_input("Nomor Baris", min_value=1, max_value=28, value=1, key="kor_baris")
    with c_kor3:
        st.write("") 
        st.write("")
        if st.button("🔍 Tarik Data untuk Koreksi", use_container_width=True): 
            st.session_state.update({'kor_dicari': True, 'kor_k': kor_kolom, 'kor_b': kor_baris})
            fetch_kolom_cached(kor_kolom, 'cache_kor_data', force_refresh=True)
            
    st.divider()

    if st.session_state.get('kor_dicari'):
        kk, kb = st.session_state['kor_k'], st.session_state['kor_b']
        all_kor_data = fetch_kolom_cached(kk, 'cache_kor_data', force_refresh=False)
        baris_kor_data = [d for d in all_kor_data if float(d.get('Baris', 0) or 0) == float(kb)]
        
        if baris_kor_data:
            df_kor = pd.DataFrame(baris_kor_data)
            df_kor['Pallet Ke'] = pd.to_numeric(df_kor['Pallet Ke'], errors='coerce')
            
            for col in ['Part Number', 'Lot Number', 'Nama Produk', 'Keterangan', 'Catatan']:
                if col in df_kor.columns:
                    df_kor[col] = df_kor[col].fillna("").astype(str).replace('nan', '')
                    
            df_kor = df_kor.sort_values('Pallet Ke', ascending=True)
            
            st.markdown(f"**Data Baris {kk}-{kb}**")
            
            config_kor = {
                'Kolom': None, 'Baris': None, 'Status': None,
                'Pallet Ke': st.column_config.NumberColumn("Idx Pallet", disabled=True),
                'Part Number': st.column_config.TextColumn("Part Number", required=True),
                'Lot Number': st.column_config.TextColumn("Lot Number", required=True),
                'Nama Produk': st.column_config.TextColumn("Nama Produk", required=True),
                'Keterangan': st.column_config.TextColumn("Keterangan (Dus/Pcs)"),
                'Catatan': st.column_config.TextColumn("Catatan / Booking Tag")
            }
            
            edited_kor_df = st.data_editor(
                df_kor, 
                column_config=config_kor, 
                hide_index=True, 
                use_container_width=True, 
                num_rows="fixed", 
                key=f"editor_kor_{kk}_{kb}"
            )
            
            if st.button("💾 Simpan Perubahan Koreksi", type="primary"):
                has_changed = not edited_kor_df.equals(df_kor)
                
                if has_changed:
                    with st.spinner("Menyimpan koreksi (Transactional Lock)..."):
                        doc_ref_kor = db.collection(NAMA_DATABASE).document(f"Kolom_{kk}")
                        new_baris_data = edited_kor_df.to_dict('records')
                        
                        for d in new_baris_data:
                            d['Kolom'] = kk
                            d['Baris'] = float(kb)
                            d['Pallet Ke'] = float(d.get('Pallet Ke', 0))
                            d['Status'] = 'Terisi'
                            d['Part Number'] = str(d.get('Part Number', '')).strip().upper()
                            d['Keterangan'] = str(d.get('Keterangan', '')).strip().replace('nan', '')
                            d['Catatan'] = str(d.get('Catatan', '')).strip().replace('nan', '')

                        @firestore.transactional
                        def tx_koreksi(transaction, doc_ref):
                            snap = doc_ref.get(transaction=transaction)
                            log_fb_usage('read', 1)
                            fresh_kor = snap.to_dict().get('data', []) if snap.exists else []
                            lain_kor_fresh = [d for d in fresh_kor if float(d.get('Baris', 0) or 0) != float(kb)]
                            transaction.set(doc_ref, {'data': lain_kor_fresh + new_baris_data})
                            log_fb_usage('write', 1)
                            return fresh_kor

                        fresh_snapshot = tx_koreksi(db.transaction(), doc_ref_kor)
                        catat_log_mutasi("KOREKSI DATA", kk, kb, "Multiple Edit", "Multiple", "-", 0, 0, 0, "Koreksi Manual via Tab Koreksi")
                        clear_all_rak_caches()
                        
                        st.session_state.update({
                            'undo_dict': {kk: fresh_snapshot},
                            'action_msg': f"Koreksi data di Rak {kk}-{kb} berhasil disimpan.",
                            'action_status': 'DONE'
                        })
                        st.rerun()
                else:
                    st.info("Tidak ada perubahan yang dilakukan pada tabel.")
        else:
            st.info("Rak Kosong, tidak ada yang bisa dikoreksi.")
