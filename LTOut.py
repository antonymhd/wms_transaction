import streamlit as st
import pandas as pd
import re

# ==========================================
# KONFIGURASI TAMPILAN MOBILE-FIRST (REMOTE)
# ==========================================
st.set_page_config(page_title="WMS Outbound - Remote", layout="centered", initial_sidebar_state="collapsed")

# Injeksi CSS meniru gaya Dark Mode UI dan Button Raksasa
st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        
        /* Tombol Utama (Navigasi Slide & Pilihan) */
        .stButton>button {
            min-height: 75px; 
            height: auto;
            font-size: 16px; 
            font-weight: 600;
            border-radius: 10px;
            border: 1px solid #444;
            background-color: #222;
            color: #fff;
            white-space: pre-wrap; /* Penting agar \n bisa membuat baris baru di tombol */
            word-wrap: break-word;
            text-align: left; /* Teks rata kiri untuk info Lot & Lokasi */
            padding: 15px;
            transition: all 0.2s ease-in-out;
        }
        
        .stButton>button:active {
            background-color: #444;
            border-color: #fff;
        }
        
        /* Tombol Eksekusi Akhir (Primary) dibuat center */
        button[kind="primary"] {
            background-color: #1E88E5 !important;
            border-color: #1E88E5 !important;
            font-size: 18px;
            text-align: center !important;
        }
        
        /* Styling Progress Bar Teks */
        .progress-container {
            font-size: 14px;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 1px solid #333;
            text-align: center;
        }
        .step-active { color: #42A5F5; font-weight: bold; }
        .step-inactive { color: #666; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# FUNGSI EKSTRAKSI & LOAD MASTER DATA
# ==========================================
def ekstrak_gramasi(nama_produk):
    match = re.search(r'(\d+[.,]\d+)\s*(?:gr|GR)', str(nama_produk), re.IGNORECASE)
    if match:
        angka = match.group(1).replace(',', '.')
        return f"{float(angka):.2f} gr"
    return "Lainnya"

def set_kategori(nama):
    nama_str = str(nama).strip().upper()
    if nama_str.startswith('PREFORM'): return 'PREFORM'
    elif nama_str.startswith('CUP NATURAL'): return 'CUP NATURAL'
    elif nama_str.startswith('CUP PRINTING'): return 'CUP PRINTING'
    elif nama_str.startswith('CUP COLOR'): return 'CUP COLOR'
    elif nama_str.startswith('CAP'): return 'CAP'
    else: return 'LAINNYA'

@st.cache_data
def load_master_data():
    try:
        df = pd.read_csv("Master_Produk.csv", sep=";")
        df = df.dropna(subset=['Nama Barang'])
        df['Kategori'] = df['Nama Barang'].apply(set_kategori)
        df['Gramasi'] = df['Nama Barang'].apply(ekstrak_gramasi)
        return df
    except Exception as e:
        st.error(f"Gagal memuat Master_Produk.csv: {e}")
        return pd.DataFrame(columns=['Nama Barang', 'Kategori', 'Gramasi'])

df_master = load_master_data()

# ==========================================
# STATE MANAGEMENT
# ==========================================
keys = ['slide', 'kategori', 'gramasi', 'item', 'pilih_lokasi', 'pilih_lot', 'max_pallet', 'max_dus']
for key in keys:
    if key not in st.session_state:
        st.session_state[key] = 1 if key == 'slide' else None

def pindah_slide(tujuan):
    st.session_state.slide = tujuan

# Visualisasi Progress Bar
def render_progress():
    steps = ["Kategori", "Gramasi", "Item", "Lokasi", "Selesai"]
    current = st.session_state.slide
    
    html = "<div class='progress-container'>"
    for i, s in enumerate(steps, 1):
        if i == current:
            html += f"<span class='step-active'>[{i}] {s}</span>"
        else:
            html += f"<span class='step-inactive'>[{i}]</span>"
        if i < len(steps): html += " ➔ "
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

# Render Progress Bar di setiap slide
render_progress()

# ==========================================
# SLIDE 1: JENIS PRODUK (KATEGORI)
# ==========================================
if st.session_state.slide == 1:
    st.markdown("### 🎛️ Remote Outbound")
    st.caption("PILIH KATEGORI PRODUK:")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🧪 PREFORM", use_container_width=True):
            st.session_state.kategori = "PREFORM"
            pindah_slide(2); st.rerun()
        if st.button("🎨 CUP COLOR", use_container_width=True):
            st.session_state.kategori = "CUP COLOR"
            pindah_slide(2); st.rerun()
        if st.button("🧢 CAP (TUTUP)", use_container_width=True):
            st.session_state.kategori = "CAP"
            pindah_slide(2); st.rerun()
            
    with col2:
        if st.button("🥤 CUP NATURAL", use_container_width=True):
            st.session_state.kategori = "CUP NATURAL"
            pindah_slide(2); st.rerun()
        if st.button("🖨️ CUP PRINTING", use_container_width=True):
            st.session_state.kategori = "CUP PRINTING"
            pindah_slide(2); st.rerun()

# ==========================================
# SLIDE 2: PILIH GRAMASI
# ==========================================
elif st.session_state.slide == 2:
    if st.button("🔙 KEMBALI"):
        pindah_slide(1); st.rerun()
        
    st.markdown(f"### VARIAN GRAMASI **{st.session_state.kategori}**:")
    
    df_kat = df_master[df_master['Kategori'] == st.session_state.kategori]
    list_gramasi = sorted(df_kat['Gramasi'].unique())
    
    for i in range(0, len(list_gramasi), 2):
        cols = st.columns(2)
        with cols[0]:
            if st.button(f"⚖️ {list_gramasi[i]}", key=f"g_{i}", use_container_width=True):
                st.session_state.gramasi = list_gramasi[i]
                pindah_slide(3); st.rerun()
        if i + 1 < len(list_gramasi):
            with cols[1]:
                if st.button(f"⚖️ {list_gramasi[i+1]}", key=f"g_{i+1}", use_container_width=True):
                    st.session_state.gramasi = list_gramasi[i+1]
                    pindah_slide(3); st.rerun()

# ==========================================
# SLIDE 3: PILIH ITEM
# ==========================================
elif st.session_state.slide == 3:
    if st.button("🔙 GANTI GRAMASI"):
        pindah_slide(2); st.rerun()
        
    st.markdown(f"### PILIH ITEM:")
    st.caption(f"{st.session_state.kategori} ({st.session_state.gramasi})")
    
    df_item = df_master[(df_master['Kategori'] == st.session_state.kategori) & 
                        (df_master['Gramasi'] == st.session_state.gramasi)]
    list_item = df_item['Nama Barang'].tolist()
    
    for item in list_item:
        if st.button(f"📦 {item}", use_container_width=True):
            st.session_state.item = item
            pindah_slide(4); st.rerun()

# ==========================================
# SLIDE 4: PILIH LOKASI & LOT (BUTTON LIST)
# ==========================================
elif st.session_state.slide == 4:
    if st.button("🔙 GANTI ITEM"):
        pindah_slide(3); st.rerun()
        
    st.markdown("### PILIH LOKASI & LOT BARANG")
    st.info(f"{st.session_state.item}")
    
    # ----------------------------------------------------
    # MOCKUP DATA BACKEND (Ganti dengan Query Firebase yang
    # memfilter ketersediaan berdasarkan st.session_state.item)
    # ----------------------------------------------------
    mock_ketersediaan = [
        {"lokasi": "F-12", "lot": "LOT-1102", "pallet": 14, "dus": 0, "fifo": "URUTAN 1 (PALING TUA)"},
        {"lokasi": "A-05", "lot": "LOT-1105", "pallet": 8, "dus": 2, "fifo": "URUTAN 2"},
        {"lokasi": "C-21", "lot": "LOT-1109", "pallet": 20, "dus": 0, "fifo": "URUTAN 3 (TERBARU)"}
    ]
    
    # Render setiap lokasi sebagai Button Raksasa
    for data in mock_ketersediaan:
        # Format teks di dalam button memanfaatkan \n (newline) dari CSS white-space: pre-wrap
        btn_label = f"📍 RAK {data['lokasi']} | 🏷️ {data['lot']}\n"
        btn_label += f"📦 Tersedia: {data['pallet']} Pallet, {data['dus']} Dus\n"
        btn_label += f"⭐ {data['fifo']}"
        
        # Jika tombol lokasi ini diklik, simpan datanya dan pindah ke Slide Eksekusi
        if st.button(btn_label, use_container_width=True):
            st.session_state.pilih_lokasi = data['lokasi']
            st.session_state.pilih_lot = data['lot']
            st.session_state.max_pallet = data['pallet']
            st.session_state.max_dus = data['dus']
            pindah_slide(5)
            st.rerun()

# ==========================================
# SLIDE 5: INPUT QTY & EKSEKUSI (FINAL)
# ==========================================
elif st.session_state.slide == 5:
    col_back, _ = st.columns([1, 2])
    if col_back.button("🔙 GANTI LOKASI"):
        pindah_slide(4); st.rerun()
        
    st.markdown("### KONFIRMASI PENGELUARAN")
    
    # Summary Kartu Info
    with st.container(border=True):
        st.markdown(f"**Item:** {st.session_state.item}")
        st.markdown(f"**Rak:** {st.session_state.pilih_lokasi} | **Lot:** {st.session_state.pilih_lot}")
        st.caption(f"Stok Fisik: {st.session_state.max_pallet} Pallet, {st.session_state.max_dus} Dus")
        
    st.write("")
    st.markdown("**JUMLAH YANG AKAN DIAMBIL:**")
    
    # Input Pallet dan Dus secara terpisah berdasarkan maksimal ketersediaan di rak tsb
    col_q1, col_q2 = st.columns(2)
    with col_q1:
        qty_pallet = st.number_input("PALLET", min_value=0, max_value=st.session_state.max_pallet, step=1, value=1 if st.session_state.max_pallet > 0 else 0)
    with col_q2:
        qty_dus = st.number_input("DUS", min_value=0, max_value=st.session_state.max_dus, step=1, value=0)
    
    st.write("")
    if qty_pallet == 0 and qty_dus == 0:
        st.warning("⚠️ Masukkan minimal 1 Qty untuk dikeluarkan.")
    else:
        # Tombol Final
        if st.button("🚀 KELUARKAN BARANG", type="primary", use_container_width=True):
            # ----------------------------------------------------
            # SCRIPT FIREBASE UPDATE STOK DILETAKKAN DI SINI
            # Gunakan st.session_state.pilih_lokasi untuk menentukan doc referensi
            # ----------------------------------------------------
            st.balloons()
            st.success(f"Berhasil! {qty_pallet} Pallet dan {qty_dus} Dus dikeluarkan dari Rak {st.session_state.pilih_lokasi}.")
            
            # Reset alur kembali ke halaman awal (Slide 1)
            for k in ['kategori', 'gramasi', 'item', 'pilih_lokasi', 'pilih_lot', 'max_pallet', 'max_dus']:
                st.session_state[k] = None
            pindah_slide(1)
            
            # Beri waktu 2 detik agar operator bisa membaca pesan sukses
            import time
            time.sleep(2)
            st.rerun()
