import streamlit as st
import wms_core as core

st.set_page_config(page_title="Rack Navigator - Outbound & WIP", layout="centered", initial_sidebar_state="collapsed")
core.inject_custom_css(accent_color="#2563EB", accent_border="#3B82F6", accent_bg_rgba="rgba(37, 99, 235, 0.1)")

DAFTAR_OPERATOR = ["Maheswara", "Amir", "Bakir", "Danny", "Joko", "Abdul"]
df_master = core.load_master_data()

keys_state = [
    'slide', 'mode_transaksi', 'kategori', 'gramasi', 'item', 'pilih_part',
    'pilih_kolom', 'pilih_baris', 'pilih_lot', 'pilih_catatan_rak',
    'max_pallet', 'max_dus',
    'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'in_sisi_masuk',
    'in_rak_skip', 'alokasi_terpilih',
    'operator', 'undo_record'
]
for key in keys_state:
    if key not in st.session_state:
        st.session_state[key] = 0 if key == 'slide' else None

def pindah_slide(tujuan):
    st.session_state.slide = tujuan

# ==========================================================
# SLIDE 0: PILIH OPERATOR BERTUGAS
# ==========================================================
if st.session_state.slide == 0:
    st.markdown("""
        <div class="db-info-box" style="text-align: center;">
            <div style="font-size: 18px; font-weight: bold; color: #2563EB;">
                🧭 Rack Navigator - Outbound & WIP
            </div>
            <div style="font-size: 13px; opacity: 0.8; margin-top: 4px;">
                Pilih Operator Forklift yang Bertugas
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="btn-center">', unsafe_allow_html=True)
    for i in range(0, len(DAFTAR_OPERATOR), 2):
        cols = st.columns(2)
        with cols[0]:
            op1 = DAFTAR_OPERATOR[i]
            if st.button(f"👤 {op1}", key=f"op_{op1}", use_container_width=True):
                st.session_state.operator = op1
                core.muat_undo_firestore("MAIN", op1)
                pindah_slide(1); st.rerun()
        if i + 1 < len(DAFTAR_OPERATOR):
            with cols[1]:
                op2 = DAFTAR_OPERATOR[i+1]
                if st.button(f"👤 {op2}", key=f"op_{op2}", use_container_width=True):
                    st.session_state.operator = op2
                    core.muat_undo_firestore("MAIN", op2)
                    pindah_slide(1); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.write("")
    with st.expander("➕ Operator Pengganti / Lainnya"):
        op_manual = st.text_input("Ketik Nama Operator", placeholder="Masukkan nama...")
        if st.button("MASUK DENGAN NAMA INI", type="primary", use_container_width=True):
            if op_manual.strip():
                nama_fix = op_manual.strip().title()
                st.session_state.operator = nama_fix
                core.muat_undo_firestore("MAIN", nama_fix)
                pindah_slide(1); st.rerun()
            else:
                st.warning("Nama operator tidak boleh kosong.")
    st.stop()

# ==========================================================
# HEADER STATUS, NOTIFIKASI UNDO & PROGRESS BAR
# ==========================================================
mode_badge = ""
if st.session_state.mode_transaksi == "OUTBOUND":
    mode_badge = "  |  <span style='color:#2563EB;font-weight:bold;'>📤 OUTBOUND</span>"
elif st.session_state.mode_transaksi == "WIP":
    mode_badge = "  |  <span style='color:#F59E0B;font-weight:bold;'>🏭 SUPLAI WIP</span>"
elif st.session_state.mode_transaksi == "INBOUND_SISA":
    mode_badge = "  |  <span style='color:#10B981;font-weight:bold;'>↩️ KEMBALI SISA</span>"

core.render_header_dan_undo(
    app_title="Rack Navigator",
    accent_color="#2563EB",
    prefix_app="MAIN",
    mode_badge=mode_badge,
    reset_keys=['mode_transaksi', 'kategori', 'gramasi', 'item', 'pilih_part', 'pilih_kolom', 'pilih_baris', 'pilih_lot', 'pilih_catatan_rak', 'max_pallet', 'max_dus', 'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'in_sisi_masuk', 'in_rak_skip', 'alokasi_terpilih']
)

if st.session_state.slide >= 1:
    core.render_progress_bar(st.session_state.slide, st.session_state.mode_transaksi or "OUTBOUND")

# ==========================================================
# SLIDE 1: PILIH JENIS TRANSAKSI
# ==========================================================
if st.session_state.slide == 1:
    st.markdown("### Pilih Aktivitas")
    st.markdown('<div class="btn-center">', unsafe_allow_html=True)
    
    if st.button("📤 OUTBOUND\nMuat Pengiriman ke Customer", use_container_width=True):
        st.session_state.mode_transaksi = "OUTBOUND"
        pindah_slide(2); st.rerun()
        
    st.write("")
    if st.button("🏭 SUPLAI WIP\nKeluar Barang ke Area Produksi / Printing", use_container_width=True):
        st.session_state.mode_transaksi = "WIP"
        pindah_slide(2); st.rerun()
        
    st.write("")
    if st.button("↩️ PENGEMBALIAN SISA PRODUKSI\nSisa Mesin / WIP Kembali ke Rak", use_container_width=True):
        st.session_state.mode_transaksi = "INBOUND_SISA"
        st.session_state.in_sisi_masuk = "BELAKANG"
        pindah_slide(2); st.rerun()
        
    st.markdown('</div>', unsafe_allow_html=True)

# ==========================================================
# SLIDE 2, 3, 4: KATEGORI -> GRAMASI -> ITEM
# ==========================================================
elif st.session_state.slide in [2, 3, 4]:
    core.render_pemilihan_produk(df_master)

# ==========================================================
# ALUR PENGEMBALIAN SISA PRODUKSI (SLIDE 5, 6, 7)
# ==========================================================
elif st.session_state.mode_transaksi == "INBOUND_SISA" and st.session_state.slide in [5, 6, 7]:
    core.render_alur_inbound_terpadu(
        prefix_app="MAIN",
        judul_inbound="Pengembalian Sisa Produksi",
        default_sisi_masuk="BELAKANG",
        jenis_log="INBOUND (Pengembalian Sisa)",
        judul_tele="PENGEMBALIAN SISA PRODUKSI",
        mode_undo_label="PENGEMBALIAN SISA"
    )

# ==========================================================
# ALUR OUTBOUND & SUPLAI WIP - SLIDE 5: PILIH LOKASI RAK (FIFO + AKSES)
# ==========================================================
elif st.session_state.slide == 5:
    if st.button("🔙 GANTI ITEM"):
        pindah_slide(4); st.rerun()
        
    is_wip = (st.session_state.mode_transaksi == "WIP")
    st.markdown(f"### Pilih Rak Pengambilan ({'Suplai WIP' if is_wip else 'Outbound'})")
    st.info(core.format_nama_warna_bold(st.session_state.item))
    
    with st.spinner("🔍 Mencari posisi stok & prioritas akses FIFO..."):
        semua_data = core.get_all_racking_data()
        df_lokasi = core.cari_lokasi_stok_hibrida(semua_data, st.session_state.item)
        
    if df_lokasi.empty:
        st.error("❌ Stok kosong untuk produk ini.")
    else:
        for index, row in df_lokasi.iterrows():
            akses = row['SisiAkses']
            if akses == "DEPAN":
                badge_akses = "🟢 AKSES DEPAN (Bebas)"
            elif akses == "BELAKANG":
                badge_akses = "🔵 AKSES BELAKANG (Bebas)"
            elif akses == "TERHALANG_DEPAN":
                badge_akses = f"⚠️ TERHALANG {row['MinPenghalang']} PALET DI DEPAN ({row['InfoPenghalang']})"
            else:
                badge_akses = f"⚠️ TERHALANG {row['MinPenghalang']} PALET DI BELAKANG ({row['InfoPenghalang']})"
                
            teks_pec = f", {row['Dus']} {row['Satuan']}" if row['Dus'] > 0 else ""
            catatan_txt = f"\n🔖 Catatan: {row['Catatan']}" if row['Catatan'] else ""
            
            btn_label = (
                f"📍 RAK {row['Kolom']}-{row['Baris']}  |  Lot: {row['Lot Number']}\n"
                f"📦 Stok: {row['Pallet']} Pallet{teks_pec}  •  {badge_akses}{catatan_txt}"
            )
            if st.button(btn_label, key=f"rak_{index}", use_container_width=True):
                st.session_state.pilih_kolom = row['Kolom']
                st.session_state.pilih_baris = int(row['Baris'])
                st.session_state.pilih_lot = row['Lot Number']
                st.session_state.pilih_catatan_rak = row['Catatan']
                st.session_state.max_pallet = int(row['Pallet'])
                st.session_state.max_dus = int(row['Dus'])
                pindah_slide(6); st.rerun()

# ==========================================================
# ALUR OUTBOUND & SUPLAI WIP - SLIDE 6: INPUT AMBIL + CEK FISIK SISA
# ==========================================================
elif st.session_state.slide == 6:
    if st.button("🔙 GANTI LOKASI RAK"):
        pindah_slide(5); st.rerun()
        
    is_wip = (st.session_state.mode_transaksi == "WIP")
    core.render_alur_pengambilan_terpadu(
        prefix_app="MAIN",
        df_master=df_master,
        judul_ambil="Suplai WIP ke Produksi" if is_wip else "Outbound Pengiriman",
        label_tombol_eksekusi="🏭 KONFIRMASI SUPLAI WIP" if is_wip else "📤 KONFIRMASI OUTBOUND",
        jenis_log="OUTBOUND (Suplai WIP)" if is_wip else "OUTBOUND (Remote)",
        judul_tele="SUPLAI WIP - RACK NAVIGATOR" if is_wip else "OUTBOUND - RACK NAVIGATOR",
        mode_undo_label="SUPLAI WIP" if is_wip else "OUTBOUND",
        reset_keys={
            'kolom': 'pilih_kolom',
            'baris': 'pilih_baris',
            'lot': 'pilih_lot',
            'catatan': 'pilih_catatan_rak',
            'max_p': 'max_pallet',
            'max_d': 'max_dus'
        }
    )
