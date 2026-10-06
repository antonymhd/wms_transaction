import streamlit as st
import pandas as pd
from datetime import datetime
import requests

# 1. Konfigurasi Halaman Khusus Transaksi
st.set_page_config(
    page_title="WMS - Form Transaksi Gudang",
    page_icon="📝",
    layout="centered"
)

# Ganti dengan URL Web App Apps Script Anda yang berakhiran /exec
WEB_APP_URL = "https://script.google.com/macros/s/AKfycbxt87dT2QAotNQyU7kQlBxFObxH37eH6uqqWwjJTBNgowk-pcNON4B6BOTWpwvkr2PYfg/exec"

# 2. Tarik data master untuk pilihan produk dan rak
@st.cache_data(ttl=30)
def load_data():
    try:
        sheet_url = "https://docs.google.com/spreadsheets/d/1rPODgznxi5QxPWk6paIK0-SsTPwIByJPcGYZ8YAUEwc/export?format=csv&gid=0"
        df = pd.read_csv(sheet_url)
        df.columns = df.columns.astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"⚠ Gagal memuat data master! Error: {e}")
        st.stop()

@st.cache_data(ttl=5)
def load_log():
    try:
        log_url = "https://docs.google.com/spreadsheets/d/1rPODgznxi5QxPWk6paIK0-SsTPwIByJPcGYZ8YAUEwc/export?format=csv&sheet=Log%20Transaksi"
        return pd.read_csv(log_url)
    except Exception:
        return pd.DataFrame(columns=["Timestamp", "Tipe Transaksi", "Nama Barang", "Rak", "Qty"])

df = load_data()
product_col = 'Nama Produk' if 'Nama Produk' in df.columns else 'Nama Barang'
part_col = 'Part Number' if 'Part Number' in df.columns else 'Part Number'

# Membuat mapping unik antara Part Number dan Nama Produk
product_mapping = df[[part_col, product_col]].dropna().drop_duplicates()
list_part_number = sorted(product_mapping[part_col].astype(str).tolist())

kolom_list = ['G', 'F', 'E', 'D', 'C', 'B', 'A']
baris_list = list(range(1, 29))

def is_rack_exist(kolom, baris):
    if kolom == 'F' and baris < 5: return False
    if kolom == 'G' and baris < 17: return False
    return True

daftar_rak = [f"{k}{b}" for k in kolom_list for b in baris_list if is_rack_exist(k, b)]

# =========================================================================
# TAMPILAN FORM TRANSAKSI
# =========================================================================
st.title("📝 Form Transaksi Inbound / Outbound")
st.caption("Gunakan halaman ini khusus untuk pencatatan pergerakan pallet di lapangan.")
st.divider()

with st.form("form_wms_transaksi"):
    tipe_trx = st.selectbox("Jenis Pergerakan:", ["INBOUND (Barang Masuk) ⬇️", "OUTBOUND (Barang Keluar) ⬆️"])
    
    # Dropdown Part Number & Auto-fill Nama Produk
    selected_part = st.selectbox("Pilih Part Number:", options=list_part_number)
    matched_row = product_mapping[product_mapping[part_col].astype(str) == selected_part]
    nama_produk = matched_row[product_col].values[0] if not matched_row.empty else ""
    
    st.text_input("Nama Produk (Auto-filled):", value=nama_produk, disabled=True)
    
    lot_number = st.text_input("Lot Number / No Lot:", placeholder="Masukkan nomor lot jika ada")
    
    c1, c2 = st.columns(2)
    with c1:
        pilih_kolom = st.selectbox("Kolom Rak:", options=kolom_list)
    with c2:
        pilih_baris = st.selectbox("Baris Rak:", options=baris_list)
        
    lokasi_rak = f"{pilih_kolom}{pilih_baris}"
    
    c3, c4 = st.columns(2)
    with c3:
        pallet_dari = st.number_input("Dari Pallet ke-:", min_value=1, step=1, value=1)
    with c4:
        pallet_sampai = st.number_input("Sampai Pallet ke-:", min_value=1, step=1, value=1)
        
    # Hitung Qty otomatis dari rentang pallet
    qty_total = (pallet_sampai - pallet_dari) + 1
    if qty_total < 1: qty_total = 1
    
    st.info(f"📦 **Total Qty Pallet yang diproses:** {qty_total} Pallet (Di Rak **{lokasi_rak}**)")
    
    keterangan = st.text_area("Keterangan Tambahan (Opsional):", placeholder="Catatan kondisi barang atau keterangan lainnya...")
    
    submitted = st.form_submit_button("💾 Simpan Transaksi ke Sistem", type="primary", use_container_width=True)
    
    if submitted:
        tipe_clean = "IN" if "INBOUND" in tipe_trx else "OUT"
        payload = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tipe": tipe_clean,
            "barang": f"{selected_part} - {nama_produk} (Lot: {lot_number})",
            "rak": lokasi_rak,
            "qty": qty_total
        }
        
        try:
            response = requests.post(WEB_APP_URL, json=payload)
            if response.status_code == 200:
                st.success(f"✅ Transaksi {tipe_clean} Berhasil Disimpan! ({qty_total} Pallet di Rak {lokasi_rak})")
                st.cache_data.clear()
            else:
                st.error("❌ Gagal mengirim data ke Google Sheets.")
        except Exception as e:
            st.error(f"Terjadi kesalahan koneksi: {e}")

st.divider()
st.subheader("📜 Riwayat Transaksi Lapangan Terbaru")
log_df = load_log()
if not log_df.empty:
    st.dataframe(log_df.tail(5).iloc[::-1], use_container_width=True, hide_index=True)
else:
    st.info("Belum ada riwayat transaksi.")