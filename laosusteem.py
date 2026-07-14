import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime, Enum, text, Date
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, joinedload
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import enum
import io
# --- UUS: LEHE SEADISTUS JA SISSELOGIMINE ---
st.set_page_config(page_title="Nutikas Laosüsteem", page_icon="📦", layout="wide", initial_sidebar_state="expanded")

def check_password():
    """Kontrollib, kas kasutaja on õige parooli sisestanud."""
    def password_entered():
        # Võrdleb sisestatud parooli Streamlit Secretsis oleva parooliga
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Kustutame parooli turvalisuse mõttes mälust
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if not st.session_state["password_correct"]:
        st.markdown("<br><br><h1 style='text-align: center;'>🔒 Turvaline ligipääs</h1>", unsafe_allow_html=True)
        # Teeme kasti keskele
        c1, c2, c3 = st.columns([1, 2, 1])
        with c2:
            st.text_input("Palun sisesta laosüsteemi parool:", type="password", on_change=password_entered, key="password")
            if st.session_state.get("password_correct") == False:
                st.error("😕 Vale parool! Proovi uuesti.")
        return False
    return True

# Kui parool on vale või sisestamata, siis siit edasi süsteemi ei laeta!
if not check_password():
    st.stop()
# ==========================================
# 1. ANDMEBAASI SEADISTUS (SQLite)
# ==========================================
# ==========================================
# 1. ANDMEBAASI SEADISTUS (Supabase / PostgreSQL)
# ==========================================
# Loeme andmebaasi URL-i turvaliselt Streamliti saladustest
SQLALCHEMY_DATABASE_URL = st.secrets["SUPABASE_URL"]

# PostgreSQL ei vaja 'check_same_thread' argumenti
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_estonian_time():
    """Tagastab alati õige Eesti aja, eemaldades ajavööndi info SQLite jaoks."""
    return datetime.now(ZoneInfo("Europe/Tallinn")).replace(tzinfo=None)

def get_db():
    db = SessionLocal()
    try:
        return db
    except Exception:
        db.close()

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    transactions = relationship("Transaction", back_populates="supplier")
    purchase_orders = relationship("PurchaseOrder", back_populates="supplier")

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True, nullable=True) 
    name = Column(String, index=True, nullable=False)
    product_group = Column(String, nullable=True) 
    default_price = Column(Float, nullable=True, default=0.0) 
    warehouse_unit = Column(String, default="tk") 
    purchase_unit = Column(String, default="tk") 
    conversion_multiplier = Column(Float, default=1.0) 
    transactions = relationship("Transaction", back_populates="product")
    purchase_orders = relationship("PurchaseOrder", back_populates="product")

class TransactionType(str, enum.Enum):
    IN_STOCK = "IN"
    OUT_STOCK = "OUT"
    RETURN = "RETURN" 
    TO_PROD = "TO_PROD" 
    PROD_CONS = "PROD_CONS" 

class OrderStatus(str, enum.Enum):
    PENDING = "Ootel"
    RECEIVED = "Saabunud"
    CANCELLED = "Tühistatud"

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    supplier_code = Column(String, nullable=True) 
    supplier_product_name = Column(String, nullable=True) 
    type = Column(Enum(TransactionType))
    quantity = Column(Float)
    price = Column(Float)
    transaction_date = Column(DateTime, default=get_estonian_time)
    notes = Column(String, nullable=True)

    product = relationship("Product", back_populates="transactions")
    supplier = relationship("Supplier", back_populates="transactions")

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    supplier_code = Column(String, nullable=True) 
    supplier_product_name = Column(String, nullable=True) 
    order_date = Column(Date, default=lambda: get_estonian_time().date())
    expected_delivery_date = Column(Date, nullable=True)
    arrival_date = Column(Date, nullable=True)
    quantity = Column(Float)
    price = Column(Float)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING)

    product = relationship("Product", back_populates="purchase_orders")
    supplier = relationship("Supplier", back_populates="purchase_orders")

Base.metadata.create_all(bind=engine)

# ==========================================
# AUTOMAATNE MIGRATSIOON (PostgreSQL tugi)
# ==========================================
with engine.begin() as conn:
    def get_columns(table_name):
        # PostgreSQL päring veergude leidmiseks
        result = conn.execute(text(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table_name}'"))
        return [row[0] for row in result]

    # Transactions
    columns_t = get_columns("transactions")
    if columns_t:
        if "supplier_id" not in columns_t: conn.execute(text("ALTER TABLE transactions ADD COLUMN supplier_id INTEGER REFERENCES suppliers(id)"))
        if "supplier_code" not in columns_t: conn.execute(text("ALTER TABLE transactions ADD COLUMN supplier_code VARCHAR"))
        if "supplier_product_name" not in columns_t: conn.execute(text("ALTER TABLE transactions ADD COLUMN supplier_product_name VARCHAR"))
        
    # Products
    columns_p = get_columns("products")
    if columns_p:
        if "conversion_multiplier" not in columns_p: conn.execute(text("ALTER TABLE products ADD COLUMN conversion_multiplier FLOAT DEFAULT 1.0"))
        
    # Purchase Orders
    columns_po = get_columns("purchase_orders")
    if columns_po:
        if "supplier_code" not in columns_po: conn.execute(text("ALTER TABLE purchase_orders ADD COLUMN supplier_code VARCHAR"))
        if "supplier_product_name" not in columns_po: conn.execute(text("ALTER TABLE purchase_orders ADD COLUMN supplier_product_name VARCHAR"))
# ==========================================
# 2. ABIFUNKTSIOONID JA MATEMAATIKA
# ==========================================
def convert_df_to_excel(df):
    """Genereerib pandas DataFrame'ist Excel faili mälus."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Andmed')
    return output.getvalue()

def render_excel_download(df, prefix="andmed"):
    """Vormistab standardse Exceli allalaadimise nupu."""
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    st.download_button(
        label="📥 Laadi alla Excel (xlsx)",
        data=convert_df_to_excel(df),
        file_name=f"{prefix}_{get_estonian_time().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

def is_discrete_unit(unit_str):
    """Kontrollib, kas ühik on selline, mis vajab alati täisarvuks ümardamist."""
    if not unit_str: return False
    return unit_str.strip().lower() in ['tk', 'tükk', 'komplekt', 'paar']

def format_color_status(val, red_vals, green_vals, yellow_vals=[], blue_vals=[], purple_vals=[]):
    """Dünaamiline CSS värvija Dataframe rakkudele."""
    if val in green_vals: return 'color: #10B981; font-weight: 700;'
    if val in red_vals: return 'color: #EF4444; font-weight: 700;'
    if val in yellow_vals: return 'color: #F59E0B; font-weight: 700;'
    if val in blue_vals: return 'color: #3B82F6; font-weight: 700;'
    if val in purple_vals: return 'color: #8B5CF6; font-weight: 700;'
    return ''

def calculate_global_inventory(db):
    """Arvutab kogu laoseisu optimeeritud päringutega (N+1 lahendatud joinedload abil)."""
    all_products = db.query(Product).options(
        joinedload(Product.transactions).joinedload(Transaction.supplier),
        joinedload(Product.purchase_orders).joinedload(PurchaseOrder.supplier)
    ).all()
    
    all_suppliers = db.query(Supplier).order_by(Supplier.name).all()
    supplier_names = [s.name for s in all_suppliers]
    product_options = {f"{p.name} ({p.code if p.code else 'Kood puudub'})": p for p in all_products}
    
    total_items_main = 0
    total_items_prod = 0
    total_value = 0.0
    inventory_data = []

    for p in all_products:
        in_qty, out_qty, ret_qty, to_prod_qty, prod_cons_qty = 0, 0, 0, 0, 0
        total_in_cost = 0.0
        
        for t in p.transactions:
            if t.type == TransactionType.IN_STOCK:
                in_qty += t.quantity
                total_in_cost += t.quantity * (t.price or 0.0)
            elif t.type == TransactionType.OUT_STOCK: out_qty += t.quantity
            elif t.type == TransactionType.RETURN: ret_qty += t.quantity
            elif t.type == TransactionType.TO_PROD: to_prod_qty += t.quantity
            elif t.type == TransactionType.PROD_CONS: prod_cons_qty += t.quantity
            
        main_stock = round((in_qty + ret_qty) - out_qty - to_prod_qty, 4)
        prod_stock = round(to_prod_qty - prod_cons_qty, 4)
        
        if is_discrete_unit(p.warehouse_unit):
            main_stock = round(main_stock)
            prod_stock = round(prod_stock)
            
        avg_price = total_in_cost / in_qty if in_qty > 0 else (p.default_price or 0.0)
        
        total_items_main += main_stock
        total_items_prod += prod_stock
        total_value += (main_stock + prod_stock) * avg_price
        
        if main_stock != 0 or prod_stock != 0:
            inventory_data.append({
                "Tootekood": p.code or "-",
                "Nimetus": p.name,
                "Tooterühm": p.product_group or "-",
                "Põhiladu": main_stock,
                "Tootmises": prod_stock,
                "Laoühik": p.warehouse_unit,
                "Keskmine hind (€)": avg_price,
                "Koguväärtus (€)": (main_stock + prod_stock) * avg_price
            })
            
    return all_products, product_options, supplier_names, inventory_data, total_items_main, total_items_prod, total_value

# ==========================================
# 3. KASUTAJALIIDESE SEADISTUS JA MENÜÜ
# ==========================================

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stApp { background-color: #F8FAFC; }
    .main .block-container { padding-top: 1rem !important; }
    [data-testid="stSidebarUserContent"] { padding-top: 2.8rem !important; }
    h1 { color: #0F172A; font-weight: 800; letter-spacing: -1px; margin-top: 0 !important; padding-top: 0 !important; }
    h2, h3 { color: #1E293B; font-weight: 600; letter-spacing: -0.5px; }
    
    [data-testid="stMetric"] { background-color: #FFFFFF; border-radius: 16px; padding: 1.5rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); border: 1px solid #E2E8F0; transition: transform 0.2s ease; }
    [data-testid="stMetric"]:hover { transform: translateY(-4px); box-shadow: 0 12px 20px -3px rgba(0,0,0,0.08); }
    [data-testid="stMetricValue"] { font-size: 2.4rem; color: #2563EB; font-weight: 800; }
    [data-testid="stMetricLabel"] { font-size: 1rem; color: #64748B; font-weight: 600; }

    .stButton>button { border-radius: 12px; font-weight: 600; background: #2563EB; color: white; border: none; padding: 0.6rem 1.5rem; transition: all 0.2s ease; }
    .stButton>button:hover { background: #1D4ED8; transform: translateY(-2px); box-shadow: 0 6px 12px -2px rgba(37, 99, 235, 0.3); color: white; }
    
    [data-testid="stSidebar"] { background-color: #FFFFFF; border-right: 1px solid #E2E8F0; }
    [data-testid="stSidebar"] .stRadio > div > label > div:first-child { display: none; }
    [data-testid="stSidebar"] .stRadio > div > label { background-color: transparent; padding: 0.8rem 1rem; border-radius: 12px; margin-bottom: 0.2rem; cursor: pointer; }
    [data-testid="stSidebar"] .stRadio > div > label p { font-size: 1.05rem; font-weight: 600; color: #475569; margin: 0; }
    [data-testid="stSidebar"] .stRadio > div > label:hover { background-color: #F8FAFC; transform: translateX(6px); }
    [data-testid="stSidebar"] .stRadio > div > label:has(input:checked) { background-color: #EFF6FF; border-left: 4px solid #2563EB; }
    [data-testid="stSidebar"] .stRadio > div > label:has(input:checked) p { color: #1D4ED8; }
    
    .stTextInput input, .stNumberInput input { border-radius: 10px !important; border: 1px solid #CBD5E1 !important; padding: 0.5rem 1rem !important; }
    .stTextInput input:focus, .stNumberInput input:focus { border-color: #3B82F6 !important; box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2) !important; }
    [data-testid="stDataFrame"] { background-color: #FFFFFF; border-radius: 16px; padding: 1rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); border: 1px solid #E2E8F0; }
</style>
""", unsafe_allow_html=True)

st.sidebar.markdown("""
    <div style="text-align: center; padding-top: 0rem; padding-bottom: 2rem;">
        <h1 style="color: #1E293B; font-size: 2.4rem; font-weight: 800; letter-spacing: -1.5px; margin-bottom: 0;">📦 Ladu</h1>
        <p style="color: #64748B; font-size: 0.85rem; margin-top: 5px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.5px;">Nutikas Haldussüsteem</p>
    </div>
""", unsafe_allow_html=True)

menyuu_valik = st.sidebar.radio("", [
    "📊 Ladu ja Töölaud", "📋 Tootekataloog", "📥 Sissetulek", "📤 Väljastus / Tootmine", 
    "🛒 Ostutellimused", "📝 Inventuur / Tagastus", "✨ Lisa / Muuda toodet", "🕒 Kannete ajalugu"
], label_visibility="collapsed")
st.sidebar.markdown("<br><br>", unsafe_allow_html=True)
st.sidebar.caption("Versioon 9.4 (Täislahendus & Massimport)")

db = get_db()
all_products, product_options, supplier_names, inventory_data, total_items_main, total_items_prod, total_value = calculate_global_inventory(db)

# ==========================================
# 4. LEHEKÜLGEDE FUNKTSIOONID
# ==========================================
def render_dashboard():
    h_col1, h_col2 = st.columns([3, 1])
    with h_col1: st.title("📊 Ladu ja Töölaud")
    
    df = pd.DataFrame(inventory_data) if inventory_data else pd.DataFrame()
    if not df.empty:
        with h_col2: render_excel_download(df, "laoseis")
            
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Erinevaid tooteid", len(all_products))
    m2.metric("Esemeid PÕHILAOS", f"{total_items_main:g}")
    m3.metric("Esemeid TOOTMISES", f"{total_items_prod:g}")
    m4.metric("Lao koguväärtus", f"{total_value:,.2f} €".replace(",", " "))

    st.markdown("<br><br>", unsafe_allow_html=True)
    st.subheader("Hetke laoseis ja asukohad")

    if not df.empty:
        def hi_main(val): return 'color: #10B981; font-weight: 700;' if val > 0 else ('color: #EF4444; font-weight: 700;' if val < 0 else 'color: #94A3B8;')
        def hi_prod(val): return 'color: #F59E0B; font-weight: 700;' if val > 0 else 'color: #94A3B8;'
            
        styled_df = df.style.map(hi_main, subset=['Põhiladu']).map(hi_prod, subset=['Tootmises']).format({
            "Põhiladu": "{:g}", "Tootmises": "{:g}", "Keskmine hind (€)": "{:.2f}", "Koguväärtus (€)": "{:.2f}"
        })
        st.dataframe(styled_df, use_container_width=True, hide_index=True, height=550)
    else:
        st.info("ℹ️ Ladu on hetkel tühi. Lisa vasakult menüüst uusi tooteid ja tee sissekandeid.")

def render_catalog():
    h_col1, h_col2 = st.columns([3, 1])
    with h_col1:
        st.title("📋 Tootekataloog")
        st.markdown("Siin on nimekiri kõikidest andmebaasi registreeritud toodetest koos seotud tarnijatega.")
        
    st.markdown("<br>", unsafe_allow_html=True)
    if not all_products:
        st.info("ℹ️ Kataloog on hetkel tühi.")
        return

    catalog_data = []
    for p in all_products:
        unique_suppliers = set()
        for t in p.transactions:
            if t.type == TransactionType.IN_STOCK and t.supplier: unique_suppliers.add((t.supplier.name, t.supplier_code, t.supplier_product_name))
        for o in p.purchase_orders:
            if o.supplier: unique_suppliers.add((o.supplier.name, o.supplier_code, o.supplier_product_name))
        
        suhe_txt = f"1 {p.purchase_unit} = {p.conversion_multiplier or 1.0:g} {p.warehouse_unit}"
        base_dict = {"Tootekood": p.code or "-", "Nimetus": p.name, "Tooterühm": p.product_group or "-", "Ühikute suhe (Ost vs Ladu)": suhe_txt}
        
        if not unique_suppliers:
            catalog_data.append({**base_dict, "Tarnija": "-", "Tarnija kood": "-", "Tarnija toote nimetus": "-"})
        else:
            for s_name, s_code, s_prod in unique_suppliers:
                catalog_data.append({**base_dict, "Tarnija": s_name, "Tarnija kood": s_code or "-", "Tarnija toote nimetus": s_prod or "-"})
        
    df_cat = pd.DataFrame(catalog_data)
    with h_col2: render_excel_download(df_cat, "tootekataloog")
    st.dataframe(df_cat, use_container_width=True, hide_index=True, height=600)

def render_transactions(is_in_transaction):
    st.title("📥 Sissetulek" if is_in_transaction else "📤 Väljastus ja Tootmisse kandmine")
    if is_in_transaction: st.markdown("Registreeri lattu sissetulev kaup. Täida info **ostuühikutes**.")
    else: st.markdown("Määra, kas kannad kauba **Tootmisse** või teed **Tavalise väljamineku** (nt müük).")
        
    st.markdown("<br>", unsafe_allow_html=True)
    if 'trans_success' in st.session_state:
        st.success(st.session_state.pop('trans_success'))
    if 'trans_error' in st.session_state:
        st.error(st.session_state.pop('trans_error'))
        
    action_type = "Sissetulek"
    if not is_in_transaction:
        action_type = st.radio("Vali tegevus:", ["Kanna TOOTMISSE", "Tavaline VÄLJAMINEK (Müük vms)"], horizontal=True)
        st.markdown("---")
    
    col1, col2 = st.columns([2, 1]) 
    with col1:
        with st.container():
            selected_product_str = st.selectbox("Otsi toodet oma kataloogist", options=["Vali toode..."] + list(product_options.keys()))
            
            if selected_product_str == "Vali toode...": return # Ootame valikut
            
            prod = product_options[selected_product_str]
            active_unit = prod.purchase_unit if is_in_transaction else prod.warehouse_unit
            
            # Kogu varasemad tarnijad (Optimeeritud otsing mälust)
            known_sups = sorted(list(set([t.supplier.name for t in prod.transactions if t.type == TransactionType.IN_STOCK and t.supplier] + [o.supplier.name for o in prod.purchase_orders if o.supplier])))
            last_sup_name = next((t.supplier.name for t in sorted(prod.transactions, key=lambda x: x.transaction_date, reverse=True) if t.type == TransactionType.IN_STOCK and t.supplier), None)

            st.markdown("<br>", unsafe_allow_html=True)
            f_col1, f_col2 = st.columns(2)
            with f_col1: qty = st.number_input(f"Kogus ({active_unit})", min_value=0.001, step=1.0, value=1.0, format="%f")
            
            price = 0.0
            if is_in_transaction:
                with f_col2: price = st.number_input(f"Hind/{prod.purchase_unit} (€)", min_value=0.0, step=0.01, value=float(prod.default_price or 0.0))
            
            actual_supplier_name, db_sup_choice, sup_code, sup_prod_name = "- Puudub -", "", "", ""
            new_supplier_name = ""
            
            if is_in_transaction:
                st.markdown("---")
                st.caption("Tarnija info")
                def_idx = known_sups.index(last_sup_name) + 1 if last_sup_name in known_sups else 0
                supplier_choice = st.selectbox("Vali Tarnija (Tootekataloogist)", options=["- Puudub -"] + known_sups + ["🌍 Otsi andmebaasist / Lisa uus..."], index=def_idx)
                actual_supplier_name = supplier_choice
                
                if supplier_choice == "🌍 Otsi andmebaasist / Lisa uus...":
                    other_sups = sorted(list(set(supplier_names) - set(known_sups)))
                    db_sup_choice = st.selectbox("Otsi olemasolevat tarnijat süsteemist", options=["➕ Sisesta uus tarnija..."] + other_sups)
                    if db_sup_choice == "➕ Sisesta uus tarnija...":
                        new_supplier_name = st.text_input("Uue tarnija nimi", placeholder="Sisesta uus tarnija nimi siia")
                        actual_supplier_name = "- Puudub -"
                    else: actual_supplier_name = db_sup_choice

                c_options, n_options = [""], [""]
                if actual_supplier_name not in ["- Puudub -", "🌍 Otsi andmebaasist / Lisa uus..."]:
                    c_options = sorted(list(set([t.supplier_code for t in prod.transactions if t.supplier and t.supplier.name == actual_supplier_name and t.supplier_code] + [o.supplier_code for o in prod.purchase_orders if o.supplier and o.supplier.name == actual_supplier_name and o.supplier_code])))
                    n_options = sorted(list(set([t.supplier_product_name for t in prod.transactions if t.supplier and t.supplier.name == actual_supplier_name and t.supplier_product_name] + [o.supplier_product_name for o in prod.purchase_orders if o.supplier and o.supplier.name == actual_supplier_name and o.supplier_product_name])))
                
                sc_col1, sc_col2 = st.columns(2)
                with sc_col1:
                    sup_code = st.selectbox("Tarnija kood", options=c_options + ["➕ Sisesta uus..."]) if len(c_options) > 0 and c_options[0] else st.text_input("Tarnija kood")
                    if sup_code == "➕ Sisesta uus...": sup_code = st.text_input("Uus tarnija kood", placeholder="Sisesta kood siia")
                with sc_col2:
                    sup_prod_name = st.selectbox("Tarnija toote nimetus", options=n_options + ["➕ Sisesta uus..."]) if len(n_options) > 0 and n_options[0] else st.text_input("Tarnija toote nimetus")
                    if sup_prod_name == "➕ Sisesta uus...": sup_prod_name = st.text_input("Uus toote nimetus", placeholder="Sisesta nimetus siia")
                
            st.markdown("---")
            notes = st.text_input("Kommentaar (valikuline)", placeholder="nt. Arve nr / Saateleht...")
            
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("💾 Salvesta kanne", use_container_width=True):
                handle_transaction_save(db, prod, qty, price, notes, is_in_transaction, action_type, actual_supplier_name, db_sup_choice, new_supplier_name, sup_code, sup_prod_name, active_unit)

def handle_transaction_save(db, prod, qty, price, notes, is_in, action_type, act_sup_name, db_sup, new_sup, sup_code, sup_prod, act_unit):
    proceed, final_sup_id = True, None
    if is_in:
        if act_sup_name == "- Puudub -" and db_sup == "➕ Sisesta uus tarnija...":
            if not new_sup.strip(): st.error("⚠️ Sisesta uue tarnija nimi!"); proceed = False
            else:
                ex_sup = db.query(Supplier).filter(Supplier.name == new_sup.strip()).first()
                if ex_sup: final_sup_id = ex_sup.id
                else:
                    n_sup = Supplier(name=new_sup.strip()); db.add(n_sup); db.flush(); final_sup_id = n_sup.id
        elif act_sup_name != "- Puudub -" and act_sup_name != "🌍 Otsi andmebaasist / Lisa uus...":
            ex_sup = db.query(Supplier).filter(Supplier.name == act_sup_name).first()
            if ex_sup: final_sup_id = ex_sup.id
    else:
        # Väljastuse kontroll
        stock_data = next((d for d in inventory_data if d["Tootekood"] == prod.code and d["Nimetus"] == prod.name), None)
        curr_stock = stock_data["Põhiladu"] if stock_data else 0.0
        if qty > curr_stock:
            st.session_state['trans_error'] = f"⚠️ Viga: PÕHILAOS on saadaval ainult: {curr_stock:g} {prod.warehouse_unit}"
            proceed = False
            
    if proceed:
        db_type = TransactionType.IN_STOCK if is_in else (TransactionType.TO_PROD if action_type == "Kanna TOOTMISSE" else TransactionType.OUT_STOCK)
        mult = prod.conversion_multiplier or 1.0
        
        final_qty = qty * mult if is_in else qty
        if is_discrete_unit(prod.warehouse_unit): final_qty = round(final_qty)
        final_price = (price / mult if mult else price) if is_in else 0.0
        final_notes = (f"[Ost: {qty:g} {act_unit}] {notes}".strip() if mult != 1.0 else notes) if is_in else notes

        db.add(Transaction(
            product_id=prod.id, supplier_id=final_sup_id, supplier_code=sup_code.strip() or None,
            supplier_product_name=sup_prod.strip() or None, type=db_type, quantity=final_qty, price=final_price, notes=final_notes
        ))
        db.commit()
        
        succ_txt = f"✅ Sissetulek salvestatud: {prod.name}" if is_in else f"✅ {'Kanti tootmisse' if db_type == TransactionType.TO_PROD else 'Väljastati laost'}: {prod.name}"
        if is_in and mult != 1.0: succ_txt += f" ({qty:g} {act_unit} => arvel {final_qty:g} {prod.warehouse_unit})"
        else: succ_txt += f" ({qty:g} {prod.warehouse_unit})"
        st.session_state['trans_success'] = succ_txt
        st.rerun()

def create_order_callback():
    """Loome uue tellimuse ja puhastame mälu (session_state) ilma vahekaarte sulgemata."""
    sel_prod_str = st.session_state.get("new_ord_prod")

    if not sel_prod_str or sel_prod_str == "Vali...":
        st.session_state['order_error'] = "⚠️ Vali toode!"
        return

    o_qty = st.session_state.get("new_ord_qty", 1.0)
    o_price = st.session_state.get("new_ord_price", 0.0)
    o_sup_ch = st.session_state.get("new_ord_sup_ch", "- Puudub -")
    db_sup = st.session_state.get("new_ord_db_sup", "")
    n_sup = st.session_state.get("new_ord_n_sup", "")

    o_code = st.session_state.get("new_ord_code_txt", "")
    if st.session_state.get("new_ord_code_sel"):
        if st.session_state.get("new_ord_code_sel") == "➕ Uus...":
            o_code = st.session_state.get("new_ord_code_new", "")
        else:
            o_code = st.session_state.get("new_ord_code_sel", "")

    o_name = st.session_state.get("new_ord_name_txt", "")
    if st.session_state.get("new_ord_name_sel"):
        if st.session_state.get("new_ord_name_sel") == "➕ Uus...":
            o_name = st.session_state.get("new_ord_name_new", "")
        else:
            o_name = st.session_state.get("new_ord_name_sel", "")

    o_date = st.session_state.get("new_ord_date", get_estonian_time().date())
    o_exp = st.session_state.get("new_ord_exp", get_estonian_time().date() + timedelta(days=7))

    db_session = SessionLocal()
    try:
        all_p = db_session.query(Product).all()
        prod = next((p for p in all_p if f"{p.name} ({p.code if p.code else 'Kood puudub'})" == sel_prod_str), None)

        if not prod:
            st.session_state['order_error'] = "⚠️ Viga toote leidmisel!"
            return

        final_sid = None
        if o_sup_ch == "🌍 Otsi andmebaasist..." and db_sup == "➕ Uus tarnija...":
            if not n_sup.strip():
                st.session_state['order_error'] = "⚠️ Sisesta uue tarnija nimi!"
                return
            ns = Supplier(name=n_sup.strip())
            db_session.add(ns); db_session.flush(); final_sid = ns.id
        elif o_sup_ch != "- Puudub -" and o_sup_ch != "🌍 Otsi andmebaasist...":
            s = db_session.query(Supplier).filter(Supplier.name == o_sup_ch).first()
            if s: final_sid = s.id
        elif o_sup_ch == "🌍 Otsi andmebaasist..." and db_sup != "➕ Uus tarnija...":
            s = db_session.query(Supplier).filter(Supplier.name == db_sup).first()
            if s: final_sid = s.id

        db_session.add(PurchaseOrder(
            product_id=prod.id, supplier_id=final_sid,
            supplier_code=o_code if str(o_code).strip() else None,
            supplier_product_name=o_name if str(o_name).strip() else None,
            order_date=o_date, expected_delivery_date=o_exp,
            quantity=o_qty, price=o_price
        ))
        db_session.commit()
        st.session_state['order_success'] = "✅ Tellimus edukalt salvestatud!"
        
        keys_to_clear = [
            "new_ord_prod", "new_ord_qty", "new_ord_price", "new_ord_sup_ch", 
            "new_ord_db_sup", "new_ord_n_sup", "new_ord_code_sel", "new_ord_code_txt", 
            "new_ord_code_new", "new_ord_name_sel", "new_ord_name_txt", "new_ord_name_new",
            "new_ord_date", "new_ord_exp"
        ]
        for k in keys_to_clear:
            if k in st.session_state:
                del st.session_state[k]

    except Exception as e:
        db_session.rollback()
        st.session_state['order_error'] = f"⚠️ Viga: {e}"
    finally:
        db_session.close()

def render_orders(db):
    st.title("🛒 Ostutellimused")
    st.markdown("Halda kauba tellimusi. **Märgi tellimus saabunuks**, et süsteem võtaks kauba automaatselt lattu arvele!")

    if 'order_success' in st.session_state: st.success(st.session_state.pop('order_success'))
    if 'order_error' in st.session_state: st.error(st.session_state.pop('order_error'))
        
    tab_act, tab_new, tab_hist = st.tabs(["⏳ Aktiivsed tellimused", "➕ Uus tellimus", "📜 Tellimuste ajalugu"])
    
    with tab_new:
        col1, _ = st.columns([2, 1])
        with col1:
            sel_prod = st.selectbox("Vali toode", options=["Vali..."] + list(product_options.keys()), key="new_ord_prod")
            if sel_prod != "Vali...":
                prod = product_options[sel_prod]
                known_sups = sorted(list(set([t.supplier.name for t in prod.transactions if t.type == TransactionType.IN_STOCK and t.supplier] + [o.supplier.name for o in prod.purchase_orders if o.supplier])))
                
                f1, f2 = st.columns(2)
                with f1: st.number_input(f"Kogus ({prod.purchase_unit})", min_value=0.001, value=1.0, key="new_ord_qty")
                with f2: st.number_input(f"Hind/{prod.purchase_unit} (€)", min_value=0.0, value=float(prod.default_price or 0.0), key="new_ord_price")
                
                o_sup_ch = st.selectbox("Tarnija", options=["- Puudub -"] + known_sups + ["🌍 Otsi andmebaasist..."], key="new_ord_sup_ch")
                act_sup = o_sup_ch
                if o_sup_ch == "🌍 Otsi andmebaasist...":
                    db_sup = st.selectbox("Otsi olemasolevat", options=["➕ Uus tarnija..."] + sorted(list(set(supplier_names)-set(known_sups))), key="new_ord_db_sup")
                    if db_sup == "➕ Uus tarnija...":
                        st.text_input("Uue tarnija nimi", key="new_ord_n_sup")
                        act_sup = "- Puudub -"
                    else: act_sup = db_sup
                        
                c_opt, n_opt = [""], [""]
                if act_sup not in ["- Puudub -", "🌍 Otsi andmebaasist..."]:
                    c_opt = sorted(list(set([t.supplier_code for t in prod.transactions if t.supplier and t.supplier.name == act_sup and t.supplier_code] + [o.supplier_code for o in prod.purchase_orders if o.supplier and o.supplier.name == act_sup and o.supplier_code])))
                    n_opt = sorted(list(set([t.supplier_product_name for t in prod.transactions if t.supplier and t.supplier.name == act_sup and t.supplier_product_name] + [o.supplier_product_name for o in prod.purchase_orders if o.supplier and o.supplier.name == act_sup and o.supplier_product_name])))
                
                sc1, sc2 = st.columns(2)
                with sc1:
                    o_code = st.selectbox("Tarnija kood", options=c_opt+["➕ Uus..."], key="new_ord_code_sel") if len(c_opt)>0 and c_opt[0] else st.text_input("Tarnija kood", key="new_ord_code_txt")
                    if o_code == "➕ Uus...": st.text_input("Uus kood", key="new_ord_code_new")
                with sc2:
                    o_name = st.selectbox("Tarnija toote nimetus", options=n_opt+["➕ Uus..."], key="new_ord_name_sel") if len(n_opt)>0 and n_opt[0] else st.text_input("Tarnija toote nimetus", key="new_ord_name_txt")
                    if o_name == "➕ Uus...": st.text_input("Uus nimetus", key="new_ord_name_new")
                
                d1, d2 = st.columns(2)
                with d1: st.date_input("Tellimuse kuupäev", value=get_estonian_time().date(), key="new_ord_date")
                with d2: st.date_input("Lubatud tarneaeg", value=get_estonian_time().date() + timedelta(days=7), key="new_ord_exp")
                
                st.button("💾 Salvesta tellimus süsteemi", use_container_width=True, on_click=create_order_callback)

    with tab_act:
        pend = db.query(PurchaseOrder).filter(PurchaseOrder.status == OrderStatus.PENDING).all()
        if pend:
            df_act = pd.DataFrame([{"ID": o.id, "Tellitud": o.order_date.strftime("%d.%m.%Y"), "Toode": o.product.name, "Kogus": f"{o.quantity:g} {o.product.purchase_unit}", "Tarnija": o.supplier.name if o.supplier else "-", "Lubatud": o.expected_delivery_date.strftime("%d.%m.%Y") if o.expected_delivery_date else "-", "Hind": o.price} for o in pend])
            def hl_late(r): return ['background-color: #fee2e2; color: #991b1b; font-weight: 600;'] * len(r) if (datetime.strptime(r['Lubatud'], "%d.%m.%Y").date() < get_estonian_time().date()) else [''] * len(r)
            st.dataframe(df_act.style.apply(hl_late, axis=1).format({"Hind": "{:.2f}"}), use_container_width=True, hide_index=True)
            
            st.subheader("Halda aktiivset tellimust")
            if 'ord_k' not in st.session_state: st.session_state['ord_k'] = 0
            opts = {f"#{o.id} - {o.product.name} ({o.quantity:g} {o.product.purchase_unit})": o for o in pend}
            sel = st.selectbox("Vali tellimus:", ["Vali..."] + list(opts.keys()), key=f"sel_{st.session_state['ord_k']}")
            
            if sel != "Vali...":
                o = opts[sel]
                c1, c2, c3, c4 = st.columns(4)
                if c1.button("📦 Võta lattu arvele", type="primary", use_container_width=True):
                    o.status = OrderStatus.RECEIVED; o.arrival_date = get_estonian_time().date()
                    mult = o.product.conversion_multiplier or 1.0
                    f_qty = round(o.quantity * mult) if is_discrete_unit(o.product.warehouse_unit) else (o.quantity * mult)
                    db.add(Transaction(product_id=o.product.id, supplier_id=o.supplier_id, type=TransactionType.IN_STOCK, quantity=f_qty, price=(o.price/mult if mult else o.price), notes=f"Tellimus #{o.id} [Ost: {o.quantity:g}]"))
                    db.commit(); st.session_state['order_success'] = "Lattu võetud!"; st.session_state['ord_k']+=1; st.rerun()
                if c2.button("🚚 Märgi saabunuks", use_container_width=True):
                    o.status = OrderStatus.RECEIVED; o.arrival_date = get_estonian_time().date()
                    db.commit(); st.session_state['order_success'] = "Märgiti saabunuks!"; st.session_state['ord_k']+=1; st.rerun()
                if c3.button("❌ Tühista", use_container_width=True):
                    o.status = OrderStatus.CANCELLED; db.commit(); st.session_state['ord_k']+=1; st.rerun()
                if c4.button("🔙 Sulge", use_container_width=True): st.session_state['ord_k']+=1; st.rerun()
        else: st.info("Ootel tellimusi hetkel pole.")
        
    with tab_hist:
        hist = db.query(PurchaseOrder).filter(PurchaseOrder.status != OrderStatus.PENDING).order_by(PurchaseOrder.id.desc()).all()
        if hist:
            df_h = pd.DataFrame([{"ID": o.id, "Toode": o.product.name, "Kogus": f"{o.quantity:g} {o.product.purchase_unit}", "Tarnija": o.supplier.name if o.supplier else "-", "Hind": o.price, "Seis": o.status.value, "Saabus": o.arrival_date.strftime("%d.%m.%Y") if o.arrival_date else "-"} for o in hist])
            st.dataframe(df_h.style.map(lambda v: format_color_status(v, ['Tühistatud'], ['Saabunud']), subset=['Seis']).format({"Hind": "{:.2f}"}), use_container_width=True, hide_index=True)
        else: st.info("Ajalugu on tühi.")

def render_inventory(db):
    st.title("📝 Tootmise inventuur (Kulu kandmine)")
    st.markdown("Lae üles fail, kus on kirjas **tootmisesse alles jäänud** kogused. Süsteem arvutab välja vahe ja kannab puuduoleva osa automaatselt kulusse. Põhilattu midagi tagasi ei kanta.")
    if 'inv_success' in st.session_state: st.success(st.session_state.pop('inv_success'))
        
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("1. Laadi mall alla")
        template_df = pd.DataFrame([{"Tootekood": "KOOD123", "Nimetus": "Kruvi", "Tootmise jääk": 0}])
        st.download_button("📥 Mall (xlsx)", convert_df_to_excel(template_df), "inventuuri_mall.xlsx", use_container_width=True)
    with c2:
        st.subheader("2. Laadi täidetud fail üles")
        up = st.file_uploader("Vali fail (.xlsx)", type=["xlsx"])
        
    if up:
        try:
            df = pd.read_excel(up, engine='openpyxl')
            valid = []
            
            qty_col = None
            if "Tootmise jääk" in df.columns: qty_col = "Tootmise jääk"
            elif "Tagastatav kogus" in df.columns: qty_col = "Tagastatav kogus"
            
            if not qty_col:
                st.error("⚠️ Failis puudub veerg 'Tootmise jääk'!")
                return
                
            has_code = "Tootekood" in df.columns
            has_name = "Nimetus" in df.columns
            
            if not has_code and not has_name:
                st.error("⚠️ Failis peab olema veerg 'Tootekood' või 'Nimetus'!")
                return

            for _, r in df.iterrows():
                try:
                    q_val = r.get(qty_col)
                    if pd.isna(q_val): continue
                    left_in_prod = float(q_val)
                    if left_in_prod < 0: continue
                    
                    code_str = str(r["Tootekood"]).strip() if has_code and pd.notna(r["Tootekood"]) else ""
                    name_str = str(r["Nimetus"]).strip() if has_name and pd.notna(r["Nimetus"]) else ""
                    
                    if code_str.lower() == 'nan': code_str = ""
                    if name_str.lower() == 'nan': name_str = ""
                    
                    p = None
                    if code_str:
                        p = db.query(Product).filter(Product.code == code_str).first()
                    if not p and name_str:
                        p = db.query(Product).filter(Product.name == name_str).first()
                            
                    if p: 
                        valid.append({"prod": p, "left_qty": left_in_prod})
                        
                except ValueError: pass 
            
            if valid:
                preview_data = []
                transactions_to_make = []
                
                for i in valid:
                    p = i['prod']
                    left_qty = round(i['left_qty']) if is_discrete_unit(p.warehouse_unit) else i['left_qty']
                    
                    # Leiame toote seisu otse TÖÖLAUA andmetest, et tagada 100% sünkroonsus
                    search_code = p.code or "-"
                    stock_info = next((item for item in inventory_data if item["Tootekood"] == search_code), None)
                    if not stock_info:
                        stock_info = next((item for item in inventory_data if item["Nimetus"] == p.name), None)
                    
                    curr_prod_stock = stock_info["Tootmises"] if stock_info else 0.0
                        
                    consumed_qty = curr_prod_stock - left_qty
                    if consumed_qty < 0: consumed_qty = 0
                    
                    if consumed_qty > 0:
                        preview_data.append({
                            "Kood": p.code or "-", 
                            "Toode": p.name, 
                            "Süsteemi järgi tootmises": curr_prod_stock,
                            "Füüsiline jääk (Excelist)": left_qty,
                            "Kandub kulusse": consumed_qty,
                            "Ühik": p.warehouse_unit
                        })
                        transactions_to_make.append({"prod": p, "cons": consumed_qty})

                if preview_data:
                    st.markdown("### 🔍 Inventuuri eelvaade")
                    st.dataframe(pd.DataFrame(preview_data), use_container_width=True, hide_index=True)
                    
                    if st.button("💾 Kinnita tootmise kulud", type="primary", use_container_width=True):
                        for item in transactions_to_make:
                            p = item['prod']
                            c_qty = item['cons']
                            
                            in_qty = sum(t.quantity for t in p.transactions if t.type == TransactionType.IN_STOCK)
                            in_cost = sum(t.quantity * (t.price or 0.0) for t in p.transactions if t.type == TransactionType.IN_STOCK)
                            avg_p = in_cost / in_qty if in_qty > 0 else (p.default_price or 0.0)
                            
                            db.add(Transaction(product_id=p.id, type=TransactionType.PROD_CONS, quantity=c_qty, price=avg_p, notes="Tootmise kulu (Inventuur)"))
                                
                        db.commit()
                        st.session_state['inv_success'] = "Tootmise kulud edukalt maha kantud!"
                        st.rerun()
                else:
                    st.info("Kõikide Excelis märgitud toodete füüsiline jääk vastab juba praegu andmebaasi tootmisjäägile. Uusi kulukandeid pole vaja teha.")
            else: 
                st.info("Süsteem ei leidnud andmebaasist ühtegi sobivat toodet. Kontrolli koodi või nimetust.")
        except Exception as e: 
            st.error(f"Viga faili töötlemisel: {e}")
def render_product_management(db):
    st.title("✨ Toote haldus")
    if 'prod_success' in st.session_state: st.success(st.session_state.pop('prod_success'))
    if 'prod_error' in st.session_state: st.error(st.session_state.pop('prod_error'))
    
    groups = sorted([g[0] for g in db.query(Product.product_group).filter(Product.product_group.isnot(None)).distinct().all() if g[0]])
    
    t1, t2, t3 = st.tabs(["➕ Lisa uus", "✏️ Muuda olemasolevat", "📁 Excelist laadimine"])
    
    def prod_form(p=None, key=""):
        name = st.text_input("Nimetus *", value=p.name if p else "", key=f"{key}n")
        code = st.text_input("Kood", value=p.code if p and p.code else "", key=f"{key}c")
        grp = st.selectbox("Rühm", ["- Puudub -"] + groups + ["➕ Uus rühm..."], index=(groups.index(p.product_group)+1 if p and p.product_group in groups else 0), key=f"{key}g")
        n_grp = st.text_input("Uus rühm") if grp == "➕ Uus rühm..." else ""
        
        c1, c2, c3 = st.columns(3)
        with c1: pr = st.number_input("Baashind (€)", value=float(p.default_price or 0.0) if p else 0.0, step=0.01, key=f"{key}p")
        with c2: wu = st.text_input("Laoühik", value=p.warehouse_unit or "tk" if p else "tk", key=f"{key}w")
        with c3: pu = st.text_input("Ostuühik", value=p.purchase_unit or "tk" if p else "tk", key=f"{key}u")
        
        mismatch = wu.strip().lower() != pu.strip().lower()
        if mismatch:
            st.markdown(f"<div style='color: #ef4444; font-size: 0.9rem; font-weight: 600;'>⚠️ Ühikud on erinevad! Kontrolli kordajat.</div>", unsafe_allow_html=True)
            st.markdown(f'<style>input[aria-label="Mitu \'{wu}\' on 1 \'{pu}\'-s?"] {{ background: #fee2e2!important; border: 2px solid #ef4444!important; color: #991b1b!important; }}</style>', unsafe_allow_html=True)
        mult = st.number_input(f"Mitu '{wu}' on 1 '{pu}'-s?", min_value=0.001, value=float(p.conversion_multiplier or 1.0) if p else 1.0, format="%f", key=f"{key}m")
        
        if st.button("💾 Salvesta", use_container_width=True, key=f"{key}b"):
            if not name: st.error("Nimetus on kohustuslik!"); return
            fin_grp = n_grp.strip() if grp == "➕ Uus rühm..." else (grp if grp != "- Puudub -" else None)
            fin_code = code.strip() or None
            
            q_code = db.query(Product).filter(Product.code == fin_code)
            q_name = db.query(Product).filter(Product.name == name, Product.product_group == fin_grp)
            if p: q_code = q_code.filter(Product.id != p.id); q_name = q_name.filter(Product.id != p.id)
            
            if fin_code and q_code.first(): st.error("See kood on juba olemas!"); return
            if q_name.first(): st.error("See toode on selles rühmas juba olemas!"); return
            
            if p:
                dp = db.query(Product).get(p.id)
                dp.name, dp.code, dp.product_group, dp.default_price, dp.warehouse_unit, dp.purchase_unit, dp.conversion_multiplier = name, fin_code, fin_grp, pr, wu, pu, mult
            else:
                db.add(Product(name=name, code=fin_code, product_group=fin_grp, default_price=pr, warehouse_unit=wu, purchase_unit=pu, conversion_multiplier=mult))
            db.commit(); st.session_state['prod_success'] = "Salvestatud!"; st.rerun()

    with t1:
        with st.columns([2,1])[0]: prod_form(key="add")
        
    with t2:
        with st.columns([2,1])[0]:
            sel = st.selectbox("Otsi toodet", ["Vali..."] + list(product_options.keys()))
            if sel != "Vali...": prod_form(product_options[sel], key="edit")
            
    with t3:
        st.markdown("Lisa mitu toodet korraga, laadides üles täidetud Exceli faili. **Nimetus** on kohustuslik väli.")
        c1, c2 = st.columns(2)
        
        with c1:
            st.subheader("1. Laadi mall alla")
            template_df = pd.DataFrame([{
                "Nimetus": "Näidistoode 1",
                "Kood": "KOOD001",
                "Rühm": "Materjalid",
                "Baashind (€)": 12.50,
                "Laoühik": "tk",
                "Ostuühik": "pk",
                "Kordaja (Mitu laoühikut on ostuühikus)": 10,
                "Tarnija": "Tarnija OÜ",
                "Tarnija kood": "TAR-001",
                "Tarnija toote nimetus": "Originaalnimi 1"
            }, {
                "Nimetus": "Näidistoode 2",
                "Kood": "",
                "Rühm": "",
                "Baashind (€)": 5.00,
                "Laoühik": "m",
                "Ostuühik": "m",
                "Kordaja (Mitu laoühikut on ostuühikus)": 1,
                "Tarnija": "",
                "Tarnija kood": "",
                "Tarnija toote nimetus": ""
            }])
            st.download_button(
                label="📥 Toodete importimise mall (xlsx)",
                data=convert_df_to_excel(template_df),
                file_name="toodete_import_mall.xlsx",
                use_container_width=True
            )
            
        with c2:
            st.subheader("2. Laadi fail üles")
            uploaded_file = st.file_uploader("Vali täidetud mall (.xlsx)", type=["xlsx"], key="prod_excel_up")
            
        if uploaded_file:
            try:
                df_upload = pd.read_excel(uploaded_file, engine='openpyxl')
                
                if "Nimetus" not in df_upload.columns:
                    st.error("⚠️ Fail peab sisaldama 'Nimetus' veergu! Palun kasuta allalaaditavat malli.")
                else:
                    st.markdown("### Eelvaade")
                    st.dataframe(df_upload, use_container_width=True, hide_index=True)
                    
                    if st.button("💾 Salvesta tooted andmebaasi", type="primary", use_container_width=True):
                        added_count = 0
                        error_count = 0
                        
                        # LISASIME UUE TRY-EXCEPT PLOKI SPETSIIFILISELT ANDMEBAASI JAOKS
                        try:
                            for _, row in df_upload.iterrows():
                                # TOOTE ANDMED
                                name = str(row.get("Nimetus", "")).strip()
                                if not name or name.lower() == 'nan':
                                    continue
                                    
                                code = str(row.get("Kood", "")).strip() if pd.notna(row.get("Kood")) else None
                                if code and code.lower() == 'nan': code = None
                                
                                group = str(row.get("Rühm", "")).strip() if pd.notna(row.get("Rühm")) else None
                                if group and group.lower() == 'nan': group = None
                                    
                                price = float(row.get("Baashind (€)", 0.0)) if pd.notna(row.get("Baashind (€)")) else 0.0
                                wh_unit = str(row.get("Laoühik", "tk")).strip() if pd.notna(row.get("Laoühik")) and str(row.get("Laoühik", "")).strip() != 'nan' else "tk"
                                pu_unit = str(row.get("Ostuühik", "tk")).strip() if pd.notna(row.get("Ostuühik")) and str(row.get("Ostuühik", "")).strip() != 'nan' else "tk"
                                mult = float(row.get("Kordaja (Mitu laoühikut on ostuühikus)", 1.0)) if pd.notna(row.get("Kordaja (Mitu laoühikut on ostuühikus)")) else 1.0

                                # TARNIJA ANDMED
                                supplier_name = str(row.get("Tarnija", "")).strip() if pd.notna(row.get("Tarnija")) else ""
                                if supplier_name.lower() == 'nan': supplier_name = ""
                                
                                sup_code = str(row.get("Tarnija kood", "")).strip() if pd.notna(row.get("Tarnija kood")) else None
                                if sup_code and sup_code.lower() == 'nan': sup_code = None
                                
                                sup_prod_name = str(row.get("Tarnija toote nimetus", "")).strip() if pd.notna(row.get("Tarnija toote nimetus")) else None
                                if sup_prod_name and sup_prod_name.lower() == 'nan': sup_prod_name = None

                                # DUPLIKAATIDE KONTROLL
                                ex_code = db.query(Product).filter(Product.code == code).first() if code else None
                                ex_name = db.query(Product).filter(Product.name == name, Product.product_group == group).first()
                                
                                if ex_code or ex_name:
                                    error_count += 1
                                else:
                                    # 1. LISAME TOOTE
                                    new_product = Product(
                                        name=name, code=code, product_group=group, 
                                        default_price=price, warehouse_unit=wh_unit, 
                                        purchase_unit=pu_unit, conversion_multiplier=mult
                                    )
                                    db.add(new_product)
                                    db.flush()
                                    
                                    # 2. LISAME TARNIJA (kui on määratud)
                                    sup = None
                                    if supplier_name:
                                        sup = db.query(Supplier).filter(Supplier.name == supplier_name).first()
                                        if not sup:
                                            sup = Supplier(name=supplier_name)
                                            db.add(sup)
                                            db.flush()
                                            
                                        # 3. LOOME SEOSKANDE (0-kogusega sissetulek)
                                        link_trans = Transaction(
                                            product_id=new_product.id,
                                            supplier_id=sup.id,
                                            supplier_code=sup_code,
                                            supplier_product_name=sup_prod_name,
                                            type=TransactionType.IN_STOCK,
                                            quantity=0.0,
                                            price=price,
                                            notes="Esmane tarnija sidumine (Excelist laadimine)"
                                        )
                                        db.add(link_trans)

                                    added_count += 1
                                    
                            if added_count > 0:
                                db.commit()
                                msg = f"✅ Edukas! Lisati {added_count} uut toodet."
                                if error_count > 0:
                                    msg += f" (Eirati {error_count} toodet, mis olid juba andmebaasis olemas)."
                                st.session_state['prod_success'] = msg
                            else:
                                st.session_state['prod_error'] = "⚠️ Ühtegi uut toodet ei lisatud (kõik olid juba olemas või info puudus)."
                            st.rerun()

                        except Exception as db_error:
                            # KRIITILINE OSA: Kui miski läheb katki, tühistame tegevuse, et vältida andmebaasi lukkujäämist!
                            db.rollback() 
                            st.error(f"⚠️ Viga andmebaasi salvestamisel (transaktsioon tühistati): {db_error}")
                            
            except Exception as e:
                st.error(f"Viga faili lugemisel: {e}")
def render_history(db):
    h_col1, h_col2 = st.columns([3, 1])
    with h_col1:
        st.title("🕒 Kannete logi")
        
    f_val = st.radio("Filtreeri:", ["Kõik kanded", "Sissetulek (IN)", "Tootmisse (TO_PROD)", "Kulu (PROD_CONS)", "Väljaminek (OUT)", "Tagastus (RETURN)"], horizontal=True)
    
    q = db.query(Transaction).options(joinedload(Transaction.product), joinedload(Transaction.supplier)).order_by(Transaction.transaction_date.desc())
    if "IN" in f_val: q = q.filter(Transaction.type == TransactionType.IN_STOCK)
    elif "OUT" in f_val: q = q.filter(Transaction.type == TransactionType.OUT_STOCK)
    elif "TO_PROD" in f_val: q = q.filter(Transaction.type == TransactionType.TO_PROD)
    elif "PROD_CONS" in f_val: q = q.filter(Transaction.type == TransactionType.PROD_CONS)
    elif "RETURN" in f_val: q = q.filter(Transaction.type == TransactionType.RETURN)
    
    data = []
    for t in q.all():
        ttype = {"IN_STOCK": "Sissetulek (IN)", "OUT_STOCK": "Väljaminek (OUT)", "TO_PROD": "Kanti tootmisse (TO_PROD)", "PROD_CONS": "Tootmise kulu (PROD_CONS)", "RETURN": "Tagastus (RETURN)"}[t.type.name]
        
        qty, price, unit = t.quantity, t.price, t.product.warehouse_unit
        if t.type == TransactionType.IN_STOCK and t.product.conversion_multiplier and t.product.conversion_multiplier != 1.0:
            qty, price, unit = t.quantity / t.product.conversion_multiplier, t.price * t.product.conversion_multiplier, t.product.purchase_unit
            if is_discrete_unit(t.product.purchase_unit): qty = round(qty)
        elif is_discrete_unit(unit): qty = round(qty)
            
        data.append({"Kuupäev": t.transaction_date.strftime("%d.%m.%Y %H:%M"), "Tüüp": ttype, "Tarnija": t.supplier.name if t.supplier else "-", "Tarnija kood": t.supplier_code or "-", "Toode": t.product.name, "Kogus": qty, "Ühik": unit, "Hind (€)": price})
        
    if data:
        df = pd.DataFrame(data)
        with h_col2:
            render_excel_download(df, "ajalugu")
        st.dataframe(df.style.map(lambda v: format_color_status(v, ['Väljaminek (OUT)'], ['Sissetulek (IN)'], ['Kanti tootmisse (TO_PROD)'], ['Tagastus (RETURN)'], ['Tootmise kulu (PROD_CONS)']), subset=['Tüüp']).format({"Kogus": "{:g}", "Hind (€)": "{:.2f}"}), use_container_width=True, hide_index=True, height=600)
    else: st.info("Kandeid ei leitud.")

# ==========================================
# 5. PEAMINE ROUTER
# ==========================================
if menyuu_valik == "📊 Ladu ja Töölaud": render_dashboard()
elif menyuu_valik == "📋 Tootekataloog": render_catalog()
elif menyuu_valik in ["📥 Sissetulek", "📤 Väljastus / Tootmine"]: render_transactions(menyuu_valik == "📥 Sissetulek")
elif menyuu_valik == "🛒 Ostutellimused": render_orders(db)
elif menyuu_valik == "📝 Inventuur / Tagastus": render_inventory(db)
elif menyuu_valik == "✨ Lisa / Muuda toodet": render_product_management(db)
elif menyuu_valik == "🕒 Kannete ajalugu": render_history(db)