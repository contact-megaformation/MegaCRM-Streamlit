# MegaCRM_Streamlit_App.py — CRM + Finance (MB/Bizerte)
# =======================================================================================
# - CRM كامل: موظفين (قفل بكلمة سر)، قائمة العملاء، بحث، ملاحظات/Tag، تعديل، إضافة، نقل
# - Admin: إضافة/حذف موظف، إضافة عميل لأي موظّف (قفل 30 دقيقة) + سجلّ نقل العملاء
# - تبويب "مداخيل (MB/Bizerte)" + "مصاريف" (ظاهر للجميع) — الملخّص الشهري Admin فقط
# - تقليل قراءات Google Sheets عبر cache لتفادي 429

import json
import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta, timezone
from PIL import Image

# ---------------- Page config ----------------
st.set_page_config(page_title="MegaCRM", layout="wide", initial_sidebar_state="expanded")
st.markdown(
    """
    <div style='text-align:center;'>
        <h1 style='color:#333; margin-top: 8px;'>📊 CRM MEGA FORMATION - إدارة العملاء</h1>
    </div>
    <hr>
    """,
    unsafe_allow_html=True
)

# ---------------- Google Sheets Auth ----------------
SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]

def make_client_and_sheet_id():
    try:
        sa = st.secrets["gcp_service_account"]
        sa_info = dict(sa) if hasattr(sa, "keys") else (json.loads(sa) if isinstance(sa, str) else {})
        creds = Credentials.from_service_account_info(sa_info, scopes=SCOPE)
        client = gspread.authorize(creds)
        sheet_id = st.secrets["SPREADSHEET_ID"]
        return client, sheet_id
    except Exception:
        # fallback محلي
        creds = Credentials.from_service_account_file("service_account.json", scopes=SCOPE)
        client = gspread.authorize(creds)
        sheet_id = "1DV0KyDRYHofWR60zdx63a9BWBywTFhLavGAExPIa6LI"
        return client, sheet_id

client, SPREADSHEET_ID = make_client_and_sheet_id()

# ---------------- Schemas ----------------
EXPECTED_HEADERS = [
    "Nom & Prénom","Téléphone","Type de contact","Formation",
    "Remarque","Date ajout","Date de suivi","Alerte",
    "Inscription","Employe","Tag"
]

FIN_REV_COLUMNS = [
    "Date", "Libellé", "Prix",
    "Montant_Admin", "Montant_Structure", "Montant_PreInscription", "Montant_Total",
    "Echeance", "Reste",
    "Mode", "Employé", "Catégorie", "Note"
]
FIN_DEP_COLUMNS = ["Date","Libellé","Montant","Caisse_Source","Mode","Employé","Catégorie","Note"]
FIN_MONTHS_FR = ["Janvier","Février","Mars","Avril","Mai","Juin","Juillet","Aout","Septembre","Octobre","Novembre","Décembre"]

# سجلّ نقل العملاء
REASSIGN_LOG_SHEET   = "Reassign_Log"
REASSIGN_LOG_HEADERS = ["timestamp","moved_by","src_employee","dst_employee","client_name","phone"]

# ---------------- Helpers (finance) ----------------
def _branch_passwords():
    try:
        b = st.secrets["branch_passwords"]
        return {"Menzel Bourguiba": str(b.get("MB", "MB_2025!")), "Bizerte": str(b.get("BZ", "BZ_2025!"))}
    except Exception:
        return {"Menzel Bourguiba": "MB_2025!", "Bizerte": "BZ_2025!"}

def fin_month_title(mois: str, kind: str, branch: str):
    prefix = "Revenue " if kind == "Revenus" else "Dépense " if kind=="Dépenses" else "Payout "
    short = "MB" if "Menzel" in branch else "BZ"
    return f"{prefix}{mois} ({short})"

def ensure_ws_generic(title: str, columns: list[str]):
    sh = client.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows="2000", cols=str(max(len(columns), 8)))
        ws.update("1:1", [columns])
        return ws
    rows = ws.get_all_values()
    if not rows:
        ws.update("1:1", [columns])
    else:
        header = rows[0]
        if not header or header[:len(columns)] != columns:
            ws.update("1:1", [columns])
    return ws

def fin_ensure_ws(title: str, columns: list[str]):
    return ensure_ws_generic(title, columns)

@st.cache_data(ttl=360)
def fin_read_df(title: str, kind: str) -> pd.DataFrame:
    cols = FIN_REV_COLUMNS if kind == "Revenus" else FIN_DEP_COLUMNS
    ws = fin_ensure_ws(title, cols)
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(values[1:], columns=values[0])

    # Dates
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)
    if kind == "Revenus" and "Echeance" in df.columns:
        df["Echeance"] = pd.to_datetime(df["Echeance"], errors="coerce", dayfirst=True)

    # Numbers
    def to_num(s):
        return (
            pd.Series(s).astype(str)
            .str.replace(" ", "", regex=False)
            .str.replace(",", ".", regex=False)
            .pipe(pd.to_numeric, errors="coerce")
            .fillna(0.0)
        )

    if kind == "Revenus":
        for c in ["Prix","Montant_Admin","Montant_Structure","Montant_PreInscription","Montant_Total","Reste"]:
            if c in df.columns:
                df[c] = to_num(df[c])
    else:
        if "Montant" in df.columns:
            df["Montant"] = to_num(df["Montant"])

    # Alerts
    if kind == "Revenus":
        df["Alert"] = ""
        if "Echeance" in df.columns and "Reste" in df.columns:
            today_ts = pd.Timestamp.now().normalize()
            ech = pd.to_datetime(df["Echeance"], errors="coerce")
            reste = pd.to_numeric(df["Reste"], errors="coerce").fillna(0.0)
            df.loc[ech.notna() & (ech < today_ts) & (reste > 0), "Alert"] = "⚠️ متأخر"
            df.loc[ech.notna() & (ech.dt.normalize() == today_ts) & (reste > 0), "Alert"] = "⏰ اليوم"
    return df

def fin_append_row(title: str, row: dict, kind: str):
    cols = FIN_REV_COLUMNS if kind=="Revenus" else FIN_DEP_COLUMNS
    ws = fin_ensure_ws(title, cols)
    header = ws.row_values(1)
    vals = [str(row.get(col, "")) for col in header]
    ws.append_row(vals)

# ---------------- Helpers (common) ----------------
def fmt_date(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if isinstance(d, date) else ""

def normalize_tn_phone(s: str) -> str:
    digits = "".join(ch for ch in str(s) if ch.isdigit())
    if digits.startswith("216"): return digits
    if len(digits) == 8: return "216" + digits
    return digits

def format_display_phone(s: str) -> str:
    d = "".join(ch for ch in str(s) if ch.isdigit())
    return f"+{d}" if d else ""

def color_tag(val):
    if isinstance(val, str) and val.strip().startswith("#") and len(val.strip()) == 7:
        return f"background-color: {val}; color: white;"
    return ""

def mark_alert_cell(val: str):
    s = str(val).strip()
    if not s: return ''
    if "متأخر" in s: return 'background-color: #ffe6b3; color: #7a4e00'
    return 'background-color: #ffcccc; color: #7a0000'

def highlight_inscrit_row(row: pd.Series):
    insc = str(row.get("Inscription", "")).strip().lower()
    return ['background-color: #d6f5e8' if insc in ("inscrit","oui") else '' for _ in row.index]

# ---------------- Employee Password Locks ----------------
def _get_emp_password(emp_name: str) -> str:
    try:
        mp = st.secrets["employee_passwords"]
        return str(mp.get(emp_name, mp.get("_default", "1234")))
    except Exception:
        return "1234"

def _emp_unlocked(emp_name: str) -> bool:
    ok = st.session_state.get(f"emp_ok::{emp_name}", False)
    ts = st.session_state.get(f"emp_ok_at::{emp_name}")
    return bool(ok and ts and (datetime.now() - ts) <= timedelta(minutes=15))

def _emp_lock_ui(emp_name: str):
    with st.expander(f"🔐 حماية ورقة الموظّف: {emp_name}", expanded=not _emp_unlocked(emp_name)):
        if _emp_unlocked(emp_name):
            c1, c2 = st.columns(2)
            with c1: st.success("مفتوح (15 دقيقة).")
            with c2:
                if st.button("قفل الآن"):
                    st.session_state[f"emp_ok::{emp_name}"] = False
                    st.session_state[f"emp_ok_at::{emp_name}"] = None
                    st.info("تم القفل.")
        else:
            pwd_try = st.text_input("أدخل كلمة السرّ", type="password", key=f"emp_pwd_{emp_name}")
            if st.button("فتح"):
                if pwd_try and pwd_try == _get_emp_password(emp_name):
                    st.session_state[f"emp_ok::{emp_name}"] = True
                    st.session_state[f"emp_ok_at::{emp_name}"] = datetime.now()
                    st.success("تم الفتح لمدة 15 دقيقة.")
                else:
                    st.error("كلمة سرّ غير صحيحة.")

# ---------------- Load all CRM data ----------------
@st.cache_data(ttl=600)
def load_all_data():
    sh = client.open_by_key(SPREADSHEET_ID)
    worksheets = sh.worksheets()
    all_dfs, all_employes = [], []

    for ws in worksheets:
        title = ws.title.strip()
        if title.endswith("_PAIEMENTS"):            # دفوعات قديمة
            continue
        if title.startswith("_"):                   # أوراق نظام
            continue
        if title.startswith("Revenue ") or title.startswith("Dépense "):  # أوراق مالية
            continue

        all_employes.append(title)

        rows = ws.get_all_values()
        if not rows:
            ws.update("1:1", [EXPECTED_HEADERS]); rows = ws.get_all_values()

        data_rows = rows[1:] if len(rows) > 1 else []
        fixed_rows = []
        for r in data_rows:
            r = list(r or [])
            if len(r) < len(EXPECTED_HEADERS): r += [""] * (len(EXPECTED_HEADERS) - len(r))
            else: r = r[:len(EXPECTED_HEADERS)]
            fixed_rows.append(r)

        df = pd.DataFrame(fixed_rows, columns=EXPECTED_HEADERS)
        df["__sheet_name"] = title
        all_dfs.append(df)

    big = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame(columns=EXPECTED_HEADERS + ["__sheet_name"])
    return big, all_employes

df_all, all_employes = load_all_data()

# ---------------- Sidebar ----------------
try:
    st.sidebar.image(Image.open("logo.png"), use_container_width=True)
except Exception:
    pass

tab_choice = st.sidebar.radio("📑 اختر تبويب:", ["CRM", "مداخيل (MB/Bizerte)"], index=0)
role = st.sidebar.radio("الدور", ["موظف", "أدمن"], horizontal=True)
employee = None
if role == "موظف":
    employee = st.sidebar.selectbox("👨‍💼 اختر الموظّف (ورقة Google Sheets)", all_employes) if all_employes else None

# ---------------- Finance Tab ----------------
if tab_choice == "مداخيل (MB/Bizerte)":
    st.title("💸 المداخيل والمصاريف — (منزل بورقيبة & بنزرت)")

    with st.sidebar:
        st.markdown("---")
        st.subheader("🔧 إعدادات المداخيل/المصاريف")
        branch = st.selectbox("الفرع", ["Menzel Bourguiba", "Bizerte"], key="fin_branch")
        kind_ar = st.radio("النوع", ["مداخيل","مصاريف"], horizontal=True, key="fin_kind_ar")
        kind = "Revenus" if kind_ar == "مداخيل" else "Dépenses"
        mois   = st.selectbox("الشهر", FIN_MONTHS_FR, index=datetime.now().month-1, key="fin_month")

        # كلمة سرّ الفرع
        BRANCH_PASSWORDS = _branch_passwords()
        key_pw = f"finance_pw_ok::{branch}"
        if key_pw not in st.session_state: st.session_state[key_pw] = False
        if not st.session_state[key_pw]:
            pw_try = st.text_input("كلمة سرّ الفرع", type="password", key=f"fin_pw_{branch}")
            if st.button("دخول الفرع", key=f"fin_enter_{branch}"):
                if pw_try and pw_try == BRANCH_PASSWORDS.get(branch, ""):
                    st.session_state[key_pw] = True; st.success("تم الدخول ✅")
                else:
                    st.error("كلمة سرّ غير صحيحة ❌")

    if not st.session_state.get(f"finance_pw_ok::{branch}", False):
        st.info("⬅️ أدخل كلمة السرّ من اليسار للمتابعة.")
        st.stop()

    fin_title = fin_month_title(mois, kind, branch)

    # قراءة + فلاتر
    df_fin = fin_read_df(fin_title, kind)
    df_view = df_fin.copy()

    # فلترة حسب الموظف
    if role == "موظف" and employee and "Employé" in df_view.columns:
        df_view = df_view[df_view["Employé"].fillna("").str.strip().str.lower() == employee.strip().lower()]

    with st.expander("🔎 فلاتر", expanded=True):
        c1, c2, c3 = st.columns(3)
        date_from = c1.date_input("من تاريخ", value=None, key="fin_from")
        date_to   = c2.date_input("إلى تاريخ", value=None, key="fin_to")
        search    = c3.text_input("بحث (Libellé/Catégorie/Mode/Note)", key="fin_search")
        if "Date" in df_view.columns:
            if date_from: df_view = df_view[df_view["Date"] >= pd.to_datetime(date_from)]
            if date_to:   df_view = df_view[df_view["Date"] <= pd.to_datetime(date_to)]
        if search and not df_view.empty:
            m = pd.Series([False]*len(df_view))
            for col in [c for c in ["Libellé","Catégorie","Mode","Employé","Note","Caisse_Source","Montant_PreInscription"] if c in df_view.columns]:
                m |= df_view[col].fillna("").astype(str).str.contains(search, case=False, na=False)
            df_view = df_view[m]

    st.subheader(f"📄 {fin_title}")
    if kind == "Revenus":
        cols_show = [c for c in ["Date","Libellé","Prix","Montant_Admin","Montant_Structure","Montant_PreInscription","Montant_Total","Echeance","Reste","Alert","Mode","Employé","Catégorie","Note"] if c in df_view.columns]
    else:
        cols_show = [c for c in ["Date","Libellé","Montant","Caisse_Source","Mode","Employé","Catégorie","Note"] if c in df_view.columns]
    st.dataframe(df_view[cols_show] if not df_view.empty else pd.DataFrame(columns=cols_show), use_container_width=True)

    # -------- ملخّص شهري — Admin فقط --------
    if role == "أدمن":
        with st.expander("📊 ملخّص الفرع للشهر (حسب الصنف) — Admin Only", expanded=False):
            rev_df = fin_read_df(fin_month_title(mois, "Revenus", branch), "Revenus")
            dep_df = fin_read_df(fin_month_title(mois, "Dépenses", branch), "Dépenses")

            sum_admin    = rev_df["Montant_Admin"].sum()           if ("Montant_Admin" in rev_df.columns and not rev_df.empty) else 0.0
            sum_struct   = rev_df["Montant_Structure"].sum()       if ("Montant_Structure" in rev_df.columns and not rev_df.empty) else 0.0
            sum_preins   = rev_df["Montant_PreInscription"].sum()  if ("Montant_PreInscription" in rev_df.columns and not rev_df.empty) else 0.0
            sum_total_as = rev_df["Montant_Total"].sum()           if ("Montant_Total" in rev_df.columns and not rev_df.empty) else (sum_admin + sum_struct)
            sum_reste_due= rev_df["Reste"].sum()                   if ("Reste" in rev_df.columns and not rev_df.empty) else 0.0

            if not dep_df.empty and "Caisse_Source" in dep_df.columns and "Montant" in dep_df.columns:
                dep_admin  = dep_df.loc[dep_df["Caisse_Source"]=="Caisse_Admin",        "Montant"].sum()
                dep_struct = dep_df.loc[dep_df["Caisse_Source"]=="Caisse_Structure",    "Montant"].sum()
                dep_inscr  = dep_df.loc[dep_df["Caisse_Source"]=="Caisse_Inscription",  "Montant"].sum()
            else:
                dep_admin = dep_struct = dep_inscr = 0.0

            reste_admin    = float(sum_admin)  - float(dep_admin)
            reste_struct   = float(sum_struct) - float(dep_struct)
            reste_inscr    = float(sum_preins) - float(dep_inscr)

            st.markdown("#### 🔹 Admin")
            a1, a2, a3 = st.columns(3)
            a1.metric("مداخيل Admin",   f"{sum_admin:,.2f}")
            a2.metric("مصاريف Admin",   f"{dep_admin:,.2f}")
            a3.metric("Reste Admin",     f"{reste_admin:,.2f}")

            st.markdown("#### 🔹 Structure")
            s1, s2, s3 = st.columns(3)
            s1.metric("مداخيل Structure", f"{sum_struct:,.2f}")
            s2.metric("مصاريف Structure", f"{dep_struct:,.2f}")
            s3.metric("Reste Structure",   f"{reste_struct:,.2f}")

            st.markdown("#### 🔹 Inscription (Pré-Inscription)")
            i1, i2, i3 = st.columns(3)
            i1.metric("مداخيل Inscription", f"{sum_preins:,.2f}")
            i2.metric("مصاريف Inscription", f"{dep_inscr:,.2f}")
            i3.metric("Reste Inscription",   f"{reste_inscr:,.2f}")

            st.markdown("#### 🔸 معلومات إضافية")
            x1, x2, x3 = st.columns(3)
            x1.metric("Total Admin+Structure (مداخيل فقط)", f"{sum_total_as:,.2f}")
            x2.metric("Total مصاريف", f"{(dep_admin + dep_struct + dep_inscr):,.2f}")
            x3.metric("إجمالي المتبقّي بالدروس (Reste Due)", f"{sum_reste_due:,.2f}")

    # -------- إضافة عملية جديدة (يظهر للجميع) --------
    st.markdown("---")
    st.markdown("### ➕ إضافة عملية جديدة")

    # اقتراح ربط بعميل مُسجّل (للRevenus)
    selected_client_info = None
    client_default_lib = ""
    client_default_emp = (employee or "")

    if kind == "Revenus":
        st.markdown("#### 👤 اربط الدفعة بعميل مُسجَّل (اختياري)")
        reg_df = df_all.copy()
        reg_df["Inscription_norm"] = reg_df["Inscription"].fillna("").astype(str).str.strip().str.lower()
        reg_df = reg_df[reg_df["Inscription_norm"].isin(["oui","inscrit"])]
        if role == "موظف" and employee:
            reg_df = reg_df[reg_df["__sheet_name"] == employee]
        options = []
        if not reg_df.empty:
            def _opt(row):
                phone = format_display_phone(row.get("Téléphone",""))
                return f"{row.get('Nom & Prénom','')} — {phone} — {row.get('Formation','')}  [{row.get('__sheet_name','')}]"
            options = [_opt(r) for _, r in reg_df.iterrows()]
        pick = st.selectbox("اختر عميلًا مُسجَّلًا", ["— بدون اختيار —"] + options, key="fin_client_pick")
        if pick and pick != "— بدون اختيار —":
            idx = options.index(pick); row = reg_df.iloc[idx]
            selected_client_info = {
                "name": str(row.get("Nom & Prénom","")).strip(),
                "tel":  str(row.get("Téléphone","")).strip(),
                "formation": str(row.get("Formation","")).strip(),
                "emp": str(row.get("__sheet_name","")).strip()
            }
            client_default_lib = f"Paiement {selected_client_info['formation']} - {selected_client_info['name']}".strip()
            if not client_default_emp: client_default_emp = selected_client_info["emp"]

    with st.form("fin_add_row"):
        d1, d2, d3 = st.columns(3)
        date_val = d1.date_input("Date", value=datetime.today())
        libelle  = d2.text_input("Libellé", value=(client_default_lib if kind=="Revenus" else ""))
        employe  = d3.text_input("Employé", value=(client_default_emp if kind=="Revenus" else (employee or "")))

        if kind == "Revenus":
            r1, r2, r3 = st.columns(3)
            prix            = r1.number_input("💰 Prix (سعر التكوين)", min_value=0.0, step=10.0)
            montant_admin   = r2.number_input("🏢 Montant Admin", min_value=0.0, step=10.0)
            montant_struct  = r3.number_input("🏫 Montant Structure", min_value=0.0, step=10.0)

            r4, r5 = st.columns(2)
            montant_preins  = r4.number_input("📝 Montant Pré-Inscription", min_value=0.0, step=10.0, help="رسوم/حجز")
            montant_total   = float(montant_admin) + float(montant_struct)

            e1, e2, e3 = st.columns(3)
            echeance   = e1.date_input("⏰ تاريخ الاستحقاق", value=datetime.today())
            mode       = e2.selectbox("Mode", ["Espèces","Virement","Carte","Chèque","Autre"])
            categorie  = e3.text_input("Catégorie", value="Revenus")

            note_default = f"Client: {selected_client_info['name']} / {selected_client_info['formation']}" if selected_client_info else ""
            note = st.text_area("Note", value=note_default)

            # مجموع ما دُفع لنفس Libellé في هذا الشهر (تخفيض قراءات)
            rev_df_current = fin_read_df(fin_month_title(mois, "Revenus", branch), "Revenus")
            paid_so_far = 0.0
            if not rev_df_current.empty and libelle.strip():
                mask = rev_df_current["Libellé"].fillna("").str.strip().str.lower() == libelle.strip().lower()
                paid_so_far = float(rev_df_current.loc[mask, "Montant_Total"].sum()) if "Montant_Total" in rev_df_current.columns else 0.0

            reste_after = max(float(prix) - (paid_so_far + float(montant_total)), 0.0)

            st.caption(
                f"💡 Total الآن (Admin+Structure): {montant_total:.2f} — مدفوع سابقًا لنفس Libellé (هذا الشهر): {paid_so_far:.2f} — "
                f"Reste المقترح: {reste_after:.2f} — Pré-Inscription منفصل: {montant_preins:.2f}"
            )

            if st.form_submit_button("✅ حفظ العملية"):
                if not libelle.strip(): st.error("Libellé مطلوب.")
                elif prix <= 0: st.error("Prix مطلوب.")
                elif montant_total <= 0 and montant_preins <= 0:
                    st.error("المبلغ لازم > 0 (Admin/Structure أو Pré-Inscription).")
                else:
                    fin_append_row(
                        fin_month_title(mois, "Revenus", branch),
                        {
                            "Date": fmt_date(date_val),
                            "Libellé": libelle.strip(),
                            "Prix": f"{float(prix):.2f}",
                            "Montant_Admin": f"{float(montant_admin):.2f}",
                            "Montant_Structure": f"{float(montant_struct):.2f}",
                            "Montant_PreInscription": f"{float(montant_preins):.2f}",
                            "Montant_Total": f"{float(montant_total):.2f}",
                            "Echeance": fmt_date(echeance),
                            "Reste": f"{float(reste_after):.2f}",
                            "Mode": mode,
                            "Employé": employe.strip(),
                            "Catégorie": categorie.strip(),
                            "Note": note.strip(),
                        },
                        "Revenus"
                    )
                    st.success("تمّ الحفظ ✅"); st.cache_data.clear(); st.experimental_rerun()

        else:  # مصاريف
            r1, r2, r3 = st.columns(3)
            montant   = r1.number_input("Montant", min_value=0.0, step=10.0)
            caisse    = r2.selectbox("Caisse_Source", ["Caisse_Admin","Caisse_Structure","Caisse_Inscription"])
            mode      = r3.selectbox("Mode", ["Espèces","Virement","Carte","Chèque","Autre"])

            c2, c3 = st.columns(2)
            categorie = c2.text_input("Catégorie", value="Achat")
            note      = c3.text_area("Note (اختياري)")

            if st.form_submit_button("✅ حفظ العملية"):
                if not libelle.strip(): st.error("Libellé مطلوب.")
                elif montant <= 0: st.error("المبلغ لازم > 0.")
                else:
                    fin_append_row(
                        fin_month_title(mois, "Dépenses", branch),
                        {
                            "Date": fmt_date(date_val),
                            "Libellé": libelle.strip(),
                            "Montant": f"{float(montant):.2f}",
                            "Caisse_Source": caisse,
                            "Mode": mode,
                            "Employé": (employee or employe or "").strip(),
                            "Catégorie": categorie.strip(),
                            "Note": note.strip(),
                        },
                        "Dépenses"
                    )
                    st.success("تمّ الحفظ ✅"); st.cache_data.clear(); st.experimental_rerun()

# ---------------- CRM مشتقّات وعرض ----------------
df_all = df_all.copy()
if not df_all.empty:
    df_all["DateAjout_dt"] = pd.to_datetime(df_all["Date ajout"], dayfirst=True, errors="coerce")
    df_all["DateSuivi_dt"] = pd.to_datetime(df_all["Date de suivi"], dayfirst=True, errors="coerce")
    df_all["Mois"] = df_all["DateAjout_dt"].dt.strftime("%m-%Y")
    today = datetime.now().date()
    base_alert = df_all["Alerte"].fillna("").astype(str).str.strip()
    dsv_date = df_all["DateSuivi_dt"].dt.date
    due_today = dsv_date.eq(today).fillna(False)
    overdue  = dsv_date.lt(today).fillna(False)
    df_all["Alerte_view"] = base_alert
    df_all.loc[base_alert.eq("") & overdue, "Alerte_view"] = "⚠️ متابعة متأخرة"
    df_all.loc[base_alert.eq("") & due_today, "Alerte_view"] = "⏰ متابعة اليوم"
    df_all["Téléphone_norm"] = df_all["Téléphone"].apply(normalize_tn_phone)
    ALL_PHONES = set(df_all["Téléphone_norm"].dropna().astype(str))
    df_all["Inscription_norm"] = df_all["Inscription"].fillna("").astype(str).str.strip().str.lower()
    inscrit_mask = df_all["Inscription_norm"].isin(["oui", "inscrit"])
    df_all.loc[inscrit_mask, "Date de suivi"] = ""
    df_all.loc[inscrit_mask, "Alerte_view"] = ""
else:
    df_all["Alerte_view"] = ""; df_all["Mois"] = ""; df_all["Téléphone_norm"] = ""; ALL_PHONES = set()

# ---------------- Admin lock ----------------
def admin_unlocked() -> bool:
    ok = st.session_state.get("admin_ok", False)
    ts = st.session_state.get("admin_ok_at", None)
    return bool(ok and ts and (datetime.now() - ts) <= timedelta(minutes=30))

def admin_lock_ui():
    with st.sidebar.expander("🔐 إدارة (Admin)", expanded=(role=="أدمن" and not admin_unlocked())):
        if admin_unlocked():
            if st.button("قفل صفحة الأدمِن"):
                st.session_state["admin_ok"] = False
                st.session_state["admin_ok_at"] = None
                st.rerun()
        else:
            admin_pwd = st.text_input("كلمة سرّ الأدمِن", type="password", key="admin_pwd_inp")
            if st.button("فتح صفحة الأدمِن"):
                conf = str(st.secrets.get("admin_password", "admin123"))
                if admin_pwd and admin_pwd == conf:
                    st.session_state["admin_ok"] = True
                    st.session_state["admin_ok_at"] = datetime.now()
                    st.success("تم فتح صفحة الأدمِن لمدة 30 دقيقة.")
                else:
                    st.error("كلمة سرّ غير صحيحة.")

if role == "أدمن": admin_lock_ui()

# ---------------- Dashboard سريع + شهري/موظفين ----------------
st.subheader("لوحة إحصائيات سريعة")
df_dash = df_all.copy()
if df_dash.empty:
    st.info("ما فماش داتا للعرض.")
else:
    df_dash["DateAjout_dt"] = pd.to_datetime(df_dash.get("Date ajout"), dayfirst=True, errors="coerce")
    df_dash["DateSuivi_dt"] = pd.to_datetime(df_dash.get("Date de suivi"), dayfirst=True, errors="coerce")
    today = datetime.now().date()
    df_dash["Inscription_norm"] = df_dash["Inscription"].fillna("").astype(str).str.strip().str.lower()
    df_dash["Alerte_norm"]      = df_dash["Alerte_view"].fillna("").astype(str).str.strip()
    added_today_mask      = df_dash["DateAjout_dt"].dt.date.eq(today)
    registered_today_mask = df_dash["Inscription_norm"].isin(["oui", "inscrit"]) & added_today_mask
    alert_now_mask        = df_dash["Alerte_norm"].ne("")
    total_clients    = int(len(df_dash))
    added_today      = int(added_today_mask.sum())
    registered_today = int(registered_today_mask.sum())
    alerts_now       = int(alert_now_mask.sum())
    registered_total = int((df_dash["Inscription_norm"] == "oui").sum())
    rate = round((registered_total / total_clients) * 100, 2) if total_clients else 0.0

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1: st.metric("👥 إجمالي العملاء", f"{total_clients}")
    with c2: st.metric("🆕 المضافون اليوم", f"{added_today}")
    with c3: st.metric("✅ المسجّلون اليوم", f"{registered_today}")
    with c4: st.metric("🚨 التنبيهات الحالية", f"{alerts_now}")
    with c5: st.metric("📈 نسبة التسجيل الإجمالية", f"{rate}%")

# شهري حسب الموظّف/التكوين
if not df_all.empty and "DateAjout_dt" in df_all.columns:
    st.markdown("---")
    st.subheader("📅 إحصائيات شهرية (العملاء)")
    df_all["MonthStr"] = df_all["DateAjout_dt"].dt.strftime("%Y-%m")
    months_avail = sorted(df_all["MonthStr"].dropna().unique(), reverse=True)
    month_pick = st.selectbox("اختر شهر", months_avail, index=0 if months_avail else None, key="stats_month_pick")
    if month_pick:
        month_mask = (df_all["DateAjout_dt"].dt.strftime("%Y-%m") == month_pick)
        df_month = df_all[month_mask].copy()

        total_clients_m = len(df_month)
        total_inscrits_m = int((df_month["Inscription_norm"] == "oui").sum())
        alerts_m = int(df_month["Alerte_view"].fillna("").astype(str).str.strip().ne("").sum())
        rate_m = round((total_inscrits_m / total_clients_m) * 100, 2) if total_clients_m else 0.0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("👥 عملاء هذا الشهر", f"{total_clients_m}")
        c2.metric("✅ مسجّلون", f"{total_inscrits_m}")
        c3.metric("🚨 تنبيهات", f"{alerts_m}")
        c4.metric("📈 نسبة التسجيل", f"{rate_m}%")

        st.markdown("#### 👨‍💼 حسب الموظّف (هذا الشهر)")
        grp_emp = (
            df_month.groupby("__sheet_name", dropna=False)
            .agg(
                Clients=("Nom & Prénom","count"),
                Inscrits=("Inscription_norm", lambda x: (x=="oui").sum()),
                Alerts=("Alerte_view", lambda x: (x.fillna("").astype(str).str.strip()!="").sum()),
            )
            .reset_index().rename(columns={"__sheet_name":"الموظف"})
        )
        grp_emp["% تسجيل"] = ((grp_emp["Inscrits"]/grp_emp["Clients"]).replace([float("inf"), float("nan")],0)*100).round(2)
        st.dataframe(grp_emp.sort_values(["Inscrits","Clients"], ascending=False), use_container_width=True)

        st.markdown("#### 📚 حسب التكوين (هذا الشهر)")
        grp_form = (
            df_month.groupby("Formation", dropna=False)
            .agg(
                Clients=("Nom & Prénom","count"),
                Inscrits=("Inscription_norm", lambda x: (x=="oui").sum()),
            )
            .reset_index().rename(columns={"Formation":"التكوين"})
        )
        grp_form["% تسجيل"] = ((grp_form["Inscrits"]/grp_form["Clients"]).replace([float("inf"), float("nan")],0)*100).round(2)
        st.dataframe(grp_form.sort_values(["Inscrits","Clients"], ascending=False), use_container_width=True)

# ---------------- Global phone search ----------------
st.subheader("🔎 بحث عام برقم الهاتف")
global_phone = st.text_input("اكتب رقم الهاتف (8 أرقام محلية أو 216XXXXXXXX)", key="global_phone_all")
if global_phone.strip():
    q_norm = normalize_tn_phone(global_phone)
    search_df = df_all.copy()
    search_df["Téléphone_norm"] = search_df["Téléphone"].apply(normalize_tn_phone)
    search_df["Alerte"] = search_df.get("Alerte_view", "")
    search_df = search_df[search_df["Téléphone_norm"] == q_norm]
    if search_df.empty:
        st.info("❕ ما لقيتش عميل بهذا الرقم.")
    else:
        display_cols = [c for c in EXPECTED_HEADERS if c in search_df.columns]
        if "Employe" in search_df.columns and "Employe" not in display_cols: display_cols.append("Employe")
        styled_global = (
            search_df[display_cols]
            .style.apply(highlight_inscrit_row, axis=1)
            .applymap(mark_alert_cell, subset=["Alerte"])
        )
        st.dataframe(styled_global, use_container_width=True)
        st.markdown("---")

# ---------------- Employee area ----------------
if role == "موظف" and employee:
    _emp_lock_ui(employee)
    if not _emp_unlocked(employee):
        st.info("🔒 أدخل كلمة سرّ الموظّف لفتح الورقة.")
        st.stop()

    st.subheader(f"📁 لوحة {employee}")
    df_emp = df_all[df_all["__sheet_name"] == employee].copy()

    if df_emp.empty:
        st.warning("⚠️ لا يوجد أي عملاء لهذا الموظف بعد.")
        st.stop()

    df_emp["DateAjout_dt"] = pd.to_datetime(df_emp["Date ajout"], dayfirst=True, errors="coerce")
    df_emp = df_emp.dropna(subset=["DateAjout_dt"])
    df_emp["Mois"] = df_emp["DateAjout_dt"].dt.strftime("%m-%Y")
    month_options = sorted(df_emp["Mois"].dropna().unique(), reverse=True)
    month_filter = st.selectbox("🗓️ اختر شهر الإضافة", month_options)
    filtered_df = df_emp[df_emp["Mois"] == month_filter].copy()

    # قائمة العملاء
    def render_table(df_disp: pd.DataFrame):
        if df_disp.empty: st.info("لا توجد بيانات."); return
        _df = df_disp.copy()
        _df["Alerte"] = _df.get("Alerte_view", "")
        display_cols = [c for c in EXPECTED_HEADERS if c in _df.columns]
        styled = (
            _df[display_cols]
            .style.apply(highlight_inscrit_row, axis=1)
            .applymap(mark_alert_cell, subset=["Alerte"])
            .applymap(color_tag, subset=["Tag"])
        )
        st.dataframe(styled, use_container_width=True)

    st.markdown("### 📋 قائمة العملاء")
    render_table(filtered_df)

    # ✏️ تعديل بيانات عميل — (FORM + submit)
    st.markdown("### ✏️ تعديل بيانات عميل")
    df_emp_edit = df_emp.copy()
    df_emp_edit["Téléphone_norm"] = df_emp_edit["Téléphone"].apply(normalize_tn_phone)

    phone_choices = {
        f"[{i}] {row['Nom & Prénom']} — {format_display_phone(row['Téléphone_norm'])}": row["Téléphone_norm"]
        for i, row in df_emp_edit.iterrows()
        if str(row.get("Téléphone","")).strip() != ""
    }

    def _force_rerun():
        st.session_state.pop("edit_form_submitted", None)
        st.rerun()

    if phone_choices:
        chosen_key   = st.selectbox("اختر العميل (بالاسم/الهاتف)", list(phone_choices.keys()), key="edit_pick", on_change=_force_rerun)
        chosen_phone = phone_choices.get(chosen_key, "")
        cur_row = df_emp_edit[df_emp_edit["Téléphone_norm"] == chosen_phone].iloc[0] if chosen_phone else None

        cur_name      = str(cur_row.get("Nom & Prénom",""))
        cur_tel_raw   = str(cur_row.get("Téléphone",""))
        cur_formation = str(cur_row.get("Formation",""))
        cur_remark    = str(cur_row.get("Remarque",""))
        cur_ajout = pd.to_datetime(cur_row.get("Date ajout",""), dayfirst=True, errors="coerce").date() if str(cur_row.get("Date ajout","")).strip() else date.today()
        cur_suivi = pd.to_datetime(cur_row.get("Date de suivi",""), dayfirst=True, errors="coerce").date() if str(cur_row.get("Date de suivi","")).strip() else date.today()
        cur_insc  = str(cur_row.get("Inscription","")).strip().lower()

        name_key   = f"edit_name_txt::{chosen_phone}"
        phone_key  = f"edit_phone_txt::{chosen_phone}"
        form_key   = f"edit_formation_txt::{chosen_phone}"
        ajout_key  = f"edit_ajout_dt::{chosen_phone}"
        suivi_key  = f"edit_suivi_dt::{chosen_phone}"
        insc_key   = f"edit_insc_sel::{chosen_phone}"
        remark_key = f"edit_remark_txt::{chosen_phone}"
        note_key   = f"append_note_txt::{chosen_phone}"

        with st.form(f"edit_client_form::{chosen_phone}", clear_on_submit=False):
            col1, col2 = st.columns(2)
            with col1:
                new_name      = st.text_input("👤 الاسم و اللقب", value=cur_name, key=name_key)
                new_phone_raw = st.text_input("📞 رقم الهاتف", value=cur_tel_raw, key=phone_key)
                new_formation = st.text_input("📚 التكوين", value=cur_formation, key=form_key)
            with col2:
                new_ajout = st.date_input("🕓 تاريخ الإضافة", value=cur_ajout, key=ajout_key)
                new_suivi = st.date_input("📆 تاريخ المتابعة", value=cur_suivi, key=suivi_key)
                new_insc  = st.selectbox("🟢 التسجيل", ["Pas encore", "Inscrit"], index=(1 if cur_insc == "oui" else 0), key=insc_key)

            new_remark_full = st.text_area("🗒️ ملاحظة (استبدال كامل)", value=cur_remark, key=remark_key)
            extra_note      = st.text_area("➕ أضف ملاحظة جديدة (طابع زمني)", placeholder="اكتب ملاحظة لإلحاقها…", key=note_key)

            submitted = st.form_submit_button("💾 حفظ التعديلات")
            if submitted:
                try:
                    ws = client.open_by_key(SPREADSHEET_ID).worksheet(employee)

                    values = ws.get_all_values()
                    header = values[0] if values else []
                    if "Téléphone" not in header:
                        st.error("❌ ورقة الموظف لا تحتوي عمود Téléphone."); st.stop()
                    tel_idx = header.index("Téléphone")
                    row_idx = None
                    for i, r in enumerate(values[1:], start=2):
                        if len(r) > tel_idx and normalize_tn_phone(r[tel_idx]) == chosen_phone:
                            row_idx = i; break
                    if not row_idx:
                        st.error("❌ تعذّر إيجاد الصف لهذا الهاتف."); st.stop()

                    col_map = {h: (EXPECTED_HEADERS.index(h) + 1) for h in [
                        "Nom & Prénom","Téléphone","Formation","Date ajout","Date de suivi","Inscription","Remarque"
                    ]}

                    new_phone_norm = normalize_tn_phone(new_phone_raw)
                    if not new_name.strip(): st.error("❌ الاسم و اللقب إجباري."); st.stop()
                    if not new_phone_norm.strip(): st.error("❌ رقم الهاتف إجباري."); st.stop()
                    phones_except_current = set(df_all["Téléphone_norm"].astype(str)) - {chosen_phone}
                    if new_phone_norm in phones_except_current: st.error("⚠️ الرقم موجود مسبقًا."); st.stop()

                    ws.update_cell(row_idx, col_map["Nom & Prénom"], new_name.strip())
                    ws.update_cell(row_idx, col_map["Téléphone"],   new_phone_norm)
                    ws.update_cell(row_idx, col_map["Formation"],   new_formation.strip())
                    ws.update_cell(row_idx, col_map["Date ajout"],  fmt_date(new_ajout))
                    ws.update_cell(row_idx, col_map["Date de suivi"], fmt_date(new_suivi))
                    ws.update_cell(row_idx, col_map["Inscription"], "Oui" if new_insc=="Inscrit" else "Pas encore")

                    if new_remark_full.strip() != cur_remark.strip():
                        ws.update_cell(row_idx, col_map["Remarque"], new_remark_full.strip())

                    if extra_note.strip():
                        old_rem = ws.cell(row_idx, col_map["Remarque"]).value or ""
                        stamp = datetime.now().strftime("%d/%m/%Y %H:%M")
                        appended = (old_rem + "\n" if old_rem else "") + f"[{stamp}] {extra_note.strip()}"
                        ws.update_cell(row_idx, col_map["Remarque"], appended)

                    st.success("✅ تم حفظ التعديلات")
                    st.cache_data.clear()
                    st.session_state["edit_form_submitted"] = True
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ خطأ أثناء التعديل: {e}")

    # Quick notes & Tag
    st.markdown("### 📝 أضف ملاحظة (سريعة)")
    scope_df = filtered_df if not filtered_df.empty else df_emp
    scope_df = scope_df.copy(); scope_df["Téléphone_norm"] = scope_df["Téléphone"].apply(normalize_tn_phone)
    tel_to_update_key = st.selectbox(
        "اختر العميل",
        [f"{r['Nom & Prénom']} — {format_display_phone(normalize_tn_phone(r['Téléphone']))}" for _, r in scope_df.iterrows()],
        key="note_quick_pick"
    )
    tel_to_update = normalize_tn_phone(tel_to_update_key.split("—")[-1]) if tel_to_update_key else ""
    new_note_quick = st.text_area("🗒️ ملاحظة جديدة (سيضاف لها طابع زمني)", key="note_quick_txt")
    if st.button("📌 أضف الملاحظة", key="note_quick_btn"):
        try:
            ws = client.open_by_key(SPREADSHEET_ID).worksheet(employee)
            values = ws.get_all_values()
            header = values[0] if values else []
            if "Téléphone" in header:
                tel_idx = header.index("Téléphone")
                row_idx = None
                for i, r in enumerate(values[1:], start=2):
                    if len(r) > tel_idx and normalize_tn_phone(r[tel_idx]) == tel_to_update:
                        row_idx = i; break
                if not row_idx: st.error("❌ الهاتف غير موجود.")
                else:
                    rem_col = EXPECTED_HEADERS.index("Remarque") + 1
                    old_remark = ws.cell(row_idx, rem_col).value or ""
                    stamp = datetime.now().strftime("%d/%m/%Y %H:%M")
                    updated = (old_remark + "\n" if old_remark else "") + f"[{stamp}] {new_note_quick.strip()}"
                    ws.update_cell(row_idx, rem_col, updated)
                    st.success("✅ تمت إضافة الملاحظة"); st.cache_data.clear()
        except Exception as e:
            st.error(f"❌ خطأ: {e}")

    st.markdown("### 🎨 اختر لون/Tag للعميل")
    tel_color_key = st.selectbox(
        "اختر العميل",
        [f"{r['Nom & Prénom']} — {format_display_phone(normalize_tn_phone(r['Téléphone']))}" for _, r in scope_df.iterrows()],
        key="tag_select"
    )
    tel_color = normalize_tn_phone(tel_color_key.split("—")[-1]) if tel_color_key else ""
    hex_color = st.color_picker("اختر اللون")
    if st.button("🖌️ تلوين"):
        try:
            ws = client.open_by_key(SPREADSHEET_ID).worksheet(employee)
            values = ws.get_all_values()
            header = values[0] if values else []
            row_idx = None
            if "Téléphone" in header:
                tel_idx = header.index("Téléphone")
                for i, r in enumerate(values[1:], start=2):
                    if len(r) > tel_idx and normalize_tn_phone(r[tel_idx]) == tel_color:
                        row_idx = i; break
            if not row_idx: st.error("❌ لم يتم إيجاد العميل.")
            else:
                color_cell = EXPECTED_HEADERS.index("Tag") + 1
                ws.update_cell(row_idx, color_cell, hex_color)
                st.success("✅ تم التلوين"); st.cache_data.clear()
        except Exception as e:
            st.error(f"❌ خطأ: {e}")

    # Add client
    st.markdown("### ➕ أضف عميل جديد")
    with st.form("emp_add_client"):
        col1, col2 = st.columns(2)
        with col1:
            nom = st.text_input("👤 الاسم و اللقب")
            tel_raw = st.text_input("📞 رقم الهاتف")
            formation = st.text_input("📚 التكوين")
            inscription = st.selectbox("🟢 التسجيل", ["Pas encore", "Inscrit"])
        with col2:
            type_contact = st.selectbox("📞 نوع الاتصال", ["Visiteur", "Appel téléphonique", "WhatsApp", "Social media"])
            date_ajout_in = st.date_input("🕓 تاريخ الإضافة", value=date.today())
            date_suivi_in = st.date_input("📆 تاريخ المتابعة", value=date.today())
        if st.form_submit_button("📥 أضف العميل"):
            try:
                ws = client.open_by_key(SPREADSHEET_ID).worksheet(employee)
                tel = normalize_tn_phone(tel_raw)
                if not(nom and tel and formation): st.error("❌ حقول أساسية ناقصة."); st.stop()
                if tel in ALL_PHONES: st.warning("⚠️ الرقم موجود مسبقًا."); st.stop()
                insc_val = "Oui" if inscription == "Inscrit" else "Pas encore"
                ws.append_row([nom, tel, type_contact, formation, "", fmt_date(date_ajout_in), fmt_date(date_suivi_in), "", insc_val, employee, ""])
                st.success("✅ تم إضافة العميل"); st.cache_data.clear()
            except Exception as e:
                st.error(f"❌ خطأ أثناء الإضافة: {e}")

    # Reassign (+ log)
    st.markdown("### 🔁 نقل عميل بين الموظفين")
    if all_employes:
        colRA, colRB = st.columns(2)
        with colRA:
            src_emp = st.selectbox("من موظّف", all_employes, key="reassign_src")
        with colRB:
            dst_emp = st.selectbox("إلى موظّف", [e for e in all_employes if e != src_emp], key="reassign_dst")
        df_src = df_all[df_all["__sheet_name"] == src_emp].copy()
        if df_src.empty:
            st.info("❕ لا يوجد عملاء عند هذا الموظّف.")
        else:
            pick = st.selectbox(
                "اختر العميل للنقل",
                [f"{r['Nom & Prénom']} — {format_display_phone(r['Téléphone'])}" for _, r in df_src.iterrows()],
                key="reassign_pick"
            )
            phone_pick = normalize_tn_phone(pick.split("—")[-1])
            if st.button("🚚 نقل الآن"):
                try:
                    sh = client.open_by_key(SPREADSHEET_ID)
                    ws_src, ws_dst = sh.worksheet(src_emp), sh.worksheet(dst_emp)
                    values = ws_src.get_all_values()
                    header = values[0] if values else []
                    row_idx = None
                    if "Téléphone" in header:
                        tel_idx = header.index("Téléphone")
                        for i, r in enumerate(values[1:], start=2):
                            if len(r) > tel_idx and normalize_tn_phone(r[tel_idx]) == phone_pick:
                                row_idx = i; break
                    if not row_idx:
                        st.error("❌ لم يتم العثور على هذا العميل.")
                    else:
                        row_values = ws_src.row_values(row_idx)
                        if len(row_values) < len(EXPECTED_HEADERS):
                            row_values += [""] * (len(EXPECTED_HEADERS) - len(row_values))
                        row_values = row_values[:len(EXPECTED_HEADERS)]
                        row_values[EXPECTED_HEADERS.index("Employe")] = dst_emp
                        ws_dst.append_row(row_values); ws_src.delete_rows(row_idx)

                        # log
                        wslog = ensure_ws_generic(REASSIGN_LOG_SHEET, REASSIGN_LOG_HEADERS)
                        wslog.append_row([
                            datetime.now(timezone.utc).isoformat(),
                            employee or "Admin", src_emp, dst_emp,
                            row_values[0], normalize_tn_phone(row_values[1])
                        ])

                        st.success(f"✅ نقل ({row_values[0]}) من {src_emp} إلى {dst_emp}"); st.cache_data.clear()
                except Exception as e:
                    st.error(f"❌ خطأ أثناء النقل: {e}")

# ---------------- Admin Page ----------------
if role == "أدمن":
    st.markdown("## 👑 لوحة الأدمِن")
    if not admin_unlocked():
        st.info("🔐 أدخل كلمة سرّ الأدمِن من اليسار لفتح الصفحة.")
    else:
        colA, colB, colC = st.columns(3)

        with colA:
            st.subheader("➕ إضافة موظّف")
            new_emp = st.text_input("اسم الموظّف الجديد")
            if st.button("إنشاء ورقة"):
                try:
                    sh = client.open_by_key(SPREADSHEET_ID)
                    titles = [w.title for w in sh.worksheets()]
                    if not new_emp or new_emp in titles:
                        st.warning("⚠️ الاسم فارغ أو موجود.")
                    else:
                        sh.add_worksheet(title=new_emp, rows="1000", cols="20")
                        sh.worksheet(new_emp).update("1:1", [EXPECTED_HEADERS])
                        st.success("✔️ تم الإنشاء"); st.cache_data.clear()
                except Exception as e:
                    st.error(f"❌ خطأ: {e}")

        with colB:
            st.subheader("➕ إضافة عميل (لأي موظّف)")
            sh = client.open_by_key(SPREADSHEET_ID)
            target_emp = st.selectbox("اختر الموظّف", all_employes, key="admin_add_emp")
            nom_a = st.text_input("👤 الاسم و اللقب", key="admin_nom")
            tel_a_raw = st.text_input("📞 الهاتف", key="admin_tel")
            formation_a = st.text_input("📚 التكوين", key="admin_form")
            type_contact_a = st.selectbox("نوع التواصل", ["Visiteur","Appel téléphonique","WhatsApp","Social media"], key="admin_type")
            inscription_a = st.selectbox("التسجيل", ["Pas encore","Inscrit"], key="admin_insc")
            date_ajout_a = st.date_input("تاريخ الإضافة", value=date.today(), key="admin_dt_add")
            suivi_date_a = st.date_input("تاريخ المتابعة", value=date.today(), key="admin_dt_suivi")
            if st.button("📥 أضف"):
                try:
                    if not (nom_a and tel_a_raw and formation_a and target_emp): st.error("❌ حقول ناقصة."); st.stop()
                    tel_a = normalize_tn_phone(tel_a_raw)
                    if tel_a in set(df_all["Téléphone_norm"]): st.warning("⚠️ الرقم موجود.")
                    else:
                        insc_val = "Oui" if inscription_a=="Inscrit" else "Pas encore"
                        ws = sh.worksheet(target_emp)
                        ws.append_row([nom_a, tel_a, type_contact_a, formation_a, "", fmt_date(date_ajout_a), fmt_date(suivi_date_a), "", insc_val, target_emp, ""])
                        st.success("✅ تمت الإضافة"); st.cache_data.clear()
                except Exception as e:
                    st.error(f"❌ خطأ: {e}")

        with colC:
            st.subheader("🗑️ حذف موظّف")
            emp_to_delete = st.selectbox("اختر الموظّف", all_employes, key="admin_del_emp")
            if st.button("❗ حذف الورقة كاملة"):
                try:
                    sh = client.open_by_key(SPREADSHEET_ID)
                    sh.del_worksheet(sh.worksheet(emp_to_delete))
                    st.success("تم الحذف"); st.cache_data.clear()
                except Exception as e:
                    st.error(f"❌ خطأ: {e}")

        st.markdown("---")
        st.subheader("📜 سجلّ نقل العملاء (Reassign Log)")
        wslog = ensure_ws_generic(REASSIGN_LOG_SHEET, REASSIGN_LOG_HEADERS)
        vals = wslog.get_all_values()
        if vals and len(vals) > 1:
            df_log = pd.DataFrame(vals[1:], columns=vals[0])
            def _fmt_ts(x):
                try:
                    return datetime.fromisoformat(x).astimezone().strftime("%Y-%m-%d %H:%M")
                except:
                    return x
            if "timestamp" in df_log.columns:
                df_log["وقت"] = df_log["timestamp"].apply(_fmt_ts)
            show_cols = ["وقت","moved_by","src_employee","dst_employee","client_name","phone"]
            show_cols = [c for c in show_cols if c in df_log.columns]
            st.dataframe(df_log[show_cols].sort_values(show_cols[0], ascending=False), use_container_width=True)
        else:
            st.caption("لا يوجد سجلّ نقل إلى حدّ الآن.")

        st.caption("صفحة الأدمِن مفتوحة لمدّة 30 دقيقة من وقت الفتح.")
