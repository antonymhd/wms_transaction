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

# Mapping unik antara Nama Produk dan Part Number
product_mapping = df[[product_col, part_col]].dropna().drop_duplicates()
list_nama_produk = sorted(product_mapping[product_col].astype(str).tolist())

baris_list = list(range(1, 29)) # Baris 1 sampai 28

# =========================================================================
# TAMPILAN FORM TRANSAKSI
# =========================================================================
st.title("📝 Form Transaksi Inbound / Outbound")
st.caption("Pencatatan pergerakan pallet, quantity, dan rentang slot rak gudang.")
st.divider()

# =========================================================================
# BAGIAN 1: INPUT TANPA FORM (AGAR DINAMIS/INTERAKTIF)
# =========================================================================
tipe_trx = st.selectbox("Jenis Pergerakan:", ["INBOUND (Barang Masuk) ⬇️", "OUTBOUND (Barang Keluar) ⬆️"])
is_inbound = "INBOUND" in tipe_trx

# Dropdown Nama Produk & Auto-fill Part Number
selected_product = st.selectbox("Pilih Nama Produk:", options=list_nama_produk)
matched_row = product_mapping[product_mapping[product_col].astype(str) == selected_product]
part_number = matched_row[part_col].values[0] if not matched_row.empty else ""

st.text_input("Part Number (Auto-filled):", value=part_number, disabled=True)

lot_number = st.text_input("No. Lot / Lot Number:", placeholder="Masukkan nomor lot...")

# =========================================================================
# LOGIKA LOKASI RAK & KAPASITAS (DINAMIS SEKARANG)
# =========================================================================
st.markdown("### 📍 Tentukan Lokasi Rak")

# Memilih baris akan langsung meng-update variabel di bawahnya tanpa perlu tombol submit
pilih_baris = st.selectbox("Pilih Baris Rak (1 - 28):", options=baris_list)

# Tentukan Kolom yang aktif berdasarkan Baris yang dipilih
if pilih_baris <= 4:
    kolom_tersedia = ['A', 'B', 'C', 'D', 'E']
    info_kolom = "Baris 1–4 (Hanya Kolom A - E)"
elif pilih_baris <= 16:
    kolom_tersedia = ['A', 'B', 'C', 'D', 'E', 'F']
    info_kolom = "Baris 5–16 (Hanya Kolom A - F)"
else:
    kolom_tersedia = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
    info_kolom = "Baris 17–28 (Kolom A - G Lengkap)"
    
pilih_kolom = st.selectbox(f"Pilih Kolom Rak ({info_kolom}):", options=kolom_tersedia)
lokasi_rak = f"{pilih_kolom}{pilih_baris}"

# Tentukan Kapasitas Maksimal Pallet berdasarkan Baris
# Baris 1-16 = 31 Pallet | Baris 17-28 = 44 Pallet
max_pallet = 31 if pilih_baris <= 16 else 44
st.info(f"ℹ️ Kapasitas maksimal untuk **Baris {pilih_baris}** adalah **{max_pallet} Pallet**.")

c3, c4 = st.columns(2)
with c3:
    pallet_dari = st.number_input("Dari Pallet ke-:", min_value=1, max_value=max_pallet, step=1, value=1)
with c4:
    pallet_sampai = st.number_input("Sampai Pallet ke-:", min_value=1, max_value=max_pallet, step=1, value=1)
    
if pallet_sampai < pallet_dari:
    st.warning("⚠ Peringatan: 'Sampai Pallet ke' tidak boleh lebih kecil dari 'Dari Pallet ke'.")

st.divider()

# =========================================================================
# LOGIKA QUANTITY & SUBMIT BUTTON
# =========================================================================
st.markdown("### 📦 Kuantitas Barang")

if is_inbound:
    qty_in = st.number_input("Quantity In (Qty Masuk):", min_value=0, step=1, value=0, help="Contoh: 21000")
    qty_out = 0
else:
    qty_in = st.number_input("Quantity In (Acuan Awal Stok Saat Ini):", min_value=0, step=1, value=0)
    qty_out = st.number_input("Quantity Out (Qty Keluar):", min_value=0, step=1, value=0, help="Jumlah barang yang akan dikeluarkan")
    
    if qty_out > qty_in:
        st.error("🚨 Validasi Gagal: Qty Out tidak boleh lebih besar dari stok awal (Qty In)!")

keterangan = st.text_area("Keterangan Tambahan (Opsional):", placeholder="Catatan operasional...")

# Tombol Submit biasa, di luar block form
submitted = st.button("💾 Simpan Transaksi", type="primary", use_container_width=True)

if submitted:
    if not is_inbound and qty_out > qty_in:
        st.error("❌ Transaksi dibatalkan karena Qty Out melebihi Qty In.")
    elif pallet_sampai < pallet_dari:
        st.error("❌ Rentang pallet tidak valid.")
    else:
        tipe_clean = "IN" if is_inbound else "OUT"
        sisa_stok = qty_in - qty_out if not is_inbound else qty_in
        
        payload = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tipe": tipe_clean,
            "barang": f"{part_number} - {selected_product} (Lot: {lot_number})",
            "rak": lokasi_rak,
            "qty": f"In:{qty_in} | Out:{qty_out} | Sisa:{sisa_stok} (Pallet {pallet_dari}-{pallet_sampai})"
        }
        
        try:
            response = requests.post(WEB_APP_URL, json=payload)
            if response.status_code == 200:
                st.success(f"✅ Transaksi {tipe_clean} Berhasil Disimpan! (Sisa Stok Tercatat: {sisa_stok} di Rak {lokasi_rak})")
                st.cache_data.clear()
            else:
                st.error("❌ Gagal mengirim data ke Google Sheets.")
        except Exception as e:
            st.error(f"Terjadi kesalahan koneksi: {e}")

st.divider()
st.subheader("📜 Riwayat Transaksi Terbaru")
log_df = load_log()
if not log_df.empty:
    st.dataframe(log_df.tail(5).iloc[::-1], use_container_width=True, hide_index=True)
else:
    st.info("Belum ada riwayat transaksi tercatat.")
