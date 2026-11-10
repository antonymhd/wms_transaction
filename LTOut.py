import streamlit as st
import pandas as pd
import re

# ==========================================
# KONFIGURASI TAMPILAN MOBILE-FIRST (REMOTE)
# ==========================================
st.set_page_config(page_title="WMS Outbound - Remote", layout="centered", initial_sidebar_state="collapsed")

# Injeksi CSS meniru gaya Dark Mode UI "Remote Outbound"
st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        
        /* Tombol Utama (Navigasi Slide) */
        .stButton>button {
            height: 75px; 
            font-size: 16px; 
            font-weight: 600;
            border-radius: 10px;
            border: 1px solid #444;
            background-color: #222;
            color: #fff;
            white-space: normal;
            word-wrap: break-word;
            transition: all 0.2s ease-in-out;
        }
        
        .stButton>button:active {
            background-color: #444;
            border-color: #fff;
        }
        
        /* Tombol Aksi Akhir (Primary) */
        button[kind="primary"] {
            background-color: #1E88E5 !important;
            border-color: #1E88E5 !important;
            font-size: 18px;
        }
        
        /* Merapikan margin metric untuk tampilan Lokasi */
        div[data-testid="stMetricValue"] {
            font-size: 24px !important;
        }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# FUNGSI EKSTRAKSI & LOAD MASTER DATA
# ==========================================
def ekstrak_gramasi(nama_produk):
    """Mencari pola gramasi seperti '3.5gr', '2,90gr', atau '1.3 GR' di dalam string"""
    match = re.search(r'(\d+[.,]\d+)\s*(?:gr|GR)', str(nama_produk), re.IGNORECASE)
    if match:
        # Standarisasi format koma menjadi titik agar mudah di-sort
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
# STATE MANAGEMENT (SISTEM SLIDE)
# ==========================================
for key in ['slide', 'kategori', 'gramasi', 'item']:
    if key not in st.session_state:
        st.session_state[key] = 1 if key == 'slide' else None

def pindah_slide(tujuan):
    st.session_state.slide = tujuan

# ==========================================
# SLIDE 1: JENIS PRODUK (KATEGORI)
# ==========================================
if st.session_state.slide == 1:
    st.markdown("### 🎛️ Remote Outbound")
    st.caption("LAKUKAN SCAN ATAU PILIH KATEGORI:")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🧪 PREFORM", use_container_width=True):
            st.session_state.kategori = "PREFORM"
            pindah_slide(2)
            st.rerun()
        if st.button("🎨 CUP COLOR", use_container_width=True):
            st.session_state.kategori = "CUP COLOR"
            pindah_slide(2)
            st.rerun()
        if st.button("🧢 CAP (TUTUP)", use_container_width=True):
            st.session_state.kategori = "CAP"
            pindah_slide(2)
            st.rerun()
            
    with col2:
        if st.button("🥤 CUP NATURAL", use_container_width=True):
            st.session_state.kategori = "CUP NATURAL"
            pindah_slide(2)
            st.rerun()
        if st.button("🖨️ CUP PRINTING", use_container_width=True):
            st.session_state.kategori = "CUP PRINTING"
            pindah_slide(2)
            st.rerun()

# ==========================================
# SLIDE 2: PILIH GRAMASI
# ==========================================
elif st.session_state.slide == 2:
    col_back, _ = st.columns([1, 2])
    if col_back.button("🔙 KEMBALI"):
        pindah_slide(1)
        st.rerun()
        
    st.markdown(f"### VARIAN GRAMASI **{st.session_state.kategori}**:")
    
    # Ambil list gramasi unik berdasarkan kategori terpilih
    df_kat = df_master[df_master['Kategori'] == st.session_state.kategori]
    list_gramasi = sorted(df_kat['Gramasi'].unique())
    
    if not list_gramasi:
        st.info("Data gramasi kosong untuk kategori ini.")
    
    # Render tombol gramasi secara dinamis (2 kolom)
    for i in range(0, len(list_gramasi), 2):
        cols = st.columns(2)
        with cols[0]:
            if st.button(f"⚖️ {list_gramasi[i]}", key=f"g_{i}", use_container_width=True):
                st.session_state.gramasi = list_gramasi[i]
                pindah_slide(3)
                st.rerun()
        if i + 1 < len(list_gramasi):
            with cols[1]:
                if st.button(f"⚖️ {list_gramasi[i+1]}", key=f"g_{i+1}", use_container_width=True):
                    st.session_state.gramasi = list_gramasi[i+1]
                    pindah_slide(3)
                    st.rerun()

# ==========================================
# SLIDE 3: PILIH ITEM (PRODUK SPESIFIK)
# ==========================================
elif st.session_state.slide == 3:
    col_back, _ = st.columns([1, 2])
    if col_back.button("🔙 GANTI GRAMASI"):
        pindah_slide(2)
        st.rerun()
        
    st.markdown(f"### ITEM: **{st.session_state.kategori} ({st.session_state.gramasi})**")
    
    # Filter berdasarkan kategori dan gramasi
    df_item = df_master[(df_master['Kategori'] == st.session_state.kategori) & 
                        (df_master['Gramasi'] == st.session_state.gramasi)]
    list_item = df_item['Nama Barang'].tolist()
    
    for item in list_item:
        if st.button(f"📦 {item}", use_container_width=True):
            st.session_state.item = item
            pindah_slide(4)
            st.rerun()

# ==========================================
# SLIDE 4: DETAIL FIFO, LOKASI & EKSEKUSI
# ==========================================
elif st.session_state.slide == 4:
    col_back, _ = st.columns([1, 2])
    if col_back.button("🔙 GANTI ITEM"):
        pindah_slide(3)
        st.rerun()
        
    # Mockup Data Backend (Nanti diganti query ke Firebase)
    mock_rak = "F"
    mock_baris = 12
    mock_stok = 14
    mock_lot = "LOT-1102"
    
    st.markdown("### KONFIRMASI PENGELUARAN")
    
    with st.container(border=True):
        st.markdown(f"**{st.session_state.item}**")
        st.divider()
        st.caption("📍 TARGET LOKASI (URUTAN FIFO):")
        
        c1, c2 = st.columns(2)
        c1.metric("KOLOM-BARIS", f"{mock_rak} - {mock_baris}")
        c2.metric("SISA PALLET", f"{mock_stok}")
        
        st.info(f"Target Lot Tertua: **{mock_lot}**")
        
    st.write("")
    qty = st.number_input("JUMLAH PALLET DIAMBIL:", min_value=1, max_value=mock_stok, step=1, value=1)
    
    st.write("")
    if st.button("🚀 KELUARKAN BARANG", type="primary", use_container_width=True):
        # ----------------------------------------------------
        # SCRIPT FIREBASE UPDATE STOK DILETAKKAN DI SINI NANTI
        # ----------------------------------------------------
        st.balloons()
        st.success(f"Berhasil: {qty} Pallet {st.session_state.item} dikeluarkan dari {mock_rak}-{mock_baris}")
        
        # Reset alur kembali ke halaman awal
        st.session_state.kategori = None
        st.session_state.gramasi = None
        st.session_state.item = None
        pindah_slide(1)
        
        import time
        time.sleep(1.5)
        st.rerun()
