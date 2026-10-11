import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import time

# ==========================================================
# 1. KONFIGURASI WEB
# ==========================================================
st.set_page_config(
    page_title="WMS Data Entry - Hokkan Deltapack",
    page_icon="📝",
    layout="wide"
)

# ==========================================================
# 2. INISIALISASI FIREBASE
# ==========================================================
if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

st.title("📝 WMS Entry Form (Inbound & Outbound)")
st.caption("Alat khusus PPIC untuk konversi laporan kertas lapangan secara instan ke sistem visualisasi WMS.")

# ==========================================================
# 3. TABS MENU (INBOUND & OUTBOUND)
# ==========================================================
tab1, tab2 = st.tabs(["📥 1. Inbound Borongan (Masuk)", "📤 2. Outbound LIFO (Keluar)"])

# ----------------------------------------------------------
# TAB 1: INBOUND BORONGAN
# ----------------------------------------------------------
with tab1:
    st.header("Input Data Kertas (Borongan)")
    st.info("Ketik data dari form kertas. Sistem otomatis mendeteksi sisa ruang dan menyusun pallet berurutan.")
    
    col_in1, col_in2 = st.columns(2)
    with col_in1:
        in_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"])
        in_baris = st.number_input("Nomor Baris", min_value=1, max_value=28, value=1)
        in_qty = st.number_input("Jumlah Pallet Masuk", min_value=1, step=1)
    
    with col_in2:
        in_produk = st.text_input("Nama Produk")
        in_part = st.text_input("Part Number", help="Jika berakhiran D/G, sistem akan otomatis membaca kemasan Box.")
        in_lot = st.text_input("No. Lot Produksi / Mesin Shift")
        
    if st.button("🚀 Simpan Inbound", type="primary", use_container_width=True):
        if not in_produk or not in_part:
            st.error("⚠️ Nama Produk dan Part Number wajib diisi!")
        else:
            with st.spinner("Menganalisis kapasitas fisik rak..."):
                doc_ref = db.collection('visualisasi_rak').document(f"Kolom_{in_kolom}")
                doc = doc_ref.get()
                all_data = doc.to_dict().get('data', []) if doc.exists else []
                
                # Filter barang yang ada di baris terpilih
                baris_data = [d for d in all_data if str(d.get('Baris')) == str(in_baris)]
                
                # --- LOGIKA KAPASITAS MELAR (BOX D/G) ---
                is_all_box = in_part.strip().upper().endswith(('D', 'G'))
                for d in baris_data:
                    pn = str(d.get('Part Number', '')).strip().upper()
                    if pn and not pn.endswith(('D', 'G')):
                        is_all_box = False # Gagal melar jika ada campuran/Sak
                        break
                        
                if is_all_box:
                    max_slot = 32 if in_baris <= 16 else 46
                else:
                    max_slot = 31 if in_baris <= 16 else 44
                
                # Cek slot terpakai
                filled_pallets = [int(float(d.get('Pallet Ke', 0))) for d in baris_data if str(d.get('Pallet Ke', '')) != '']
                current_qty = len(filled_pallets)
                
                # Validasi Overcapacity Fisik
                if current_qty + in_qty > max_slot:
                    st.error(f"❌ **Kapasitas Fisik Penuh!** Baris ini maksimal muat {max_slot} pallet. Saat ini sudah terisi {current_qty} pallet. Sisa ruang hanya {max_slot - current_qty} slot.")
                else:
                    # Cari urutan pallet terdepan
                    next_pallet = max(filled_pallets) + 1 if filled_pallets else 1
                    
                    new_records = []
                    added = 0
                    curr_p = next_pallet
                    
                    while added < in_qty:
                        if curr_p not in filled_pallets:
                            new_records.append({
                                'Kolom': in_kolom,
                                'Baris': in_baris,
                                'Pallet Ke': curr_p,
                                'Part Number': in_part,
                                'Lot Number': in_lot,
                                'Nama Produk': in_produk,
                                'In': 1, 'Out': 0, 'Sisa': 1, 'Status': 'Terisi'
                            })
                            added += 1
                        curr_p += 1
                        
                    # Simpan pergerakan data ke Firestore
                    all_data.extend(new_records)
                    doc_ref.set({'data': all_data})
                    st.success(f"✅ Berhasil menyuntikkan {in_qty} pallet ke rak {in_kolom}-{in_baris}!")
                    time.sleep(1)
                    st.rerun()

# ----------------------------------------------------------
# TAB 2: OUTBOUND (LIFO)
# ----------------------------------------------------------
with tab2:
    st.header("Eksekusi Pengeluaran Barang")
    st.warning("Sistem mematuhi logika Last-In-First-Out (LIFO). Pallet terluar/terdepan akan dikeluarkan terlebih dahulu.")
    
    col_out1, col_out2 = st.columns([1, 2])
    with col_out1:
        out_kolom = st.selectbox("Lokasi Kolom Racking", ["A", "B", "C", "D", "E", "F", "G"], key="outk")
        out_baris = st.number_input("Nomor Baris", min_value=1, max_value=28, value=1, key="outb")
        
    doc_ref_out = db.collection('visualisasi_rak').document(f"Kolom_{out_kolom}")
    doc_out = doc_ref_out.get()
    all_out_data = doc_out.to_dict().get('data', []) if doc_out.exists else []
    baris_out_data = [d for d in all_out_data if str(d.get('Baris')) == str(out_baris)]
    
    with col_out2:
        if baris_out_data:
            df_out = pd.DataFrame(baris_out_data)
            df_out['Pallet Ke'] = pd.to_numeric(df_out['Pallet Ke'], errors='coerce')
            df_out = df_out.sort_values('Pallet Ke', ascending=False) # LIFO (Nomor Terbesar di atas)
            
            st.markdown(f"**📦 Posisi Terluar di Rak {out_kolom}-{out_baris} saat ini:**")
            st.dataframe(df_out[['Pallet Ke', 'Nama Produk', 'Lot Number']].head(5), hide_index=True)
            
            out_qty = st.number_input("Jumlah Pallet yang di-Bongkar (Outbound)", min_value=1, max_value=len(baris_out_data), step=1)
            
            if st.button("🚜 Eksekusi Outbound", type="primary", use_container_width=True):
                # Ambil ID/Pallet Ke dari N barang teratas
                pallets_to_remove = df_out['Pallet Ke'].head(out_qty).tolist()
                
                new_all_out_data = []
                removed = 0
                for d in all_out_data:
                    # Filter: Hapus data jika Baris & Pallet Ke-nya cocok dengan yang dibongkar
                    if str(d.get('Baris')) == str(out_baris) and pd.to_numeric(d.get('Pallet Ke', 0)) in pallets_to_remove:
                        removed += 1
                        continue 
                    new_all_out_data.append(d)
                
                doc_ref_out.set({'data': new_all_out_data})
                st.success(f"✅ {removed} Pallet sukses dikeluarkan dari posisi terdepan rak {out_kolom}-{out_baris}!")
                time.sleep(1)
                st.rerun()
        else:
            st.info(f"📍 Rak {out_kolom}-{out_baris} dalam keadaan kosong melompong.")
