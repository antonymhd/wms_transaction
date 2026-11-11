import streamlit as st
import pandas as pd
import re
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================================
# 1. KONFIGURASI TAMPILAN MOBILE-FIRST (REMOTE)
# ==========================================================
st.set_page_config(page_title="WMS Outbound - Remote", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        
        .stButton>button {
            min-height: 75px; 
            height: auto;
            font-size: 16px; 
            font-weight: 600;
            border-radius: 10px;
            border: 1px solid #444;
            background-color: #222;
            color: #fff;
            white-space: pre-wrap; 
            word-wrap: break-word;
            text-align: left; 
            padding: 15px;
            transition: all 0.2s ease-in-out;
        }
        .stButton>button:active { background-color: #444; border-color: #fff; }
        
        button[kind="primary"] {
            background-color: #1E88E5 !important;
            border-color: #1E88E5 !important;
            font-size: 18px;
            text-align: center !important;
        }
        
        /* Tombol Undo Merah Custom */
        .btn-undo > button {
            background-color: #D32F2F !important;
            border-color: #D32F2F !important;
            text-align: center !important;
        }
        
        .progress-container {
            font-size: 14px;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid #333;
            text-align: center;
        }
        .step-active { color: #42A5F5; font-weight: bold; }
        .step-inactive { color: #666; }
        div[data-testid="stMetricValue"] { font-size: 24px !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================================
# 2. INISIALISASI FIREBASE & HELPER FUNCTIONS
# ==========================================================
NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

def get_waktu_jkt(): return datetime.utcnow() + timedelta(hours=7)

def parse_keterangan(ket_str):
    ket_str = str(ket_str).lower()
    pecahan, eceran = 0, 0
    m_pec = re.search(r'pecahan:\s*(\d+)', ket_str)
    if m_pec: pecahan = int(m_pec.group(1))
    m_ec = re.search(r'eceran:\s*(\d+)', ket_str)
    if m_ec: eceran = int(m_ec.group(1))
    return pecahan, eceran

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
# 3. FIREBASE READ CACHE & TRANSACTION WRITE
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
def proses_outbound_transaction(transaction, doc_ref, b_pilih, lot_pilih, qty_pallet):
    snapshot = doc_ref.get(transaction=transaction)
    if not snapshot.exists: raise ValueError("Data Kolom tidak ditemukan di database.")
        
    all_col_data = snapshot.to_dict().get('data', [])
    data_lama_sebelum_update = all_col_data.copy()
    
    rak_ini = [d for d in all_col_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
    rak_lain = [d for d in all_col_data if float(d.get('Baris', 0) or 0) != float(b_pilih)]
    
    df_lot = pd.DataFrame([d for d in rak_ini if str(d.get('Lot Number', '')) == lot_pilih])
    if df_lot.empty or len(df_lot) < qty_pallet:
        raise ValueError("Stok tidak mencukupi! Kemungkinan pallet baru saja diambil operator lain.")
        
    df_lot['Pallet Ke'] = pd.to_numeric(df_lot['Pallet Ke'])
    df_lot = df_lot.sort_values('Pallet Ke', ascending=False)
    
    pallets_to_remove = df_lot['Pallet Ke'].head(qty_pallet).tolist() if qty_pallet > 0 else []
    
    updated_rak_ini = []
    for d in rak_ini:
        p_val = pd.to_numeric(d.get('Pallet Ke', 0))
        if p_val in pallets_to_remove and str(d.get('Lot Number', '')) == lot_pilih: continue
        updated_rak_ini.append(d)
    
    updated_rak_ini = sorted(updated_rak_ini, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
    for i, d in enumerate(updated_rak_ini, 1): d['Pallet Ke'] = float(i)
    
    final_data = rak_lain + updated_rak_ini
    transaction.update(doc_ref, {'data': final_data}) 
    return data_lama_sebelum_update 

# ==========================================================
# 4. MASTER PRODUK & STATE MANAGEMENT
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

@st.cache_data(ttl=3600)
def load_master_data():
    try:
        df = pd.read_csv("Master_Produk.csv", sep=";")
        df = df.dropna(subset=['Nama Barang'])
        df['Kategori'] = df['Nama Barang'].apply(set_kategori)
        df['Gramasi'] = df['Nama Barang'].apply(ekstrak_gramasi)
        return df
    except Exception: return pd.DataFrame(columns=['Nama Barang', 'Kategori', 'Gramasi'])

df_master = load_master_data()

# INISIALISASI SEMUA STATE
keys_state = ['slide', 'kategori', 'gramasi', 'item', 'pilih_kolom', 'pilih_baris', 'pilih_lot', 'pilih_part', 'max_pallet', 'max_dus', 'operator', 'action_msg', 'action_status', 'undo_dict']
for key in keys_state:
    if key not in st.session_state:
        if key == 'operator': st.session_state[key] = "Operator 1 (Default)"
        elif key == 'slide': st.session_state[key] = 1
        elif key == 'undo_dict': st.session_state[key] = {}
        elif key == 'action_msg': st.session_state[key] = ""
        else: st.session_state[key] = None

def pindah_slide(tujuan): st.session_state.slide = tujuan

# HEADER DATABASE STATUS
st.markdown(f"""
    <div style="background-color: #1a1a1a; border: 1px solid #333; padding: 12px; border-radius: 8px; margin-bottom: 10px;">
        <div style="font-size: 13px; color: #aaa; margin-bottom: 6px;">
            📡 DB: <strong style="color: #4CAF50;">{NAMA_DATABASE}</strong>
        </div>
        <div style="font-size: 13px; color: #aaa;">
            👤 Aktif: <strong style="color: #fff;">{st.session_state.operator}</strong>
        </div>
    </div>
""", unsafe_allow_html=True)

# ==========================================================
# 5. NOTIFIKASI & SISTEM UNDO (BATAL) - POSISI DI ATAS
# ==========================================================
if st.session_state.action_status == 'DONE':
    with st.container(border=True):
        st.success(f"✅ **SUKSES:** {st.session_state.action_msg}")
        c_undo1, c_undo2 = st.columns(2)
        with c_undo1:
            st.markdown('<div class="btn-undo">', unsafe_allow_html=True)
            if st.button("⏪ UNDO (BATAL)", use_container_width=True):
                with st.spinner("Memulihkan stok..."):
                    batch_undo = db.batch()
                    kol_terdampak = []
                    for kol, dat in st.session_state.undo_dict.items():
                        doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{kol}")
                        batch_undo.set(doc_ref, {'data': dat})
                        kol_terdampak.append(kol)
                    batch_undo.commit()
                    
                    get_all_racking_data.clear()
                    st.session_state.action_status = 'UNDONE'
                    catat_log_mutasi("PEMBATALAN (UNDO)", ",".join(kol_terdampak), "ALL", "Koreksi Sistem", "-", "-", 0, 0, 0, f"Dibatalkan oleh: {st.session_state.operator}")
                    st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        with c_undo2:
            if st.button("TUTUP / LANJUT", use_container_width=True):
                st.session_state.action_status = None
                st.rerun()
                
elif st.session_state.action_status == 'UNDONE':
    with st.container(border=True):
        st.warning("⏪ **Aksi dibatalkan.** Stok telah kembali seperti semula.")
        if st.button("TUTUP PESAN", use_container_width=True):
            st.session_state.action_status = None
            st.rerun()

# RENDER PROGRESS BAR
def render_progress():
    steps = ["Kategori", "Gramasi", "Item", "Lokasi", "Selesai"]
    current = st.session_state.slide
    html = "<div class='progress-container'>"
    for i, s in enumerate(steps, 1):
        if i == current: html += f"<span class='step-active'>[{i}] {s}</span>"
        else: html += f"<span class='step-inactive'>[{i}]</span>"
        if i < len(steps): html += " ➔ "
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

render_progress()

# ==========================================================
# UI SLIDE 1-3: NAVIGASI PRODUK
# ==========================================================
if st.session_state.slide == 1:
    st.markdown("### 🎛️ Remote Outbound")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🧪 PREFORM", use_container_width=True): st.session_state.kategori = "PREFORM"; pindah_slide(2); st.rerun()
        if st.button("🎨 CUP COLOR", use_container_width=True): st.session_state.kategori = "CUP COLOR"; pindah_slide(2); st.rerun()
        if st.button("🧢 CAP (TUTUP)", use_container_width=True): st.session_state.kategori = "CAP"; pindah_slide(2); st.rerun()
    with col2:
        if st.button("🥤 CUP NATURAL", use_container_width=True): st.session_state.kategori = "CUP NATURAL"; pindah_slide(2); st.rerun()
        if st.button("🖨️ CUP PRINTING", use_container_width=True): st.session_state.kategori = "CUP PRINTING"; pindah_slide(2); st.rerun()

elif st.session_state.slide == 2:
    if st.button("🔙 KEMBALI KATEGORI"): pindah_slide(1); st.rerun()
    st.markdown(f"### VARIAN GRAMASI **{st.session_state.kategori}**:")
    df_kat = df_master[df_master['Kategori'] == st.session_state.kategori]
    list_gramasi = sorted(df_kat['Gramasi'].unique())
    for i in range(0, len(list_gramasi), 2):
        cols = st.columns(2)
        with cols[0]:
            if st.button(f"⚖️ {list_gramasi[i]}", key=f"g_{i}", use_container_width=True): st.session_state.gramasi = list_gramasi[i]; pindah_slide(3); st.rerun()
        if i + 1 < len(list_gramasi):
            with cols[1]:
                if st.button(f"⚖️ {list_gramasi[i+1]}", key=f"g_{i+1}", use_container_width=True): st.session_state.gramasi = list_gramasi[i+1]; pindah_slide(3); st.rerun()

elif st.session_state.slide == 3:
    if st.button("🔙 GANTI GRAMASI"): pindah_slide(2); st.rerun()
    st.markdown(f"### PILIH ITEM:")
    df_item = df_master[(df_master['Kategori'] == st.session_state.kategori) & (df_master['Gramasi'] == st.session_state.gramasi)]
    list_item = df_item['Nama Barang'].tolist()
    for item in list_item:
        if st.button(f"📦 {item}", use_container_width=True): st.session_state.item = item; pindah_slide(4); st.rerun()

# ==========================================================
# SLIDE 4: PENCARIAN STOK (CACHE)
# ==========================================================
elif st.session_state.slide == 4:
    if st.button("🔙 GANTI ITEM"): pindah_slide(3); st.rerun()
    st.markdown("### PILIH LOKASI & LOT BARANG")
    st.info(f"{st.session_state.item}")
    
    with st.spinner("🔍 Mencari lokasi stok di gudang..."):
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
                    
                    jml_utuh = df_group[~df_group['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                    pecahan_rows = df_group[df_group['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
                    
                    jml_dus = 0
                    for _, r in pecahan_rows.iterrows():
                        p, e = parse_keterangan(r['Keterangan']); jml_dus += p
                        
                    rekap_lokasi.append({
                        'Kolom': kol, 'Baris': bar, 'Lot Number': lot, 
                        'Pallet': jml_utuh, 'Dus': jml_dus, 'Catatan': catatan, 'Part': part_num
                    })
                
                df_rekap = pd.DataFrame(rekap_lokasi).sort_values('Lot Number', ascending=True).reset_index(drop=True)
                
                for idx, row in df_rekap.iterrows():
                    fifo_label = "🌟 URUTAN 1 (PALING TUA - DIREKOMENDASIKAN)" if idx == 0 else f"⭐ URUTAN {idx + 1}"
                    catatan_label = f"\n🔖 Catatan: {row['Catatan']}" if row['Catatan'] else ""
                    
                    btn_label = f"📍 RAK {row['Kolom']}-{row['Baris']} | 🏷️ Lot: {row['Lot Number']}\n"
                    btn_label += f"📦 Tersedia: {row['Pallet']} Pallet, {row['Dus']} Dus\n"
                    btn_label += f"{fifo_label}{catatan_label}"
                    
                    if st.button(btn_label, use_container_width=True, key=f"btn_loc_{idx}"):
                        st.session_state.pilih_kolom = row['Kolom']; st.session_state.pilih_baris = row['Baris']
                        st.session_state.pilih_lot = row['Lot Number']; st.session_state.pilih_part = row['Part']
                        st.session_state.max_pallet = row['Pallet']; st.session_state.max_dus = row['Dus']
                        pindah_slide(5); st.rerun()

# ==========================================================
# SLIDE 5: EKSEKUSI FIREBASE TRANSACTION & CATATAN
# ==========================================================
elif st.session_state.slide == 5:
    col_back, _ = st.columns([1, 2])
    if col_back.button("🔙 GANTI LOKASI"): pindah_slide(4); st.rerun()
        
    st.markdown("### KONFIRMASI PENGELUARAN")
    with st.container(border=True):
        st.markdown(f"**Item:** {st.session_state.item}")
        st.markdown(f"**Rak:** {st.session_state.pilih_kolom}-{st.session_state.pilih_baris} | **Lot:** {st.session_state.pilih_lot}")
        st.caption(f"Stok Fisik: {st.session_state.max_pallet} Pallet, {st.session_state.max_dus} Dus")
        
    st.write("")
    st.markdown("**JUMLAH YANG AKAN DIAMBIL:**")
    
    col_q1, col_q2 = st.columns(2)
    with col_q1: qty_pallet = st.number_input("PALLET UTUH", min_value=0, max_value=st.session_state.max_pallet, step=1, value=1 if st.session_state.max_pallet > 0 else 0)
    with col_q2: qty_dus = st.number_input("DUS PECAHAN", min_value=0, max_value=st.session_state.max_dus, step=1, value=0)
    
    st.write("")
    # TAMBAHAN: Input Catatan Operator (Untuk siapa / Kendala fisik aktual)
    catatan_operator = st.text_input("📝 CATATAN / KENDALA (Opsional)", placeholder="Cth: Untuk Line 2 / Fisik kurang 1 pallet")
    
    st.write("")
    if qty_pallet == 0 and qty_dus == 0:
        st.warning("⚠️ Masukkan minimal 1 Qty untuk dikeluarkan.")
    else:
        if st.button("🚀 KELUARKAN BARANG", type="primary", use_container_width=True):
            with st.spinner("Memproses transaksi terisolasi..."):
                
                k_pilih = st.session_state.pilih_kolom
                b_pilih = st.session_state.pilih_baris
                lot_pilih = st.session_state.pilih_lot
                
                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                transaction = db.transaction() 
                
                try:
                    # Jalankan transaksi pengambilan barang
                    data_sebelum_update = proses_outbound_transaction(transaction, doc_ref, b_pilih, lot_pilih, qty_pallet)
                    
                    # Format teks keterangan log mutasi (Menggabungkan info operator & catatan khusus)
                    teks_keterangan = f"Dieksekusi oleh: {st.session_state.operator}"
                    if catatan_operator.strip():
                        teks_keterangan += f" | Catatan: {catatan_operator.strip()}"
                    
                    # Catat log mutasi ke Firebase
                    catat_log_mutasi("OUTBOUND (Remote)", k_pilih, b_pilih, st.session_state.item, st.session_state.pilih_part, lot_pilih, qty_pallet, qty_dus, 0, teks_keterangan)
                    
                    get_all_racking_data.clear() 
                    
                    # SETUP NOTIFIKASI & BACKUP UNDO
                    st.session_state.undo_dict = {k_pilih: data_sebelum_update}
                    st.session_state.action_msg = f"{qty_pallet} Pallet dikeluarkan dari Rak {k_pilih}-{b_pilih}."
                    if catatan_operator.strip():
                        st.session_state.action_msg += f" (Catatan: {catatan_operator.strip()})"
                    st.session_state.action_status = 'DONE'
                    
                    st.toast(st.session_state.action_msg, icon="✅")
                    
                    # KEMBALI KE SLIDE 1
                    for k in ['kategori', 'gramasi', 'item', 'pilih_kolom', 'pilih_baris', 'pilih_lot', 'pilih_part', 'max_pallet', 'max_dus']:
                        st.session_state[k] = None
                    pindah_slide(1)
                    
                    st.rerun()
                    
                except ValueError as err:
                    st.error(f"❌ Transaksi Dibatalkan: {err}")
                except Exception as err:
                    st.error(f"❌ Kesalahan Sistem WMS: {err}")
