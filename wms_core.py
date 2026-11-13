import streamlit as st
import pandas as pd
import math
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
# 4. DATA LOADERS & ALGORITMA 2 PINTU SHUTTLE (DEPAN=FIFO, BELAKANG=LIFO)
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
    Mengevaluasi akses 2 pintu shuttle racking:
    - Sisi Depan (Pallet Ke terkecil) = FIFO
    - Sisi Belakang (Pallet Ke terbesar) = LIFO
    Urutan prioritas:
    1. Bebas diakses dari Sisi Depan (0 penghalang depan) -> urut Lot terlama
    2. Bebas diakses dari Sisi Belakang (0 penghalang belakang) -> urut Lot terlama
    3. Terjepit di tengah -> urut jumlah langsir paling sedikit (antara sisi Depan/Belakang)
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
        
        min_pos_target = df_group['Pallet_Ke_Num'].min()
        max_pos_target = df_group['Pallet_Ke_Num'].max()
        df_aisle = df_all[(df_all['Kolom'] == kol) & (df_all['Baris_Int'] == bar)]
        
        is_other = ~((df_aisle['Nama Produk'] == target_item) & (df_aisle['Lot Number'].astype(str) == lot))
        df_penghalang_depan = df_aisle[is_other & (df_aisle['Pallet_Ke_Num'] < min_pos_target)]
        df_penghalang_belakang = df_aisle[is_other & (df_aisle['Pallet_Ke_Num'] > max_pos_target)]
        
        jml_depan = len(df_penghalang_depan)
        jml_belakang = len(df_penghalang_belakang)
        
        if jml_depan == 0:
            akses_rank = 0
            min_penghalang = 0
            sisi_akses = "DEPAN"
            info_penghalang = ""
        elif jml_belakang == 0:
            akses_rank = 1
            min_penghalang = 0
            sisi_akses = "BELAKANG"
            info_penghalang = ""
        else:
            akses_rank = 2
            if jml_depan <= jml_belakang:
                min_penghalang = jml_depan
                sisi_akses = "TERHALANG_DEPAN"
                df_blok = df_penghalang_depan
            else:
                min_penghalang = jml_belakang
                sisi_akses = "TERHALANG_BELAKANG"
                df_blok = df_penghalang_belakang
                
            item_blok = df_blok['Nama Produk'].dropna().unique().tolist()
            if len(item_blok) == 1 and item_blok[0] == target_item:
                lot_blok = df_blok['Lot Number'].dropna().unique().tolist()
                info_penghalang = f"Lot {', '.join(lot_blok[:2])}"
            else:
                nama_blok = [x.split(',')[-1].strip() if ',' in x else x[:16] for x in item_blok[:2]]
                info_penghalang = f"{', '.join(nama_blok)}"
                
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
            'AksesRank': akses_rank,
            'MinPenghalang': min_penghalang,
            'SisiAkses': sisi_akses,
            'InfoPenghalang': info_penghalang
        })
        
    df_rekap = pd.DataFrame(rekap_lokasi).sort_values(
        by=['AksesRank', 'MinPenghalang', 'FIFO_Key'],
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
            with st.container(border=True):
                st.success(f"✅ **BERHASIL ({mode_undo}):** {undo_rec.get('action_msg', '')}")
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
# 6. TRANSAKSI ATOMIK FIREBASE (2 PINTU SHUTTLE & VALIDASI)
# ==========================================================
@firestore.transactional
def proses_inbound_multi_transaction(transaction, doc_refs_dict, alokasi_list, lot_pilih, part_num, item_name, tipe_pecahan, catatan, sisi_masuk="BELAKANG"):
    """
    Mengeksekusi penempatan inbound ke 1 atau beberapa rak sekaligus:
    - sisi_masuk == 'DEPAN'    -> Palet baru ditempatkan di urutan paling depan (1..N) untuk re-inbound FIFO.
    - sisi_masuk == 'BELAKANG' -> Palet baru ditempatkan di urutan paling belakang setelah stok eksisting.
    """
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
            
        rak_ini = sorted(rak_ini, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
        
        pallets_baru = []
        for i in range(qty_p):
            pallets_baru.append({
                'Kolom': k_pilih, 'Baris': b_pilih,
                'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
                'Keterangan': 'Utuh', 'Catatan': catatan,
                'Pallet Ke': float(i + 1)
            })
            
        if qty_c > 0:
            pallets_baru.append({
                'Kolom': k_pilih, 'Baris': b_pilih,
                'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
                'Keterangan': f"Pecahan: {qty_c} {tipe_pecahan}", 'Catatan': catatan,
                'Pallet Ke': float(qty_p + 1)
            })
            
        if sisi_masuk == "DEPAN":
            gabungan_rak = pallets_baru + rak_ini
        else:
            gabungan_rak = rak_ini + pallets_baru
            
        for idx_p, d in enumerate(gabungan_rak, 1):
            d['Pallet Ke'] = float(idx_p)
            
        data_per_kolom[k_pilih] = rak_lain + gabungan_rak
        
    for kol, ref in doc_refs_dict.items():
        if snapshots[kol].exists:
            transaction.update(ref, {'data': data_per_kolom[kol]})
        else:
            transaction.set(ref, {'data': data_per_kolom[kol]})
            
    return backup_kolom

@firestore.transactional
def proses_pengambilan_stok(transaction, doc_ref, k_pilih, b_pilih, lot_pilih, part_num, item_name, init_pallet, init_dus, final_pallet, final_dus, satuan_kemasan, catatan_lama):
    """Transaksi terpadu Initial vs Final dengan mempertahankan posisi relatif palet sisa di lorong"""
    snapshot = doc_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise ValueError("Data Kolom tidak ditemukan di database.")
        
    all_col_data = snapshot.to_dict().get('data', [])
    data_lama_sebelum_update = [d.copy() for d in all_col_data]
    
    rak_ini = sorted(
        [d for d in all_col_data if float(d.get('Baris', 0) or 0) == float(b_pilih)],
        key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))
    )
    rak_lain = [d for d in all_col_data if float(d.get('Baris', 0) or 0) != float(b_pilih)]
    
    lot_records = [d for d in rak_ini if str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name]
    db_utuh = sum(1 for d in lot_records if 'pecahan' not in str(d.get('Keterangan', '')).lower() and 'eceran' not in str(d.get('Keterangan', '')).lower())
    db_dus = sum(parse_keterangan(d.get('Keterangan', ''))[0] for d in lot_records if 'pecahan' in str(d.get('Keterangan', '')).lower() or 'eceran' in str(d.get('Keterangan', '')).lower())
    
    if db_utuh != init_pallet or db_dus != init_dus:
        raise ValueError(f"Stok di database baru saja berubah ({db_utuh} Pallet, {db_dus} {satuan_kemasan}). Silakan tekan 'Sinkronkan Stok'.")
    
    # Pertahankan posisi awal target di dalam lorong agar palet sisa tidak bergeser ke belakang
    first_target_idx = next(
        (idx for idx, d in enumerate(rak_ini) if str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name),
        len(rak_ini)
    )
    
    records_baru = []
    for _ in range(final_pallet):
        records_baru.append({
            'Kolom': k_pilih, 'Baris': float(b_pilih),
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': 'Utuh', 'Catatan': catatan_lama, 'Pallet Ke': 0.0
        })
        
    if final_dus > 0:
        records_baru.append({
            'Kolom': k_pilih, 'Baris': float(b_pilih),
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': f"Pecahan: {final_dus} {satuan_kemasan}", 'Catatan': catatan_lama, 'Pallet Ke': 0.0
        })
        
    updated_rak_ini = []
    inserted = False
    for idx, d in enumerate(rak_ini):
        if idx == first_target_idx and not inserted:
            updated_rak_ini.extend(records_baru)
            inserted = True
        if not (str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name):
            updated_rak_ini.append(d)
    if not inserted:
        updated_rak_ini.extend(records_baru)
        
    for i, d in enumerate(updated_rak_ini, 1):
        d['Pallet Ke'] = float(i)
        
    final_data = rak_lain + updated_rak_ini
    transaction.update(doc_ref, {'data': final_data}) 
    return data_lama_sebelum_update

# ==========================================================
# 7. MODUL UI INBOUND TERPADU (UNTUK LTIn & LTOut)
# ==========================================================
def render_alur_inbound_terpadu(prefix_app, judul_inbound, default_sisi_masuk="BELAKANG", jenis_log="INBOUND (Remote)", judul_tele="INBOUND - RACK NAVIGATOR", mode_undo_label="INBOUND", reset_keys=None):
    """
    Menjalankan alur 3 langkah Inbound (Slide 5: Input -> Slide 6: Rekomendasi Rak -> Slide 7: Konfirmasi)
    sehingga bisa dipakai bersama oleh LTIn (Inbound) maupun LTOut (Hasil Bongkar / Inbound Sisa).
    """
    # --- SLIDE 5: INPUT LOT, QTY & SISI PINTU MASUK ---
    if st.session_state.slide == 5:
        if st.button("🔙 GANTI AKTIVITAS"):
            st.session_state.slide = 4
            st.rerun()
            
        st.markdown(f"### {judul_inbound}")
        st.info(format_nama_warna_bold(st.session_state.item))
        
        satuan_otomatis = get_satuan_kemasan(st.session_state.pilih_part)
        opsi_sisi = ["DEPAN (FIFO - Siap Kirim Duluan)", "BELAKANG (LIFO - Barang Baru Masuk)"]
        idx_default_sisi = 0 if st.session_state.get("in_sisi_masuk", default_sisi_masuk) == "DEPAN" else 1
        
        with st.container(border=True):
            in_lot = st.text_input("Nomor Lot", value=st.session_state.get("in_lot") or "", placeholder="Cth: 3D3121126")
            c_q1, c_q2 = st.columns(2)
            with c_q1:
                in_qty_p = st.number_input("Pallet Utuh", min_value=0, value=st.session_state.get("in_qty_p") if st.session_state.get("in_qty_p") is not None else 1)
            with c_q2:
                in_qty_c = st.number_input(f"Qty Pecahan ({satuan_otomatis})", min_value=0, value=st.session_state.get("in_qty_c") if st.session_state.get("in_qty_c") is not None else 0)
            
            sisi_pilihan = st.radio(
                "🚪 Pintu Masuk Shuttle Racking:",
                options=opsi_sisi,
                index=idx_default_sisi,
                horizontal=True
            )
                
        st.write("")
        if st.button("🔍 CARI LOKASI RAK (KONSOLIDASI)", type="primary", use_container_width=True):
            if not in_lot.strip():
                st.error("⚠️ Nomor Lot wajib diisi!")
            elif in_qty_p == 0 and in_qty_c == 0:
                st.error("⚠️ Masukkan minimal 1 kuantitas!")
            else:
                st.session_state.in_lot = in_lot.strip()
                st.session_state.in_qty_p = in_qty_p
                st.session_state.in_qty_c = in_qty_c
                st.session_state.in_tipe_c = satuan_otomatis
                st.session_state.in_sisi_masuk = "DEPAN" if sisi_pilihan.startswith("DEPAN") else "BELAKANG"
                st.session_state.slide = 6
                st.rerun()

    # --- SLIDE 6: REKOMENDASI LOKASI RAK ---
    elif st.session_state.slide == 6:
        if st.button("🔙 UBAH INPUT"):
            st.session_state.slide = 5
            st.rerun()
            
        st.markdown(f"### Rekomendasi Lokasi Rak ({st.session_state.in_sisi_masuk})")
        
        qty_p_in = int(st.session_state.in_qty_p or 0)
        qty_c_in = int(st.session_state.in_qty_c or 0)
        total_slot_butuh = qty_p_in + (1 if qty_c_in > 0 else 0)
        
        with st.spinner("📐 Menghitung kapasitas & prioritas konsolidasi..."):
            semua_data = get_all_racking_data()
            KOLOM_LIST = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
            BARIS_LIST = list(range(1, 29))
            slot_map = {f"{k}-{b}": [] for k in KOLOM_LIST for b in BARIS_LIST}
            
            for d in semua_data:
                k = str(d.get('Kolom', '')).strip().upper()
                try: b = int(float(d.get('Baris', 0)))
                except Exception: continue
                if f"{k}-{b}" in slot_map: slot_map[f"{k}-{b}"].append(d)
                    
            in_is_sac = str(st.session_state.pilih_part).strip().upper().endswith('C')
            target_item = str(st.session_state.item).strip()
            
            kandidat_rak = []
            for loc, pallets in slot_map.items():
                k, b = loc.split('-')
                b = int(b)
                max_sac = 31 if b <= 16 else 44
                max_dus = 32 if b <= 16 else 46
                max_target = max_sac if in_is_sac else max_dus
                
                used_cap = 0.0
                distinct_items = set()
                distinct_lots = set()
                has_sac = False
                
                for p in pallets:
                    p_name = str(p.get('Nama Produk', '')).strip()
                    p_lot = str(p.get('Lot Number', '')).strip()
                    if p_name: distinct_items.add(p_name)
                    if p_lot: distinct_lots.add(p_lot)
                    p_part = str(p.get('Part Number', '')).strip().upper()
                    if p_part.endswith('C'):
                        has_sac = True
                        used_cap += 1.0 / max_sac
                    else:
                        used_cap += 1.0 / max_dus
                    
                sisa_cap = max(0.0, 1.0001 - used_cap)
                sisa_slot_pallet = int(math.floor(sisa_cap * max_target))
                
                if (in_is_sac or has_sac) and sisa_slot_pallet <= 1:
                    continue
                if sisa_slot_pallet <= 0:
                    continue
                    
                if len(distinct_items) == 1 and target_item in distinct_items:
                    prio, status = 1, "🟢 KONSOLIDASI (SEJENIS)"
                    isi_eksisting = f"{len(pallets)} Pallet sejenis"
                    sub_sort = -used_cap
                elif len(distinct_items) == 0:
                    prio, status = 2, "⚪ BARIS KOSONG"
                    isi_eksisting = "Kosong"
                    sub_sort = 0.0
                elif len(distinct_items) == 1 and target_item not in distinct_items:
                    prio, status = 3, "🟡 CAMPUR (MAX 2 ITEM)"
                    item_lain = list(distinct_items)[0]
                    lot_str = ", ".join(sorted(list(distinct_lots))[:2])
                    nama_ringkas = f"{item_lain[:18]}..." if len(item_lain) > 18 else item_lain
                    isi_eksisting = f"{len(pallets)}P {nama_ringkas} (Lot: {lot_str})"
                    sub_sort = used_cap
                else:
                    continue
                    
                kandidat_rak.append({
                    'Loc': loc, 'Kolom': k, 'Baris': b, 'Priority': prio, 'Status': status,
                    'IsiEksisting': isi_eksisting, 'UsedCap': used_cap, 'SubSort': sub_sort,
                    'MaxTarget': max_target, 'SisaSlot': sisa_slot_pallet
                })
                
            if not kandidat_rak:
                st.error("❌ Seluruh rak penuh atau sudah berisi 2 jenis item berbeda!")
            else:
                df_kandidat = pd.DataFrame(kandidat_rak).sort_values(
                    by=['Priority', 'SubSort', 'Kolom', 'Baris'], ascending=[True, True, True, True]
                ).reset_index(drop=True)
                
                if in_is_sac:
                    st.caption("🛡️ *Filter aktif: Rak Sak dengan sisa ≤ 1 slot otomatis dilewati karena toleransi panjang fisik.*")
                    
                rak_skip = st.multiselect(
                    "🚫 Lewati Rak (Jika di lapangan sudah penuh / terhalang):",
                    options=df_kandidat['Loc'].tolist(),
                    placeholder="Pilih rak untuk dilewati (opsional)..."
                )
                if rak_skip:
                    df_kandidat = df_kandidat[~df_kandidat['Loc'].isin(rak_skip)].reset_index(drop=True)
                    
                df_single_muat = df_kandidat[df_kandidat['SisaSlot'] >= total_slot_butuh].reset_index(drop=True)
                
                if not df_single_muat.empty:
                    st.caption(f"Kebutuhan ruang: **{total_slot_butuh} slot pallet** (Masuk dari **Sisi {st.session_state.in_sisi_masuk}**)")
                    for idx, row in df_single_muat.head(8).iterrows():
                        prio_label = "🌟 REKOMENDASI TERBAIK" if idx == 0 else ""
                        persentase_terisi = int(row['UsedCap'] * 100)
                        est_akhir = int(min(100, (row['UsedCap'] + (total_slot_butuh / row['MaxTarget'])) * 100))
                        
                        btn_label = f"📍 RAK {row['Kolom']}-{row['Baris']}  |  {row['Status']}\n"
                        btn_label += f"📊 Sisa Muat: {row['SisaSlot']} Pallet  |  Terisi: {persentase_terisi}% ➔ {est_akhir}%\n"
                        btn_label += f"📦 Kondisi: {row['IsiEksisting']} {(' | ' + prio_label) if prio_label else ''}"
                        
                        if st.button(btn_label, use_container_width=True, key=f"btn_single_{prefix_app}_{idx}"):
                            st.session_state.alokasi_terpilih = [{
                                'Kolom': row['Kolom'], 'Baris': int(row['Baris']),
                                'qty_p': qty_p_in, 'qty_c': qty_c_in, 'Status': row['Status']
                            }]
                            st.session_state.slide = 7
                            st.rerun()
                else:
                    total_kapasitas_gudang = int(df_kandidat['SisaSlot'].sum()) if not df_kandidat.empty else 0
                    if total_kapasitas_gudang < total_slot_butuh:
                        st.error(f"❌ Total sisa kapasitas gudang ({total_kapasitas_gudang} palet) tidak mencukupi untuk menampung {total_slot_butuh} palet!")
                    else:
                        st.info(f"🔀 **Alokasi Multi-Rak Otomatis:** Kebutuhan **{total_slot_butuh} slot pallet** dibagi berurutan sesuai prioritas:")
                        rencana_alokasi = []
                        sisa_utuh, sisa_pecahan = qty_p_in, qty_c_in
                        
                        for _, row in df_kandidat.iterrows():
                            if sisa_utuh == 0 and sisa_pecahan == 0: break
                            kap = int(row['SisaSlot'])
                            ambil_p = min(sisa_utuh, kap)
                            sisa_utuh -= ambil_p
                            kap_sisa = kap - ambil_p
                            
                            ambil_c = 0
                            if sisa_utuh == 0 and sisa_pecahan > 0 and kap_sisa >= 1:
                                ambil_c = sisa_pecahan
                                sisa_pecahan = 0
                                
                            if ambil_p > 0 or ambil_c > 0:
                                rencana_alokasi.append({
                                    'Kolom': row['Kolom'], 'Baris': int(row['Baris']),
                                    'qty_p': ambil_p, 'qty_c': ambil_c,
                                    'Status': row['Status'], 'SisaSlot': int(row['SisaSlot'])
                                })
                                
                        with st.container(border=True):
                            for i_alok, al in enumerate(rencana_alokasi, 1):
                                teks_pec = f" + {al['qty_c']} {st.session_state.in_tipe_c}" if al['qty_c'] > 0 else ""
                                st.markdown(f"**{i_alok}. Rak {al['Kolom']}-{al['Baris']}** ({al['Status']}) ➔ Masuk **{al['qty_p']} Pallet{teks_pec}** *(dari sisa {al['SisaSlot']} slot)*")
                                
                        st.write("")
                        if st.button("✅ GUNAKAN RENCANA PENEMPATAN INI", type="primary", use_container_width=True):
                            st.session_state.alokasi_terpilih = rencana_alokasi
                            st.session_state.slide = 7
                            st.rerun()

    # --- SLIDE 7: KONFIRMASI & EKSEKUSI INBOUND ---
    elif st.session_state.slide == 7:
        if st.button("🔙 BATAL (GANTI LOKASI)"):
            st.session_state.slide = 6
            st.rerun()
            
        alokasi = st.session_state.alokasi_terpilih or []
        sisi_masuk = st.session_state.get("in_sisi_masuk", default_sisi_masuk)
        
        st.markdown(f"### Konfirmasi {judul_inbound}")
        with st.container(border=True):
            st.markdown(format_nama_warna_bold(st.session_state.item))
            st.markdown(f"**Lot Masuk:** `{st.session_state.in_lot}`  |  **Pintu Masuk:** Sisi **{sisi_masuk}**")
            teks_pecahan = f", {st.session_state.in_qty_c} {st.session_state.in_tipe_c}" if st.session_state.in_qty_c > 0 else ""
            st.caption(f"Total Masuk: {st.session_state.in_qty_p} Pallet Utuh{teks_pecahan}")
            st.divider()
            st.markdown("**Rincian Rak Tujuan:**")
            for al in alokasi:
                p_str = f" + {al['qty_c']} {st.session_state.in_tipe_c}" if al['qty_c'] > 0 else ""
                st.markdown(f"- 📍 **Rak {al['Kolom']}-{al['Baris']}**: {al['qty_p']} Pallet{p_str} *({al['Status']})*")
            
        st.write("")
        catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Sisa bongkar muat / Retur muatan")
        st.write("")
        if st.button("📥 MASUKKAN KE DATABASE SEKARANG", type="primary", use_container_width=True):
            with st.spinner("Memasukkan data ke rak secara aman..."):
                kolom_unik = sorted(list(set(al['Kolom'] for al in alokasi)))
                doc_refs_dict = {kol: db.collection(NAMA_DATABASE).document(f"Kolom_{kol}") for kol in kolom_unik}
                transaction = db.transaction()
                
                try:
                    backup_kolom = proses_inbound_multi_transaction(
                        transaction, doc_refs_dict, alokasi,
                        st.session_state.in_lot, st.session_state.pilih_part, st.session_state.item,
                        st.session_state.in_tipe_c, catatan_operator, sisi_masuk=sisi_masuk
                    )
                    
                    teks_keterangan = f"Op: {st.session_state.operator} | Sisi: {sisi_masuk}"
                    if catatan_operator.strip(): teks_keterangan += f" | Catatan: {catatan_operator.strip()}"
                    
                    ringkasan_rak = []
                    for al in alokasi:
                        catat_log_mutasi(
                            jenis_log, al['Kolom'], al['Baris'],
                            st.session_state.item, st.session_state.pilih_part, st.session_state.in_lot,
                            al['qty_p'], al['qty_c'], 0, teks_keterangan
                        )
                        p_str = f"+{al['qty_c']}{st.session_state.in_tipe_c}" if al['qty_c'] > 0 else ""
                        ringkasan_rak.append(f"{al['Kolom']}-{al['Baris']} ({al['qty_p']}P{p_str})")
                    
                    lokasi_gabungan = ", ".join(ringkasan_rak)
                    pecahan_tele = f", {st.session_state.in_qty_c} {st.session_state.in_tipe_c}" if st.session_state.in_qty_c > 0 else ""
                    
                    pesan_tele = (
                        f"📥 <b>{judul_tele}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📦 <b>Item:</b> {escape_html(st.session_state.item)}\n"
                        f"🏷️ <b>Lot:</b> <code>{escape_html(st.session_state.in_lot)}</code>\n"
                        f"📊 <b>Total:</b> {st.session_state.in_qty_p} Pallet{pecahan_tele}\n"
                        f"📍 <b>Penempatan:</b> {escape_html(lokasi_gabungan)} (Sisi {sisi_masuk})\n"
                        f"👤 <b>Operator:</b> {escape_html(st.session_state.operator)}\n"
                    )
                    if catatan_operator.strip():
                        pesan_tele += f"📝 <b>Catatan:</b> <i>{escape_html(catatan_operator.strip())}</i>\n"
                    kirim_notif_telegram(pesan_tele)
                    
                    get_all_racking_data.clear()
                    msg_sukses = f"{st.session_state.in_qty_p} Pallet{pecahan_tele} masuk ke {lokasi_gabungan} (Sisi {sisi_masuk})."
                    simpan_undo_firestore(prefix_app, st.session_state.operator, backup_kolom, msg_sukses, mode_label=mode_undo_label)
                    
                    st.toast(msg_sukses, icon="✅")
                    keys_clear = ['kategori', 'gramasi', 'item', 'pilih_part', 'mode_transaksi', 'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'in_sisi_masuk', 'alokasi_terpilih']
                    if reset_keys:
                        keys_clear.extend(reset_keys)
                    for k in set(keys_clear):
                        if k in st.session_state:
                            st.session_state[k] = None
                    st.session_state.slide = 1
                    st.rerun()
                    
                except ValueError as err:
                    st.error(f"❌ Validasi Kapasitas: {err}")
                except Exception as err:
                    st.error(f"❌ Kesalahan Sistem: {err}")
