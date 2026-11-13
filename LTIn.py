import streamlit as st
import pandas as pd
import math
import re
import requests
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================================
# 1. KONFIGURASI TAMPILAN & CSS ADAPTIF (INBOUND & WIP)
# ==========================================================
st.set_page_config(page_title="Inbound & WIP - Rack Navigator", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        
        .stButton>button {
            min-height: 72px; 
            height: auto;
            font-size: 17px; 
            font-weight: 600;
            border-radius: 12px;
            white-space: pre-wrap; 
            word-wrap: break-word;
            text-align: left; 
            padding: 15px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            transition: all 0.15s ease-in-out;
        }
        
        button[kind="primary"] {
            background-color: #10B981 !important;
            border-color: #059669 !important;
            color: white !important;
            font-size: 18px !important;
            text-align: center !important;
        }
        
        .btn-undo > button {
            background-color: #DC2626 !important;
            border-color: #EF4444 !important;
            color: white !important;
            text-align: center !important;
        }

        .btn-center > button {
            text-align: center !important;
            font-size: 19px !important;
        }
        
        .progress-container {
            font-size: 13px;
            font-weight: 500;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border-color);
            text-align: center;
        }
        .step-active { color: #10B981; font-weight: bold; background: rgba(16, 185, 129, 0.1); padding: 4px 8px; border-radius: 6px; }
        .step-inactive { color: var(--text-color); opacity: 0.6; }
        
        .db-info-box {
            background-color: var(--secondary-background-color);
            border: 1px solid var(--border-color);
            padding: 12px 16px;
            border-radius: 10px;
            margin-bottom: 15px;
        }
        
        .btn-gramasi > button { font-size: 20px !important; text-align: center !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================================
# 2. INISIALISASI FIREBASE, TELEGRAM & HELPER
# ==========================================================
NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'
DB_UNDO_STATE = 'wms_undo_state'
OPERATOR_INBOUND = ["Fahmi", "Adip", "Didik", "Agung"]

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

def get_waktu_jkt():
    return datetime.utcnow() + timedelta(hours=7)

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

# ==========================================================
# 3. PERSISTENT UNDO 30 MENIT (HEMAT KUOTA READ)
# ==========================================================
def simpan_undo_firestore(operator_name, undo_dict, action_msg, mode_label="INBOUND"):
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
        db.collection(DB_UNDO_STATE).document(f"IN_{operator_name}").set(payload)
    except Exception:
        pass

def muat_undo_firestore(operator_name):
    try:
        doc = db.collection(DB_UNDO_STATE).document(f"IN_{operator_name}").get()
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

def tutup_undo_firestore(operator_name, new_status="CLOSED"):
    st.session_state.undo_record = {"status": new_status} if new_status == "UNDONE" else None
    try:
        db.collection(DB_UNDO_STATE).document(f"IN_{operator_name}").update({"status": new_status})
    except Exception:
        pass

# ==========================================================
# 4. FIREBASE TRANSACTIONS & CACHE (INBOUND & AMBIL WIP)
# ==========================================================
@st.cache_data(ttl=300)
def get_all_racking_data():
    docs = db.collection(NAMA_DATABASE).stream()
    semua_data = []
    for doc in docs:
        doc_data = doc.to_dict()
        if 'data' in doc_data: semua_data.extend(doc_data['data'])
    return semua_data

@firestore.transactional
def proses_inbound_multi_transaction(transaction, doc_refs_dict, alokasi_list, lot_pilih, part_num, item_name, tipe_pecahan, catatan):
    snapshots = {kol: ref.get(transaction=transaction) for kol, ref in doc_refs_dict.items()}
    backup_kolom = {}
    data_per_kolom = {}
    
    for kol, snap in snapshots.items():
        existing = snap.to_dict().get('data', []) if snap.exists else []
        backup_kolom[kol] = [d.copy() for d in existing]
        data_per_kolom[kol] = [d.copy() for d in existing]
        
    for item_alok in alokasi_list:
        k_pilih = item_alok['Kolom']
        b_pilih = float(item_alok['Baris'])
        qty_p = int(item_alok['qty_p'])
        qty_c = int(item_alok['qty_c'])
        
        all_col = data_per_kolom[k_pilih]
        rak_ini = [d for d in all_col if float(d.get('Baris', 0) or 0) == b_pilih]
        rak_lain = [d for d in all_col if float(d.get('Baris', 0) or 0) != b_pilih]
        
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
def proses_wip_initial_final(transaction, doc_ref, k_pilih, b_pilih, lot_pilih, part_num, item_name, init_pallet, init_dus, final_pallet, final_dus, satuan_kemasan, catatan_lama):
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
        raise ValueError(f"Stok di database baru saja berubah ({db_utuh} Pallet, {db_dus} {satuan_kemasan}). Silakan muat ulang lokasi.")
    
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

# ==========================================================
# 5. MASTER PRODUK & KONVERSI UNIT
# ==========================================================
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
            except:
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

df_master = load_master_data()

keys_state = [
    'slide', 'kategori', 'gramasi', 'item', 'pilih_part', 'mode_transaksi',
    'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'alokasi_terpilih',
    'wip_kolom', 'wip_baris', 'wip_lot', 'wip_catatan_rak', 'wip_max_pallet', 'wip_max_dus',
    'operator', 'undo_record'
]
for key in keys_state:
    if key not in st.session_state:
        if key == 'slide': st.session_state[key] = 0
        else: st.session_state[key] = None

def pindah_slide(tujuan):
    st.session_state.slide = tujuan

# ==========================================================
# SLIDE 0: PILIH OPERATOR BERTUGAS
# ==========================================================
if st.session_state.slide == 0:
    st.markdown("""
        <div class="db-info-box" style="text-align: center;">
            <div style="font-size: 18px; font-weight: bold; color: #10B981;">
                🧭 Inbound & WIP - Rack Navigator
            </div>
            <div style="font-size: 13px; opacity: 0.8; margin-top: 4px;">
                Pilih Operator Forklift yang Bertugas
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="btn-center">', unsafe_allow_html=True)
    for i in range(0, len(OPERATOR_INBOUND), 2):
        cols = st.columns(2)
        with cols[0]:
            op1 = OPERATOR_INBOUND[i]
            if st.button(f"👤 {op1}", key=f"op_{op1}", use_container_width=True):
                st.session_state.operator = op1
                muat_undo_firestore(op1)
                pindah_slide(1); st.rerun()
        if i + 1 < len(OPERATOR_INBOUND):
            with cols[1]:
                op2 = OPERATOR_INBOUND[i+1]
                if st.button(f"👤 {op2}", key=f"op_{op2}", use_container_width=True):
                    st.session_state.operator = op2
                    muat_undo_firestore(op2)
                    pindah_slide(1); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.write("")
    with st.expander("➕ Operator Pengganti / Lainnya"):
        op_manual = st.text_input("Ketik Nama Operator", placeholder="Masukkan nama...")
        if st.button("MASUK DENGAN NAMA INI", type="primary", use_container_width=True):
            if op_manual.strip():
                nama_fix = op_manual.strip().title()
                st.session_state.operator = nama_fix
                muat_undo_firestore(nama_fix)
                pindah_slide(1); st.rerun()
            else:
                st.warning("Nama operator tidak boleh kosong.")
    st.stop()

# ==========================================================
# HEADER STATUS & NOTIFIKASI UNDO (BERLAKU 30 MENIT)
# ==========================================================
col_hdr1, col_hdr2 = st.columns([3, 1])
with col_hdr1:
    mode_badge = ""
    if st.session_state.mode_transaksi == "INBOUND":
        mode_badge = "  |  <span style='color:#10B981;font-weight:bold;'>📥 INBOUND</span>"
    elif st.session_state.mode_transaksi == "WIP":
        mode_badge = "  |  <span style='color:#F59E0B;font-weight:bold;'>🏭 AMBIL WIP</span>"
        
    st.markdown(f"""
        <div class="db-info-box" style="margin-bottom: 8px;">
            <div style="font-size: 14px; font-weight: bold; color: #10B981;">
                🧭 Inbound & WIP - Rack Navigator
            </div>
            <div style="font-size: 12px; opacity: 0.85; margin-top: 2px;">
                👤 <strong>{st.session_state.operator}</strong>  |  📡 {NAMA_DATABASE}{mode_badge}
            </div>
        </div>
    """, unsafe_allow_html=True)
with col_hdr2:
    if st.button("🔄 Ganti\nOperator", use_container_width=True):
        st.session_state.operator = None
        st.session_state.undo_record = None
        st.session_state.mode_transaksi = None
        pindah_slide(0); st.rerun()

undo_rec = st.session_state.undo_record
if undo_rec and undo_rec.get("status") == "DONE":
    masih_berlaku = True
    if undo_rec.get("created_at"):
        created_dt = datetime.strptime(undo_rec["created_at"], "%Y-%m-%d %H:%M:%S")
        if (get_waktu_jkt() - created_dt) > timedelta(minutes=30):
            masih_berlaku = False
            st.session_state.undo_record = None
            
    if masih_berlaku:
        mode_undo = undo_rec.get("mode", "INBOUND")
        judul_sukses = "BERHASIL MASUK:" if mode_undo == "INBOUND" else "BERHASIL AMBIL WIP:"
        with st.container(border=True):
            st.success(f"✅ **{judul_sukses}** {undo_rec.get('action_msg', '')}")
            st.caption(f"⏱️ Opsi Undo tersedia selama 30 menit (Input pukul {undo_rec.get('created_at', '')[-8:]} WIB)")
            c_undo1, c_undo2 = st.columns(2)
            with c_undo1:
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
                        tutup_undo_firestore(st.session_state.operator, "UNDONE")
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
            with c_undo2:
                if st.button("TUTUP / LANJUT", use_container_width=True):
                    tutup_undo_firestore(st.session_state.operator, "CLOSED"); st.rerun()

elif undo_rec and undo_rec.get("status") == "UNDONE":
    with st.container(border=True):
        st.warning("⏪ **Transaksi dibatalkan.** Stok kembali ke posisi semula.")
        if st.button("TUTUP PESAN", use_container_width=True):
            st.session_state.undo_record = None; st.rerun()

def render_progress():
    current = st.session_state.slide
    mode = st.session_state.mode_transaksi
    
    if mode == "WIP":
        steps = ["Kategori", "Item", "Aksi", "Lokasi WIP", "Selesai"]
        idx_map = {1:1, 2:1, 3:2, 4:3, 5:4, 6:5}
    elif mode == "INBOUND":
        steps = ["Kategori", "Item", "Aksi", "Input", "Lokasi", "Selesai"]
        idx_map = {1:1, 2:1, 3:2, 4:3, 5:4, 6:5, 7:6}
    else:
        steps = ["Kategori", "Item", "Aksi", "Proses", "Selesai"]
        idx_map = {1:1, 2:1, 3:2, 4:3, 5:4, 6:5, 7:5}
        
    curr_step = idx_map.get(current, 1)
    html = "<div class='progress-container'>"
    for i, s in enumerate(steps, 1):
        if i == curr_step: html += f"<span class='step-active'>{s}</span>"
        else: html += f"<span class='step-inactive'>{s}</span>"
        if i < len(steps): html += " <span style='opacity: 0.5;'>➔</span> "
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

render_progress()

# ==========================================================
# SLIDE 1: KATEGORI
# ==========================================================
if st.session_state.slide == 1:
    st.session_state.mode_transaksi = None
    st.markdown("### Pilih Kategori Produk")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("PREFORM", use_container_width=True): st.session_state.kategori = "PREFORM"; pindah_slide(2); st.rerun()
        if st.button("CUP COLOR", use_container_width=True): st.session_state.kategori = "CUP COLOR"; pindah_slide(2); st.rerun()
        if st.button("CAP (TUTUP)", use_container_width=True): st.session_state.kategori = "CAP"; pindah_slide(2); st.rerun()
    with col2:
        if st.button("CUP NATURAL", use_container_width=True): st.session_state.kategori = "CUP NATURAL"; pindah_slide(2); st.rerun()
        if st.button("CUP PRINTING", use_container_width=True): st.session_state.kategori = "CUP PRINTING"; pindah_slide(2); st.rerun()

# ==========================================================
# SLIDE 2: GRAMASI
# ==========================================================
elif st.session_state.slide == 2:
    if st.button("🔙 KEMBALI"): pindah_slide(1); st.rerun()
    st.markdown(f"### Gramasi: **{st.session_state.kategori}**")
    df_kat = df_master[df_master['Kategori'] == st.session_state.kategori]
    list_gramasi = sorted(df_kat['Gramasi'].unique())
    
    st.markdown('<div class="btn-gramasi">', unsafe_allow_html=True)
    for i in range(0, len(list_gramasi), 2):
        cols = st.columns(2)
        with cols[0]:
            if st.button(f"{list_gramasi[i]}", key=f"g_{i}", use_container_width=True): st.session_state.gramasi = list_gramasi[i]; pindah_slide(3); st.rerun()
        if i + 1 < len(list_gramasi):
            with cols[1]:
                if st.button(f"{list_gramasi[i+1]}", key=f"g_{i+1}", use_container_width=True): st.session_state.gramasi = list_gramasi[i+1]; pindah_slide(3); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# ==========================================================
# SLIDE 3: ITEM SPESIFIK
# ==========================================================
elif st.session_state.slide == 3:
    if st.button("🔙 KEMBALI"): pindah_slide(2); st.rerun()
    st.markdown(f"### Pilih Item Produk")
    st.caption(f"{st.session_state.kategori} | {st.session_state.gramasi}")
    
    df_item = df_master[(df_master['Kategori'] == st.session_state.kategori) & (df_master['Gramasi'] == st.session_state.gramasi)]
    
    for idx, row in df_item.iterrows():
        nama_brg = row['Nama Barang']
        part_num = str(row['Part Number']).strip().upper()
        
        tipe_kemasan = ""
        if part_num:
            if part_num[-1] in ['D', 'G']: tipe_kemasan = "[📦 DUS] "
            elif part_num[-1] == 'C': tipe_kemasan = "[👝 SAK] "
            elif part_num[-1] == 'A': tipe_kemasan = "[🗃️ NICTAINER] "
                
        nama_terformat = format_nama_warna_bold(nama_brg)
        btn_label = f"{tipe_kemasan}{nama_terformat}"
        
        if st.button(btn_label, use_container_width=True, key=f"btn_item_{idx}"): 
            st.session_state.item = nama_brg
            st.session_state.pilih_part = part_num
            pindah_slide(4); st.rerun()

# ==========================================================
# SLIDE 4: PILIH TRANSAKSI (INBOUND VS AMBIL WIP)
# ==========================================================
elif st.session_state.slide == 4:
    if st.button("🔙 KEMBALI KE ITEM"):
        st.session_state.mode_transaksi = None
        pindah_slide(3); st.rerun()
        
    st.markdown("### Pilih Jenis Aktivitas")
    st.info(format_nama_warna_bold(st.session_state.item))
    
    st.write("")
    if st.button("📥 INBOUND (Simpan Barang Masuk ke Rak)\nMemasukkan palet baru hasil produksi / datang ke dalam rak", type="primary", use_container_width=True):
        st.session_state.mode_transaksi = "INBOUND"
        pindah_slide(5); st.rerun()
        
    st.write("")
    if st.button("🏭 AMBIL WIP (Suplai Bahan ke Produksi)\nMengeluarkan stok dari rak berdasarkan urutan FIFO untuk kebutuhan mesin", use_container_width=True):
        st.session_state.mode_transaksi = "WIP"
        pindah_slide(5); st.rerun()

# ==========================================================
# CABANG A: PROSES INBOUND (SLIDE 5 -> 6 -> 7)
# ==========================================================
elif st.session_state.mode_transaksi == "INBOUND":
    
    # --- SLIDE 5 INBOUND: INPUT DATA ---
    if st.session_state.slide == 5:
        if st.button("🔙 GANTI AKTIVITAS"): pindah_slide(4); st.rerun()
        
        st.markdown("### Detail Barang Masuk (Inbound)")
        st.info(format_nama_warna_bold(st.session_state.item))
        
        part_num = str(st.session_state.pilih_part).strip().upper()
        if part_num.endswith('C'): satuan_otomatis = "Sak"
        elif part_num.endswith('A'): satuan_otomatis = "Nictainer"
        else: satuan_otomatis = "Dus"
            
        with st.container(border=True):
            in_lot = st.text_input("Nomor Lot", value=st.session_state.in_lot if st.session_state.in_lot else "", placeholder="Cth: 3D3121126")
            
            c_q1, c_q2 = st.columns(2)
            with c_q1:
                in_qty_p = st.number_input("Pallet Utuh", min_value=0, value=st.session_state.in_qty_p if st.session_state.in_qty_p is not None else 1)
            with c_q2:
                in_qty_c = st.number_input(f"Qty Pecahan ({satuan_otomatis})", min_value=0, value=st.session_state.in_qty_c if st.session_state.in_qty_c is not None else 0)
                
        st.write("")
        if st.button("🔍 CARI LOKASI RAK (KONSOLIDASI)", type="primary", use_container_width=True):
            if not in_lot.strip(): st.error("⚠️ Nomor Lot wajib diisi!")
            elif in_qty_p == 0 and in_qty_c == 0: st.error("⚠️ Masukkan minimal 1 kuantitas!")
            else:
                st.session_state.in_lot = in_lot.strip()
                st.session_state.in_qty_p = in_qty_p
                st.session_state.in_qty_c = in_qty_c
                st.session_state.in_tipe_c = satuan_otomatis 
                pindah_slide(6); st.rerun()

    # --- SLIDE 6 INBOUND: REKOMENDASI LOKASI RAK ---
    elif st.session_state.slide == 6:
        if st.button("🔙 UBAH INPUT"): pindah_slide(5); st.rerun()
        st.markdown("### Rekomendasi Lokasi Rak")
        
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
                b_val = d.get('Baris', 0)
                try: b = int(float(b_val))
                except: continue
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
                has_sac = False
                
                for p in pallets:
                    p_name = str(p.get('Nama Produk', '')).strip()
                    if p_name:
                        distinct_items.add(p_name)
                    p_part = str(p.get('Part Number', '')).strip().upper()
                    if p_part.endswith('C'):
                        has_sac = True
                        used_cap += 1.0 / max_sac
                    else:
                        used_cap += 1.0 / max_dus
                    
                sisa_cap = max(0.0, 1.0001 - used_cap)
                sisa_slot_pallet = int(math.floor(sisa_cap * max_target))
                
                # Buffer fisik kemasan sak: sisa <= 1 slot dilewati
                if (in_is_sac or has_sac) and sisa_slot_pallet <= 1:
                    continue
                    
                if sisa_slot_pallet <= 0:
                    continue
                    
                if len(distinct_items) == 1 and target_item in distinct_items:
                    prio = 1
                    status = "🟢 KONSOLIDASI (SEJENIS)"
                    isi_eksisting = f"{len(pallets)} Pallet sejenis"
                elif len(distinct_items) == 0:
                    prio = 2
                    status = "⚪ BARIS KOSONG"
                    isi_eksisting = "Kosong"
                elif len(distinct_items) == 1 and target_item not in distinct_items:
                    prio = 3
                    status = "🟡 CAMPUR (MAX 2 ITEM)"
                    item_lain = list(distinct_items)[0]
                    isi_eksisting = f"1 Item lain ({item_lain[:22]}...)" if len(item_lain) > 22 else f"1 Item lain ({item_lain})"
                else:
                    continue
                    
                kandidat_rak.append({
                    'Loc': loc, 'Kolom': k, 'Baris': b, 'Priority': prio, 'Status': status,
                    'IsiEksisting': isi_eksisting, 'UsedCap': used_cap,
                    'MaxTarget': max_target, 'SisaSlot': sisa_slot_pallet
                })
                
            if not kandidat_rak:
                st.error("❌ Seluruh rak penuh atau sudah berisi 2 jenis item berbeda!")
            else:
                df_kandidat = pd.DataFrame(kandidat_rak)
                df_kandidat = df_kandidat.sort_values(
                    by=['Priority', 'UsedCap', 'Kolom', 'Baris'],
                    ascending=[True, False, True, True]
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
                    st.caption(f"Kebutuhan ruang: **{total_slot_butuh} slot pallet** (Muat dalam 1 baris rak)")
                    for idx, row in df_single_muat.head(8).iterrows():
                        prio_label = "🌟 REKOMENDASI TERBAIK" if idx == 0 else ""
                        persentase_terisi = int(row['UsedCap'] * 100)
                        est_akhir = int(min(100, (row['UsedCap'] + (total_slot_butuh / row['MaxTarget'])) * 100))
                        
                        btn_label = f"📍 RAK {row['Kolom']}-{row['Baris']}  |  {row['Status']}\n"
                        btn_label += f"📊 Sisa Muat: {row['SisaSlot']} Pallet  |  Terisi: {persentase_terisi}% ➔ {est_akhir}%\n"
                        btn_label += f"📦 Kondisi: {row['IsiEksisting']} {(' | ' + prio_label) if prio_label else ''}"
                        
                        if st.button(btn_label, use_container_width=True, key=f"btn_single_{idx}"):
                            st.session_state.alokasi_terpilih = [{
                                'Kolom': row['Kolom'], 'Baris': int(row['Baris']),
                                'qty_p': qty_p_in, 'qty_c': qty_c_in,
                                'Status': row['Status']
                            }]
                            pindah_slide(7); st.rerun()
                else:
                    total_kapasitas_gudang = int(df_kandidat['SisaSlot'].sum()) if not df_kandidat.empty else 0
                    if total_kapasitas_gudang < total_slot_butuh:
                        st.error(f"❌ Total sisa kapasitas gudang ({total_kapasitas_gudang} palet) tidak mencukupi untuk menampung {total_slot_butuh} palet!")
                    else:
                        st.info(f"🔀 **Alokasi Multi-Rak Otomatis:** Kebutuhan **{total_slot_butuh} slot pallet** dibagi berurutan sesuai prioritas:")
                        
                        rencana_alokasi = []
                        sisa_utuh = qty_p_in
                        sisa_pecahan = qty_c_in
                        
                        for _, row in df_kandidat.iterrows():
                            if sisa_utuh == 0 and sisa_pecahan == 0:
                                break
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
                            pindah_slide(7); st.rerun()

    # --- SLIDE 7 INBOUND: KONFIRMASI & EKSEKUSI ---
    elif st.session_state.slide == 7:
        if st.button("🔙 BATAL (GANTI LOKASI)"): pindah_slide(6); st.rerun()
            
        alokasi = st.session_state.alokasi_terpilih or []
        st.markdown("### Konfirmasi Inbound")
        with st.container(border=True):
            st.markdown(format_nama_warna_bold(st.session_state.item))
            st.markdown(f"**Lot Masuk:** `{st.session_state.in_lot}`")
            teks_pecahan = f", {st.session_state.in_qty_c} {st.session_state.in_tipe_c}" if st.session_state.in_qty_c > 0 else ""
            st.caption(f"Total Masuk: {st.session_state.in_qty_p} Pallet Utuh{teks_pecahan}")
            st.divider()
            st.markdown("**Rincian Rak Tujuan:**")
            for al in alokasi:
                p_str = f" + {al['qty_c']} {st.session_state.in_tipe_c}" if al['qty_c'] > 0 else ""
                st.markdown(f"- 📍 **Rak {al['Kolom']}-{al['Baris']}**: {al['qty_p']} Pallet{p_str} *({al['Status']})*")
            
        st.write("")
        catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Titipan sementara / DO-9821")
        
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
                        st.session_state.in_tipe_c, catatan_operator
                    )
                    
                    teks_keterangan = f"Dieksekusi oleh: {st.session_state.operator}"
                    if catatan_operator.strip(): teks_keterangan += f" | Catatan: {catatan_operator.strip()}"
                    
                    ringkasan_rak = []
                    for al in alokasi:
                        catat_log_mutasi(
                            "INBOUND (Remote)", al['Kolom'], al['Baris'],
                            st.session_state.item, st.session_state.pilih_part, st.session_state.in_lot,
                            al['qty_p'], al['qty_c'], 0, teks_keterangan
                        )
                        p_str = f"+{al['qty_c']}{st.session_state.in_tipe_c}" if al['qty_c'] > 0 else ""
                        ringkasan_rak.append(f"{al['Kolom']}-{al['Baris']} ({al['qty_p']}P{p_str})")
                    
                    lokasi_gabungan = ", ".join(ringkasan_rak)
                    pecahan_tele = f", {st.session_state.in_qty_c} {st.session_state.in_tipe_c}" if st.session_state.in_qty_c > 0 else ""
                    
                    pesan_tele = (
                        f"📥 <b>INBOUND - RACK NAVIGATOR</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📦 <b>Item:</b> {escape_html(st.session_state.item)}\n"
                        f"🏷️ <b>Lot:</b> <code>{escape_html(st.session_state.in_lot)}</code>\n"
                        f"📊 <b>Total:</b> {st.session_state.in_qty_p} Pallet{pecahan_tele}\n"
                        f"📍 <b>Penempatan:</b> {escape_html(lokasi_gabungan)}\n"
                        f"👤 <b>Operator:</b> {escape_html(st.session_state.operator)}\n"
                    )
                    if catatan_operator.strip():
                        pesan_tele += f"📝 <b>Catatan:</b> <i>{escape_html(catatan_operator.strip())}</i>\n"
                    kirim_notif_telegram(pesan_tele)
                    
                    get_all_racking_data.clear()
                    
                    msg_sukses = f"{st.session_state.in_qty_p} Pallet{pecahan_tele} masuk ke {lokasi_gabungan}."
                    simpan_undo_firestore(st.session_state.operator, backup_kolom, msg_sukses, mode_label="INBOUND")
                    
                    st.toast(msg_sukses, icon="✅")
                    
                    for k in ['kategori', 'gramasi', 'item', 'pilih_part', 'mode_transaksi', 'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'alokasi_terpilih']:
                        st.session_state[k] = None
                    pindah_slide(1); st.rerun()
                    
                except Exception as err:
                    st.error(f"❌ Kesalahan Sistem: {err}")

# ==========================================================
# CABANG B: PROSES AMBIL WIP (SLIDE 5 -> 6, LOGIKA OUTBOUND)
# ==========================================================
elif st.session_state.mode_transaksi == "WIP":
    
    # --- SLIDE 5 WIP: PENCARIAN LOKASI STOK (FIFO) ---
    if st.session_state.slide == 5:
        if st.button("🔙 GANTI AKTIVITAS"): pindah_slide(4); st.rerun()
        st.markdown("### Lokasi Target WIP (FIFO)")
        st.info(format_nama_warna_bold(st.session_state.item))
        
        with st.spinner("🔍 Memindai data stok gudang..."):
            semua_data = get_all_racking_data()
            
            if not semua_data:
                st.warning("Database gudang kosong.")
            else:
                df_all = pd.DataFrame(semua_data)
                df_target = df_all[(df_all['Nama Produk'] == st.session_state.item) & (df_all['Part Number'].astype(str).str.strip() != "")]
                
                if df_target.empty:
                    st.error("❌ Stok WIP untuk item ini tidak ditemukan di seluruh rak!")
                else:
                    df_target['Catatan'] = df_target['Catatan'].fillna("").astype(str).replace('nan', '')
                    df_target['Group_Key'] = df_target['Kolom'].astype(str) + "_" + df_target['Baris'].astype(str) + "_" + df_target['Lot Number'].astype(str)
                    
                    rekap_lokasi = []
                    for g_key, df_group in df_target.groupby('Group_Key'):
                        kol = str(df_group.iloc[0]['Kolom'])
                        bar = int(float(df_group.iloc[0]['Baris']))
                        lot = str(df_group.iloc[0]['Lot Number'])
                        catatan = str(df_group.iloc[0]['Catatan'])
                        part_num = str(df_group.iloc[0]['Part Number'])
                        
                        akhiran_part = part_num.strip().upper()[-1:] if part_num.strip() else ""
                        satuan_str = "Sak" if akhiran_part == 'C' else "Nictainer" if akhiran_part == 'A' else "Dus"
                        
                        jml_utuh = df_group[~df_group['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                        pecahan_rows = df_group[df_group['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
                        
                        jml_dus = 0
                        for _, r in pecahan_rows.iterrows():
                            p, e = parse_keterangan(r['Keterangan']); jml_dus += p
                            
                        rekap_lokasi.append({
                            'Kolom': kol, 'Baris': bar, 'Lot Number': lot, 
                            'FIFO_Key': parse_fifo_key(lot),
                            'Pallet': jml_utuh, 'Dus': jml_dus, 'Satuan': satuan_str,
                            'Catatan': catatan, 'Part': part_num
                        })
                    
                    df_rekap = pd.DataFrame(rekap_lokasi).sort_values('FIFO_Key', ascending=True).reset_index(drop=True)
                    
                    for idx, row in df_rekap.iterrows():
                        fifo_label = "🌟 REKOMENDASI UTAMA" if idx == 0 else f"⭐ URUTAN {idx + 1}"
                        catatan_label = f"\n🔖 Catatan: {row['Catatan']}" if row['Catatan'] else ""
                        
                        btn_label = f"📍 RAK {row['Kolom']}-{row['Baris']}  |  🏷️ Lot: {row['Lot Number']}\n"
                        btn_label += f"Stok: {row['Pallet']} Pallet, {row['Dus']} {row['Satuan']}\n"
                        btn_label += f"{fifo_label}{catatan_label}"
                        
                        if st.button(btn_label, use_container_width=True, key=f"btn_wip_loc_{idx}"):
                            st.session_state.wip_kolom = row['Kolom']
                            st.session_state.wip_baris = row['Baris']
                            st.session_state.wip_lot = row['Lot Number']
                            st.session_state.pilih_part = row['Part']
                            st.session_state.wip_catatan_rak = row['Catatan']
                            st.session_state.wip_max_pallet = row['Pallet']
                            st.session_state.wip_max_dus = row['Dus']
                            pindah_slide(6); st.rerun()

    # --- SLIDE 6 WIP: KONFIRMASI PENGELUARAN WIP (INITIAL VS FINAL PCS) ---
    elif st.session_state.slide == 6:
        if st.button("🔙 BATAL"): pindah_slide(5); st.rerun()
        
        init_p = int(st.session_state.wip_max_pallet or 0)
        init_d = int(st.session_state.wip_max_dus or 0)
        
        row_m = df_master[df_master['Nama Barang'] == st.session_state.item]
        total_pcs_pallet = int(row_m.iloc[0]['Total_Pcs_Pallet']) if not row_m.empty and int(row_m.iloc[0]['Total_Pcs_Pallet']) > 0 else 1
        pcs_per_box = int(row_m.iloc[0]['Pcs_Per_Box']) if not row_m.empty and int(row_m.iloc[0]['Pcs_Per_Box']) > 0 else 1
        pcs_per_sac = int(row_m.iloc[0]['Pcs_Per_Sac']) if not row_m.empty and int(row_m.iloc[0]['Pcs_Per_Sac']) > 0 else 1
        
        akhiran_target = str(st.session_state.pilih_part).strip().upper()[-1:] if str(st.session_state.pilih_part).strip() else ""
        if akhiran_target == 'C':
            satuan = "Sak"
            pcs_per_pecahan = pcs_per_sac
        elif akhiran_target == 'A':
            satuan = "Nictainer"
            pcs_per_pecahan = pcs_per_box
        else:
            satuan = "Dus"
            pcs_per_pecahan = pcs_per_box
            
        pecahan_per_pallet = total_pcs_pallet // pcs_per_pecahan if pcs_per_pecahan > 0 else 1
        if pecahan_per_pallet == 0: pecahan_per_pallet = 1
        
        total_pcs_init = (init_p * total_pcs_pallet) + (init_d * pcs_per_pecahan)
        
        st.markdown("### Konfirmasi Ambil WIP (Produksi)")
        with st.container(border=True):
            st.markdown(format_nama_warna_bold(st.session_state.item))
            st.markdown(f"**Lokasi:** Rak {st.session_state.wip_kolom}-{st.session_state.wip_baris}  |  **Lot:** `{st.session_state.wip_lot}`")
            st.info(f"📦 **Stok Awal (Initial):** {init_p} Pallet, {init_d} {satuan} (**{total_pcs_init:,} Pcs**)")
            st.caption(f"Standar Packing: 1 Pallet = {pecahan_per_pallet} {satuan} | 1 {satuan} = {pcs_per_pecahan:,} Pcs")
            
        st.write("")
        st.markdown("**INPUT SISA STOK AKHIR DI RAK (FINAL):**")
        
        default_sisa_p = max(0, init_p - 1) if init_p > 0 else 0
        col_q1, col_q2 = st.columns(2)
        with col_q1:
            sisa_pallet = st.number_input("SISA PALLET UTUH", min_value=0, max_value=init_p, step=1, value=default_sisa_p)
        with col_q2:
            max_sisa_d = max(init_d, pecahan_per_pallet * 2) if pecahan_per_pallet > 1 else max(init_d, 100)
            sisa_dus = st.number_input(f"SISA PECAHAN ({satuan.upper()})", min_value=0, max_value=max_sisa_d, step=1, value=init_d)
            
        total_pcs_final = (sisa_pallet * total_pcs_pallet) + (sisa_dus * pcs_per_pecahan)
        selisih_pcs = total_pcs_init - total_pcs_final
        
        keluar_pallet_eq = selisih_pcs // total_pcs_pallet if total_pcs_pallet > 0 else 0
        sisa_pcs_temp = selisih_pcs % total_pcs_pallet if total_pcs_pallet > 0 else selisih_pcs
        keluar_dus_eq = sisa_pcs_temp // pcs_per_pecahan if pcs_per_pecahan > 0 else 0
        
        st.write("")
        if selisih_pcs > 0:
            st.success(f"📊 **Diambil untuk WIP:** **{selisih_pcs:,} Pcs** ({keluar_pallet_eq} Pallet, {keluar_dus_eq} {satuan})\n\nSisa Akhir di Rak: {sisa_pallet} Pallet, {sisa_dus} {satuan} ({total_pcs_final:,} Pcs)")
        elif selisih_pcs == 0:
            st.warning("⚠️ Sisa akhir sama dengan stok awal (0 Pcs diambil). Ubah angka sisa di atas.")
        else:
            st.error("❌ Sisa stok akhir tidak boleh lebih besar dari stok awal!")
            
        catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Suplai Mesin 3 / Line Printing")
        
        st.write("")
        if selisih_pcs > 0:
            if st.button("🏭 KONFIRMASI AMBIL WIP", type="primary", use_container_width=True):
                with st.spinner("Memproses pengambilan WIP..."):
                    k_pilih = st.session_state.wip_kolom
                    b_pilih = st.session_state.wip_baris
                    lot_pilih = st.session_state.wip_lot
                    
                    doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                    transaction = db.transaction() 
                    
                    try:
                        data_sebelum_update = proses_wip_initial_final(
                            transaction, doc_ref, k_pilih, b_pilih, lot_pilih,
                            st.session_state.pilih_part, st.session_state.item,
                            init_p, init_d, sisa_pallet, sisa_dus, satuan,
                            st.session_state.wip_catatan_rak or ""
                        )
                        
                        teks_keterangan = f"WIP Op: {st.session_state.operator} | Awal: {init_p}P {init_d}{satuan[0]} -> Sisa: {sisa_pallet}P {sisa_dus}{satuan[0]}"
                        if catatan_operator.strip(): teks_keterangan += f" | Catatan: {catatan_operator.strip()}"
                        
                        catat_log_mutasi(
                            "AMBIL WIP (Remote)", k_pilih, b_pilih, st.session_state.item,
                            st.session_state.pilih_part, lot_pilih,
                            keluar_pallet_eq, keluar_dus_eq, selisih_pcs, teks_keterangan
                        )
                        
                        pesan_tele = (
                            f"🏭 <b>AMBIL WIP - RACK NAVIGATOR</b>\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"📦 <b>Item:</b> {escape_html(st.session_state.item)}\n"
                            f"📍 <b>Rak:</b> {k_pilih}-{b_pilih} | <b>Lot:</b> <code>{escape_html(lot_pilih)}</code>\n"
                            f"📊 <b>Diambil WIP:</b> <b>{selisih_pcs:,} Pcs</b> ({keluar_pallet_eq} Pallet, {keluar_dus_eq} {satuan})\n"
                            f"📉 <b>Stok Rak:</b> {init_p}P {init_d}{satuan} ➔ <b>{sisa_pallet}P {sisa_dus}{satuan}</b>\n"
                            f"👤 <b>Operator:</b> {escape_html(st.session_state.operator)}\n"
                        )
                        if catatan_operator.strip(): pesan_tele += f"📝 <b>Catatan:</b> <i>{escape_html(catatan_operator.strip())}</i>\n"
                        kirim_notif_telegram(pesan_tele)
                        
                        get_all_racking_data.clear() 
                        
                        msg_sukses = f"Ambil WIP {selisih_pcs:,} Pcs ({keluar_pallet_eq}P {keluar_dus_eq}{satuan}) dari Rak {k_pilih}-{b_pilih}. Sisa: {sisa_pallet}P {sisa_dus}{satuan}."
                        simpan_undo_firestore(st.session_state.operator, {k_pilih: data_sebelum_update}, msg_sukses, mode_label="AMBIL WIP")
                        
                        st.toast(msg_sukses, icon="✅")
                        
                        for k in ['kategori', 'gramasi', 'item', 'pilih_part', 'mode_transaksi', 'wip_kolom', 'wip_baris', 'wip_lot', 'wip_catatan_rak', 'wip_max_pallet', 'wip_max_dus']:
                            st.session_state[k] = None
                        pindah_slide(1); st.rerun()
                        
                    except ValueError as err: st.error(f"❌ Transaksi Dibatalkan: {err}")
                    except Exception as err: st.error(f"❌ Kesalahan Sistem: {err}")
