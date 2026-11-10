import streamlit as st
import pandas as pd

# ==========================================
# KONFIGURASI TAMPILAN MOBILE-FIRST (REMOTE CONTROL)
# ==========================================
st.set_page_config(page_title="WMS Outbound - Remote", layout="centered", initial_sidebar_state="collapsed")

# Injeksi CSS untuk membuat tombol raksasa dan menyembunyikan elemen bawaan Streamlit
st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        
        /* Gaya untuk tombol utama (kategori & aksi) */
        .stButton>button {
            height: 80px; 
            font-size: 20px; 
            font-weight: bold;
            border-radius: 12px;
            white-space: normal;
            word-wrap: break-word;
        }
        
        /* Gaya khusus untuk tombol list produk agar teksnya muat */
        .btn-produk>div>button {
            height: auto;
            min-height: 70px;
            font-size: 16px;
        }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# LOAD & FILTER DATA MASTER PRODUK
# ==========================================
@st.cache_data
def load_master_data():
    # Load CSV (pastikan separator disesuaikan dengan format asli, di sini pakai titik koma)
    try:
        df = pd.read_csv("Master_Produk.csv", sep=";")
    except FileNotFoundError:
        # Fallback dummy dataframe jika file belum ada di local test
        st.error("File Master_Produk.csv tidak ditemukan!")
        return pd.DataFrame(columns=['Nama Barang'])
        
    df = df.dropna(subset=['Nama Barang'])
    
    # Fungsi kategorisasi berdasarkan instruksi
    def set_kategori(nama):
        nama_str = str(nama).strip()
        if nama_str.startswith('Preform'): return 'Preform'
        elif nama_str.startswith('Cup Natural'): return 'Cup Natural'
        elif nama_str.startswith('Cup Printing'): return 'Cup Printing'
        else: return 'Lainnya' # Untuk Cup Color White, Cap Short Neck, dll
        
    df['Kategori'] = df['Nama Barang'].apply(set_kategori)
    return df

df_master = load_master_data()

# ==========================================
# STATE MANAGEMENT (SISTEM SLIDE)
# ==========================================
if 'slide' not in st.session_state:
    st.session_state.slide = 1
if 'pilih_kategori' not in st.session_state:
    st.session_state.pilih_kategori = None
if 'pilih_produk' not in st.session_state:
    st.session_state.pilih_produk = None

def pindah_slide(tujuan):
    st.session_state.slide = tujuan

# ==========================================
# SLIDE 1: KATEGORI PRODUK
# ==========================================
if st.session_state.slide == 1:
    st.title("📱 Remote Outbound")
    st.markdown("### LAKUKAN SCAN ATAU PILIH KATEGORI:")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🧪\nPREFORM", use_container_width=True):
            st.session_state.pilih_kategori = "Preform"
            pindah_slide(2)
            st.rerun()
        if st.button("📦\nLAINNYA", use_container_width=True):
            st.session_state.pilih_kategori = "Lainnya"
            pindah_slide(2)
            st.rerun()
            
    with col2:
        if st.button("🥤\nCUP NATURAL", use_container_width=True):
            st.session_state.pilih_kategori = "Cup Natural"
            pindah_slide(2)
            st.rerun()
        if st.button("🖨️\nCUP PRINTING", use_container_width=True):
            st.session_state.pilih_kategori = "Cup Printing"
            pindah_slide(2)
            st.rerun()

# ==========================================
# SLIDE 2: DAFTAR PRODUK (BERDASARKAN KATEGORI)
# ==========================================
elif st.session_state.slide == 2:
    if st.button("🔙 KEMBALI KE MENU UTAMA", type="secondary"):
        st.session_state.pilih_kategori = None
        pindah_slide(1)
        st.rerun()
        
    st.markdown(f"### PILIH VARIAN **{st.session_state.pilih_kategori.upper()}**:")
    
    # Filter dataframe sesuai kategori yang diklik
    df_filter = df_master[df_master['Kategori'] == st.session_state.pilih_kategori]
    daftar_nama_produk = df_filter['Nama Barang'].tolist()
    
    if not daftar_nama_produk:
        st.info(f"Tidak ada produk di kategori {st.session_state.pilih_kategori}.")
    else:
        # Render deretan tombol untuk setiap produk (scrolling ke bawah)
        st.markdown('<div class="btn-produk">', unsafe_allow_html=True)
        for nama_produk in daftar_nama_produk:
            # Gunakan clean name untuk tampilan tombol
            if st.button(f"{nama_produk}", use_container_width=True):
                st.session_state.pilih_produk = nama_produk
                pindah_slide(3)
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

# ==========================================
# SLIDE 3: KONFIRMASI & AMBIL BARANG
# ==========================================
elif st.session_state.slide == 3:
    if st.button("🔙 GANTI PRODUK", type="secondary"):
        st.session_state.pilih_produk = None
        pindah_slide(2)
        st.rerun()
        
    st.markdown("### TARGET LOKASI (FIFO)")
    
    # Kotak Informasi Visual
    with st.container(border=True):
        st.success(f"**PRODUK TARGET:**\n\n{st.session_state.pilih_produk}")
        
        # Area Backend (Sementara di-Mockup)
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.metric(label="RUTE LORONG", value="RAK F - 12")
        with col_m2:
            st.metric(label="LOT TERTUA", value="LOT-099")
            
    st.write("")
    st.markdown("### JUMLAH DIAMBIL:")
    qty = st.number_input("Total Pallet", min_value=1, step=1, value=1)
    
    st.write("")
    if st.button("🚀 KELUARKAN SEKARANG", type="primary", use_container_width=True):
        # Nanti script sinkronisasi ke Firebase diletakkan di sini
        
        st.toast("Data Outbound Berhasil Disimpan!", icon="✅")
        # Reset alur kembali ke awal
        st.session_state.pilih_kategori = None
        st.session_state.pilih_produk = None
        pindah_slide(1)
        import time
        time.sleep(1)
        st.rerun()
