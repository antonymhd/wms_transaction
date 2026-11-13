import streamlit as st
import pandas as pd
import re
import requests
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================================
# 1. KONFIGURASI TAMPILAN & CSS ADAPTIF (GELAP/TERANG)
# ==========================================================
st.set_page_config(page_title="Outbound - Rack Navigator", layout="centered", initial_sidebar_state="collapsed")

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
            background-color: #2563EB !important;
            border-color: #3B82F6 !important;
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
        .step-active { color: #2563EB; font-weight: bold; background: rgba(37, 99, 235, 0.1); padding: 4px 8px; border-radius: 6px; }
        .step-inactive { color: var(--text-color); opacity: 0.6; }
        
        .db-info-box {
            background-color: var(--secondary-background-color);
            border: 1px solid var(--border-color);
            padding: 12px 16px;
            border-radius: 10px;
            margin-bottom: 15px;
        }
        
        .btn-gramasi > button {
            font-size: 20px !important;
            text-align: center !important;
        }
    </style>
""", unsafe_allow_html=True)

# ==========================================================
# 2. INISIALISASI FIREBASE, TELEGRAM & HELPER
# ==========================================================
NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'
DB_UNDO_STATE = 'wms_undo_state'
OPERATOR_OUTBOUND = ["Maheswara", "Amir", "Bakir", "Danny"]

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

def get_satuan_kemasan(part_num):
    p = str(part_num).strip().upper()
    if p.endswith('C'): return "Sak"
    elif p.endswith('A'): return "Nictainer"
    return "Dus"

def catat_log_mutasi(jenis, kolom, baris, produk, part_num, no_lot, pallet_utuh, pecahan, eceran, keterangan=""):
    waktu_sekarang = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")
    log_data = {
        "Waktu Transaksi": waktu_sekarang, "Jenis Transaksi": jenis, "Lokasi Rak": f"{kolom}-{baris}",
        "Nama Produk": produk, "Part Number": part_num, "No Lot": no_lot if str(no_lot).strip() != "" else "-",
        "Mutasi Pallet": float(pallet_utuh), "Mutasi Pecahan (Dus/Sak)": float(pecahan),
        "Mutasi Eceran (Pcs)": float(eceran), "Keterangan": keterangan,
        "Timestamp_System": firestore.SERVER_TIMESTAMP
    }
    try: db.collection(DB_LOG_MUTASI).add(log_data)
    except Exception: pass 

# ==========================================================
# 3. PERSISTENT UNDO 30 MENIT (HEMAT KUOTA READ)
# ==========================================================
def simpan_undo_firestore(operator_name, undo_dict, action_msg):
    """Menyimpan backup Undo ke Firestore agar bertahan meski browser refresh (maks 30 menit)"""
    now_str = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "status": "DONE",
        "undo_dict": undo_dict,
        "action_msg": action_msg,
        "created_at": now_str
    }
    st.session_state.undo_record = payload
    try:
        db.collection(DB_UNDO_STATE).document(f"OUT_{operator_name}").set(payload)
    except Exception:
        pass

def muat_undo_firestore(operator_name):
    """Dipanggil HANYA 1x saat operator memilih nama di Slide 0"""
    try:
        doc = db.collection(DB_UNDO_STATE).document(f"OUT_{operator_name}").get()
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
        db.collection(DB_UNDO_STATE).document(f"OUT_{operator_name}").update({"status": new_status})
    except Exception:
        pass

# ==========================================================
# 4. FIREBASE TRANSACTIONS & CACHE
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
def proses_outbound_initial_final(transaction, doc_ref, k_pilih, b_pilih, lot_pilih, part_num, item_name, init_pallet, init_dus, final_pallet, final_dus, satuan_kemasan, catatan_lama):
    """
    Memverifikasi stok awal masih sama (anti-race condition), lalu mengganti state
    Lot tersebut di baris rak menjadi stok sisa akhir (final_pallet + final_dus).
    """
    snapshot = doc_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise ValueError("Data Kolom tidak ditemukan di database.")
        
    all_col_data = snapshot.to_dict().get('data', [])
    data_lama_sebelum_update = all_col_data.copy()
    
    rak_ini = [d for d in all_col_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
    rak_lain = [d for d in all_col_data if float(d.get('Baris', 0) or 0) != float(b_pilih)]
    
    # Validasi ulang stok riil di database saat transaksi dikunci
    lot_records = [d for d in rak_ini if str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name]
    db_utuh = sum(1 for d in lot_records if 'pecahan' not in str(d.get('Keterangan', '')).lower() and 'eceran' not in str(d.get('Keterangan', '')).lower())
    db_dus = sum(parse_keterangan(d.get('Keterangan', ''))[0] for d in lot_records if 'pecahan' in str(d.get('Keterangan', '')).lower() or 'eceran' in str(d.get('Keterangan', '')).lower())
    
    if db_utuh != init_pallet or db_dus != init_dus:
        raise ValueError(f"Stok di database baru saja berubah ({db_utuh} Pallet, {db_dus} {satuan_kemasan}). Silakan muat ulang lokasi.")
    
    # Pertahankan entri lot lain di baris yang sama
    sisa_rak_ini = [d for d in rak_ini if not (str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name)]
    
    # Bangun ulang entri sisa akhir (Final) untuk Lot ini
    records_baru = []
    for _ in range(final_pallet):
        records_baru.append({
            'Kolom': k_pilih, 'Baris': float(b_pilih),
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': 'Utuh', 'Catatan': catatan_lama,
            'Pallet Ke': 999.0
        })
        
    if final_dus > 0:
        records_baru.append({
            'Kolom': k_pilih, 'Baris': float(b_pilih),
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': f"Pecahan: {final_dus} {satuan_kemasan}", 'Catatan': catatan_lama,
            'Pallet Ke': 999.0
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
    if len(parts) == 2:
        return f"{parts[0]}, **{parts[1].strip()}**"
    return str(nama_barang)

def parse_angka_aman(val, default=1):
    try:
        clean = re.sub(r'[^\d.]', '', str(val).replace(',', '.'))
        num = int(float(clean))
        return num if num > 0 else default
    except Exception:
        return default

@st.cache_data(ttl=3600)
def load_master_data():
    try:
        df = pd.read_csv("Master_Produk.csv", sep=";", dtype=str)
        df = df.dropna(subset=['Nama Barang'])
        df['Kategori'] = df['Nama Barang'].apply(set_kategori)
        df['Gramasi'] = df['Nama Barang'].apply(ekstrak_gramasi)
        if 'Part Number' not in df.columns: df['Part Number'] = ""
        
        # Deteksi otomatis kolom konversi Dus/Pallet & Pcs/Dus dari Master_Produk.csv
        col_dus_per_pal = next((c for c in df.columns if any(k in c.lower() for k in ['dus/pal', 'sak/pal', 'isi pal', 'qty/pal', 'pallet'])), None)
        col_pcs_per_dus = next((c for c in df.columns if any(k in c.lower() for k in ['pcs/dus', 'pcs/sak', 'pcs/pack', 'isi dus', 'qty/dus', 'pcs'])), None)
        
        df['Conv_Dus_Per_Pallet'] = df[col_dus_per_pal].apply(lambda x: parse_angka_aman(x, 1)) if col_dus_per_pal else 1
        df['Conv_Pcs_Per_Dus'] = df[col_pcs_per_dus].apply(lambda x: parse_angka_aman(x, 1)) if col_pcs_per_dus else 1
        return df
    except Exception:
        return pd.DataFrame(columns=['Nama Barang', 'Kategori', 'Gramasi', 'Part Number', 'Conv_Dus_Per_Pallet', 'Conv_Pcs_Per_Dus'])

df_master = load_master_data()

keys_state = [
    'slide', 'kategori', 'gramasi', 'item', 'pilih_kolom', 'pilih_baris',
    'pilih_lot', 'pilih_part', 'pilih_catatan_rak', 'max_pallet', 'max_dus',
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
            <div style="font-size: 18px; font-weight: bold; color: #2563EB;">
                🧭 Outbound - Rack Navigator
            </div>
            <div style="font-size: 13px; opacity: 0.8; margin-top: 4px;">
                Pilih Operator Forklift yang Bertugas
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="btn-center">', unsafe_allow_html=True)
    for i in range(0, len(OPERATOR_OUTBOUND), 2):
        cols = st.columns(2)
        with cols[0]:
            op1 = OPERATOR_OUTBOUND[i]
            if st.button(f"👤 {op1}", key=f"op_{op1}", use_container_width=True):
                st.session_state.operator = op1
                muat_undo_firestore(op1)
                pindah_slide(1)
                st.rerun()
        if i + 1 < len(OPERATOR_OUTBOUND):
            with cols[1]:
                op2 = OPERATOR_OUTBOUND[i+1]
                if st.button(f"👤 {op2}", key=f"op_{op2}", use_container_width=True):
                    st.session_state.operator = op2
                    muat_undo_firestore(op2)
                    pindah_slide(1)
                    st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.write("")
    with st.expander("➕ Operator Pengganti / Lainnya"):
        op_manual = st.text_input("Ketik Nama Operator", placeholder="Masukkan nama...")
        if st.button("MASUK DENGAN NAMA INI", type="primary", use_container_width=True):
            if op_manual.strip():
                nama_fix = op_manual.strip().title()
                st.session_state.operator = nama_fix
                muat_undo_firestore(nama_fix)
                pindah_slide(1)
                st.rerun()
            else:
                st.warning("Nama operator tidak boleh kosong.")
    st.stop()

# ==========================================================
# HEADER STATUS & NOTIFIKASI UNDO (BERLAKU 30 MENIT)
# ==========================================================
col_hdr1, col_hdr2 = st.columns([3, 1])
with col_hdr1:
    st.markdown(f"""
        <div class="db-info-box" style="margin-bottom: 8px;">
            <div style="font-size: 14px; font-weight: bold; color: #2563EB;">
                🧭 Outbound - Rack Navigator
            </div>
            <div style="font-size: 12px; opacity: 0.85; margin-top: 2px;">
                👤 <strong>{st.session_state.operator}</strong>  |  📡 {NAMA_DATABASE}
            </div>
        </div>
    """, unsafe_allow_html=True)
with col_hdr2:
    if st.button("🔄 Ganti\nOperator", use_container_width=True):
        st.session_state.operator = None
        st.session_state.undo_record = None
        pindah_slide(0)
        st.rerun()

# Cek validitas 30 menit di memori sesi (0 Firestore Read!)
undo_rec = st.session_state.undo_record
if undo_rec and undo_rec.get("status") == "DONE":
    masih_berlaku = True
    if undo_rec.get("created_at"):
        created_dt = datetime.strptime(undo_rec["created_at"], "%Y-%m-%d %H:%M:%S")
        if (get_waktu_jkt() - created_dt) > timedelta(minutes=30):
            masih_berlaku = False
            st.session_state.undo_record = None
            
    if masih_berlaku:
        with st.container(border=True):
            st.success(f"✅ **BERHASIL KELUAR:** {undo_rec.get('action_msg', '')}")
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
                        catat_log_mutasi("PEMBATALAN (UNDO)", ",".join(kol_terdampak), "ALL", "Koreksi Sistem", "-", "-", 0, 0, 0, f"Dibatalkan oleh: {st.session_state.operator}")
                        
                        pesan_undo = (
                            f"⏪ <b>UNDO OUTBOUND - RACK NAVIGATOR</b>\n"
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
                    tutup_undo_firestore(st.session_state.operator, "CLOSED")
                    st.rerun()

elif undo_rec and undo_rec.get("status") == "UNDONE":
    with st.container(border=True):
        st.warning("⏪ **Transaksi dibatalkan.** Stok kembali ke posisi semula.")
        if st.button("TUTUP PESAN", use_container_width=True):
            st.session_state.undo_record = None
            st.rerun()

def render_progress():
    steps = ["Kategori", "Gramasi", "Item", "Lokasi", "Selesai"]
    current = st.session_state.slide
    html = "<div class='progress-container'>"
    for i, s in enumerate(steps, 1):
        if i == current: html += f"<span class='step-active'>{s}</span>"
        else: html += f"<span class='step-inactive'>{s}</span>"
        if i < len(steps): html += " ➔ "
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

render_progress()

# ==========================================================
# SLIDE 1: KATEGORI
# ==========================================================
if st.session_state.slide == 1:
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
    st.markdown(f"### Pilih Item Spesifik")
    st.caption(f"{st.session_state.kategori} | {st.session_state.gramasi}")
    
    df_item = df_master[(df_master['Kategori'] == st.session_state.kategori) & (df_master['Gramasi'] == st.session_state.gramasi)]
    
    for idx, row in df_item.iterrows():
        nama_brg = row['Nama Barang']
        part_num = str(row['Part Number']).strip().upper()
        
        tipe_kemasan = ""
        if part_num:
            akhiran = part_num[-1]
            if akhiran in ['D', 'G']: tipe_kemasan = "[📦 DUS] "
            elif akhiran == 'C': tipe_kemasan = "[👝 SAK] "
            elif akhiran == 'A': tipe_kemasan = "[🗃️ NICTAINER] "
                
        nama_terformat = format_nama_warna_bold(nama_brg)
        btn_label = f"{tipe_kemasan}{nama_terformat}"
        
        if st.button(btn_label, use_container_width=True, key=f"btn_item_{idx}"): 
            st.session_state.item = nama_brg
            pindah_slide(4)
            st.rerun()

# ==========================================================
# SLIDE 4: PENCARIAN LOKASI STOK (FIFO AKURAT TANGGAL)
# ==========================================================
elif st.session_state.slide == 4:
    if st.button("🔙 KEMBALI"): pindah_slide(3); st.rerun()
    st.markdown("### Lokasi Target (FIFO)")
    st.info(format_nama_warna_bold(st.session_state.item))
    
    with st.spinner("🔍 Memindai data stok gudang..."):
        semua_data = get_all_racking_data()
        
        if not semua_data:
            st.warning("Database gudang kosong.")
        else:
            df_all = pd.DataFrame(semua_data)
            df_target = df_all[(df_all['Nama Produk'] == st.session_state.item) & (df_all['Part Number'].astype(str).str.strip() != "")]
            
            if df_target.empty:
                st.error("❌ Stok untuk item ini tidak ditemukan di seluruh rak!")
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
                    satuan = get_satuan_kemasan(part_num)
                    
                    jml_utuh = df_group[~df_group['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                    pecahan_rows = df_group[df_group['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
                    
                    jml_dus = 0
                    for _, r in pecahan_rows.iterrows():
                        p, e = parse_keterangan(r['Keterangan']); jml_dus += p
                        
                    rekap_lokasi.append({
                        'Kolom': kol, 'Baris': bar, 'Lot Number': lot, 
                        'FIFO_Key': parse_fifo_key(lot),
                        'Pallet': jml_utuh, 'Dus': jml_dus, 'Satuan': satuan,
                        'Catatan': catatan, 'Part': part_num
                    })
                
                df_rekap = pd.DataFrame(rekap_lokasi).sort_values('FIFO_Key', ascending=True).reset_index(drop=True)
                
                for idx, row in df_rekap.iterrows():
                    fifo_label = "🌟 REKOMENDASI UTAMA" if idx == 0 else f"⭐ URUTAN {idx + 1}"
                    catatan_label = f"\n🔖 Catatan: {row['Catatan']}" if row['Catatan'] else ""
                    
                    btn_label = f"📍 RAK {row['Kolom']}-{row['Baris']}  |  🏷️ Lot: {row['Lot Number']}\n"
                    btn_label += f"Stok: {row['Pallet']} Pallet, {row['Dus']} {row['Satuan']}\n"
                    btn_label += f"{fifo_label}{catatan_label}"
                    
                    if st.button(btn_label, use_container_width=True, key=f"btn_loc_{idx}"):
                        st.session_state.pilih_kolom = row['Kolom']
                        st.session_state.pilih_baris = row['Baris']
                        st.session_state.pilih_lot = row['Lot Number']
                        st.session_state.pilih_part = row['Part']
                        st.session_state.pilih_catatan_rak = row['Catatan']
                        st.session_state.max_pallet = row['Pallet']
                        st.session_state.max_dus = row['Dus']
                        pindah_slide(5); st.rerun()

# ==========================================================
# SLIDE 5: KONFIRMASI PENGELUARAN (INITIAL VS FINAL PCS)
# ==========================================================
elif st.session_state.slide == 5:
    if st.button("🔙 BATAL"): pindah_slide(4); st.rerun()
    
    satuan = get_satuan_kemasan(st.session_state.pilih_part)
    init_p = int(st.session_state.max_pallet or 0)
    init_d = int(st.session_state.max_dus or 0)
    
    # Ambil rasio konversi dari Master_Produk.csv
    row_m = df_master[df_master['Nama Barang'] == st.session_state.item]
    dus_per_pallet = int(row_m.iloc[0]['Conv_Dus_Per_Pallet']) if not row_m.empty else 1
    pcs_per_dus = int(row_m.iloc[0]['Conv_Pcs_Per_Dus']) if not row_m.empty else 1
    
    total_dus_init = (init_p * dus_per_pallet) + init_d
    total_pcs_init = total_dus_init * pcs_per_dus
    
    st.markdown("### Konfirmasi Pengeluaran")
    with st.container(border=True):
        st.markdown(format_nama_warna_bold(st.session_state.item))
        st.markdown(f"**Lokasi:** Rak {st.session_state.pilih_kolom}-{st.session_state.pilih_baris}  |  **Lot:** `{st.session_state.pilih_lot}`")
        st.info(f"📦 **Stok Awal (Initial):** {init_p} Pallet, {init_d} {satuan} (**{total_pcs_init:,} Pcs**)")
        st.caption(f"Standar Packing: 1 Pallet = {dus_per_pallet} {satuan} | 1 {satuan} = {pcs_per_dus:,} Pcs")
        
    st.write("")
    st.markdown("**INPUT SISA STOK AKHIR DI RAK (FINAL):**")
    
    default_sisa_p = max(0, init_p - 1) if init_p > 0 else 0
    col_q1, col_q2 = st.columns(2)
    with col_q1:
        sisa_pallet = st.number_input("SISA PALLET UTUH", min_value=0, max_value=init_p, step=1, value=default_sisa_p)
    with col_q2:
        # Diizinkan melebihi init_d jika ada palet utuh yang dipecah (misal 2 palet 3 dus -> sisa 1 palet 4 dus)
        max_sisa_d = max(init_d, dus_per_pallet * 2) if dus_per_pallet > 1 else max(init_d, 100)
        sisa_dus = st.number_input(f"SISA PECAHAN ({satuan.upper()})", min_value=0, max_value=max_sisa_d, step=1, value=init_d)
        
    total_dus_final = (sisa_pallet * dus_per_pallet) + sisa_dus
    total_pcs_final = total_dus_final * pcs_per_dus
    
    selisih_dus_total = total_dus_init - total_dus_final
    selisih_pcs = total_pcs_init - total_pcs_final
    
    # Konversi selisih ke format Pallet & Dus untuk log mutasi
    keluar_pallet_eq = selisih_dus_total // dus_per_pallet if dus_per_pallet > 0 else 0
    keluar_dus_eq = selisih_dus_total % dus_per_pallet if dus_per_pallet > 0 else selisih_dus_total
    
    st.write("")
    if selisih_pcs > 0:
        st.success(f"📊 **Selisih Keluar:** **{selisih_pcs:,} Pcs** ({keluar_pallet_eq} Pallet, {keluar_dus_eq} {satuan})\n\nSisa Akhir di Rak: {sisa_pallet} Pallet, {sisa_dus} {satuan} ({total_pcs_final:,} Pcs)")
    elif selisih_pcs == 0:
        st.warning("⚠️ Sisa akhir sama dengan stok awal (0 Pcs keluar). Ubah angka sisa di atas.")
    else:
        st.error("❌ Sisa stok akhir tidak boleh lebih besar dari stok awal!")
        
    catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Untuk Line 2 / Stok kurang 1")
    
    st.write("")
    if selisih_pcs > 0:
        if st.button("🚀 KONFIRMASI PENGELUARAN", type="primary", use_container_width=True):
            with st.spinner("Memproses ke database..."):
                
                k_pilih = st.session_state.pilih_kolom
                b_pilih = st.session_state.pilih_baris
                lot_pilih = st.session_state.pilih_lot
                
                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                transaction = db.transaction() 
                
                try:
                    data_sebelum_update = proses_outbound_initial_final(
                        transaction, doc_ref, k_pilih, b_pilih, lot_pilih,
                        st.session_state.pilih_part, st.session_state.item,
                        init_p, init_d, sisa_pallet, sisa_dus, satuan,
                        st.session_state.pilih_catatan_rak or ""
                    )
                    
                    teks_keterangan = f"Op: {st.session_state.operator} | Awal: {init_p}P {init_d}{satuan[0]} -> Sisa: {sisa_pallet}P {sisa_dus}{satuan[0]}"
                    if catatan_operator.strip():
                        teks_keterangan += f" | Catatan: {catatan_operator.strip()}"
                    
                    catat_log_mutasi(
                        "OUTBOUND (Remote)", k_pilih, b_pilih, st.session_state.item,
                        st.session_state.pilih_part, lot_pilih,
                        keluar_pallet_eq, keluar_dus_eq, selisih_pcs, teks_keterangan
                    )
                    
                    pesan_tele = (
                        f"📤 <b>OUTBOUND - RACK NAVIGATOR</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📦 <b>Item:</b> {escape_html(st.session_state.item)}\n"
                        f"📍 <b>Rak:</b> {k_pilih}-{b_pilih} | <b>Lot:</b> <code>{escape_html(lot_pilih)}</code>\n"
                        f"📊 <b>Keluar:</b> <b>{selisih_pcs:,} Pcs</b> ({keluar_pallet_eq} Pallet, {keluar_dus_eq} {satuan})\n"
                        f"📉 <b>Stok Rak:</b> {init_p}P {init_d}{satuan} ➔ <b>{sisa_pallet}P {sisa_dus}{satuan}</b>\n"
                        f"👤 <b>Operator:</b> {escape_html(st.session_state.operator)}\n"
                    )
                    if catatan_operator.strip():
                        pesan_tele += f"📝 <b>Catatan:</b> <i>{escape_html(catatan_operator.strip())}</i>\n"
                    kirim_notif_telegram(pesan_tele)
                    
                    get_all_racking_data.clear() 
                    
                    msg_sukses = f"Keluar {selisih_pcs:,} Pcs ({keluar_pallet_eq}P {keluar_dus_eq}{satuan}) dari Rak {k_pilih}-{b_pilih}. Sisa: {sisa_pallet}P {sisa_dus}{satuan}."
                    simpan_undo_firestore(st.session_state.operator, {k_pilih: data_sebelum_update}, msg_sukses)
                    
                    st.toast(msg_sukses, icon="✅")
                    
                    for k in ['kategori', 'gramasi', 'item', 'pilih_kolom', 'pilih_baris', 'pilih_lot', 'pilih_part', 'pilih_catatan_rak', 'max_pallet', 'max_dus']:
                        st.session_state[k] = None
                    pindah_slide(1)
                    st.rerun()
                    
                except ValueError as err:
                    st.error(f"❌ Transaksi Dibatalkan: {err}")
                except Exception as err:
                    st.error(f"❌ Kesalahan Sistem: {err}")
