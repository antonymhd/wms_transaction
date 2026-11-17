import streamlit as st
import wms_core as core

st.set_page_config(page_title="Outbound & Bongkar - Rack Navigator", layout="centered", initial_sidebar_state="collapsed")
core.inject_custom_css(accent_color="#2563EB", accent_border="#3B82F6", accent_bg_rgba="rgba(37, 99, 235, 0.1)")

OPERATOR_OUTBOUND = ["Maheswara", "Amir", "Bakir", "Danny"]
df_master = core.load_master_data()

keys_state = [
    'slide', 'mode_transaksi', 'kategori', 'gramasi', 'item', 'pilih_part',
    'pilih_kolom', 'pilih_baris', 'pilih_lot', 'pilih_catatan_rak', 'max_pallet', 'max_dus',
    'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'in_sisi_masuk', 'alokasi_terpilih',
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
                🧭 Outbound & Bongkar - Rack Navigator
            </div>
            <div style="font-size: 13px; opacity: 0.8; margin-top: 4px;">
                Pilih Operator Forklift yang Bertugas
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="btn-center">', unsafe_allow_html=True)
    for i in range(0, len(OPERATOR_OUTBOUND), 2):
        cols = st.columns(2)
        with cols[0]:
            op1 = OPERATOR_OUTBOUND[i]
            if st.button(f"👤 {op1}", key=f"op_{op1}", use_container_width=True):
                st.session_state.operator = op1
                core.muat_undo_firestore("OUT", op1)
                pindah_slide(1); st.rerun()
        if i + 1 < len(OPERATOR_OUTBOUND):
            with cols[1]:
                op2 = OPERATOR_OUTBOUND[i+1]
                if st.button(f"👤 {op2}", key=f"op_{op2}", use_container_width=True):
                    st.session_state.operator = op2
                    core.muat_undo_firestore("OUT", op2)
                    pindah_slide(1); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.write("")
    with st.expander("➕ Operator Pengganti / Lainnya"):
        op_manual = st.text_input("Ketik Nama Operator", placeholder="Masukkan nama...")
        if st.button("MASUK DENGAN NAMA INI", type="primary", use_container_width=True):
            if op_manual.strip():
                nama_fix = op_manual.strip().title()
                st.session_state.operator = nama_fix
                core.muat_undo_firestore("OUT", nama_fix)
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
elif st.session_state.mode_transaksi == "INBOUND_SISA":
    mode_badge = "  |  <span style='color:#10B981;font-weight:bold;'>📥 HASIL BONGKAR / INBOUND SISA</span>"

core.render_header_dan_undo(
    app_title="Outbound & Bongkar - Rack Navigator",
    accent_color="#2563EB",
    prefix_app="OUT",
    mode_badge=mode_badge,
    reset_keys=['mode_transaksi']
)
core.render_progress_bar(st.session_state.slide, st.session_state.mode_transaksi)

# ==========================================================
# SLIDE 1: PILIH AKSI UTAMA (TASK-FIRST)
# ==========================================================
if st.session_state.slide == 1:
    st.session_state.mode_transaksi = None
    st.markdown("### Pilih Jenis Aktivitas")
    
    st.write("")
    if st.button("📤 OUTBOUND (Keluarkan Barang dari Rak)\nMengambil stok berdasarkan akses 2 pintu (Depan FIFO / Belakang LIFO)", type="primary", use_container_width=True):
        st.session_state.mode_transaksi = "OUTBOUND"
        pindah_slide(2); st.rerun()
        
    st.write("")
    if st.button("📥 HASIL BONGKAR / INBOUND SISA\nMemasukkan barang bongkaran atau sisa muatan kembali ke rak (Default Sisi Depan Push-Back)", use_container_width=True):
        st.session_state.mode_transaksi = "INBOUND_SISA"
        st.session_state.in_sisi_masuk = "DEPAN"
        pindah_slide(2); st.rerun()

# ==========================================================
# SLIDE 2 - 4: PEMILIHAN PRODUK (KATEGORI -> GRAMASI -> ITEM)
# ==========================================================
elif st.session_state.slide in [2, 3, 4]:
    core.render_pemilihan_produk(df_master)

# ==========================================================
# CABANG A: PROSES OUTBOUND (SLIDE 5 -> 6, 2 PINTU SHUTTLE)
# ==========================================================
elif st.session_state.mode_transaksi == "OUTBOUND":
    if st.session_state.slide == 5:
        if st.button("🔙 GANTI ITEM"): pindah_slide(4); st.rerun()
        st.markdown("### Lokasi Target Outbound (Akses 2 Pintu)")
        st.info(core.format_nama_warna_bold(st.session_state.item))
        
        with st.spinner("🔍 Memindai posisi lorong depan & belakang..."):
            semua_data = core.get_all_racking_data()
            df_rekap = core.cari_lokasi_stok_hibrida(semua_data, st.session_state.item)
            
            if df_rekap.empty:
                st.error("❌ Stok untuk item ini tidak ditemukan di seluruh rak!")
            else:
                for idx, row in df_rekap.iterrows():
                    sisi_akses = row.get('SisiAkses', 'DEPAN')
                    if sisi_akses == "DEPAN":
                        akses_badge = "✅ AKSES DEPAN (FIFO)"
                        prioritas_label = "🌟 REKOMENDASI UTAMA" if idx == 0 else f"⭐ URUTAN {idx + 1}"
                    elif sisi_akses == "BELAKANG":
                        akses_badge = "✅ AKSES BELAKANG (LIFO)"
                        prioritas_label = "🌟 REKOMENDASI UTAMA" if idx == 0 else f"⭐ URUTAN {idx + 1}"
                    else:
                        sisi_str = "Depan" if sisi_akses == "TERHALANG_DEPAN" else "Belakang"
                        akses_badge = f"⚠️ TERHALANG {row.get('MinPenghalang', 0)} PALET ({sisi_str})"
                        prioritas_label = f"🚧 PERLU LANGSIR ({row.get('InfoPenghalang', '-')})"
                        
                    catatan_label = f"\n🔖 Catatan: {row['Catatan']}" if row['Catatan'] else ""
                    btn_label = f"📍 RAK {row['Kolom']}-{row['Baris']}  |  🏷️ Lot: {row['Lot Number']}\n"
                    btn_label += f"Stok: {row['Pallet']} Pallet, {row['Dus']} {row['Satuan']}  |  {akses_badge}\n"
                    btn_label += f"{prioritas_label}{catatan_label}"
                    
                    if st.button(btn_label, use_container_width=True, key=f"btn_loc_{idx}"):
                        st.session_state.pilih_kolom = row['Kolom']
                        st.session_state.pilih_baris = row['Baris']
                        st.session_state.pilih_lot = row['Lot Number']
                        st.session_state.pilih_part = row['Part']
                        st.session_state.pilih_catatan_rak = row['Catatan']
                        st.session_state.max_pallet = row['Pallet']
                        st.session_state.max_dus = row['Dus']
                        pindah_slide(6); st.rerun()

    elif st.session_state.slide == 6:
        if st.button("🔙 BATAL"): pindah_slide(5); st.rerun()
        core.render_alur_pengambilan_terpadu(
            prefix_app="OUT",
            df_master=df_master,
            judul_ambil="Pengeluaran Barang (Outbound)",
            label_tombol_eksekusi="🚀 KONFIRMASI PENGELUARAN",
            jenis_log="OUTBOUND (Remote)",
            judul_tele="OUTBOUND - RACK NAVIGATOR",
            mode_undo_label="OUTBOUND",
            reset_keys={
                'kolom': 'pilih_kolom',
                'baris': 'pilih_baris',
                'lot': 'pilih_lot',
                'catatan': 'pilih_catatan_rak',
                'max_p': 'max_pallet',
                'max_d': 'max_dus'
            }
        )

# ==========================================================
# CABANG B: HASIL BONGKAR / INBOUND SISA (SLIDE 5 -> 6 -> 7)
# ==========================================================
elif st.session_state.mode_transaksi == "INBOUND_SISA":
    core.render_alur_inbound_terpadu(
        prefix_app="OUT",
        judul_inbound="Hasil Bongkar / Inbound Sisa",
        default_sisi_masuk="DEPAN",
        jenis_log="HASIL BONGKAR / INBOUND SISA (Remote)",
        judul_tele="HASIL BONGKAR / INBOUND SISA",
        mode_undo_label="HASIL BONGKAR / INBOUND SISA"
    )
