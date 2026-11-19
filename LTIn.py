import streamlit as st
import wms_core as core

st.set_page_config(page_title="Inbound - Rack Navigator", layout="centered", initial_sidebar_state="collapsed")
core.inject_custom_css(accent_color="#10B981", accent_border="#059669", accent_bg_rgba="rgba(16, 185, 129, 0.1)")

OPERATOR_INBOUND = ["Maheswara", "Amir", "Bakir", "Danny", "Joko", "Abdul"]
df_master = core.load_master_data()

keys_state = [
    'slide', 'mode_transaksi', 'kategori', 'gramasi', 'item', 'pilih_part',
    'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'in_sisi_masuk',
    'in_rak_skip', 'alokasi_terpilih', 'ai_scan_result',
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
            <div style="font-size: 18px; font-weight: bold; color: #10B981;">
                🧭 Inbound - Rack Navigator
            </div>
            <div style="font-size: 13px; opacity: 0.8; margin-top: 4px;">
                Pilih Operator Forklift yang Bertugas
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="btn-center">', unsafe_allow_html=True)
    for i in range(0, len(OPERATOR_INBOUND), 2):
        cols = st.columns(2)
        with cols[0]:
            op1 = OPERATOR_INBOUND[i]
            if st.button(f"👤 {op1}", key=f"op_{op1}", use_container_width=True):
                st.session_state.operator = op1
                core.muat_undo_firestore("IN", op1)
                pindah_slide(1); st.rerun()
        if i + 1 < len(OPERATOR_INBOUND):
            with cols[1]:
                op2 = OPERATOR_INBOUND[i+1]
                if st.button(f"👤 {op2}", key=f"op_{op2}", use_container_width=True):
                    st.session_state.operator = op2
                    core.muat_undo_firestore("IN", op2)
                    pindah_slide(1); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.write("")
    with st.expander("➕ Operator Pengganti / Lainnya"):
        op_manual = st.text_input("Ketik Nama Operator", placeholder="Masukkan nama...")
        if st.button("MASUK DENGAN NAMA INI", type="primary", use_container_width=True):
            if op_manual.strip():
                nama_fix = op_manual.strip().title()
                st.session_state.operator = nama_fix
                core.muat_undo_firestore("IN", nama_fix)
                pindah_slide(1); st.rerun()
            else:
                st.warning("Nama operator tidak boleh kosong.")
    st.stop()

# ==========================================================
# HEADER STATUS, NOTIFIKASI UNDO & PROGRESS BAR
# ==========================================================
mode_badge = ""
if st.session_state.mode_transaksi == "INBOUND":
    mode_badge = "  |  <span style='color:#10B981;font-weight:bold;'>📥 INBOUND</span>"
elif st.session_state.mode_transaksi == "INBOUND_SISA":
    mode_badge = "  |  <span style='color:#3B82F6;font-weight:bold;'>↩️ KEMBALI SISA</span>"

core.render_header_dan_undo(
    app_title="Inbound - Rack Navigator",
    accent_color="#10B981",
    prefix_app="IN",
    mode_badge=mode_badge,
    reset_keys=['mode_transaksi', 'kategori', 'gramasi', 'item', 'pilih_part', 'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'in_sisi_masuk', 'in_rak_skip', 'alokasi_terpilih', 'ai_scan_result']
)

if st.session_state.slide >= 1:
    core.render_progress_bar(st.session_state.slide, st.session_state.mode_transaksi or "INBOUND")

# ==========================================================
# SLIDE 1: PILIH JENIS PENERIMAAN BARANG
# ==========================================================
if st.session_state.slide == 1:
    st.markdown("### Pilih Jenis Transaksi Masuk")
    st.markdown('<div class="btn-center">', unsafe_allow_html=True)
    
    if st.button("📥 INBOUND\nBarang Baru dari Produksi", use_container_width=True):
        st.session_state.mode_transaksi = "INBOUND"
        st.session_state.in_sisi_masuk = "BELAKANG"
        pindah_slide(2); st.rerun()
        
    st.write("")
    if st.button("↩️ PENGEMBALIAN SISA PRODUKSI\nBarang Sisa Mesin / WIP Kembali ke Rak", use_container_width=True):
        st.session_state.mode_transaksi = "INBOUND_SISA"
        st.session_state.in_sisi_masuk = "BELAKANG"
        pindah_slide(2); st.rerun()
        
    st.markdown('</div>', unsafe_allow_html=True)

# ==========================================================
# SLIDE 2, 3, 4: SCAN/UPLOAD LABEL ATAU PILIH PRODUK MANUAL
# ==========================================================
elif st.session_state.slide in [2, 3, 4]:
    core.render_pemilihan_produk(df_master)

# ==========================================================
# SLIDE 5, 6, 7: INPUT QTY -> REKOMENDASI RAK -> KONFIRMASI
# ==========================================================
elif st.session_state.slide in [5, 6, 7]:
    is_sisa = (st.session_state.mode_transaksi == "INBOUND_SISA")
    core.render_alur_inbound_terpadu(
        prefix_app="IN",
        judul_inbound="Pengembalian Sisa Produksi" if is_sisa else "Inbound Barang Baru",
        default_sisi_masuk="BELAKANG",
        jenis_log="INBOUND (Pengembalian Sisa)" if is_sisa else "INBOUND (Remote)",
        judul_tele="PENGEMBALIAN SISA PRODUKSI" if is_sisa else "INBOUND - RACK NAVIGATOR",
        mode_undo_label="PENGEMBALIAN SISA" if is_sisa else "INBOUND"
    )
