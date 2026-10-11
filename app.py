import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import time
import io
import copy

# ==========================================================
# 1. KONFIGURASI WEB & DATABASE
# ==========================================================
st.set_page_config(
    page_title="WMS Data Entry - Hokkan Deltapack",
    page_icon="📝",
    layout="wide"
)

# 💡 NAMA DATABASE
NAMA_DATABASE = 'uji_coba_rak'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

st.title("📝 WMS Entry Form (Inbound & Outbound)")
st.caption(f"Alat khusus PPIC. Terhubung ke database: {NAMA_DATABASE}")

# ==========================================================
# 2. FITUR UNDO & REDO (MEMORI JANGKA PENDEK)
# ==========================================================
if 'undo_data' not in st.session_state:
    st.session_state['undo_data'] = None
    st.session_state['redo_data'] = None
    st.session_state['action_msg'] = ""
    st.session_state['action_kolom'] = ""
    st.session_state['action_status'] = None 

if st.session_state['action_status'] == 'DONE':
    st.info(f"🔄 **Aksi Terakhir:** {st.session_state['action_msg']}")
    if st.button("🔙 BATALKAN AKSI TERAKHIR (UNDO)", type="primary"):
        with st.spinner("Mengembalikan data seperti semula..."):
            doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state['action_kolom']}")
            doc_ref.set({'data': st.session_state['undo_data']})
            st.session_state['action_status'] = 'UNDONE'
            st.success("✅ Aksi dibatalkan (Undo)! Data kembali seperti semula.")
            time.sleep(1.5)
            st.rerun()
            
elif st.session_state['action_status'] == 'UNDONE':
    st.warning(f"↩️ **Anda membatalkan aksi:** {st.session_state['action_msg']}")
    if st.button("↪️ KEMBALIKAN LAGI (REDO)", type="primary"):
        with st.spinner("Memulihkan aksi..."):
            doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state['action_kolom']}")
            doc_ref.set({'data': st.session_state['redo_data']})
            st.session_state['action_status'] = 'DONE'
            st.success("✅ Aksi dipulihkan (Redo)!")
            time.sleep(1.5)
            st.rerun()

if st.session_state['action_status'] is not None:
    st.divider()

# ==========================================================
# 3. TABS MENU UTAMA
# ==========================================================
tab1, tab2, tab3 = st.tabs(["📥 1. Inbound Borongan", "📤 2. Penyesuaian Outbound", "⚙️ 3. Pengaturan & Backup Data"])

# ----------------------------------------------------------
# TAB 1: INBOUND BORONGAN
# ----------------------------------------------------------
with tab1:
    st.header("Input Data Kertas (Borongan)")
    st.caption("Pilih rak dan produk. Sistem otomatis melacak sisa slot kosong dan mengisi barisnya.")
    
    col_in1, col_in2 = st.columns(2)
    with col_in1:
        in_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="in_kolom")
        in_baris = st.number_input("Nomor Baris (Inbound)", min_value=1, max_value=28, value=1)
        
        col_qty1, col_qty2 = st.columns(2)
        with col_qty1:
            in_qty = st.number_input("Pallet Masuk UTUH", min_value=0, step=1, value=1)
        with col_qty2:
            in_pecahan = st.number_input("Sisa Pecahan (Qty)", min_value=0, step=1, value=0)
    
    with col_in2:
        try:
            try:
                df_master = pd.read_csv("Master_Produk.csv", sep=";")
            except:
                df_master = pd.read_csv("Master_Produk.csv", sep=",")
            daftar_produk = df_master['Nama Barang'].dropna().astype(str).unique().tolist()
        except FileNotFoundError:
            df_master = pd.DataFrame()
            daftar_produk = []
            st.warning("⚠️ File 'Master_Produk.csv' tidak ditemukan. Mode ketik manual aktif.")
            
        pilihan_barang = st.selectbox("Pilih Produk", ["-- Ketik Manual --"] + daftar_produk)
        
        if pilihan_barang != "-- Ketik Manual --" and not df_master.empty:
            in_produk = pilihan_barang
            match_part = df_master[df_master['Nama Barang'] == pilihan_barang]['Part Number'].values
            val_part = str(match_part[0]) if len(match_part) > 0 else ""
            in_part = st.text_input("Part Number (Auto-fill)", value=val_part, disabled=True)
        else:
            in_produk = st.text_input("Nama Produk (Ketik Manual)")
            in_part = st.text_input("Part Number (Ketik Manual)")
            
        in_lot = st.text_input("No. Lot Produksi / Mesin Shift")
        
    if st.button("🚀 Simpan Inbound", type="primary", use_container_width=True):
        if not in_produk or not in_part:
            st.error("⚠️ Nama Produk dan Part Number wajib diisi!")
        elif in_qty == 0 and in_pecahan == 0:
            st.error("⚠️ Masukkan minimal 1 Pallet Utuh atau Pecahan!")
        else:
            with st.spinner("Menyusun data ke dalam rak..."):
                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{in_kolom}")
                doc = doc_ref.get()
                all_data_raw = doc.to_dict().get('data', []) if doc.exists else []
                
                data_sebelum = copy.deepcopy(all_data_raw)
                
                # --- PERBAIKAN KRUSIAL: Sapu bersih data "Hantu" (Baris yg kosong melompong dari CSV awal) ---
                # Hanya singkirkan data kosong DI BARIS YANG SEDANG DIPILIH SAJA.
                all_data = []
                for d in all_data_raw:
                    is_baris_sama = float(d.get('Baris', 0) or 0) == float(in_baris)
                    is_hantu = str(d.get('Part Number', '')).strip() == ''
                    if is_baris_sama and is_hantu:
                        continue # Abaikan data hantu ini (jangan dimasukkan ke all_data)
                    all_data.append(d)
                
                baris_data = [d for d in all_data if float(d.get('Baris', 0) or 0) == float(in_baris)]
                
                is_all_box = in_part.strip().upper().endswith(('D', 'G'))
                for d in baris_data:
                    pn = str(d.get('Part Number', '')).strip().upper()
                    if pn and not pn.endswith(('D', 'G')):
                        is_all_box = False 
                        break
                        
                if is_all_box:
                    max_slot = 32 if in_baris <= 16 else 46
                else:
                    max_slot = 31 if in_baris <= 16 else 44
                
                # Sekarang perhitungan terisi menjadi 100% akurat sesuai fisik aktual
                filled_pallets = [int(float(d.get('Pallet Ke', 0) or 0)) for d in baris_data if pd.notna(d.get('Pallet Ke')) and str(d.get('Pallet Ke')).strip() != '']
                current_qty = len(filled_pallets)
                if current_qty > max_slot: max_slot = current_qty 
                
                total_inbound_slots = in_qty + (1 if in_pecahan > 0 else 0)
                
                if current_qty + total_inbound_slots > max_slot:
                    st.error(f"❌ **Kapasitas Penuh!** Anda memasukkan {total_inbound_slots} Slot Fisik. Rak maksimal muat {max_slot}. Terisi {current_qty}. Sisa ruang {max_slot - current_qty} slot.")
                else:
                    next_pallet = max(filled_pallets) + 1 if filled_pallets else 1
                    new_records = []
                    
                    for _ in range(int(in_qty)):
                        while next_pallet in filled_pallets:
                            next_pallet += 1
                        new_records.append({
                            'Kolom': in_kolom,
                            'Baris': float(in_baris),
                            'Pallet Ke': float(next_pallet),
                            'Part Number': in_part,
                            'Lot Number': in_lot,
                            'Nama Produk': in_produk,
                            'In': 1, 'Out': 0, 'Sisa': 1, 'Status': 'Terisi',
                            'Keterangan': ''
                        })
                        filled_pallets.append(next_pallet)
                        next_pallet += 1
                        
                    if in_pecahan > 0:
                        while next_pallet in filled_pallets:
                            next_pallet += 1
                        new_records.append({
                            'Kolom': in_kolom,
                            'Baris': float(in_baris),
                            'Pallet Ke': float(next_pallet),
                            'Part Number': in_part,
                            'Lot Number': in_lot,
                            'Nama Produk': in_produk,
                            'In': 1, 'Out': 0, 'Sisa': 1, 'Status': 'Terisi',
                            'Keterangan': f"Pecahan: {in_pecahan} qty"
                        })
                        filled_pallets.append(next_pallet)
                        
                    all_data.extend(new_records)
                    
                    st.session_state['undo_data'] = data_sebelum
                    st.session_state['redo_data'] = copy.deepcopy(all_data)
                    st.session_state['action_kolom'] = in_kolom
                    st.session_state['action_msg'] = f"Inbound {total_inbound_slots} Slot Fisik '{in_produk}' ke Rak {in_kolom}-{in_baris}"
                    st.session_state['action_status'] = 'DONE'
                    
                    doc_ref.set({'data': all_data})
                    st.success(f"✅ Berhasil menyuntikkan data! Memakan {total_inbound_slots} Slot Fisik di rak {in_kolom}-{in_baris}.")
                    time.sleep(1.5)
                    st.rerun()

# ----------------------------------------------------------
# TAB 2: OUTBOUND (PENYESUAIAN SISA BERDASARKAN LOT)
# ----------------------------------------------------------
with tab2:
    st.header("Penyesuaian Sisa Stok (Berdasarkan No. Lot)")
    st.caption("Pilih No. Lot yang dibongkar, lalu masukkan SISA AKHIR pallet sesuai coretan di kertas.")
    
    if 'out_lokasi_dicari' not in st.session_state:
        st.session_state['out_lokasi_dicari'] = False
        st.session_state['out_k_pilih'] = "A"
        st.session_state['out_b_pilih'] = 1

    col_out1, col_out2 = st.columns([1, 2])
    with col_out1:
        st.markdown("**1. Pilih Lokasi Rak**")
        out_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="out_kolom")
        out_baris = st.number_input("Nomor Baris (Outbound)", min_value=1, max_value=28, value=1)
        
        if st.button("🔍 Cari Data Rak", use_container_width=True):
            st.session_state['out_lokasi_dicari'] = True
            st.session_state['out_k_pilih'] = out_kolom
            st.session_state['out_b_pilih'] = out_baris
            
    with col_out2:
        if st.session_state['out_lokasi_dicari']:
            k_pilih = st.session_state['out_k_pilih']
            b_pilih = st.session_state['out_b_pilih']
            
            doc_ref_out = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            doc_out = doc_ref_out.get()
            all_out_data = doc_out.to_dict().get('data', []) if doc_out.exists else []
            
            baris_out_data = [d for d in all_out_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
            
            if baris_out_data:
                df_out = pd.DataFrame(baris_out_data)
                df_out['Pallet Ke'] = pd.to_numeric(df_out['Pallet Ke'], errors='coerce')
                
                # Saring hanya yang ada Part Number-nya (Bukan Hantu)
                df_out = df_out[df_out['Part Number'].astype(str).str.strip() != ""]
                
                if not df_out.empty and 'Lot Number' in df_out.columns:
                    tersedia_lot = []
                    mapping_lot = {}
                    
                    for _, row in df_out.drop_duplicates(subset=['Lot Number', 'Nama Produk']).iterrows():
                        lot_asli = str(row['Lot Number'])
                        nama_prod = str(row['Nama Produk'])
                        label_dropdown = f"{nama_prod} (Lot: {lot_asli})"
                        
                        tersedia_lot.append(label_dropdown)
                        mapping_lot[label_dropdown] = lot_asli
                        
                    st.markdown(f"**2. Eksekusi Pembongkaran di Rak {k_pilih}-{b_pilih}**")
                    pilihan_label = st.selectbox("🎯 Pilih Produk & No. Lot yang dikeluarkan:", tersedia_lot)
                    
                    pilih_lot = mapping_lot[pilihan_label]
                    
                    df_lot = df_out[df_out['Lot Number'].astype(str) == pilih_lot].sort_values('Pallet Ke', ascending=False)
                    current_qty_lot = len(df_lot)
                    
                    st.markdown(f"**📦 Total Lot `{pilih_lot}` di Rak ini: `{current_qty_lot}` Slot Fisik**")
                    
                    kolom_tampil = ['Pallet Ke', 'Part Number', 'Nama Produk', 'Lot Number', 'Keterangan']
                    kolom_tersedia = [c for c in kolom_tampil if c in df_lot.columns]
                    st.dataframe(df_lot[kolom_tersedia].head(5), hide_index=True)
                    
                    col_sisa1, col_sisa2 = st.columns(2)
                    with col_sisa1:
                        out_sisa_akhir = st.number_input(f"Sisa Pallet UTUH (0 - {current_qty_lot})", min_value=0, max_value=current_qty_lot, step=1, value=current_qty_lot)
                    with col_sisa2:
                        out_sisa_dus = st.number_input("Sisa Pecahan (Dus/Box/Sak)", min_value=0, step=1, value=0)
                    
                    if st.button("🚜 Sesuaikan Stok Lot Ini", type="primary", use_container_width=True):
                        slot_tambahan = 1 if out_sisa_dus > 0 else 0
                        total_slot_dibutuhkan = out_sisa_akhir + slot_tambahan
                        
                        if total_slot_dibutuhkan > current_qty_lot:
                            st.error(f"❌ Tidak logis! Anda mengklaim sisa fisik {total_slot_dibutuhkan} Slot, padahal di sistem hanya ada {current_qty_lot} Pallet.")
                        elif total_slot_dibutuhkan == current_qty_lot and out_sisa_dus == 0:
                            st.info("Sisa pallet sama dengan sistem. Tidak ada yang diubah.")
                        else:
                            data_sebelum_out = copy.deepcopy(all_out_data)
                            
                            out_qty = current_qty_lot - total_slot_dibutuhkan
                            pallets_to_remove = df_lot['Pallet Ke'].head(out_qty).tolist() if out_qty > 0 else []
                            
                            new_all_out_data = []
                            for d in all_out_data:
                                b_val = float(d.get('Baris', 0) or 0)
                                p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                                l_str = str(d.get('Lot Number', ''))
                                
                                if b_val == float(b_pilih) and p_val in pallets_to_remove:
                                    continue 
                                
                                if out_sisa_dus > 0 and b_val == float(b_pilih) and l_str == pilih_lot:
                                    sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
                                    if not sisa_pallets.empty and p_val == sisa_pallets['Pallet Ke'].max():
                                        d['Keterangan'] = f"Pecahan: {out_sisa_dus} qty"
                                
                                elif out_sisa_dus == 0 and b_val == float(b_pilih) and l_str == pilih_lot:
                                    if 'Pecahan' in str(d.get('Keterangan', '')):
                                        d['Keterangan'] = ""
                                        
                                new_all_out_data.append(d)
                            
                            st.session_state['undo_data'] = data_sebelum_out
                            st.session_state['redo_data'] = copy.deepcopy(new_all_out_data)
                            st.session_state['action_kolom'] = k_pilih
                            st.session_state['action_msg'] = f"Penyesuaian Outbound Lot '{pilih_lot}' di Rak {k_pilih}-{b_pilih}"
                            st.session_state['action_status'] = 'DONE'
                            
                            doc_ref_out.set({'data': new_all_out_data})
                            st.success(f"✅ Selesai! Menggunakan {total_slot_dibutuhkan} Slot Fisik.")
                            
                            st.session_state['out_lokasi_dicari'] = False
                            time.sleep(1.5)
                            st.rerun()
                else:
                    st.info(f"📍 Rak {k_pilih}-{b_pilih} masih kosong melompong (Hanya berisi slot kosong / hantu).")
            else:
                st.info(f"📍 Rak {k_pilih}-{b_pilih} belum ada data sama sekali.")
        else:
            st.info("👈 Silakan tentukan Lokasi Kolom dan Baris di sebelah kiri, lalu klik 'Cari Data Rak'.")

# ----------------------------------------------------------
# TAB 3: PENGATURAN & BACKUP DATA
# ----------------------------------------------------------
with tab3:
    st.header("⚙️ Ekspor dan Hapus Data")
    
    st.subheader("📥 Tarik Laporan (Export to CSV)")
    st.write("Unduh kondisi terkini seluruh racking gudang menjadi file Excel (CSV).")
    
    if st.button("Persiapkan Data untuk Diunduh"):
        with st.spinner("Menarik data dari Firebase..."):
            docs = db.collection(NAMA_DATABASE).stream()
            semua_data = []
            for doc in docs:
                doc_data = doc.to_dict()
                if 'data' in doc_data:
                    semua_data.extend(doc_data['data'])
                    
            if semua_data:
                df_export = pd.DataFrame(semua_data)
                kolom_harapan = ['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk', 'Saldo Awal', 'In', 'Out', 'Sisa', 'Keterangan', 'Status']
                
                for c in kolom_harapan:
                    if c not in df_export.columns:
                        df_export[c] = ""
                        
                df_export = df_export[kolom_harapan]
                
                csv_buffer = io.StringIO()
                df_export.to_csv(csv_buffer, index=False, sep=";")
                csv_data = csv_buffer.getvalue()
                
                st.success("✅ Data berhasil ditarik! Klik tombol di bawah untuk mengunduh.")
                st.download_button(
                    label="⬇️ Unduh Data (CSV)",
                    data=csv_data,
                    file_name="Backup_Racking_Hokkan.csv",
                    mime="text/csv"
                )
            else:
                st.warning("Database saat ini kosong. Tidak ada data yang bisa diunduh.")

    st.divider()

    st.subheader("⚠️ Zona Berbahaya: Reset Database")
    st.error("Perhatian! Menekan tombol ini akan menghapus SELURUH data racking di aplikasi WMS. Pastikan Anda sudah mengunduh (backup) data terlebih dahulu.")
    
    hapus_pin = st.text_input("Masukkan PIN Reset (1234) untuk membuka tombol hapus:", type="password")
    
    if hapus_pin == "1234":
        if st.button("🗑️ SAYA YAKIN, HAPUS SEMUA DATA SEKARANG!", type="primary", use_container_width=True):
            with st.spinner("Menghapus data di Firebase..."):
                try:
                    docs = db.collection(NAMA_DATABASE).stream()
                    batch = db.batch()
                    for doc in docs:
                        batch.delete(doc.reference)
                    batch.commit()
                    
                    st.session_state['action_status'] = None
                    st.session_state['out_lokasi_dicari'] = False
                    
                    st.success("✅ Seluruh data berhasil dihapus dan dikosongkan!")
                    time.sleep(1.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal menghapus: {e}")
