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
    reset_keys=['out_jadwal_pilih', 'out_customer_pilih', 'out_last_summary']
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
            if st.button("▶️ LANJUTKAN DRAFT", type="primary", use_container_width=True):
                st.session_state.out_jadwal_pilih = draft_out.get('jadwal_kirim')
                st.session_state.out_customer_pilih = draft_out.get('target_penerima')
                pindah_slide(2)
                st.rerun()
        with c_dr2:
            if st.button("🗑️ Hapus Draft", use_container_width=True):
                core.hapus_draft_outbound("OUTB", st.session_state.operator)
                st.rerun()

# Muat data rak & QC map
semua_data = core.get_all_racking_data() or []
qc_map = core.get_qc_approval_map() or {}

df_all = pd.DataFrame(semua_data) if semua_data else pd.DataFrame()
if not df_all.empty:
    for col_opt in ['Jadwal Kirim', 'Target Penerima', 'Approval QC', 'Keterangan', 'Catatan']:
        df_all[col_opt] = df_all[col_opt].apply(core.clean_str) if col_opt in df_all.columns else ""
    df_all['Kolom_Clean'] = df_all['Kolom'].apply(core.clean_str).str.upper()
    df_all['Baris_Int'] = pd.to_numeric(df_all['Baris'], errors='coerce').fillna(0).astype(int)
    df_plotted_rak = df_all[
        (df_all['Kolom_Clean'] != 'S') &
        (df_all['Jadwal Kirim'] != "") &
        (df_all['Target Penerima'] != "")
    ].copy()
else:
    df_plotted_rak = pd.DataFrame()

# ==========================================================
# SLIDE 1: PILIH JADWAL KIRIM & CUSTOMER
# ==========================================================
if st.session_state.slide == 1:
    st.markdown("### 📅 1. Pilih Jadwal Kirim & Customer")

    if df_plotted_rak.empty:
        st.info("📭 Saat ini belum ada jadwal pengiriman yang di-plot PPIC di rak gudang (A–G).")
    else:
        daftar_jadwal = sorted(
            df_plotted_rak['Jadwal Kirim'].unique().tolist(),
            key=core.parse_tgl_kirim_sort
        )
        idx_def = daftar_jadwal.index(st.session_state.out_jadwal_pilih) if st.session_state.out_jadwal_pilih in daftar_jadwal else 0
        pilih_jadwal = st.selectbox("📆 Tanggal Jadwal Kirim:", options=daftar_jadwal, index=idx_def)

        df_by_jk = df_plotted_rak[df_plotted_rak['Jadwal Kirim'] == pilih_jadwal].copy()
        st.markdown(f"**Daftar Pengiriman Tanggal `{pilih_jadwal}`:**")

        for idx_c, (cust_name, grp_c) in enumerate(df_by_jk.groupby('Target Penerima')):
            df_app_c, df_blk_c = core.scan_plotting_untuk_outbound(semua_data, pilih_jadwal, cust_name, qc_map)
            tot_app_p = int(df_app_c['Pallet'].sum()) if not df_app_c.empty else 0
            tot_app_d = int(df_app_c['Dus'].sum()) if not df_app_c.empty else 0
            tot_blk_lot = len(df_blk_c)

            with st.container(border=True):
                if not df_app_c.empty and tot_blk_lot == 0:
                    badge_html = "<span style='background:#10B981;color:white;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold;'>✅ SIAP TURUN (ALL APPROVED)</span>"
                elif not df_app_c.empty and tot_blk_lot > 0:
                    badge_html = f"<span style='background:#F59E0B;color:black;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold;'>⚠️ PARSIAL ({tot_blk_lot} Lot Belum QC)</span>"
                else:
                    badge_html = "<span style='background:#DC2626;color:white;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold;'>🔒 MENUNGGU APPROVAL QC</span>"

                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                    f"<span style='font-size:17px;font-weight:bold;'>🏢 {core.escape_html(cust_name)}</span>"
                    f"{badge_html}</div>",
                    unsafe_allow_html=True
                )
                pec_info = f" + {tot_app_d} Pecahan" if tot_app_d > 0 else ""
                st.caption(
                    f"📦 Siap Turun (Approved QC): **{tot_app_p} Palet{pec_info}** ({len(df_app_c)} Lokasi/Lot)  |  "
                    f"Terkunci QC: **{tot_blk_lot} Lot**"
                )

                if st.button(
                    "🚚 BUKA DAFTAR PENGAMBILAN ➔" if not df_app_c.empty else "🔍 LIHAT DETAIL STATUS QC",
                    type="primary" if not df_app_c.empty else "secondary",
                    use_container_width=True,
                    key=f"btn_cust_outb_{idx_c}"
                ):
                    st.session_state.out_jadwal_pilih = pilih_jadwal
                    st.session_state.out_customer_pilih = cust_name
                    pindah_slide(2)
                    st.rerun()

# ==========================================================
# SLIDE 2: DAFTAR LOKASI APPROVED QC & KONFIRMASI TURUN KE S-1
# ==========================================================
elif st.session_state.slide == 2:
    jk_aktif = st.session_state.out_jadwal_pilih
    cust_aktif = st.session_state.out_customer_pilih

    if st.button("🔙 GANTI CUSTOMER / JADWAL"):
        pindah_slide(1)
        st.rerun()

    st.markdown("### 🚚 2. Turunkan Barang ke Staging (S-1)")
    st.info(f"📅 **Jadwal Kirim:** `{jk_aktif}`  \n🏢 **Target Penerima:** **{cust_aktif}**")

    df_approved, df_blocked = core.scan_plotting_untuk_outbound(semua_data, jk_aktif, cust_aktif, qc_map)

    if not df_blocked.empty:
        with st.expander(f"🔒 {len(df_blocked)} Lokasi/Lot Belum Approved QC / HOLD (Terkunci)", expanded=df_approved.empty):
            st.caption("Barang di bawah ini TIDAK BOLEH diturunkan sebelum di-approve QC di aplikasi RQC:")
            for _, rb in df_blocked.iterrows():
                st_lbl = "⛔ HOLD QC" if rb['QC_Status'] == "HOLD" else "⏳ Menunggu QC"
                pec_b = f" + {rb['Dus']} {rb['Satuan']}" if rb['Dus'] > 0 else ""
                st.markdown(
                    f"- **Rak {rb['Lokasi']}** | {rb['Nama Produk']} | Lot `{rb['Lot Number']}` "
                    f"({rb['Pallet']}P{pec_b}) — **{st_lbl}**"
                )

    if df_approved.empty:
        st.error("🚫 Belum ada palet berstatus **APPROVED QC** untuk customer ini. Hubungi petugas QC terlebih dahulu.")
    else:
        st.caption(
            "📋 *Urutan Strict FIFO. Centang lokasi yang sudah diambil forklift, sesuaikan angka jika aktual lapangan berbeda, "
            "lalu tekan tombol konfirmasi di bagian bawah.*"
        )

        saved_items_map = {}
        if (draft_out
                and draft_out.get("jadwal_kirim") == jk_aktif
                and draft_out.get("target_penerima") == cust_aktif):
            saved_items_map = draft_out.get("items_state", {})

        daftar_eksekusi = []
        draft_items_to_save = {}

        for idx, r in df_approved.iterrows():
            loc_str = r['Lokasi']
            pn_now = r['Part Number']
            nama_now = r['Nama Produk']
            lot_now = r['Lot Number']
            satuan = r['Satuan']
            max_p = int(r['Pallet'])
            max_d = int(r['Dus'])
            akses_str = r['AksesPintu']
            row_uid = f"{loc_str}_{pn_now}_{lot_now}"

            prev_st = saved_items_map.get(row_uid, {})
            def_chk = prev_st.get("checked", True)
            def_tp = min(max_p, int(prev_st.get("turun_p", max_p)))
            def_td = min(max_d, int(prev_st.get("turun_d", max_d)))

            with st.container(border=True):
                c_top1, c_top2 = st.columns([3.2, 1.3])
                with c_top1:
                    st.markdown(
                        f"<div style='font-size:19px;font-weight:bold;'>#{idx+1}  📍 RAK {loc_str}  |  🏷️ <code>{lot_now}</code></div>",
                        unsafe_allow_html=True
                    )
                with c_top2:
                    ikut_turun = st.checkbox(
                        "✅ Ambil",
                        value=def_chk,
                        key=f"chk_take_{jk_aktif}_{cust_aktif}_{row_uid}"
                    )

                st.markdown(f"📦 {core.format_nama_warna_bold(nama_now)} (`{pn_now}`)", unsafe_allow_html=True)

                akses_color = "#10B981" if "DEPAN" in akses_str or "BELAKANG (" in akses_str else "#F59E0B"
                st.markdown(
                    f"🚪 **Akses:** <span style='color:{akses_color};font-weight:bold;'>{akses_str}</span>  |  "
                    f"📊 **Plot Tersedia:** **{max_p} Palet**" + (f" + **{max_d} {satuan}**" if max_d > 0 else ""),
                    unsafe_allow_html=True
                )
                if r.get('Catatan'):
                    st.caption(f"🔖 Catatan Rak: {r['Catatan']}")

                c_q1, c_q2 = st.columns(2)
                with c_q1:
                    val_tp = st.number_input(
                        "Palet Utuh Turun ke S-1",
                        min_value=0,
                        max_value=max_p,
                        value=def_tp if ikut_turun else 0,
                        step=1,
                        disabled=not ikut_turun,
                        key=f"tp_{jk_aktif}_{cust_aktif}_{row_uid}"
                    )
                with c_q2:
                    val_td = st.number_input(
                        f"Pecahan Turun ({satuan})",
                        min_value=0,
                        max_value=max_d,
                        value=def_td if ikut_turun else 0,
                        step=1,
                        disabled=not ikut_turun or max_d == 0,
                        key=f"td_{jk_aktif}_{cust_aktif}_{row_uid}"
                    )

                draft_items_to_save[row_uid] = {
                    "checked": bool(ikut_turun),
                    "turun_p": int(val_tp),
                    "turun_d": int(val_td)
                }

                if ikut_turun and (val_tp > 0 or val_td > 0):
                    daftar_eksekusi.append({
                        'Kolom': r['Kolom'],
                        'Baris': int(r['Baris']),
                        'Lokasi': loc_str,
                        'Part Number': pn_now,
                        'Nama Produk': nama_now,
                        'Lot Number': lot_now,
                        'Satuan': satuan,
                        'AksesPintu': akses_str,
                        'Plot_P': max_p,
                        'Plot_D': max_d,
                        'Turun_P': int(val_tp),
                        'Turun_D': int(val_td)
                    })

        draft_sig = f"{jk_aktif}|{cust_aktif}|" + str(draft_items_to_save)
        if st.session_state.get("_last_out_draft_sig") != draft_sig:
            core.simpan_draft_outbound("OUTB", st.session_state.operator, {
                "jadwal_kirim": jk_aktif,
                "target_penerima": cust_aktif,
                "items_state": draft_items_to_save
            })
            st.session_state["_last_out_draft_sig"] = draft_sig

        st.divider()
        catatan_muat = st.text_input(
            "📝 Catatan Pengiriman / No. Polisi Truk (Opsional):",
            placeholder="Cth: Truk B 9123 KLA / Sopir Pak Hendra",
            key="cat_outbound_staging"
        )

        tot_turun_p = sum(x['Turun_P'] for x in daftar_eksekusi)
        tot_turun_d = sum(x['Turun_D'] for x in daftar_eksekusi)

        if not daftar_eksekusi:
            st.warning("⚠️ Centang minimal 1 lokasi dengan kuantitas > 0 untuk diturunkan ke Staging (S-1).")
        else:
            st.success(
                f"📋 **Ringkasan Siap Turun ke Staging (S-1):** **{tot_turun_p} Palet Utuh**"
                + (f" + **{tot_turun_d} Pecahan**" if tot_turun_d > 0 else "")
                + f" dari **{len(daftar_eksekusi)} lokasi rak**."
            )

            if st.button("🚚 KONFIRMASI TURUNKAN KE STAGING (S-1)", type="primary", use_container_width=True):
                with st.spinner("Memindahkan palet terpilih ke Staging (S-1)..."):
                    kolom_unik = sorted({x['Kolom'] for x in daftar_eksekusi})
                    doc_refs_asal = {
                        k: core.db.collection(core.NAMA_DATABASE).document(f"Kolom_{k}")
                        for k in kolom_unik
                    }
                    doc_ref_staging = core.db.collection(core.NAMA_DATABASE).document("Kolom_S")
                    transaction = core.db.transaction()

                    try:
                        backup_dict, ringkasan_eks = core.proses_turun_plotting_ke_staging_multi(
                            transaction,
                            doc_refs_asal,
                            doc_ref_staging,
                            daftar_eksekusi,
                            jk_aktif,
                            cust_aktif,
                            st.session_state.operator,
                            catatan_muat
                        )

                        tele_lines = []
                        for re_it in ringkasan_eks:
                            isi_pal, pcs_pec = get_konversi_pcs_item(re_it['Part Number'], re_it['Nama Produk'])
                            tot_pcs_it = (re_it['Turun_P'] * isi_pal) + (re_it['Turun_D'] * pcs_pec)
                            pec_s = f" + {re_it['Turun_D']} {re_it['Satuan']}" if re_it['Turun_D'] > 0 else ""

                            ket_lg = (
                                f"Customer: {cust_aktif} ({jk_aktif}) | Op: {st.session_state.operator} | "
                                f"Turun ke S-1: {re_it['Turun_P']}P{pec_s}"
                            )
                            if catatan_muat.strip():
                                ket_lg += f" | Catatan: {catatan_muat.strip()}"

                            core.catat_log_mutasi(
                                "TURUN KE STAGING (OUTBOUND)",
                                re_it['Kolom'],
                                re_it['Baris'],
                                re_it['Nama Produk'],
                                re_it['Part Number'],
                                re_it['Lot Number'],
                                re_it['Turun_P'],
                                re_it['Turun_D'],
                                tot_pcs_it,
                                ket_lg
                            )

                            tele_lines.append(
                                f"• Rak <b>{re_it['Kolom']}-{re_it['Baris']}</b> | "
                                f"{core.escape_html(re_it['Nama Produk'])} (Lot <code>{core.escape_html(re_it['Lot Number'])}</code>): "
                                f"<b>{re_it['Turun_P']}P{pec_s}</b>"
                            )

                        pesan_tele = (
                            f"🚚 <b>TURUN KE STAGING (S-1) - RACK NAVIGATOR</b>\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"📅 <b>Jadwal Kirim:</b> {core.escape_html(jk_aktif)}\n"
                            f"🏢 <b>Customer:</b> {core.escape_html(cust_aktif)}\n"
                            f"👤 <b>Operator Forklift:</b> {core.escape_html(st.session_state.operator)}\n"
                            f"📦 <b>Rincian Barang di Staging S-1:</b>\n" + "\n".join(tele_lines) + "\n"
                        )
                        if catatan_muat.strip():
                            pesan_tele += f"📝 <b>Catatan:</b> <i>{core.escape_html(catatan_muat.strip())}</i>\n"
                        core.kirim_notif_telegram(pesan_tele)

                        core.hapus_draft_outbound("OUTB", st.session_state.operator)
                        st.session_state.pop("_last_out_draft_sig", None)
                        core.get_all_racking_data.clear()

                        msg_sukses = (
                            f"Berhasil menurunkan {tot_turun_p}P"
                            + (f" + {tot_turun_d} Pecahan" if tot_turun_d > 0 else "")
                            + f" untuk {cust_aktif} ke Staging (S-1)."
                        )
                        core.simpan_undo_firestore(
                            "OUTB",
                            st.session_state.operator,
                            backup_dict,
                            msg_sukses,
                            mode_label="OUTBOUND STAGING"
                        )

                        st.session_state.out_last_summary = {
                            "jadwal_kirim": jk_aktif,
                            "target_penerima": cust_aktif,
                            "items": ringkasan_eks
                        }
                        st.toast(msg_sukses, icon="✅")
                        pindah_slide(3)
                        st.rerun()

                    except ValueError as err:
                        st.error(f"❌ Validasi Stok: {err}")
                    except Exception as err:
                        st.error(f"❌ Kesalahan Sistem: {err}")

# ==========================================================
# SLIDE 3: SELESAI & LAPOR SISA BUFFER DI STAGING (S-1)
# ==========================================================
elif st.session_state.slide == 3:
    last_sum = st.session_state.get("out_last_summary") or {}
    jk_done = last_sum.get("jadwal_kirim", "-")
    cust_done = last_sum.get("target_penerima", "-")
    items_done = last_sum.get("items", [])

    st.success(f"✅ **Barang untuk `{cust_done}` (Jadwal `{jk_done}`) sudah berada di Staging (`S-1`).**")

    with st.container(border=True):
        st.markdown("#### 🛡️ Lapor Sisa Buffer di Staging (`S-1`) ke Admin PPIC")
        st.caption(
            "Jika setelah muat ke truk selesai ternyata ada palet cadangan/buffer yang tersisa di Staging `S-1`, "
            "laporkan di sini agar Admin PPIC mendapat notifikasi Telegram beserta rekomendasi rak masuk kembali."
        )

        if not items_done:
            st.info("Tidak ada daftar item dari transaksi terakhir.")
        else:
            opsi_buf_items = [
                f"{x['Nama Produk']} | Lot {x['Lot Number']} ({x['Part Number']})"
                for x in items_done
            ]
            pilih_buf_lbl = st.selectbox("Pilih Item & Lot yang Tersisa di S-1:", options=opsi_buf_items)
            idx_buf = opsi_buf_items.index(pilih_buf_lbl)
            it_buf = items_done[idx_buf]
            satuan_buf = it_buf['Satuan']

            c_b1, c_b2 = st.columns(2)
            with c_b1:
                sisa_buf_p = st.number_input("Sisa Palet Utuh di S-1:", min_value=0, value=1, step=1, key="buf_sisa_p")
            with c_b2:
                sisa_buf_d = st.number_input(f"Sisa Pecahan ({satuan_buf}):", min_value=0, value=0, step=1, key="buf_sisa_d")

            ket_buf = st.text_input(
                "Keterangan Tambahan (Opsional):",
                placeholder="Cth: Muatan truk penuh, sisa 1 palet buffer di S-1",
                key="buf_ket_input"
            )

            if st.button("📲 KIRIM LAPORAN SISA BUFFER KE TELEGRAM PPIC", type="primary", use_container_width=True):
                if sisa_buf_p == 0 and sisa_buf_d == 0:
                    st.warning("⚠️ Isi minimal 1 palet atau pecahan yang tersisa di Staging S-1.")
                else:
                    with st.spinner("Mencari rekomendasi rak kembalinya & mengirim laporan..."):
                        fresh_data = core.get_all_racking_data() or []
                        df_rek_kembali = core.hitung_kandidat_rak_inbound(
                            fresh_data,
                            it_buf['Nama Produk'],
                            it_buf['Part Number'],
                            sisi_masuk="DEPAN",
                            target_lot=it_buf['Lot Number']
                        )
                        saran_rak_str = "-"
                        if not df_rek_kembali.empty:
                            top_raks = [
                                f"{rk['Loc']} ({rk['Status']})"
                                for _, rk in df_rek_kembali.head(3).iterrows()
                            ]
                            saran_rak_str = ", ".join(top_raks)

                        pec_b_str = f" + {sisa_buf_d} {satuan_buf}" if sisa_buf_d > 0 else ""
                        pesan_buf = (
                            f"🛡️ <b>LAPORAN SISA BUFFER STAGING (S-1) - RACK NAVIGATOR</b>\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"🏢 <b>Ex Customer:</b> {core.escape_html(cust_done)} ({core.escape_html(jk_done)})\n"
                            f"📦 <b>Item:</b> {core.escape_html(it_buf['Nama Produk'])}\n"
                            f"🏷️ <b>Lot:</b> <code>{core.escape_html(it_buf['Lot Number'])}</code>\n"
                            f"📊 <b>Sisa Buffer di S-1:</b> <b>{sisa_buf_p} Palet{pec_b_str}</b>\n"
                            f"📍 <b>Saran Rak Masuk Kembali (Depan):</b> {core.escape_html(saran_rak_str)}\n"
                            f"👤 <b>Pelapor:</b> {core.escape_html(st.session_state.operator)}\n"
                        )
                        if ket_buf.strip():
                            pesan_buf += f"📝 <b>Catatan:</b> <i>{core.escape_html(ket_buf.strip())}</i>\n"

                        core.kirim_notif_telegram(pesan_buf)
                        st.success(f"📲 Laporan sisa buffer terkirim ke Telegram PPIC! (Saran rak: **{saran_rak_str}**)")

    st.write("")
    if st.button("🏠 KEMBALI KE DAFTAR JADWAL PENGIRIMAN", use_container_width=True):
        st.session_state.out_last_summary = None
        pindah_slide(1)
        st.rerun()
