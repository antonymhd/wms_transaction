import streamlit as st
import pandas as pd
import re
import requests
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================================
# 1. KONSTANTA & INISIALISASI FIREBASE
# ==========================================================
NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'
DB_UNDO_STATE = 'wms_undo_state'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

def get_waktu_jkt():
    return datetime.utcnow() + timedelta(hours=7)

# ==========================================================
# 2. CSS ADAPTIF & KOMPONEN HEADER + SINKRONISASI STOK
# ==========================================================
def inject_custom_css(accent_color="#10B981", accent_border="#059669", accent_bg_rgba="rgba(16, 185, 129, 0.1)"):
    st.markdown(f"""
        <style>
            #MainMenu {{visibility: hidden;}}
            header {{visibility: hidden;}}
            footer {{visibility: hidden;}}
            
            .stButton>button {{
                min-height: 70px; 
                height: auto;
                font-size: 16px; 
                font-weight: 600;
                border-radius: 12px;
                white-space: pre-wrap; 
                word-wrap: break-word;
                text-align: left; 
                padding: 14px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
                transition: all 0.15s ease-in-out;
            }}
            
            button[kind="primary"] {{
                background-color: {accent_color} !important;
                border-color: {accent_border} !important;
                color: white !important;
                font-size: 18px !important;
                text-align: center !important;
            }}
            
            .btn-undo > button {{
                background-color: #DC2626 !important;
                border-color: #EF4444 !important;
                color: white !important;
                text-align: center !important;
            }}

            .btn-center > button {{
                text-align: center !important;
                font-size: 19px !important;
            }}

            .btn-header-action > button {{
                min-height: 58px !important;
                padding: 8px 10px !important;
                font-size: 13px !important;
                text-align: center !important;
            }}
            
            .progress-container {{
                font-size: 13px;
                font-weight: 500;
                margin-bottom: 15px;
                padding-bottom: 10px;
                border-bottom: 1px solid var(--border-color);
                text-align: center;
            }}
            .step-active {{ color: {accent_color}; font-weight: bold; background: {accent_bg_rgba}; padding: 4px 8px; border-radius: 6px; }}
            .step-inactive {{ color: var(--text-color); opacity: 0.6; }}
            
            .db-info-box {{
                background-color: var(--secondary-background-color);
                border: 1px solid var(--border-color);
                padding: 10px 14px;
                border-radius: 10px;
                margin-bottom: 10px;
            }}
            
            .btn-gramasi > button {{ font-size: 20px !important; text-align: center !important; }}
        </style>
    """, unsafe_allow_html=True)

# ==========================================================
# 3. TELEGRAM & PARSING UTILITAS
# ==========================================================
def kirim_notif_telegram(pesan_html):
    try:
        token = st.secrets["telegram"]["bot_token"]
        raw_chat_ids = str(st.secrets["telegram"]["chat_id"])
        daftar_id = [cid.strip() for cid in raw_chat_ids.split(",") if cid.strip()]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        for cid in daftar_id:
            payload = {"chat_id": cid, "text": pesan_html, "parse_mode": "HTML"}
            requests.post(url, json=payload, timeout=2)
    except Exception:
        pass

def escape_html(text):
    return str(text).replace("&", "&").replace("<", "<").replace(">", ">")

def parse_keterangan(ket_str):
    ket_str = str(ket_str).lower()
    pecahan, eceran = 0, 0
    m_pec = re.search(r'pecahan:\s*(\d+)', ket_str)
    if m_pec: pecahan = int(m_pec.group(1))
    m_ec = re.search(r'eceran:\s*(\d+)', ket_str)
    if m_ec: eceran = int(m_ec.group(1))
    return pecahan, eceran

def parse_fifo_key(lot_str):
    s = str(lot_str).strip()
    match = re.search(r'(\d{2})(\d{2})(\d{2})$', s)
    if match:
        dd, mm, yy = match.groups()
        return f"{yy}{mm}{dd}_{s}"
    return f"999999_{s}"

def get_satuan_kemasan(part_num):
    p = str(part_num).strip().upper()
    if p.endswith('C'): return "Sak"
    elif p.endswith('A'): return "Nictainer"
    return "Dus"

def ekstrak_gramasi(nama_produk):
    match = re.search(r'(\d+[.,]\d+)\s*(?:gr|GR)', str(nama_produk), re.IGNORECASE)
    if match: return f"{float(match.group(1).replace(',', '.')):.2f} gr"
    return "Lainnya"

def set_kategori(nama):
    nama_str = str(nama).strip().upper()
    if nama_str.startswith('PREFORM'): return 'PREFORM'
    elif nama_str.startswith('CUP NATURAL'): return 'CUP NATURAL'
    elif nama_str.startswith('CUP PRINTING'): return 'CUP PRINTING'
    elif nama_str.startswith('CUP COLOR'): return 'CUP COLOR'
    elif nama_str.startswith('CAP'): return 'CAP'
    else: return 'LAINNYA'

def format_nama_warna_bold(nama_barang):
    parts = str(nama_barang).rsplit(',', 1)
    if len(parts) == 2: return f"{parts[0]}, **{parts[1].strip()}**"
    return str(nama_barang)

# ==========================================================
# 4. DATA LOADERS & ALGORITMA PENCARIAN HIBRIDA (LIFO + FIFO)
# ==========================================================
@st.cache_data(ttl=300)
def get_all_racking_data():
    docs = db.collection(NAMA_DATABASE).stream()
    semua_data = []
    for doc in docs:
        doc_data = doc.to_dict()
        if 'data' in doc_data: semua_data.extend(doc_data['data'])
    return semua_data

@st.cache_data(ttl=3600)
def load_master_data():
    try:
        df = pd.read_csv("Master_Produk.csv", sep=";", dtype=str)
        df = df.dropna(subset=['Nama Barang'])
        df['Kategori'] = df['Nama Barang'].apply(set_kategori)
        df['Gramasi'] = df['Nama Barang'].apply(ekstrak_gramasi)
        if 'Part Number' not in df.columns: df['Part Number'] = ""
        
        df.columns = df.columns.str.strip()
        
        def clean_num(val):
            try:
                c = re.sub(r'[^\d]', '', str(val))
                return int(c) if c else 0
            except Exception:
                return 0
                
        df['Total_Pcs_Pallet'] = df['Isi 1 Pallet (pcs)'].apply(clean_num) if 'Isi 1 Pallet (pcs)' in df.columns else 0
        df['Pcs_Per_Box'] = df['Box / Pallet'].apply(clean_num) if 'Box / Pallet' in df.columns else 0
        df['Pcs_Per_Sac'] = df['Sac / Pallet'].apply(clean_num) if 'Sac / Pallet' in df.columns else 0
        return df
    except Exception:
        return pd.DataFrame(columns=[
            'Nama Barang', 'Kategori', 'Gramasi', 'Part Number',
            'Total_Pcs_Pallet', 'Pcs_Per_Box', 'Pcs_Per_Sac'
        ])

def cari_lokasi_stok_hibrida(semua_data, target_item):
    """
    Mengurutkan lokasi pengambilan stok dengan aturan Hibrida Shuttle Racking:
    1. Kelompok AKSES LANGSUNG (0 palet penghalang di depan/mulut rak) -> diurutkan FIFO (Lot terlama dulu).
    2. Kelompok TERHALANG DI DALAM (ada palet lain di depan) -> diurutkan dari penghalang paling sedikit, lalu FIFO.
    """
    if not semua_data:
        return pd.DataFrame()
        
    df_all = pd.DataFrame(semua_data)
    df_all['Baris_Int'] = pd.to_numeric(df_all['Baris'], errors='coerce').fillna(0).astype(int)
    df_all['Pallet_Ke_Num'] = pd.to_numeric(df_all['Pallet Ke'], errors='coerce').fillna(0)
    df_all['Catatan'] = df_all['Catatan'].fillna("").astype(str).replace('nan', '')
    
    df_target = df_all[
        (df_all['Nama Produk'] == target_item) & 
        (df_all['Part Number'].astype(str).str.strip() != "")
    ].copy()
    
    if df_target.empty:
        return pd.DataFrame()
        
    df_target['Group_Key'] = (
        df_target['Kolom'].astype(str) + "_" + 
        df_target['Baris_Int'].astype(str) + "_" + 
        df_target['Lot Number'].astype(str)
    )
    
    rekap_lokasi = []
    for _, df_group in df_target.groupby('Group_Key'):
        kol = str(df_group.iloc[0]['Kolom'])
        bar = int(df_group.iloc[0]['Baris_Int'])
        lot = str(df_group.iloc[0]['Lot Number'])
        catatan = str(df_group.iloc[0]['Catatan'])
        part_num = str(df_group.iloc[0]['Part Number'])
        satuan_str = get_satuan_kemasan(part_num)
        
        jml_utuh = df_group[~df_group['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
        pecahan_rows = df_group[df_group['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
        jml_dus = sum(parse_keterangan(r['Keterangan'])[0] for _, r in pecahan_rows.iterrows())
        
        # Deteksi apakah palet target terjepit di tengah/belakang lorong (Shuttle LIFO)
        max_pos_target = df_group['Pallet_Ke_Num'].max()
        df_aisle = df_all[(df_all['Kolom'] == kol) & (df_all['Baris_Int'] == bar)]
        
        df_penghalang = df_aisle[
            (df_aisle['Pallet_Ke_Num'] > max_pos_target) &
            ~((df_aisle['Nama Produk'] == target_item) & (df_aisle['Lot Number'].astype(str) == lot))
        ]
        jml_penghalang = len(df_penghalang)
        
        info_penghalang = ""
        if jml_penghalang > 0:
            item_depan = df_penghalang['Nama Produk'].dropna().unique().tolist()
            if len(item_depan) == 1 and item_depan[0] == target_item:
                lot_depan = df_penghalang['Lot Number'].dropna().unique().tolist()
                info_penghalang = f"Lot lebih baru ({', '.join(lot_depan[:2])})"
            else:
                nama_depan = [x.split(',')[-1].strip() if ',' in x else x[:18] for x in item_depan[:2]]
                info_penghalang = f"Item lain ({', '.join(nama_depan)})"
                
        rekap_lokasi.append({
            'Kolom': kol,
            'Baris': bar,
            'Lot Number': lot,
            'FIFO_Key': parse_fifo_key(lot),
            'Pallet': jml_utuh,
            'Dus': jml_dus,
            'Satuan': satuan_str,
            'Catatan': catatan,
            'Part': part_num,
            'JmlPenghalang': jml_penghalang,
            'InfoPenghalang': info_penghalang,
            'IsTerhalang': 1 if jml_penghalang > 0 else 0
        })
        
    df_rekap = pd.DataFrame(rekap_lokasi).sort_values(
        by=['IsTerhalang', 'JmlPenghalang', 'FIFO_Key'],
        ascending=[True, True, True]
    ).reset_index(drop=True)
    
    return df_rekap

# ==========================================================
# 5. AUDIT LOG & PERSISTENT UNDO (30 MENIT)
# ==========================================================
def catat_log_mutasi(jenis, kolom, baris, produk, part_num, no_lot, pallet_utuh, pecahan, eceran, keterangan=""):
    waktu_sekarang = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")
    log_data = {
        "Waktu Transaksi": waktu_sekarang, "Jenis Transaksi": jenis, "Lokasi Rak": f"{kolom}-{baris}",
        "Nama Produk": produk, "Part Number": part_num, "No Lot": str(no_lot).strip() or "-",
        "Mutasi Pallet": float(pallet_utuh), "Mutasi Pecahan (Dus/Sak)": float(pecahan),
        "Mutasi Eceran (Pcs)": float(eceran), "Keterangan": keterangan,
        "Timestamp_System": firestore.SERVER_TIMESTAMP
    }
    try: db.collection(DB_LOG_MUTASI).add(log_data)
    except Exception: pass 

def simpan_undo_firestore(prefix_app, operator_name, undo_dict, action_msg, mode_label):
    now_str = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "status": "DONE",
        "mode": mode_label,
        "undo_dict": undo_dict,
        "action_msg": action_msg,
        "created_at": now_str
    }
    st.session_state.undo_record = payload
    try:
        db.collection(DB_UNDO_STATE).document(f"{prefix_app}_{operator_name}").set(payload)
    except Exception:
        pass

def muat_undo_firestore(prefix_app, operator_name):
    try:
        doc = db.collection(DB_UNDO_STATE).document(f"{prefix_app}_{operator_name}").get()
        if doc.exists:
            data = doc.to_dict()
            if data.get("status") == "DONE" and data.get("created_at"):
                created_dt = datetime.strptime(data["created_at"], "%Y-%m-%d %H:%M:%S")
                if (get_waktu_jkt() - created_dt) <= timedelta(minutes=30):
                    st.session_state.undo_record = data
                    return
    except Exception:
        pass
    st.session_state.undo_record = None

def tutup_undo_firestore(prefix_app, operator_name, new_status="CLOSED"):
    st.session_state.undo_record = {"status": new_status} if new_status == "UNDONE" else None
    try:
        db.collection(DB_UNDO_STATE).document(f"{prefix_app}_{operator_name}").update({"status": new_status})
    except Exception:
        pass

def render_header_dan_undo(app_title, accent_color, prefix_app, mode_badge="", reset_keys=None):
    """Merender header status, tombol Sinkronkan Stok, Ganti Operator, dan Banner Undo 30 Menit"""
    col_hdr, col_sync, col_op = st.columns([2.6, 1.2, 1.2])
    with col_hdr:
        st.markdown(f"""
            <div class="db-info-box">
                <div style="font-size: 14px; font-weight: bold; color: {accent_color};">
                    🧭 {app_title}
                </div>
                <div style="font-size: 12px; opacity: 0.85; margin-top: 2px;">
                    👤 <strong>{st.session_state.operator}</strong>  |  📡 {NAMA_DATABASE}{mode_badge}
                </div>
            </div>
        """, unsafe_allow_html=True)
    with col_sync:
        st.markdown('<div class="btn-header-action">', unsafe_allow_html=True)
        if st.button("🔄 Sinkronkan\nStok", use_container_width=True, help="Muat ulang data rak terbaru dari server"):
            get_all_racking_data.clear()
            st.toast("Data stok rak berhasil disinkronkan!", icon="🔄")
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    with col_op:
        st.markdown('<div class="btn-header-action">', unsafe_allow_html=True)
        if st.button("👤 Ganti\nOperator", use_container_width=True):
            st.session_state.operator = None
            st.session_state.undo_record = None
            if reset_keys:
                for rk in reset_keys:
                    st.session_state[rk] = None
            st.session_state.slide = 0
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    undo_rec = st.session_state.get("undo_record")
    if undo_rec and undo_rec.get("status") == "DONE":
        masih_berlaku = True
        if undo_rec.get("created_at"):
            created_dt = datetime.strptime(undo_rec["created_at"], "%Y-%m-%d %H:%M:%S")
            if (get_waktu_jkt() - created_dt) > timedelta(minutes=30):
                masih_berlaku = False
                st.session_state.undo_record = None
                
        if masih_berlaku:
            mode_undo = undo_rec.get("mode", "TRANSAKSI")
            judul_sukses = "BERHASIL MASUK:" if mode_undo == "INBOUND" else f"BERHASIL {mode_undo}:"
            with st.container(border=True):
                st.success(f"✅ **{judul_sukses}** {undo_rec.get('action_msg', '')}")
                st.caption(f"⏱️ Opsi Undo tersedia selama 30 menit (Input pukul {undo_rec.get('created_at', '')[-8:]} WIB)")
                c_u1, c_u2 = st.columns(2)
                with c_u1:
                    st.markdown('<div class="btn-undo">', unsafe_allow_html=True)
                    if st.button("⏪ BATALKAN (UNDO)", use_container_width=True):
                        with st.spinner("Memulihkan posisi stok..."):
                            batch_undo = db.batch()
                            kol_terdampak = []
                            for kol, dat in undo_rec.get("undo_dict", {}).items():
                                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{kol}")
                                batch_undo.set(doc_ref, {'data': dat})
                                kol_terdampak.append(kol)
                            batch_undo.commit()
                            
                            get_all_racking_data.clear()
                            tutup_undo_firestore(prefix_app, st.session_state.operator, "UNDONE")
                            catat_log_mutasi(f"PEMBATALAN (UNDO {mode_undo})", ",".join(kol_terdampak), "ALL", "Koreksi Sistem", "-", "-", 0, 0, 0, f"Dibatalkan oleh: {st.session_state.operator}")
                            
                            pesan_undo = (
                                f"⏪ <b>UNDO {mode_undo} - RACK NAVIGATOR</b>\n"
                                f"━━━━━━━━━━━━━━━━━━\n"
                                f"📍 <b>Kolom Dipulihkan:</b> {', '.join(kol_terdampak)}\n"
                                f"👤 <b>Operator:</b> {escape_html(st.session_state.operator)}\n"
                                f"🕒 <b>Waktu:</b> {get_waktu_jkt().strftime('%H:%M:%S WIB')}"
                            )
                            kirim_notif_telegram(pesan_undo)
                            st.rerun()
                    st.markdown('</div>', unsafe_allow_html=True)
                with c_u2:
                    if st.button("TUTUP / LANJUT", use_container_width=True):
                        tutup_undo_firestore(prefix_app, st.session_state.operator, "CLOSED")
                        st.rerun()

    elif undo_rec and undo_rec.get("status") == "UNDONE":
        with st.container(border=True):
            st.warning("⏪ **Transaksi dibatalkan.** Stok kembali ke posisi semula.")
            if st.button("TUTUP PESAN", use_container_width=True):
                st.session_state.undo_record = None
                st.rerun()

# ==========================================================
# 6. TRANSAKSI ATOMIK FIREBASE (DENGAN VALIDASI KAPASITAS)
# ==========================================================
@firestore.transactional
def proses_inbound_multi_transaction(transaction, doc_refs_dict, alokasi_list, lot_pilih, part_num, item_name, tipe_pecahan, catatan):
    snapshots = {kol: ref.get(transaction=transaction) for kol, ref in doc_refs_dict.items()}
    backup_kolom = {}
    data_per_kolom = {}
    
    for kol, snap in snapshots.items():
        existing = snap.to_dict().get('data', []) if snap.exists else []
        backup_kolom[kol] = [d.copy() for d in existing]
        data_per_kolom[kol] = [d.copy() for d in existing]
        
    in_is_sac = str(part_num).strip().upper().endswith('C')
    
    for item_alok in alokasi_list:
        k_pilih = item_alok['Kolom']
        b_int = int(item_alok['Baris'])
        b_pilih = float(b_int)
        qty_p = int(item_alok['qty_p'])
        qty_c = int(item_alok['qty_c'])
        
        all_col = data_per_kolom[k_pilih]
        rak_ini = [d for d in all_col if float(d.get('Baris', 0) or 0) == b_pilih]
        rak_lain = [d for d in all_col if float(d.get('Baris', 0) or 0) != b_pilih]
        
        max_sac = 31 if b_int <= 16 else 44
        max_dus = 32 if b_int <= 16 else 46
        
        used_cap = 0.0
        for p in rak_ini:
            p_part = str(p.get('Part Number', '')).strip().upper()
            used_cap += (1.0 / max_sac) if p_part.endswith('C') else (1.0 / max_dus)
            
        slot_masuk = qty_p + (1 if qty_c > 0 else 0)
        inc_cap = (slot_masuk / max_sac) if in_is_sac else (slot_masuk / max_dus)
        
        if used_cap + inc_cap > 1.0001:
            raise ValueError(f"Rak {k_pilih}-{b_int} baru saja terisi operator lain sehingga kapasitas tidak mencukupi. Silakan tekan 'Sinkronkan Stok' dan ulangi pencarian.")
            
        max_pallet = int(max([float(d.get('Pallet Ke', 0)) for d in rak_ini])) if rak_ini else 0
        
        pallets_baru = []
        for i in range(qty_p):
            pallets_baru.append({
                'Kolom': k_pilih, 'Baris': b_pilih,
                'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
                'Keterangan': 'Utuh', 'Catatan': catatan,
                'Pallet Ke': float(max_pallet + 1 + i)
            })
            
        if qty_c > 0:
            pallets_baru.append({
                'Kolom': k_pilih, 'Baris': b_pilih,
                'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
                'Keterangan': f"Pecahan: {qty_c} {tipe_pecahan}", 'Catatan': catatan,
                'Pallet Ke': float(max_pallet + 1 + qty_p)
            })
            
        rak_ini.extend(pallets_baru)
        rak_ini = sorted(rak_ini, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
        for idx_p, d in enumerate(rak_ini, 1):
            d['Pallet Ke'] = float(idx_p)
            
        data_per_kolom[k_pilih] = rak_lain + rak_ini
        
    for kol, ref in doc_refs_dict.items():
        if snapshots[kol].exists:
            transaction.update(ref, {'data': data_per_kolom[kol]})
        else:
            transaction.set(ref, {'data': data_per_kolom[kol]})
            
    return backup_kolom

@firestore.transactional
def proses_pengambilan_stok(transaction, doc_ref, k_pilih, b_pilih, lot_pilih, part_num, item_name, init_pallet, init_dus, final_pallet, final_dus, satuan_kemasan, catatan_lama):
    """Transaksi terpadu Initial vs Final untuk Outbound maupun Ambil WIP"""
    snapshot = doc_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise ValueError("Data Kolom tidak ditemukan di database.")
        
    all_col_data = snapshot.to_dict().get('data', [])
    data_lama_sebelum_update = [d.copy() for d in all_col_data]
    
    rak_ini = [d for d in all_col_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
    rak_lain = [d for d in all_col_data if float(d.get('Baris', 0) or 0) != float(b_pilih)]
    
    lot_records = [d for d in rak_ini if str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name]
    db_utuh = sum(1 for d in lot_records if 'pecahan' not in str(d.get('Keterangan', '')).lower() and 'eceran' not in str(d.get('Keterangan', '')).lower())
    db_dus = sum(parse_keterangan(d.get('Keterangan', ''))[0] for d in lot_records if 'pecahan' in str(d.get('Keterangan', '')).lower() or 'eceran' in str(d.get('Keterangan', '')).lower())
    
    if db_utuh != init_pallet or db_dus != init_dus:
        raise ValueError(f"Stok di database baru saja berubah ({db_utuh} Pallet, {db_dus} {satuan_kemasan}). Silakan tekan 'Sinkronkan Stok'.")
    
    sisa_rak_ini = [d for d in rak_ini if not (str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name)]
    
    records_baru = []
    for _ in range(final_pallet):
        records_baru.append({
            'Kolom': k_pilih, 'Baris': float(b_pilih),
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': 'Utuh', 'Catatan': catatan_lama, 'Pallet Ke': 999.0
        })
        
    if final_dus > 0:
        records_baru.append({
            'Kolom': k_pilih, 'Baris': float(b_pilih),
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': f"Pecahan: {final_dus} {satuan_kemasan}", 'Catatan': catatan_lama, 'Pallet Ke': 999.0
        })
        
    updated_rak_ini = sisa_rak_ini + records_baru
    updated_rak_ini = sorted(updated_rak_ini, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
    for i, d in enumerate(updated_rak_ini, 1):
        d['Pallet Ke'] = float(i)
        
    final_data = rak_lain + updated_rak_ini
    transaction.update(doc_ref, {'data': final_data}) 
    return data_lama_sebelum_update
