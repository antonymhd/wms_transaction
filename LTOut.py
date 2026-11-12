import streamlit as st
import pandas as pd
import re
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================================
# 1. KONFIGURASI TAMPILAN & CSS ADAPTIF (GELAP/TERANG)
# ==========================================================
st.set_page_config(page_title="WMS Outbound - Remote", layout="centered", initial_sidebar_state="collapsed")

# Menghapus hardcode background-color agar Streamlit mengikuti Tema Sistem (Gelap/Terang)
st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        
        /* Tombol Utama (Adaptif terhadap Tema Streamlit) */
        .stButton>button {
            min-height: 75px; 
            height: auto;
            font-size: 17px; /* Sedikit diperbesar */
            font-weight: 600;
            border-radius: 12px;
            white-space: pre-wrap; 
            word-wrap: break-word;
            text-align: left; 
            padding: 16px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            transition: all 0.15s ease-in-out;
        }
        
        /* Tombol Eksekusi Akhir */
        button[kind="primary"] {
            background-color: #2563EB !important;
            border-color: #3B82F6 !important;
            color: white !important;
            font-size: 18px !important;
            text-align: center !important;
        }
        
        /* Tombol Undo */
        .btn-undo > button {
            background-color: #DC2626 !important;
            border-color: #EF4444 !important;
            color: white !important;
            text-align: center !important;
        }
        
        /* Progress Bar */
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
        
        /* Info Box Header */
        .db-info-box {
            background-color: var(--secondary-background-color);
            border: 1px solid var(--border-color);
            padding: 12px 16px;
            border-radius: 10px;
            margin-bottom: 15px;
        }
        
        /* Memperbesar Font Gramasi */
        .btn-gramasi > button {
            font-size: 20px !important;
            text-align: center !important;
        }
    </style>
""", unsafe_allow_html=True)

# ==========================================================
# 2. INISIALISASI FIREBASE & HELPER
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
# 3. FIREBASE TRANSACTIONS
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
# 4. MASTER PRODUK & PARSING STRING WARNA
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
    """Mendeteksi warna (teks setelah koma terakhir) dan mencetaknya tebal"""
    parts = str(nama_barang).rsplit(',', 1)
    if len(parts) == 2:
        return f"{parts[0]}, **{parts[1].strip()}**"
    return str(nama_barang)

@st.cache_data(ttl=3600)
def load_master_data():
    try:
        # PENTING: Pastikan kolom 'Part Number' ditarik jika ada di Master_Produk.csv
        df = pd.read_csv("Master_Produk.csv", sep=";", dtype=str)
        df = df.dropna(subset=['Nama Barang'])
        df['Kategori'] = df['Nama Barang'].apply(set_kategori)
        df['Gramasi'] = df['Nama Barang'].apply(ekstrak_gramasi)
        
        # Buat kolom dummy jika Part Number tidak ada agar skrip tidak error
        if 'Part Number' not in df.columns:
            df['Part Number'] = ""
            
        return df
    except Exception: return pd.DataFrame(columns=['Nama Barang', 'Kategori', 'Gramasi', 'Part Number'])

df_master = load_master_data()

keys_state = ['slide', 'kategori', 'gramasi', 'item', 'pilih_kolom', 'pilih_baris', 'pilih_lot', 'pilih_part', 'max_pallet', 'max_dus', 'operator', 'action_msg', 'action_status', 'undo_dict']
for key in keys_state:
    if key not in st.session_state:
        if key == 'operator': st.session_state[key] = "Operator 1 (Default)"
        elif key == 'slide': st.session_state[key] = 1
        elif key == 'undo_dict': st.session_state[key] = {}
        elif key == 'action_msg': st.session_state[key] = ""
        else: st.session_state[key] = None

def pindah_slide(tujuan): st.session_state.slide = tujuan

# HEADER STATUS 
st.markdown(f"""
    <div class="db-info-box">
        <div style="font-size: 13px; opacity: 0.8; margin-bottom: 4px;">
            📡 Database WMS: <strong style="color: #3B82F6;">{NAMA_DATABASE}</strong>
        </div>
        <div style="font-size: 13px; opacity: 0.8;">
            👤 Operator Bertugas: <strong>{st.session_state.operator}</strong>
        </div>
    </div>
""", unsafe_allow_html=True)

# ==========================================================
# 5. NOTIFIKASI & SISTEM UNDO
# ==========================================================
if st.session_state.action_status == 'DONE':
    with st.container(border=True):
        st.success(f"✅ **BERHASIL KELUAR:** {st.session_state.action_msg}")
        c_undo1, c_undo2 = st.columns(2)
        with c_undo1:
            st.markdown('<div class="btn-undo">', unsafe_allow_html=True)
            if st.button("⏪ BATALKAN (UNDO)", use_container_width=True):
                with st.spinner("Memulihkan posisi stok..."):
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
        st.warning("⏪ **Transaksi dibatalkan.** Stok kembali ke posisi semula.")
        if st.button("TUTUP PESAN", use_container_width=True):
            st.session_state.action_status = None
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
# SLIDE 3: ITEM SPESIFIK (DETEKSI PACKAGING & BOLD WARNA)
# ==========================================================
elif st.session_state.slide == 3:
    if st.button("🔙 KEMBALI"): pindah_slide(2); st.rerun()
    st.markdown(f"### Pilih Item Spesifik")
    st.caption(f"{st.session_state.kategori} | {st.session_state.gramasi}")
    
    df_item = df_master[(df_master['Kategori'] == st.session_state.kategori) & (df_master['Gramasi'] == st.session_state.gramasi)]
    
    for idx, row in df_item.iterrows():
        nama_brg = row['Nama Barang']
        part_num = str(row['Part Number']).strip().upper()
        
        # Identifikasi tipe kemasan berdasarkan Part Number
        # Mengubah akhiran D/G menjadi Dus, C menjadi Sac
        tipe_kemasan = ""
        if part_num:
            akhiran = part_num[-1]
            if akhiran in ['D', 'G']:
                tipe_kemasan = "[📦 DUS] "
            elif akhiran == 'C':
                tipe_kemasan = "[👝 SAC] "
                
        # Format nama barang agar warnanya tercetak tebal
        nama_terformat = format_nama_warna_bold(nama_brg)
        
        # Label final tombol
        btn_label = f"{tipe_kemasan}{nama_terformat}"
        
        if st.button(btn_label, use_container_width=True, key=f"btn_item_{idx}"): 
            st.session_state.item = nama_brg
            pindah_slide(4)
            st.rerun()

# ==========================================================
# SLIDE 4: PENCARIAN LOKASI STOK (FIFO)
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
                    fifo_label = "🌟 REKOMENDASI UTAMA" if idx == 0 else f"⭐ URUTAN {idx + 1}"
                    catatan_label = f"\n🔖 Catatan: {row['Catatan']}" if row['Catatan'] else ""
                    
                    btn_label = f"📍 RAK {row['Kolom']}-{row['Baris']}  |  🏷️ Lot: {row['Lot Number']}\n"
                    btn_label += f"Stok: {row['Pallet']} Pallet, {row['Dus']} Dus\n"
                    btn_label += f"{fifo_label}{catatan_label}"
                    
                    if st.button(btn_label, use_container_width=True, key=f"btn_loc_{idx}"):
                        st.session_state.pilih_kolom = row['Kolom']; st.session_state.pilih_baris = row['Baris']
                        st.session_state.pilih_lot = row['Lot Number']; st.session_state.pilih_part = row['Part']
                        st.session_state.max_pallet = row['Pallet']; st.session_state.max_dus = row['Dus']
                        pindah_slide(5); st.rerun()

# ==========================================================
# SLIDE 5: KONFIRMASI & EKSEKUSI PENGELUARAN
# ==========================================================
elif st.session_state.slide == 5:
    if st.button("🔙 BATAL"): pindah_slide(4); st.rerun()
        
    st.markdown("### Konfirmasi Pengeluaran")
    with st.container(border=True):
        st.markdown(format_nama_warna_bold(st.session_state.item))
        st.markdown(f"**Tujuan:** Rak {st.session_state.pilih_kolom}-{st.session_state.pilih_baris} (Lot {st.session_state.pilih_lot})")
        st.caption(f"Maksimal Tersedia: {st.session_state.max_pallet} Pallet, {st.session_state.max_dus} Dus")
        
    st.write("")
    
    col_q1, col_q2 = st.columns(2)
    with col_q1: qty_pallet = st.number_input("PALLET UTUH", min_value=0, max_value=st.session_state.max_pallet, step=1, value=1 if st.session_state.max_pallet > 0 else 0)
    with col_q2: qty_dus = st.number_input("DUS/SAC", min_value=0, max_value=st.session_state.max_dus, step=1, value=0)
    
    st.write("")
    catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Untuk Line 2 / Stok kurang 1")
    
    st.write("")
    if qty_pallet == 0 and qty_dus == 0:
        st.warning("⚠️ Masukkan minimal 1 Qty untuk dikeluarkan.")
    else:
        if st.button("🚀 KELUARKAN SEKARANG", type="primary", use_container_width=True):
            with st.spinner("Memproses ke database..."):
                
                k_pilih = st.session_state.pilih_kolom
                b_pilih = st.session_state.pilih_baris
                lot_pilih = st.session_state.pilih_lot
                
                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                transaction = db.transaction() 
                
                try:
                    data_sebelum_update = proses_outbound_transaction(transaction, doc_ref, b_pilih, lot_pilih, qty_pallet)
                    
                    teks_keterangan = f"Dieksekusi oleh: {st.session_state.operator}"
                    if catatan_operator.strip():
                        teks_keterangan += f" | Catatan: {catatan_operator.strip()}"
                    
                    catat_log_mutasi("OUTBOUND (Remote)", k_pilih, b_pilih, st.session_state.item, st.session_state.pilih_part, lot_pilih, qty_pallet, qty_dus, 0, teks_keterangan)
                    
                    get_all_racking_data.clear() 
                    
                    st.session_state.undo_dict = {k_pilih: data_sebelum_update}
                    st.session_state.action_msg = f"{qty_pallet} Pallet dikeluarkan dari Rak {k_pilih}-{b_pilih}."
                    if catatan_operator.strip():
                        st.session_state.action_msg += f" (Catatan: {catatan_operator.strip()})"
                    st.session_state.action_status = 'DONE'
                    
                    st.toast(st.session_state.action_msg, icon="✅")
                    
                    for k in ['kategori', 'gramasi', 'item', 'pilih_kolom', 'pilih_baris', 'pilih_lot', 'pilih_part', 'max_pallet', 'max_dus']:
                        st.session_state[k] = None
                    pindah_slide(1)
                    
                    st.rerun()
                    
                except ValueError as err:
                    st.error(f"❌ Transaksi Dibatalkan: {err}")
                except Exception as err:
                    st.error(f"❌ Kesalahan Sistem WMS: {err}")
