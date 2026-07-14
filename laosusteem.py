import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime, Enum, text, Date, func, case
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, joinedload
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import enum
import io
from collections import defaultdict

# --- LEHE SEADISTUS JA SISSELOGIMINE ---
st.set_page_config(page_title="Nutikas Laosüsteem", page_icon="📦", layout="wide", initial_sidebar_state="expanded")

def check_password():
    """Kontrollib, kas kasutaja on õige parooli sisestanud."""
    def password_entered():
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if not st.session_state["password_correct"]:
        st.markdown("<br><br><h1 style='text-align: center;'>🔒 Turvaline ligipääs</h1>", unsafe_allow_html=True)
        c1, c2, c3 = st.columns([1, 2, 1])
        with c2:
            st.text_input("Palun sisesta laosüsteemi parool:", type="password", on_change=password_entered, key="password")
            if st.session_state.get("password_correct") == False:
                st.error("😕 Vale parool! Proovi uuesti.")
        return False
    return True

if not check_password():
    st.stop()

# ==========================================
# 1. ANDMEBAASI SEADISTUS (OPTIMEERITUD ÜHENDUS)
# ==========================================
SQLALCHEMY_DATABASE_URL = st.secrets["SUPABASE_URL"]
Base = declarative_base()

def get_estonian_time():
    return datetime.now(ZoneInfo("Europe/Tallinn")).replace(tzinfo=None)

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
    # Suurte andmemahtude korral on välisvõtmetel indeksid hädavajalikud (määratud andmebaasi seadistuses hiljem automaatselt)
    product_id = Column(Integer, ForeignKey("products.id"), index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True, index=True)
    supplier_code = Column(String, nullable=True) 
    supplier_product_name = Column(String, nullable=True) 
    type = Column(Enum(TransactionType), index=True)
    quantity = Column(Float)
    price = Column(Float)
    transaction_date = Column(DateTime, default=get_estonian_time, index=True)
    notes = Column(String, nullable=True)

    product = relationship("Product", back_populates="transactions")
    supplier = relationship("Supplier", back_populates="transactions")

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True, index=True)
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

@st.cache_resource(show_spinner=False)
def init_database_connection():
    """Loob andmebaasi mootori, kontrollib struktuuri ja hoiab sessiooni vahemälus."""
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=1800
    )
    
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        def get_columns(table_name):
            result = conn.execute(text(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table_name}'"))
            return [row[0] for row in result]

        # Veerud (tagasiühilduvus vanema skeemiga)
        columns_t = get_columns("transactions")
        if columns_t:
            if "supplier_id" not in columns_t: conn.execute(text("ALTER TABLE transactions ADD COLUMN supplier_id INTEGER REFERENCES suppliers(id)"))
            if "supplier_code" not in columns_t: conn.execute(text("ALTER TABLE transactions ADD COLUMN supplier_code VARCHAR"))
            if "supplier_product_name" not in columns_t: conn.execute(text("ALTER TABLE transactions ADD COLUMN supplier_product_name VARCHAR"))
            
        columns_p = get_columns("products")
        if columns_p:
            if "conversion_multiplier" not in columns_p: conn.execute(text("ALTER TABLE products ADD COLUMN conversion_multiplier FLOAT DEFAULT 1.0"))
            
        columns_po = get_columns("purchase_orders")
        if columns_po:
            if "supplier_code" not in columns_po: conn.execute(text("ALTER TABLE purchase_orders ADD COLUMN supplier_code VARCHAR"))
            if "supplier_product_name" not in columns_po: conn.execute(text("ALTER TABLE purchase_orders ADD COLUMN supplier_product_name VARCHAR"))

        # KRIITILINE KIIRUSE JAOKS 50 000+ RIDADE KORRAL: Lisame otse andmebaasi otsingu-indeksid
        try:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_trans_prod_id ON transactions(product_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_trans_sup_id ON transactions(supplier_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_po_prod_id ON purchase_orders(product_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_po_sup_id ON purchase_orders(supplier_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_trans_type ON transactions(type)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_trans_date ON transactions(transaction_date)"))
        except Exception:
            pass # Igaks juhuks kinni püütud, juhul kui andmebaasi dialekt (nt vana SQLite) toetab teist süntaksit
            
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)

SessionLocal = init_database_connection()

def get_db():
    db = SessionLocal()
    try:
        return db
    except Exception:
        db.close()


# ==========================================
# 2. ABIFUNKTSIOONID JA OPTIMEERITUD PÄRINGUD
# ==========================================
def convert_df_to_excel(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Andmed')
    return output.getvalue()

def render_excel_download(df, prefix="andmed"):
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    st.download_button(
        label="📥 Laadi alla Excel (xlsx)",
        data=convert_df_to_excel(df),
        file_name=f"{prefix}_{get_estonian_time().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

def is_discrete_unit(unit_str):
    if not unit_str: return False
    return unit_str.strip().lower() in ['tk', 'tükk', 'komplekt', 'paar']

def format_color_status(val, red_vals, green_vals, yellow_vals=[], blue_vals=[], purple_vals=[]):
    if val in green_vals: return 'color: #10B981; font-weight: 700;'
    if val in red_vals: return 'color: #EF4444; font-weight: 700;'
    if val in yellow_vals: return 'color: #F59E0B; font-weight: 700;'
    if val in blue_vals: return 'color: #3B82F6; font-weight: 700;'
    if val in purple_vals: return 'color: #8B5CF6; font-weight: 700;'
    return ''


def calculate_global_inventory(db):
    in_qty = func.coalesce(func.sum(case((Transaction.type == TransactionType.IN_STOCK, Transaction.quantity), else_=0)), 0)
    out_qty = func.coalesce(func.sum(case((Transaction.type == TransactionType.OUT_STOCK, Transaction.quantity), else_=0)), 0)
    ret_qty = func.coalesce(func.sum(case((Transaction.type == TransactionType.RETURN, Transaction.quantity), else_=0)), 0)
    to_prod_qty = func.coalesce(func.sum(case((Transaction.type == TransactionType.TO_PROD, Transaction.quantity), else_=0)), 0)
    prod_cons_qty = func.coalesce(func.sum(case((Transaction.type == TransactionType.PROD_CONS, Transaction.quantity), else_=0)), 0)
    
    in_cost = func.coalesce(func.sum(case((Transaction.type == TransactionType.IN_STOCK, Transaction.quantity * Transaction.price), else_=0)), 0)

    results = db.query(
        Product.id, Product.code, Product.name, Product.product_group,
        Product.default_price, Product.warehouse_unit,
        in_qty.label('in_qty'), out_qty.label('out_qty'), ret_qty.label('ret_qty'),
        to_prod_qty.label('to_prod_qty'), prod_cons_qty.label('prod_cons_qty'),
        in_cost.label('in_cost')
    ).outerjoin(Transaction, Product.id == Transaction.product_id).group_by(Product.id).all()

    inventory_data = []
    total_items_main, total_items_prod, total_value = 0, 0, 0.0

    for r in results:
        main_stock = round((r.in_qty + r.ret_qty) - r.out_qty - r.to_prod_qty, 4)
        prod_stock = round(r.to_prod_qty - r.prod_cons_qty, 4)
        
        if is_discrete_unit(r.warehouse_unit):
            main_stock = round(main_stock)
            prod_stock = round(prod_stock)
            
        avg_price = float(r.in_cost) / float(r.in_qty) if r.in_qty > 0 else (r.default_price or 0.0)
        
        total_items_main += main_stock
        total_items_prod += prod_stock
        total_value += (main_stock + prod_stock) * avg_price
        
        if main_stock != 0 or prod_stock != 0:
            inventory_data.append({
                "Tootekood": r.code or "-",
                "Nimetus": r.name,
                "Tooterühm": r.product_group or "-",
                "Põhiladu": main_stock,
                "Tootmises": prod_stock,
                "Laoühik": r.warehouse_unit,
                "Keskmine hind (€)": avg_price,
                "Koguväärtus (€)": (main_stock + prod_stock) * avg_price
            })
            
    return len(results), inventory_data, total_items_main, total_items_prod, total_value

# KÄRBITUD OBJEKTID: Süsteem laeb nüüd rippmenüüdesse tuhandete ORM mudelite asemel ainult toored andmed, kiirendades laadimist sadu kordi.
def get_product_options(db):
    products = db.query(
        Product.id, Product.name, Product.code, Product.product_group,
        Product.purchase_unit, Product.warehouse_unit, 
        Product.conversion_multiplier, Product.default_price
    ).order_by(Product.name).all()
    return {f"{p.name} ({p.code if p.code else 'Kood puudub'})": p for p in products}

def get_supplier_names(db):
    return [s[0] for s in db.query(Supplier.name).order_by(Supplier.name).all()]

def get_product_main_stock(db, product_id):
    # Kiire tänu uuele andmebaasi indeksile idx_trans_prod_id
    res = db.query(
        func.sum(case((Transaction.type == TransactionType.IN_STOCK, Transaction.quantity), else_=0)),
        func.sum(case((Transaction.type == TransactionType.RETURN, Transaction.quantity), else_=0)),
        func.sum(case((Transaction.type == TransactionType.OUT_STOCK, Transaction.quantity), else_=0)),
        func.sum(case((Transaction.type == TransactionType.TO_PROD, Transaction.quantity), else_=0))
    ).filter(Transaction.product_id == product_id).first()
    
    if not res or res[0] is None: return 0.0
    return (res[0] or 0) + (res[1] or 0) - (res[2] or 0) - (res[3] or 0)


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
    
    /* Sunnime tabeli ja filtrikastid absoluutselt 100% laiuseks, isegi kui andmeid on vähe */
    [data-testid="stDataFrame"] { background-color: #FFFFFF; border-radius: 16px; padding: 1rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); border: 1px solid #E2E8F0; box-sizing: border-box; width: 100% !important; min-width: 100% !important; }
    [data-testid="stExpander"] { background-color: #FFFFFF; border-radius: 16px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); border: 1px solid #E2E8F0; box-sizing: border-box; overflow: hidden; width: 100% !important; min-width: 100% !important; }
    [data-testid="stExpander"] details { border-color: transparent; }
</style>
""", unsafe_allow_html=True)

st.sidebar.markdown("""
    <div style="text-align: center; padding-top: 0rem; padding-bottom: 2rem;">
        <h1 style="color: #1E293B; font-size: 2.4rem; font-weight: 800; letter-spacing: -1.5px; margin-bottom: 0;">📦 Ladu</h1>
        <p style="color: #64748B; font-size: 0.85rem; margin-top: 5px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.5px;">Nutikas Haldussüsteem</p>
    </div>
""", unsafe_allow_html=True)

menyuu_valik = st.sidebar.radio("Menüü", [
    "📊 Ladu ja Töölaud", "📋 Tootekataloog", "📥 Sissetulek", "📤 Väljastus / Tootmine", 
    "🛒 Ostutellimused", "📝 Inventuur / Tagastus", "✨ Lisa / Muuda toodet", "🕒 Kannete ajalugu"
], label_visibility="collapsed")
st.sidebar.markdown("<br><br>", unsafe_allow_html=True)
st.sidebar.caption("Versioon 10.5 (Täis-Optimeeritud - Indeksid+Tuples)")


# ==========================================
# 4. LEHEKÜLGEDE FUNKTSIOONID
# ==========================================

def render_dashboard(db):
    h_col1, h_col2 = st.columns([3, 1])
    with h_col1: st.title("📊 Ladu ja Töölaud")
    
    total_products, inventory_data, total_items_main, total_items_prod, total_value = calculate_global_inventory(db)
    
    df = pd.DataFrame(inventory_data) if inventory_data else pd.DataFrame()
            
    # Mõõdikud kuvavad alati KOGU lao seisu
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Erinevaid tooteid", total_products)
    m2.metric("Esemeid PÕHILAOS", f"{total_items_main:g}")
    m3.metric("Esemeid TOOTMISES", f"{total_items_prod:g}")
    m4.metric("Lao koguväärtus", f"{total_value:,.2f} €".replace(",", " "))

    st.markdown("<br><br>", unsafe_allow_html=True)
    st.subheader("Hetke laoseis ja asukohad")

    if not df.empty:
        # --- FILTREERIMINE ---
        with st.expander("🔍 Otsing ja filtrid", expanded=True):
            f1, f2, f3 = st.columns(3)
            with f1:
                f_kood = st.text_input("Tootekood (osaline otsing)", key="dash_kood")
            with f2:
                f_nimi = st.text_input("Nimetus (osaline otsing)", key="dash_nimi")
            with f3:
                all_groups = sorted([g for g in df["Tooterühm"].unique() if g and g != "-"])
                f_grupp = st.multiselect("Tooterühm", options=all_groups, key="dash_grupp")
                
        filtered_df = df.copy()
        if f_kood:
            filtered_df = filtered_df[filtered_df["Tootekood"].astype(str).str.contains(f_kood, case=False, na=False)]
        if f_nimi:
            filtered_df = filtered_df[filtered_df["Nimetus"].astype(str).str.contains(f_nimi, case=False, na=False)]
        if f_grupp:
            filtered_df = filtered_df[filtered_df["Tooterühm"].isin(f_grupp)]
            
        st.markdown(f"<div style='margin-top: 1rem; margin-bottom: 1rem; padding-left: 0.5rem;'><span style='color:#64748B; font-weight: 600; font-size:1rem;'>Kuvatakse {len(filtered_df)} rida</span></div>", unsafe_allow_html=True)
        # ----------------------

        # Nüüd renderdame Exceli nupu üles paremale nurka, aga anname talle filtreeritud andmed!
        with h_col2: 
            render_excel_download(filtered_df, "laoseis")

        def hi_main(val): return 'color: #10B981; font-weight: 700;' if val > 0 else ('color: #EF4444; font-weight: 700;' if val < 0 else 'color: #94A3B8;')
        def hi_prod(val): return 'color: #F59E0B; font-weight: 700;' if val > 0 else 'color: #94A3B8;'
            
        styled_df = filtered_df.style.map(hi_main, subset=['Põhiladu']).map(hi_prod, subset=['Tootmises']).format({
            "Põhiladu": "{:g}", "Tootmises": "{:g}", "Keskmine hind (€)": "{:.2f}", "Koguväärtus (€)": "{:.2f}"
        })
        st.dataframe(styled_df, use_container_width=True, hide_index=True, height=550)
    else:
        st.info("ℹ️ Ladu on hetkel tühi. Lisa vasakult menüüst uusi tooteid ja tee sissekandeid.")

def render_catalog(db):
    h_col1, h_col2 = st.columns([3, 1])
    with h_col1:
        st.title("📋 Tootekataloog")
        st.markdown("Siin on nimekiri kõikidest andmebaasi registreeritud toodetest koos seotud tarnijatega.")
        
    st.markdown("<br>", unsafe_allow_html=True)
    
    # 50k VASTUPIDAVUS: Küsib vaid minimaalsed veerud, ei lae suuri andmebaasi objekte
    products = db.query(
        Product.id, Product.code, Product.name, Product.product_group, 
        Product.purchase_unit, Product.warehouse_unit, Product.conversion_multiplier
    ).order_by(Product.name).all()
    
    if not products:
        st.info("ℹ️ Kataloog on hetkel tühi.")
        return
        
    trans_sups = db.query(Transaction.product_id, Supplier.name, Transaction.supplier_code, Transaction.supplier_product_name)\
        .join(Supplier, Transaction.supplier_id == Supplier.id)\
        .filter(Transaction.type == TransactionType.IN_STOCK).distinct().all()
        
    order_sups = db.query(PurchaseOrder.product_id, Supplier.name, PurchaseOrder.supplier_code, PurchaseOrder.supplier_product_name)\
        .join(Supplier, PurchaseOrder.supplier_id == Supplier.id).distinct().all()
        
    supplier_mapping = defaultdict(set)
    for r in trans_sups + order_sups:
        supplier_mapping[r[0]].add((r[1], r[2], r[3]))

    catalog_data = []
    for p in products:
        suhe_txt = f"1 {p.purchase_unit} = {p.conversion_multiplier or 1.0:g} {p.warehouse_unit}"
        base_dict = {"Tootekood": p.code or "-", "Nimetus": p.name, "Tooterühm": p.product_group or "-", "Ühikute suhe (Ost vs Ladu)": suhe_txt}
        
        unique_suppliers = supplier_mapping.get(p.id, set())
        
        if not unique_suppliers:
            catalog_data.append({**base_dict, "Tarnija": "-", "Tarnija kood": "-", "Tarnija toote nimetus": "-"})
        else:
            for s_name, s_code, s_prod in unique_suppliers:
                catalog_data.append({**base_dict, "Tarnija": s_name, "Tarnija kood": s_code or "-", "Tarnija toote nimetus": s_prod or "-"})
        
    df_cat = pd.DataFrame(catalog_data)
    
    # --- FILTREERIMINE ---
    with st.expander("🔍 Otsing ja filtrid", expanded=True):
        f1, f2, f3, f4 = st.columns(4)
        with f1:
            f_kood = st.text_input("Tootekood (osaline otsing)")
        with f2:
            f_nimi = st.text_input("Nimetus (osaline otsing)")
        with f3:
            all_groups = sorted([g for g in df_cat["Tooterühm"].unique() if g and g != "-"])
            f_grupp = st.multiselect("Tooterühm", options=all_groups)
        with f4:
            all_sups = sorted([s for s in df_cat["Tarnija"].unique() if s and s != "-"])
            f_tarnija = st.multiselect("Tarnija", options=all_sups)
            
    filtered_df = df_cat.copy()
    if f_kood:
        filtered_df = filtered_df[filtered_df["Tootekood"].str.contains(f_kood, case=False, na=False)]
    if f_nimi:
        filtered_df = filtered_df[filtered_df["Nimetus"].str.contains(f_nimi, case=False, na=False)]
    if f_grupp:
        filtered_df = filtered_df[filtered_df["Tooterühm"].isin(f_grupp)]
    if f_tarnija:
        filtered_df = filtered_df[filtered_df["Tarnija"].isin(f_tarnija)]
        
    st.markdown(f"<div style='margin-top: 1rem; margin-bottom: 1rem; padding-left: 0.5rem;'><span style='color:#64748B; font-weight: 600; font-size:1rem;'>Kuvatakse {len(filtered_df)} rida</span></div>", unsafe_allow_html=True)
    # ----------------------

    with h_col2: render_excel_download(filtered_df, "tootekataloog") # Excel laeb nüüd alla filtreeritud info!
    st.dataframe(filtered_df, use_container_width=True, hide_index=True, height=600)

def render_transactions(db, is_in_transaction):
    st.title("📥 Sissetulek" if is_in_transaction else "📤 Väljastus ja Tootmisse kandmine")
    if is_in_transaction: st.markdown("Registreeri lattu sissetulev kaup. Täida info **ostuühikutes**.")
    else: st.markdown("Määra, kas kannad kauba **Tootmisse** või teed **Tavalise väljamineku** (nt müük).")
        
    st.markdown("<br>", unsafe_allow_html=True)
    if 'trans_success' in st.session_state: st.success(st.session_state.pop('trans_success'))
    if 'trans_error' in st.session_state: st.error(st.session_state.pop('trans_error'))
        
    action_type = "Sissetulek"
    if not is_in_transaction:
        action_type = st.radio("Vali tegevus:", ["Kanna TOOTMISSE", "Tavaline VÄLJAMINEK (Müük vms)"], horizontal=True)
        st.markdown("---")
    
    col1, col2 = st.columns([2, 1]) 
    with col1:
        with st.container():
            product_options = get_product_options(db)
            selected_product_str = st.selectbox("Otsi toodet oma kataloogist", options=["Vali toode..."] + list(product_options.keys()))
            
            if selected_product_str == "Vali toode...": return
            
            prod = product_options[selected_product_str]
            active_unit = prod.purchase_unit if is_in_transaction else prod.warehouse_unit
            
            t_sups = db.query(Supplier.name).join(Transaction).filter(Transaction.product_id == prod.id, Transaction.type == TransactionType.IN_STOCK).distinct().all()
            o_sups = db.query(Supplier.name).join(PurchaseOrder).filter(PurchaseOrder.product_id == prod.id).distinct().all()
            known_sups = sorted(list(set([s[0] for s in t_sups + o_sups])))
            
            last_sup = db.query(Supplier.name).join(Transaction).filter(Transaction.product_id == prod.id, Transaction.type == TransactionType.IN_STOCK).order_by(Transaction.transaction_date.desc()).first()
            last_sup_name = last_sup[0] if last_sup else None

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
                    supplier_names = get_supplier_names(db)
                    other_sups = sorted(list(set(supplier_names) - set(known_sups)))
                    db_sup_choice = st.selectbox("Otsi olemasolevat tarnijat süsteemist", options=["➕ Sisesta uus tarnija..."] + other_sups)
                    if db_sup_choice == "➕ Sisesta uus tarnija...":
                        new_supplier_name = st.text_input("Uue tarnija nimi", placeholder="Sisesta uus tarnija nimi siia")
                        actual_supplier_name = "- Puudub -"
                    else: actual_supplier_name = db_sup_choice

                c_options, n_options = [""], [""]
                if actual_supplier_name not in ["- Puudub -", "🌍 Otsi andmebaasist / Lisa uus..."]:
                    t_codes = db.query(Transaction.supplier_code).join(Supplier).filter(Transaction.product_id == prod.id, Supplier.name == actual_supplier_name).distinct().all()
                    o_codes = db.query(PurchaseOrder.supplier_code).join(Supplier).filter(PurchaseOrder.product_id == prod.id, Supplier.name == actual_supplier_name).distinct().all()
                    c_options = sorted([c[0] for c in t_codes + o_codes if c[0]])
                    
                    t_names = db.query(Transaction.supplier_product_name).join(Supplier).filter(Transaction.product_id == prod.id, Supplier.name == actual_supplier_name).distinct().all()
                    o_names = db.query(PurchaseOrder.supplier_product_name).join(Supplier).filter(PurchaseOrder.product_id == prod.id, Supplier.name == actual_supplier_name).distinct().all()
                    n_options = sorted([n[0] for n in t_names + o_names if n[0]])

                sc_col1, sc_col2 = st.columns(2)
                with sc_col1:
                    sup_code = st.selectbox("Tarnija kood", options=c_options + ["➕ Sisesta uus..."]) if c_options else st.text_input("Tarnija kood")
                    if sup_code == "➕ Sisesta uus...": sup_code = st.text_input("Uus tarnija kood", placeholder="Sisesta kood siia")
                with sc_col2:
                    sup_prod_name = st.selectbox("Tarnija toote nimetus", options=n_options + ["➕ Sisesta uus..."]) if n_options else st.text_input("Tarnija toote nimetus")
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
        curr_stock = get_product_main_stock(db, prod.id)
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
        prod_options = get_product_options(db_session)
        prod = prod_options.get(sel_prod_str)

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
            product_options = get_product_options(db)
            sel_prod = st.selectbox("Vali toode", options=["Vali..."] + list(product_options.keys()), key="new_ord_prod")
            if sel_prod != "Vali...":
                prod = product_options[sel_prod]
                
                t_sups = db.query(Supplier.name).join(Transaction).filter(Transaction.product_id == prod.id, Transaction.type == TransactionType.IN_STOCK).distinct().all()
                o_sups = db.query(Supplier.name).join(PurchaseOrder).filter(PurchaseOrder.product_id == prod.id).distinct().all()
                known_sups = sorted(list(set([s[0] for s in t_sups + o_sups])))
                
                f1, f2 = st.columns(2)
                with f1: st.number_input(f"Kogus ({prod.purchase_unit})", min_value=0.001, value=1.0, key="new_ord_qty")
                with f2: st.number_input(f"Hind/{prod.purchase_unit} (€)", min_value=0.0, value=float(prod.default_price or 0.0), key="new_ord_price")
                
                o_sup_ch = st.selectbox("Tarnija", options=["- Puudub -"] + known_sups + ["🌍 Otsi andmebaasist..."], key="new_ord_sup_ch")
                act_sup = o_sup_ch
                if o_sup_ch == "🌍 Otsi andmebaasist...":
                    supplier_names = get_supplier_names(db)
                    db_sup = st.selectbox("Otsi olemasolevat", options=["➕ Uus tarnija..."] + sorted(list(set(supplier_names)-set(known_sups))), key="new_ord_db_sup")
                    if db_sup == "➕ Uus tarnija...":
                        st.text_input("Uue tarnija nimi", key="new_ord_n_sup")
                        act_sup = "- Puudub -"
                    else: act_sup = db_sup
                        
                c_opt, n_opt = [""], [""]
                if act_sup not in ["- Puudub -", "🌍 Otsi andmebaasist..."]:
                    t_codes = db.query(Transaction.supplier_code).join(Supplier).filter(Transaction.product_id == prod.id, Supplier.name == act_sup).distinct().all()
                    o_codes = db.query(PurchaseOrder.supplier_code).join(Supplier).filter(PurchaseOrder.product_id == prod.id, Supplier.name == act_sup).distinct().all()
                    c_opt = sorted([c[0] for c in t_codes + o_codes if c[0]])
                    
                    t_names = db.query(Transaction.supplier_product_name).join(Supplier).filter(Transaction.product_id == prod.id, Supplier.name == act_sup).distinct().all()
                    o_names = db.query(PurchaseOrder.supplier_product_name).join(Supplier).filter(PurchaseOrder.product_id == prod.id, Supplier.name == act_sup).distinct().all()
                    n_opt = sorted([n[0] for n in t_names + o_names if n[0]])
                
                sc1, sc2 = st.columns(2)
                with sc1:
                    o_code = st.selectbox("Tarnija kood", options=c_opt+["➕ Uus..."], key="new_ord_code_sel") if c_opt else st.text_input("Tarnija kood", key="new_ord_code_txt")
                    if o_code == "➕ Uus...": st.text_input("Uus kood", key="new_ord_code_new")
                with sc2:
                    o_name = st.selectbox("Tarnija toote nimetus", options=n_opt+["➕ Uus..."], key="new_ord_name_sel") if n_opt else st.text_input("Tarnija toote nimetus", key="new_ord_name_txt")
                    if o_name == "➕ Uus...": st.text_input("Uus nimetus", key="new_ord_name_new")
                
                d1, d2 = st.columns(2)
                with d1: st.date_input("Tellimuse kuupäev", value=get_estonian_time().date(), key="new_ord_date")
                with d2: st.date_input("Lubatud tarneaeg", value=get_estonian_time().date() + timedelta(days=7), key="new_ord_exp")
                
                st.button("💾 Salvesta tellimus süsteemi", use_container_width=True, on_click=create_order_callback)

    with tab_act:
        pend = db.query(PurchaseOrder).options(joinedload(PurchaseOrder.product), joinedload(PurchaseOrder.supplier)).filter(PurchaseOrder.status == OrderStatus.PENDING).all()
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
        hist = db.query(PurchaseOrder).options(joinedload(PurchaseOrder.product), joinedload(PurchaseOrder.supplier)).filter(PurchaseOrder.status != OrderStatus.PENDING).order_by(PurchaseOrder.id.desc()).limit(1000).all()
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
                
                _, inventory_data, _, _, _ = calculate_global_inventory(db)
                
                for i in valid:
                    p = i['prod']
                    left_qty = round(i['left_qty']) if is_discrete_unit(p.warehouse_unit) else i['left_qty']
                    
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
                        transactions_to_make.append({"prod": p, "cons": consumed_qty, "avg_p": stock_info["Keskmine hind (€)"] if stock_info else p.default_price})

                if preview_data:
                    st.markdown("### 🔍 Inventuuri eelvaade")
                    st.dataframe(pd.DataFrame(preview_data), use_container_width=True, hide_index=True)
                    
                    if st.button("💾 Kinnita tootmise kulud", type="primary", use_container_width=True):
                        for item in transactions_to_make:
                            p = item['prod']
                            c_qty = item['cons']
                            avg_p = item['avg_p']
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
            product_options = get_product_options(db)
            sel = st.selectbox("Otsi toodet", ["Vali..."] + list(product_options.keys()))
            if sel != "Vali...": prod_form(product_options[sel], key="edit")
            
    with t3:
        st.markdown("Lisa mitu toodet korraga, laadides üles täidetud Exceli faili. **Nimetus** on kohustuslik väli.")
        c1, c2 = st.columns(2)
        
        with c1:
            st.subheader("1. Laadi mall alla")
            template_df = pd.DataFrame([{
                "Nimetus": "Näidistoode 1", "Kood": "KOOD001", "Rühm": "Materjalid",
                "Baashind (€)": 12.50, "Laoühik": "tk", "Ostuühik": "pk",
                "Kordaja (Mitu laoühikut on ostuühikus)": 10,
                "Tarnija": "Tarnija OÜ", "Tarnija kood": "TAR-001", "Tarnija toote nimetus": "Originaalnimi 1"
            }, {
                "Nimetus": "Näidistoode 2", "Kood": "", "Rühm": "",
                "Baashind (€)": 5.00, "Laoühik": "m", "Ostuühik": "m",
                "Kordaja (Mitu laoühikut on ostuühikus)": 1,
                "Tarnija": "", "Tarnija kood": "", "Tarnija toote nimetus": ""
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
                df_upload = pd.read_excel(uploaded_file, engine='openpyxl', dtype=str)
                
                if "Nimetus" not in df_upload.columns:
                    st.error("⚠️ Fail peab sisaldama 'Nimetus' veergu! Palun kasuta allalaaditavat malli.")
                else:
                    st.markdown("### Eelvaade")
                    st.dataframe(df_upload, use_container_width=True, hide_index=True)
                    
                    if st.button("💾 Salvesta tooted andmebaasi", type="primary", use_container_width=True):
                        with st.spinner("Salvestan andmeid andmebaasi, palun oota..."):
                            added_count = 0
                            error_count = 0
                            
                            try:
                                existing_codes = {p.code for p in db.query(Product.code).filter(Product.code.isnot(None)).all()}
                                existing_names_groups = {(p.name, p.product_group) for p in db.query(Product.name, Product.product_group).all()}
                                supplier_map = {s.name: s for s in db.query(Supplier).all()}

                                def parse_float(val, default_val=0.0):
                                    if pd.isna(val): return default_val
                                    val_str = str(val).strip().lower()
                                    if val_str in ['nan', 'none', 'null', '']: return default_val
                                    try: return float(val_str.replace(',', '.'))
                                    except (ValueError, TypeError): return default_val

                                def parse_str(val):
                                    if pd.isna(val): return None
                                    val_str = str(val).strip()
                                    if val_str.lower() in ['nan', 'none', 'null', '']: return None
                                    return val_str

                                for _, row in df_upload.iterrows():
                                    name = parse_str(row.get("Nimetus"))
                                    if not name: continue
                                        
                                    code = parse_str(row.get("Kood"))
                                    group = parse_str(row.get("Rühm"))
                                    price = parse_float(row.get("Baashind (€)"), 0.0)
                                    wh_unit = parse_str(row.get("Laoühik")) or "tk"
                                    pu_unit = parse_str(row.get("Ostuühik")) or "tk"
                                    mult = parse_float(row.get("Kordaja (Mitu laoühikut on ostuühikus)"), 1.0)

                                    supplier_name = parse_str(row.get("Tarnija"))
                                    sup_code = parse_str(row.get("Tarnija kood"))
                                    sup_prod_name = parse_str(row.get("Tarnija toote nimetus"))

                                    if code and code in existing_codes:
                                        error_count += 1
                                        continue
                                    if (name, group) in existing_names_groups:
                                        error_count += 1
                                        continue
                                    
                                    new_product = Product(
                                        name=name, code=code, product_group=group, 
                                        default_price=price, warehouse_unit=wh_unit, 
                                        purchase_unit=pu_unit, conversion_multiplier=mult
                                    )
                                    db.add(new_product)
                                    db.flush()
                                    
                                    if code: existing_codes.add(code)
                                    existing_names_groups.add((name, group))
                                        
                                    if supplier_name:
                                        if supplier_name not in supplier_map:
                                            sup = Supplier(name=supplier_name)
                                            db.add(sup)
                                            db.flush()
                                            supplier_map[supplier_name] = sup
                                        else: sup = supplier_map[supplier_name]
                                            
                                        link_trans = Transaction(
                                            product_id=new_product.id, supplier_id=sup.id, supplier_code=sup_code,
                                            supplier_product_name=sup_prod_name, type=TransactionType.IN_STOCK,
                                            quantity=0.0, price=price, notes="Esmane tarnija sidumine (Excelist laadimine)"
                                        )
                                        db.add(link_trans)

                                    added_count += 1
                                        
                                if added_count > 0:
                                    db.commit()
                                    msg = f"✅ Edukas! Lisati {added_count} uut toodet."
                                    if error_count > 0: msg += f" (Eirati {error_count} toodet, mis olid juba süsteemis)."
                                    st.session_state['prod_success'] = msg
                                else: st.session_state['prod_error'] = f"⚠️ Ühtegi uut toodet ei lisatud (kõik {error_count} olid juba olemas või info puudus)."
                                st.rerun()

                            except Exception as db_error:
                                db.rollback() 
                                st.error(f"⚠️ Viga andmebaasi salvestamisel (transaktsioon tühistati): {db_error}")
                                
            except Exception as e:
                st.error(f"Viga faili lugemisel: {e}")

def render_history(db):
    h_col1, h_col2 = st.columns([3, 1])
    with h_col1: st.title("🕒 Kannete logi")
        
    f_val = st.radio("Filtreeri:", ["Kõik kanded", "Sissetulek (IN)", "Tootmisse (TO_PROD)", "Kulu (PROD_CONS)", "Väljaminek (OUT)", "Tagastus (RETURN)"], horizontal=True)
    
    # KÄRBITUD OBJEKTID: Päritakse otse lihtsad veerud, see eemaldab tuhandete ORM objektide laadimise RAM mällu. See laeb tuhandeid ridu paari millisekundiga!
    q = db.query(
        Transaction.transaction_date, Transaction.type, Transaction.quantity, Transaction.price, Transaction.supplier_code,
        Supplier.name.label("supplier_name"),
        Product.name.label("product_name"), Product.warehouse_unit, Product.purchase_unit, Product.conversion_multiplier
    ).outerjoin(Supplier, Transaction.supplier_id == Supplier.id).join(Product, Transaction.product_id == Product.id).order_by(Transaction.transaction_date.desc())
    
    if "IN" in f_val: q = q.filter(Transaction.type == TransactionType.IN_STOCK)
    elif "OUT" in f_val: q = q.filter(Transaction.type == TransactionType.OUT_STOCK)
    elif "TO_PROD" in f_val: q = q.filter(Transaction.type == TransactionType.TO_PROD)
    elif "PROD_CONS" in f_val: q = q.filter(Transaction.type == TransactionType.PROD_CONS)
    elif "RETURN" in f_val: q = q.filter(Transaction.type == TransactionType.RETURN)
    
    data = []
    # Rakendame siiski ohutuse mõttes piirangu (limiidi 3000 tk laadimist korraga on tavakasutajale piisav)
    for t in q.limit(3000).all():
        ttype_name = t.type.name if hasattr(t.type, 'name') else t.type
        ttype = {"IN_STOCK": "Sissetulek (IN)", "OUT_STOCK": "Väljaminek (OUT)", "TO_PROD": "Kanti tootmisse (TO_PROD)", "PROD_CONS": "Tootmise kulu (PROD_CONS)", "RETURN": "Tagastus (RETURN)"}.get(ttype_name, ttype_name)
        
        qty, price, unit = t.quantity, t.price, t.warehouse_unit
        if ttype_name == "IN_STOCK" and t.conversion_multiplier and t.conversion_multiplier != 1.0:
            qty, price, unit = t.quantity / t.conversion_multiplier, t.price * t.conversion_multiplier, t.purchase_unit
            if is_discrete_unit(t.purchase_unit): qty = round(qty)
        elif is_discrete_unit(unit): qty = round(qty)
            
        data.append({"Kuupäev": t.transaction_date.strftime("%d.%m.%Y %H:%M"), "Tüüp": ttype, "Tarnija": t.supplier_name if t.supplier_name else "-", "Tarnija kood": t.supplier_code or "-", "Toode": t.product_name, "Kogus": qty, "Ühik": unit, "Hind (€)": price})
        
    if data:
        df = pd.DataFrame(data)
        with h_col2:
            render_excel_download(df, "ajalugu")
        if len(data) == 3000:
            st.warning("⚠️ Kuvatakse ainult viimased 3000 kannet, et hoida süsteem kiirena.")
        st.dataframe(df.style.map(lambda v: format_color_status(v, ['Väljaminek (OUT)'], ['Sissetulek (IN)'], ['Kanti tootmisse (TO_PROD)'], ['Tagastus (RETURN)'], ['Tootmise kulu (PROD_CONS)']), subset=['Tüüp']).format({"Kogus": "{:g}", "Hind (€)": "{:.2f}"}), use_container_width=True, hide_index=True, height=600)
    else: st.info("Kandeid ei leitud.")


# ==========================================
# 5. PEAMINE ROUTER TRY/FINALLY PLOKIS
# ==========================================
db = get_db()
try:
    if menyuu_valik == "📊 Ladu ja Töölaud": render_dashboard(db)
    elif menyuu_valik == "📋 Tootekataloog": render_catalog(db)
    elif menyuu_valik in ["📥 Sissetulek", "📤 Väljastus / Tootmine"]: render_transactions(db, menyuu_valik == "📥 Sissetulek")
    elif menyuu_valik == "🛒 Ostutellimused": render_orders(db)
    elif menyuu_valik == "📝 Inventuur / Tagastus": render_inventory(db)
    elif menyuu_valik == "✨ Lisa / Muuda toodet": render_product_management(db)
    elif menyuu_valik == "🕒 Kannete ajalugu": render_history(db)
finally:
    db.close()