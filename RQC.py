import streamlit as st
import pandas as pd
import wms_core as core

st.set_page_config(page_title="Release & Approval QC - Rack Navigator", layout="centered", initial_sidebar_state="collapsed")
core.inject_custom_css(accent_color="#2563EB", accent_border="#1D4ED8", accent_bg_rgba="rgba(37, 99, 235, 0.12)")

OPERATOR_QC = ["Admin QC"]
df_master = core.load_master_data()

def get_password_qc():
    try:
        if "qc" in st.secrets and "password" in st.secrets["qc"]:
            return str(st.secrets["qc"]["password"]).strip()
    except Exception:
        pass
    return "rqc2026"

keys_state = [
    'slide', 'mode_transaksi', 'kategori', 'gramasi', 'item', 'pilih_part',
    'operator', 'undo_record'
]
for key in keys_state:
    if key not in st.session_state:
        st.session_state[key] = 0 if key == 'slide' else None

# ==========================================================
# SLIDE 0: LOGIN PETUGAS QC & PASSWORD
# ==========================================================
if st.session_state.slide == 0:
    st.markdown("""
        <div class="db-info-box" style="text-align: center;">
            <div style="font-size: 18px; font-weight: bold; color: #2563EB;">
                🔬 Release & Approval QC - Rack Navigator
            </div>
            <div style="font-size: 13px; opacity: 0.8; margin-top: 4px;">
                Otorisasi Kelayakan Outbound per Lot Produksi
            </div>
        </div>
    """, unsafe_allow_html=True)

    with st.container(border=True):
        pilihan_user = st.selectbox(
            "👤 Pilih User QC:",
            options=OPERATOR_QC + ["Input Nama Petugas Lain..."]
        )
        if pilihan_user == "Input Nama Petugas Lain...":
            nama_qc = st.text_input("Nama Petugas QC:", placeholder="Cth: Budi (QC)").strip().title()
        else:
            nama_qc = pilihan_user

        pwd_input = st.text_input("🔑 Password QC:", type="password", placeholder="Masukkan password QC...")
        st.write("")
        if st.button("🔓 MASUK PORTAL QC", type="primary", use_container_width=True):
            if not nama_qc:
                st.error("⚠️ Nama petugas QC wajib diisi.")
            elif pwd_input.strip() != get_password_qc():
                st.error("❌ Password QC salah!")
            else:
                st.session_state.operator = nama_qc
                st.session_state.mode_transaksi = "QC"
                st.session_state.slide = 2
                st.rerun()
    st.stop()

# ==========================================================
# HEADER STATUS & PROGRESS BAR
# ==========================================================
st.session_state.mode_transaksi = "QC"
core.render_header_dan_undo(
    app_title="Release & Approval QC",
    accent_color="#2563EB",
    prefix_app="QC",
    mode_badge="  |  <span style='color:#2563EB;font-weight:bold;'>🔬 LABELLING QC</span>",
    reset_keys=['kategori', 'gramasi', 'item', 'pilih_part'],
    show_undo=False
)
core.render_progress_bar(st.session_state.slide, "QC")

# ==========================================================
# SLIDE 2 - 4: PEMILIHAN PRODUK (KATEGORI -> GRAMASI -> ITEM)
# ==========================================================
if st.session_state.slide in [2, 3, 4]:
    core.render_pemilihan_produk(df_master, allow_back_to_slide1=False)

# ==========================================================
# SLIDE 5: DAFTAR LOT PER PRODUK (URUTAN FIFO) & APPROVAL QC
# ==========================================================
elif st.session_state.slide == 5:
    if st.button("🔙 GANTI ITEM PRODUK"):
        st.session_state.slide = 4
        st.rerun()

    item_aktif = st.session_state.item
    part_aktif = str(st.session_state.pilih_part).strip().upper()
    satuan = core.get_satuan_kemasan(part_aktif)

    st.markdown("### Approval QC per Lot (Urutan FIFO)")
    st.info(core.format_nama_warna_bold(item_aktif))

    with st.spinner("🔍 Memuat daftar lot dari gudang..."):
        semua_data = core.get_all_racking_data()
        qc_map = core.get_qc_approval_map()

        # Kumpulkan stok di rak (A-G) untuk item ini dikelompokkan per Lot Number
        df_all = pd.DataFrame(semua_data) if semua_data else pd.DataFrame()
        if not df_all.empty:
            df_all = df_all[
                (df_all['Nama Produk'] == item_aktif) &
                (df_all['Kolom'].astype(str).str.strip().str.upper() != 'S')
            ].copy()

        if df_all.empty:
            st.warning("⚠️ Belum ada stok fisik item ini di dalam rak gudang (A–G).")
        else:
            if 'Approval QC' not in df_all.columns:
                df_all['Approval QC'] = ""
            df_all['Baris_Int'] = pd.to_numeric(df_all['Baris'], errors='coerce').fillna(0).astype(int)

            daftar_lot = []
            for lot_val, grp in df_all.groupby('Lot Number'):
                lot_str = str(lot_val).strip()
                jml_p = grp[~grp['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                pec_rows = grp[grp['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
                jml_d = sum(core.parse_keterangan(r['Keterangan'])[0] for _, r in pec_rows.iterrows())

                lokasi_list = sorted(set(f"{r['Kolom']}-{int(r['Baris_Int'])}" for _, r in grp.iterrows()))
                fb_qc = str(grp.iloc[0].get('Approval QC', ''))
                info_qc = core.get_status_qc_lot(qc_map, part_aktif, lot_str, fb_qc)

                daftar_lot.append({
                    'Lot': lot_str,
                    'FIFO_Key': core.parse_fifo_key(lot_str),
                    'Pallet': jml_p,
                    'Dus': jml_d,
                    'Lokasi': ", ".join(lokasi_list),
                    'Status_QC': info_qc.get('status', 'PENDING'),
                    'QC_By': info_qc.get('qc_by', '-'),
                    'QC_Waktu': info_qc.get('waktu', '-'),
                    'QC_Catatan': info_qc.get('catatan', '')
                })

            df_lots = pd.DataFrame(daftar_lot).sort_values(
                by=['FIFO_Key', 'Lot'], ascending=[True, True]
            ).reset_index(drop=True)

            st.caption("📋 *Diurutkan dari Lot & Shift produksi terlama (FIFO) di bagian paling atas.*")

            for idx, r in df_lots.iterrows():
                lot_now = r['Lot']
                st_qc = r['Status_QC']
                pec_txt = f", {r['Dus']} {satuan}" if r['Dus'] > 0 else ""

                with st.container(border=True):
                    if st_qc == "APPROVED":
                        badge_html = "<span style='background:#10B981;color:white;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold;'>✅ APPROVED QC</span>"
                    elif st_qc == "HOLD":
                        badge_html = "<span style='background:#DC2626;color:white;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold;'>⛔ HOLD / KARANTINA</span>"
                    else:
                        badge_html = "<span style='background:#F59E0B;color:black;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold;'>⏳ MENUNGGU APPROVAL QC</span>"

                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                        f"<span style='font-size:19px;font-weight:bold;'>#{idx+1}  🏷️ Lot: <code>{lot_now}</code></span>"
                        f"{badge_html}</div>",
                        unsafe_allow_html=True
                    )
                    st.markdown(f"📦 **Total Stok:** {r['Pallet']} Pallet{pec_txt}  |  📍 **Lokasi Rak:** {r['Lokasi']}")
                    if st_qc in ["APPROVED", "HOLD"]:
                        cat_qc_str = f" | Catatan: *{r['QC_Catatan']}*" if r['QC_Catatan'] else ""
                        st.caption(f"Oleh: **{r['QC_By']}** ({r['QC_Waktu']}){cat_qc_str}")

                    cat_input = st.text_input(
                        "Catatan / Keterangan QC (Opsional):",
                        value=r['QC_Catatan'],
                        placeholder="Cth: Lulus uji dimensi & visual / CoA OK",
                        key=f"note_qc_{part_aktif}_{lot_now}_{idx}"
                    )

                    c_b1, c_b2, c_b3 = st.columns([1.6, 1.4, 1.1])
                    with c_b1:
                        st.markdown('<div class="btn-compact">', unsafe_allow_html=True)
                        if st.button("✅ APPROVE", type="primary", use_container_width=True, key=f"app_btn_{part_aktif}_{lot_now}_{idx}"):
                            with st.spinner("Menyimpan approval QC..."):
                                kol_upd, _ = core.simpan_approval_qc_lot(
                                    part_aktif, item_aktif, lot_now, "APPROVED", st.session_state.operator, cat_input
                                )
                                ket_log = f"QC: {st.session_state.operator} | Rak: {r['Lokasi']}"
                                if cat_input.strip():
                                    ket_log += f" | Catatan: {cat_input.strip()}"
                                core.catat_log_mutasi(
                                    "APPROVAL QC (APPROVED)", r['Lokasi'], "LOT", item_aktif,
                                    part_aktif, lot_now, r['Pallet'], r['Dus'], 0, ket_log
                                )
                                pesan_tele = (
                                    f"✅ <b>APPROVAL QC (RELEASE) - RACK NAVIGATOR</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━\n"
                                    f"📦 <b>Item:</b> {core.escape_html(item_aktif)}\n"
                                    f"🏷️ <b>Lot:</b> <code>{core.escape_html(lot_now)}</code>\n"
                                    f"📊 <b>Stok:</b> {r['Pallet']} Pallet{pec_txt} (Rak: {core.escape_html(r['Lokasi'])})\n"
                                    f"🟢 <b>Status:</b> <b>APPROVED (BOLEH OUTBOUND)</b>\n"
                                    f"🔬 <b>Petugas QC:</b> {core.escape_html(st.session_state.operator)}\n"
                                )
                                if cat_input.strip():
                                    pesan_tele += f"📝 <b>Keterangan:</b> <i>{core.escape_html(cat_input.strip())}</i>\n"
                                core.kirim_notif_telegram(pesan_tele)
                                st.toast(f"Lot {lot_now} APPROVED untuk Outbound!", icon="✅")
                                st.rerun()
                        st.markdown('</div>', unsafe_allow_html=True)

                    with c_b2:
                        st.markdown('<div class="btn-compact">', unsafe_allow_html=True)
                        if st.button("⛔ HOLD", use_container_width=True, key=f"hold_btn_{part_aktif}_{lot_now}_{idx}"):
                            with st.spinner("Menyimpan status HOLD QC..."):
                                core.simpan_approval_qc_lot(
                                    part_aktif, item_aktif, lot_now, "HOLD", st.session_state.operator, cat_input
                                )
                                ket_log = f"QC: {st.session_state.operator} | Rak: {r['Lokasi']}"
                                if cat_input.strip():
                                    ket_log += f" | Alasan: {cat_input.strip()}"
                                core.catat_log_mutasi(
                                    "APPROVAL QC (HOLD)", r['Lokasi'], "LOT", item_aktif,
                                    part_aktif, lot_now, r['Pallet'], r['Dus'], 0, ket_log
                                )
                                pesan_tele = (
                                    f"⛔ <b>HOLD QC (KARANTINA) - RACK NAVIGATOR</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━\n"
                                    f"📦 <b>Item:</b> {core.escape_html(item_aktif)}\n"
                                    f"🏷️ <b>Lot:</b> <code>{core.escape_html(lot_now)}</code>\n"
                                    f"📊 <b>Stok:</b> {r['Pallet']} Pallet{pec_txt} (Rak: {core.escape_html(r['Lokasi'])})\n"
                                    f"🔴 <b>Status:</b> <b>HOLD / DITAHAN QC</b>\n"
                                    f"🔬 <b>Petugas QC:</b> {core.escape_html(st.session_state.operator)}\n"
                                )
                                if cat_input.strip():
                                    pesan_tele += f"📝 <b>Alasan:</b> <i>{core.escape_html(cat_input.strip())}</i>\n"
                                core.kirim_notif_telegram(pesan_tele)
                                st.toast(f"Lot {lot_now} ditandai HOLD!", icon="⛔")
                                st.rerun()
                        st.markdown('</div>', unsafe_allow_html=True)

                    with c_b3:
                        if st_qc != "PENDING":
                            st.markdown('<div class="btn-compact">', unsafe_allow_html=True)
                            if st.button("🔄 Reset", use_container_width=True, key=f"rst_btn_{part_aktif}_{lot_now}_{idx}"):
                                core.simpan_approval_qc_lot(
                                    part_aktif, item_aktif, lot_now, "PENDING", st.session_state.operator, ""
                                )
                                st.toast(f"Status QC Lot {lot_now} direset.", icon="🔄")
                                st.rerun()
                            st.markdown('</div>', unsafe_allow_html=True)
