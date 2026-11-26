import streamlit as st
import pandas as pd
import wms_core as core

st.set_page_config(
    page_title="Outbound Plotting & QC - Rack Navigator",
    layout="centered",
    initial_sidebar_state="collapsed"
)
core.inject_custom_css(
    accent_color="#F59E0B",
    accent_border="#D97706",
    accent_bg_rgba="rgba(245, 158, 11, 0.12)"
)

OPERATOR_OUTBOUND = ["Maheswara", "Amir", "Bakir", "Danny", "Joko", "Abdul"]
df_master = core.load_master_data()

DEFAULT_STATE = {
    'slide': 0,
    'mode_transaksi': "OUTBOUND_PLOT",
    'operator': None,
    'undo_record': None,
    'outbound_draft': None,
    'out_jadwal_pilih': None,
    'out_customer_pilih': None,
    'out_last_summary': None,
}
for k, v in DEFAULT_STATE.items():
    if k not in st.session_state:
        st.session_state[k] = v

def pindah_slide(tujuan):
    st.session_state.slide = tujuan

def get_konversi_pcs_item(part_num, item_name):
    pn_c = core.clean_str(part_num).upper()
    nm_c = core.clean_str(item_name)
    if df_master.empty:
        return 0, 0
    sub = df_master[df_master['Part Number'].astype(str).str.strip().str.upper() == pn_c]
    if sub.empty:
        sub = df_master[df_master['Nama Barang'].astype(str).str.strip() == nm_c]
    if sub.empty:
        return 0, 0
    r = sub.iloc[0]
    isi_pal = int(r.get('Total_Pcs_Pallet', 0) or 0)
    satuan = core.get_satuan_kemasan(pn_c)
    pcs_pec = int(r.get('Pcs_Per_Sac', 0) or 0) if satuan == "Sak" else int(r.get('Pcs_Per_Box', 0) or 0)
    return isi_pal, pcs_pec

# ==========================================================
# SLIDE 0: LOGIN OPERATOR BERTUGAS (USERNAME + PIN/PASSWORD)
# ==========================================================
if st.session_state.slide == 0:
    core.render_login_operator(
        app_title="Outbound Plotting & QC - Rack Navigator",
        subtitle="Pengeluaran Barang Ter-Plot PPIC & Approved QC ke Staging (S-1)",
        accent_color="#F59E0B",
        prefix_app="OUTB",
        operator_list=OPERATOR_OUTBOUND,
        slide_tujuan=1,
        default_mode="OUTBOUND_PLOT",
        load_draft_type="OUTBOUND"
    )
    st.stop()

# ==========================================================
# HEADER STATUS, NOTIFIKASI UNDO & PROGRESS BAR
# ==========================================================
st.session_state.mode_transaksi = "OUTBOUND_PLOT"
core.render_header_dan_undo(
    app_title="Outbound Plotting & QC - Rack Navigator",
    accent_color="#F59E0B",
    prefix_app="OUTB",
    mode_badge="  |  <span style='color:#F59E0B;font-weight:bold;'>🚚 OUTBOUND STAGING</span>",
    reset_keys=[
        'out_jadwal_pilih',
        'out_customer_pilih',
        'out_last_summary',
        'outbound_draft',
        '_login_selected_user_OUTB'
    ]
)
core.render_progress_bar(st.session_state.slide, "OUTBOUND_PLOT")

# ==========================================================
# BANNER PEMULIHAN DRAFT OUTBOUND (JIKA ADA SESI TERTUNDA)
# ==========================================================
draft_out = st.session_state.get("outbound_draft")
if draft_out and st.session_state.slide == 1:
    with st.container(border=True):
        st.info(
            f"💾 **Ditemukan Draft Penurunan Barang Tertunda** *(disimpan {draft_out.get('saved_at', '-')[-8:]} WIB)*:\n\n"
            f"📅 Jadwal: **{draft_out.get('jadwal_kirim', '-')}** | 🏢 Customer: **{draft_out.get('target_penerima', '-')}**"
        )
        c_dr1, c_dr2 = st.columns(2)
        with c_dr1:
            if st.button("▶️ LANJUTKAN DRAFT", type="primary", use_container_width=True, key="btn_resume_out_draft"):
                st.session_state.out_jadwal_pilih = draft_out.get('jadwal_kirim')
                st.session_state.out_customer_pilih = draft_out.get('target
