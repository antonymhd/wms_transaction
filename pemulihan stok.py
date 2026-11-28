import copy
import re
import pandas as pd
import streamlit as st
from app import (
    supabase,
    TBL_STOK_RAK,
    TBL_LOG_MUTASI,
    KOLOM_IDS,
    clean_str,
    load_all_rak_from_supabase,
    save_kolom_to_supabase,
    load_master_produk,
    get_master_konversi,
    parse_keterangan,
    format_keterangan,
    is_same_baris,
    is_valid_item,
    update_bayangan_baris,
)

st.set_page_config(page_title="Pemulihan Stok Rak (Replay Log)", page_icon="🛠️", layout="wide")
st.title("🛠️ Pemulihan Posisi Stok Rak dari Log Mutasi (`id >= 6133`)")

if st.button("1️⃣ Simulasikan Replay 107 Transaksi", type="primary"):
    with st.spinner("Membaca snapshot awal uji_coba_rak dan log_mutasi_rak (id >= 6133)..."):
        all_rak = load_all_rak_from_supabase()
        df_master = load_master_produk()

        res_logs = (
            supabase.table(TBL_LOG_MUTASI)
            .select("*")
            .gte("id", 6133)
            .order("id", desc=False)
            .execute()
        )
        logs = res_logs.data or []

        # Kelompokkan rak per Kolom (A-G dan S)
        rak_by_kol = {k: [] for k in KOLOM_IDS}
        for r in all_rak:
            k = clean_str(r.get("Kolom")).upper()
            if k in rak_by_kol and is_valid_item(r):
                rak_by_kol[k].append(copy.deepcopy(r))

        audit_trail = []

        for lg in logs:
            tx_id = lg["id"]
            arus = clean_str(lg.get("arus")).upper()
            jenis = clean_str(lg.get("jenis_transaksi")).upper()
            lok = clean_str(lg.get("lokasi_rak")).upper()
            kol = clean_str(lg.get("kolom")).upper()
            bar = int(pd.to_numeric(lg.get("baris", 0), errors="coerce") or 0)
            pn = clean_str(lg.get("part_number")).upper()
            nama = clean_str(lg.get("nama_produk"))
            lot = clean_str(lg.get("lot_number"))
            m_pal = int(pd.to_numeric(lg.get("mutasi_pallet", 0), errors="coerce") or 0)
            m_pec = int(pd.to_numeric(lg.get("mutasi_pecahan", 0), errors="coerce") or 0)
            tot_pcs = int(pd.to_numeric(lg.get("total_pcs", 0), errors="coerce") or 0)
            det = clean_str(lg.get("detail_sistem"))

            isi_p, pcs_s = get_master_konversi(df_master, pn)
            m_ec = max(0, tot_pcs - (m_pal * isi_p) - (m_pec * pcs_s)) if isi_p > 0 else tot_pcs

            # A. TRANSAKSI MASUK (INBOUND / KOREKSI MASUK)
            if arus == "MASUK" or "MASUK (KOREKSI" in jenis:
                if kol not in rak_by_kol or bar <= 0:
                    audit_trail.append((tx_id, jenis, lok, "SKIP (lokasi tidak valid)"))
                    continue

                dari_depan = "DEPAN" in det.upper() or "PUSH-BACK" in det.upper()
                baris_items = [d for d in rak_by_kol[kol] if is_same_baris(d, bar)]
                lain_items = [d for d in rak_by_kol[kol] if not is_same_baris(d, bar)]
                baris_items.sort(key=lambda x: pd.to_numeric(x.get("Pallet Ke", 0)))

                new_recs = [
                    {
                        "Kolom": kol, "Baris": float(bar), "Pallet Ke": 0.0,
                        "Part Number": pn, "Lot Number": lot, "Nama Produk": nama,
                        "Status": "Terisi", "Keterangan": "", "Catatan": "",
                        "Approval QC": "", "Jadwal Kirim": "", "Target Penerima": "",
                    }
                    for _ in range(m_pal)
                ]

                if m_pec > 0 or m_ec > 0:
                    merged = False
                    if not dari_depan:
                        for d in baris_items:
                            if (
                                clean_str(d.get("Part Number")).upper() == pn
                                and clean_str(d.get("Lot Number")) == lot
                                and ("Pecahan" in clean_str(d.get("Keterangan")) or "Eceran" in clean_str(d.get("Keterangan")))
                            ):
                                ex_p, ex_e = parse_keterangan(d.get("Keterangan"))
                                d["Keterangan"] = format_keterangan(ex_p + m_pec, ex_e + m_ec)
                                merged = True
                                break
                    if not merged:
                        new_recs.append({
                            "Kolom": kol, "Baris": float(bar), "Pallet Ke": 0.0,
                            "Part Number": pn, "Lot Number": lot, "Nama Produk": nama,
                            "Status": "Terisi", "Keterangan": format_keterangan(m_pec, m_ec),
                            "Catatan": "", "Approval QC": "", "Jadwal Kirim": "", "Target Penerima": "",
                        })

                combined = (new_recs + baris_items) if dari_depan else (baris_items + new_recs)
                rak_by_kol[kol] = lain_items + update_bayangan_baris(combined, kol, bar)
                audit_trail.append((tx_id, jenis, lok, f"OK (+{m_pal}P {m_pec} pec)"))

            # B. TRANSAKSI KELUAR / TURUN STAGING / KOREKSI KELUAR
            elif arus == "KELUAR" or "KELUAR (KOREKSI" in jenis:
                # Parsing lokasi jika berbentuk "A-24" atau "A-24 -> S-1"
                m_lok = re.match(r"^([A-GS])-(\d+)", lok)
                if m_lok:
                    kol, bar = m_lok.group(1), int(m_lok.group(2))

                if kol not in rak_by_kol or bar <= 0:
                    audit_trail.append((tx_id, jenis, lok, "SKIP (lokasi tidak valid)"))
                    continue

                baris_items = [d for d in rak_by_kol[kol] if is_same_baris(d, bar)]
                lain_items = [d for d in rak_by_kol[kol] if not is_same_baris(d, bar)]

                # Cari slot milik (pn, lot) pada baris tersebut
                matching = [
                    d for d in baris_items
                    if clean_str(d.get("Part Number")).upper() == pn and clean_str(d.get("Lot Number")) == lot
                ]
                matching.sort(key=lambda x: pd.to_numeric(x.get("Pallet Ke", 0)), reverse=True)

                # Hitung stok awal & target sisa dari detail_sistem ("Awal: XP Ydus -> Sisa: ZP Wdus")
                m_sisa = re.search(r"Sisa:\s*(\d+)P\s*(\d+)", det)
                m_akhir = re.search(r"Akhir:\s*(\d+)P\s*(\d+)", det)

                if m_sisa:
                    sisa_u, sisa_d, sisa_e = int(m_sisa.group(1)), int(m_sisa.group(2)), 0
                elif m_akhir:
                    sisa_u, sisa_d, sisa_e = int(m_akhir.group(1)), int(m_akhir.group(2)), 0
                else:
                    # Fallback hitung manual dari jumlah slot matching
                    cur_u = sum(1 for d in matching if "Pecahan" not in clean_str(d.get("Keterangan")) and "Eceran" not in clean_str(d.get("Keterangan")))
                    cur_d = sum(parse_keterangan(d.get("Keterangan"))[0] for d in matching)
                    cur_e = sum(parse_keterangan(d.get("Keterangan"))[1] for d in matching)
                    tot_awal_pcs = (cur_u * isi_p) + (cur_d * pcs_s) + cur_e if isi_p > 0 else cur_e
                    tot_sisa_pcs = max(0, tot_awal_pcs - tot_pcs)
                    if isi_p > 0 and pcs_s > 0:
                        sisa_u = tot_sisa_pcs // isi_p
                        rem = tot_sisa_pcs % isi_p
                        sisa_d = rem // pcs_s
                        sisa_e = rem % pcs_s
                    else:
                        sisa_u = max(0, cur_u - m_pal)
                        sisa_d = max(0, cur_d - m_pec)
                        sisa_e = max(0, cur_e - m_ec)

                slot_pec_butuh = 1 if (sisa_d > 0 or sisa_e > 0) else 0
                total_slot_sisa = sisa_u + slot_pec_butuh
                out_qty = max(0, len(matching) - total_slot_sisa)
                pallets_to_remove = {pd.to_numeric(d.get("Pallet Ke", 0)) for d in matching[:out_qty]}

                sisa_matching = [d for d in matching if pd.to_numeric(d.get("Pallet Ke", 0)) not in pallets_to_remove]
                max_sisa_p = max((pd.to_numeric(d.get("Pallet Ke", 0)) for d in sisa_matching), default=None)

                updated_baris = []
                for d in baris_items:
                    is_target = clean_str(d.get("Part Number")).upper() == pn and clean_str(d.get("Lot Number")) == lot
                    if is_target:
                        p_val = pd.to_numeric(d.get("Pallet Ke", 0))
                        if p_val in pallets_to_remove:
                            continue
                        if max_sisa_p is not None and p_val == max_sisa_p:
                            d["Keterangan"] = format_keterangan(sisa_d, sisa_e)
                        else:
                            d["Keterangan"] = ""
                    updated_baris.append(d)

                rak_by_kol[kol] = lain_items + update_bayangan_baris(updated_baris, kol, bar)

                # Jika jenis transaksi TURUN KE STAGING (S-1), masukkan ke Kolom S Baris 1
                if "STAGING" in jenis or "S-1" in jenis or "S-1" in det.upper():
                    s_items = [d for d in rak_by_kol["S"] if is_same_baris(d, 1)]
                    s_lain = [d for d in rak_by_kol["S"] if not is_same_baris(d, 1)]
                    moved_to_s = [
                        {
                            "Kolom": "S", "Baris": 1.0, "Pallet Ke": 0.0,
                            "Part Number": pn, "Lot Number": lot, "Nama Produk": nama,
                            "Status": "Terisi", "Keterangan": "",
                            "Catatan": f"[Asal: {kol}-{bar}]",
                            "Approval QC": "", "Jadwal Kirim": "", "Target Penerima": "",
                        }
                        for _ in range(m_pal)
                    ]
                    if m_pec > 0 or m_ec > 0:
                        moved_to_s.append({
                            "Kolom": "S", "Baris": 1.0, "Pallet Ke": 0.0,
                            "Part Number": pn, "Lot Number": lot, "Nama Produk": nama,
                            "Status": "Terisi", "Keterangan": format_keterangan(m_pec, m_ec),
                            "Catatan": f"[Asal: {kol}-{bar}]",
                            "Approval QC": "", "Jadwal Kirim": "", "Target Penerima": "",
                        })
                    rak_by_kol["S"] = s_lain + update_bayangan_baris(s_items + moved_to_s, "S", 1)

                audit_trail.append((tx_id, jenis, lok, f"OK (Sisa {sisa_u}P {sisa_d} pec)"))

            # C. ERROR CORRECTION EDIT ATRIBUT
            else:
                audit_trail.append((tx_id, jenis, lok, f"INFO ({det})"))

        st.session_state["simulated_rak"] = rak_by_kol
        st.session_state["audit_trail"] = audit_trail

if "simulated_rak" in st.session_state:
    df_audit = pd.DataFrame(st.session_state["audit_trail"], columns=["ID", "Jenis", "Lokasi", "Hasil Replay"])
    st.dataframe(df_audit, use_container_width=True, height=350)

    total_slot_akhir = sum(len(v) for v in st.session_state["simulated_rak"].values())
    st.info(f"Total slot terisi setelah replay: **{total_slot_akhir} slot palet**.")

    if st.button("2️⃣ Simpan Hasil Pemulihan ke `uji_coba_rak` Sekarang", type="primary"):
        with st.spinner("Menyimpan seluruh kolom rak (A–G dan S) ke Supabase..."):
            for kol_id, records in st.session_state["simulated_rak"].items():
                save_kolom_to_supabase(kol_id, records)
        st.success("🎉 Seluruh posisi stok di `uji_coba_rak` berhasil dipulihkan ke kondisi terkini!")
