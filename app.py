import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from supabase import create_client, Client
import io
import copy
import re
import math
import html
from datetime import datetime, timedelta, timezone

# ==========================================================
# 1. KONFIGURASI WEB, STATE & SISTEM LOGIN
# ==========================================================
st.set_page_config(page_title="WMS Data Entry - Hokkan Deltapack", page_icon="📝", layout="wide")

DEFAULT_SESSION_STATE = {
    'logged_in': False,
    'sb_usage': {'read': 0, 'write': 0, 'delete': 0},
    'doc_sizes': {},
    'undo_dict': {},
    'action_msg': "",
    'action_status': None,
    'last_reindex': "Belum pernah",
    'form_reset_counter': 0,
    'in_lokasi_dicari': False, 'in_k_pilih': "A", 'in_b_pilih': 1,
    'out_lokasi_dicari': False, 'out_k_pilih': "A", 'out_b_pilih': 1,
    'relok_dicari': False, 'relok_ak': "A", 'relok_ab': 1,
    'kor_dicari': False, 'kor_k': "A", 'kor_b': 1,
    'print_rak_cache': {},
    'print_ready': False,
    'log_loaded_records': [],
    'plot_all_records_cache': [],
    'plot_target_part': "",
    'plot_target_item': "",
    'plot_search_ready': False,
    'active_plots_list': None,
}
for k, v in DEFAULT_SESSION_STATE.items():
    if k not in st.session_state:
        st.session_state[k] = copy.deepcopy(v)

def log_sb_usage(op_type, count=1):
    if op_type in st.session_state['sb_usage']:
        st.session_state['sb_usage'][op_type] += count

if not st.session_state['logged_in']:
    st.title("🔒 WMS Security Login")
    st.caption("Silakan masukkan kata sandi untuk mengakses form mutasi gudang.")

    correct_password = st.secrets.get("wms_password", "Admin123")
    with st.form("login_form"):
        pwd_input = st.text_input("Kata Sandi", type="password")
        if st.form_submit_button("Login", type="primary"):
            if pwd_input == correct_password:
                st.session_state['logged_in'] = True
                st.rerun()
            else:
                st.error("Kata sandi salah!")
    st.stop()

# ==========================================================
# 2. KONSTANTA & INISIALISASI DATABASE (SUPABASE)
# ==========================================================
TBL_STOK_RAK = 'stok_rak'
TBL_LOG_MUTASI = 'log_mutasi_rak'
KOLOM_OPTIONS = ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"]
KOLOM_IDS = ["A", "B", "C", "D", "E", "F", "G", "S"]
KOLOM_RACKING_AG = ["A", "B", "C", "D", "E", "F", "G"]
LIMIT_SUPABASE_BYTES = 524_288_000  # 500 MiB Free Plan Supabase

@st.cache_resource
def init_supabase() -> Client:
    url = str(st.secrets["supabase"]["url"]).strip().rstrip("/")
    key = str(st.secrets["supabase"]["key"]).strip()
    return create_client(url, key)

supabase = init_supabase()

# ==========================================================
# 3. FUNGSI BANTUAN TERPUSAT & ADAPTER SUPABASE
# ==========================================================
def get_waktu_jkt():
    """Mendapatkan waktu saat ini di zona WIB (UTC+7)."""
    return datetime.now(timezone.utc) + timedelta(hours=7)

def clean_str(val):
    """Membersihkan nilai None/NaN maupun tempelan '| nan' secara konsisten."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    if s.lower() in ["nan", "none", "<na>"]:
        return ""
    return re.sub(r'(\s*\|\s*nan\b|\bnan\s*\|\s*)', '', s, flags=re.IGNORECASE).strip()

def db_row_to_app_dict(row: dict) -> dict:
    """Mengonversi 1 baris tabel PostgreSQL stok_rak ke format dictionary aplikasi."""
    return {
        'Kolom': clean_str(row.get('kolom')).upper(),
        'Baris': float(pd.to_numeric(row.get('baris', 0), errors='coerce') or 0),
        'Pallet Ke': float(pd.to_numeric(row.get('pallet_ke', 0), errors='coerce') or 0),
        'Part Number': clean_str(row.get('part_number')).upper(),
        'Lot Number': clean_str(row.get('lot_number')),
        'Nama Produk': clean_str(row.get('nama_produk')),
        'Keterangan': clean_str(row.get('keterangan')),
        'Catatan': clean_str(row.get('catatan')),
        'Approval QC': clean_str(row.get('approval_qc')),
        'Jadwal Kirim': clean_str(row.get('jadwal_kirim')),
        'Target Penerima': clean_str(row.get('target_penerima')),
        'Status': clean_str(row.get('status')) or 'Terisi',
    }

def app_dict_to_db_row(item: dict, kolom_fallback: str = "") -> dict:
    """Mengonversi 1 dictionary aplikasi ke format kolom PostgreSQL stok_rak."""
    kol = clean_str(item.get('Kolom') or kolom_fallback).upper()
    bar = int(float(pd.to_numeric(item.get('Baris', 0), errors='coerce') or 0))
    plt_ke = int(float(pd.to_numeric(item.get('Pallet Ke', 0), errors='coerce') or 0))
    return {
        'kolom': kol,
        'baris': bar,
        'pallet_ke': plt_ke,
        'part_number': clean_str(item.get('Part Number')).upper(),
        'nama_produk': clean_str(item.get('Nama Produk')),
        'lot_number': clean_str(item.get('Lot Number')),
        'keterangan': clean_str(item.get('Keterangan')),
        'catatan': clean_str(item.get('Catatan')),
        'approval_qc': clean_str(item.get('Approval QC')),
        'jadwal_kirim': clean_str(item.get('Jadwal Kirim')),
        'target_penerima': clean_str(item.get('Target Penerima')),
        'status': clean_str(item.get('Status')) or 'Terisi',
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }

def load_kolom_from_supabase(kolom_id: str) -> list:
    """Mengambil seluruh slot aktif pada 1 Kolom (A-G atau S) dari Supabase."""
    kol = clean_str(kolom_id).upper()
    semua = []
    step = 1000
    start = 0
    while True:
        res = (
            supabase.table(TBL_STOK_RAK)
            .select("*")
            .eq("kolom", kol)
            .order("baris", desc=False)
            .order("pallet_ke", desc=False)
            .range(start, start + step - 1)
            .execute()
        )
        log_sb_usage('read', 1)
        batch = res.data or []
        semua.extend(db_row_to_app_dict(r) for r in batch)
        if len(batch) < step:
            break
        start += step
    record_doc_size(f"Kolom_{kol}", semua)
    return semua

def load_all_rak_from_supabase() -> list:
    """Mengambil seluruh slot aktif di seluruh gudang dari Supabase."""
    semua = []
    step = 1000
    start = 0
    while True:
        res = (
            supabase.table(TBL_STOK_RAK)
            .select("*")
            .order("kolom", desc=False)
            .order("baris", desc=False)
            .order("pallet_ke", desc=False)
            .range(start, start + step - 1)
            .execute()
        )
        log_sb_usage('read', 1)
        batch = res.data or []
        semua.extend(db_row_to_app_dict(r) for r in batch)
        if len(batch) < step:
            break
        start += step

    by_kol = {k: [] for k in KOLOM_IDS}
    for d in semua:
        k = d['Kolom']
        if k in by_kol:
            by_kol[k].append(d)
    for k, arr in by_kol.items():
        record_doc_size(f"Kolom_{k}", arr)
    return semua

def save_kolom_to_supabase(kolom_id: str, records_list: list):
    """
    Menyimpan kondisi terbaru seluruh slot pada 1 Kolom ke Supabase secara atomik:
    1. Upsert seluruh slot valid terbaru.
    2. Hapus slot sisa lama di kolom tersebut yang sudah keluar/kosong.
    """
    kol = clean_str(kolom_id).upper()
    rows_map = {}
    for item in records_list:
        if not is_valid_item(item):
            continue
        db_row = app_dict_to_db_row(item, kol)
        if db_row['baris'] > 0 and db_row['pallet_ke'] > 0:
            rows_map[(db_row['baris'], db_row['pallet_ke'])] = db_row

    payload = list(rows_map.values())
    if not payload:
        supabase.table(TBL_STOK_RAK).delete().eq("kolom", kol).execute()
        log_sb_usage('delete', 1)
        record_doc_size(f"Kolom_{kol}", [])
        return

    BATCH_SIZE = 500
    for i in range(0, len(payload), BATCH_SIZE):
        batch = payload[i : i + BATCH_SIZE]
        supabase.table(TBL_STOK_RAK).upsert(batch, on_conflict="kolom,baris,pallet_ke").execute()
        log_sb_usage('write', 1)

    # Bersihkan baris/slot lama yang terhapus akibat outbound/relokasi/koreksi
    existing_res = (
        supabase.table(TBL_STOK_RAK)
        .select("id,baris,pallet_ke")
        .eq("kolom", kol)
        .execute()
    )
    log_sb_usage('read', 1)
    ids_to_delete = [
        r['id'] for r in (existing_res.data or [])
        if (int(r['baris']), int(r['pallet_ke'])) not in rows_map
    ]
    if ids_to_delete:
        for i in range(0, len(ids_to_delete), 200):
            supabase.table(TBL_STOK_RAK).delete().in_("id", ids_to_delete[i : i + 200]).execute()
            log_sb_usage('delete', 1)

    record_doc_size(f"Kolom_{kol}", [db_row_to_app_dict(r) for r in payload])

def make_g_key(lot, prod, cat, jk="", tp=""):
    """Membuat kunci pengelompokan yang 100% identik antara UI dan Transaksi."""
    return f"{clean_str(lot)}_{clean_str(prod)}_{clean_str(cat)}_{clean_str(jk)}_{clean_str(tp)}"

def match_g_key(d, target_key):
    """Mencocokkan dictionary record dengan target Group_Key."""
    return make_g_key(
        d.get('Lot Number'), d.get('Nama Produk'), d.get('Catatan'),
        d.get('Jadwal Kirim'), d.get('Target Penerima')
    ) == target_key

def is_valid_item(d):
    """Mengecek apakah slot memiliki Part Number yang valid."""
    return clean_str(d.get('Part Number')) != ""

def is_same_baris(d, baris_num):
    """Mengecek kesesuaian nomor baris secara aman."""
    return float(d.get('Baris', 0) or 0) == float(baris_num)

def get_baris_records(data_list, baris_num, only_valid=True):
    """Menyaring salinan record pada satu nomor baris tertentu secara aman."""
    return [
        copy.deepcopy(d) for d in data_list
        if is_same_baris(d, baris_num) and (not only_valid or is_valid_item(d))
    ]

def parse_keterangan(ket_str):
    """Membaca jumlah Pecahan (Dus/Sak) dan Eceran (Pcs) dari string Keterangan."""
    s = clean_str(ket_str).lower()
    m_pec = re.search(r'pecahan:\s*(\d+)', s)
    m_ec = re.search(r'eceran:\s*(\d+)', s)
    return (int(m_pec.group(1)) if m_pec else 0, int(m_ec.group(1)) if m_ec else 0)

def format_keterangan(pecahan, eceran):
    """Membentuk string Keterangan standar dari nilai Pecahan dan Eceran."""
    parts = []
    if pecahan > 0:
        parts.append(f"Pecahan: {int(pecahan)} qty")
    if eceran > 0:
        parts.append(f"Eceran: {int(eceran)} pcs")
    return " | ".join(parts)

def get_satuan_kemasan(part_number):
    """Menentukan label satuan pecahan berdasarkan akhiran Part Number."""
    pn = clean_str(part_number).upper()
    if pn.endswith(('D', 'G')):
        return "dus"
    if pn.endswith('C'):
        return "sak"
    if pn.endswith('A'):
        return "nictainer"
    return "qty"

def set_kategori_produk(nama):
    """Mengelompokkan nama produk ke kategori standar."""
    s = clean_str(nama).upper()
    if s.startswith('PREFORM'):
        return 'PREFORM'
    if s.startswith('CUP NATURAL'):
        return 'CUP NATURAL'
    if s.startswith('CUP PRINTING'):
        return 'CUP PRINTING'
    if s.startswith('CUP COLOR'):
        return 'CUP COLOR'
    if s.startswith('CAP'):
        return 'CAP'
    return 'LAINNYA'

def parse_fifo_lot_key(lot_str):
    """Kunci urut FIFO murni dari nomor lot (YYMMDD + Shift)."""
    s = clean_str(lot_str)
    m_sh = re.search(r'([123])(\d{2})(\d{2})(\d{2})$', s)
    if m_sh:
        sh, dd, mm, yy = m_sh.groups()
        return f"{yy}{mm}{dd}{sh}"
    m_dt = re.search(r'(\d{2})(\d{2})(\d{2})$', s)
    if m_dt:
        dd, mm, yy = m_dt.groups()
        return f"{yy}{mm}{dd}0"
    return "9999999"

def get_master_konversi(df_master, part_num):
    """Mengambil (Isi 1 Pallet pcs, Pcs per Dus/Sak) dari Master_Produk.csv."""
    pn = clean_str(part_num).upper()
    if df_master.empty or 'Part Number Clean' not in df_master.columns or pn not in df_master['Part Number Clean'].values:
        return 0, 0
    m_row = df_master[df_master['Part Number Clean'] == pn].iloc[0]
    isi_pallet = int(pd.to_numeric(m_row.get('Isi 1 Pallet (pcs)', 0), errors='coerce') or 0)
    col_satuan = 'Sac / Pallet' if pn.endswith('C') else 'Box / Pallet'
    pcs_per_satuan = int(pd.to_numeric(m_row.get(col_satuan, 0), errors='coerce') or 0)
    return isi_pallet, pcs_per_satuan

def hitung_konversi_mutasi_pcs(df_master, part_num, awal_p, awal_d, awal_pcs, sisa_p, sisa_d, sisa_pcs):
    """
    Menghitung selisih keluar berbasis total Pcs lalu memecahnya ke (Pallet, Dus/Sak, Eceran Pcs)
    sehingga mencegah angka minus saat palet utuh dipecah.
    """
    isi_pallet, pcs_per_satuan = get_master_konversi(df_master, part_num)
    if isi_pallet > 0 and pcs_per_satuan > 0:
        tot_awal = (awal_p * isi_pallet) + (awal_d * pcs_per_satuan) + awal_pcs
        tot_sisa = (sisa_p * isi_pallet) + (sisa_d * pcs_per_satuan) + sisa_pcs
        diff_pcs = tot_awal - tot_sisa
        if diff_pcs < 0:
            return None, None, None, -1
        kel_p = diff_pcs // isi_pallet
        rem = diff_pcs % isi_pallet
        kel_d = rem // pcs_per_satuan
        kel_pcs = rem % pcs_per_satuan
        return int(kel_p), int(kel_d), int(kel_pcs), int(diff_pcs)

    diff_p = int(awal_p - sisa_p)
    diff_d = int(awal_d - sisa_d)
    diff_ec = int(awal_pcs - sisa_pcs)
    if diff_p < 0 or (diff_p == 0 and diff_d < 0) or (diff_p == 0 and diff_d == 0 and diff_ec < 0):
        return None, None, None, -1
    return diff_p, diff_d, diff_ec, 0

def hitung_ringkasan_stok_lot(records_list, df_master):
    """
    Menghitung total stok (Utuh, Dus/Sak, Eceran Pcs, Total Pcs) per (Part Number, Lot Number)
    untuk menjaga keseimbangan mutasi saat koreksi data.
    """
    summary = {}
    for d in records_list:
        pn = clean_str(d.get('Part Number')).upper()
        if not pn:
            continue
        lot = clean_str(d.get('Lot Number')) or "-"
        nama = clean_str(d.get('Nama Produk'))
        key = (pn, lot)
        if key not in summary:
            summary[key] = {'nama': nama, 'utuh': 0, 'dus': 0, 'pcs_ec': 0, 'tot_pcs': 0}
        elif nama and not summary[key]['nama']:
            summary[key]['nama'] = nama

        ket = clean_str(d.get('Keterangan')).lower()
        if 'pecahan' in ket or 'eceran' in ket:
            p_val, e_val = parse_keterangan(ket)
            summary[key]['dus'] += p_val
            summary[key]['pcs_ec'] += e_val
        else:
            summary[key]['utuh'] += 1

    for (pn, _), vals in summary.items():
        isi_p, pcs_s = get_master_konversi(df_master, pn)
        if isi_p > 0:
            vals['tot_pcs'] = (vals['utuh'] * isi_p) + (vals['dus'] * pcs_s) + vals['pcs_ec']
        else:
            vals['tot_pcs'] = vals['pcs_ec']
    return summary

def hitung_ulang_pcs_log(df_log, df_master):
    """Menghitung ulang Total Pcs mutasi secara dinamis berdasarkan Master_Produk.csv terbaru."""
    if df_log.empty:
        return df_log

    pcs_recalc_list = []
    selisih_info_list = []

    for _, row in df_log.iterrows():
        pn = clean_str(row.get('Part Number')).upper()
        m_pallet = float(pd.to_numeric(row.get('Mutasi Pallet', 0), errors='coerce') or 0)
        m_pecahan = float(pd.to_numeric(row.get('Mutasi Pecahan (Dus/Sak)', 0), errors='coerce') or 0)
        m_eceran_lama = float(pd.to_numeric(row.get('Mutasi Eceran (Pcs)', 0), errors='coerce') or 0)

        isi_pallet, pcs_per_satuan = get_master_konversi(df_master, pn)

        if isi_pallet > 0 and (m_pallet != 0 or m_pecahan != 0):
            pcs_baru = int(round((m_pallet * isi_pallet) + (m_pecahan * pcs_per_satuan)))
            pcs_recalc_list.append(pcs_baru)

            if m_eceran_lama > 0 and int(round(m_eceran_lama)) != pcs_baru:
                selisih_info_list.append(f"Master Baru (Lama: {int(round(m_eceran_lama)):,})")
            else:
                selisih_info_list.append("Sesuai Master")
        else:
            pcs_recalc_list.append(int(round(m_eceran_lama)))
            selisih_info_list.append("Eceran / Non-Master")

    df_log['Total Pcs (Master Terbaru)'] = pcs_recalc_list
    df_log['Status Konversi'] = selisih_info_list
    return df_log

def tentukan_arus_log(jenis_transaksi):
    """Menentukan kategori arus stok standar (MASUK / KELUAR / PINDAH / KOREKSI)."""
    jt = clean_str(jenis_transaksi).upper()
    if "REVERSAL OUTBOUND" in jt or "INBOUND" in jt or "RESTORE" in jt or "MASUK (KOREKSI)" in jt:
        return "MASUK"
    if "REVERSAL INBOUND" in jt or "OUTBOUND" in jt or "WIP" in jt or "STAGING" in jt or "KELUAR (KOREKSI)" in jt:
        return "KELUAR"
    if "RELOKASI" in jt:
        return "PINDAH"
    return "KOREKSI"

def scan_stok_bebas_untuk_plotting(semua_data, target_part_num, strict_fifo_shift=True):
    """Memindai seluruh rak (A-G) untuk Part Number tertentu yang BELUM memiliki Jadwal Kirim."""
    if not semua_data:
        return pd.DataFrame()

    df_all = pd.DataFrame(semua_data)
    df_all = df_all[df_all['Kolom'].apply(clean_str).str.upper().isin(KOLOM_RACKING_AG)].copy()
    if df_all.empty:
        return pd.DataFrame()

    df_all['Baris_Int'] = pd.to_numeric(df_all['Baris'], errors='coerce').fillna(0).astype(int)
    df_all['Pallet_Ke_Num'] = pd.to_numeric(df_all['Pallet Ke'], errors='coerce').fillna(0)
    for col_opt in ['Catatan', 'Approval QC', 'Jadwal Kirim', 'Target Penerima', 'Keterangan']:
        df_all[col_opt] = df_all[col_opt].apply(clean_str) if col_opt in df_all.columns else ""

    pn_target = clean_str(target_part_num).upper()
    df_free = df_all[
        (df_all['Part Number'].apply(clean_str).str.upper() == pn_target) &
        (df_all['Jadwal Kirim'] == "")
    ].copy()

    if df_free.empty:
        return pd.DataFrame()

    df_free['Loc_Lot_Key'] = (
        df_free['Kolom'].apply(clean_str) + "_" +
        df_free['Baris_Int'].astype(str) + "_" +
        df_free['Lot Number'].apply(clean_str)
    )

    rekap = []
    for _, grp in df_free.groupby('Loc_Lot_Key'):
        kol = clean_str(grp.iloc[0]['Kolom'])
        bar = int(grp.iloc[0]['Baris_Int'])
        lot = clean_str(grp.iloc[0]['Lot Number'])
        nama = clean_str(grp.iloc[0]['Nama Produk'])
        cat_rak = clean_str(grp.iloc[0]['Catatan'])
        qc_st = clean_str(grp.iloc[0]['Approval QC'])

        is_pec_mask = grp['Keterangan'].str.contains('Pecahan|Eceran', case=False, na=False)
        jml_utuh = int((~is_pec_mask).sum())
        jml_dus = sum(parse_keterangan(k)[0] for k in grp.loc[is_pec_mask, 'Keterangan'])

        min_pos = grp['Pallet_Ke_Num'].min()
        max_pos = grp['Pallet_Ke_Num'].max()
        df_aisle = df_all[(df_all['Kolom'].apply(clean_str) == kol) & (df_all['Baris_Int'] == bar)]
        is_other = ~((df_aisle['Part Number'].apply(clean_str).str.upper() == pn_target) &
                     (df_aisle['Lot Number'].apply(clean_str) == lot) &
                     (df_aisle['Jadwal Kirim'] == ""))

        depan_blk = int((is_other & (df_aisle['Pallet_Ke_Num'] < min_pos)).sum())
        belakang_blk = int((is_other & (df_aisle['Pallet_Ke_Num'] > max_pos)).sum())

        if depan_blk == 0:
            akses_rank, min_blk, sisi_str = 0, 0, "DEPAN (FIFO)"
        elif belakang_blk == 0:
            akses_rank, min_blk, sisi_str = 1, 0, "BELAKANG (LIFO)"
        else:
            akses_rank = 2
            if depan_blk <= belakang_blk:
                min_blk, sisi_str = depan_blk, f"TERHALANG DEPAN ({depan_blk}P)"
            else:
                min_blk, sisi_str = belakang_blk, f"TERHALANG BELAKANG ({belakang_blk}P)"

        rekap.append({
            'Kolom': kol,
            'Baris': bar,
            'Lokasi': f"{kol}-{bar}",
            'Lot Number': lot,
            'FIFO_Key': parse_fifo_lot_key(lot),
            'Nama Produk': nama,
            'Part Number': pn_target,
            'Stok Utuh': jml_utuh,
            'Stok Dus/Sak': jml_dus,
            'Akses Pintu': sisi_str,
            'AksesRank': akses_rank,
            'MinPenghalang': min_blk,
            'Status QC': qc_st or "Pending",
            'Catatan Rak': cat_rak
        })

    if strict_fifo_shift:
        sort_cols = ['FIFO_Key', 'Stok Utuh', 'MinPenghalang', 'AksesRank', 'Lot Number', 'Kolom', 'Baris']
    else:
        sort_cols = ['MinPenghalang', 'FIFO_Key', 'Stok Utuh', 'AksesRank', 'Lot Number', 'Kolom', 'Baris']

    return pd.DataFrame(rekap).sort_values(
        by=sort_cols,
        ascending=[True] * len(sort_cols)
    ).reset_index(drop=True)

def evaluasi_akses_dinamis_plot(df_kandidat, all_rak_records, target_part_num):
    """Menghitung ulang status 'Akses Pintu' secara dinamis dengan memperhitungkan rombongan yang ter-plot."""
    if df_kandidat.empty:
        return df_kandidat

    pn_target = clean_str(target_part_num).upper()
    df_all = pd.DataFrame(all_rak_records)
    df_all['Baris_Int'] = pd.to_numeric(df_all['Baris'], errors='coerce').fillna(0).astype(int)
    df_all['Pallet_Ke_Num'] = pd.to_numeric(df_all['Pallet Ke'], errors='coerce').fillna(0)
    for col_opt in ['Jadwal Kirim', 'Target Penerima', 'Keterangan']:
        df_all[col_opt] = df_all[col_opt].apply(clean_str) if col_opt in df_all.columns else ""

    plot_map = {}
    for _, r in df_kandidat.iterrows():
        plot_map[(r['Kolom'], int(r['Baris']), clean_str(r['Lot Number']))] = int(r['Plot Utuh'])

    akses_dinamis_list = []
    for _, r in df_kandidat.iterrows():
        kol = r['Kolom']
        bar = int(r['Baris'])
        lot = clean_str(r['Lot Number'])

        df_aisle = df_all[
            (df_all['Kolom'].apply(clean_str) == kol) &
            (df_all['Baris_Int'] == bar)
        ].sort_values('Pallet_Ke_Num')

        target_slots = df_aisle[
            (df_aisle['Part Number'].apply(clean_str).str.upper() == pn_target) &
            (df_aisle['Lot Number'].apply(clean_str) == lot) &
            (df_aisle['Jadwal Kirim'] == "") &
            (df_aisle['Target Penerima'] == "")
        ]
        if target_slots.empty:
            akses_dinamis_list.append(r['Akses Pintu'])
            continue

        min_pos = target_slots['Pallet_Ke_Num'].min()
        max_pos = target_slots['Pallet_Ke_Num'].max()

        sisa_depan = 0
        sisa_belakang = 0
        terbuka_oleh_rombongan = False

        for pos_side, df_side in [
            ("DEPAN", df_aisle[df_aisle['Pallet_Ke_Num'] < min_pos]),
            ("BELAKANG", df_aisle[df_aisle['Pallet_Ke_Num'] > max_pos])
        ]:
            sisa_sisi = 0
            for (p_s, l_s, jk_s), grp_s in df_side.groupby([
                df_side['Part Number'].apply(clean_str).str.upper(),
                df_side['Lot Number'].apply(clean_str),
                df_side['Jadwal Kirim']
            ]):
                total_slot_grup = len(grp_s)
                if p_s == pn_target and jk_s == "":
                    diambil_plot = plot_map.get((kol, bar, l_s), 0)
                    sisa_grup = max(0, total_slot_grup - diambil_plot)
                    if diambil_plot > 0 and sisa_grup == 0 and pos_side == "DEPAN":
                        terbuka_oleh_rombongan = True
                else:
                    sisa_grup = total_slot_grup
                sisa_sisi += sisa_grup

            if pos_side == "DEPAN":
                sisa_depan = sisa_sisi
            else:
                sisa_belakang = sisa_sisi

        if sisa_depan == 0:
            if terbuka_oleh_rombongan:
                akses_dinamis_list.append("DEPAN (FIFO - Lanjut Antrean)")
            else:
                akses_dinamis_list.append("DEPAN (FIFO)")
        elif sisa_belakang == 0:
            akses_dinamis_list.append("BELAKANG (LIFO)")
        else:
            if sisa_depan <= sisa_belakang:
                akses_dinamis_list.append(f"TERHALANG DEPAN ({sisa_depan}P)")
            else:
                akses_dinamis_list.append(f"TERHALANG BELAKANG ({sisa_belakang}P)")

    df_out = df_kandidat.copy()
    df_out['Akses Pintu'] = akses_dinamis_list
    return df_out

# ==========================================================
# 4. CETAK KARTU RACKING / PICKING LIST
# ==========================================================
def build_picking_cards(baris_num, kolom_id, baris_records):
    """Membentuk daftar blok kartu picking list (run-length grouping) untuk 1 lokasi rak."""
    lokasi_label = f"Baris {int(baris_num)}{kolom_id}"
    valid_items = sorted(
        [d for d in baris_records if is_valid_item(d)],
        key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))
    )

    if not valid_items:
        return [{
            'Lokasi': lokasi_label,
            'Nama Item': 'RAK KOSONG',
            'Part Number': '-',
            'Lot Produksi': '-',
            'Palet ke': '-',
            'Jumlah': '-',
            'Status QC': '-',
            'Is_Empty': True
        }]

    runs = []
    for item in valid_items:
        pn = clean_str(item.get('Part Number')).upper()
        lot = clean_str(item.get('Lot Number'))
        nama = clean_str(item.get('Nama Produk'))
        p_idx = int(pd.to_numeric(item.get('Pallet Ke', 0)) or 0)
        ket = clean_str(item.get('Keterangan'))
        qc_val = clean_str(item.get('Approval QC'))

        if runs and runs[-1]['pn'] == pn and runs[-1]['lot'] == lot:
            runs[-1]['items'].append((p_idx, ket))
            if qc_val and not runs[-1]['qc']:
                runs[-1]['qc'] = qc_val
        else:
            runs.append({'pn': pn, 'lot': lot, 'nama': nama, 'qc': qc_val, 'items': [(p_idx, ket)]})

    cards = []
    for r in runs:
        start_p = r['items'][0][0]
        end_p = r['items'][-1][0]
        palet_range = f"{start_p}" if start_p == end_p else f"{start_p}-{end_p}"

        utuh_cnt, dus_sak_cnt, pcs_cnt = 0, 0, 0
        for _, ket in r['items']:
            if 'pecahan' in ket.lower() or 'eceran' in ket.lower():
                p_val, e_val = parse_keterangan(ket)
                dus_sak_cnt += p_val
                pcs_cnt += e_val
            else:
                utuh_cnt += 1

        satuan = get_satuan_kemasan(r['pn'])
        qty_parts = []
        if utuh_cnt > 0:
            qty_parts.append(f"{utuh_cnt}P")
        if dus_sak_cnt > 0:
            qty_parts.append(f"{dus_sak_cnt} {satuan}")
        if pcs_cnt > 0:
            qty_parts.append(f"{pcs_cnt} pcs")
        jumlah_str = " + ".join(qty_parts) if qty_parts else "0P"

        cards.append({
            'Lokasi': lokasi_label,
            'Nama Item': r['nama'],
            'Part Number': r['pn'],
            'Lot Produksi': r['lot'] or '-',
            'Palet ke': palet_range,
            'Jumlah': jumlah_str,
            'Status QC': r['qc'] or 'Pending QC',
            'Is_Empty': False
        })
    return cards

def render_print_html(cards_list, judul_cetak, waktu_cetak):
    """Merender HTML siap cetak A4 dengan tombol Print langsung di browser."""
    cards_html = []
    for c in cards_list:
        lok = html.escape(str(c['Lokasi']))
        nama = html.escape(str(c['Nama Item']))
        pn = html.escape(str(c['Part Number']))
        lot = html.escape(str(c['Lot Produksi']))
        palet = html.escape(str(c['Palet ke']))
        jml = html.escape(str(c['Jumlah']))
        qc_st = html.escape(str(c.get('Status QC', '-')))

        row_class = "empty-row" if c['Is_Empty'] else "data-row"
        cards_html.append(f"""
        <div class="card-block">
            <table>
                <thead>
                    <tr class="loc-header">
                        <th colspan="6">{lok}</th>
                    </tr>
                    <tr class="col-header">
                        <th style="width: 34%;">Nama Item</th>
                        <th style="width: 15%;">Part Number</th>
                        <th style="width: 13%;">Lot Produksi</th>
                        <th style="width: 10%;">Palet ke</th>
                        <th style="width: 14%;">Jumlah</th>
                        <th style="width: 14%;">Status QC</th>
                    </tr>
                </thead>
                <tbody>
                    <tr class="{row_class}">
                        <td>{nama}</td>
                        <td>{pn}</td>
                        <td>{lot}</td>
                        <td class="center">{palet}</td>
                        <td class="bold">{jml}</td>
                        <td>{qc_st}</td>
                    </tr>
                </tbody>
            </table>
        </div>
        """)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, Helvetica, sans-serif; margin: 12px; color: #000; background: #fff; }}
        .toolbar {{
            display: flex; justify-content: space-between; align-items: center;
            padding: 10px 14px; margin-bottom: 14px;
            background: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 6px;
        }}
        .btn-print {{
            background: #0f172a; color: #fff; border: none; padding: 8px 16px;
            font-size: 13px; font-weight: bold; border-radius: 4px; cursor: pointer;
        }}
        .btn-print:hover {{ background: #334155; }}
        .meta {{ font-size: 12px; color: #334155; }}
        .card-block {{ margin-bottom: 14px; page-break-inside: avoid; break-inside: avoid; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
        th, td {{ border: 1px solid #000; padding: 5px 8px; text-align: left; }}
        tr.loc-header th {{ background-color: #e2e8f0; font-size: 13px; font-weight: bold; }}
        tr.col-header th {{ background-color: #f8fafc; font-weight: bold; }}
        tr.empty-row td {{ color: #64748b; font-style: italic; }}
        td.center {{ text-align: center; }}
        td.bold {{ font-weight: bold; }}
        @media print {{
            .toolbar {{ display: none !important; }}
            body {{ margin: 0; }}
            .card-block {{ margin-bottom: 12px; }}
        }}
    </style>
    </head>
    <body>
        <div class="toolbar">
            <div class="meta">
                <strong>{html.escape(judul_cetak)}</strong> • Dicetak: {html.escape(waktu_cetak)} • Total Kartu: {len(cards_list)}
            </div>
            <button class="btn-print" onclick="window.print()">🖨️ Cetak / Simpan PDF</button>
        </div>
        {"".join(cards_html)}
    </body>
    </html>
    """

# ==========================================================
# 5. OPERASI SLOT RAK, CACHE & MONITOR STORAGE
# ==========================================================
def summarize_lot_groups(baris_records):
    """Meringkas list record satu baris menjadi grup per (Lot, Produk, Catatan, Jadwal Kirim, Target Penerima)."""
    df = pd.DataFrame(baris_records)
    df['Pallet Ke'] = pd.to_numeric(df['Pallet Ke'], errors='coerce')
    df['Catatan_Clean'] = df['Catatan'].apply(clean_str) if 'Catatan' in df.columns else ""
    df['Keterangan_Clean'] = df['Keterangan'].apply(clean_str) if 'Keterangan' in df.columns else ""
    df['Approval_QC_Clean'] = df['Approval QC'].apply(clean_str) if 'Approval QC' in df.columns else ""
    df['Jadwal_Kirim_Clean'] = df['Jadwal Kirim'].apply(clean_str) if 'Jadwal Kirim' in df.columns else ""
    df['Target_Penerima_Clean'] = df['Target Penerima'].apply(clean_str) if 'Target Penerima' in df.columns else ""
    df['Group_Key'] = df.apply(
        lambda r: make_g_key(
            r.get('Lot Number'), r.get('Nama Produk'), r.get('Catatan'),
            r.get('Jadwal Kirim'), r.get('Target Penerima')
        ),
        axis=1
    )

    groups = []
    for _, row in df.drop_duplicates(subset=['Group_Key']).iterrows():
        g_key = str(row['Group_Key'])
        df_lot = df[df['Group_Key'] == g_key]
        is_frac_mask = df_lot['Keterangan_Clean'].str.contains('Pecahan|Eceran', case=False, na=False)
        jml_utuh = int((~is_frac_mask).sum())
        pecahan, eceran = 0, 0
        for ket_val in df_lot.loc[is_frac_mask, 'Keterangan_Clean']:
            p, e = parse_keterangan(ket_val)
            pecahan += p
            eceran += e

        qc_vals = [q for q in df_lot['Approval_QC_Clean'].unique() if q]
        groups.append({
            'Group_Key': g_key,
            'Produk': clean_str(row.get('Nama Produk')),
            'Part Number': clean_str(row.get('Part Number')).upper(),
            'Lot Number': clean_str(row.get('Lot Number')),
            'Catatan': str(row['Catatan_Clean']),
            'Approval QC': qc_vals[0] if qc_vals else "",
            'Jadwal Kirim': str(row['Jadwal_Kirim_Clean']),
            'Target Penerima': str(row['Target_Penerima_Clean']),
            'Utuh': jml_utuh,
            'Dus': int(pecahan),
            'Pcs': int(eceran),
        })
    return groups

def apply_slot_reduction(records_list, baris_num, g_key, sisa_utuh, sisa_dus, sisa_pcs, catatan_tambahan=""):
    """Mengurangi slot dari baris rak secara immutable."""
    df_lot = pd.DataFrame([
        d for d in records_list
        if match_g_key(d, g_key) and is_same_baris(d, baris_num) and is_valid_item(d)
    ])
    if df_lot.empty:
        return None, "empty"

    df_lot['Pallet Ke'] = pd.to_numeric(df_lot['Pallet Ke'])
    df_lot = df_lot.sort_values('Pallet Ke', ascending=False)

    slot_tambahan = 1 if (sisa_dus > 0 or sisa_pcs > 0) else 0
    total_slot_sisa = sisa_utuh + slot_tambahan
    if total_slot_sisa > len(df_lot):
        return None, f"over:{len(df_lot)}"

    out_qty = len(df_lot) - total_slot_sisa
    pallets_to_remove = df_lot['Pallet Ke'].head(out_qty).tolist() if out_qty > 0 else []
    sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
    max_sisa_pallet = sisa_pallets['Pallet Ke'].max() if not sisa_pallets.empty else None

    updated_records = []
    for orig_d in records_list:
        d = copy.deepcopy(orig_d)
        if match_g_key(d, g_key) and is_same_baris(d, baris_num):
            p_val = pd.to_numeric(d.get('Pallet Ke', 0))
            if p_val in pallets_to_remove:
                continue

            c_lama = clean_str(d.get('Catatan'))
            if catatan_tambahan and catatan_tambahan not in c_lama:
                d['Catatan'] = f"{c_lama} | {catatan_tambahan}".strip(' |')
            else:
                d['Catatan'] = c_lama

            if max_sisa_pallet is not None and p_val == max_sisa_pallet:
                d['Keterangan'] = format_keterangan(sisa_dus, sisa_pcs)
            else:
                ket_lama = clean_str(d.get('Keterangan'))
                if 'Pecahan' in ket_lama or 'Eceran' in ket_lama:
                    d['Keterangan'] = ""
        updated_records.append(d)

    return updated_records, ""

def get_max_slot(kolom_id, baris_num, records_baris, extra_part=""):
    """Menghitung kapasitas maksimal slot pada satu baris rak."""
    if kolom_id == 'S':
        return 999
    valid_parts = [clean_str(d.get('Part Number')).upper() for d in records_baris if is_valid_item(d)]
    if clean_str(extra_part):
        valid_parts.append(clean_str(extra_part).upper())
    is_all_box = bool(valid_parts) and all(pn.endswith(('D', 'G')) for pn in valid_parts)
    if is_all_box:
        return 32 if baris_num <= 16 else 46
    return 31 if baris_num <= 16 else 44

def update_bayangan_baris(records_baris, kolom_id, baris_num):
    """Mengevaluasi ulang indeks Pallet Ke (1..N), sanitasi string, dan tag [BAYANGAN - OVERCAPACITY]."""
    max_slot = get_max_slot(kolom_id, baris_num, records_baris)
    result = []
    for idx, orig_item in enumerate(records_baris, start=1):
        item = copy.deepcopy(orig_item)
        item['Pallet Ke'] = float(idx)
        item['Keterangan'] = clean_str(item.get('Keterangan'))
        item['Approval QC'] = clean_str(item.get('Approval QC'))
        item['Jadwal Kirim'] = clean_str(item.get('Jadwal Kirim'))
        item['Target Penerima'] = clean_str(item.get('Target Penerima'))
        cat_lama = clean_str(item.get('Catatan'))
        if idx > max_slot and 'BAYANGAN' not in cat_lama.upper():
            item['Catatan'] = f"[BAYANGAN - OVERCAPACITY] {cat_lama}".strip()
        elif idx <= max_slot and 'BAYANGAN' in cat_lama.upper():
            item['Catatan'] = cat_lama.replace("[BAYANGAN - OVERCAPACITY]", "").strip()
        else:
            item['Catatan'] = cat_lama
        result.append(item)
    return result

def hitung_estimasi_bytes(val):
    """Menghitung estimasi ukuran byte data tabel PostgreSQL."""
    if val is None or isinstance(val, bool):
        return 1
    if isinstance(val, (int, float, datetime)):
        return 8
    if isinstance(val, str):
        return len(val.encode('utf-8')) + 1
    if isinstance(val, list):
        return sum(hitung_estimasi_bytes(item) for item in val)
    if isinstance(val, dict):
        return sum((len(str(k).encode('utf-8')) + 1) + hitung_estimasi_bytes(v) for k, v in val.items())
    return 0

def record_doc_size(doc_name, data_list):
    """Memperbarui catatan estimasi ukuran byte per kolom di session_state."""
    st.session_state['doc_sizes'][doc_name] = hitung_estimasi_bytes(data_list)

def fetch_kolom_cached(kolom_id, state_key, force_refresh=False):
    """Membaca data Kolom sekali saja saat diminta dari Supabase dan menyimpannya di cache sesi."""
    if force_refresh or state_key not in st.session_state or st.session_state[state_key] is None:
        data_list = load_kolom_from_supabase(kolom_id)
        st.session_state[state_key] = copy.deepcopy(data_list)
    return st.session_state[state_key]

def clear_all_rak_caches():
    """Membersihkan seluruh cache lokal rak setiap kali ada mutasi tersimpan."""
    for k in ['cache_in_data', 'cache_out_data', 'cache_relok_asal', 'cache_kor_data']:
        st.session_state[k] = None
    st.session_state['print_rak_cache'] = {}
    st.session_state['print_ready'] = False
    st.session_state['plot_all_records_cache'] = []
    st.session_state['active_plots_list'] = None

@st.cache_data(ttl=300)
def load_master_produk():
    """Memuat Master Data Produk untuk Auto-fill Part Number & Konversi Satuan."""
    try:
        try:
            df = pd.read_csv("Master_Produk.csv", sep=";", encoding='utf-8-sig')
        except Exception:
            df = pd.read_csv("Master_Produk.csv", sep=",", encoding='utf-8-sig')
        df.columns = df.columns.str.strip()
        if 'Part Number' in df.columns:
            df['Part Number Clean'] = df['Part Number'].apply(clean_str).str.upper()
        return df
    except Exception as e:
        st.toast(f"Gagal memuat master produk: {e}", icon="🚨")
        return pd.DataFrame()

def catat_log_mutasi(
    jenis, kolom, baris, produk, part_num, no_lot,
    pallet_utuh, pecahan, eceran, keterangan="",
    tujuan_kategori="", detail_sistem=""
):
    """Mencatat setiap pergerakan stok ke tabel log_mutasi_rak Supabase."""
    b_str = str(baris).strip()
    lok_str = f"{kolom}-{b_str}" if b_str and b_str.upper() != "NONE" else str(kolom)
    bar_int = int(float(b_str)) if b_str.isdigit() else 0
    tujuan_clean = clean_str(tujuan_kategori) or clean_str(keterangan)
    detail_clean = clean_str(detail_sistem)
    ket_gabungan = f"{tujuan_clean} | {detail_clean}".strip(" |") if detail_clean else tujuan_clean

    row_insert = {
        "waktu_transaksi": datetime.now(timezone.utc).isoformat(),
        "arus": tentukan_arus_log(jenis),
        "jenis_transaksi": clean_str(jenis),
        "lokasi_rak": lok_str,
        "kolom": clean_str(kolom),
        "baris": bar_int,
        "part_number": clean_str(part_num),
        "nama_produk": clean_str(produk),
        "lot_number": clean_str(no_lot) or "-",
        "mutasi_pallet": int(round(float(pallet_utuh))),
        "mutasi_pecahan": int(round(float(pecahan))),
        "mutasi_eceran": int(round(float(eceran))),
        "total_pcs": int(round(float(eceran))),
        "tujuan_kategori": tujuan_clean,
        "detail_sistem": detail_clean,
        "operator": "Admin PPIC",
        "keterangan": ket_gabungan,
    }
    try:
        supabase.table(TBL_LOG_MUTASI).insert(row_insert).execute()
        log_sb_usage('write', 1)
        if 'total_log_bytes' in st.session_state:
            st.session_state['total_log_bytes'] += hitung_estimasi_bytes(row_insert)
            st.session_state['total_log_docs'] += 1
    except Exception as e:
        st.toast(f"⚠️ Peringatan: Gagal mencatat log mutasi ({e})", icon="⚠️")

# ==========================================================
# 6. SIDEBAR NAVIGASI, KUOTA & MONITOR STORAGE SUPABASE
# ==========================================================
st.sidebar.markdown("### ⚙️ Kontrol Sistem")
if st.sidebar.button("🔄 Reload Master Data CSV", use_container_width=True):
    st.cache_data.clear()
    st.toast("Master Data berhasil diperbarui dari GitHub!", icon="✅")
    st.rerun()

st.sidebar.divider()
st.sidebar.markdown("### 📊 Monitor Query Sesi Ini")
u_col1, u_col2, u_col3 = st.sidebar.columns(3)
u_col1.metric("Read", st.session_state['sb_usage']['read'])
u_col2.metric("Write", st.session_state['sb_usage']['write'])
u_col3.metric("Delete", st.session_state['sb_usage']['delete'])
st.sidebar.caption("Supabase Free Plan: **Unlimited API Requests**.")

if st.sidebar.button("Reset Counter Sesi", use_container_width=True):
    st.session_state['sb_usage'] = {'read': 0, 'write': 0, 'delete': 0}
    st.rerun()

st.sidebar.divider()
st.sidebar.markdown("### 💾 Monitor Storage (Supabase 500 MB)")

if st.session_state['doc_sizes']:
    for d_name, b_size in sorted(st.session_state['doc_sizes'].items()):
        kb_size = b_size / 1024
        st.sidebar.caption(f"🟢 **{d_name}**: `{b_size:,}` B ({kb_size:.1f} KB)")
else:
    st.sidebar.caption("Belum ada kolom rak yang diakses pada sesi ini.")

if st.sidebar.button("📏 Cek Total Storage (Rak + Log)", use_container_width=True):
    all_rows = load_all_rak_from_supabase()
    total_rak_b = sum(st.session_state['doc_sizes'].values())
    st.session_state['total_rak_bytes'] = total_rak_b

    try:
        cnt_res = supabase.table(TBL_LOG_MUTASI).select("id", count="exact").limit(1).execute()
        log_sb_usage('read', 1)
        jml_log = int(cnt_res.count or 0)
        st.session_state['total_log_docs'] = jml_log
        st.session_state['total_log_bytes'] = jml_log * 450
    except Exception:
        st.session_state['total_log_docs'] = 0
        st.session_state['total_log_bytes'] = 0

    st.rerun()

if 'total_rak_bytes' in st.session_state:
    tot_rak = st.session_state['total_rak_bytes']
    tot_log = st.session_state.get('total_log_bytes', 0)
    jml_log_docs = st.session_state.get('total_log_docs', 0)
    grand_tot_b = tot_rak + tot_log
    persen_sb = (grand_tot_b / LIMIT_SUPABASE_BYTES) * 100

    st.sidebar.info(
        f"**1. Rak Utama (`stok_rak`):** `{tot_rak:,}` B ({tot_rak / (1024*1024):.2f} MB)\n\n"
        f"**2. Log Mutasi ({jml_log_docs:,} Baris):** ~`{tot_log:,}` B ({tot_log / (1024*1024):.2f} MB)\n\n"
        f"**Total Gabungan:** `{grand_tot_b / (1024*1024):.2f} MB` (**{persen_sb:.3f}%** dari 500 MB Supabase)"
    )

st.sidebar.divider()
st.sidebar.button("🚪 Logout", on_click=lambda: st.session_state.update({'logged_in': False}), use_container_width=True)

# ==========================================================
# 7. HEADER & NOTIFIKASI UNDO
# ==========================================================
st.title("📝 WMS Entry Form (Inbound, Outbound, Plotting & Relokasi)")
st.caption("Alat khusus PPIC. Terhubung ke database: **Supabase PostgreSQL (`stok_rak`)**")

if st.session_state['action_status'] in ['DONE', 'DONE_NO_UNDO']:
    st.toast(f"Berhasil: {st.session_state['action_msg']}", icon="✅")

if st.session_state['action_status'] == 'DONE' and st.session_state.get('undo_dict'):
    with st.container(border=True):
        st.success(f"✅ **Sukses:** {st.session_state['action_msg']}")
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            st.caption("Jika terjadi kesalahan input, Anda bisa mengembalikan stok dan atribut rak ke kondisi persis sebelum aksi ini.")
        with c2:
            if st.button("🔙 UNDO AKSI INI", type="primary", use_container_width=True):
                with st.spinner("Memulihkan data ke kondisi semula..."):
                    snapshot_restore = copy.deepcopy(st.session_state['undo_dict'])
                    kolom_terdampak = []
                    for kol, dat in snapshot_restore.items():
                        save_kolom_to_supabase(kol, dat)
                        kolom_terdampak.append(kol)
                    clear_all_rak_caches()
                    st.session_state['undo_dict'] = {}
                    st.session_state['action_status'] = 'UNDONE'
                    catat_log_mutasi(
                        "PEMBATALAN (UNDO)", ",".join(kolom_terdampak), "ALL",
                        "Koreksi Sistem", "-", "-", 0, 0, 0,
                        keterangan=f"Membatalkan: {st.session_state['action_msg']}",
                        tujuan_kategori="UNDO TRANSAKSI",
                        detail_sistem=f"Membatalkan: {st.session_state['action_msg']}"
                    )
                    st.rerun()
        with c3:
            if st.button("Tutup & Lanjut Input", use_container_width=True):
                st.session_state['undo_dict'] = {}
                st.session_state['action_status'] = None
                st.rerun()

elif st.session_state['action_status'] == 'DONE_NO_UNDO':
    with st.container(border=True):
        st.success(f"✅ **Sukses:** {st.session_state['action_msg']}")
        if st.button("Tutup Pesan Ini"):
            st.session_state['action_status'] = None
            st.rerun()

elif st.session_state['action_status'] == 'UNDONE':
    with st.container(border=True):
        st.warning("⏪ **Berhasil dibatalkan.** Stok, pecahan dus/sak, dan catatan telah kembali persis seperti semula.")
        if st.button("Tutup Pesan", use_container_width=True):
            st.session_state['action_status'] = None
            st.rerun()

# ==========================================================
# 8. TABS MENU UTAMA (9 TAB OPERASIONAL)
# ==========================================================
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "📥 1. Inbound", "📤 2. Outbound", "⚙️ 3. Audit & Backup",
    "📊 4. Log Mutasi", "🚀 5. Sync CSV", "🔄 6. Relokasi",
    "✏️ 7. Koreksi", "🖨️ 8. Cetak Racking", "📅 9. Plotting Outbound"
])

# ----------------------------------------------------------
# TAB 1: INBOUND
# ----------------------------------------------------------
with tab1:
    st.header("Input Barang Masuk")
    reset_key = st.session_state['form_reset_counter']

    col_in1, col_in2 = st.columns([1, 2])
    with col_in1:
        st.markdown("**1. Pilih Lokasi Rak Tujuan**")
        in_kolom = st.selectbox("Lokasi Kolom", KOLOM_OPTIONS, key="in_kolom").split(" ")[0]
        in_baris = st.number_input("Nomor Baris (Inbound)", min_value=1, max_value=29, value=1)
        if st.button("🔍 Cek Status Rak", use_container_width=True):
            st.session_state.update({'in_lokasi_dicari': True, 'in_k_pilih': in_kolom, 'in_b_pilih': in_baris})
            fetch_kolom_cached(in_kolom, 'cache_in_data', force_refresh=True)

    with col_in2:
        if st.session_state['in_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['in_k_pilih'], st.session_state['in_b_pilih']
            all_data_raw = fetch_kolom_cached(k_pilih, 'cache_in_data', force_refresh=False)
            baris_data_preview = get_baris_records(all_data_raw, b_pilih, only_valid=True)

            max_slot_display = get_max_slot(k_pilih, b_pilih, baris_data_preview)
            current_qty = len(baris_data_preview)
            sisa_ruang = max_slot_display - current_qty

            st.markdown(f"**📦 Status Rak {k_pilih}-{b_pilih} | Terisi: `{current_qty}` / `{max_slot_display}` (Sisa Ruang: `{sisa_ruang}` Slot)**")
            if baris_data_preview:
                df_baris = pd.DataFrame(baris_data_preview)
                df_baris['Pallet Ke'] = pd.to_numeric(df_baris['Pallet Ke'], errors='coerce')
                df_baris = df_baris.sort_values('Pallet Ke', ascending=True)
                kolom_tersedia = [
                    c for c in ['Pallet Ke', 'Part Number', 'Nama Produk', 'Lot Number', 'Keterangan', 'Approval QC', 'Jadwal Kirim', 'Target Penerima']
                    if c in df_baris.columns
                ]
                st.dataframe(df_baris[kolom_tersedia], hide_index=True, height=150)
            else:
                st.info("Rak ini masih kosong. Siap diisi penuh!")

            st.divider()
            st.markdown(f"**2. Eksekusi Inbound (Tujuan: {k_pilih}-{b_pilih})**")

            df_master = load_master_produk()
            daftar_produk = df_master['Nama Barang'].dropna().astype(str).unique().tolist() if not df_master.empty else []
            pilihan_barang = st.selectbox("Pilih Produk", ["-- Ketik Manual --"] + daftar_produk)

            if pilihan_barang != "-- Ketik Manual --" and not df_master.empty:
                in_produk = pilihan_barang
                match_part = df_master[df_master['Nama Barang'] == pilihan_barang]['Part Number'].values
                in_part = st.text_input("Part Number (Auto-fill)", value=clean_str(match_part[0]) if len(match_part) > 0 else "", disabled=True)
            else:
                in_produk = st.text_input("Nama Produk (Ketik Manual)")
                in_part = st.text_input("Part Number (Ketik Manual)")

            in_lot = st.text_input("No. Lot Produksi / Mesin Shift", placeholder="WAJIB DIISI", key=f"in_lot_input_{reset_key}")

            col_qty1, col_qty2, col_qty3 = st.columns(3)
            with col_qty1:
                in_qty = st.number_input("Pallet Masuk UTUH", min_value=0, step=1, value=1, key=f"in_qty_input_{reset_key}")
            with col_qty2:
                in_pecahan = st.number_input("Sisa Pecahan (Dus/Sak)", min_value=0, step=1, value=0, key=f"in_pecahan_input_{reset_key}")
            with col_qty3:
                in_eceran = st.number_input("Eceran (Pcs)", min_value=0, step=1, value=0, key=f"in_eceran_input_{reset_key}")

            in_catatan_ops = st.text_input("Catatan Rak (Opsional)", placeholder="Cth: Titipan Produksi / Retur", key=f"in_cat_input_{reset_key}")
            in_dari_depan = st.checkbox("📥 Masukkan dari DEPAN Rak (Push-back)")

            if st.button("🚀 Simpan Inbound", type="primary", use_container_width=True):
                in_part_clean = clean_str(in_part).upper()
                in_produk_clean = clean_str(in_produk)
                in_lot_final = clean_str(in_lot)
                in_cat_final = clean_str(in_catatan_ops)

                if not in_lot_final or in_lot_final == "-":
                    st.error("⚠️ No. Lot bersifat WAJIB DIISI!")
                elif not in_produk_clean or not in_part_clean:
                    st.error("⚠️ Nama Produk dan Part Number wajib diisi!")
                elif in_qty == 0 and in_pecahan == 0 and in_eceran == 0:
                    st.error("⚠️ Masukkan minimal 1 Qty!")
                else:
                    with st.spinner("Menyusun data ke dalam rak (Supabase)..."):
                        fresh_raw = load_kolom_from_supabase(k_pilih)
                        working_raw = copy.deepcopy(fresh_raw)

                        all_data = [
                            d for d in working_raw
                            if not (is_same_baris(d, b_pilih) and not is_valid_item(d))
                        ]
                        baris_data_actual = get_baris_records(all_data, b_pilih, only_valid=False)
                        lain_data_actual = [d for d in all_data if not is_same_baris(d, b_pilih)]

                        existing_fraction_row = None
                        if not in_dari_depan:
                            for d in baris_data_actual:
                                if (clean_str(d.get('Part Number')).upper() == in_part_clean
                                        and clean_str(d.get('Lot Number')) == in_lot_final
                                        and clean_str(d.get('Jadwal Kirim')) == ""):
                                    ket = clean_str(d.get('Keterangan'))
                                    if 'Pecahan' in ket or 'Eceran' in ket:
                                        existing_fraction_row = d

                        slot_tambahan_in = 1 if ((in_pecahan > 0 or in_eceran > 0) and not existing_fraction_row) else 0
                        total_inbound_slots = int(in_qty) + slot_tambahan_in

                        new_records = [
                            {
                                'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': 0.0,
                                'Part Number': in_part_clean, 'Lot Number': in_lot_final, 'Nama Produk': in_produk_clean,
                                'Status': 'Terisi', 'Keterangan': '', 'Catatan': in_cat_final,
                                'Approval QC': '', 'Jadwal Kirim': '', 'Target Penerima': ''
                            }
                            for _ in range(int(in_qty))
                        ]

                        if in_pecahan > 0 or in_eceran > 0:
                            if existing_fraction_row and not in_dari_depan:
                                ex_pec, ex_ec = parse_keterangan(existing_fraction_row.get('Keterangan'))
                                existing_fraction_row['Keterangan'] = format_keterangan(ex_pec + in_pecahan, ex_ec + in_eceran)
                            else:
                                new_records.append({
                                    'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': 0.0,
                                    'Part Number': in_part_clean, 'Lot Number': in_lot_final, 'Nama Produk': in_produk_clean,
                                    'Status': 'Terisi', 'Keterangan': format_keterangan(in_pecahan, in_eceran),
                                    'Catatan': in_cat_final, 'Approval QC': '', 'Jadwal Kirim': '', 'Target Penerima': ''
                                })

                        baris_sorted = sorted(baris_data_actual, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                        combined_baris = (new_records + baris_sorted) if in_dari_depan else (baris_sorted + new_records)
                        combined_baris = update_bayangan_baris(combined_baris, k_pilih, b_pilih)

                        final_kolom_data = lain_data_actual + combined_baris
                        save_kolom_to_supabase(k_pilih, final_kolom_data)

                        isi_p, pcs_s = get_master_konversi(df_master, in_part_clean)
                        tot_in_pcs = ((in_qty * isi_p) + (in_pecahan * pcs_s) + in_eceran) if isi_p > 0 else in_eceran

                        sisi_lbl = "Push-Back (Depan)" if in_dari_depan else "Normal (Belakang)"
                        det_in = f"Masuk Rak {k_pilih}-{b_pilih} | Sisi: {sisi_lbl}" + (f" | Catatan: {in_cat_final}" if in_cat_final else "")
                        catat_log_mutasi(
                            "INBOUND (Admin)", k_pilih, b_pilih, in_produk_clean, in_part_clean, in_lot_final,
                            in_qty, in_pecahan, tot_in_pcs,
                            keterangan=det_in,
                            tujuan_kategori="INBOUND PRODUKSI",
                            detail_sistem=det_in
                        )
                        clear_all_rak_caches()

                        st.session_state.update({
                            'undo_dict': {k_pilih: copy.deepcopy(fresh_raw)},
                            'action_msg': f"Inbound {total_inbound_slots} Slot '{in_produk_clean}' ke Rak {k_pilih}-{b_pilih} {'(Push-back)' if in_dari_depan else ''}",
                            'action_status': 'DONE',
                            'kor_dicari': False,
                            'relok_dicari': False
                        })
                        st.session_state['form_reset_counter'] += 1
                        st.rerun()

# ----------------------------------------------------------
# TAB 2: OUTBOUND MULTI-LOT (ANTI-MINUS + UNDO SNAPSHOT AMAN)
# ----------------------------------------------------------
with tab2:
    st.header("Penyesuaian Sisa Stok (Outbound Massal)")
    st.caption("Ubah angka sisa fisik pada tabel, pilih Kategori & Tujuan keluar, lalu klik Simpan.")

    st.markdown("**1. Pilih Lokasi Rak**")
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        out_kolom = st.selectbox("Lokasi Kolom", KOLOM_OPTIONS, key="out_kolom_2").split(" ")[0]
    with c2:
        out_baris = st.number_input("Nomor Baris", min_value=1, max_value=29, value=1, key="out_baris_2")
    with c3:
        st.write("")
        st.write("")
        if st.button("🔍 Cari Data Rak", use_container_width=True):
            st.session_state.update({'out_lokasi_dicari': True, 'out_k_pilih': out_kolom, 'out_b_pilih': out_baris})
            fetch_kolom_cached(out_kolom, 'cache_out_data', force_refresh=True)

    st.divider()

    if st.session_state['out_lokasi_dicari']:
        k_pilih, b_pilih = st.session_state['out_k_pilih'], st.session_state['out_b_pilih']
        all_out_data = fetch_kolom_cached(k_pilih, 'cache_out_data', force_refresh=False)
        baris_out_data = get_baris_records(all_out_data, b_pilih, only_valid=True)

        if baris_out_data:
            df_master_out = load_master_produk()
            rekap_lot = []
            for g in summarize_lot_groups(baris_out_data):
                jk_g = g.get('Jadwal Kirim', '')
                tp_g = g.get('Target Penerima', '')
                plot_info = f"Plot: {tp_g} ({jk_g})" if (jk_g or tp_g) else g['Catatan']
                rekap_lot.append({
                    'Group_Key': g['Group_Key'],
                    'Produk': g['Produk'],
                    'Part Number': g['Part Number'],
                    'Lot Number': g['Lot Number'],
                    'Status / Plot': plot_info,
                    'Awal Utuh': g['Utuh'],
                    'Awal Dus': g['Dus'],
                    'Awal Pcs': g['Pcs'],
                    'Sisa Utuh Baru': g['Utuh'],
                    'Sisa Dus Baru': g['Dus'],
                    'Sisa Pcs Baru': g['Pcs'],
                    'Kategori': 'CUSTOMER',
                    'Tujuan / Alasan': tp_g
                })

            df_rekap = pd.DataFrame(rekap_lot)
            st.markdown(f"**2. Edit Sisa Fisik Rak {k_pilih}-{b_pilih} (Konversi Otomatis Anti-Minus)**")
            st.caption("💡 *Kategori: **CUSTOMER** (isi nama tujuan), **OUT** (tujuan menyusul), **WIP** (suplai produksi), atau **EC** (Error Correction).*")

            config_kolom = {
                'Group_Key': None,
                'Produk': st.column_config.TextColumn("Produk", disabled=True),
                'Part Number': st.column_config.TextColumn("Part", disabled=True),
                'Lot Number': st.column_config.TextColumn("Lot", disabled=True),
                'Status / Plot': st.column_config.TextColumn("Status / Plot", disabled=True),
                'Awal Utuh': st.column_config.NumberColumn("Sistem Utuh", disabled=True),
                'Awal Dus': st.column_config.NumberColumn("Sistem Dus/Sak", disabled=True),
                'Awal Pcs': st.column_config.NumberColumn("Sistem Pcs", disabled=True),
                'Sisa Utuh Baru': st.column_config.NumberColumn("SISA UTUH BARU", min_value=0, required=True),
                'Sisa Dus Baru': st.column_config.NumberColumn("SISA DUS/SAK BARU", min_value=0, required=True),
                'Sisa Pcs Baru': st.column_config.NumberColumn("SISA PCS BARU", min_value=0, required=True),
                'Kategori': st.column_config.SelectboxColumn(
                    "Kategori Keluar",
                    options=["CUSTOMER", "OUT", "WIP", "EC"],
                    required=True
                ),
                'Tujuan / Alasan': st.column_config.TextColumn("Nama Customer / Alasan EC", max_chars=80)
            }

            edited_df = st.data_editor(
                df_rekap, column_config=config_kolom, hide_index=True,
                use_container_width=True, num_rows="fixed", key=f"editor_out_{k_pilih}_{b_pilih}"
            )

            if st.button("🚜 Simpan Penyesuaian Massal (OUTBOUND)", type="primary", use_container_width=True):
                fresh_out_data = load_kolom_from_supabase(k_pilih)
                working_out_data = copy.deepcopy(fresh_out_data)

                has_changes = False
                error_msg = ""
                logs_to_write = []

                data_rak_ini = get_baris_records(working_out_data, b_pilih, only_valid=True)
                data_rak_lain = [d for d in working_out_data if not is_same_baris(d, b_pilih)]

                for _, row in edited_df.iterrows():
                    g_key = str(row['Group_Key'])
                    l = clean_str(row['Lot Number'])
                    pn = clean_str(row['Part Number']).upper()
                    sisa_utuh, sisa_dus, sisa_pcs = int(row['Sisa Utuh Baru']), int(row['Sisa Dus Baru']), int(row['Sisa Pcs Baru'])
                    awal_utuh, awal_dus, awal_pcs = int(row['Awal Utuh']), int(row['Awal Dus']), int(row['Awal Pcs'])
                    kat_keluar = clean_str(row['Kategori']) or "OUT"
                    teks_tujuan = clean_str(row['Tujuan / Alasan'])

                    angka_berubah = (sisa_utuh != awal_utuh or sisa_dus != awal_dus or sisa_pcs != awal_pcs)
                    if not angka_berubah and teks_tujuan != "" and f"Plot: {teks_tujuan}" not in clean_str(row['Status / Plot']):
                        error_msg = f"⚠️ Lot {l}: Anda mengisi tujuan '{teks_tujuan}' tetapi angka sisa stok belum diubah."
                        break

                    if angka_berubah:
                        kel_p, kel_d, kel_pcs, diff_pcs = hitung_konversi_mutasi_pcs(
                            df_master_out, pn, awal_utuh, awal_dus, awal_pcs, sisa_utuh, sisa_dus, sisa_pcs
                        )
                        if diff_pcs == -1:
                            error_msg = f"❌ Total stok sisa Lot {l} lebih besar dari stok awal! Gunakan Tab Inbound untuk menambah stok."
                            break
                        if kel_p == 0 and kel_d == 0 and kel_pcs == 0:
                            error_msg = f"⚠️ Lot {l}: Nilai akhir setara dengan stok awal (selisih 0 Pcs)."
                            break

                        has_changes = True
                        cat_palet = f"{kat_keluar}: {teks_tujuan}" if teks_tujuan else (kat_keluar if kat_keluar in ["WIP", "EC"] else "")
                        updated_rak, err_code = apply_slot_reduction(
                            data_rak_ini, b_pilih, g_key, sisa_utuh, sisa_dus, sisa_pcs, cat_palet
                        )
                        if err_code == "empty":
                            error_msg = f"❌ Data Lot {l} tidak ditemukan di rak terkini. Silakan klik 'Cari Data Rak' ulang."
                            break
                        if err_code.startswith("over:"):
                            error_msg = f"❌ Sisa akhir Lot {l} melebihi kapasitas stok terkini ({err_code.split(':')[1]} slot)."
                            break

                        data_rak_ini = updated_rak

                        if kat_keluar == "WIP":
                            jenis_tx = "SUPLAI WIP"
                            tujuan_final = f"WIP{f' ({teks_tujuan})' if teks_tujuan else ''}"
                        elif kat_keluar == "EC":
                            jenis_tx = "ERROR CORRECTION (EC)"
                            tujuan_final = f"EC: {teks_tujuan or 'Penyesuaian Stok Fisik'}"
                        elif kat_keluar == "CUSTOMER" and teks_tujuan:
                            jenis_tx = "OUTBOUND CUSTOMER"
                            tujuan_final = teks_tujuan
                        else:
                            jenis_tx = "OUTBOUND (Belum Terjadwal)"
                            tujuan_final = teks_tujuan if teks_tujuan else "OUT (Tujuan Menyusul)"

                        satuan_lbl = get_satuan_kemasan(pn)
                        detail_sys = (
                            f"Awal: {awal_utuh}P {awal_dus}{satuan_lbl} -> "
                            f"Sisa: {sisa_utuh}P {sisa_dus}{satuan_lbl} | "
                            f"Keluar: {kel_p}P {kel_d}{satuan_lbl}"
                            + (f" ({diff_pcs:,} pcs)" if diff_pcs > 0 else "")
                        )

                        logs_to_write.append((
                            jenis_tx, k_pilih, b_pilih,
                            clean_str(row['Produk']), pn, l,
                            kel_p, kel_d, diff_pcs if diff_pcs > 0 else kel_pcs,
                            tujuan_final, tujuan_final, detail_sys
                        ))

                if error_msg:
                    st.error(error_msg)
                elif has_changes:
                    data_rak_ini = sorted(data_rak_ini, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                    data_rak_ini = update_bayangan_baris(data_rak_ini, k_pilih, b_pilih)

                    final_kolom_data = data_rak_lain + data_rak_ini
                    save_kolom_to_supabase(k_pilih, final_kolom_data)
                    for lg in logs_to_write:
                        catat_log_mutasi(*lg)
                    clear_all_rak_caches()
                    st.session_state.update({
                        'undo_dict': {k_pilih: copy.deepcopy(fresh_out_data)},
                        'action_msg': f"Penyesuaian Outbound Massal di Rak {k_pilih}-{b_pilih}",
                        'action_status': 'DONE',
                        'kor_dicari': False,
                        'relok_dicari': False
                    })
                    st.rerun()
                else:
                    st.info("Tidak ada perubahan angka pada tabel. Outbound dibatalkan.")
        else:
            st.info("Rak Kosong.")

# ----------------------------------------------------------
# TAB 3: AUDIT STOK & BACKUP
# ----------------------------------------------------------
with tab3:
    st.header("⚙️ Audit Stok & Ekspor Data")
    st.subheader("🔧 Maintenance: Re-Indexing Global")
    st.write("Mengurutkan ulang urutan palet dari nomor 1 serta membersihkan data/tag bayangan di seluruh gudang.")
    st.caption(f"🕒 Terakhir Re-Index: **{st.session_state['last_reindex']}**")

    if st.button("⚙️ Jalankan Re-Indexing Semua Rak Sekarang", type="secondary"):
        with st.spinner("Memperbaiki urutan index pallet di Supabase..."):
            try:
                all_records_reidx = load_all_rak_from_supabase()
                total_koreksi = 0

                for kol_huruf in KOLOM_IDS:
                    valid_records = [
                        d for d in all_records_reidx
                        if d['Kolom'] == kol_huruf and is_valid_item(d)
                    ]
                    if not valid_records:
                        save_kolom_to_supabase(kol_huruf, [])
                        total_koreksi += 1
                        continue

                    df_col = pd.DataFrame(valid_records)
                    df_col['Baris'] = pd.to_numeric(df_col['Baris'], errors='coerce')
                    df_col['Pallet Ke'] = pd.to_numeric(df_col['Pallet Ke'], errors='coerce')
                    new_doc_data = []

                    for baris, group in df_col.groupby('Baris'):
                        group = group.sort_values('Pallet Ke', ascending=True)
                        baris_records = group.to_dict('records')
                        for r in baris_records:
                            r['Kolom'] = kol_huruf
                            r['Baris'] = float(baris)
                            r['Part Number'] = clean_str(r.get('Part Number')).upper()
                            r['Lot Number'] = clean_str(r.get('Lot Number'))
                            r['Nama Produk'] = clean_str(r.get('Nama Produk'))
                            r['Approval QC'] = clean_str(r.get('Approval QC'))
                            r['Jadwal Kirim'] = clean_str(r.get('Jadwal Kirim'))
                            r['Target Penerima'] = clean_str(r.get('Target Penerima'))
                            r['Status'] = 'Terisi'
                        baris_records = update_bayangan_baris(baris_records, kol_huruf, int(baris))
                        new_doc_data.extend(baris_records)

                    save_kolom_to_supabase(kol_huruf, new_doc_data)
                    total_koreksi += 1

                clear_all_rak_caches()
                st.session_state['last_reindex'] = get_waktu_jkt().strftime("%d-%m-%Y %H:%M:%S")
                st.balloons()
                st.session_state.update({
                    'action_msg': f"Re-Indexing & Pembersihan Selesai! {total_koreksi} Kolom diperbarui.",
                    'action_status': 'DONE_NO_UNDO'
                })
                st.rerun()
            except Exception as e:
                st.error(f"Gagal Re-Indexing: {e}")

    st.divider()
    st.subheader("📥 Tarik Laporan Audit (CSV Super)")
    if st.button("Racik Laporan Audit Stok Sekarang", type="primary"):
        with st.spinner("Membaca database Supabase..."):
            semua_data = load_all_rak_from_supabase()
            st.session_state['total_rak_bytes'] = sum(st.session_state['doc_sizes'].values())

            if semua_data:
                df_export = pd.DataFrame(semua_data)
                df_master = load_master_produk()

                qty_utuh_list, qty_dus_list, qty_pcs_list, grand_total_list = [], [], [], []
                for _, row in df_export.iterrows():
                    part_num_clean = clean_str(row.get('Part Number')).upper()
                    if not part_num_clean:
                        qty_utuh_list.append(0)
                        qty_dus_list.append(0)
                        qty_pcs_list.append(0)
                        grand_total_list.append(0)
                        continue

                    ket = clean_str(row.get('Keterangan')).lower()
                    is_pecahan = 'pecahan' in ket or 'eceran' in ket
                    q_utuh = 0 if is_pecahan else 1
                    q_dus, q_pcs = parse_keterangan(ket) if is_pecahan else (0, 0)

                    qty_utuh_list.append(q_utuh)
                    qty_dus_list.append(q_dus)
                    qty_pcs_list.append(q_pcs)

                    isi_pallet, pcs_per_satuan = get_master_konversi(df_master, part_num_clean)
                    grand_total_list.append((q_utuh * isi_pallet) + (q_dus * pcs_per_satuan) + q_pcs)

                for col_opt in ['Keterangan', 'Catatan', 'Approval QC', 'Jadwal Kirim', 'Target Penerima']:
                    df_export[col_opt] = df_export[col_opt].apply(clean_str) if col_opt in df_export.columns else ""
                df_export['Qty Utuh'] = qty_utuh_list
                df_export['Qty Dus/Sak'] = qty_dus_list
                df_export['Qty Pcs Eceran'] = qty_pcs_list
                df_export['Grand Total Pcs'] = grand_total_list

                kolom_harapan = [
                    'Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk',
                    'Keterangan', 'Catatan', 'Approval QC', 'Jadwal Kirim', 'Target Penerima', 'Status',
                    'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs'
                ]
                for c in kolom_harapan:
                    if c not in df_export.columns:
                        df_export[c] = ""

                for col in ['Baris', 'Pallet Ke', 'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs']:
                    df_export[col] = pd.to_numeric(df_export[col], errors='coerce').fillna(0).astype(int)
                    if col in ['Baris', 'Pallet Ke']:
                        df_export[col] = df_export[col].replace(0, "")

                df_export = df_export[kolom_harapan]
                csv_buffer = io.StringIO()
                df_export.to_csv(csv_buffer, index=False, sep=";")
                st.success("✅ CSV Super berhasil diracik!")
                st.download_button(
                    label="⬇️ Unduh CSV Super",
                    data=csv_buffer.getvalue(),
                    file_name=f"Audit_Stok_WMS_{get_waktu_jkt().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )
            else:
                st.warning("Database kosong.")

# ----------------------------------------------------------
# TAB 4: LAPORAN MUTASI & REVERSAL TRANSAKSI SALAH
# ----------------------------------------------------------
with tab4:
    st.header("📊 Laporan Riwayat Mutasi & Reversal")
    st.caption("Total Pcs otomatis dihitung ulang mengikuti Master_Produk.csv terbaru. Gunakan fitur Reversal untuk membatalkan transaksi outbound/inbound yang keliru.")

    hari_ini_jkt = get_waktu_jkt().date()
    c_tgl1, c_tgl2 = st.columns(2)
    with c_tgl1:
        tgl_mulai = st.date_input("Dari Tanggal", value=hari_ini_jkt, max_value=hari_ini_jkt)
    with c_tgl2:
        tgl_selesai = st.date_input("Sampai Tanggal", value=hari_ini_jkt, min_value=tgl_mulai, max_value=hari_ini_jkt)

    c_opt1, c_opt2 = st.columns(2)
    with c_opt1:
        tampilkan_ket_gabungan = st.checkbox(
            "Tampilkan kolom 'Keterangan' gabungan (untuk arsip lama)",
            value=False,
            key="chk_show_ket_gabungan"
        )
    with c_opt2:
        filter_arus = st.multiselect(
            "Filter Arus Transaksi (Opsional):",
            options=["MASUK", "KELUAR", "PINDAH", "KOREKSI"],
            default=[],
            key="filter_arus_log"
        )

    if st.button("Racik Laporan Mutasi Sekarang", type="primary"):
        with st.spinner("Menarik data log mutasi dari Supabase & menyesuaikan konversi Master..."):
            try:
                start_utc = (datetime.combine(tgl_mulai, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=7)).isoformat()
                end_utc = (datetime.combine(tgl_selesai, datetime.max.time(), tzinfo=timezone.utc) - timedelta(hours=7)).isoformat()

                res_logs = (
                    supabase.table(TBL_LOG_MUTASI)
                    .select("*")
                    .gte("waktu_transaksi", start_utc)
                    .lte("waktu_transaksi", end_utc)
                    .order("waktu_transaksi", desc=True)
                    .limit(5000)
                    .execute()
                )
                log_sb_usage('read', 1)

                raw_log_records = []
                for r in (res_logs.data or []):
                    dt_utc = pd.to_datetime(r.get('waktu_transaksi'), utc=True, errors='coerce')
                    waktu_wib_str = (dt_utc + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S") if pd.notna(dt_utc) else ""
                    jenis_val = clean_str(r.get('jenis_transaksi'))
                    arus_val = clean_str(r.get('arus')) or tentukan_arus_log(jenis_val)
                    tuj_val = clean_str(r.get('tujuan_kategori')) or clean_str(r.get('keterangan'))
                    det_val = clean_str(r.get('detail_sistem'))

                    raw_log_records.append({
                        '_doc_id': r.get('id'),
                        'Waktu Transaksi': waktu_wib_str,
                        'Arus': arus_val,
                        'Jenis Transaksi': jenis_val,
                        'Lokasi Rak': clean_str(r.get('lokasi_rak')),
                        'Part Number': clean_str(r.get('part_number')),
                        'Nama Produk': clean_str(r.get('nama_produk')),
                        'No Lot': clean_str(r.get('lot_number')) or "-",
                        'Mutasi Pallet': float(r.get('mutasi_pallet') or 0),
                        'Mutasi Pecahan (Dus/Sak)': float(r.get('mutasi_pecahan') or 0),
                        'Mutasi Eceran (Pcs)': float(r.get('mutasi_eceran') or r.get('total_pcs') or 0),
                        'Tujuan / Kategori': tuj_val,
                        'Detail Sistem': det_val,
                        'Keterangan': clean_str(r.get('keterangan')),
                    })

                st.session_state['log_loaded_records'] = raw_log_records
                if not raw_log_records:
                    st.info("Tidak ada catatan mutasi pada rentang tanggal tersebut.")
            except Exception as e:
                st.error(f"Gagal memuat log mutasi: {e}")

    if st.session_state.get('log_loaded_records'):
        df_log_raw = pd.DataFrame(st.session_state['log_loaded_records'])
        df_master_log = load_master_produk()
        df_log = hitung_ulang_pcs_log(df_log_raw.copy(), df_master_log)

        if filter_arus:
            df_log = df_log[df_log['Arus'].isin(filter_arus)]

        target_cols = [
            "Waktu Transaksi", "Arus", "Jenis Transaksi", "Lokasi Rak",
            "Part Number", "Nama Produk", "No Lot",
            "Mutasi Pallet", "Mutasi Pecahan (Dus/Sak)",
            "Total Pcs (Master Terbaru)", "Status Konversi",
            "Tujuan / Kategori", "Detail Sistem"
        ]
        if tampilkan_ket_gabungan:
            target_cols.append("Keterangan")

        avail_cols = [c for c in target_cols if c in df_log.columns]
        df_log_display = df_log[avail_cols]

        jml_recalc = int((df_log_display['Status Konversi'].str.startswith("Master Baru")).sum()) if 'Status Konversi' in df_log_display.columns else 0
        st.dataframe(df_log_display, hide_index=True, use_container_width=True, height=300)

        csv_log_buffer = io.StringIO()
        df_log_display.to_csv(csv_log_buffer, index=False, sep=";")
        info_recalc = f" | **{jml_recalc} baris** disesuaikan dengan Master terbaru" if jml_recalc > 0 else ""
        st.success(f"✅ Menampilkan **{len(df_log_display)}** catatan mutasi{info_recalc}.")
        st.download_button(
            label="⬇️ Unduh Laporan Mutasi",
            data=csv_log_buffer.getvalue(),
            file_name=f"Mutasi_WMS_{tgl_mulai.strftime('%Y%m%d')}_{tgl_selesai.strftime('%Y%m%d')}.csv",
            mime="text/csv"
        )

        # --- PANEL REVERSAL TRANSAKSI SALAH ---
        st.divider()
        with st.expander("⏪ Batalkan / Balikkan Transaksi Salah (Reversal Mutasi)", expanded=False):
            st.caption("Pilih transaksi `KELUAR` (Outbound/WIP) atau `MASUK` (Inbound) yang keliru.")

            kandidat_rev = []
            for r_log in st.session_state['log_loaded_records']:
                arus_r = clean_str(r_log.get('Arus')) or tentukan_arus_log(r_log.get('Jenis Transaksi'))
                lok_r = clean_str(r_log.get('Lokasi Rak'))
                pn_r = clean_str(r_log.get('Part Number')).upper()
                jenis_r = clean_str(r_log.get('Jenis Transaksi'))
                m_pal = int(float(pd.to_numeric(r_log.get('Mutasi Pallet', 0), errors='coerce') or 0))
                m_pec = int(float(pd.to_numeric(r_log.get('Mutasi Pecahan (Dus/Sak)', 0), errors='coerce') or 0))
                m_pcs = int(float(pd.to_numeric(r_log.get('Mutasi Eceran (Pcs)', 0), errors='coerce') or 0))

                if (arus_r in ["KELUAR", "MASUK"]
                        and re.fullmatch(r'[A-GS]-\d{1,2}', lok_r)
                        and pn_r and pn_r != "-"
                        and "REVERSAL" not in jenis_r.upper()
                        and (m_pal > 0 or m_pec > 0 or m_pcs > 0)):
                    sat_r = get_satuan_kemasan(pn_r)
                    lbl_rev = (
                        f"[{clean_str(r_log.get('Waktu Transaksi'))}] {arus_r} | {lok_r} | "
                        f"{clean_str(r_log.get('Nama Produk'))} (Lot: {clean_str(r_log.get('No Lot'))}) | "
                        f"{m_pal}P {m_pec} {sat_r} ({m_pcs:,} pcs) — {clean_str(r_log.get('Tujuan / Kategori'))}"
                    )
                    kandidat_rev.append((lbl_rev, r_log))

            if kandidat_rev:
                pilih_rev_lbl = st.selectbox(
                    "Pilih Transaksi yang Akan Dibalikkan:",
                    options=[x[0] for x in kandidat_rev],
                    key="select_reversal_tx"
                )
                alasan_rev = st.text_input(
                    "Alasan Reversal (Wajib diisi):",
                    placeholder="Cth: Salah input qty outbound customer MAS",
                    key="input_alasan_reversal"
                )
                target_log = next(x[1] for x in kandidat_rev if x[0] == pilih_rev_lbl)

                if st.button("⏪ Eksekusi Reversal Sekarang", type="primary"):
                    if not clean_str(alasan_rev):
                        st.error("⚠️ Alasan reversal wajib diisi untuk jejak audit!")
                    else:
                        lok_parts = clean_str(target_log.get('Lokasi Rak')).split("-")
                        rk_kol, rk_baris = lok_parts[0], int(lok_parts[1])
                        rk_pn = clean_str(target_log.get('Part Number')).upper()
                        rk_nama = clean_str(target_log.get('Nama Produk'))
                        rk_lot = clean_str(target_log.get('No Lot'))
                        rk_arus = clean_str(target_log.get('Arus')) or tentukan_arus_log(target_log.get('Jenis Transaksi'))
                        rk_pal = int(float(pd.to_numeric(target_log.get('Mutasi Pallet', 0), errors='coerce') or 0))
                        rk_pec = int(float(pd.to_numeric(target_log.get('Mutasi Pecahan (Dus/Sak)', 0), errors='coerce') or 0))
                        rk_pcs_tot = int(float(pd.to_numeric(target_log.get('Mutasi Eceran (Pcs)', 0), errors='coerce') or 0))

                        isi_p_rev, pcs_s_rev = get_master_konversi(df_master_log, rk_pn)
                        if isi_p_rev > 0 and pcs_s_rev > 0:
                            sisa_rem_pcs = max(0, rk_pcs_tot - (rk_pal * isi_p_rev) - (rk_pec * pcs_s_rev))
                            rk_ec_only = sisa_rem_pcs
                        else:
                            rk_ec_only = rk_pcs_tot if (rk_pal == 0 and rk_pec == 0) else 0

                        fresh_rev = load_kolom_from_supabase(rk_kol)
                        working_rev = copy.deepcopy(fresh_rev)
                        baris_rev = get_baris_records(working_rev, rk_baris, only_valid=True)
                        lain_rev = [d for d in working_rev if not is_same_baris(d, rk_baris)]

                        sukses_rev, err_rev = True, ""
                        if rk_arus == "KELUAR":
                            rest_records = [
                                {
                                    'Kolom': rk_kol, 'Baris': float(rk_baris), 'Pallet Ke': 0.0,
                                    'Part Number': rk_pn, 'Lot Number': rk_lot, 'Nama Produk': rk_nama,
                                    'Status': 'Terisi', 'Keterangan': '', 'Catatan': '',
                                    'Approval QC': '', 'Jadwal Kirim': '', 'Target Penerima': ''
                                }
                                for _ in range(rk_pal)
                            ]
                            if rk_pec > 0 or rk_ec_only > 0:
                                merged = False
                                for item_ex in baris_rev:
                                    if (clean_str(item_ex.get('Part Number')).upper() == rk_pn
                                            and clean_str(item_ex.get('Lot Number')) == rk_lot
                                            and ('Pecahan' in clean_str(item_ex.get('Keterangan')) or 'Eceran' in clean_str(item_ex.get('Keterangan')))):
                                        ex_p, ex_e = parse_keterangan(item_ex.get('Keterangan'))
                                        item_ex['Keterangan'] = format_keterangan(ex_p + rk_pec, ex_e + rk_ec_only)
                                        merged = True
                                        break
                                if not merged:
                                    rest_records.append({
                                        'Kolom': rk_kol, 'Baris': float(rk_baris), 'Pallet Ke': 0.0,
                                        'Part Number': rk_pn, 'Lot Number': rk_lot, 'Nama Produk': rk_nama,
                                        'Status': 'Terisi', 'Keterangan': format_keterangan(rk_pec, rk_ec_only),
                                        'Catatan': '', 'Approval QC': '', 'Jadwal Kirim': '', 'Target Penerima': ''
                                    })
                            baris_sorted = sorted(baris_rev, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                            combined_baris = update_bayangan_baris(baris_sorted + rest_records, rk_kol, rk_baris)
                        else:
                            stok_lot = hitung_ringkasan_stok_lot(baris_rev, df_master_log).get((rk_pn, rk_lot))
                            if not stok_lot:
                                sukses_rev, err_rev = False, f"❌ Stok Lot {rk_lot} tidak ditemukan di Rak {rk_kol}-{rk_baris} untuk ditarik kembali."
                            else:
                                _, _, _, diff_p = hitung_konversi_mutasi_pcs(
                                    df_master_log, rk_pn,
                                    stok_lot['utuh'], stok_lot['dus'], stok_lot['pcs_ec'],
                                    max(0, stok_lot['utuh'] - rk_pal),
                                    max(0, stok_lot['dus'] - rk_pec),
                                    max(0, stok_lot['pcs_ec'] - rk_ec_only)
                                )
                                if diff_p == -1 or (stok_lot['tot_pcs'] < rk_pcs_tot and isi_p_rev > 0):
                                    sukses_rev, err_rev = False, f"❌ Stok fisik Lot {rk_lot} di Rak {rk_kol}-{rk_baris} sudah berkurang, tidak cukup untuk ditarik."
                                else:
                                    target_sisa_pcs = max(0, stok_lot['tot_pcs'] - rk_pcs_tot) if isi_p_rev > 0 else 0
                                    if isi_p_rev > 0 and pcs_s_rev > 0:
                                        sisa_u_baru = target_sisa_pcs // isi_p_rev
                                        rem_u = target_sisa_pcs % isi_p_rev
                                        sisa_d_baru = rem_u // pcs_s_rev
                                        sisa_e_baru = rem_u % pcs_s_rev
                                    else:
                                        sisa_u_baru = max(0, stok_lot['utuh'] - rk_pal)
                                        sisa_d_baru = max(0, stok_lot['dus'] - rk_pec)
                                        sisa_e_baru = max(0, stok_lot['pcs_ec'] - rk_ec_only)

                                    sample_match = next(
                                        (d for d in baris_rev if clean_str(d.get('Part Number')).upper() == rk_pn and clean_str(d.get('Lot Number')) == rk_lot),
                                        None
                                    )
                                    g_key_rev = make_g_key(
                                        sample_match.get('Lot Number'), sample_match.get('Nama Produk'),
                                        sample_match.get('Catatan'), sample_match.get('Jadwal Kirim'),
                                        sample_match.get('Target Penerima')
                                    )
                                    updated_baris, err_c = apply_slot_reduction(baris_rev, rk_baris, g_key_rev, sisa_u_baru, sisa_d_baru, sisa_e_baru)
                                    if err_c:
                                        sukses_rev, err_rev = False, f"❌ Gagal mengurangi slot: {err_c}"
                                    else:
                                        combined_baris = update_bayangan_baris(
                                            sorted(updated_baris, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))),
                                            rk_kol, rk_baris
                                        )

                        if not sukses_rev:
                            st.error(err_rev)
                        else:
                            save_kolom_to_supabase(rk_kol, lain_rev + combined_baris)
                            jenis_jurnal_rev = "REVERSAL OUTBOUND (Pengembalian Stok)" if rk_arus == "KELUAR" else "REVERSAL INBOUND (Tarik Stok)"
                            det_jurnal_rev = f"Membalikkan transaksi [{clean_str(target_log.get('Waktu Transaksi'))}] | Alasan: {clean_str(alasan_rev)}"
                            catat_log_mutasi(
                                jenis_jurnal_rev, rk_kol, rk_baris, rk_nama, rk_pn, rk_lot,
                                rk_pal, rk_pec, rk_pcs_tot,
                                keterangan=det_jurnal_rev,
                                tujuan_kategori=f"REVERSAL: {clean_str(alasan_rev)}",
                                detail_sistem=det_jurnal_rev
                            )
                            clear_all_rak_caches()
                            st.session_state['log_loaded_records'] = []
                            st.session_state.update({
                                'undo_dict': {rk_kol: copy.deepcopy(fresh_rev)},
                                'action_msg': f"Reversal berhasil di Rak {rk_kol}-{rk_baris} ({rk_nama} Lot {rk_lot})",
                                'action_status': 'DONE'
                            })
                            st.rerun()
            else:
                st.info("Tidak ada transaksi MASUK/KELUAR pada lokasi rak spesifik dalam daftar di atas yang dapat di-reversal.")

    st.divider()
    with st.expander("🗑️ Bersihkan Log Mutasi Lama (Hemat Storage Supabase)"):
        st.warning("⚠️ Pastikan Anda sudah mengunduh laporan CSV di atas sebagai arsip sebelum menghapus data dari database!")
        batas_default = hari_ini_jkt - timedelta(days=90)
        tgl_batas_hapus = st.date_input(
            "Hapus seluruh log SEBELUM tanggal:",
            value=batas_default,
            max_value=hari_ini_jkt,
            key="tgl_batas_hapus"
        )
        konfirmasi_hapus = st.checkbox(f"Saya sudah backup CSV dan yakin menghapus log sebelum {tgl_batas_hapus.strftime('%d-%m-%Y')}.")

        if st.button("🗑️ Hapus Log Lama", type="secondary", disabled=not konfirmasi_hapus):
            with st.spinner("Menghapus log mutasi lama dari Supabase..."):
                try:
                    cutoff_utc = (datetime.combine(tgl_batas_hapus, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=7)).isoformat()
                    del_res = (
                        supabase.table(TBL_LOG_MUTASI)
                        .delete()
                        .lt("waktu_transaksi", cutoff_utc)
                        .execute()
                    )
                    log_sb_usage('delete', 1)
                    jml_terhapus = len(del_res.data or [])
                    st.session_state.pop('total_log_bytes', None)
                    st.session_state.pop('total_log_docs', None)
                    st.success(f"✅ Berhasil menghapus **{jml_terhapus}** baris log mutasi lama.")
                except Exception as e:
                    st.error(f"Gagal menghapus log: {e}")

# ----------------------------------------------------------
# TAB 5: SYNC CSV (RESTORE)
# ----------------------------------------------------------
with tab5:
    st.header("🚀 Restore Data Database dari CSV")
    st.warning("⚠️ **PERINGATAN:** Fitur ini berfungsi sebagai *Disaster Recovery*. Jika dieksekusi, sistem akan **MENGHAPUS SELURUH ISI RAK** dan menimpanya dengan isi dari file CSV yang diunggah.")

    uploaded_file = st.file_uploader("Upload File Backup CSV (.csv)", type=['csv'])
    if uploaded_file is not None:
        if st.button("🚨 SAYA YAKIN, HAPUS DATABASE & TIMPA DENGAN CSV INI", type="primary", use_container_width=True):
            with st.spinner("Membaca CSV dan menimpa database Supabase..."):
                try:
                    try:
                        df_sync = pd.read_csv(uploaded_file, sep=";")
                    except Exception:
                        df_sync = pd.read_csv(uploaded_file, sep=",")

                    req_cols = ['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk']
                    if not all(col in df_sync.columns for col in req_cols):
                        st.error("❌ Format CSV tidak valid! Kolom wajib tidak ditemukan.")
                    else:
                        for kol in KOLOM_IDS:
                            df_kol = df_sync[df_sync['Kolom'].apply(clean_str) == kol]
                            new_data = []
                            if not df_kol.empty:
                                df_kol = df_kol.sort_values(by=['Baris', 'Pallet Ke'])
                                for _, row in df_kol.iterrows():
                                    pn = clean_str(row['Part Number']).upper()
                                    if not pn:
                                        continue
                                    new_data.append({
                                        'Kolom': kol,
                                        'Baris': float(row['Baris']),
                                        'Pallet Ke': float(row['Pallet Ke']),
                                        'Part Number': pn,
                                        'Lot Number': clean_str(row['Lot Number']),
                                        'Nama Produk': clean_str(row['Nama Produk']),
                                        'Keterangan': clean_str(row.get('Keterangan')),
                                        'Catatan': clean_str(row.get('Catatan')),
                                        'Approval QC': clean_str(row.get('Approval QC')),
                                        'Jadwal Kirim': clean_str(row.get('Jadwal Kirim')),
                                        'Target Penerima': clean_str(row.get('Target Penerima')),
                                        'Status': 'Terisi'
                                    })
                            save_kolom_to_supabase(kol, new_data)

                        clear_all_rak_caches()
                        catat_log_mutasi(
                            "RESTORE CSV", "ALL", "ALL", "Sistem Database", "-", "-", 0, 0, 0,
                            keterangan="Hard Reset via CSV Upload",
                            tujuan_kategori="RESTORE DATABASE",
                            detail_sistem="Hard Reset via CSV Upload"
                        )

                        st.session_state.update({
                            'action_msg': "Database Supabase berhasil di-restore dari file CSV!",
                            'action_status': 'DONE_NO_UNDO'
                        })
                        st.rerun()
                except Exception as e:
                    st.error(f"Terjadi kesalahan saat memproses CSV: {e}")

# ----------------------------------------------------------
# TAB 6: RELOKASI RAK (PUSH-BACK & ROTASI LORONG SAMA)
# ----------------------------------------------------------
with tab6:
    st.header("🔄 Relokasi Pallet (Pindah Rak / Rotasi Push-Back)")
    st.caption("Pindahkan item antar rak secara presisi (Pallet, Dus, Pcs), atau tarik Lot ke posisi terdepan (Pallet Ke-1) dengan mode Push-Back.")

    col_r1, col_r2 = st.columns(2)
    with col_r1:
        st.markdown("**Rak ASAL (Sumber)**")
        r_asal_k = st.selectbox("Kolom Asal", KOLOM_OPTIONS, key="r_ak").split(" ")[0]
        r_asal_b = st.number_input("Baris Asal", min_value=1, max_value=29, value=1, key="r_ab")
    with col_r2:
        st.markdown("**Rak TUJUAN (Destinasi)**")
        r_tuj_k = st.selectbox("Kolom Tujuan", KOLOM_OPTIONS, key="r_tk").split(" ")[0]
        r_tuj_b = st.number_input("Baris Tujuan", min_value=1, max_value=29, value=1, key="r_tb")

    if st.button("🔍 Cari Data Rak Asal", type="primary", use_container_width=True):
        st.session_state.update({'relok_dicari': True, 'relok_ak': r_asal_k, 'relok_ab': r_asal_b})
        fetch_kolom_cached(r_asal_k, 'cache_relok_asal', force_refresh=True)

    if st.session_state.get('relok_dicari'):
        ak, ab = st.session_state['relok_ak'], st.session_state['relok_ab']
        tk, tb = r_tuj_k, r_tuj_b
        is_same_rack = (ak == tk and ab == tb)

        all_asal_data = fetch_kolom_cached(ak, 'cache_relok_asal', force_refresh=False)
        baris_asal_data = get_baris_records(all_asal_data, ab, only_valid=True)

        if baris_asal_data:
            df_master_relok = load_master_produk()
            rekap_relok = [
                {
                    'Group_Key': g['Group_Key'],
                    'Produk': g['Produk'],
                    'Lot Number': g['Lot Number'],
                    'Catatan': g['Catatan'],
                    'Approval QC': g['Approval QC'],
                    'Jadwal Kirim': g['Jadwal Kirim'],
                    'Target Penerima': g['Target Penerima'],
                    'Part Number': g['Part Number'],
                    'Tersedia Utuh': g['Utuh'],
                    'Tersedia Dus': g['Dus'],
                    'Tersedia Pcs': g['Pcs'],
                    'PINDAH UTUH': 0,
                    'PINDAH DUS': 0,
                    'PINDAH PCS': 0
                }
                for g in summarize_lot_groups(baris_asal_data)
            ]

            df_rekap_relok = pd.DataFrame(rekap_relok)

            st.divider()
            st.markdown(f"**2. Tentukan Jumlah yang Dipindah dari `{ak}-{ab}` ke `{tk}-{tb}`**")

            config_kolom = {
                'Group_Key': None, 'Part Number': None, 'Approval QC': None,
                'Jadwal Kirim': None, 'Target Penerima': None,
                'Produk': st.column_config.TextColumn("Produk", disabled=True),
                'Lot Number': st.column_config.TextColumn("Lot", disabled=True),
                'Catatan': st.column_config.TextColumn("Tag / Catatan", disabled=True),
                'Tersedia Utuh': st.column_config.NumberColumn("Stok Utuh", disabled=True),
                'Tersedia Dus': st.column_config.NumberColumn("Stok Dus", disabled=True),
                'Tersedia Pcs': st.column_config.NumberColumn("Stok Pcs", disabled=True),
                'PINDAH UTUH': st.column_config.NumberColumn("🚚 PINDAH UTUH", min_value=0),
                'PINDAH DUS': st.column_config.NumberColumn("📦 PINDAH DUS", min_value=0),
                'PINDAH PCS': st.column_config.NumberColumn("🧩 PINDAH PCS", min_value=0)
            }

            edited_relok = st.data_editor(
                df_rekap_relok, column_config=config_kolom, hide_index=True,
                use_container_width=True, key=f"editor_relok_{ak}_{ab}_{tk}_{tb}"
            )

            relok_dari_depan = st.checkbox(
                "📥 Masukkan ke Rak Tujuan dari DEPAN (Push-back)",
                value=is_same_rack,
                key="relok_dari_depan"
            )

            if is_same_rack:
                if relok_dari_depan:
                    st.info(f"🔄 **Mode Rotasi Lorong ({ak}-{ab}):** Item terpilih akan ditarik keluar dan dimasukkan kembali dari pintu depan (**Pallet Ke-1**), mendorong slot lainnya ke belakang.")
                else:
                    st.warning("⚠️ Rak Asal dan Tujuan sama. Centang opsi **Push-back** di atas jika ingin memindahkan item ke posisi terdepan (Pallet Ke-1).")

            if st.button("🚀 Eksekusi Pindah Rak", type="primary", use_container_width=True):
                if is_same_rack and not relok_dari_depan:
                    st.error("❌ Rak asal dan tujuan sama! Aktifkan checkbox Push-back jika ingin memutar posisi Lot ke depan.")
                else:
                    fresh_asal = load_kolom_from_supabase(ak)
                    fresh_tuj = load_kolom_from_supabase(tk) if ak != tk else copy.deepcopy(fresh_asal)

                    has_changes = False
                    error_msg = ""
                    logs_to_write = []

                    data_rak_asal = copy.deepcopy(fresh_asal)
                    data_rak_tujuan = data_rak_asal if ak == tk else copy.deepcopy(fresh_tuj)

                    undo_dict_setup = {ak: copy.deepcopy(fresh_asal)}
                    if ak != tk:
                        undo_dict_setup[tk] = copy.deepcopy(fresh_tuj)

                    all_moved_records = []

                    for _, row in edited_relok.iterrows():
                        pindah_utuh, pindah_dus, pindah_pcs = int(row['PINDAH UTUH']), int(row['PINDAH DUS']), int(row['PINDAH PCS'])
                        if pindah_utuh == 0 and pindah_dus == 0 and pindah_pcs == 0:
                            continue

                        g_key = str(row['Group_Key'])
                        l = clean_str(row['Lot Number'])
                        nama_prod = clean_str(row['Produk'])
                        part_num = clean_str(row['Part Number']).upper()
                        catatan_awal = clean_str(row['Catatan'])
                        qc_awal = clean_str(row.get('Approval QC'))
                        jk_awal = clean_str(row.get('Jadwal Kirim'))
                        tp_awal = clean_str(row.get('Target Penerima'))

                        sisa_utuh = int(row['Tersedia Utuh']) - pindah_utuh
                        sisa_dus = int(row['Tersedia Dus']) - pindah_dus
                        sisa_pcs = int(row['Tersedia Pcs']) - pindah_pcs

                        if sisa_utuh < 0 or sisa_dus < 0 or sisa_pcs < 0:
                            error_msg = f"❌ Jumlah pindah Lot {l} melebihi stok tersedia!"
                            break

                        has_changes = True
                        updated_asal, err_code = apply_slot_reduction(
                            data_rak_asal, ab, g_key, sisa_utuh, sisa_dus, sisa_pcs
                        )
                        if err_code == "empty":
                            error_msg = f"❌ Data Lot {l} di rak asal tidak ditemukan. Silakan klik 'Cari Data Rak Asal' ulang."
                            break
                        if err_code.startswith("over:"):
                            error_msg = f"❌ Stok terkini Lot {l} tidak mencukupi untuk dipindahkan."
                            break

                        data_rak_asal = updated_asal
                        if ak == tk:
                            data_rak_tujuan = data_rak_asal

                        catatan_baru = catatan_awal.replace("[BAYANGAN - OVERCAPACITY]", "").strip()
                        if tk == 'S' and ak != 'S':
                            cat_bersih = re.sub(r'\[Asal:.*?\]\s*', '', catatan_baru)
                            catatan_baru = f"[Asal: {ak}-{ab}] {cat_bersih}".strip()
                        elif tk != 'S':
                            catatan_baru = re.sub(r'\[Asal:.*?\]\s*', '', catatan_baru).strip()

                        for _ in range(pindah_utuh):
                            all_moved_records.append({
                                'Kolom': tk, 'Baris': float(tb), 'Pallet Ke': 0.0,
                                'Part Number': part_num, 'Lot Number': l, 'Nama Produk': nama_prod,
                                'Status': 'Terisi', 'Keterangan': '', 'Catatan': catatan_baru,
                                'Approval QC': qc_awal, 'Jadwal Kirim': jk_awal, 'Target Penerima': tp_awal
                            })

                        if pindah_dus > 0 or pindah_pcs > 0:
                            all_moved_records.append({
                                'Kolom': tk, 'Baris': float(tb), 'Pallet Ke': 0.0,
                                'Part Number': part_num, 'Lot Number': l, 'Nama Produk': nama_prod,
                                'Status': 'Terisi', 'Keterangan': format_keterangan(pindah_dus, pindah_pcs),
                                'Catatan': catatan_baru, 'Approval QC': qc_awal,
                                'Jadwal Kirim': jk_awal, 'Target Penerima': tp_awal
                            })

                        isi_p, pcs_s = get_master_konversi(df_master_relok, part_num)
                        tot_pindah_pcs = ((pindah_utuh * isi_p) + (pindah_dus * pcs_s) + pindah_pcs) if isi_p > 0 else pindah_pcs

                        jenis_log = f"RELOKASI{' (Push-Back)' if relok_dari_depan else ''}"
                        rute_relok = f"{ak}-{ab} -> {tk}-{tb}"
                        logs_to_write.append((
                            jenis_log, rute_relok, "NONE", nama_prod, part_num, l,
                            pindah_utuh, pindah_dus, tot_pindah_pcs,
                            f"Pindah ke {tk}-{tb}", f"Pindah ke {tk}-{tb}",
                            f"Asal: {ak}-{ab} | Tujuan: {tk}-{tb}"
                        ))

                    if error_msg:
                        st.error(error_msg)
                    elif has_changes:
                        if ak == tk and ab == tb:
                            existing_tb = sorted(get_baris_records(data_rak_tujuan, tb), key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                            lain_tb = [d for d in data_rak_tujuan if not is_same_baris(d, tb)]
                            combined_tb = update_bayangan_baris((all_moved_records + existing_tb) if relok_dari_depan else (existing_tb + all_moved_records), tk, tb)
                            save_kolom_to_supabase(ak, lain_tb + combined_tb)

                        elif ak == tk and ab != tb:
                            baris_asal_sisa = update_bayangan_baris(sorted(get_baris_records(data_rak_asal, ab), key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))), ak, ab)
                            existing_tb = sorted(get_baris_records(data_rak_asal, tb), key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                            combined_tb = update_bayangan_baris((all_moved_records + existing_tb) if relok_dari_depan else (existing_tb + all_moved_records), tk, tb)
                            lain_bars = [d for d in data_rak_asal if not (is_same_baris(d, ab) or is_same_baris(d, tb))]
                            save_kolom_to_supabase(ak, lain_bars + baris_asal_sisa + combined_tb)

                        else:
                            baris_asal_sisa = update_bayangan_baris(sorted(get_baris_records(data_rak_asal, ab), key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))), ak, ab)
                            lain_asal = [d for d in data_rak_asal if not is_same_baris(d, ab)]
                            save_kolom_to_supabase(ak, lain_asal + baris_asal_sisa)

                            existing_tb = sorted(get_baris_records(data_rak_tujuan, tb), key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                            combined_tb = update_bayangan_baris((all_moved_records + existing_tb) if relok_dari_depan else (existing_tb + all_moved_records), tk, tb)
                            lain_tuj = [d for d in data_rak_tujuan if not is_same_baris(d, tb)]
                            save_kolom_to_supabase(tk, lain_tuj + combined_tb)

                        for lg in logs_to_write:
                            catat_log_mutasi(*lg)
                        clear_all_rak_caches()
                        mode_txt = " (Push-Back ke Depan)" if relok_dari_depan else ""
                        st.session_state.update({
                            'undo_dict': copy.deepcopy(undo_dict_setup),
                            'action_msg': f"Berhasil memindahkan item dari Rak {ak}-{ab} ke {tk}-{tb}{mode_txt}",
                            'action_status': 'DONE',
                            'relok_dicari': False,
                            'kor_dicari': False
                        })
                        st.rerun()
                    else:
                        st.info("Tidak ada jumlah yang dipindah.")
        else:
            st.warning("Rak Asal Kosong!")
            st.session_state['relok_dicari'] = False

# ----------------------------------------------------------
# TAB 7: KOREKSI DATA
# ----------------------------------------------------------
with tab7:
    st.header("✏️ Koreksi Data Rak (Konservasi Mutasi Otomatis)")
    st.caption("Edit atribut slot, tambah baris palet yang kurang, atau hapus baris berlebih.")

    c_kor1, c_kor2, c_kor3 = st.columns([1, 1, 2])
    with c_kor1:
        kor_kolom = st.selectbox("Lokasi Kolom", KOLOM_OPTIONS, key="kor_kolom").split(" ")[0]
    with c_kor2:
        kor_baris = st.number_input("Nomor Baris", min_value=1, max_value=29, value=1, key="kor_baris")
    with c_kor3:
        st.write("")
        st.write("")
        if st.button("🔍 Tarik Data untuk Koreksi", use_container_width=True):
            st.session_state.update({'kor_dicari': True, 'kor_k': kor_kolom, 'kor_b': kor_baris})
            fetch_kolom_cached(kor_kolom, 'cache_kor_data', force_refresh=True)

    st.divider()

    if st.session_state.get('kor_dicari'):
        kk, kb = st.session_state['kor_k'], st.session_state['kor_b']
        all_kor_data = fetch_kolom_cached(kk, 'cache_kor_data', force_refresh=False)
        baris_kor_data = get_baris_records(all_kor_data, kb, only_valid=True)

        df_kor = pd.DataFrame(baris_kor_data) if baris_kor_data else pd.DataFrame(
            columns=['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk', 'Keterangan', 'Catatan', 'Approval QC', 'Jadwal Kirim', 'Target Penerima', 'Status']
        )
        if not df_kor.empty:
            df_kor['Pallet Ke'] = pd.to_numeric(df_kor['Pallet Ke'], errors='coerce')
            for col in ['Part Number', 'Lot Number', 'Nama Produk', 'Keterangan', 'Catatan', 'Approval QC', 'Jadwal Kirim', 'Target Penerima']:
                df_kor[col] = df_kor[col].apply(clean_str) if col in df_kor.columns else ""
            df_kor = df_kor.sort_values('Pallet Ke', ascending=True).reset_index(drop=True)

        st.markdown(f"**Data Baris {kk}-{kb}** *(Anda dapat menambah/menghapus baris di tabel bawah ini)*")

        config_kor = {
            'Kolom': None, 'Baris': None, 'Status': None,
            'Pallet Ke': st.column_config.NumberColumn("Idx Pallet", disabled=True),
            'Part Number': st.column_config.TextColumn("Part Number", required=True),
            'Lot Number': st.column_config.TextColumn("Lot Number", required=True),
            'Nama Produk': st.column_config.TextColumn("Nama Produk", required=True),
            'Keterangan': st.column_config.TextColumn("Keterangan (Pecahan: X qty)"),
            'Catatan': st.column_config.TextColumn("Catatan / Tag"),
            'Approval QC': st.column_config.TextColumn("Approval QC"),
            'Jadwal Kirim': st.column_config.TextColumn("Jadwal Kirim (DD-MM-YYYY)"),
            'Target Penerima': st.column_config.TextColumn("Target Penerima")
        }

        edited_kor_df = st.data_editor(
            df_kor, column_config=config_kor, hide_index=True,
            use_container_width=True, num_rows="dynamic", key=f"editor_kor_{kk}_{kb}"
        )

        alasan_koreksi_input = st.text_input(
            "Alasan Koreksi (Dicatat di Jurnal Mutasi):",
            placeholder="Cth: Koreksi salah jumlah outbound customer MAS / Salah tulis No Lot",
            key=f"alasan_kor_{kk}_{kb}"
        )

        if st.button("💾 Simpan Perubahan Koreksi", type="primary"):
            if not edited_kor_df.equals(df_kor):
                df_master_kor = load_master_produk()
                raw_edited_records = edited_kor_df.to_dict('records')

                invalid_new = [
                    r for r in raw_edited_records
                    if clean_str(r.get('Part Number')) == "" and (
                        clean_str(r.get('Nama Produk')) != "" or clean_str(r.get('Lot Number')) != ""
                    )
                ]
                if invalid_new:
                    st.error("❌ Baris baru wajib memiliki Part Number yang valid!")
                else:
                    with st.spinner("Menyimpan koreksi & menyeimbangkan jurnal mutasi..."):
                        new_baris_data = []
                        for d in raw_edited_records:
                            pn_clean = clean_str(d.get('Part Number')).upper()
                            if not pn_clean:
                                continue
                            nama_clean = clean_str(d.get('Nama Produk'))
                            if not nama_clean and not df_master_kor.empty and 'Part Number Clean' in df_master_kor.columns:
                                m_nm = df_master_kor[df_master_kor['Part Number Clean'] == pn_clean]['Nama Barang'].values
                                if len(m_nm) > 0:
                                    nama_clean = clean_str(m_nm[0])

                            new_baris_data.append({
                                'Kolom': kk,
                                'Baris': float(kb),
                                'Pallet Ke': float(pd.to_numeric(d.get('Pallet Ke', 0), errors='coerce') or 0),
                                'Status': 'Terisi',
                                'Part Number': pn_clean,
                                'Lot Number': clean_str(d.get('Lot Number')) or "-",
                                'Nama Produk': nama_clean,
                                'Keterangan': clean_str(d.get('Keterangan')),
                                'Catatan': clean_str(d.get('Catatan')),
                                'Approval QC': clean_str(d.get('Approval QC')),
                                'Jadwal Kirim': clean_str(d.get('Jadwal Kirim')),
                                'Target Penerima': clean_str(d.get('Target Penerima'))
                            })

                        new_baris_data = update_bayangan_baris(new_baris_data, kk, kb)

                        fresh_kor = load_kolom_from_supabase(kk)
                        lain_kor_fresh = [d for d in fresh_kor if not is_same_baris(d, kb)]
                        final_kolom_data = lain_kor_fresh + new_baris_data
                        save_kolom_to_supabase(kk, final_kolom_data)

                        stok_sebelum = hitung_ringkasan_stok_lot(df_kor.to_dict('records'), df_master_kor)
                        stok_sesudah = hitung_ringkasan_stok_lot(new_baris_data, df_master_kor)
                        semua_kunci_lot = sorted(set(stok_sebelum.keys()) | set(stok_sesudah.keys()))

                        alasan_txt = clean_str(alasan_koreksi_input) or "Penyesuaian Koreksi Rak"
                        jurnal_kuantitas_tercatat = 0

                        for (pn_k, lot_k) in semua_kunci_lot:
                            s_old = stok_sebelum.get((pn_k, lot_k), {'nama': '', 'utuh': 0, 'dus': 0, 'pcs_ec': 0, 'tot_pcs': 0})
                            s_new = stok_sesudah.get((pn_k, lot_k), {'nama': '', 'utuh': 0, 'dus': 0, 'pcs_ec': 0, 'tot_pcs': 0})
                            nama_k = s_new['nama'] or s_old['nama']
                            isi_p_k, pcs_s_k = get_master_konversi(df_master_kor, pn_k)

                            if isi_p_k > 0 and pcs_s_k > 0:
                                delta_pcs = s_new['tot_pcs'] - s_old['tot_pcs']
                                if delta_pcs != 0:
                                    jurnal_kuantitas_tercatat += 1
                                    abs_pcs = abs(delta_pcs)
                                    d_pal = abs_pcs // isi_p_k
                                    rem_k = abs_pcs % isi_p_k
                                    d_dus = rem_k // pcs_s_k
                                    sat_k = get_satuan_kemasan(pn_k)
                                    if delta_pcs > 0:
                                        j_jenis = "MASUK (Koreksi / Retur)"
                                        j_tujuan = f"KOREKSI MASUK: {alasan_txt}"
                                    else:
                                        j_jenis = "KELUAR (Koreksi Stok)"
                                        j_tujuan = f"KOREKSI KELUAR: {alasan_txt}"

                                    j_detail = (
                                        f"Awal: {s_old['utuh']}P {s_old['dus']}{sat_k} ({s_old['tot_pcs']:,} pcs) -> "
                                        f"Akhir: {s_new['utuh']}P {s_new['dus']}{sat_k} ({s_new['tot_pcs']:,} pcs) | "
                                        f"Selisih: {delta_pcs:+,} pcs"
                                    )
                                    catat_log_mutasi(
                                        j_jenis, kk, kb, nama_k, pn_k, lot_k,
                                        d_pal, d_dus, abs_pcs,
                                        keterangan=j_detail,
                                        tujuan_kategori=j_tujuan,
                                        detail_sistem=j_detail
                                    )
                            else:
                                delta_u = s_new['utuh'] - s_old['utuh']
                                delta_d = s_new['dus'] - s_old['dus']
                                delta_e = s_new['pcs_ec'] - s_old['pcs_ec']
                                if delta_u != 0 or delta_d != 0 or delta_e != 0:
                                    jurnal_kuantitas_tercatat += 1
                                    is_plus = (delta_u > 0) or (delta_u == 0 and delta_d > 0) or (delta_u == 0 and delta_d == 0 and delta_e > 0)
                                    j_jenis = "MASUK (Koreksi / Retur)" if is_plus else "KELUAR (Koreksi Stok)"
                                    j_tujuan = f"{'KOREKSI MASUK' if is_plus else 'KOREKSI KELUAR'}: {alasan_txt}"
                                    j_detail = f"Delta: {delta_u:+}P, {delta_d:+} qty, {delta_e:+} pcs"
                                    catat_log_mutasi(
                                        j_jenis, kk, kb, nama_k, pn_k, lot_k,
                                        abs(delta_u), abs(delta_d), abs(delta_e),
                                        keterangan=j_detail,
                                        tujuan_kategori=j_tujuan,
                                        detail_sistem=j_detail
                                    )

                        if jurnal_kuantitas_tercatat == 0:
                            perubahan_atribut = 0
                            min_len = min(len(df_kor), len(new_baris_data))
                            for idx_k in range(min_len):
                                r_lama = df_kor.iloc[idx_k]
                                r_baru = new_baris_data[idx_k]
                                diff_fields = []
                                for col_cek in ['Part Number', 'Lot Number', 'Nama Produk', 'Keterangan', 'Catatan', 'Approval QC', 'Jadwal Kirim', 'Target Penerima']:
                                    v_lama = clean_str(r_lama.get(col_cek))
                                    v_baru = clean_str(r_baru.get(col_cek))
                                    if v_lama != v_baru:
                                        diff_fields.append(f"{col_cek}: '{v_lama}' -> '{v_baru}'")
                                if diff_fields:
                                    perubahan_atribut += 1
                                    det_kor = f"Pallet #{int(r_baru.get('Pallet Ke', 0))} | " + "; ".join(diff_fields)
                                    catat_log_mutasi(
                                        "ERROR CORRECTION (Edit Slot)", kk, kb,
                                        clean_str(r_baru.get('Nama Produk')),
                                        clean_str(r_baru.get('Part Number')),
                                        clean_str(r_baru.get('Lot Number')),
                                        0, 0, 0,
                                        keterangan=det_kor,
                                        tujuan_kategori=f"EC: {alasan_txt}",
                                        detail_sistem=det_kor
                                    )

                            if perubahan_atribut == 0:
                                catat_log_mutasi(
                                    "KOREKSI DATA", kk, kb, "Koreksi Rak", "-", "-", 0, 0, 0,
                                    keterangan=alasan_txt,
                                    tujuan_kategori=f"EC: {alasan_txt}",
                                    detail_sistem=alasan_txt
                                )

                        clear_all_rak_caches()
                        st.session_state.update({
                            'undo_dict': {kk: copy.deepcopy(fresh_kor)},
                            'action_msg': f"Koreksi data di Rak {kk}-{kb} tersimpan ({jurnal_kuantitas_tercatat} jurnal kuantitas dicatat).",
                            'action_status': 'DONE'
                        })
                        st.rerun()
            else:
                st.info("Tidak ada perubahan yang dilakukan pada tabel.")

# ----------------------------------------------------------
# TAB 8: CETAK RACKING / PICKING LIST (1A - 29G)
# ----------------------------------------------------------
with tab8:
    st.header("🖨️ Cetak Kartu Racking / Petunjuk Pengambilan (1A – 29G)")
    st.caption("Sistem hanya membaca kolom dari lokasi yang dipilih dan menyimpannya di cache sesi.")

    mode_cetak = st.radio(
        "Pilih Mode Cetak:",
        ["🎯 Lokasi Spesifik (Pilih / Ketik Bebas)", "📑 Rentang Massal (Per Kolom & Baris)"],
        horizontal=True,
        key="print_mode_radio"
    )

    daftar_semua_lokasi = [f"{b}{k}" for b in range(1, 30) for k in KOLOM_RACKING_AG]
    target_lokasi_list = []

    if mode_cetak.startswith("🎯"):
        sp_c1, sp_c2 = st.columns([2, 1])
        with sp_c1:
            pilih_lokasi_multi = st.multiselect(
                "Pilih Satu atau Beberapa Lokasi Rak (1A – 29G):",
                options=daftar_semua_lokasi,
                placeholder="Contoh: 1A, 2B, 17C...",
                key="print_multi_loc"
            )
        with sp_c2:
            ketik_lokasi_manual = st.text_input(
                "Atau Ketik Cepat (pisah koma/spasi):",
                placeholder="Cth: 1A, 5C, 12G",
                key="print_manual_loc"
            )

        seen_loc = set()
        for loc in pilih_lokasi_multi:
            if loc not in seen_loc:
                target_lokasi_list.append(loc)
                seen_loc.add(loc)

        if clean_str(ketik_lokasi_manual):
            tokens = re.split(r'[\s,;]+', clean_str(ketik_lokasi_manual).upper())
            for tok in tokens:
                if not tok:
                    continue
                m = re.fullmatch(r'(\d{1,2})([A-G])', tok)
                if m and 1 <= int(m.group(1)) <= 29:
                    norm_loc = f"{int(m.group(1))}{m.group(2)}"
                    if norm_loc not in seen_loc:
                        target_lokasi_list.append(norm_loc)
                        seen_loc.add(norm_loc)
                else:
                    st.warning(f"⚠️ Format lokasi '{tok}' diabaikan (gunakan format 1A s/d 29G).")

    else:
        p_col1, p_col2, p_col3, p_col4 = st.columns([2, 1, 1, 2])
        with p_col1:
            pilih_kolom_cetak = st.multiselect(
                "Pilih Kolom Racking",
                options=KOLOM_RACKING_AG,
                default=KOLOM_RACKING_AG,
                key="print_kolom_sel"
            )
        with p_col2:
            baris_awal_cetak = st.number_input("Dari Baris", min_value=1, max_value=29, value=1, key="print_b_start")
        with p_col3:
            baris_akhir_cetak = st.number_input("Sampai Baris", min_value=1, max_value=29, value=29, key="print_b_end")
        with p_col4:
            mode_urutan = st.selectbox(
                "Urutan Cetak",
                ["Per Baris (1A, 1B ... 29G)", "Per Kolom (1A, 2A ... 29G)"],
                key="print_sort_mode"
            )

        if pilih_kolom_cetak and baris_awal_cetak <= baris_akhir_cetak:
            kolom_terurut = [k for k in KOLOM_RACKING_AG if k in pilih_kolom_cetak]
            rentang_baris = range(int(baris_awal_cetak), int(baris_akhir_cetak) + 1)
            if mode_urutan.startswith("Per Baris"):
                target_lokasi_list = [f"{b}{k}" for b in rentang_baris for k in kolom_terurut]
            else:
                target_lokasi_list = [f"{b}{k}" for k in kolom_terurut for b in rentang_baris]
        elif baris_awal_cetak > baris_akhir_cetak:
            st.error("❌ 'Dari Baris' tidak boleh lebih besar dari 'Sampai Baris'.")

    btn_c1, btn_c2 = st.columns([2, 1])
    with btn_c1:
        if st.button("📋 Susun Kartu Racking Sekarang", type="primary", use_container_width=True):
            if not target_lokasi_list:
                st.warning("Pilih atau ketik minimal 1 lokasi rak (contoh: 1A) terlebih dahulu.")
            else:
                kolom_dibutuhkan = sorted({re.sub(r'\d+', '', loc) for loc in target_lokasi_list})
                with st.spinner(f"Mengambil data untuk {len(kolom_dibutuhkan)} kolom terkait..."):
                    for kol in kolom_dibutuhkan:
                        if kol not in st.session_state['print_rak_cache']:
                            st.session_state['print_rak_cache'][kol] = load_kolom_from_supabase(kol)
                    st.session_state['print_ready'] = True
    with btn_c2:
        if st.button("🔄 Tarik Ulang Data Terbaru", use_container_width=True):
            st.session_state['print_rak_cache'] = {}
            st.session_state['print_ready'] = False
            st.toast("Cache cetak dibersihkan. Klik 'Susun Kartu Racking Sekarang' untuk menarik data terbaru.", icon="🔄")

    if st.session_state.get('print_ready') and target_lokasi_list:
        kolom_dibutuhkan = {re.sub(r'\d+', '', loc) for loc in target_lokasi_list}
        if not kolom_dibutuhkan.issubset(st.session_state['print_rak_cache'].keys()):
            st.info("ℹ️ Anda menambahkan lokasi dari kolom baru. Klik **'Susun Kartu Racking Sekarang'** untuk memuat data kolom tersebut.")
        else:
            semua_kartu = []
            for loc in target_lokasi_list:
                m = re.fullmatch(r'(\d{1,2})([A-G])', loc)
                b_num, k_id = int(m.group(1)), m.group(2)
                raw_kol = st.session_state['print_rak_cache'].get(k_id, [])
                baris_recs = get_baris_records(raw_kol, b_num, only_valid=True)
                semua_kartu.extend(build_picking_cards(b_num, k_id, baris_recs))

            st.divider()
            info_c1, info_c2 = st.columns([2, 1])
            with info_c1:
                jml_isi = sum(1 for c in semua_kartu if not c['Is_Empty'])
                jml_kosong = sum(1 for c in semua_kartu if c['Is_Empty'])
                st.success(f"✅ Siap Cetak: **{len(semua_kartu)} Kartu** dari **{len(target_lokasi_list)} Lokasi** ({jml_isi} Terisi, {jml_kosong} Kosong).")
            with info_c2:
                df_print_csv = pd.DataFrame(semua_kartu).drop(columns=['Is_Empty'])
                csv_print_buf = io.StringIO()
                df_print_csv.to_csv(csv_print_buf, index=False, sep=";")
                st.download_button(
                    label="⬇️ Unduh Data Picking List (CSV)",
                    data=csv_print_buf.getvalue(),
                    file_name=f"Picking_List_Racking_{get_waktu_jkt().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )

            ringkasan_judul = ", ".join(target_lokasi_list[:6]) + (f" (+{len(target_lokasi_list)-6} lainnya)" if len(target_lokasi_list) > 6 else "")
            judul_dok = f"Petunjuk Pengambilan Racking: {ringkasan_judul}"
            waktu_skrg = get_waktu_jkt().strftime("%d-%m-%Y %H:%M WIB")
            html_cetak = render_print_html(semua_kartu, judul_dok, waktu_skrg)
            components.html(html_cetak, height=650, scrolling=True)

# ----------------------------------------------------------
# TAB 9: PLOTTING JADWAL OUTBOUND (PPIC -> RQC -> LTOutb)
# ----------------------------------------------------------
with tab9:
    st.header("📅 Plotting Jadwal Outbound (PPIC)")
    st.caption(
        "Pilih kategori & item produk serta jumlah kebutuhan (dalam Pcs otomatis dibulatkan ke Palet, atau langsung Palet). "
        "Sistem mencarikan Lot & lokasi rak secara Strict FIFO (Tanggal & Shift tertua) serta menyertakan pecahan tertua."
    )

    df_master_plot = load_master_produk()
    if df_master_plot.empty:
        st.error("❌ Master_Produk.csv tidak ditemukan atau kosong.")
    else:
        df_master_plot = df_master_plot.dropna(subset=['Nama Barang', 'Part Number']).copy()
        df_master_plot['Kategori'] = df_master_plot['Nama Barang'].apply(set_kategori_produk)

        mode_input_plot = st.radio(
            "Satuan Input Kebutuhan Delivery:",
            [
                "🔢 Input dalam Pcs (Otomatis Konversi & Bulatkan ke Palet)",
                "📦 Input Manual Palet & Pecahan"
            ],
            horizontal=True,
            key="plot_mode_satuan"
        )

        c_pl1, c_pl2 = st.columns([1.2, 2.3])
        with c_pl1:
            urutan_kat = ["PREFORM", "CUP NATURAL", "CUP COLOR", "CUP PRINTING", "CAP", "LAINNYA"]
            kat_tersedia = [k for k in urutan_kat if k in set(df_master_plot['Kategori'].unique())]
            pilih_kat_plot = st.selectbox("1. Kategori Produk", options=kat_tersedia, key="plot_kat_sel")

        df_sub_kat = df_master_plot[df_master_plot['Kategori'] == pilih_kat_plot].copy()
        daftar_item_plot = sorted(df_sub_kat['Nama Barang'].apply(clean_str).unique().tolist())

        with c_pl2:
            pilih_item_plot = st.selectbox("2. Item Produk", options=daftar_item_plot, key="plot_item_sel")

        row_m_plot = df_sub_kat[df_sub_kat['Nama Barang'].apply(clean_str) == pilih_item_plot].iloc[0]
        part_plot_clean = clean_str(row_m_plot['Part Number']).upper()
        satuan_plot = get_satuan_kemasan(part_plot_clean)
        isi_pallet_master, pcs_satuan_master = get_master_konversi(df_master_plot, part_plot_clean)

        qty_plot_palet = 0
        qty_plot_dus = 0
        qty_plot_pcs = 0
        konversi_valid = True

        if mode_input_plot.startswith("🔢"):
            c_pcs1, c_pcs2 = st.columns([1.4, 2.1])
            with c_pcs1:
                qty_plot_pcs = st.number_input(
                    "3. Kebutuhan Delivery (Pcs)",
                    min_value=1,
                    value=max(isi_pallet_master, 1000),
                    step=500,
                    key="plot_qty_pcs"
                )
            with c_pcs2:
                if isi_pallet_master > 0:
                    palet_desimal = qty_plot_pcs / isi_pallet_master
                    qty_plot_palet = int(math.ceil(palet_desimal))
                    total_pcs_bulat = qty_plot_palet * isi_pallet_master
                    selisih_pcs = total_pcs_bulat - qty_plot_pcs
                    st.info(
                        f"📐 **Standar Master (`{part_plot_clean}`):** `1 Palet = {isi_pallet_master:,} pcs`"
                        + (f" (`1 {satuan_plot} = {pcs_satuan_master:,} pcs`)" if pcs_satuan_master > 0 else "")
                        + f"\n\n🔄 **{qty_plot_pcs:,} pcs** = `{palet_desimal:.2f} palet` ➔ Dibulatkan ke atas menjadi **{qty_plot_palet} Palet** "
                        f"(`{total_pcs_bulat:,} pcs`, cadangan pembulatan `+{selisih_pcs:,} pcs`)"
                    )
                else:
                    konversi_valid = False
                    st.error(
                        f"❌ Data `Isi 1 Pallet (pcs)` untuk `{part_plot_clean}` belum terisi di `Master_Produk.csv`. "
                        f"Gunakan mode input Palet manual."
                    )
        else:
            c_man1, c_man2 = st.columns(2)
            with c_man1:
                qty_plot_palet = st.number_input("3. Kebutuhan Palet", min_value=0, value=1, step=1, key="plot_qty_p")
            with c_man2:
                qty_plot_dus = st.number_input(f"4. Pecahan ({satuan_plot})", min_value=0, value=0, step=1, key="plot_qty_d")

        c_opt_pl1, c_opt_pl2 = st.columns(2)
        with c_opt_pl1:
            pakai_buffer_1 = st.checkbox(
                "🛡️ Tambahkan Buffer Antisipasi +1 Palet",
                value=True,
                help="Direkomendasikan agar operator forklift tidak kekurangan stok di lorong.",
                key="plot_chk_buffer"
            )
        with c_opt_pl2:
            ikut_pecahan_fifo = st.checkbox(
                "🧹 Otomatis ikut sertakan slot pecahan tertua (FIFO)",
                value=True,
                help="Menyertakan sisa pecahan dus/sak tertua agar tidak tertinggal menjadi stok mati di rak.",
                key="plot_chk_pecahan_fifo"
            )

        if st.button("🔍 Cari Lot & Lokasi Racking (Strict FIFO)", type="primary", use_container_width=True, disabled=not konversi_valid):
            with st.spinner("Memindai seluruh rak A–G untuk mencari stok bebas (Strict FIFO)..."):
                all_rak_records = load_all_rak_from_supabase()
                st.session_state['plot_all_records_cache'] = all_rak_records
                st.session_state['plot_target_part'] = part_plot_clean
                st.session_state['plot_target_item'] = pilih_item_plot
                st.session_state['plot_search_ready'] = True

        if st.session_state.get('plot_search_ready') and st.session_state.get('plot_target_part') == part_plot_clean and konversi_valid:
            all_rak_records = st.session_state.get('plot_all_records_cache', [])
            df_kandidat = scan_stok_bebas_untuk_plotting(all_rak_records, part_plot_clean, strict_fifo_shift=True)

            if df_kandidat.empty:
                st.warning(f"⚠️ Tidak ditemukan stok bebas (belum ter-plot) untuk **{pilih_item_plot}** (`{part_plot_clean}`) di rak A–G.")
            else:
                target_p_total = int(qty_plot_palet) + (1 if pakai_buffer_1 and qty_plot_palet > 0 else 0)
                target_d_total = int(qty_plot_dus)

                auto_pecahan_map = {}
                total_pcs_dari_pecahan = 0
                if ikut_pecahan_fifo:
                    for idx_r, r_k in df_kandidat.iterrows():
                        avail_d = int(r_k['Stok Dus/Sak'])
                        if avail_d > 0:
                            auto_pecahan_map[idx_r] = avail_d
                            if pcs_satuan_master > 0:
                                total_pcs_dari_pecahan += avail_d * pcs_satuan_master

                if mode_input_plot.startswith("🔢") and isi_pallet_master > 0 and ikut_pecahan_fifo and total_pcs_dari_pecahan > 0:
                    sisa_pcs_setelah_pecahan = max(0, int(qty_plot_pcs) - total_pcs_dari_pecahan)
                    palet_utuh_efektif = int(math.ceil(sisa_pcs_setelah_pecahan / isi_pallet_master)) if sisa_pcs_setelah_pecahan > 0 else 0
                    target_p_total = palet_utuh_efektif + (1 if pakai_buffer_1 and (palet_utuh_efektif > 0 or total_pcs_dari_pecahan > 0) else 0)

                sisa_p_need = target_p_total
                sisa_d_need = target_d_total
                plot_p_list, plot_d_list = [], []

                for idx_r, r_k in df_kandidat.iterrows():
                    avail_p = int(r_k['Stok Utuh'])
                    avail_d = int(r_k['Stok Dus/Sak'])

                    take_p = min(avail_p, sisa_p_need)
                    sisa_p_need -= take_p

                    if idx_r in auto_pecahan_map:
                        take_d = auto_pecahan_map[idx_r]
                    else:
                        take_d = min(avail_d, sisa_d_need)
                        sisa_d_need -= take_d

                    plot_p_list.append(take_p)
                    plot_d_list.append(take_d)

                df_kandidat['Plot Utuh'] = plot_p_list
                df_kandidat[f'Plot {satuan_plot.title()}'] = plot_d_list
                df_kandidat = evaluasi_akses_dinamis_plot(df_kandidat, all_rak_records, part_plot_clean)

                st.divider()
                st.markdown(f"**5. Hasil Kandidat Lot & Lokasi Rak untuk `{pilih_item_plot}` (`{part_plot_clean}`)**")

                tot_plot_p_prev = int(df_kandidat['Plot Utuh'].sum())
                tot_plot_d_prev = int(df_kandidat[f'Plot {satuan_plot.title()}'].sum())

                info_target_str = f"Rencana Ter-plot: **{tot_plot_p_prev} Palet Utuh**"
                if tot_plot_d_prev > 0:
                    info_target_str += f" + **{tot_plot_d_prev} {satuan_plot}**"
                if mode_input_plot.startswith("🔢") and isi_pallet_master > 0:
                    est_total_pcs = (tot_plot_p_prev * isi_pallet_master) + (tot_plot_d_prev * pcs_satuan_master)
                    info_target_str += f" *(Kebutuhan: {qty_plot_pcs:,} pcs ➔ Estimasi Muat: **{est_total_pcs:,} pcs**)*"
                elif isi_pallet_master > 0:
                    est_total_pcs = (tot_plot_p_prev * isi_pallet_master) + (tot_plot_d_prev * pcs_satuan_master)
                    info_target_str += f" *(Setara **{est_total_pcs:,} pcs**)*"

                st.caption(info_target_str + ". *Silakan sesuaikan kolom `PLOT PALET` / `PLOT DUS/SAK` jika perlu.*")

                if sisa_p_need > 0 or sisa_d_need > 0:
                    st.warning(
                        f"⚠️ Stok bebas di gudang kurang **{sisa_p_need} Palet** "
                        f"{f'dan **{sisa_d_need} {satuan_plot}**' if sisa_d_need > 0 else ''} dari target plotting!"
                    )

                cols_tampil = [
                    'Lokasi', 'Lot Number', 'Akses Pintu', 'Stok Utuh', 'Stok Dus/Sak',
                    'Plot Utuh', f'Plot {satuan_plot.title()}', 'Status QC', 'Catatan Rak'
                ]
                edited_plot_df = st.data_editor(
                    df_kandidat[cols_tampil],
                    column_config={
                        'Lokasi': st.column_config.TextColumn("Lokasi Rak", disabled=True),
                        'Lot Number': st.column_config.TextColumn("No. Lot (FIFO)", disabled=True),
                        'Akses Pintu': st.column_config.TextColumn("Akses Pintu (Dinamis)", disabled=True),
                        'Stok Utuh': st.column_config.NumberColumn("Stok Palet", disabled=True),
                        'Stok Dus/Sak': st.column_config.NumberColumn(f"Stok {satuan_plot.title()}", disabled=True),
                        'Plot Utuh': st.column_config.NumberColumn("🎯 PLOT PALET", min_value=0, required=True),
                        f'Plot {satuan_plot.title()}': st.column_config.NumberColumn(f"🎯 PLOT {satuan_plot.upper()}", min_value=0, required=True),
                        'Status QC': st.column_config.TextColumn("Status QC", disabled=True),
                        'Catatan Rak': st.column_config.TextColumn("Catatan Rak", disabled=True),
                    },
                    hide_index=True,
                    use_container_width=True,
                    key=f"editor_plotting_{part_plot_clean}_{target_p_total}_{target_d_total}_{ikut_pecahan_fifo}"
                )

                st.markdown("**6. Tetapkan Jadwal Kirim & Target Penerima (Customer)**")
                c_dst1, c_dst2 = st.columns([1.2, 2.3])
                with c_dst1:
                    tgl_kirim_input = st.date_input(
                        "Tanggal Jadwal Kirim:",
                        value=get_waktu_jkt().date() + timedelta(days=1),
                        format="DD/MM/YYYY",
                        key="plot_tgl_kirim"
                    )
                with c_dst2:
                    target_penerima_input = st.text_input(
                        "Target Penerima (Nama Customer):",
                        placeholder="Cth: PT Tirta Fresindo Jaya (Pasuruan)",
                        key="plot_nama_customer"
                    )

                if st.button("✅ Simpan Plotting ke Database", type="primary", use_container_width=True):
                    cust_clean = clean_str(target_penerima_input)
                    tgl_kirim_str = tgl_kirim_input.strftime("%d-%m-%Y")

                    if not cust_clean:
                        st.error("⚠️ Nama Target Penerima (Customer) wajib diisi!")
                    else:
                        alokasi_valid = []
                        err_plot = ""
                        for _, r_ed in edited_plot_df.iterrows():
                            p_ambil = int(r_ed['Plot Utuh'])
                            d_ambil = int(r_ed[f'Plot {satuan_plot.title()}'])
                            if p_ambil == 0 and d_ambil == 0:
                                continue
                            if p_ambil > int(r_ed['Stok Utuh']) or d_ambil > int(r_ed['Stok Dus/Sak']):
                                err_plot = f"❌ Jumlah plot pada Rak {r_ed['Lokasi']} (Lot {r_ed['Lot Number']}) melebihi stok bebas tersedia!"
                                break
                            alokasi_valid.append({
                                'Lokasi': r_ed['Lokasi'],
                                'Kolom': r_ed['Lokasi'].split("-")[0],
                                'Baris': int(r_ed['Lokasi'].split("-")[1]),
                                'Lot': clean_str(r_ed['Lot Number']),
                                'Akses': clean_str(r_ed['Akses Pintu']),
                                'Plot_P': p_ambil,
                                'Plot_D': d_ambil
                            })

                        if err_plot:
                            st.error(err_plot)
                        elif not alokasi_valid:
                            st.warning("⚠️ Tidak ada palet/pecahan yang di-plot (semua bernilai 0).")
                        else:
                            with st.spinner("Menyematkan Jadwal Kirim & Target Penerima ke slot rak..."):
                                kolom_terlibat = sorted({x['Kolom'] for x in alokasi_valid})
                                fresh_map = {}
                                work_map = {}
                                for k in kolom_terlibat:
                                    arr = load_kolom_from_supabase(k)
                                    fresh_map[k] = copy.deepcopy(arr)
                                    work_map[k] = copy.deepcopy(arr)

                                ok_pl = True
                                err_tx_pl = ""
                                for al in alokasi_valid:
                                    k_id, b_num, lot_id = al['Kolom'], al['Baris'], al['Lot']
                                    need_p, need_d = al['Plot_P'], al['Plot_D']
                                    dari_belakang = "BELAKANG" in al['Akses'].upper()

                                    baris_items = [
                                        d for d in work_map[k_id]
                                        if is_same_baris(d, b_num)
                                        and clean_str(d.get('Part Number')).upper() == part_plot_clean
                                        and clean_str(d.get('Lot Number')) == lot_id
                                        and clean_str(d.get('Jadwal Kirim')) == ""
                                        and clean_str(d.get('Target Penerima')) == ""
                                    ]
                                    baris_items.sort(
                                        key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)),
                                        reverse=dari_belakang
                                    )

                                    tagged_p = 0
                                    slot_pecahan_butuh = 1 if need_d > 0 else 0
                                    tagged_slot_pec = 0

                                    for slot_d in baris_items:
                                        ket_s = clean_str(slot_d.get('Keterangan')).lower()
                                        is_pec = ('pecahan' in ket_s or 'eceran' in ket_s)
                                        if not is_pec and tagged_p < need_p:
                                            slot_d['Jadwal Kirim'] = tgl_kirim_str
                                            slot_d['Target Penerima'] = cust_clean
                                            tagged_p += 1
                                        elif is_pec and tagged_slot_pec < slot_pecahan_butuh:
                                            slot_d['Jadwal Kirim'] = tgl_kirim_str
                                            slot_d['Target Penerima'] = cust_clean
                                            tagged_slot_pec += 1

                                    if tagged_p < need_p or tagged_slot_pec < slot_pecahan_butuh:
                                        ok_pl = False
                                        err_tx_pl = f"❌ Stok bebas di Rak {k_id}-{b_num} (Lot {lot_id}) berubah sebelum disimpan. Silakan klik 'Cari Lot & Lokasi Racking' ulang."
                                        break

                                if not ok_pl:
                                    st.error(err_tx_pl)
                                else:
                                    for k_u in kolom_terlibat:
                                        save_kolom_to_supabase(k_u, work_map[k_u])

                                    tot_pl_p = sum(x['Plot_P'] for x in alokasi_valid)
                                    tot_pl_d = sum(x['Plot_D'] for x in alokasi_valid)
                                    ringkas_lok = ", ".join(
                                        f"{x['Lokasi']} ({x['Lot']}: {x['Plot_P']}P"
                                        + (f"+{x['Plot_D']}{satuan_plot}" if x['Plot_D'] > 0 else "")
                                        + ")"
                                        for x in alokasi_valid
                                    )
                                    req_pcs_note = f" | Req: {qty_plot_pcs:,} pcs" if mode_input_plot.startswith("🔢") else ""
                                    det_pl = f"Jadwal: {tgl_kirim_str} | Customer: {cust_clean}{req_pcs_note} | Lokasi: {ringkas_lok}"

                                    catat_log_mutasi(
                                        "PLOTTING OUTBOUND (PPIC)", ",".join(kolom_terlibat), "PLOT",
                                        pilih_item_plot, part_plot_clean, "-",
                                        tot_pl_p, tot_pl_d, 0,
                                        keterangan=det_pl,
                                        tujuan_kategori=f"PLOT: {cust_clean} ({tgl_kirim_str})",
                                        detail_sistem=det_pl
                                    )
                                    clear_all_rak_caches()
                                    st.session_state['plot_search_ready'] = False
                                    st.session_state.update({
                                        'undo_dict': copy.deepcopy(fresh_map),
                                        'action_msg': f"Plotting {tot_pl_p}P {tot_pl_d} {satuan_plot} '{pilih_item_plot}' untuk {cust_clean} (Jadwal {tgl_kirim_str}) tersimpan!",
                                        'action_status': 'DONE'
                                    })
                                    st.rerun()

    # --- MONITOR & BATALKAN PLOTTING AKTIF ---
    st.divider()
    st.subheader("📋 Daftar Plotting Aktif di Gudang")
    if st.button("🔄 Muat Daftar Plotting Aktif", key="btn_load_active_plots"):
        all_act = load_all_rak_from_supabase()
        active_rows = []
        for item_ac in all_act:
            jk = clean_str(item_ac.get('Jadwal Kirim'))
            tp = clean_str(item_ac.get('Target Penerima'))
            if jk or tp:
                ket_ac = clean_str(item_ac.get('Keterangan')).lower()
                is_pec_ac = ('pecahan' in ket_ac or 'eceran' in ket_ac)
                pec_val_ac = parse_keterangan(ket_ac)[0] if is_pec_ac else 0
                active_rows.append({
                    'Jadwal Kirim': jk,
                    'Target Penerima': tp,
                    'Lokasi': f"{clean_str(item_ac.get('Kolom'))}-{int(float(item_ac.get('Baris', 0) or 0))}",
                    'Part Number': clean_str(item_ac.get('Part Number')),
                    'Nama Produk': clean_str(item_ac.get('Nama Produk')),
                    'Lot Number': clean_str(item_ac.get('Lot Number')),
                    'Approval QC': clean_str(item_ac.get('Approval QC')) or "Menunggu QC",
                    'Pallet_Utuh': 0 if is_pec_ac else 1,
                    'Qty_Pecahan': pec_val_ac
                })
        st.session_state['active_plots_list'] = active_rows

    if st.session_state.get('active_plots_list') is not None:
        act_list = st.session_state['active_plots_list']
        if not act_list:
            st.info("Saat ini tidak ada palet yang sedang dalam status Plotting Outbound.")
        else:
            df_act = pd.DataFrame(act_list)
            df_act_grp = df_act.groupby(
                ['Jadwal Kirim', 'Target Penerima', 'Lokasi', 'Part Number', 'Nama Produk', 'Lot Number', 'Approval QC'],
                as_index=False
            ).agg(
                Pallet_Utuh=('Pallet_Utuh', 'sum'),
                Qty_Pecahan=('Qty_Pecahan', 'sum')
            )
            st.dataframe(df_act_grp, hide_index=True, use_container_width=True)

            with st.expander("🗑️ Batalkan Plotting Aktif (Lepas Kunci Jadwal Kirim)"):
                df_act_grp['Label_Batal'] = df_act_grp.apply(
                    lambda r: f"[{r['Jadwal Kirim']}] {r['Target Penerima']} | Rak {r['Lokasi']} | {r['Nama Produk']} (Lot {r['Lot Number']}) — {int(r['Pallet_Utuh'])}P {int(r['Qty_Pecahan'])} Pecahan",
                    axis=1
                )
                pilih_batal_list = st.multiselect(
                    "Pilih baris plotting yang ingin dibatalkan (dikembalikan ke stok bebas):",
                    options=df_act_grp['Label_Batal'].tolist(),
                    key="sel_batal_plotting"
                )
                if st.button("🔓 Batalkan Plotting Terpilih", type="secondary"):
                    if not pilih_batal_list:
                        st.warning("Pilih minimal 1 baris plotting yang akan dibatalkan.")
                    else:
                        rows_to_cancel = df_act_grp[df_act_grp['Label_Batal'].isin(pilih_batal_list)]
                        kol_batal = sorted({r['Lokasi'].split("-")[0] for _, r in rows_to_cancel.iterrows()})
                        fresh_b, work_b = {}, {}
                        for k in kol_batal:
                            arr = load_kolom_from_supabase(k)
                            fresh_b[k] = copy.deepcopy(arr)
                            work_b[k] = copy.deepcopy(arr)

                        for _, rc in rows_to_cancel.iterrows():
                            k_b, b_b = rc['Lokasi'].split("-")[0], int(rc['Lokasi'].split("-")[1])
                            for slot in work_b[k_b]:
                                if (is_same_baris(slot, b_b)
                                        and clean_str(slot.get('Part Number')).upper() == clean_str(rc['Part Number']).upper()
                                        and clean_str(slot.get('Lot Number')) == clean_str(rc['Lot Number'])
                                        and clean_str(slot.get('Jadwal Kirim')) == clean_str(rc['Jadwal Kirim'])
                                        and clean_str(slot.get('Target Penerima')) == clean_str(rc['Target Penerima'])):
                                    slot['Jadwal Kirim'] = ""
                                    slot['Target Penerima'] = ""

                        for k_u in kol_batal:
                            save_kolom_to_supabase(k_u, work_b[k_u])

                        catat_log_mutasi(
                            "BATAL PLOTTING (PPIC)", ",".join(kol_batal), "PLOT",
                            "Pembatalan Plotting Outbound", "-", "-", 0, 0, 0,
                            keterangan=f"Membatalkan {len(rows_to_cancel)} grup plotting",
                            tujuan_kategori="BATAL PLOTTING",
                            detail_sistem="; ".join(pilih_batal_list)
                        )
                        clear_all_rak_caches()
                        st.session_state.update({
                            'undo_dict': copy.deepcopy(fresh_b),
                            'action_msg': f"Berhasil membatalkan {len(rows_to_cancel)} grup plotting (kembali ke stok bebas).",
                            'action_status': 'DONE'
                        })
                        st.rerun()
