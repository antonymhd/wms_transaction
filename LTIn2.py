import streamlit as st
import wms_core as core

st.set_page_config(page_title="Inbound & WIP - Rack Navigator", layout="centered", initial_sidebar_state="collapsed")
core.inject_custom_css(accent_color="#10B981", accent_border="#059669", accent_bg_rgba="rgba(16, 185, 129, 0.1)")

OPERATOR_INBOUND = ["Fahmi", "Adip", "Didik", "Agung"]
df_master = core.load_master_data()

keys_state = [
    'slide', 'mode_transaksi', 'kategori', 'gramasi', 'item', 'pilih_part',
    'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'in_sisi_masuk', 'in_rak_skip', 'alokasi_terpilih',
    'wip_kolom', 'wip_baris', 'wip_lot', 'wip_catatan_rak', 'wip_max_pallet', 'wip_max_dus',
    'operator', 'undo_record', 'inbound_draft'
]
for key in keys_state:
    if key not in st.session_state:
        st.session_state[key] = 0 if key == 'slide' else None

def pindah_slide(tujuan):
    st.session_state.slide = tujuan

def pulihkan_draft_ke_session(draft):
    st.session_state.mode_transaksi = "INBOUND"
    st.session_state.kategori = draft.get("kategori") or ""
    st.session_state.gramasi = draft.get("gramasi") or ""
    st.session_state.item = draft.get("item") or ""
    st.session_state.pilih_part = draft.get("pilih_part") or ""
    st.session_state.in_lot = draft.get("in_lot") or ""
    st.session_state.in_qty_p = int(draft.get("in_qty_p") or 0)
    st.session_state.in_qty_c = int(draft.get("in_qty_c") or 0)
    st.session_state.in_tipe_c = draft.get("in_tipe_c") or "Dus"
    st.session_state.in_sisi_masuk = draft.get("in_sisi_masuk") or "BELAKANG"
    st.session_state.in_rak_skip = draft.get("in_rak_skip") or []
    st.session_state.alokasi_terpilih = draft.get("alokasi_terpilih") or []
    st.session_state.slide = 6

# ==========================================================
# SLIDE 0: LOGIN OPERATOR BERTUGAS (USERNAME + PIN/PASSWORD)
# ==========================================================
if st.session_state.slide == 0:
    core.render_login_operator(
        app_title="Inbound & WIP - Rack Navigator",
        subtitle="Pilih Operator Forklift & Masukkan PIN Tugas",
        accent_color="#10B981",
        prefix_app="INB",
        operator_list=OPERATOR_INBOUND,
        slide_tujuan=1,
        load_draft_type="INBOUND"
    )
    st.stop()

# ==========================================================
# HEADER STATUS, NOTIFIKASI UNDO & PROGRESS BAR
# ==========================================================
mode_badge = ""
if st.session_state.mode_transaksi == "INBOUND":
    mode_badge = "  |  <span style='color:#10B981;font-weight:bold;'>📥 INBOUND</span>"
elif st.session_state.mode_transaksi == "WIP":
    mode_badge = "  |  <span style='color:#F59E0B;font-weight:bold;'>🏭 AMBIL WIP</span>"

core.render_header_dan_undo(
    app_title="Inbound & WIP - Rack Navigator",
    accent_color="#10B981",
    prefix_app="INB",
    mode_badge=mode_badge,
    reset_keys=['mode_transaksi', 'inbound_draft', '_login_selected_user_INB']
)
core.render_progress_bar(st.session_state.slide, st.session_state.mode_transaksi)

# ==========================================================
# SLIDE 1: PILIH AKSI UTAMA (TASK-FIRST) + BANNER DRAFT INBOUND
# ==========================================================
if st.session_state.slide == 1:
    st.session_state.mode_transaksi = None
    
    draft = st.session_state.get("inbound_draft")
    if draft and draft.get("item"):
        alok_draft = draft.get("alokasi_terpilih") or []
        ringkas_rak = ", ".join(f"{a['Kolom']}-{a['Baris']}" for a in alok_draft) or "-"
        pec_draft = f" + {draft.get('in_qty_c', 0)} {draft.get('in_tipe_c', '')}" if int(draft.get("in_qty_c") or 0) > 0 else ""
        with st.container(border=True):
            st.warning(
                f"📋 **DRAFT INBOUND BELUM SELESAI** *(Disimpan {draft.get('saved_at', '')[-8:]} WIB)*\n\n"
                f"📦 **{draft.get('item')}** | Lot: `{draft.get('in_lot')}`\n\n"
                f"📊 Total: **{draft.get('in_qty_p', 0)} Pallet{pec_draft}** ➔ Rak **{ringkas_rak}**"
            )
            c_dr1, c_dr2 = st.columns([2.6, 1.4])
            with c_dr1:
                if st.button("➡️ LANJUTKAN DRAFT INBOUND", type="primary", use_container_width=True, key="btn_resume_draft"):
                    pulihkan_draft_ke_session(draft)
                    st.rerun()
            with c_dr2:
                st.markdown('<div class="btn-compact">', unsafe_allow_html=True)
                if st.button("🗑️ Hapus Draft", use_container_width=True, key="btn_del_draft"):
                    core.hapus_draft_inbound("INB", st.session_state.operator)
                    st.session_state.pop("_last_draft_sig", None)
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
        st.write("")

    st.markdown("### Pilih Jenis Aktivitas")
    
    st.write("")
    if st.button("📥 INBOUND (Simpan Barang Masuk ke Rak)\nMemasukkan palet baru hasil produksi / datang ke dalam rak", type="primary", use_container_width=True):
        st.session_state.mode_transaksi = "INBOUND"
        pindah_slide(2)
        st.rerun()
        
    st.write("")
    if st.button("🏭 AMBIL WIP (Suplai Bahan ke Produksi)\nMengeluarkan stok dari rak berdasarkan akses 2 pintu (Depan FIFO / Belakang LIFO)", use_container_width=True):
        st.session_state.mode_transaksi = "WIP"
        pindah_slide(2)
        st.rerun()

# ==========================================================
# SLIDE 2 - 4: PEMILIHAN PRODUK (KATEGORI -> GRAMASI -> ITEM)
# ==========================================================
elif st.session_state.slide in [2, 3, 4]:
    core.render_pemilihan_produk(df_master)

# ==========================================================
# CABANG A: PROSES INBOUND (SLIDE 5 -> 6 -> 7)
# ==========================================================
elif st.session_state.mode_transaksi == "INBOUND":
    core.render_alur_inbound_terpadu(
        prefix_app="INB",
        judul_inbound="Detail Barang Masuk (Inbound)",
        default_sisi_masuk="BELAKANG",
        jenis_log="INBOUND (Remote)",
        judul_tele="INBOUND - RACK NAVIGATOR",
        mode_undo_label="INBOUND"
    )

# ==========================================================
# CABANG B: PROSES AMBIL WIP (SLIDE 5 -> 6, 2 PINTU SHUTTLE)
# ==========================================================
elif st.session_state.mode_transaksi == "WIP":
    if st.session_state.slide == 5:
        if st.button("🔙 GANTI ITEM"):
            pindah_slide(4)
            st.rerun()
        st.markdown("### Lokasi Target WIP (Akses 2 Pintu)")
        st.info(core.format_nama_warna_bold(st.session_state.item))
        
        with st.spinner("🔍 Memindai posisi lorong depan & belakang..."):
            semua_data = core.get_all_racking_data()
            df_rekap = core.cari_lokasi_stok_hibrida(semua_data, st.session_state.item)
            
            if df_rekap.empty:
                st.error("❌ Stok WIP untuk item ini tidak ditemukan di seluruh rak!")
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
                    
                    if st.button(btn_label, use_container_width=True, key=f"btn_wip_loc_{idx}"):
                        st.session_state.wip_kolom = row['Kolom']
                        st.session_state.wip_baris = row['Baris']
                        st.session_state.wip_lot = row['Lot Number']
                        st.session_state.pilih_part = row['Part']
                        st.session_state.wip_catatan_rak = row['Catatan']
                        st.session_state.wip_max_pallet = row['Pallet']
                        st.session_state.wip_max_dus = row['Dus']
                        pindah_slide(6)
                        st.rerun()

    elif st.session_state.slide == 6:
        if st.button("🔙 BATAL"):
            pindah_slide(5)
            st.rerun()
        core.render_alur_pengambilan_terpadu(
            prefix_app="INB",
            df_master=df_master,
            judul_ambil="Ambil WIP (Suplai Produksi)",
            label_tombol_eksekusi="🏭 KONFIRMASI AMBIL WIP",
            jenis_log="AMBIL WIP (Remote)",
            judul_tele="AMBIL WIP - RACK NAVIGATOR",
            mode_undo_label="AMBIL WIP",
            reset_keys={
                'kolom': 'wip_kolom',
                'baris': 'wip_baris',
                'lot': 'wip_lot',
                'catatan': 'wip_catatan_rak',
                'max_p': 'wip_max_pallet',
                'max_d': 'wip_max_dus'
            }
        )
