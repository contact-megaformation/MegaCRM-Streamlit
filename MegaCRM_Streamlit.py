# MegaCRM_Streamlit_App.py — Dashboard + Search + Admin + Employee + Notes + Tags + Reassign + Payments (with Password Lock)

import json
import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
from PIL import Image

# =========================
# 0) إعدادات عامة
# =========================
st.set_page_config(page_title="MegaCRM", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <div style='text-align:center;'>
        <h1 style='color:#333; margin-top: 8px;'>📊 CRM MEGA FORMATION - إدارة العملاء ميقا للتكوين</h1>
    </div>
    <hr>
    """,
    unsafe_allow_html=True
)

SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]

def make_client_and_sheet_id():
    """توليد عميل Google + Sheet ID (من secrets وإلا من ملف محلي)."""
    try:
        sa = st.secrets["gcp_service_account"]
        if hasattr(sa, "keys"):
            sa_info = dict(sa)
        elif isinstance(sa, str):
            sa_info = json.loads(sa)
        else:
            raise ValueError("Bad gcp_service_account format")
        creds = Credentials.from_service_account_info(sa_info, scopes=SCOPE)
        client = gspread.authorize(creds)
        sheet_id = st.secrets["SPREADSHEET_ID"]
        return client, sheet_id
    except Exception:
        creds = Credentials.from_service_account_file("service_account.json", scopes=SCOPE)
        client = gspread.authorize(creds)
        sheet_id = "1DV0KyDRYHofWR60zdx63a9BWBywTFhLavGAExPIa6LI"  # بدّلها إذا يلزم
        return client, sheet_id

client, SPREADSHEET_ID = make_client_and_sheet_id()

# تهيئة جلسة حماية المدفوعات
if "payments_ok" not in st.session_state:
    st.session_state["payments_ok"] = False
    st.session_state["payments_ok_at"] = None

# =========================
# 1) ثوابت + Helpers
# =========================
EXPECTED_HEADERS = [
    "Nom & Prénom","Téléphone","Type de contact","Formation",
    "Remarque","Date ajout","Date de suivi","Alerte",
    "Inscription","Employe","Tag"
]

def fmt_date(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if isinstance(d, date) else ""

def normalize_tn_phone(s: str) -> str:
    """Digits only. If 8-digit Tunisian local -> prefix 216. If starts with 216 keep. Else return digits."""
    digits = "".join(ch for ch in str(s) if ch.isdigit())
    if digits.startswith("216"):
        return digits
    if len(digits) == 8:
        return "216" + digits
    return digits

def format_display_phone(s: str) -> str:
    d = "".join(ch for ch in str(s) if ch.isdigit())
    return f"+{d}" if d else ""

def find_row_by_phone(ws, phone_digits: str) -> int | None:
    values = ws.get_all_values()
    if not values:
        return None
    header = values[0]
    if "Téléphone" not in header:
        return None
    tel_idx = header.index("Téléphone")
    for i, r in enumerate(values[1:], start=2):
        if len(r) > tel_idx and normalize_tn_phone(r[tel_idx]) == phone_digits:
            return i
    return None

# ستايل
def color_tag(val):
    if isinstance(val, str) and val.strip().startswith("#") and len(val.strip()) == 7:
        return f"background-color: {val}; color: white;"
    return ""

def mark_alert_cell(val: str):
    s = str(val).strip()
    if not s:
        return ''
    if "متأخرة" in s:
        return 'background-color: #ffe6b3; color: #7a4e00'  # متأخرة = أصفر
    return 'background-color: #ffcccc; color: #7a0000'     # تنبيه اليوم أو نص يدوي = أحمر

def highlight_inscrit_row(row: pd.Series):
    insc = str(row.get("Inscription", "")).strip().lower()
    is_inscrit = insc in ("inscrit", "oui")
    return ['background-color: #d6f5e8' if is_inscrit else '' for _ in row.index]

# =========================
# 2) تحميل الداتا من جميع أوراق الموظفين
# =========================
@st.cache_data(ttl=600)
def load_all_data():
    sh = client.open_by_key(SPREADSHEET_ID)
    worksheets = sh.worksheets()
    all_dfs, all_employes = [], []

    for ws in worksheets:
        all_employes.append(ws.title)

        rows = ws.get_all_values()
        if not rows:
            ws.update("1:1", [EXPECTED_HEADERS])
            rows = ws.get_all_values()

        try:
            ws.update("1:1", [EXPECTED_HEADERS])
            rows = ws.get_all_values()
        except Exception:
            pass

        data_rows = rows[1:] if len(rows) > 1 else []
        fixed_rows = []
        for r in data_rows:
            r = list(r) if r is not None else []
            if len(r) < len(EXPECTED_HEADERS):
                r = r + [""] * (len(EXPECTED_HEADERS) - len(r))
            else:
                r = r[:len(EXPECTED_HEADERS)]
            fixed_rows.append(r)

        df = pd.DataFrame(fixed_rows, columns=EXPECTED_HEADERS)
        df["__sheet_name"] = ws.title
        all_dfs.append(df)

    big = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame(columns=EXPECTED_HEADERS + ["__sheet_name"])
    return big, all_employes

df_all, all_employes = load_all_data()
df_emp = pd.DataFrame()
filtered_df = pd.DataFrame()

# =========================
# 3) أعمدة مشتقة + تنظيف تنبيهات/تواريخ
# =========================
if not df_all.empty:
    df_all["DateAjout_dt"] = pd.to_datetime(df_all["Date ajout"], dayfirst=True, errors="coerce")
    df_all["DateSuivi_dt"] = pd.to_datetime(df_all["Date de suivi"], dayfirst=True, errors="coerce")
    df_all["Mois"] = df_all["DateAjout_dt"].dt.strftime("%m-%Y")

    today = datetime.now().date()
    base_alert = df_all["Alerte"].fillna("").astype(str).str.strip()

    dsv_date = df_all["DateSuivi_dt"].dt.date
    due_today = dsv_date.eq(today).fillna(False)
    overdue = dsv_date.lt(today).fillna(False)

    df_all["Alerte_view"] = base_alert
    df_all.loc[base_alert.eq("") & overdue, "Alerte_view"] = "⚠️ متابعة متأخرة"
    df_all.loc[base_alert.eq("") & due_today, "Alerte_view"] = "⏰ متابعة اليوم"

    df_all["Téléphone_norm"] = df_all["Téléphone"].apply(normalize_tn_phone)
    ALL_PHONES = set(df_all["Téléphone_norm"].dropna().astype(str))

    # تنظيف المسجّلين
    df_all["Inscription_norm"] = df_all["Inscription"].fillna("").astype(str).str.strip().str.lower()
    inscrit_mask = df_all["Inscription_norm"].isin(["oui", "inscrit"])
    df_all.loc[inscrit_mask, "Date de suivi"] = ""
    df_all.loc[inscrit_mask, "Alerte_view"] = ""
else:
    df_all["Alerte_view"] = ""
    df_all["Mois"] = ""
    df_all["Téléphone_norm"] = ""
    ALL_PHONES = set()

# شعار في الـsidebar (اختياري)
try:
    st.sidebar.image(Image.open("logo.png"), use_container_width=True)
except Exception:
    pass

# =========================
# 4) اختيار الدور + الموظف
# =========================
role = st.sidebar.selectbox("الدور", ["موظف", "أدمن"])
employee = st.sidebar.selectbox("اختر اسمك", all_employes) if role == "موظف" else None

# =========================
# 5) Dashboard
# =========================
st.subheader("لوحة إحصائيات سريعة")

df_dash = df_all.copy()
if df_dash.empty:
    st.info("ما فماش داتا للعرض.")
else:
    df_dash["DateAjout_dt"] = pd.to_datetime(df_dash.get("Date ajout"), dayfirst=True, errors="coerce")
    df_dash["DateSuivi_dt"] = pd.to_datetime(df_dash.get("Date de suivi"), dayfirst=True, errors="coerce")

    today = datetime.now().date()
    df_dash["Inscription_norm"] = df_dash["Inscription"].fillna("").astype(str).str.strip().str.lower()
    df_dash["Alerte_norm"] = df_dash["Alerte_view"].fillna("").astype(str).str.strip()

    # أرقام اليوم
    added_today_mask = df_dash["DateAjout_dt"].dt.date.eq(today)
    registered_today_mask = df_dash["Inscription_norm"].isin(["oui", "inscrit"]) & added_today_mask
    alert_now_mask = df_dash["Alerte_norm"].ne("")

    total_clients = int(len(df_dash))
    added_today = int(added_today_mask.sum())
    registered_today = int(registered_today_mask.sum())
    alerts_now = int(alert_now_mask.sum())
    registered_total = int((df_dash["Inscription_norm"] == "oui").sum())
    rate = round((registered_total / total_clients) * 100, 2) if total_clients else 0.0

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1: st.metric("👥 إجمالي العملاء", f"{total_clients}")
    with c2: st.metric("🆕 المضافون اليوم", f"{added_today}")
    with c3: st.metric("✅ المسجّلون اليوم", f"{registered_today}")
    with c4: st.metric("🚨 التنبيهات الحالية", f"{alerts_now}")
    with c5: st.metric("📈 نسبة التسجيل الإجمالية", f"{rate}%")

    # حسب الموظف
    df_dash["__added_today"] = added_today_mask
    df_dash["__reg_today"]   = registered_today_mask
    df_dash["__has_alert"]   = alert_now_mask

    grp_base = (
        df_dash.groupby("__sheet_name", dropna=False)
        .agg(
            Clients=("Nom & Prénom", "count"),
            Inscrits=("Inscription_norm", lambda x: (x == "oui").sum()),
            تنبيهات=("__has_alert", "sum"),
        )
        .reset_index()
        .rename(columns={"__sheet_name": "الموظف"})
    )
    today_by_emp = (
        df_dash.groupby("__sheet_name", dropna=False)
        .agg(
            مضافون_اليوم=("__added_today", "sum"),
            مسجلون_اليوم=("__reg_today", "sum"),
        )
        .reset_index()
        .rename(columns={"__sheet_name": "الموظف"})
    )
    grp = grp_base.merge(today_by_emp, on="الموظف", how="left")
    grp["% تسجيل"] = ((grp["Inscrits"] / grp["Clients"]).replace([float("inf"), float("nan")], 0) * 100).round(2)
    grp = grp.sort_values(by=["تنبيهات", "Clients"], ascending=[False, False])

    st.markdown("#### حسب الموظّف")
    st.dataframe(grp, use_container_width=True)

# =========================
# 6) بحث عام بالهاتف
# =========================
st.subheader("🔎 بحث عام برقم الهاتف")
global_phone = st.text_input("اكتب رقم الهاتف (8 أرقام محلية أو 216XXXXXXXX)", key="global_phone_all")

if global_phone.strip():
    q_norm = normalize_tn_phone(global_phone)
    search_df = df_all.copy()
    if "Téléphone_norm" not in search_df.columns:
        search_df["Téléphone_norm"] = search_df["Téléphone"].apply(normalize_tn_phone)
    if "Alerte_view" in search_df.columns:
        search_df["Alerte"] = search_df["Alerte_view"]
    search_df = search_df[search_df["Téléphone_norm"] == q_norm]

    if search_df.empty:
        st.info("❕ ما لقيتش عميل بهذا الرقم في كامل النظام.")
    else:
        st.success(f"✅ تم العثور على {len(search_df)} نتيجة (على كامل الباز).")
        display_cols = [c for c in EXPECTED_HEADERS if c in search_df.columns]
        if "Employe" in search_df.columns and "Employe" not in display_cols:
            display_cols.append("Employe")
        styled_global = (
            search_df[display_cols]
            .style.apply(highlight_inscrit_row, axis=1)
            .applymap(mark_alert_cell, subset=["Alerte"])
        )
        st.dataframe(styled_global, use_container_width=True)
        st.markdown("---")

# =========================
# 7) لوحة الأدمــن
# =========================
if role == "أدمن":
    st.subheader("👨‍💼 إدارة الموظفين")

    # إضافة موظف
    st.markdown("### ➕ إضافة موظف")
    new_emp = st.text_input("اسم الموظف الجديد")
    if st.button("إنشاء ورقة جديدة"):
        try:
            sh = client.open_by_key(SPREADSHEET_ID)
            if new_emp and new_emp not in [w.title for w in sh.worksheets()]:
                sh.add_worksheet(title=new_emp, rows="1000", cols="20")
                sh.worksheet(new_emp).update("1:1", [EXPECTED_HEADERS])
                st.success("✔️ تم إنشاء الموظف بنجاح")
                st.cache_data.clear()
            else:
                st.warning("⚠️ الاسم فارغ أو الموظف موجود مسبقًا")
        except Exception as e:
            st.error(f"❌ خطأ: {e}")

    # إضافة عميل لأي موظف
    st.markdown("### ➕ إضافة عميل جديد (من الأدمن)")
    with st.form("admin_add_client_form"):
        col1, col2 = st.columns(2)
        with col1:
            nom_a = st.text_input("👤 الاسم و اللقب", key="admin_nom")
            tel_a_raw = st.text_input("📞 رقم الهاتف", key="admin_tel")
            formation_a = st.text_input("📚 التكوين", key="admin_formation")
            inscription_a = st.selectbox("🟢 حالة التسجيل", ["Pas encore", "Inscrit"], key="admin_insc")
        with col2:
            type_contact_a = st.selectbox("📞 نوع التواصل", ["Visiteur", "Appel téléphonique", "WhatsApp", "Social media"], key="admin_type")
            date_ajout_a = st.date_input("🕓 تاريخ الإضافة", value=date.today(), key="admin_date_ajout")
            suivi_date_a = st.date_input("📆 تاريخ المتابعة", value=date.today(), key="admin_suivi")
            employee_choice = st.selectbox("👨‍💼 الموظف", all_employes, key="admin_emp")

        add_admin_client = st.form_submit_button("📥 أضف العميل")
        if add_admin_client:
            if not (nom_a and tel_a_raw and formation_a and employee_choice):
                st.error("❌ الرجاء ملء جميع الحقول الأساسية")
            else:
                try:
                    ws = client.open_by_key(SPREADSHEET_ID).worksheet(employee_choice)
                    tel_a = normalize_tn_phone(tel_a_raw)
                    if tel_a in ALL_PHONES:
                        st.warning("⚠️ رقم الهاتف موجود مسبقًا في النظام")
                    else:
                        insc_val = "Oui" if inscription_a == "Inscrit" else "Pas encore"
                        ws.append_row([
                            nom_a, tel_a, type_contact_a, formation_a, "",
                            fmt_date(date_ajout_a), fmt_date(suivi_date_a), "", insc_val, employee_choice, ""
                        ])
                        st.success(f"✅ تم إضافة العميل ({nom_a}) إلى موظّف: {employee_choice}")
                        st.cache_data.clear()
                except Exception as e:
                    st.error(f"❌ خطأ أثناء الإضافة: {e}")

    # نقل عميل
    st.markdown("### 🔁 نقل عميل بين الموظفين")
    colRA, colRB = st.columns(2)
    with colRA:
        src_emp = st.selectbox("من موظّف", all_employes, key="reassign_src")
    with colRB:
        dst_emp = st.selectbox("إلى موظّف", [e for e in all_employes if e != src_emp], key="reassign_dst")

    df_src = df_all[df_all["__sheet_name"] == src_emp].copy()
    if df_src.empty:
        st.info("❕ لا يوجد عملاء عند هذا الموظّف.")
    else:
        df_src["_tel_norm"] = df_src["Téléphone"].apply(normalize_tn_phone)
        pick = st.selectbox(
            "اختر العميل للنقل",
            [f"{r['Nom & Prénom']} — {format_display_phone(r['Téléphone'])}" for _, r in df_src.iterrows()],
            key="reassign_pick"
        )
        phone_pick = normalize_tn_phone(pick.split("—")[-1])

        if st.button("🚚 نقل الآن"):
            try:
                sh = client.open_by_key(SPREADSHEET_ID)
                ws_src = sh.worksheet(src_emp)
                ws_dst = sh.worksheet(dst_emp)

                row_idx = find_row_by_phone(ws_src, phone_pick)
                if not row_idx:
                    st.error("❌ لم يتم العثور على هذا العميل في ورقة المصدر.")
                else:
                    row_values = ws_src.row_values(row_idx)
                    if len(row_values) < len(EXPECTED_HEADERS):
                        row_values += [""] * (len(EXPECTED_HEADERS) - len(row_values))
                    row_values[EXPECTED_HEADERS.index("Employe")] = dst_emp
                    ws_dst.append_row(row_values)
                    ws_src.delete_rows(row_idx)
                    st.success(f"✅ تم نقل العميل ({row_values[0]}) من {src_emp} إلى {dst_emp}")
                    st.cache_data.clear()
            except Exception as e:
                st.error(f"❌ خطأ أثناء النقل: {e}")

    st.markdown("### 🗑️ حذف موظف")
    emp_to_delete = st.selectbox("اختر موظفًا للحذف", all_employes, key="delete_emp")
    if st.button("❗ احذف هذا الموظف"):
        st.warning("⚠️ لا يمكن الحذف مباشرة عبر Streamlit لأسباب أمنية. احذف يدويًا من Google Sheets.")

# =========================
# 8) واجهة الموظّف: عرض + ملاحظات + Tag + إضافة عميل
# =========================
if role == "موظف" and employee:
    st.subheader(f"📁 لوحة {employee}")
    df_emp = df_all[df_all["__sheet_name"] == employee].copy()

    # فلترة بالشهر
    if not df_emp.empty:
        df_emp["DateAjout_dt"] = pd.to_datetime(df_emp["Date ajout"], dayfirst=True, errors="coerce")
        df_emp = df_emp.dropna(subset=["DateAjout_dt"])
        df_emp["Mois"] = df_emp["DateAjout_dt"].dt.strftime("%m-%Y")
        month_filter = st.selectbox("🗓️ اختر شهر الإضافة", sorted(df_emp["Mois"].dropna().unique(), reverse=True))
        filtered_df = df_emp[df_emp["Mois"] == month_filter].copy()
    else:
        st.warning("⚠️ لا يوجد أي عملاء بعد. قاعدة البيانات فارغة.")
        filtered_df = pd.DataFrame()

    # عدّاد مضافين بلا ملاحظات
    if not filtered_df.empty:
        pending_mask = filtered_df["Remarque"].fillna("").astype(str).str.strip() == ""
        pending_no_notes = int(pending_mask.sum())
        st.markdown("### 📊 متابعتك")
        st.metric("⏳ مضافين بلا ملاحظات", pending_no_notes)

        formations = sorted([f for f in filtered_df["Formation"].dropna().astype(str).unique() if f.strip()])
        formation_choice = st.selectbox("📚 فلترة بالتكوين", ["الكل"] + formations)
        if formation_choice != "الكل":
            filtered_df = filtered_df[filtered_df["Formation"].astype(str) == formation_choice]

    # عرض جدول
    def render_table(df_disp: pd.DataFrame):
        if df_disp.empty:
            st.info("لا توجد بيانات في هذا الفلتر.")
            return
        _df = df_disp.copy()
        if "Alerte_view" in _df.columns:
            _df["Alerte"] = _df["Alerte_view"]
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

    if not filtered_df.empty and st.checkbox("🔴 عرض العملاء الذين لديهم تنبيهات"):
        _df = filtered_df.copy()
        if "Alerte_view" in _df.columns:
            _df["Alerte"] = _df["Alerte_view"]
        alerts_df = _df[_df["Alerte"].fillna("").astype(str).str.strip() != ""]
        st.markdown("### 🚨 عملاء مع تنبيهات")
        render_table(alerts_df)

    # تعديل بيانات عميل
    if not df_emp.empty:
        st.markdown("### ✏️ تعديل بيانات عميل")
        df_emp_edit = df_emp.copy()
        df_emp_edit["Téléphone_norm"] = df_emp_edit["Téléphone"].apply(normalize_tn_phone)

        phone_choices = {
            f"[{i}] {row['Nom & Prénom']} — {format_display_phone(row['Téléphone_norm'])}": row["Téléphone_norm"]
            for i, row in df_emp_edit.iterrows()
            if str(row["Téléphone"]).strip() != ""
        }

        if phone_choices:
            chosen_key = st.selectbox("اختر العميل (بالاسم/الهاتف)", list(phone_choices.keys()), key="edit_pick")
            chosen_phone = phone_choices.get(chosen_key, "")

            cur_row = df_emp_edit[df_emp_edit["Téléphone_norm"] == chosen_phone].iloc[0] if chosen_phone else None
            cur_name = str(cur_row["Nom & Prénom"]) if cur_row is not None else ""
            cur_tel_raw = str(cur_row["Téléphone"]) if cur_row is not None else ""
            cur_formation = str(cur_row["Formation"]) if cur_row is not None else ""
            cur_remark = str(cur_row.get("Remarque", "")) if cur_row is not None else ""

            cur_ajout = pd.to_datetime(cur_row["Date ajout"], dayfirst=True, errors="coerce").date() if cur_row is not None else date.today()
            cur_suivi = pd.to_datetime(cur_row["Date de suivi"], dayfirst=True, errors="coerce").date() if cur_row is not None and str(cur_row["Date de suivi"]).strip() else date.today()
            cur_insc = str(cur_row["Inscription"]).strip().lower() if cur_row is not None else ""

            col1, col2 = st.columns(2)
            with col1:
                new_name = st.text_input("👤 الاسم و اللقب", value=cur_name, key="edit_name_txt")
                new_phone_raw = st.text_input("📞 رقم الهاتف (8 أرقام أو 216XXXXXXXX)", value=cur_tel_raw, key="edit_phone_txt")
                new_formation = st.text_input("📚 التكوين (Formation)", value=cur_formation, key="edit_formation_txt")
            with col2:
                new_ajout = st.date_input("🕓 تاريخ الإضافة", value=cur_ajout, key="edit_ajout_dt")
                new_suivi = st.date_input("📆 تاريخ المتابعة", value=cur_suivi, key="edit_suivi_dt")
                new_insc = st.selectbox("🟢 التسجيل", ["Pas encore", "Inscrit"], index=(1 if cur_insc == "oui" else 0), key="edit_insc_sel")

            new_remark_full = st.text_area("🗒️ ملاحظة (سيتم استبدال المحتوى الحالي بهذا النص)", value=cur_remark, key="edit_remark_txt")
            extra_note = st.text_area("➕ أضف ملاحظة جديدة (سوف تُضاف مع الطابع الزمني)", placeholder="اكتب ملاحظة لإلحاقها…", key="append_note_txt")

            if st.button("💾 حفظ التعديلات", key="save_all_edits"):
                try:
                    ws = client.open_by_key(SPREADSHEET_ID).worksheet(employee)
                    row_idx = find_row_by_phone(ws, chosen_phone)
                    if not row_idx:
                        st.error("❌ تعذّر إيجاد الصف لهذا الهاتف.")
                    else:
                        col_map = {h: EXPECTED_HEADERS.index(h) + 1 for h in [
                            "Nom & Prénom", "Téléphone", "Formation",
                            "Date ajout", "Date de suivi", "Inscription", "Remarque"
                        ]}

                        new_phone_norm = normalize_tn_phone(new_phone_raw)
                        if not new_name.strip():
                            st.error("❌ الاسم و اللقب إجباري."); st.stop()
                        if not new_phone_norm.strip():
                            st.error("❌ رقم الهاتف إجباري."); st.stop()

                        phones_except_current = set(ALL_PHONES) - {chosen_phone}
                        if new_phone_norm in phones_except_current:
                            st.error("⚠️ الرقم موجود مسبقًا في النظام."); st.stop()

                        ws.update_cell(row_idx, col_map["Nom & Prénom"], new_name.strip())
                        ws.update_cell(row_idx, col_map["Téléphone"], new_phone_norm)
                        ws.update_cell(row_idx, col_map["Formation"], new_formation.strip())
                        ws.update_cell(row_idx, col_map["Date ajout"], fmt_date(new_ajout))
                        ws.update_cell(row_idx, col_map["Date de suivi"], fmt_date(new_suivi))
                        ws.update_cell(row_idx, col_map["Inscription"], "Oui" if new_insc == "Inscrit" else "Pas encore")

                        if new_remark_full.strip() != cur_remark.strip():
                            ws.update_cell(row_idx, col_map["Remarque"], new_remark_full.strip())

                        if extra_note.strip():
                            old_rem = ws.cell(row_idx, col_map["Remarque"]).value or ""
                            stamp = datetime.now().strftime("%d/%m/%Y %H:%M")
                            appended = (old_rem + "\n" if old_rem else "") + f"[{stamp}] {extra_note.strip()}"
                            ws.update_cell(row_idx, col_map["Remarque"], appended)

                        st.success("✅ تم حفظ التعديلات")
                        st.cache_data.clear()
                except Exception as e:
                    st.error(f"❌ خطأ أثناء التعديل: {e}")

    # ملاحظات سريعة
    if not df_emp.empty:
        st.markdown("### 📝 أضف ملاحظة (سريعة)")
        scope_df = filtered_df if not filtered_df.empty else df_emp
        scope_df = scope_df.copy()
        scope_df["Téléphone_norm"] = scope_df["Téléphone"].apply(normalize_tn_phone)
        tel_to_update_key = st.selectbox(
            "اختر العميل",
            [f"{r['Nom & Prénom']} — {format_display_phone(normalize_tn_phone(r['Téléphone']))}" for _, r in scope_df.iterrows()],
            key="note_quick_pick"
        )
        tel_to_update = normalize_tn_phone(tel_to_update_key.split("—")[-1])
        new_note_quick = st.text_area("🗒️ ملاحظة جديدة (سيضاف لها طابع زمني)", key="note_quick_txt")
        if st.button("📌 أضف الملاحظة", key="note_quick_btn"):
            if new_note_quick.strip() == "":
                st.warning("⚠️ الملاحظة فارغة!")
            else:
                try:
                    ws = client.open_by_key(SPREADSHEET_ID).worksheet(employee)
                    row_idx = find_row_by_phone(ws, tel_to_update)
                    if not row_idx:
                        st.error("❌ لم يتم إيجاد العميل بالهاتف.")
                    else:
                        rem_col = EXPECTED_HEADERS.index("Remarque") + 1
                        old_remark = ws.cell(row_idx, rem_col).value or ""
                        stamp = datetime.now().strftime("%d/%m/%Y %H:%M")
                        updated = (old_remark + "\n" if old_remark else "") + f"[{stamp}] {new_note_quick.strip()}"
                        ws.update_cell(row_idx, rem_col, updated)
                        st.success("✅ تمت إضافة الملاحظة")
                        st.cache_data.clear()
                except Exception as e:
                    st.error(f"❌ خطأ أثناء حفظ الملاحظة: {e}")

    # Tag
    if not df_emp.empty:
        st.markdown("### 🎨 اختر لون/Tag للعميل")
        scope_df = filtered_df if not filtered_df.empty else df_emp
        scope_df = scope_df.copy()
        scope_df["Téléphone_norm"] = scope_df["Téléphone"].apply(normalize_tn_phone)
        tel_color_key = st.selectbox(
            "اختر العميل",
            [f"{r['Nom & Prénom']} — {format_display_phone(normalize_tn_phone(r['Téléphone']))}" for _, r in scope_df.iterrows()],
            key="tag_select"
        )
        tel_color = normalize_tn_phone(tel_color_key.split("—")[-1])
        hex_color = st.color_picker("اختر اللون")
        if st.button("🖌️ تلوين"):
            try:
                ws = client.open_by_key(SPREADSHEET_ID).worksheet(employee)
                row_idx = find_row_by_phone(ws, tel_color)
                if not row_idx:
                    st.error("❌ لم يتم إيجاد العميل.")
                else:
                    color_cell = EXPECTED_HEADERS.index("Tag") + 1
                    ws.update_cell(row_idx, color_cell, hex_color)
                    st.success("✅ تم التلوين")
                    st.cache_data.clear()
            except Exception as e:
                st.error(f"❌ خطأ أثناء الحفظ: {e}")

    # إضافة عميل (الموظف)
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

        submitted = st.form_submit_button("📥 أضف العميل")
        if submitted:
            if not (nom and tel_raw and formation):
                st.error("❌ الرجاء ملء جميع الحقول الأساسية")
            else:
                try:
                    ws = client.open_by_key(SPREADSHEET_ID).worksheet(employee)
                    tel = normalize_tn_phone(tel_raw)
                    if tel in ALL_PHONES:
                        st.warning("⚠️ الرقم موجود مسبقًا في النظام")
                    else:
                        insc_val = "Oui" if inscription == "Inscrit" else "Pas encore"
                        ws.append_row([
                            nom, tel, type_contact, formation, "",
                            fmt_date(date_ajout_in), fmt_date(date_suivi_in), "", insc_val, employee, ""
                        ])
                        st.success("✅ تم إضافة العميل")
                        st.cache_data.clear()
                except Exception as e:
                    st.error(f"❌ خطأ أثناء الإضافة: {e}")

    # واتساب
    if not df_emp.empty:
        st.markdown("### 📲 تواصل عبر واتساب")
        df_emp_w = df_emp.copy()
        df_emp_w["Téléphone_norm"] = df_emp_w["Téléphone"].apply(normalize_tn_phone)
        choice = st.selectbox(
            "اختر العميل",
            [f"{r['Nom & Prénom']} — {format_display_phone(normalize_tn_phone(r['Téléphone']))}" for _, r in df_emp_w.iterrows()],
            key="wa_select"
        )
        tel_norm = normalize_tn_phone(choice.split("—")[-1])
        default_msg = "Bonjour, c'est MegaFormation. On vous contacte pour le suivi de votre formation."
        msg = st.text_input("نص الرسالة", value=default_msg)
        from urllib.parse import quote
        wa_url = f"https://wa.me/{tel_norm}?text={quote(msg)}"
        st.link_button("📤 فتح واتساب", wa_url)

# =========================
# 9) Payments (دوال + UI محمية بباسورد)
# =========================

# ===== حماية المدفوعات بكلمة سرّ (خاصة بكل موظّف) =====
from datetime import datetime, timedelta
import streamlit as st

def _get_pay_password_for(user_login: str | None) -> str:
    """
    يرجّع كلمة السرّ الخاصة بالموظف user_login من secrets.toml
    التركيب المطلوب في secrets:
    [payments_protect]
    password = "1234"                       # اختيارية (افتراضي)
    [payments_protect.by_user]
    "Olfa Crm" = "olfa123"
    "Ons Crm"  = "ons456"
    ...
    """
    try:
        sec = st.secrets["payments_protect"]
    except Exception:
        return "1234"

    # per-user
    if user_login and "by_user" in sec and user_login in sec["by_user"]:
        return str(sec["by_user"][user_login])

    # default
    return str(sec.get("password", "1234"))


def _lock_key_for(user_login: str) -> str:
    return f"paylock_{user_login}"

def payments_unlocked(user_login: str) -> bool:
    """
    القفل مربوط بكل موظف: نخزّنو حقلين في session_state:
    - paylock_<user> : True/False
    - paylock_<user>_at : timestamp وقت الفتح
    """
    key = _lock_key_for(user_login)
    ok = st.session_state.get(key, False)
    ts = st.session_state.get(f"{key}_at")
    if not ok or ts is None:
        return False
    # صلاحية 15 دقيقة
    if datetime.now() - ts <= timedelta(minutes=15):
        return True
    # انتهت الصلاحية
    st.session_state[key] = False
    st.session_state[f"{key}_at"] = None
    return False


def reset_lock_on_employee_change(current_user: str):
    """
    إذا تبدّل الموظف من X إلى Y نلغي سريان الفتح القديم،
    باش ما يلقى القسم مفتوح تلقائياً.
    """
    prev = st.session_state.get("_pay_prev_user")
    if prev is None:
        st.session_state["_pay_prev_user"] = current_user
        return
    if prev != current_user:
        # نقفل القديم ونحدّث المتغيّر
        prev_key = _lock_key_for(prev)
        st.session_state[prev_key] = False
        st.session_state[f"{prev_key}_at"] = None
        st.session_state["_pay_prev_user"] = current_user


def payments_lock_ui(user_login: str):
    """واجهة القفل/الفتح للموظف الحالي فقط."""
    reset_lock_on_employee_change(user_login)

    with st.expander("🔒 حماية المدفوعات (Password)", expanded=not payments_unlocked(user_login)):
        if payments_unlocked(user_login):
            col1, col2 = st.columns([3,1])
            with col1:
                st.success(f"تم فتح قسم المدفوعات لـ {user_login} (ينتهي بعد 15 دقيقة).")
            with col2:
                if st.button("🔐 قفل الآن"):
                    key = _lock_key_for(user_login)
                    st.session_state[key] = False
                    st.session_state[f"{key}_at"] = None
                    st.info("تم القفل.")
        else:
            cfg = _get_pay_password_for(user_login)
            pwd = st.text_input("أدخل كلمة السرّ لفتح قسم المدفوعات", type="password", key=f"pwd_{user_login}")
            if st.button("🔓 فتح"):
                if pwd == cfg:
                    key = _lock_key_for(user_login)
                    st.session_state[key] = True
                    st.session_state[f"{key}_at"] = datetime.now()
                    st.success("تم الفتح لمدة 15 دقيقة.")
                else:
                    st.error("كلمة سرّ غير صحيحة.")

    # Debug اختيارية:
    dprint("👤 employee =", user_login)
    dprint("🔑 expected password =", _get_pay_password_for(user_login))
    dprint("🔓 unlocked? =", payments_unlocked(user_login))
# --- ورقة الدفوعات ---
PAY_HEADERS_STD = ["Tel", "Formation", "Prix", "Montant", "Date", "Reste"]
PAY_ALIASES = {
    "tel": "Tel", "téléphone": "Tel", "telephone": "Tel", "num": "Tel",
    "formation": "Formation",
    "prix": "Prix", "prix total": "Prix", "price": "Prix", "coût": "Prix", "cout": "Prix",
    "montant": "Montant", "montant payé": "Montant", "paid": "Montant", "payment": "Montant",
    "date": "Date",
    "reste": "Reste", "remaining": "Reste", "solde": "Reste"
}

def _to_float(x):
    s = str(x).strip()
    if not s:
        return 0.0
    for ch in ["DT", "TND", "د", "د.", "دينار", "€", "$"]:
        s = s.replace(ch, "")
    s = s.replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0

def ensure_payments_ws(sh, employee_name: str):
    ws_name = f"{employee_name}_PAIEMENTS"
    try:
        ws = sh.worksheet(ws_name)
    except Exception:
        ws = sh.add_worksheet(title=ws_name, rows="2000", cols="10")
        ws.update("1:1", [PAY_HEADERS_STD])
        return ws

    rows = ws.get_all_values()
    if not rows:
        ws.update("1:1", [PAY_HEADERS_STD])
    else:
        header = [h.strip() for h in rows[0]]
        if any(h not in PAY_HEADERS_STD for h in header) or len(header) != len(PAY_HEADERS_STD):
            ws.update("1:1", [PAY_HEADERS_STD])
    return ws

def _read_payments_for(sh, phone_norm: str, employee_name: str) -> pd.DataFrame:
    ws = ensure_payments_ws(sh, employee_name)
    rows = ws.get_all_values()
    if not rows or len(rows) == 1:
        return pd.DataFrame(columns=PAY_HEADERS_STD)

    data = rows[1:]
    df = pd.DataFrame(data, columns=PAY_HEADERS_STD[:len(rows[0])])
    # إكمال الأعمدة الناقصة
    for col in PAY_HEADERS_STD:
        if col not in df.columns:
            df[col] = ""

    df = df[PAY_HEADERS_STD]

    df["Tel"] = df["Tel"].apply(normalize_tn_phone)
    df = df[df["Tel"] == str(phone_norm)]

    if not df.empty:
        df["Prix"] = df["Prix"].apply(_to_float)
        df["Montant"] = df["Montant"].apply(_to_float)
        df["Reste"] = df["Reste"].apply(_to_float)
        try:
            df["Date_dt"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
        except Exception:
            df["Date_dt"] = pd.NaT
        df = df.sort_values(by=["Date_dt"], ascending=True).drop(columns=["Date_dt"], errors="ignore")
    return df

def _append_payment(sh, employee_name: str, phone_norm: str, formation: str, prix_total: float, montant: float, dt: date):
    ws = ensure_payments_ws(sh, employee_name)
    existing = _read_payments_for(sh, phone_norm, employee_name)
    sum_old = float(existing["Montant"].sum()) if not existing.empty else 0.0
    reste = max(float(prix_total) - (sum_old + float(montant)), 0.0)

    row = [
        phone_norm,
        str(formation or ""),
        f"{float(prix_total):.2f}",
        f"{float(montant):.2f}",
        fmt_date(dt),
        f"{reste:.2f}"
    ]
    ws.append_row(row)
    return reste

# --- واجهة المدفوعات (محميّة) ---
if role == "موظف" and employee:
    st.markdown("## 💳 الدفوعات")

    # واجهة القفل/الفتح
    payments_lock_ui(employee)

    if payments_unlocked():
        df_emp_for_pay = df_all[df_all["__sheet_name"] == employee].copy()
        df_emp_for_pay["Téléphone_norm"] = df_emp_for_pay["Téléphone"].apply(normalize_tn_phone)
        pay_choices = {
            f"{row['Nom & Prénom']} — {format_display_phone(row['Téléphone_norm'])}": row["Téléphone_norm"]
            for _, row in df_emp_for_pay.iterrows() if str(row["Téléphone"]).strip() != ""
        }

        if not pay_choices:
            st.info("لا يوجد عملاء لاختيارهم للدفوعات.")
        else:
            pay_key = st.selectbox("اختر العميل (للـدفوعات)", list(pay_choices.keys()), key="pay_pick")
            pay_phone = pay_choices.get(pay_key, "")
            cur_row = df_emp_for_pay[df_emp_for_pay["Téléphone_norm"] == pay_phone].iloc[0]
            cur_formation = str(cur_row.get("Formation", ""))

            sh = client.open_by_key(SPREADSHEET_ID)
            df_payments = _read_payments_for(sh, pay_phone, employee)
            if df_payments.empty:
                st.info("لا توجد دفوعات سابقة لهذا العميل.")
            else:
                st.dataframe(df_payments, use_container_width=True)

            with st.form("pay_add_form"):
                colp1, colp2, colp3 = st.columns(3)
                with colp1:
                    prix_total = st.number_input(
                        "💰 سعر التكوين (Prix)",
                        min_value=0.0, step=10.0,
                        value=float(df_payments["Prix"].max()) if not df_payments.empty else 0.0
                    )
                with colp2:
                    montant = st.number_input("💵 المبلغ المدفوع (Montant)", min_value=0.0, step=10.0)
                with colp3:
                    date_pay = st.date_input("📅 تاريخ الدفع", value=date.today())

                submitted_pay = st.form_submit_button("➕ أضف الدفعة")
                if submitted_pay:
                    if prix_total <= 0 or montant <= 0:
                        st.warning("رجاءً أدخل قيم موجبة لسعر التكوين والمبلغ المدفوع.")
                    else:
                        try:
                            reste = _append_payment(sh, employee, pay_phone, cur_formation, prix_total, montant, date_pay)
                            st.success(f"✅ تمت الإضافة. المتبقي الآن: {reste:.2f}")
                            st.cache_data.clear()
                        except Exception as e:
                            st.error(f"❌ خطأ أثناء إضافة الدفعة: {e}")
    else:
        st.info("🔒 قسم المدفوعات مقفول. أدخل كلمة السرّ أعلاه لفتح التفاصيل.")
