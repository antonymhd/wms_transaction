import streamlit as st
import pandas as pd
import re
import requests
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================================
# 1. KONFIGURASI TAMPILAN & CSS ADAPTIF (INBOUND)
# ==========================================================
st.set_page_config(page_title="Inbound - Rack Navigator", layout="centered", initial_sidebar_state="collapsed")

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
def simpan_undo_firestore(operator_name, undo_dict, action_msg):
    now_str = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "status": "DONE",
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
def proses_inbound_transaction(transaction, doc_ref, k_pilih, b_pilih, lot_pilih, part_num, item_name, qty_pallet, qty_pecahan, tipe_pecahan, catatan):
    snapshot = doc_ref.get(transaction=transaction)
    
    all_col_data = snapshot.to_dict().get('data', []) if snapshot.exists else []
    data_lama_sebelum_update = all_col_data.copy()
    
    rak_ini = [d for d in all_col_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
    rak_lain = [d for d in all_col_data if float(d.get('Baris', 0) or 0) != float(b_pilih)]
    
    max_pallet = 0
    if rak_ini:
        max_pallet = int(max([float(d.get('Pallet Ke', 0)) for d in rak_ini]))
        
    pallets_baru = []
    for i in range(qty_pallet):
        pallets_baru.append({
            'Kolom': k_pilih, 'Baris': float(b_pilih),
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': 'Utuh', 'Catatan': catatan,
            'Pallet Ke': float(max_pallet + 1 + i)
        })
        
    if qty_pecahan > 0:
        pallets_baru.append({
            'Kolom': k_pilih, 'Baris': float(b_pilih),
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': f"Pecahan: {qty_pecahan} {tipe_pecahan}", 'Catatan': catatan,
            'Pallet Ke': float(max_pallet + 1 + qty_pallet)
        })
        
    rak_ini.extend(pallets_baru)
    
    rak_ini = sorted(rak_ini, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
    for i, d in enumerate(rak_ini, 1):
        d['Pallet Ke'] = float(i)
        
    final_data = rak_lain + rak_ini
    
    if snapshot.exists: transaction.update(doc_ref, {'data': final_data})
    else: transaction.set(doc_ref, {'data': final_data})
        
    return data_lama_sebelum_update 

# ==========================================================
# 5. MASTER PRODUK & FORMATTING
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
        return df
    except Exception: return pd.DataFrame(columns=['Nama Barang', 'Kategori', 'Gramasi', 'Part Number'])

df_master = load_master_data()

keys_state = [
    'slide', 'kategori', 'gramasi', 'item', 'pilih_part', 'in_lot',
    'in_qty_p', 'in_qty_c', 'in_tipe_c', 'pilih_kolom', 'pilih_baris',
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
                🧭 Inbound - Rack Navigator
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
                pindah_slide(1)
                st.rerun()
        if i + 1 < len(OPERATOR_INBOUND):
            with cols[1]:
                op2 = OPERATOR_INBOUND[i+1]
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
            <div style="font-size: 14px; font-weight: bold; color: #10B981;">
                🧭 Inbound - Rack Navigator
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
            st.success(f"✅ **BERHASIL MASUK:** {undo_rec.get('action_msg', '')}")
            st.caption(f"⏱️ Opsi Undo tersedia selama 30 menit (Input pukul {undo_rec.get('created_at', '')[-8:]} WIB)")
            c_undo1, c_undo2 = st.columns(2)
            with c_undo1:
                st.markdown('<div class="btn-undo">', unsafe_allow_html=True)
                if st.button("⏪ BATAL MASUK (UNDO)", use_container_width=True):
                    with st.spinner("Membatalkan penerimaan..."):
                        batch_undo = db.batch()
                        kol_terdampak = []
                        for kol, dat in undo_rec.get("undo_dict", {}).items():
                            doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{kol}")
                            batch_undo.set(doc_ref, {'data': dat})
                            kol_terdampak.append(kol)
                        batch_undo.commit()
                        
                        get_all_racking_data.clear()
                        tutup_undo_firestore(st.session_state.operator, "UNDONE")
                        catat_log_mutasi("PEMBATALAN (UNDO INBOUND)", ",".join(kol_terdampak), "ALL", "Koreksi Sistem", "-", "-", 0, 0, 0, f"Dibatalkan oleh: {st.session_state.operator}")
                        
                        pesan_undo = (
                            f"⏪ <b>UNDO INBOUND - RACK NAVIGATOR</b>\n"
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
        st.warning("⏪ **Transaksi dibatalkan.** Stok ditarik kembali dari sistem.")
        if st.button("TUTUP PESAN", use_container_width=True):
            st.session_state.undo_record = None
            st.rerun()

def render_progress():
    steps = ["Kategori", "Item", "Input", "Lokasi", "Selesai"]
    current = st.session_state.slide
    idx_map = {1:1, 2:1, 3:2, 4:3, 5:4, 6:5}
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
# SLIDE 1 & 2: KATEGORI & GRAMASI
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
# SLIDE 3: ITEM SPESIFIK & KEMASAN OTOMATIS
# ==========================================================
elif st.session_state.slide == 3:
    if st.button("🔙 KEMBALI"): pindah_slide(2); st.rerun()
    st.markdown(f"### Pilih Barang Masuk")
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
            pindah_slide(4)
            st.rerun()

# ==========================================================
# SLIDE 4: INPUT DATA (TANPA DROPDOWN PECAHAN)
# ==========================================================
elif st.session_state.slide == 4:
    if st.button("🔙 KEMBALI KE ITEM"): pindah_slide(3); st.rerun()
    
    st.markdown("### Detail Barang Masuk")
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
            pindah_slide(5)
            st.rerun()

# ==========================================================
# SLIDE 5: PENCARIAN & KALKULASI KAPASITAS RAK
# ==========================================================
elif st.session_state.slide == 5:
    if st.button("🔙 UBAH INPUT"): pindah_slide(4); st.rerun()
    st.markdown("### Rekomendasi Lokasi Rak")
    
    with st.spinner("📐 Menghitung kapasitas dimensi..."):
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
        
        valid_slots = []
        for loc, pallets in slot_map.items():
            k, b = loc.split('-')
            b = int(b)
            
            max_sac = 31 if b <= 16 else 44
            max_dus = 32 if b <= 16 else 46
            
            used_cap = 0.0
            mix_item = False
            item_sejenis = 0
            
            for p in pallets:
                if str(p.get('Nama Produk', '')) != st.session_state.item:
                    mix_item = True
                else:
                    item_sejenis += 1
                    
                p_part = str(p.get('Part Number', '')).strip().upper()
                if p_part.endswith('C'): used_cap += 1.0 / max_sac
                else: used_cap += 1.0 / max_dus
                
            if mix_item: continue 
            
            inc_cap = (st.session_state.in_qty_p / max_sac) if in_is_sac else (st.session_state.in_qty_p / max_dus)
            if st.session_state.in_qty_c > 0:
                inc_cap += (1.0 / max_sac) if in_is_sac else (1.0 / max_dus)
                
            if used_cap + inc_cap > 1.0001: continue 
            
            status = "🟢 KONSOLIDASI (SEJENIS)" if item_sejenis > 0 else "⚪ KOSONG (BARU)"
            
            valid_slots.append({
                'Kolom': k, 'Baris': b, 'Status': status, 'Sejenis': item_sejenis,
                'UsedCap': used_cap, 'IncCap': inc_cap
            })
            
        if not valid_slots:
            st.error("❌ Tidak ada rak yang memiliki kapasitas cukup tanpa mencampur produk yang berbeda!")
        else:
            df_slots = pd.DataFrame(valid_slots)
            df_slots['Priority'] = df_slots['Status'].apply(lambda x: 1 if 'KONSOLIDASI' in x else 2)
            df_slots = df_slots.sort_values(by=['Priority', 'UsedCap'], ascending=[True, False]).head(8)
            
            for idx, row in df_slots.iterrows():
                prio_label = "🌟 REKOMENDASI TERBAIK" if idx == 0 else ""
                persentase_terisi = int(row['UsedCap'] * 100)
                estimasi_akhir = int((row['UsedCap'] + row['IncCap']) * 100)
                
                btn_label = f"📍 RAK {row['Kolom']}-{row['Baris']}  |  {row['Status']}\n"
                btn_label += f"📊 Terisi: {persentase_terisi}% ➔ Estimasi Menjadi: {estimasi_akhir}%\n"
                btn_label += f"{prio_label}"
                
                if st.button(btn_label, use_container_width=True, key=f"btn_loc_{idx}"):
                    st.session_state.pilih_kolom = row['Kolom']; st.session_state.pilih_baris = row['Baris']
                    pindah_slide(6); st.rerun()

# ==========================================================
# SLIDE 6: KONFIRMASI & EKSEKUSI INBOUND
# ==========================================================
elif st.session_state.slide == 6:
    if st.button("🔙 BATAL (GANTI RAK)"): pindah_slide(5); st.rerun()
        
    st.markdown("### Konfirmasi Inbound")
    with st.container(border=True):
        st.markdown(format_nama_warna_bold(st.session_state.item))
        st.markdown(f"**Rak Tujuan:** {st.session_state.pilih_kolom}-{st.session_state.pilih_baris}  |  **Lot Masuk:** `{st.session_state.in_lot}`")
        teks_pecahan = f", {st.session_state.in_qty_c} {st.session_state.in_tipe_c}" if st.session_state.in_qty_c > 0 else ""
        st.caption(f"Total Masuk: {st.session_state.in_qty_p} Pallet Utuh{teks_pecahan}")
        
    st.write("")
    catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Titipan sementara / DO-9821")
    
    st.write("")
    if st.button("📥 MASUKKAN KE DATABASE SEKARANG", type="primary", use_container_width=True):
        with st.spinner("Memasukkan data ke rak secara aman..."):
            
            k_pilih = st.session_state.pilih_kolom
            b_pilih = st.session_state.pilih_baris
            
            doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            transaction = db.transaction() 
            
            try:
                data_sebelum_update = proses_inbound_transaction(
                    transaction, doc_ref, k_pilih, b_pilih, 
                    st.session_state.in_lot, st.session_state.pilih_part, st.session_state.item, 
                    st.session_state.in_qty_p, st.session_state.in_qty_c, st.session_state.in_tipe_c, catatan_operator
                )
                
                teks_keterangan = f"Dieksekusi oleh: {st.session_state.operator}"
                if catatan_operator.strip(): teks_keterangan += f" | Catatan: {catatan_operator.strip()}"
                
                catat_log_mutasi("INBOUND (Remote)", k_pilih, b_pilih, st.session_state.item, st.session_state.pilih_part, st.session_state.in_lot, st.session_state.in_qty_p, st.session_state.in_qty_c, 0, teks_keterangan)
                
                pecahan_tele = f", {st.session_state.in_qty_c} {st.session_state.in_tipe_c}" if st.session_state.in_qty_c > 0 else ""
                pesan_tele = (
                    f"📥 <b>INBOUND - RACK NAVIGATOR</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📦 <b>Item:</b> {escape_html(st.session_state.item)}\n"
                    f"📍 <b>Rak:</b> {k_pilih}-{b_pilih} | <b>Lot:</b> <code>{escape_html(st.session_state.in_lot)}</code>\n"
                    f"📊 <b>Qty:</b> {st.session_state.in_qty_p} Pallet{pecahan_tele}\n"
                    f"👤 <b>Operator:</b> {escape_html(st.session_state.operator)}\n"
                )
                if catatan_operator.strip():
                    pesan_tele += f"📝 <b>Catatan:</b> <i>{escape_html(catatan_operator.strip())}</i>\n"
                kirim_notif_telegram(pesan_tele)
                
                get_all_racking_data.clear() 
                
                msg_sukses = f"{st.session_state.in_qty_p} Pallet{pecahan_tele} masuk ke Rak {k_pilih}-{b_pilih}."
                simpan_undo_firestore(st.session_state.operator, {k_pilih: data_sebelum_update}, msg_sukses)
                
                st.toast(msg_sukses, icon="✅")
                
                for k in ['kategori', 'gramasi', 'item', 'pilih_part', 'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'pilih_kolom', 'pilih_baris']:
                    st.session_state[k] = None
                pindah_slide(1)
                st.rerun()
                
            except Exception as err:
                st.error(f"❌ Kesalahan Sistem: {err}")
