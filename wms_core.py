import copy
import hmac
import html
import json
import math
import re
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import streamlit as st
from supabase import Client, create_client


# ==========================================================
# 1. KONSTANTA, ZONA KHUSUS, FILTER WIP & KONEKSI SUPABASE
# ==========================================================
NAMA_DATABASE = "uji_coba_rak"
DB_RAK = NAMA_DATABASE  # Alias kompatibilitas untuk RQC.py
DB_LOG_MUTASI = "log_mutasi_rak"
DB_UNDO_STATE = "wms_undo_state"
DB_DRAFT_INBOUND = "wms_inbound_drafts"
DB_DRAFT_OUTBOUND = "wms_outbound_drafts"
DB_QC_APPROVAL = "wms_qc_approval"

WIB_TZ = timezone(timedelta(hours=7))
KOLOM_RAK_UTAMA = ["A", "B", "C", "D", "E", "F", "G"]
BARIS_RAK_UTAMA = list(range(1, 29))

# Batas maksimal palet di sisi ujung pintu masuk untuk lorong >= 3 item
MAX_TAIL_PALLETS_MULTI_ITEM = 10

# Item khusus JDO Diamond (Utama: F1-16, Cadangan: G17-28)
PART_KHUSUS_TOP_TIER = {"MJSCN3802703D", "MJSCN3802901D"}

# Item Cup Natural khusus yang boleh diambil untuk Suplai WIP
PART_WIP_CUP_NATURAL = {"MJSCN3902902G", "MJSCN3903101D", "MJSCN3204303D", "MJSCN3205004D"}


class _DummySupabaseShim:
    """Kompatibilitas pemanggilan `db.collection(...).document(...)` & `db.transaction()`."""

    class _DummyDocRef:
        def __init__(self, table_name: str, doc_id: str):
            self.table_name = table_name
            self.id = doc_id

    class _DummyCollection:
        def __init__(self, table_name: str):
            self.table_name = table_name

        def document(self, doc_id: str):
            return _DummySupabaseShim._DummyDocRef(self.table_name, doc_id)

    def collection(self, table_name: str):
        return self._DummyCollection(table_name)

    def transaction(self):
        return None


@st.cache_resource
def init_supabase() -> Client:
    url = str(st.secrets["supabase"]["url"]).strip().rstrip("/")
    key = str(st.secrets["supabase"]["key"]).strip()
    return create_client(url, key)


supabase = init_supabase()
db = _DummySupabaseShim()


# ==========================================================
# 2. UTILITAS WAKTU, STRING, PARSING & TELEGRAM
# ==========================================================
def get_waktu_jkt() -> datetime:
    """Mengembalikan waktu saat ini dalam zona WIB (timezone-aware UTC+7)."""
    return datetime.now(WIB_TZ)


def _parse_dt_wib(dt_val):
    """Mengonversi string/datetime apa pun menjadi timezone-aware WIB secara aman."""
    if not dt_val:
        return None
    if isinstance(dt_val, datetime):
        return dt_val.astimezone(WIB_TZ) if dt_val.tzinfo else dt_val.replace(tzinfo=WIB_TZ)
    s = str(dt_val).strip()
    if not s:
        return None
    try:
        dt_parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt_parsed.astimezone(WIB_TZ) if dt_parsed.tzinfo else dt_parsed.replace(tzinfo=WIB_TZ)
    except Exception:
        pass
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=WIB_TZ)
    except Exception:
        return None


def clean_str(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    if s.lower() in {"nan", "none", "<na>", "null"}:
        return ""
    s = re.sub(r"(\s*\|\s*nan\b|\bnan\s*\|\s*)", "", s, flags=re.IGNORECASE)
    return s.strip(" |").strip()


def escape_html(text) -> str:
    return html.escape(str(text), quote=False)


def _safe_int_val(val, default: int = 0) -> int:
    if val is None or isinstance(val, bool):
        return int(default)
    num = pd.to_numeric(val, errors="coerce")
    return int(default) if pd.isna(num) else int(round(float(num)))


def parse_keterangan(ket_str):
    ket_str = str(ket_str).lower()
    m_pec = re.search(r"pecahan:\s*(\d+)", ket_str)
    m_ec = re.search(r"eceran:\s*(\d+)", ket_str)
    return (int(m_pec.group(1)) if m_pec else 0, int(m_ec.group(1)) if m_ec else 0)


def parse_fifo_key(lot_str) -> str:
    """Kunci pengurutan kronologis FIFO murni: YYMMDD + Shift (1/2/3)."""
    s = str(lot_str).strip()
    m_shift = re.search(r"([123])(\d{2})(\d{2})(\d{2})$", s)
    if m_shift:
        sh, dd, mm, yy = m_shift.groups()
        return f"{yy}{mm}{dd}{sh}"
    m_date = re.search(r"(\d{2})(\d{2})(\d{2})$", s)
    if m_date:
        dd, mm, yy = m_date.groups()
        return f"{yy}{mm}{dd}0"
    return "9999999"


def parse_lifo_rank(lot_str) -> int:
    """Skor numerik terbaru (Inbound): YYMMDD * 10 + Shift (1/2/3)."""
    s = str(lot_str).strip()
    m_shift = re.search(r"([123])(\d{2})(\d{2})(\d{2})$", s)
    if m_shift:
        sh, dd, mm, yy = m_shift.groups()
        return int(f"{yy}{mm}{dd}{sh}")
    m_date = re.search(r"(\d{2})(\d{2})(\d{2})$", s)
    if m_date:
        dd, mm, yy = m_date.groups()
        return int(f"{yy}{mm}{dd}0")
    return 0


def parse_tgl_kirim_sort(tgl_str) -> datetime:
    try:
        return datetime.strptime(clean_str(tgl_str), "%d-%m-%Y")
    except Exception:
        return datetime(2099, 12, 31)


def get_satuan_kemasan(part_num) -> str:
    p = str(part_num).strip().upper()
    if p.endswith("C"):
        return "Sak"
    if p.endswith("A"):
        return "Nictainer"
    return "Dus"


def ekstrak_gramasi(nama_produk) -> str:
    match = re.search(r"(\d+[.,]\d+)\s*(?:gr|GR)", str(nama_produk), re.IGNORECASE)
    if match:
        return f"{float(match.group(1).replace(',', '.')):.2f} gr"
    return "Lainnya"


def set_kategori(nama) -> str:
    nama_str = str(nama).strip().upper()
    for prefix in ("PREFORM", "CUP NATURAL", "CUP PRINTING", "CUP COLOR", "CAP"):
        if nama_str.startswith(prefix):
            return prefix
    return "LAINNYA"


def format_nama_warna_bold(nama_barang) -> str:
    parts = str(nama_barang).rsplit(",", 1)
    if len(parts) == 2:
        return f"{parts[0]}, **{parts[1].strip()}**"
    return str(nama_barang)


def kirim_notif_telegram(pesan_html: str) -> bool:
    """Mengirim notifikasi HTML ke satu atau banyak chat_id Telegram (mendukung thread/topic)."""
    try:
        tele_cfg = st.secrets.get("telegram")
        if not tele_cfg:
            st.toast("⚠️ Secrets [telegram] belum dikonfigurasi pada aplikasi ini.", icon="⚠️")
            return False

        token = str(tele_cfg.get("bot_token", "")).strip()
        raw_chat_ids = tele_cfg.get("chat_id", "")
        if isinstance(raw_chat_ids, (list, tuple)):
            daftar_id = [str(cid).strip() for cid in raw_chat_ids if str(cid).strip()]
        else:
            daftar_id = [cid.strip() for cid in str(raw_chat_ids).split(",") if cid.strip()]

        if not token or not daftar_id:
            st.toast("⚠️ bot_token / chat_id Telegram kosong di Secrets.", icon="⚠️")
            return False

        thread_id = str(tele_cfg.get("message_thread_id", "")).strip()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        sukses_semua = True

        for cid in daftar_id:
            payload = {"chat_id": cid, "text": pesan_html, "parse_mode": "HTML"}
            if thread_id.isdigit():
                payload["message_thread_id"] = int(thread_id)
            resp = requests.post(url, json=payload, timeout=8)
            if not resp.ok:
                sukses_semua = False
                st.toast(f"⚠️ Telegram gagal ({resp.status_code}) [ID: {cid}]: {resp.text[:90]}", icon="⚠️")
        return sukses_semua
    except Exception as e:
        st.toast(f"⚠️ Gagal kirim Telegram: {e}", icon="⚠️")
        return False


# ==========================================================
# 3. ATURAN ZONA RAK & HELPER KAPASITAS
# ==========================================================
def is_zona_jdo(kolom, baris_int: int) -> bool:
    k = str(kolom).strip().upper()
    return (k == "F" and 1 <= baris_int <= 16) or (k == "G" and 17 <= baris_int <= 28)


def is_rak_valid_untuk_produk(kolom, baris_int: int, part_num) -> bool:
    p = str(part_num).strip().upper()
    if p in PART_KHUSUS_TOP_TIER:
        return is_zona_jdo(kolom, baris_int)
    return True


def hitung_zone_penalty(kolom, baris_int: int, target_item, part_num) -> int:
    k = str(kolom).strip().upper()
    p = str(part_num).strip().upper()
    nama_upper = str(target_item).strip().upper()

    if p in PART_KHUSUS_TOP_TIER:
        if k == "F" and 1 <= baris_int <= 16:
            return 0
        if k == "G" and 17 <= baris_int <= 28:
            return 1
        return 99

    in_jdo_zone = is_zona_jdo(k, baris_int)
    if nama_upper.startswith("PREFORM"):
        if not in_jdo_zone and 1 <= baris_int <= 16:
            return 0
        return 1 if (not in_jdo_zone and 17 <= baris_int <= 28) else 2

    if nama_upper.startswith("CUP"):
        if not in_jdo_zone and 17 <= baris_int <= 28:
            return 0
        return 1 if (not in_jdo_zone and 1 <= baris_int <= 16) else 2

    return 1 if in_jdo_zone else 0


def hitung_blok_ujung(pallets_sorted: list, target_item_clean: str, sisi_masuk: str) -> int:
    seq = pallets_sorted if sisi_masuk == "DEPAN" else list(reversed(pallets_sorted))
    count = 0
    for p in seq:
        if str(p.get("Nama Produk", "")).strip() == target_item_clean:
            count += 1
        else:
            break
    return count


def _kapasitas_baris(baris_int: int):
    """Mengembalikan (max_sac, max_dus) sesuai panjang lorong baris."""
    return (31, 32) if baris_int <= 16 else (44, 46)


# ==========================================================
# 4. ADAPTER SUPABASE (TABEL RACKING & JSON STORE)
# ==========================================================
def _row_db_to_slot_dict(r: dict) -> dict:
    bar_num = pd.to_numeric(r.get("baris", 0), errors="coerce")
    plt_num = pd.to_numeric(r.get("pallet_ke", 0), errors="coerce")
    return {
        "id": r.get("id"),
        "Kolom": clean_str(r.get("kolom")).upper(),
        "Baris": float(0 if pd.isna(bar_num) else int(bar_num)),
        "Pallet Ke": float(0 if pd.isna(plt_num) else int(plt_num)),
        "Part Number": clean_str(r.get("part_number")).upper(),
        "Nama Produk": clean_str(r.get("nama_produk")),
        "Lot Number": clean_str(r.get("lot_number")),
        "Keterangan": clean_str(r.get("keterangan")) or "Utuh",
        "Catatan": clean_str(r.get("catatan")),
        "Approval QC": clean_str(r.get("approval_qc")),
        "Jadwal Kirim": clean_str(r.get("jadwal_kirim")),
        "Target Penerima": clean_str(r.get("target_penerima")),
        "Status": clean_str(r.get("status")) or "Terisi",
    }


def _resolve_kolom_id(doc_ref_or_kolom) -> str:
    if isinstance(doc_ref_or_kolom, str):
        return doc_ref_or_kolom.replace("Kolom_", "").strip().upper()
    if hasattr(doc_ref_or_kolom, "id"):
        return str(doc_ref_or_kolom.id).replace("Kolom_", "").strip().upper()
    return clean_str(doc_ref_or_kolom).upper()


def _fetch_kolom_rows(kolom_id: str) -> list:
    kol = clean_str(kolom_id).upper()
    semua = []
    step = 1000
    start = 0
    while True:
        res = (
            supabase.table(NAMA_DATABASE)
            .select("*")
            .eq("kolom", kol)
            .order("baris", desc=False)
            .order("pallet_ke", desc=False)
            .range(start, start + step - 1)
            .execute()
        )
        batch = res.data or []
        semua.extend(_row_db_to_slot_dict(r) for r in batch)
        if len(batch) < step:
            break
        start += step
    return semua


def _sync_kolom_to_supabase(kolom_id: str, slot_dicts: list):
    """Menyinkronkan seluruh slot 1 kolom (upsert baris aktif + hapus sisa slot kosong)."""
    kol = clean_str(kolom_id).upper()
    payload_map = {}
    now_iso = datetime.now(timezone.utc).isoformat()

    for item in slot_dicts:
        pn = clean_str(item.get("Part Number")).upper()
        bar = int(float(item.get("Baris", 0) or 0))
        plt_ke = int(float(item.get("Pallet Ke", 0) or 0))
        if not pn or bar <= 0 or plt_ke <= 0:
            continue
        payload_map[(bar, plt_ke)] = {
            "kolom": kol,
            "baris": bar,
            "pallet_ke": plt_ke,
            "part_number": pn,
            "nama_produk": clean_str(item.get("Nama Produk")),
            "lot_number": clean_str(item.get("Lot Number")),
            "keterangan": clean_str(item.get("Keterangan")) or "Utuh",
            "catatan": clean_str(item.get("Catatan")),
            "approval_qc": clean_str(item.get("Approval QC")),
            "jadwal_kirim": clean_str(item.get("Jadwal Kirim")),
            "target_penerima": clean_str(item.get("Target Penerima")),
            "status": clean_str(item.get("Status")) or "Terisi",
            "updated_at": now_iso,
        }

    payload = list(payload_map.values())
    for i in range(0, len(payload), 500):
        supabase.table(NAMA_DATABASE).upsert(
            payload[i:i + 500],
            on_conflict="kolom,baris,pallet_ke",
        ).execute()

    existing_ids = []
    step = 1000
    start = 0
    while True:
        res_ex = (
            supabase.table(NAMA_DATABASE)
            .select("id,baris,pallet_ke")
            .eq("kolom", kol)
            .range(start, start + step - 1)
            .execute()
        )
        batch_ex = res_ex.data or []
        for r in batch_ex:
            if (int(r["baris"]), int(r["pallet_ke"])) not in payload_map:
                existing_ids.append(r["id"])
        if len(batch_ex) < step:
            break
        start += step

    for i in range(0, len(existing_ids), 500):
        supabase.table(NAMA_DATABASE).delete().in_("id", existing_ids[i:i + 500]).execute()


def _upsert_json_doc(table_name: str, doc_id: str, data_dict: dict):
    supabase.table(table_name).upsert(
        {
            "doc_id": doc_id,
            "data": data_dict,
            "migrated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="doc_id",
    ).execute()


def _read_json_doc(table_name: str, doc_ids: list):
    res = supabase.table(table_name).select("*").in_("doc_id", doc_ids).execute()
    hasil = []
    for rec in res.data or []:
        data = rec.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = None
        if isinstance(data, dict):
            hasil.append(data)
    return hasil


# ==========================================================
# 5. DATA LOADERS, MASTER PRODUK & APPROVAL QC
# ==========================================================
@st.cache_data(ttl=300)
def get_all_racking_data() -> list:
    semua_data = []
    step = 1000
    start = 0
    while True:
        res = (
            supabase.table(NAMA_DATABASE)
            .select("*")
            .order("kolom", desc=False)
            .order("baris", desc=False)
            .order("pallet_ke", desc=False)
            .range(start, start + step - 1)
            .execute()
        )
        batch = res.data or []
        semua_data.extend(_row_db_to_slot_dict(r) for r in batch)
        if len(batch) < step:
            break
        start += step
    return semua_data


@st.cache_data(ttl=3600)
def load_master_data() -> pd.DataFrame:
    try:
        df = pd.read_csv("Master_Produk.csv", sep=";", dtype=str)
        df = df.dropna(subset=["Nama Barang"])
        df.columns = df.columns.str.strip()
        if "Part Number" not in df.columns:
            df["Part Number"] = ""

        df["Part Number"] = df["Part Number"].fillna("").astype(str).str.strip()
        df["Nama Barang"] = df["Nama Barang"].fillna("").astype(str).str.strip()
        df = df.drop_duplicates(subset=["Part Number", "Nama Barang"]).reset_index(drop=True)

        df["Kategori"] = df["Nama Barang"].apply(set_kategori)
        df["Gramasi"] = df["Nama Barang"].apply(ekstrak_gramasi)

        def clean_num(val):
            c = re.sub(r"[^\d]", "", str(val))
            return int(c) if c else 0

        df["Total_Pcs_Pallet"] = df["Isi 1 Pallet (pcs)"].apply(clean_num) if "Isi 1 Pallet (pcs)" in df.columns else 0
        df["Pcs_Per_Box"] = df["Box / Pallet"].apply(clean_num) if "Box / Pallet" in df.columns else 0
        df["Pcs_Per_Sac"] = df["Sac / Pallet"].apply(clean_num) if "Sac / Pallet" in df.columns else 0
        return df
    except Exception:
        return pd.DataFrame(
            columns=[
                "Nama Barang",
                "Kategori",
                "Gramasi",
                "Part Number",
                "Total_Pcs_Pallet",
                "Pcs_Per_Box",
                "Pcs_Per_Sac",
            ]
        )


def get_df_master_terfilter(df_master: pd.DataFrame) -> pd.DataFrame:
    if df_master.empty:
        return df_master
    if st.session_state.get("mode_transaksi") == "WIP":
        part_clean = df_master["Part Number"].astype(str).str.strip().str.upper()
        return df_master[
            (df_master["Kategori"].isin(["PREFORM", "CUP COLOR"])) | (part_clean.isin(PART_WIP_CUP_NATURAL))
        ].copy()
    return df_master


@st.cache_data(ttl=120)
def get_qc_approval_map() -> dict:
    """Membaca peta master Approval QC dari tabel wms_qc_approval."""
    try:
        res = supabase.table(DB_QC_APPROVAL).select("*").execute()
        lots_map = {}
        for r in res.data or []:
            doc_id = clean_str(r.get("doc_id"))
            data_json = r.get("data") if isinstance(r.get("data"), dict) else None
            if doc_id == "status_lot" and data_json is not None:
                nested_lots = data_json.get("lots")
                if isinstance(nested_lots, dict):
                    for k_lot, v_info in nested_lots.items():
                        if isinstance(v_info, dict):
                            lots_map[clean_str(k_lot).upper()] = v_info
            elif doc_id and data_json is not None:
                lots_map[doc_id.upper()] = data_json
        return lots_map
    except Exception:
        return {}


def make_qc_lot_key(part_num, lot_num) -> str:
    return f"{str(part_num).strip().upper()}_{str(lot_num).strip().upper()}"


def get_status_qc_lot(qc_map: dict, part_num, lot_num, fallback_col_val: str = "") -> dict:
    fb = clean_str(fallback_col_val).upper()
    if fb.startswith("APPROVED"):
        return {"status": "APPROVED", "qc_by": "-", "waktu": "-", "catatan": fallback_col_val}
    if fb.startswith("HOLD"):
        return {"status": "HOLD", "qc_by": "-", "waktu": "-", "catatan": fallback_col_val}

    key = make_qc_lot_key(part_num, lot_num)
    return qc_map.get(key, {"status": "PENDING", "qc_by": "-", "waktu": "-", "catatan": ""})


def _sync_qc_master_entry(part_clean: str, item_name: str, lot_clean: str, status_baru: str, qc_by: str, cat_clean: str, waktu_str: str):
    key = make_qc_lot_key(part_clean, lot_clean)
    lots_dict = dict(get_qc_approval_map())
    if status_baru == "PENDING":
        lots_dict.pop(key, None)
    else:
        lots_dict[key] = {
            "status": status_baru,
            "part_num": part_clean,
            "item_name": str(item_name).strip(),
            "lot_num": lot_clean,
            "qc_by": qc_by,
            "waktu": waktu_str,
            "catatan": cat_clean,
        }
    _upsert_json_doc(DB_QC_APPROVAL, "status_lot", {"lots": lots_dict, "updated_at": waktu_str})


def simpan_approval_qc_lot(part_num, item_name, lot_num, status_baru, qc_by, catatan_qc: str = ""):
    part_clean = str(part_num).strip().upper()
    lot_clean = str(lot_num).strip().upper()
    cat_clean = str(catatan_qc).strip()
    waktu_str = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")

    if status_baru == "PENDING":
        label_kolom_rak = ""
    else:
        label_kolom_rak = f"{status_baru} ({qc_by} | {waktu_str[5:16]})" + (f" - {cat_clean}" if cat_clean else "")

    _sync_qc_master_entry(part_clean, item_name, lot_clean, status_baru, qc_by, cat_clean, waktu_str)

    res_match = (
        supabase.table(NAMA_DATABASE)
        .select("kolom")
        .eq("part_number", part_clean)
        .ilike("lot_number", lot_clean)
        .execute()
    )
    kolom_terupdate = sorted(
        list({clean_str(r.get("kolom")).upper() for r in (res_match.data or []) if clean_str(r.get("kolom"))})
    )

    if kolom_terupdate:
        (
            supabase.table(NAMA_DATABASE)
            .update({"approval_qc": label_kolom_rak, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("part_number", part_clean)
            .ilike("lot_number", lot_clean)
            .execute()
        )

    get_qc_approval_map.clear()
    get_all_racking_data.clear()
    return kolom_terupdate, label_kolom_rak


def update_qc_slot_terplot(jadwal_kirim, target_penerima, part_num, lot_num, status_qc, operator_qc, catatan_qc: str = ""):
    """Memperbarui status Approval QC pada slot ter-plot & menyinkronkan ke master wms_qc_approval."""
    jk_target = clean_str(jadwal_kirim)
    tp_target = clean_str(target_penerima)
    pn_target = clean_str(part_num).upper()
    lot_target = clean_str(lot_num)
    cat_clean = clean_str(catatan_qc)
    waktu_str = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")

    if status_qc in {"APPROVED", "HOLD"}:
        label_qc = f"{status_qc} ({operator_qc} | {waktu_str[5:16]})" + (f" - {cat_clean}" if cat_clean else "")
    else:
        label_qc = ""

    res_slots = (
        supabase.table(NAMA_DATABASE)
        .select("id,kolom,nama_produk")
        .neq("kolom", "S")
        .eq("jadwal_kirim", jk_target)
        .eq("target_penerima", tp_target)
        .eq("part_number", pn_target)
        .eq("lot_number", lot_target)
        .execute()
    )
    rows_found = res_slots.data or []
    kolom_terdampak = sorted(list({clean_str(r.get("kolom")).upper() for r in rows_found if clean_str(r.get("kolom"))}))
    nama_item_found = next((clean_str(r.get("nama_produk")) for r in rows_found if clean_str(r.get("nama_produk"))), "")

    if rows_found:
        ids_upd = [r["id"] for r in rows_found]
        now_iso = datetime.now(timezone.utc).isoformat()
        for i in range(0, len(ids_upd), 500):
            (
                supabase.table(NAMA_DATABASE)
                .update({"approval_qc": label_qc, "updated_at": now_iso})
                .in_("id", ids_upd[i:i + 500])
                .execute()
            )

    try:
        _sync_qc_master_entry(pn_target, nama_item_found, lot_target.upper(), status_qc, operator_qc, cat_clean, waktu_str)
    except Exception:
        pass

    get_qc_approval_map.clear()
    get_all_racking_data.clear()
    return kolom_terdampak


# ==========================================================
# 6. ALGORITMA REKOMENDASI INBOUND, STOK & OUTBOUND PLOTTING
# ==========================================================
def hitung_kandidat_rak_inbound(semua_data, target_item, part_num, sisi_masuk: str = "BELAKANG", target_lot: str = "") -> pd.DataFrame:
    slot_map = {f"{k}-{b}": [] for k in KOLOM_RAK_UTAMA for b in BARIS_RAK_UTAMA}

    for d in semua_data:
        k = str(d.get("Kolom", "")).strip().upper()
        try:
            b = int(float(d.get("Baris", 0)))
        except Exception:
            continue
        if f"{k}-{b}" in slot_map:
            slot_map[f"{k}-{b}"].append(d)

    part_clean = str(part_num).strip().upper()
    in_is_sac = part_clean.endswith("C")
    target_item_clean = str(target_item).strip()
    target_lot_clean = str(target_lot).strip().upper()

    kandidat_rak = []
    for loc, pallets in slot_map.items():
        k, b_str = loc.split("-")
        b = int(b_str)

        if not is_rak_valid_untuk_produk(k, b, part_clean):
            continue

        zone_penalty = hitung_zone_penalty(k, b, target_item_clean, part_clean)
        max_sac, max_dus = _kapasitas_baris(b)
        max_target = max_sac if in_is_sac else max_dus

        pallets_sorted = sorted(pallets, key=lambda x: pd.to_numeric(x.get("Pallet Ke", 0), errors="coerce"))

        used_cap = 0.0
        distinct_items = set()
        distinct_lots = set()
        sejenis_lots = []
        has_sac = False
        jml_sejenis = 0

        for p in pallets_sorted:
            p_name = str(p.get("Nama Produk", "")).strip()
            p_lot = str(p.get("Lot Number", "")).strip()
            if p_name:
                distinct_items.add(p_name)
                if p_name == target_item_clean:
                    jml_sejenis += 1
                    if p_lot:
                        sejenis_lots.append(p_lot)
            if p_lot:
                distinct_lots.add(p_lot)
            p_part = str(p.get("Part Number", "")).strip().upper()
            if p_part.endswith("C"):
                has_sac = True
                used_cap += 1.0 / max_sac
            else:
                used_cap += 1.0 / max_dus

        sisa_cap = max(0.0, 1.0001 - used_cap)
        sisa_slot_pallet = int(math.floor(sisa_cap * max_target))

        if (in_is_sac or has_sac) and sisa_slot_pallet <= 1:
            continue
        if sisa_slot_pallet <= 0:
            continue
        if len(distinct_items) >= 2 and target_item_clean not in distinct_items:
            continue

        sama_lot = any(l.upper() == target_lot_clean for l in sejenis_lots) if target_lot_clean else False
        newest_rank = max([parse_lifo_rank(l) for l in sejenis_lots], default=0)

        if target_item_clean in distinct_items and len(distinct_items) == 1:
            prio = 1
            lot_ujung = str((pallets_sorted[0] if sisi_masuk == "DEPAN" else pallets_sorted[-1]).get("Lot Number", "")).strip()
            status = "🟢 Lanjutkan Lot Sama" if sama_lot else "🟢 Lanjutkan Sejenis (Lot Terbaru)"
            info_lot = f"Lot {lot_ujung}" if lot_ujung else "sejenis"
            isi_eksisting = f"{jml_sejenis} Pallet ({info_lot})"
            sub_sort = (0 if sama_lot else 1, -newest_rank, -used_cap)

        elif target_item_clean in distinct_items and len(distinct_items) == 2:
            item_ujung = str((pallets_sorted[0] if sisi_masuk == "DEPAN" else pallets_sorted[-1]).get("Nama Produk", "")).strip()
            if item_ujung != target_item_clean:
                continue
            prio = 2
            status = f"🔵 Lorong 2 Item (Sejenis di {sisi_masuk.title()})"
            item_lain = [x for x in distinct_items if x != target_item_clean][0]
            nama_ringkas = f"{item_lain[:16]}..." if len(item_lain) > 16 else item_lain
            isi_eksisting = f"{jml_sejenis}P sejenis + {len(pallets_sorted) - jml_sejenis}P {nama_ringkas}"
            sub_sort = (0 if sama_lot else 1, -newest_rank, -used_cap)

        elif len(distinct_items) == 0:
            prio = 3
            status = "⚪ Lorong Kosong"
            isi_eksisting = f"Kosong (Kapasitas {max_target} Pallet)"
            sub_sort = (0, 0, 0.0)

        elif target_item_clean in distinct_items and len(distinct_items) >= 3:
            item_ujung = str((pallets_sorted[0] if sisi_masuk == "DEPAN" else pallets_sorted[-1]).get("Nama Produk", "")).strip()
            if item_ujung != target_item_clean:
                continue
            blok_ujung = hitung_blok_ujung(pallets_sorted, target_item_clean, sisi_masuk)
            sisa_kuota_ujung = MAX_TAIL_PALLETS_MULTI_ITEM - blok_ujung
            if sisa_kuota_ujung <= 0:
                continue
            sisa_slot_pallet = min(sisa_slot_pallet, sisa_kuota_ujung)
            prio = 4
            status = f"🟣 Lorong {len(distinct_items)} Item (Sisa Ekor Maks. {sisa_slot_pallet}P)"
            isi_eksisting = f"{len(distinct_items)} item campur | Ujung {sisi_masuk.lower()}: {blok_ujung}P sejenis"
            sub_sort = (0 if sama_lot else 1, -newest_rank, blok_ujung)

        elif len(distinct_items) == 1 and target_item_clean not in distinct_items:
            prio = 5
            status = "🟡 Campur (Max 2 Item)"
            item_lain = list(distinct_items)[0]
            lot_str = ", ".join(sorted(list(distinct_lots))[:2])
            nama_ringkas = f"{item_lain[:16]}..." if len(item_lain) > 16 else item_lain
            isi_eksisting = f"{len(pallets_sorted)}P {nama_ringkas} (Lot: {lot_str})"
            sub_sort = (0, 0, used_cap)
        else:
            continue

        kandidat_rak.append(
            {
                "Loc": loc,
                "Kolom": k,
                "Baris": b,
                "Priority": prio,
                "ZonePenalty": zone_penalty,
                "Status": status,
                "IsiEksisting": isi_eksisting,
                "UsedCap": used_cap,
                "SubSort": sub_sort,
                "MaxTarget": max_target,
                "SisaSlot": sisa_slot_pallet,
            }
        )

    if not kandidat_rak:
        return pd.DataFrame()

    return pd.DataFrame(kandidat_rak).sort_values(
        by=["ZonePenalty", "Priority", "SubSort", "Kolom", "Baris"],
        ascending=[True, True, True, True, True],
    ).reset_index(drop=True)


def cari_lokasi_stok_hibrida(semua_data, target_item, exclude_staging: bool = False) -> pd.DataFrame:
    if not semua_data:
        return pd.DataFrame()

    df_all = pd.DataFrame(semua_data)
    if exclude_staging:
        df_all = df_all[df_all["Kolom"].astype(str).str.strip().str.upper() != "S"].copy()

    df_all["Baris_Int"] = pd.to_numeric(df_all["Baris"], errors="coerce").fillna(0).astype(int)
    df_all["Pallet_Ke_Num"] = pd.to_numeric(df_all["Pallet Ke"], errors="coerce").fillna(0)
    df_all["Catatan"] = df_all["Catatan"].apply(clean_str) if "Catatan" in df_all.columns else ""
    df_all["Approval QC"] = df_all["Approval QC"].apply(clean_str) if "Approval QC" in df_all.columns else ""

    df_target = df_all[
        (df_all["Nama Produk"] == target_item) & (df_all["Part Number"].astype(str).str.strip() != "")
    ].copy()
    if df_target.empty:
        return pd.DataFrame()

    df_target["Group_Key"] = (
        df_target["Kolom"].astype(str) + "_" + df_target["Baris_Int"].astype(str) + "_" + df_target["Lot Number"].astype(str)
    )

    rekap_lokasi = []
    for _, df_group in df_target.groupby("Group_Key"):
        kol = str(df_group.iloc[0]["Kolom"])
        bar = int(df_group.iloc[0]["Baris_Int"])
        lot = str(df_group.iloc[0]["Lot Number"])
        catatan = str(df_group.iloc[0]["Catatan"])
        app_qc_col = str(df_group.iloc[0]["Approval QC"])
        part_num = str(df_group.iloc[0]["Part Number"])
        satuan_str = get_satuan_kemasan(part_num)

        is_pec_mask = df_group["Keterangan"].astype(str).str.contains("Pecahan|Eceran", case=False, na=False)
        jml_utuh = int((~is_pec_mask).sum())
        jml_dus = sum(parse_keterangan(k)[0] for k in df_group.loc[is_pec_mask, "Keterangan"])

        min_pos_target = df_group["Pallet_Ke_Num"].min()
        max_pos_target = df_group["Pallet_Ke_Num"].max()
        df_aisle = df_all[(df_all["Kolom"] == kol) & (df_all["Baris_Int"] == bar)]

        is_other = ~((df_aisle["Nama Produk"] == target_item) & (df_aisle["Lot Number"].astype(str) == lot))
        df_penghalang_depan = df_aisle[is_other & (df_aisle["Pallet_Ke_Num"] < min_pos_target)]
        df_penghalang_belakang = df_aisle[is_other & (df_aisle["Pallet_Ke_Num"] > max_pos_target)]

        jml_depan = len(df_penghalang_depan)
        jml_belakang = len(df_penghalang_belakang)

        if jml_depan == 0:
            akses_rank, min_penghalang, sisi_akses, info_penghalang = 0, 0, "DEPAN", ""
        elif jml_belakang == 0:
            akses_rank, min_penghalang, sisi_akses, info_penghalang = 1, 0, "BELAKANG", ""
        else:
            akses_rank = 2
            if jml_depan <= jml_belakang:
                min_penghalang, sisi_akses, df_blok = jml_depan, "TERHALANG_DEPAN", df_penghalang_depan
            else:
                min_penghalang, sisi_akses, df_blok = jml_belakang, "TERHALANG_BELAKANG", df_penghalang_belakang

            item_blok = df_blok["Nama Produk"].dropna().unique().tolist()
            if len(item_blok) == 1 and item_blok[0] == target_item:
                lot_blok = df_blok["Lot Number"].dropna().unique().tolist()
                info_penghalang = f"Lot {', '.join(lot_blok[:2])}"
            else:
                nama_blok = [x.split(",")[-1].strip() if "," in x else x[:16] for x in item_blok[:2]]
                info_penghalang = f"{', '.join(nama_blok)}"

        rekap_lokasi.append(
            {
                "Kolom": kol,
                "Baris": bar,
                "Lot Number": lot,
                "FIFO_Key": parse_fifo_key(lot),
                "Pallet": jml_utuh,
                "Dus": jml_dus,
                "Satuan": satuan_str,
                "Catatan": catatan,
                "Approval QC": app_qc_col,
                "Part": part_num,
                "AksesRank": akses_rank,
                "MinPenghalang": min_penghalang,
                "SisiAkses": sisi_akses,
                "InfoPenghalang": info_penghalang,
            }
        )

    return pd.DataFrame(rekap_lokasi).sort_values(
        by=["MinPenghalang", "FIFO_Key", "Pallet", "Dus", "AksesRank", "Lot Number", "Kolom", "Baris"],
        ascending=[True, True, True, True, True, True, True, True],
    ).reset_index(drop=True)


def cari_lokasi_stok_outbound_qc(semua_data, target_item, qc_map):
    df_all_loc = cari_lokasi_stok_hibrida(semua_data, target_item, exclude_staging=True)
    if df_all_loc.empty:
        return pd.DataFrame(), pd.DataFrame()

    qc_statuses, qc_bys, qc_notes = [], [], []
    for _, row in df_all_loc.iterrows():
        info = get_status_qc_lot(qc_map, row["Part"], row["Lot Number"], row.get("Approval QC", ""))
        qc_statuses.append(info.get("status", "PENDING"))
        qc_bys.append(info.get("qc_by", "-"))
        qc_notes.append(info.get("catatan", ""))

    df_all_loc["QC_Status"] = qc_statuses
    df_all_loc["QC_By"] = qc_bys
    df_all_loc["QC_Catatan"] = qc_notes

    return (
        df_all_loc[df_all_loc["QC_Status"] == "APPROVED"].reset_index(drop=True),
        df_all_loc[df_all_loc["QC_Status"] != "APPROVED"].reset_index(drop=True),
    )


def scan_plotting_untuk_outbound(semua_data, jadwal_kirim, target_penerima, qc_map=None):
    """Memindai slot rak (A-G) yang ter-plot PPIC untuk (jadwal_kirim, target_penerima)."""
    if not semua_data:
        return pd.DataFrame(), pd.DataFrame()

    jk_target = clean_str(jadwal_kirim)
    tp_target = clean_str(target_penerima)
    qc_map = qc_map or {}

    df_all = pd.DataFrame(semua_data)
    for col_opt in ["Jadwal Kirim", "Target Penerima", "Approval QC", "Keterangan", "Catatan"]:
        df_all[col_opt] = df_all[col_opt].apply(clean_str) if col_opt in df_all.columns else ""

    df_all["Kolom_Clean"] = df_all["Kolom"].apply(clean_str).str.upper()
    df_all["Baris_Int"] = pd.to_numeric(df_all["Baris"], errors="coerce").fillna(0).astype(int)
    df_all["Pallet_Ke_Num"] = pd.to_numeric(df_all["Pallet Ke"], errors="coerce").fillna(0)

    df_rak = df_all[df_all["Kolom_Clean"] != "S"].copy()
    df_plot = df_rak[(df_rak["Jadwal Kirim"] == jk_target) & (df_rak["Target Penerima"] == tp_target)].copy()
    if df_plot.empty:
        return pd.DataFrame(), pd.DataFrame()

    df_plot["Group_Key"] = (
        df_plot["Kolom_Clean"]
        + "_"
        + df_plot["Baris_Int"].astype(str)
        + "_"
        + df_plot["Part Number"].apply(clean_str).str.upper()
        + "_"
        + df_plot["Lot Number"].apply(clean_str)
    )

    rekap_rows = []
    for _, grp in df_plot.groupby("Group_Key"):
        kol = clean_str(grp.iloc[0]["Kolom_Clean"])
        bar = int(grp.iloc[0]["Baris_Int"])
        pn = clean_str(grp.iloc[0]["Part Number"]).upper()
        nama = clean_str(grp.iloc[0]["Nama Produk"])
        lot = clean_str(grp.iloc[0]["Lot Number"])
        cat_rak = clean_str(grp.iloc[0]["Catatan"])
        raw_qc = clean_str(grp.iloc[0]["Approval QC"])
        satuan_str = get_satuan_kemasan(pn)

        info_qc = get_status_qc_lot(qc_map, pn, lot, raw_qc)
        st_qc = info_qc.get("status", "PENDING")

        is_pec_mask = grp["Keterangan"].str.contains("Pecahan|Eceran", case=False, na=False)
        jml_utuh = int((~is_pec_mask).sum())
        jml_dus = sum(parse_keterangan(k)[0] for k in grp.loc[is_pec_mask, "Keterangan"])

        min_pos = grp["Pallet_Ke_Num"].min()
        max_pos = grp["Pallet_Ke_Num"].max()

        df_aisle = df_rak[(df_rak["Kolom_Clean"] == kol) & (df_rak["Baris_Int"] == bar)].sort_values("Pallet_Ke_Num")
        is_penghalang_fisik = ~((df_aisle["Jadwal Kirim"] == jk_target) & (df_aisle["Target Penerima"] == tp_target))
        is_rombongan_depan = (~is_penghalang_fisik) & (df_aisle["Pallet_Ke_Num"] < min_pos)

        depan_blk = int((is_penghalang_fisik & (df_aisle["Pallet_Ke_Num"] < min_pos)).sum())
        belakang_blk = int((is_penghalang_fisik & (df_aisle["Pallet_Ke_Num"] > max_pos)).sum())

        if depan_blk == 0:
            akses_rank, min_blk = 0, 0
            sisi_akses = "DEPAN (FIFO - Lanjut Antrean)" if int(is_rombongan_depan.sum()) > 0 else "DEPAN (FIFO)"
        elif belakang_blk == 0:
            akses_rank, min_blk, sisi_akses = 1, 0, "BELAKANG (LIFO)"
        else:
            akses_rank = 2
            if depan_blk <= belakang_blk:
                min_blk, sisi_akses = depan_blk, f"TERHALANG DEPAN ({depan_blk}P)"
            else:
                min_blk, sisi_akses = belakang_blk, f"TERHALANG BELAKANG ({belakang_blk}P)"

        rekap_rows.append(
            {
                "Kolom": kol,
                "Baris": bar,
                "Lokasi": f"{kol}-{bar}",
                "Part Number": pn,
                "Nama Produk": nama,
                "Lot Number": lot,
                "FIFO_Key": parse_fifo_key(lot),
                "Pallet": jml_utuh,
                "Dus": jml_dus,
                "Satuan": satuan_str,
                "AksesPintu": sisi_akses,
                "AksesRank": akses_rank,
                "MinPenghalang": min_blk,
                "Catatan": cat_rak,
                "RawQC": raw_qc,
                "QC_Status": st_qc,
                "QC_Catatan": info_qc.get("catatan", ""),
            }
        )

    df_res = pd.DataFrame(rekap_rows).sort_values(
        by=["FIFO_Key", "Part Number", "Kolom", "Baris", "Lot Number"],
        ascending=[True, True, True, True, True],
    ).reset_index(drop=True)

    return (
        df_res[df_res["QC_Status"] == "APPROVED"].reset_index(drop=True),
        df_res[df_res["QC_Status"] != "APPROVED"].reset_index(drop=True),
    )


# ==========================================================
# 7. AUDIT LOG, PERSISTENT UNDO & AUTO-SAVE DRAFT
# ==========================================================
def catat_log_mutasi(jenis, kolom, baris, produk, part_num, no_lot, pallet_utuh, pecahan, eceran, keterangan: str = ""):
    kol_str = clean_str(kolom)
    bar_int = _safe_int_val(baris, 0)
    if bar_int > 0:
        lok_str = f"{kol_str}-{bar_int}"
    elif clean_str(baris) and clean_str(baris).upper() not in {"ALL", "NONE", "PLOT", "LOT"}:
        lok_str = clean_str(baris)
    else:
        lok_str = kol_str

    j_up = str(jenis).upper()
    arus = "KELUAR" if any(x in j_up for x in ("KELUAR", "OUTBOUND", "WIP", "STAGING")) else "MASUK"
    if "UNDO" in j_up or "PEMBATALAN" in j_up:
        arus = "KOREKSI"

    op_name = clean_str(st.session_state.get("operator")) or "Operator Lapangan"
    ec_int = _safe_int_val(eceran, 0)
    row_log = {
        "waktu_transaksi": datetime.now(timezone.utc).isoformat(),
        "arus": arus,
        "jenis_transaksi": str(jenis),
        "lokasi_rak": lok_str,
        "kolom": kol_str,
        "baris": bar_int,
        "part_number": clean_str(part_num),
        "nama_produk": clean_str(produk),
        "lot_number": str(no_lot).strip() or "-",
        "mutasi_pallet": _safe_int_val(pallet_utuh, 0),
        "mutasi_pecahan": _safe_int_val(pecahan, 0),
        "mutasi_eceran": ec_int,
        "total_pcs": ec_int,
        "tujuan_kategori": str(jenis),
        "detail_sistem": str(keterangan),
        "operator": op_name,
        "keterangan": str(keterangan),
    }
    try:
        supabase.table(DB_LOG_MUTASI).insert(row_log).execute()
    except Exception:
        pass


def simpan_draft_inbound(prefix_app: str, operator_name: str, draft_payload: dict) -> bool:
    op_clean = clean_str(operator_name)
    if not op_clean:
        return False
    payload_copy = copy.deepcopy(draft_payload)
    payload_copy["saved_at"] = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")
    payload_copy["source_app"] = prefix_app
    st.session_state.inbound_draft = payload_copy
    try:
        _upsert_json_doc(DB_DRAFT_INBOUND, f"INBOUND_{op_clean}", payload_copy)
        return True
    except Exception as e:
        st.error(f"❌ Gagal menyimpan draft ke Supabase: {e}")
        return False


def muat_draft_inbound(prefix_app: str, operator_name: str):
    op_clean = clean_str(operator_name)
    if not op_clean:
        st.session_state.inbound_draft = None
        return None
    try:
        kandidat_ids = list(dict.fromkeys([f"INBOUND_{op_clean}", f"{prefix_app}_{op_clean}", f"INB_{op_clean}", f"IN_{op_clean}"]))
        for data in _read_json_doc(DB_DRAFT_INBOUND, kandidat_ids):
            if data.get("item"):
                saved_dt = _parse_dt_wib(data.get("saved_at"))
                if saved_dt is not None and (get_waktu_jkt() - saved_dt) > timedelta(hours=12):
                    continue
                st.session_state.inbound_draft = data
                return data
    except Exception as e:
        st.warning(f"⚠️ Gagal membaca draft: {e}")
    st.session_state.inbound_draft = None
    return None


def hapus_draft_inbound(prefix_app: str, operator_name: str):
    st.session_state.inbound_draft = None
    op_clean = clean_str(operator_name)
    if not op_clean:
        return
    try:
        kandidat_ids = list(dict.fromkeys([f"INBOUND_{op_clean}", f"{prefix_app}_{op_clean}", f"INB_{op_clean}", f"IN_{op_clean}"]))
        supabase.table(DB_DRAFT_INBOUND).delete().in_("doc_id", kandidat_ids).execute()
    except Exception:
        pass


def simpan_draft_outbound(prefix_app: str, operator_name: str, draft_payload: dict):
    if not operator_name:
        return
    payload_copy = copy.deepcopy(draft_payload)
    payload_copy["saved_at"] = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")
    st.session_state.outbound_draft = payload_copy
    try:
        _upsert_json_doc(DB_DRAFT_OUTBOUND, f"{prefix_app}_{operator_name}", payload_copy)
    except Exception as e:
        st.toast(f"⚠️ Gagal simpan draft outbound: {e}", icon="⚠️")


def muat_draft_outbound(prefix_app: str, operator_name: str):
    if not operator_name:
        st.session_state.outbound_draft = None
        return
    try:
        for data in _read_json_doc(DB_DRAFT_OUTBOUND, [f"{prefix_app}_{operator_name}"]):
            saved_dt = _parse_dt_wib(data.get("saved_at"))
            if saved_dt is None or (get_waktu_jkt() - saved_dt) <= timedelta(hours=12):
                st.session_state.outbound_draft = data
                return
    except Exception:
        pass
    st.session_state.outbound_draft = None


def hapus_draft_outbound(prefix_app: str, operator_name: str):
    st.session_state.outbound_draft = None
    if not operator_name:
        return
    try:
        supabase.table(DB_DRAFT_OUTBOUND).delete().eq("doc_id", f"{prefix_app}_{operator_name}").execute()
    except Exception:
        pass


def simpan_undo_firestore(prefix_app: str, operator_name: str, undo_dict: dict, action_msg: str, mode_label: str):
    payload = {
        "status": "DONE",
        "mode": mode_label,
        "undo_dict": undo_dict,
        "action_msg": action_msg,
        "created_at": get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S"),
    }
    st.session_state.undo_record = payload
    try:
        _upsert_json_doc(DB_UNDO_STATE, f"{prefix_app}_{operator_name}", payload)
    except Exception as e:
        st.toast(f"⚠️ Gagal simpan state undo: {e}", icon="⚠️")


def muat_undo_firestore(prefix_app: str, operator_name: str):
    try:
        for data in _read_json_doc(DB_UNDO_STATE, [f"{prefix_app}_{operator_name}"]):
            if data.get("status") == "DONE" and data.get("created_at"):
                created_dt = _parse_dt_wib(data["created_at"])
                if created_dt is not None and (get_waktu_jkt() - created_dt) <= timedelta(minutes=30):
                    st.session_state.undo_record = data
                    return
    except Exception:
        pass
    st.session_state.undo_record = None


def tutup_undo_firestore(prefix_app: str, operator_name: str, new_status: str = "CLOSED"):
    current_data = copy.deepcopy(st.session_state.get("undo_record") or {})
    current_data["status"] = new_status
    st.session_state.undo_record = {"status": new_status} if new_status == "UNDONE" else None
    try:
        _upsert_json_doc(DB_UNDO_STATE, f"{prefix_app}_{operator_name}", current_data)
    except Exception:
        pass


# ==========================================================
# 8. TRANSAKSI DATABASE (INBOUND, PENGAMBILAN, STAGING)
# ==========================================================
def proses_inbound_multi_transaction(
    transaction, doc_refs_dict, alokasi_list, lot_pilih, part_num,
    item_name, tipe_pecahan, catatan, sisi_masuk: str = "BELAKANG", approval_qc: str = ""
):
    kolom_list = [_resolve_kolom_id(k) for k in doc_refs_dict.keys()]
    backup_kolom = {}
    data_per_kolom = {}

    for kol in kolom_list:
        existing = _fetch_kolom_rows(kol)
        backup_kolom[kol] = [d.copy() for d in existing]
        data_per_kolom[kol] = [d.copy() for d in existing]

    in_is_sac = str(part_num).strip().upper().endswith("C")
    target_clean = str(item_name).strip()

    for item_alok in alokasi_list:
        k_pilih = clean_str(item_alok["Kolom"]).upper()
        b_int = int(item_alok["Baris"])
        b_pilih = float(b_int)
        qty_p = int(item_alok["qty_p"])
        qty_c = int(item_alok["qty_c"])

        if qty_p == 0 and qty_c == 0:
            continue

        if not is_rak_valid_untuk_produk(k_pilih, b_int, part_num):
            raise ValueError(
                f"Rak {k_pilih}-{b_int} bukan zona khusus untuk {item_name} (wajib Kolom F baris 1-16 atau Kolom G baris 17-28)."
            )

        all_col = data_per_kolom[k_pilih]
        rak_ini = sorted(
            [d for d in all_col if int(float(d.get("Baris", 0) or 0)) == b_int],
            key=lambda x: pd.to_numeric(x.get("Pallet Ke", 0), errors="coerce"),
        )
        rak_lain = [d for d in all_col if int(float(d.get("Baris", 0) or 0)) != b_int]

        existing_items = {str(p.get("Nama Produk", "")).strip() for p in rak_ini if str(p.get("Nama Produk", "")).strip()}
        if len(existing_items) >= 2:
            if target_clean not in existing_items:
                raise ValueError(
                    f"Rak {k_pilih}-{b_int} sudah terisi {len(existing_items)} jenis item lain. Tidak boleh menambah item baru berbeda!"
                )
            item_ujung = str((rak_ini[0] if sisi_masuk == "DEPAN" else rak_ini[-1]).get("Nama Produk", "")).strip()
            if item_ujung != target_clean:
                raise ValueError(
                    f"Rak {k_pilih}-{b_int} memiliki item lain di posisi {sisi_masuk.lower()}, tidak boleh dikunci dari sisi {sisi_masuk.lower()}!"
                )

        max_sac, max_dus = _kapasitas_baris(b_int)
        used_cap = sum(
            (1.0 / max_sac) if str(p.get("Part Number", "")).strip().upper().endswith("C") else (1.0 / max_dus)
            for p in rak_ini
        )

        slot_masuk = qty_p + (1 if qty_c > 0 else 0)
        inc_cap = (slot_masuk / max_sac) if in_is_sac else (slot_masuk / max_dus)
        if used_cap + inc_cap > 1.0001:
            raise ValueError(
                f"Rak {k_pilih}-{b_int} melebihi kapasitas maksimal baris ({max_sac if in_is_sac else max_dus} palet)."
            )

        pallets_baru = [
            {
                "Kolom": k_pilih,
                "Baris": b_pilih,
                "Nama Produk": item_name,
                "Part Number": part_num,
                "Lot Number": lot_pilih,
                "Keterangan": "Utuh",
                "Catatan": catatan,
                "Approval QC": approval_qc,
                "Jadwal Kirim": "",
                "Target Penerima": "",
                "Pallet Ke": float(i + 1),
            }
            for i in range(qty_p)
        ]

        if qty_c > 0:
            pallets_baru.append(
                {
                    "Kolom": k_pilih,
                    "Baris": b_pilih,
                    "Nama Produk": item_name,
                    "Part Number": part_num,
                    "Lot Number": lot_pilih,
                    "Keterangan": f"Pecahan: {qty_c} {tipe_pecahan}",
                    "Catatan": catatan,
                    "Approval QC": approval_qc,
                    "Jadwal Kirim": "",
                    "Target Penerima": "",
                    "Pallet Ke": float(qty_p + 1),
                }
            )

        gabungan_rak = (pallets_baru + rak_ini) if sisi_masuk == "DEPAN" else (rak_ini + pallets_baru)
        for idx_p, d in enumerate(gabungan_rak, 1):
            d["Pallet Ke"] = float(idx_p)

        data_per_kolom[k_pilih] = rak_lain + gabungan_rak

    for kol in kolom_list:
        _sync_kolom_to_supabase(kol, data_per_kolom[kol])

    return backup_kolom


def proses_pengambilan_stok(
    transaction, doc_ref, k_pilih, b_pilih, lot_pilih, part_num, item_name,
    init_pallet, init_dus, final_pallet, final_dus, satuan_kemasan, catatan_lama
):
    kol = _resolve_kolom_id(doc_ref) if doc_ref is not None else clean_str(k_pilih).upper()
    b_int = int(float(b_pilih))

    all_col_data = _fetch_kolom_rows(kol)
    if not all_col_data:
        raise ValueError("Data Kolom tidak ditemukan di database.")

    data_lama_sebelum_update = [d.copy() for d in all_col_data]
    rak_ini = sorted(
        [d for d in all_col_data if int(float(d.get("Baris", 0) or 0)) == b_int],
        key=lambda x: pd.to_numeric(x.get("Pallet Ke", 0)),
    )
    rak_lain = [d for d in all_col_data if int(float(d.get("Baris", 0) or 0)) != b_int]

    lot_records = [
        d for d in rak_ini if str(d.get("Lot Number", "")) == lot_pilih and str(d.get("Nama Produk", "")) == item_name
    ]
    db_utuh = sum(
        1
        for d in lot_records
        if "pecahan" not in str(d.get("Keterangan", "")).lower() and "eceran" not in str(d.get("Keterangan", "")).lower()
    )
    db_dus = sum(
        parse_keterangan(d.get("Keterangan", ""))[0]
        for d in lot_records
        if "pecahan" in str(d.get("Keterangan", "")).lower() or "eceran" in str(d.get("Keterangan", "")).lower()
    )
    app_qc_lama = str(lot_records[0].get("Approval QC", "")) if lot_records else ""
    jk_lama = clean_str(lot_records[0].get("Jadwal Kirim", "")) if lot_records else ""
    tp_lama = clean_str(lot_records[0].get("Target Penerima", "")) if lot_records else ""

    if db_utuh != init_pallet or db_dus != init_dus:
        raise ValueError(
            f"Stok di database baru saja berubah ({db_utuh} Pallet, {db_dus} {satuan_kemasan}). Silakan tekan 'Sinkronkan Stok'."
        )

    first_target_idx = next(
        (
            idx
            for idx, d in enumerate(rak_ini)
            if str(d.get("Lot Number", "")) == lot_pilih and str(d.get("Nama Produk", "")) == item_name
        ),
        len(rak_ini),
    )

    records_baru = [
        {
            "Kolom": kol,
            "Baris": float(b_int),
            "Nama Produk": item_name,
            "Part Number": part_num,
            "Lot Number": lot_pilih,
            "Keterangan": "Utuh",
            "Catatan": catatan_lama,
            "Approval QC": app_qc_lama,
            "Jadwal Kirim": jk_lama,
            "Target Penerima": tp_lama,
            "Pallet Ke": 0.0,
        }
        for _ in range(final_pallet)
    ]

    if final_dus > 0:
        records_baru.append(
            {
                "Kolom": kol,
                "Baris": float(b_int),
                "Nama Produk": item_name,
                "Part Number": part_num,
                "Lot Number": lot_pilih,
                "Keterangan": f"Pecahan: {final_dus} {satuan_kemasan}",
                "Catatan": catatan_lama,
                "Approval QC": app_qc_lama,
                "Jadwal Kirim": jk_lama,
                "Target Penerima": tp_lama,
                "Pallet Ke": 0.0,
            }
        )

    updated_rak_ini = []
    inserted = False
    for idx, d in enumerate(rak_ini):
        if idx == first_target_idx and not inserted:
            updated_rak_ini.extend(records_baru)
            inserted = True
        if not (str(d.get("Lot Number", "")) == lot_pilih and str(d.get("Nama Produk", "")) == item_name):
            updated_rak_ini.append(d)
    if not inserted:
        updated_rak_ini.extend(records_baru)

    for i, d in enumerate(updated_rak_ini, 1):
        d["Pallet Ke"] = float(i)

    _sync_kolom_to_supabase(kol, rak_lain + updated_rak_ini)
    return data_lama_sebelum_update


def proses_turun_ke_staging(
    transaction, doc_ref_asal, doc_ref_staging,
    k_pilih, b_pilih, lot_pilih, part_num, item_name,
    init_pallet, init_dus, final_pallet, final_dus,
    turun_pallet, turun_dus, satuan_kemasan,
    catatan_rak_lama, catatan_staging
):
    kol = clean_str(k_pilih).upper()
    b_int = int(float(b_pilih))

    all_col_asal = _fetch_kolom_rows(kol)
    if not all_col_asal:
        raise ValueError(f"Data Kolom {kol} tidak ditemukan di database.")

    all_col_staging = _fetch_kolom_rows("S")
    backup_asal = [d.copy() for d in all_col_asal]
    backup_staging = [d.copy() for d in all_col_staging]

    rak_ini = sorted(
        [d for d in all_col_asal if int(float(d.get("Baris", 0) or 0)) == b_int],
        key=lambda x: pd.to_numeric(x.get("Pallet Ke", 0)),
    )
    rak_lain = [d for d in all_col_asal if int(float(d.get("Baris", 0) or 0)) != b_int]

    lot_records = [
        d for d in rak_ini if str(d.get("Lot Number", "")) == lot_pilih and str(d.get("Nama Produk", "")) == item_name
    ]
    db_utuh = sum(
        1
        for d in lot_records
        if "pecahan" not in str(d.get("Keterangan", "")).lower() and "eceran" not in str(d.get("Keterangan", "")).lower()
    )
    db_dus = sum(
        parse_keterangan(d.get("Keterangan", ""))[0]
        for d in lot_records
        if "pecahan" in str(d.get("Keterangan", "")).lower() or "eceran" in str(d.get("Keterangan", "")).lower()
    )
    app_qc_lama = str(lot_records[0].get("Approval QC", "")) if lot_records else ""

    if db_utuh != init_pallet or db_dus != init_dus:
        raise ValueError(
            f"Stok di Rak {kol}-{b_int} baru saja berubah ({db_utuh} Pallet, {db_dus} {satuan_kemasan}). Silakan tekan 'Sinkronkan Stok'."
        )

    first_target_idx = next(
        (
            idx
            for idx, d in enumerate(rak_ini)
            if str(d.get("Lot Number", "")) == lot_pilih and str(d.get("Nama Produk", "")) == item_name
        ),
        len(rak_ini),
    )

    sisa_records_asal = [
        {
            "Kolom": kol,
            "Baris": float(b_int),
            "Nama Produk": item_name,
            "Part Number": part_num,
            "Lot Number": lot_pilih,
            "Keterangan": "Utuh",
            "Catatan": catatan_rak_lama,
            "Approval QC": app_qc_lama,
            "Jadwal Kirim": "",
            "Target Penerima": "",
            "Pallet Ke": 0.0,
        }
        for _ in range(final_pallet)
    ]
    if final_dus > 0:
        sisa_records_asal.append(
            {
                "Kolom": kol,
                "Baris": float(b_int),
                "Nama Produk": item_name,
                "Part Number": part_num,
                "Lot Number": lot_pilih,
                "Keterangan": f"Pecahan: {final_dus} {satuan_kemasan}",
                "Catatan": catatan_rak_lama,
                "Approval QC": app_qc_lama,
                "Jadwal Kirim": "",
                "Target Penerima": "",
                "Pallet Ke": 0.0,
            }
        )

    updated_rak_asal = []
    inserted = False
    for idx, d in enumerate(rak_ini):
        if idx == first_target_idx and not inserted:
            updated_rak_asal.extend(sisa_records_asal)
            inserted = True
        if not (str(d.get("Lot Number", "")) == lot_pilih and str(d.get("Nama Produk", "")) == item_name):
            updated_rak_asal.append(d)
    if not inserted:
        updated_rak_asal.extend(sisa_records_asal)

    for i, d in enumerate(updated_rak_asal, 1):
        d["Pallet Ke"] = float(i)

    staging_s1 = sorted(
        [d.copy() for d in all_col_staging if int(float(d.get("Baris", 0) or 0)) == 1],
        key=lambda x: pd.to_numeric(x.get("Pallet Ke", 0)),
    )
    staging_lain = [d.copy() for d in all_col_staging if int(float(d.get("Baris", 0) or 0)) != 1]

    pallets_ke_staging = [
        {
            "Kolom": "S",
            "Baris": 1.0,
            "Nama Produk": item_name,
            "Part Number": part_num,
            "Lot Number": lot_pilih,
            "Keterangan": "Utuh",
            "Catatan": catatan_staging,
            "Approval QC": app_qc_lama,
            "Jadwal Kirim": "",
            "Target Penerima": "",
            "Pallet Ke": 0.0,
        }
        for _ in range(turun_pallet)
    ]
    if turun_dus > 0:
        pallets_ke_staging.append(
            {
                "Kolom": "S",
                "Baris": 1.0,
                "Nama Produk": item_name,
                "Part Number": part_num,
                "Lot Number": lot_pilih,
                "Keterangan": f"Pecahan: {turun_dus} {satuan_kemasan}",
                "Catatan": catatan_staging,
                "Approval QC": app_qc_lama,
                "Jadwal Kirim": "",
                "Target Penerima": "",
                "Pallet Ke": 0.0,
            }
        )

    gabungan_s1 = staging_s1 + pallets_ke_staging
    for i, d in enumerate(gabungan_s1, 1):
        d["Pallet Ke"] = float(i)

    _sync_kolom_to_supabase(kol, rak_lain + updated_rak_asal)
    _sync_kolom_to_supabase("S", staging_lain + gabungan_s1)

    return {kol: backup_asal, "S": backup_staging}


def proses_turun_plotting_ke_staging_multi(
    transaction, doc_refs_asal_dict, doc_ref_staging,
    item_turun_list, jadwal_kirim, target_penerima, operator_name, catatan_umum: str = ""
):
    jk_target = clean_str(jadwal_kirim)
    tp_target = clean_str(target_penerima)

    kolom_asal_list = [_resolve_kolom_id(k) for k in doc_refs_asal_dict.keys()]
    backup_dict = {}
    work_asal = {}

    for k in kolom_asal_list:
        arr = _fetch_kolom_rows(k)
        if not arr:
            raise ValueError(f"Data Kolom {k} tidak ditemukan.")
        backup_dict[k] = [d.copy() for d in arr]
        work_asal[k] = [d.copy() for d in arr]

    all_staging = _fetch_kolom_rows("S")
    backup_dict["S"] = [d.copy() for d in all_staging]

    staging_s1 = sorted(
        [d.copy() for d in all_staging if int(float(d.get("Baris", 0) or 0)) == 1],
        key=lambda x: pd.to_numeric(x.get("Pallet Ke", 0)),
    )
    staging_lain = [d.copy() for d in all_staging if int(float(d.get("Baris", 0) or 0)) != 1]

    pallets_ke_staging = []
    ringkasan_eksekusi = []

    for it in item_turun_list:
        k_id = clean_str(it["Kolom"]).upper()
        b_num = int(it["Baris"])
        pn = clean_str(it["Part Number"]).upper()
        nama = clean_str(it["Nama Produk"])
        lot = clean_str(it["Lot Number"])
        satuan = clean_str(it["Satuan"])
        turun_p = int(it["Turun_P"])
        turun_d = int(it["Turun_D"])
        dari_belakang = "BELAKANG" in clean_str(it.get("AksesPintu", "")).upper()

        if turun_p <= 0 and turun_d <= 0:
            continue

        all_col = work_asal[k_id]
        rak_ini = sorted(
            [d for d in all_col if int(float(d.get("Baris", 0) or 0)) == b_num],
            key=lambda x: pd.to_numeric(x.get("Pallet Ke", 0)),
        )
        rak_lain = [d for d in all_col if int(float(d.get("Baris", 0) or 0)) != b_num]

        idx_kandidat = [
            idx
            for idx, d in enumerate(rak_ini)
            if clean_str(d.get("Part Number")).upper() == pn
            and clean_str(d.get("Lot Number")) == lot
            and clean_str(d.get("Jadwal Kirim")) == jk_target
            and clean_str(d.get("Target Penerima")) == tp_target
        ]
        if dari_belakang:
            idx_kandidat = list(reversed(idx_kandidat))

        for idx_s in idx_kandidat:
            if not clean_str(rak_ini[idx_s].get("Approval QC")).upper().startswith("APPROVED"):
                raise ValueError(f"Rak {k_id}-{b_num} (Lot {lot}) belum berstatus APPROVED QC!")

        idx_hapus = set()
        diambil_p = 0
        diambil_slot_pec = 0
        app_qc_ref = ""

        for idx_s in idx_kandidat:
            slot_d = rak_ini[idx_s]
            if not app_qc_ref:
                app_qc_ref = clean_str(slot_d.get("Approval QC"))

            ket_s = clean_str(slot_d.get("Keterangan")).lower()
            is_pec = "pecahan" in ket_s or "eceran" in ket_s

            if not is_pec and diambil_p < turun_p:
                idx_hapus.add(idx_s)
                diambil_p += 1
            elif is_pec and turun_d > 0 and diambil_slot_pec < 1:
                idx_hapus.add(idx_s)
                diambil_slot_pec += 1

        if diambil_p < turun_p:
            raise ValueError(
                f"Stok palet ter-plot di Rak {k_id}-{b_num} (Lot {lot}) berubah ({diambil_p}P tersedia vs {turun_p}P). Silakan Sinkronkan Stok."
            )

        updated_rak_ini = []
        for idx_s, slot_d in enumerate(rak_ini):
            if idx_s in idx_hapus:
                continue
            if (
                clean_str(slot_d.get("Part Number")).upper() == pn
                and clean_str(slot_d.get("Lot Number")) == lot
                and clean_str(slot_d.get("Jadwal Kirim")) == jk_target
                and clean_str(slot_d.get("Target Penerima")) == tp_target
            ):
                slot_d["Jadwal Kirim"] = ""
                slot_d["Target Penerima"] = ""
            updated_rak_ini.append(slot_d)

        for i_pos, d in enumerate(updated_rak_ini, 1):
            d["Pallet Ke"] = float(i_pos)

        work_asal[k_id] = rak_lain + updated_rak_ini

        cat_stg = f"Customer: {tp_target} ({jk_target}) | Dari: {k_id}-{b_num} | Op: {operator_name}"
        if catatan_umum.strip():
            cat_stg += f" | {catatan_umum.strip()}"

        for _ in range(turun_p):
            pallets_ke_staging.append(
                {
                    "Kolom": "S",
                    "Baris": 1.0,
                    "Nama Produk": nama,
                    "Part Number": pn,
                    "Lot Number": lot,
                    "Keterangan": "Utuh",
                    "Catatan": cat_stg,
                    "Approval QC": app_qc_ref,
                    "Jadwal Kirim": jk_target,
                    "Target Penerima": tp_target,
                    "Pallet Ke": 0.0,
                }
            )
        if turun_d > 0:
            pallets_ke_staging.append(
                {
                    "Kolom": "S",
                    "Baris": 1.0,
                    "Nama Produk": nama,
                    "Part Number": pn,
                    "Lot Number": lot,
                    "Keterangan": f"Pecahan: {turun_d} {satuan}",
                    "Catatan": cat_stg,
                    "Approval QC": app_qc_ref,
                    "Jadwal Kirim": jk_target,
                    "Target Penerima": tp_target,
                    "Pallet Ke": 0.0,
                }
            )

        ringkasan_eksekusi.append(
            {
                "Kolom": k_id,
                "Baris": b_num,
                "Part Number": pn,
                "Nama Produk": nama,
                "Lot Number": lot,
                "Satuan": satuan,
                "Turun_P": turun_p,
                "Turun_D": turun_d,
            }
        )

    gabungan_s1 = staging_s1 + pallets_ke_staging
    for i_pos, d in enumerate(gabungan_s1, 1):
        d["Pallet Ke"] = float(i_pos)

    for k_id in kolom_asal_list:
        _sync_kolom_to_supabase(k_id, work_asal[k_id])

    _sync_kolom_to_supabase("S", staging_lain + gabungan_s1)
    return backup_dict, ringkasan_eksekusi


# ==========================================================
# 9. KOMPONEN UI TERPADU (CSS, HEADER, LOGIN, PRODUK, INBOUND, WIP)
# ==========================================================
def inject_custom_css(accent_color: str = "#10B981", accent_border: str = "#059669", accent_bg_rgba: str = "rgba(16, 185, 129, 0.1)"):
    st.markdown(
        f"""
        <style>
            #MainMenu {{visibility: hidden;}}
            header {{visibility: hidden;}}
            footer {{visibility: hidden;}}

            .stButton>button {{
                min-height: 70px;
                height: auto;
                font-size: 16px;
                font-weight: 600;
                border-radius: 12px;
                white-space: pre-wrap;
                word-wrap: break-word;
                text-align: left;
                padding: 14px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
                transition: all 0.15s ease-in-out;
            }}

            button[kind="primary"] {{
                background-color: {accent_color} !important;
                border-color: {accent_border} !important;
                color: white !important;
                font-size: 18px !important;
                text-align: center !important;
            }}

            .btn-undo > button {{
                background-color: #DC2626 !important;
                border-color: #EF4444 !important;
                color: white !important;
                text-align: center !important;
            }}

            .btn-center > button {{
                text-align: center !important;
                font-size: 19px !important;
            }}

            .btn-header-action > button {{
                min-height: 58px !important;
                padding: 8px 10px !important;
                font-size: 13px !important;
                text-align: center !important;
            }}

            .btn-compact > button {{
                min-height: 44px !important;
                padding: 8px 12px !important;
                font-size: 14px !important;
                text-align: center !important;
            }}

            .progress-container {{
                font-size: 13px;
                font-weight: 500;
                margin-bottom: 15px;
                padding-bottom: 10px;
                border-bottom: 1px solid var(--border-color);
                text-align: center;
            }}
            .step-active {{ color: {accent_color}; font-weight: bold; background: {accent_bg_rgba}; padding: 4px 8px; border-radius: 6px; }}
            .step-inactive {{ color: var(--text-color); opacity: 0.6; }}

            .db-info-box {{
                background-color: var(--secondary-background-color);
                border: 1px solid var(--border-color);
                padding: 10px 14px;
                border-radius: 10px;
                margin-bottom: 10px;
            }}

            .cek-fisik-box {{
                background-color: var(--secondary-background-color);
                border-left: 5px solid {accent_color};
                padding: 12px 14px;
                border-radius: 8px;
                margin: 10px 0;
            }}

            .btn-gramasi > button {{ font-size: 20px !important; text-align: center !important; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_progress_bar(current_slide: int, mode_transaksi: str):
    config_steps = {
        "STAGING": (["Pilih Rak", "Produk & Lot", "Outbound"], {1: 1, 2: 2, 3: 3}),
        "QC": (["Jadwal & Customer", "Approval Lot"], {1: 1, 2: 2}),
        "OUTBOUND_PLOT": (["Jadwal & Customer", "Turun ke Staging", "Selesai"], {1: 1, 2: 2, 3: 3}),
        "OUTBOUND_QC": (["Kategori", "Gramasi", "Item", "Rak Approved", "Staging"], {2: 1, 3: 2, 4: 3, 5: 4, 6: 5}),
        "INBOUND": (["Aksi", "Kategori", "Item", "Input", "Lokasi", "Selesai"], {1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}),
        "INBOUND_SISA": (["Aksi", "Kategori", "Item", "Input", "Lokasi", "Selesai"], {1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}),
        "WIP": (["Aksi", "Kategori", "Item", "Lokasi", "Selesai"], {1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 5}),
        "OUTBOUND": (["Aksi", "Kategori", "Item", "Lokasi", "Selesai"], {1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 5}),
    }
    steps, idx_map = config_steps.get(
        mode_transaksi,
        (["Aksi", "Kategori", "Item", "Proses", "Selesai"], {1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 5, 7: 5}),
    )

    curr_step = idx_map.get(current_slide, 1)
    parts = [
        f"<span class='{'step-active' if i == curr_step else 'step-inactive'}'>{s}</span>"
        for i, s in enumerate(steps, 1)
    ]
    st.markdown(
        "<div class='progress-container'>" + " <span style='opacity: 0.5;'>➔</span> ".join(parts) + "</div>",
        unsafe_allow_html=True,
    )


def render_header_dan_undo(app_title, accent_color, prefix_app, mode_badge="", reset_keys=None, show_undo=True):
    col_hdr, col_sync, col_op = st.columns([2.6, 1.2, 1.2])
    with col_hdr:
        st.markdown(
            f"""
            <div class="db-info-box">
                <div style="font-size: 14px; font-weight: bold; color: {accent_color};">
                    🧭 {app_title}
                </div>
                <div style="font-size: 12px; opacity: 0.85; margin-top: 2px;">
                    👤 <strong>{st.session_state.operator}</strong>  |  📡 {NAMA_DATABASE}{mode_badge}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_sync:
        st.markdown('<div class="btn-header-action">', unsafe_allow_html=True)
        if st.button("🔄 Sinkronkan\nStok", use_container_width=True, help="Muat ulang data rak & status QC terbaru"):
            get_all_racking_data.clear()
            get_qc_approval_map.clear()
            st.toast("Data rak & QC berhasil disinkronkan!", icon="🔄")
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    with col_op:
        st.markdown('<div class="btn-header-action">', unsafe_allow_html=True)
        if st.button("👤 Ganti\nOperator", use_container_width=True):
            st.session_state.operator = None
            st.session_state.undo_record = None
            st.session_state.inbound_draft = None
            st.session_state.outbound_draft = None
            if reset_keys:
                for rk in reset_keys:
                    st.session_state[rk] = None
            st.session_state.slide = 0
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    if not show_undo:
        return

    undo_rec = st.session_state.get("undo_record")
    if undo_rec and undo_rec.get("status") == "DONE":
        masih_berlaku = True
        created_dt = _parse_dt_wib(undo_rec.get("created_at"))
        if created_dt is not None and (get_waktu_jkt() - created_dt) > timedelta(minutes=30):
            masih_berlaku = False
            st.session_state.undo_record = None

        if masih_berlaku:
            mode_undo = undo_rec.get("mode", "TRANSAKSI")
            created_txt = str(undo_rec.get("created_at", ""))
            jam_txt = created_txt[-8:] if len(created_txt) >= 8 else created_txt
            with st.container(border=True):
                st.success(f"✅ **BERHASIL ({mode_undo}):** {undo_rec.get('action_msg', '')}")
                st.caption(f"⏱️ Opsi Undo tersedia selama 30 menit (Input pukul {jam_txt} WIB)")
                c_u1, c_u2 = st.columns(2)
                with c_u1:
                    st.markdown('<div class="btn-undo">', unsafe_allow_html=True)
                    if st.button("⏪ BATALKAN (UNDO)", use_container_width=True):
                        with st.spinner("Memulihkan posisi stok..."):
                            kol_terdampak = []
                            for kol, dat in undo_rec.get("undo_dict", {}).items():
                                _sync_kolom_to_supabase(kol, dat)
                                kol_terdampak.append(kol)

                            get_all_racking_data.clear()
                            tutup_undo_firestore(prefix_app, st.session_state.operator, "UNDONE")
                            catat_log_mutasi(
                                f"PEMBATALAN (UNDO {mode_undo})",
                                ",".join(kol_terdampak),
                                "ALL",
                                "Koreksi Sistem",
                                "-",
                                "-",
                                0,
                                0,
                                0,
                                f"Dibatalkan oleh: {st.session_state.operator}",
                            )
                            pesan_undo = (
                                f"⏪ <b>UNDO {mode_undo} - RACK NAVIGATOR</b>\n"
                                f"━━━━━━━━━━━━━━━━━━\n"
                                f"📍 <b>Kolom Dipulihkan:</b> {', '.join(kol_terdampak)}\n"
                                f"👤 <b>Operator:</b> {escape_html(st.session_state.operator)}\n"
                                f"🕒 <b>Waktu:</b> {get_waktu_jkt().strftime('%H:%M:%S WIB')}"
                            )
                            kirim_notif_telegram(pesan_undo)
                            st.rerun()
                    st.markdown("</div>", unsafe_allow_html=True)
                with c_u2:
                    if st.button("TUTUP / LANJUT", use_container_width=True):
                        tutup_undo_firestore(prefix_app, st.session_state.operator, "CLOSED")
                        st.rerun()

    elif undo_rec and undo_rec.get("status") == "UNDONE":
        with st.container(border=True):
            st.warning("⏪ **Transaksi dibatalkan.** Stok kembali ke posisi semula.")
            if st.button("TUTUP PESAN", use_container_width=True):
                st.session_state.undo_record = None
                st.rerun()


def get_daftar_akun_operator() -> dict:
    try:
        raw_auth = dict(st.secrets["auth_operators"])
        return {str(k).strip(): str(v).strip() for k, v in raw_auth.items() if str(k).strip()}
    except Exception:
        return {}


def verifikasi_login_operator(username_input: str, password_input: str):
    akun_map = get_daftar_akun_operator()
    if not akun_map:
        return False, "Konfigurasi [auth_operators] belum diatur di Streamlit Secrets."

    u_clean = str(username_input).strip()
    p_clean = str(password_input).strip()
    if not u_clean or not p_clean:
        return False, "Username dan PIN/Password wajib diisi."

    for nama_terdaftar, pass_terdaftar in akun_map.items():
        if nama_terdaftar.lower() == u_clean.lower():
            if hmac.compare_digest(p_clean, pass_terdaftar):
                return True, nama_terdaftar
            return False, "PIN / Password salah!"

    return False, f"Username '{u_clean}' tidak terdaftar."


def _selesaikan_login(prefix_app: str, nama_op: str, slide_tujuan: int, default_mode, load_draft_type, sel_key: str):
    st.session_state.operator = nama_op
    if default_mode:
        st.session_state.mode_transaksi = default_mode
    muat_undo_firestore(prefix_app, nama_op)
    if load_draft_type == "INBOUND":
        muat_draft_inbound(prefix_app, nama_op)
    elif load_draft_type == "OUTBOUND":
        muat_draft_outbound(prefix_app, nama_op)
    st.session_state[sel_key] = None
    st.session_state.slide = slide_tujuan
    st.rerun()


def render_login_operator(
    app_title, subtitle, accent_color, prefix_app, operator_list,
    slide_tujuan: int = 1, default_mode=None, load_draft_type=None
):
    st.markdown(
        f"""
        <div class="db-info-box" style="text-align: center;">
            <div style="font-size: 18px; font-weight: bold; color: {accent_color};">
                🔐 {app_title}
            </div>
            <div style="font-size: 13px; opacity: 0.85; margin-top: 4px;">
                {subtitle}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    sel_key = f"_login_selected_user_{prefix_app}"
    if sel_key not in st.session_state:
        st.session_state[sel_key] = None

    if st.session_state[sel_key] is None:
        st.markdown("**1. Pilih Nama Operator Bertugas:**")
        st.markdown('<div class="btn-center">', unsafe_allow_html=True)
        for i in range(0, len(operator_list), 2):
            cols = st.columns(2)
            for j in range(2):
                if i + j < len(operator_list):
                    op_now = operator_list[i + j]
                    with cols[j]:
                        if st.button(f"👤 {op_now}", key=f"sel_op_{prefix_app}_{op_now}", use_container_width=True):
                            st.session_state[sel_key] = op_now
                            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

        st.write("")
        with st.expander("➕ Login Username Manual / Operator Pengganti"):
            with st.form(key=f"form_manual_login_{prefix_app}", clear_on_submit=False):
                u_man = st.text_input("Username", placeholder="Cth: Operator_Pengganti")
                p_man = st.text_input("PIN / Password", type="password", placeholder="Masukkan PIN/Password...")
                if st.form_submit_button("🔓 MASUK", type="primary", use_container_width=True):
                    ok, res = verifikasi_login_operator(u_man, p_man)
                    if ok:
                        _selesaikan_login(prefix_app, res, slide_tujuan, default_mode, load_draft_type, sel_key)
                    else:
                        st.error(f"❌ {res}")
    else:
        nama_dipilih = st.session_state[sel_key]
        if st.button("🔙 GANTI NAMA OPERATOR", key=f"back_sel_op_{prefix_app}"):
            st.session_state[sel_key] = None
            st.rerun()

        with st.container(border=True):
            st.markdown(
                f"<div style='text-align:center;font-size:17px;margin-bottom:8px;'>"
                f"Login sebagai: <strong style='color:{accent_color};'>👤 {escape_html(nama_dipilih)}</strong>"
                f"</div>",
                unsafe_allow_html=True,
            )
            with st.form(key=f"form_pin_login_{prefix_app}", clear_on_submit=True):
                pin_input = st.text_input("Masukkan PIN / Password:", type="password", placeholder="••••")
                if st.form_submit_button("🔓 VERIFIKASI & MASUK", type="primary", use_container_width=True):
                    ok, res = verifikasi_login_operator(nama_dipilih, pin_input)
                    if ok:
                        _selesaikan_login(prefix_app, res, slide_tujuan, default_mode, load_draft_type, sel_key)
                    else:
                        st.error(f"❌ {res}")


def render_pemilihan_produk(df_master: pd.DataFrame, allow_back_to_slide1: bool = True):
    df_aktif = get_df_master_terfilter(df_master)

    if st.session_state.slide == 2:
        if allow_back_to_slide1 and st.button("🔙 GANTI AKSI"):
            st.session_state.mode_transaksi = None
            st.session_state.slide = 1
            st.rerun()

        st.markdown("### Pilih Kategori Produk")
        if st.session_state.get("mode_transaksi") == "WIP":
            st.caption("🏭 *Menampilkan khusus item bahan WIP (Seluruh Preform, Cup Color, & Cup Mountea/JDB).*")

        urutan_kategori = ["PREFORM", "CUP NATURAL", "CUP COLOR", "CUP PRINTING", "CAP"]
        kategori_ada = [k for k in urutan_kategori if k in set(df_aktif["Kategori"].unique())] or urutan_kategori

        for i in range(0, len(kategori_ada), 2):
            cols = st.columns(2)
            for j in range(2):
                if i + j < len(kategori_ada):
                    kat = kategori_ada[i + j]
                    lbl = "CAP (TUTUP)" if kat == "CAP" else kat
                    with cols[j]:
                        if st.button(lbl, key=f"kat_{kat}", use_container_width=True):
                            st.session_state.kategori = kat
                            st.session_state.slide = 3
                            st.rerun()

    elif st.session_state.slide == 3:
        if st.button("🔙 KEMBALI"):
            st.session_state.slide = 2
            st.rerun()
        st.markdown(f"### Gramasi: **{st.session_state.kategori}**")
        list_gramasi = sorted(df_aktif[df_aktif["Kategori"] == st.session_state.kategori]["Gramasi"].unique())

        st.markdown('<div class="btn-gramasi">', unsafe_allow_html=True)
        for i in range(0, len(list_gramasi), 2):
            cols = st.columns(2)
            for j in range(2):
                if i + j < len(list_gramasi):
                    with cols[j]:
                        if st.button(f"{list_gramasi[i + j]}", key=f"g_{i + j}", use_container_width=True):
                            st.session_state.gramasi = list_gramasi[i + j]
                            st.session_state.slide = 4
                            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    elif st.session_state.slide == 4:
        if st.button("🔙 KEMBALI"):
            st.session_state.slide = 3
            st.rerun()
        st.markdown("### Pilih Item Spesifik")
        st.caption(f"{st.session_state.kategori} | {st.session_state.gramasi}")

        df_item = df_aktif[(df_aktif["Kategori"] == st.session_state.kategori) & (df_aktif["Gramasi"] == st.session_state.gramasi)]
        kemasan_badge = {"D": "[📦 DUS] ", "G": "[📦 DUS] ", "C": "[👝 SAK] ", "A": "[🗃️ NICTAINER] "}
        for idx, row in df_item.iterrows():
            nama_brg = row["Nama Barang"]
            part_num = str(row["Part Number"]).strip().upper()
            tipe_kemasan = kemasan_badge.get(part_num[-1], "") if part_num else ""
            if st.button(f"{tipe_kemasan}{format_nama_warna_bold(nama_brg)}", use_container_width=True, key=f"btn_item_{idx}"):
                st.session_state.item = nama_brg
                st.session_state.pilih_part = part_num
                st.session_state.slide = 5
                st.rerun()


def render_alur_inbound_terpadu(
    prefix_app, judul_inbound, default_sisi_masuk: str = "BELAKANG",
    jenis_log: str = "INBOUND (Remote)", judul_tele: str = "INBOUND - RACK NAVIGATOR",
    mode_undo_label: str = "INBOUND", reset_keys=None
):
    if st.session_state.slide == 5:
        if st.button("🔙 GANTI ITEM"):
            st.session_state.slide = 4 if st.session_state.get("gramasi") else 2
            st.rerun()

        st.markdown(f"### {judul_inbound}")
        st.info(format_nama_warna_bold(st.session_state.item))
        if str(st.session_state.pilih_part).strip().upper() in PART_KHUSUS_TOP_TIER:
            st.caption("🏗️ *Item Zona Khusus: Utama di Rak F1–F16, jika penuh dialihkan ke G17–G28.*")

        satuan_otomatis = get_satuan_kemasan(st.session_state.pilih_part)
        opsi_sisi = [
            "BELAKANG (Normal - Lanjut Antrean Belakang)",
            "DEPAN (Push-Back - Dorong Stok Lama ke Dalam)",
        ]
        idx_default_sisi = 1 if st.session_state.get("in_sisi_masuk", default_sisi_masuk) == "DEPAN" else 0

        with st.container(border=True):
            st.markdown("**🏷️ Susun Nomor Lot Produksi:**")
            mode_lot_manual = st.checkbox("✏️ Input Manual (Jika format lot non-standar)", value=False, key=f"chk_lot_man_{prefix_app}")

            if mode_lot_manual:
                in_lot = st.text_input("Nomor Lot Manual", value=st.session_state.get("in_lot") or "", placeholder="Cth: 1A1231126").strip().upper()
            else:
                lot_awal = str(st.session_state.get("in_lot") or "").strip().upper()
                opsi_mesin, opsi_grup, opsi_shift = ["1", "2", "3", "4", "5"], ["A", "B", "C", "D"], ["1", "2", "3"]
                idx_m, idx_g, idx_s = 0, 0, 0
                tgl_default = get_waktu_jkt().date()

                if len(lot_awal) == 9:
                    if lot_awal[0] in opsi_mesin:
                        idx_m = opsi_mesin.index(lot_awal[0])
                    if lot_awal[1] in opsi_grup:
                        idx_g = opsi_grup.index(lot_awal[1])
                    if lot_awal[2] in opsi_shift:
                        idx_s = opsi_shift.index(lot_awal[2])
                    try:
                        tgl_default = datetime.strptime(lot_awal[3:], "%d%m%y").date()
                    except Exception:
                        pass

                c_l1, c_l2, c_l3, c_l4 = st.columns([1, 1, 1, 1.8])
                with c_l1:
                    sel_mesin = st.selectbox("Mesin", opsi_mesin, index=idx_m, key=f"lot_m_{prefix_app}")
                with c_l2:
                    sel_grup = st.selectbox("Grup", opsi_grup, index=idx_g, key=f"lot_g_{prefix_app}")
                with c_l3:
                    sel_shift = st.selectbox("Shift", opsi_shift, index=idx_s, key=f"lot_s_{prefix_app}")
                with c_l4:
                    sel_tgl = st.date_input("Tanggal", value=tgl_default, format="DD/MM/YYYY", key=f"lot_d_{prefix_app}")

                in_lot = f"{sel_mesin}{sel_grup}{sel_shift}{sel_tgl.strftime('%d%m%y')}"
                st.markdown(
                    f"<div style='background:rgba(16,185,129,0.12); border:1px solid #10B981; "
                    f"padding:8px 12px; border-radius:8px; text-align:center; margin-top:4px;'>"
                    f"Hasil Nomor Lot: <span style='font-size:20px; font-weight:bold; letter-spacing:1.5px;'>{in_lot}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        with st.container(border=True):
            c_q1, c_q2 = st.columns(2)
            with c_q1:
                in_qty_p = st.number_input(
                    "Berapa Pallet Utuh?",
                    min_value=0,
                    value=st.session_state.get("in_qty_p") if st.session_state.get("in_qty_p") is not None else 1,
                )
            with c_q2:
                in_qty_c = st.number_input(
                    f"Qty Pecahan ({satuan_otomatis})",
                    min_value=0,
                    value=st.session_state.get("in_qty_c") if st.session_state.get("in_qty_c") is not None else 0,
                )
            sisi_pilihan = st.radio("🚪 Pintu Masuk Shuttle Racking:", options=opsi_sisi, index=idx_default_sisi)

        st.write("")
        if st.button("🔍 HITUNG ALOKASI RAK OTOMATIS", type="primary", use_container_width=True):
            if not in_lot.strip():
                st.error("⚠️ Nomor Lot wajib diisi!")
            elif in_qty_p == 0 and in_qty_c == 0:
                st.error("⚠️ Masukkan minimal 1 kuantitas!")
            else:
                st.session_state.in_lot = in_lot.strip().upper()
                st.session_state.in_qty_p = in_qty_p
                st.session_state.in_qty_c = in_qty_c
                st.session_state.in_tipe_c = satuan_otomatis
                st.session_state.in_sisi_masuk = "DEPAN" if sisi_pilihan.startswith("DEPAN") else "BELAKANG"
                st.session_state.in_rak_skip = []
                st.session_state.slide = 6
                st.rerun()

    elif st.session_state.slide == 6:
        if st.button("🔙 UBAH INPUT"):
            st.session_state.in_rak_skip = []
            st.session_state.slide = 5
            st.rerun()

        if st.session_state.get("in_rak_skip") is None:
            st.session_state.in_rak_skip = []

        sisi_masuk = st.session_state.get("in_sisi_masuk", default_sisi_masuk)
        sisi_label = "Depan" if sisi_masuk == "DEPAN" else "Belakang"
        sisi_color = "#3B82F6" if sisi_masuk == "DEPAN" else "#10B981"
        part_aktif = str(st.session_state.pilih_part).strip().upper()
        lot_aktif = str(st.session_state.in_lot or "").strip()
        qty_p_in = int(st.session_state.in_qty_p or 0)
        qty_c_in = int(st.session_state.in_qty_c or 0)
        satuan_c = st.session_state.in_tipe_c
        total_slot_butuh = qty_p_in + (1 if qty_c_in > 0 else 0)

        st.markdown("### Rekomendasi Lokasi Rak")
        teks_pec_hdr = f" + {qty_c_in} {satuan_c}" if qty_c_in > 0 else ""
        with st.container(border=True):
            st.markdown(f"📦 {format_nama_warna_bold(st.session_state.item)}")
            st.markdown(
                f"<div style='font-size:13px; opacity:0.9;'>Lot: <strong>{lot_aktif}</strong>  |  "
                f"Total: <strong>{qty_p_in} Pallet{teks_pec_hdr}</strong>  |  "
                f"Masuk: <span style='color:{sisi_color}; font-weight:bold;'>{sisi_label.upper()}</span></div>",
                unsafe_allow_html=True,
            )

        with st.spinner("📐 Mencari lorong rak terbaik..."):
            semua_data = get_all_racking_data()
            df_kandidat = hitung_kandidat_rak_inbound(
                semua_data, st.session_state.item, part_aktif, sisi_masuk=sisi_masuk, target_lot=lot_aktif
            )
            in_is_sac = part_aktif.endswith("C")

            if df_kandidat.empty:
                st.error(
                    "❌ Seluruh rak zona khusus JDO (F1–F16 & G17–G28) penuh!"
                    if part_aktif in PART_KHUSUS_TOP_TIER
                    else "❌ Seluruh rak penuh atau tidak memenuhi syarat posisi item!"
                )
            else:
                if in_is_sac:
                    st.caption("🛡️ *Rak Sak sisa ≤ 1 slot otomatis dilewati (toleransi panjang fisik).*")

                rak_skip_list = st.session_state.in_rak_skip
                if rak_skip_list:
                    df_kandidat = df_kandidat[~df_kandidat["Loc"].isin(rak_skip_list)].reset_index(drop=True)

                total_kapasitas_gudang = int(df_kandidat["SisaSlot"].sum()) if not df_kandidat.empty else 0
                if total_kapasitas_gudang < total_slot_butuh:
                    zona_teks = "zona khusus JDO (F1–F16 & G17–G28)" if part_aktif in PART_KHUSUS_TOP_TIER else "gudang"
                    st.error(
                        f"❌ Total sisa kapasitas {zona_teks} ({total_kapasitas_gudang} palet) tidak mencukupi untuk menampung {total_slot_butuh} palet!"
                    )
                else:
                    rencana_alokasi = []
                    sisa_utuh, sisa_pecahan = qty_p_in, qty_c_in

                    for _, row in df_kandidat.iterrows():
                        if sisa_utuh == 0 and sisa_pecahan == 0:
                            break
                        kap = int(row["SisaSlot"])
                        ambil_p = min(sisa_utuh, kap)
                        sisa_utuh -= ambil_p
                        kap_sisa = kap - ambil_p

                        ambil_c = sisa_pecahan if (sisa_utuh == 0 and sisa_pecahan > 0 and kap_sisa >= 1) else 0
                        if ambil_c > 0:
                            sisa_pecahan = 0

                        if ambil_p > 0 or ambil_c > 0:
                            rencana_alokasi.append(
                                {
                                    "Kolom": row["Kolom"],
                                    "Baris": int(row["Baris"]),
                                    "qty_p": ambil_p,
                                    "qty_c": ambil_c,
                                    "Status": row["Status"],
                                    "IsiEksisting": row["IsiEksisting"],
                                    "SisaSlot": int(row["SisaSlot"]),
                                }
                            )

                    draft_sig = f"S6|{st.session_state.item}|{lot_aktif}|{qty_p_in}|{qty_c_in}|{sisi_masuk}|" + ",".join(
                        f"{x['Kolom']}-{x['Baris']}:{x['qty_p']}:{x['qty_c']}" for x in rencana_alokasi
                    )
                    if st.session_state.get("_last_draft_sig") != draft_sig:
                        simpan_draft_inbound(
                            prefix_app,
                            st.session_state.operator,
                            {
                                "kategori": st.session_state.get("kategori") or "",
                                "gramasi": st.session_state.get("gramasi") or "",
                                "item": st.session_state.item,
                                "pilih_part": part_aktif,
                                "in_lot": lot_aktif,
                                "in_qty_p": qty_p_in,
                                "in_qty_c": qty_c_in,
                                "in_tipe_c": satuan_c,
                                "in_sisi_masuk": sisi_masuk,
                                "in_rak_skip": rak_skip_list,
                                "alokasi_terpilih": rencana_alokasi,
                                "last_slide": 6,
                            },
                        )
                        st.session_state["_last_draft_sig"] = draft_sig

                    st.info("💾 **Draft tersimpan otomatis.** Layar HP/tablet aman dimatikan selama proses muat ke rak.")

                    for i_alok, al in enumerate(rencana_alokasi, 1):
                        loc_str = f"{al['Kolom']}-{al['Baris']}"
                        teks_pec = f" + {al['qty_c']} {satuan_c}" if al["qty_c"] > 0 else ""
                        slot_dipakai = al["qty_p"] + (1 if al["qty_c"] > 0 else 0)
                        sisa_setelah = al["SisaSlot"] - slot_dipakai
                        instruksi_baris = (
                            f"Isi dari {sisi_label} sampai penuh ± {al['qty_p']} Pallet{teks_pec}"
                            if sisa_setelah == 0
                            else f"Isi dari {sisi_label} ± {al['qty_p']} Pallet{teks_pec}"
                        )

                        with st.container(border=True):
                            c_info, c_skip = st.columns([3.3, 1.3])
                            with c_info:
                                st.markdown(f"<div style='font-size:20px; font-weight:bold;'>{i_alok}. 📍 RAK {loc_str}</div>", unsafe_allow_html=True)
                                st.markdown(f"➡️ **{instruksi_baris}**")
                                st.caption(f"Kondisi rak: {al['IsiEksisting']}")
                            with c_skip:
                                st.markdown('<div class="btn-compact">', unsafe_allow_html=True)
                                if st.button("🚫 Lewati", key=f"skip_{prefix_app}_{loc_str}_{i_alok}", use_container_width=True):
                                    st.session_state.in_rak_skip.append(loc_str)
                                    st.rerun()
                                st.markdown("</div>", unsafe_allow_html=True)

                if rak_skip_list:
                    c_sk1, c_sk2 = st.columns([3.2, 1.4])
                    with c_sk1:
                        st.caption(f"🚫 Dilewati: **{', '.join(rak_skip_list)}**")
                    with c_sk2:
                        st.markdown('<div class="btn-compact">', unsafe_allow_html=True)
                        if st.button("↩️ Reset", key=f"reset_skip_{prefix_app}", use_container_width=True):
                            st.session_state.in_rak_skip = []
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)

                if total_kapasitas_gudang >= total_slot_butuh:
                    st.write("")
                    if st.button("✅ LANJUT KE KONFIRMASI AKTUAL", type="primary", use_container_width=True):
                        st.session_state.alokasi_terpilih = rencana_alokasi
                        st.session_state.slide = 7
                        st.rerun()

    elif st.session_state.slide == 7:
        if st.button("🔙 BATAL (UBAH ALOKASI)"):
            st.session_state.slide = 6
            st.rerun()

        alokasi = st.session_state.alokasi_terpilih or []
        sisi_masuk = st.session_state.get("in_sisi_masuk", default_sisi_masuk)
        sisi_str = "DEPAN (Push-Back)" if sisi_masuk == "DEPAN" else "BELAKANG (Normal)"
        target_p_awal = int(st.session_state.in_qty_p or 0)
        target_c_awal = int(st.session_state.in_qty_c or 0)
        satuan_c = st.session_state.in_tipe_c
        in_is_sac = str(st.session_state.pilih_part).strip().upper().endswith("C")
        lot_aktif = str(st.session_state.in_lot or "").strip()

        st.markdown(f"### Konfirmasi Aktual {judul_inbound}")
        with st.container(border=True):
            st.markdown(format_nama_warna_bold(st.session_state.item))
            st.markdown(f"**Lot:** `{lot_aktif}`  |  **Pintu:** {sisi_str}")
            teks_pec_target = f", {target_c_awal} {satuan_c}" if target_c_awal > 0 else ""
            st.caption(f"Rencana Awal: {target_p_awal} Pallet Utuh{teks_pec_target} *(Ubah angka di bawah jika aktual lapangan berbeda)*")

        st.markdown("**Penempatan Aktual per Lorong Rak:**")
        alokasi_aktual = []
        indeks_hapus = None

        for idx_a, al in enumerate(alokasi):
            b_int = int(al["Baris"])
            max_sac, max_dus = _kapasitas_baris(b_int)
            max_fisik_baris = max_sac if in_is_sac else max_dus

            with st.container(border=True):
                c_hdr1, c_hdr2 = st.columns([3.5, 1.2])
                with c_hdr1:
                    st.markdown(f"📍 **Rak {al['Kolom']}-{b_int}**  |  <span style='font-size:12px;'>{al['Status']}</span>", unsafe_allow_html=True)
                with c_hdr2:
                    if len(alokasi) > 1:
                        st.markdown('<div class="btn-compact">', unsafe_allow_html=True)
                        if st.button("🗑️ Hapus", key=f"del_alok_{prefix_app}_{idx_a}", use_container_width=True):
                            indeks_hapus = idx_a
                        st.markdown("</div>", unsafe_allow_html=True)

                c_in1, c_in2 = st.columns(2)
                with c_in1:
                    val_p = st.number_input(
                        "Pallet Utuh Aktual", min_value=0, max_value=max_fisik_baris, step=1,
                        value=int(al["qty_p"]), key=f"akt_p_{prefix_app}_{al['Kolom']}_{b_int}_{idx_a}",
                    )
                with c_in2:
                    val_c = st.number_input(
                        f"Pecahan Aktual ({satuan_c})", min_value=0, step=1,
                        value=int(al["qty_c"]), key=f"akt_c_{prefix_app}_{al['Kolom']}_{b_int}_{idx_a}",
                    )
                alokasi_aktual.append({"Kolom": al["Kolom"], "Baris": b_int, "qty_p": val_p, "qty_c": val_c, "Status": al["Status"]})

        if indeks_hapus is not None:
            alokasi.pop(indeks_hapus)
            st.session_state.alokasi_terpilih = alokasi
            st.rerun()

        st.session_state.alokasi_terpilih = alokasi_aktual

        draft_sig_s7 = f"S7|{st.session_state.item}|{lot_aktif}|{target_p_awal}|{target_c_awal}|{sisi_masuk}|" + ",".join(
            f"{x['Kolom']}-{x['Baris']}:{x['qty_p']}:{x['qty_c']}" for x in alokasi_aktual
        )
        if st.session_state.get("_last_draft_sig") != draft_sig_s7:
            simpan_draft_inbound(
                prefix_app,
                st.session_state.operator,
                {
                    "kategori": st.session_state.get("kategori") or "",
                    "gramasi": st.session_state.get("gramasi") or "",
                    "item": st.session_state.item,
                    "pilih_part": st.session_state.pilih_part,
                    "in_lot": lot_aktif,
                    "in_qty_p": target_p_awal,
                    "in_qty_c": target_c_awal,
                    "in_tipe_c": satuan_c,
                    "in_sisi_masuk": sisi_masuk,
                    "in_rak_skip": st.session_state.get("in_rak_skip") or [],
                    "alokasi_terpilih": alokasi_aktual,
                    "last_slide": 7,
                },
            )
            st.session_state["_last_draft_sig"] = draft_sig_s7

        total_p_aktual = sum(x["qty_p"] for x in alokasi_aktual)
        total_c_aktual = sum(x["qty_c"] for x in alokasi_aktual)
        sisa_p_belum = target_p_awal - total_p_aktual
        sisa_c_belum = target_c_awal - total_c_aktual

        if sisa_p_belum > 0 or sisa_c_belum > 0:
            st.warning(
                f"⚠️ **Ada sisa belum ditempatkan:** **{max(0, sisa_p_belum)} Pallet**"
                + (f", **{max(0, sisa_c_belum)} {satuan_c}**" if sisa_c_belum > 0 else "")
                + " (karena rak sebelumnya sudah penuh di fisik)."
            )
            semua_data = get_all_racking_data()
            df_kandidat_sisa = hitung_kandidat_rak_inbound(
                semua_data, st.session_state.item, st.session_state.pilih_part, sisi_masuk=sisi_masuk, target_lot=lot_aktif
            )
            rak_sudah_dipakai = {f"{x['Kolom']}-{x['Baris']}" for x in alokasi_aktual}

            if not df_kandidat_sisa.empty:
                df_opsi_tambah = df_kandidat_sisa[~df_kandidat_sisa["Loc"].isin(rak_sudah_dipakai)].reset_index(drop=True)
                if not df_opsi_tambah.empty:
                    opsi_label = [f"{r['Loc']} | {r['Status']} (Sisa {r['SisaSlot']} slot)" for _, r in df_opsi_tambah.head(15).iterrows()]
                    c_add1, c_add2 = st.columns([2.6, 1.4])
                    with c_add1:
                        pilih_rak_tambahan = st.selectbox(
                            "Pilih Rak Berikutnya untuk Sisa Palet:",
                            options=opsi_label,
                            label_visibility="collapsed",
                            key=f"sel_add_rak_{prefix_app}_{len(alokasi_aktual)}",
                        )
                    with c_add2:
                        st.markdown('<div class="btn-compact">', unsafe_allow_html=True)
                        if st.button("➕ Tambah Rak", use_container_width=True, key=f"btn_add_rak_{prefix_app}"):
                            row_t = df_opsi_tambah.iloc[opsi_label.index(pilih_rak_tambahan)]
                            alokasi_aktual.append(
                                {
                                    "Kolom": row_t["Kolom"],
                                    "Baris": int(row_t["Baris"]),
                                    "qty_p": max(0, sisa_p_belum),
                                    "qty_c": max(0, sisa_c_belum),
                                    "Status": row_t["Status"],
                                }
                            )
                            st.session_state.alokasi_terpilih = alokasi_aktual
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)
        elif sisa_p_belum < 0 or sisa_c_belum < 0:
            st.info(
                f"ℹ️ **Aktual lebih besar dari rencana awal:** Total terinput **{total_p_aktual} Pallet**"
                + (f", **{total_c_aktual} {satuan_c}**" if total_c_aktual > 0 else "")
                + ". Pastikan angka fisik sudah sesuai sebelum menyimpan."
            )

        st.write("")
        catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Aktual muat 29 palet, 1 pindah ke rak sebelah")
        st.write("")

        alokasi_valid = [x for x in alokasi_aktual if x["qty_p"] > 0 or x["qty_c"] > 0]
        if not alokasi_valid:
            st.error("⚠️ Masukkan minimal 1 kuantitas aktual pada rak tujuan.")
        else:
            if st.button("📥 MASUKKAN KE DATABASE SEKARANG", type="primary", use_container_width=True):
                with st.spinner("Memasukkan data aktual ke rak..."):
                    qc_map = get_qc_approval_map()
                    qc_info_lot = get_status_qc_lot(qc_map, st.session_state.pilih_part, lot_aktif)
                    label_qc_awal = (
                        f"{qc_info_lot['status']} ({qc_info_lot.get('qc_by', '-')})"
                        if qc_info_lot.get("status") in {"APPROVED", "HOLD"}
                        else ""
                    )

                    kolom_unik = sorted(list(set(al["Kolom"] for al in alokasi_valid)))
                    doc_refs_dict = {kol: db.collection(NAMA_DATABASE).document(f"Kolom_{kol}") for kol in kolom_unik}
                    transaction = db.transaction()

                    try:
                        backup_kolom = proses_inbound_multi_transaction(
                            transaction,
                            doc_refs_dict,
                            alokasi_valid,
                            lot_aktif,
                            st.session_state.pilih_part,
                            st.session_state.item,
                            satuan_c,
                            catatan_operator,
                            sisi_masuk=sisi_masuk,
                            approval_qc=label_qc_awal,
                        )

                        teks_keterangan = f"Op: {st.session_state.operator} | Sisi: {sisi_str}"
                        if total_p_aktual != target_p_awal or total_c_aktual != target_c_awal:
                            teks_keterangan += f" | Rencana:{target_p_awal}P -> Aktual:{total_p_aktual}P"
                        if catatan_operator.strip():
                            teks_keterangan += f" | Catatan: {catatan_operator.strip()}"

                        ringkasan_rak = []
                        for al in alokasi_valid:
                            catat_log_mutasi(
                                jenis_log,
                                al["Kolom"],
                                al["Baris"],
                                st.session_state.item,
                                st.session_state.pilih_part,
                                lot_aktif,
                                al["qty_p"],
                                al["qty_c"],
                                0,
                                teks_keterangan,
                            )
                            p_str = f"+{al['qty_c']}{satuan_c}" if al["qty_c"] > 0 else ""
                            ringkasan_rak.append(f"{al['Kolom']}-{al['Baris']} ({al['qty_p']}P{p_str})")

                        lokasi_gabungan = ", ".join(ringkasan_rak)
                        pecahan_tele = f", {total_c_aktual} {satuan_c}" if total_c_aktual > 0 else ""

                        pesan_tele = (
                            f"📥 <b>{judul_tele}</b>\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"📦 <b>Item:</b> {escape_html(st.session_state.item)}\n"
                            f"🏷️ <b>Lot:</b> <code>{escape_html(lot_aktif)}</code>\n"
                            f"📊 <b>Total Aktual:</b> {total_p_aktual} Pallet{pecahan_tele}\n"
                            f"📍 <b>Penempatan:</b> {escape_html(lokasi_gabungan)} ({sisi_str})\n"
                            f"👤 <b>Operator:</b> {escape_html(st.session_state.operator)}\n"
                        )
                        if catatan_operator.strip():
                            pesan_tele += f"📝 <b>Catatan:</b> <i>{escape_html(catatan_operator.strip())}</i>\n"
                        kirim_notif_telegram(pesan_tele)

                        hapus_draft_inbound(prefix_app, st.session_state.operator)
                        st.session_state.pop("_last_draft_sig", None)
                        get_all_racking_data.clear()

                        msg_sukses = f"{total_p_aktual} Pallet{pecahan_tele} masuk ke {lokasi_gabungan} ({sisi_str})."
                        simpan_undo_firestore(prefix_app, st.session_state.operator, backup_kolom, msg_sukses, mode_label=mode_undo_label)

                        st.toast(msg_sukses, icon="✅")
                        keys_clear = [
                            "kategori", "gramasi", "item", "pilih_part", "mode_transaksi",
                            "in_lot", "in_qty_p", "in_qty_c", "in_tipe_c", "in_sisi_masuk", "in_rak_skip", "alokasi_terpilih",
                        ] + (list(reset_keys) if reset_keys else [])
                        for k in set(keys_clear):
                            if k in st.session_state:
                                st.session_state[k] = None
                        st.session_state.slide = 1
                        st.rerun()

                    except ValueError as err:
                        st.error(f"❌ Validasi Kapasitas: {err}")
                    except Exception as err:
                        st.error(f"❌ Kesalahan Sistem: {err}")


def render_alur_pengambilan_terpadu(
    prefix_app, df_master, judul_ambil, label_tombol_eksekusi,
    jenis_log, judul_tele, mode_undo_label, reset_keys
):
    k_pilih = st.session_state.get(reset_keys["kolom"])
    b_pilih = st.session_state.get(reset_keys["baris"])
    lot_pilih = st.session_state.get(reset_keys["lot"])
    init_p = int(st.session_state.get(reset_keys["max_p"]) or 0)
    init_d = int(st.session_state.get(reset_keys["max_d"]) or 0)
    catatan_rak_lama = st.session_state.get(reset_keys["catatan"]) or ""

    row_m = df_master[df_master["Nama Barang"] == st.session_state.item]
    total_pcs_pallet = int(row_m.iloc[0]["Total_Pcs_Pallet"]) if not row_m.empty and int(row_m.iloc[0]["Total_Pcs_Pallet"]) > 0 else 1
    pcs_per_box = int(row_m.iloc[0]["Pcs_Per_Box"]) if not row_m.empty and int(row_m.iloc[0]["Pcs_Per_Box"]) > 0 else 1
    pcs_per_sac = int(row_m.iloc[0]["Pcs_Per_Sac"]) if not row_m.empty and int(row_m.iloc[0]["Pcs_Per_Sac"]) > 0 else 1

    satuan = get_satuan_kemasan(st.session_state.pilih_part)
    pcs_per_pecahan = pcs_per_sac if satuan == "Sak" else pcs_per_box
    pecahan_per_pallet = max(1, total_pcs_pallet // pcs_per_pecahan) if pcs_per_pecahan > 0 else 1
    total_pcs_init = (init_p * total_pcs_pallet) + (init_d * pcs_per_pecahan)

    st.markdown(f"### {judul_ambil}")
    with st.container(border=True):
        st.markdown(format_nama_warna_bold(st.session_state.item))
        st.markdown(f"**Lokasi:** Rak {k_pilih}-{b_pilih}  |  **Lot:** `{lot_pilih}`")
        st.info(f"📦 **Stok Tercatat di Rak:** **{init_p} Pallet**, **{init_d} {satuan}** ({total_pcs_init:,} Pcs)")

    st.markdown("**1. Berapa yang Diambil Forklift?**")
    default_ambil_p = 1 if init_p > 0 else 0
    default_ambil_d = init_d if init_p == 0 else 0

    c_amb1, c_amb2 = st.columns(2)
    with c_amb1:
        ambil_p = st.number_input(
            "Pallet Utuh Diambil", min_value=0, max_value=init_p, step=1,
            value=default_ambil_p, key=f"amb_p_{prefix_app}_{k_pilih}_{b_pilih}_{lot_pilih}",
        )
    with c_amb2:
        max_ambil_d = init_d if init_p == 0 else max(init_d, pecahan_per_pallet - 1)
        ambil_d = st.number_input(
            f"Pecahan Diambil ({satuan})", min_value=0, max_value=max_ambil_d, step=1,
            value=default_ambil_d, key=f"amb_d_{prefix_app}_{k_pilih}_{b_pilih}_{lot_pilih}",
        )

    pcs_diambil_input = (ambil_p * total_pcs_pallet) + (ambil_d * pcs_per_pecahan)
    pcs_sisa_hitung = max(0, total_pcs_init - pcs_diambil_input)
    sisa_p_hitung = pcs_sisa_hitung // total_pcs_pallet if total_pcs_pallet > 0 else max(0, init_p - ambil_p)
    sisa_pcs_rem = pcs_sisa_hitung % total_pcs_pallet if total_pcs_pallet > 0 else 0
    sisa_d_hitung = sisa_pcs_rem // pcs_per_pecahan if pcs_per_pecahan > 0 else max(0, init_d - ambil_d)

    st.markdown(
        f"""
        <div class="cek-fisik-box">
            <div style="font-size: 13px; opacity: 0.85;">👀 <strong>CEK FISIK LORONG RAK {k_pilih}-{b_pilih}:</strong></div>
            <div style="font-size: 20px; font-weight: bold; margin-top: 4px;">
                Sisa di rak harus tinggal: <u>{sisa_p_hitung} Pallet, {sisa_d_hitung} {satuan}</u>
            </div>
            <div style="font-size: 12px; opacity: 0.8; margin-top: 2px;">
                ({pcs_sisa_hitung:,} Pcs) — Pastikan jumlah palet tersisa di lorong sesuai!
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("🛠️ Tidak aktual? Koreksi disini"):
        st.caption("Ubah angka di bawah jika setelah diambil ternyata sisa fisik di lorong tidak sama dengan hitungan di atas:")
        max_sisa_d_kor = max(init_d, pecahan_per_pallet - 1) if pecahan_per_pallet > 1 else max(init_d, 100)
        c_kor1, c_kor2 = st.columns(2)
        with c_kor1:
            sisa_pallet_final = st.number_input(
                "Sisa Pallet Utuh Aktual", min_value=0, max_value=init_p, step=1,
                value=int(sisa_p_hitung), key=f"kor_p_{prefix_app}_{k_pilih}_{b_pilih}_{lot_pilih}_{ambil_p}_{ambil_d}",
            )
        with c_kor2:
            sisa_dus_final = st.number_input(
                f"Sisa Pecahan Aktual ({satuan})", min_value=0, max_value=max_sisa_d_kor, step=1,
                value=int(sisa_d_hitung), key=f"kor_d_{prefix_app}_{k_pilih}_{b_pilih}_{lot_pilih}_{ambil_p}_{ambil_d}",
            )

    total_pcs_final = (sisa_pallet_final * total_pcs_pallet) + (sisa_dus_final * pcs_per_pecahan)
    selisih_pcs_total = total_pcs_init - total_pcs_final
    keluar_pallet_eq = selisih_pcs_total // total_pcs_pallet if total_pcs_pallet > 0 else 0
    sisa_pcs_temp = selisih_pcs_total % total_pcs_pallet if total_pcs_pallet > 0 else selisih_pcs_total
    keluar_dus_eq = sisa_pcs_temp // pcs_per_pecahan if pcs_per_pecahan > 0 else 0

    ada_koreksi_fisik = (sisa_pallet_final != sisa_p_hitung) or (sisa_dus_final != sisa_d_hitung)
    if ada_koreksi_fisik and selisih_pcs_total > 0:
        st.warning(
            f"⚠️ **Koreksi Sisa Aktif:** Tercatat keluar **{selisih_pcs_total:,} Pcs** "
            f"({keluar_pallet_eq} Pallet, {keluar_dus_eq} {satuan}), sisa akhir rak menjadi **{sisa_pallet_final} Pallet, {sisa_dus_final} {satuan}**."
        )
    elif pcs_diambil_input > total_pcs_init:
        st.error("❌ Jumlah diambil melebihi stok yang tersedia di rak!")
    elif selisih_pcs_total <= 0:
        st.warning("⚠️ Masukkan minimal 1 Pallet atau Pecahan yang diambil.")

    catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Suplai Mesin 3")
    st.write("")

    if selisih_pcs_total > 0 and pcs_diambil_input <= total_pcs_init:
        if st.button(label_tombol_eksekusi, type="primary", use_container_width=True):
            with st.spinner("Menyimpan transaksi ke database..."):
                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                transaction = db.transaction()

                try:
                    data_sebelum_update = proses_pengambilan_stok(
                        transaction,
                        doc_ref,
                        k_pilih,
                        b_pilih,
                        lot_pilih,
                        st.session_state.pilih_part,
                        st.session_state.item,
                        init_p,
                        init_d,
                        sisa_pallet_final,
                        sisa_dus_final,
                        satuan,
                        catatan_rak_lama,
                    )

                    teks_keterangan = (
                        f"Op: {st.session_state.operator} | Ambil: {ambil_p}P {ambil_d}{satuan[0]} | "
                        f"Awal: {init_p}P {init_d}{satuan[0]} -> Sisa: {sisa_pallet_final}P {sisa_dus_final}{satuan[0]}"
                    )
                    if ada_koreksi_fisik:
                        teks_keterangan += f" [Koreksi Fisik dari {sisa_p_hitung}P {sisa_d_hitung}{satuan[0]}]"
                    if catatan_operator.strip():
                        teks_keterangan += f" | Catatan: {catatan_operator.strip()}"

                    catat_log_mutasi(
                        jenis_log,
                        k_pilih,
                        b_pilih,
                        st.session_state.item,
                        st.session_state.pilih_part,
                        lot_pilih,
                        keluar_pallet_eq,
                        keluar_dus_eq,
                        selisih_pcs_total,
                        teks_keterangan,
                    )

                    pesan_tele = (
                        f"📤 <b>{judul_tele}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📦 <b>Item:</b> {escape_html(st.session_state.item)}\n"
                        f"📍 <b>Rak:</b> {k_pilih}-{b_pilih} | <b>Lot:</b> <code>{escape_html(lot_pilih)}</code>\n"
                        f"📊 <b>Keluar:</b> <b>{selisih_pcs_total:,} Pcs</b> ({keluar_pallet_eq} Pallet, {keluar_dus_eq} {satuan})\n"
                        f"📉 <b>Stok Rak:</b> {init_p}P {init_d}{satuan} ➔ <b>{sisa_pallet_final}P {sisa_dus_final}{satuan}</b>\n"
                        f"👤 <b>Operator:</b> {escape_html(st.session_state.operator)}\n"
                    )
                    if ada_koreksi_fisik:
                        pesan_tele += f"⚠️ <i>Koreksi sisa fisik (hitungan awal: {sisa_p_hitung}P {sisa_d_hitung}{satuan})</i>\n"
                    if catatan_operator.strip():
                        pesan_tele += f"📝 <b>Catatan:</b> <i>{escape_html(catatan_operator.strip())}</i>\n"
                    kirim_notif_telegram(pesan_tele)

                    get_all_racking_data.clear()
                    msg_sukses = (
                        f"Keluar {selisih_pcs_total:,} Pcs ({keluar_pallet_eq}P {keluar_dus_eq}{satuan}) "
                        f"dari Rak {k_pilih}-{b_pilih}. Sisa: {sisa_pallet_final}P {sisa_dus_final}{satuan}."
                    )
                    simpan_undo_firestore(
                        prefix_app, st.session_state.operator, {k_pilih: data_sebelum_update}, msg_sukses, mode_label=mode_undo_label
                    )

                    st.toast(msg_sukses, icon="✅")
                    keys_clear = ["kategori", "gramasi", "item", "pilih_part", "mode_transaksi"] + list(reset_keys.values())
                    for k in set(keys_clear):
                        if k in st.session_state:
                            st.session_state[k] = None
                    st.session_state.slide = 1
                    st.rerun()

                except ValueError as err:
                    st.error(f"❌ Transaksi Dibatalkan: {err}")
                except Exception as err:
                    st.error(f"❌ Kesalahan Sistem: {err}")
