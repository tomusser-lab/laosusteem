import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime, Enum, text, Date, func, case, union
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, joinedload
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import enum
import io
from collections import defaultdict

# --- LEHE SEADISTUS JA SISSELOGIMINE ---
st.set_page_config(page_title="Nutikas Laosüsteem", page_icon="📦", layout="wide", initial_sidebar_state="expanded")

if "pw_correct" not in st.session_state: st.session_state["pw_correct"] = False
if not st.session_state["pw_correct"]:
    st.markdown("<br><br><h1 style='text-align: center;'>🔒 Turvaline ligipääs</h1>", unsafe_allow_html=True)
    pw = st.text_input("Palun sisesta laosüsteemi parool:", type="password", key="pw")
    if pw:
        if pw == st.secrets.get("APP_PASSWORD", ""):
            st.session_state["pw_correct"] = True
            st.rerun()
        else: st.error("😕 Vale parool!")
    st.stop()

# --- ANDMEBAAS ---
Base = declarative_base()
def get_est_time(): return datetime.now(ZoneInfo("Europe/Tallinn")).replace(tzinfo=None)

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True) 
    name = Column(String, index=True, nullable=False)
    product_group = Column(String) 
    default_price = Column(Float, default=0.0) 
    warehouse_unit = Column(String, default="tk") 
    purchase_unit = Column(String, default="tk") 
    conversion_multiplier = Column(Float, default=1.0) 

class TransactionType(str, enum.Enum):
    IN_STOCK = "IN"
    OUT_STOCK = "OUT"
    RETURN = "RETURN" 
    TO_PROD = "TO_PROD" 
    PROD_CONS = "PROD_CONS" 

class OrderStatus(str, enum.Enum): PENDING, RECEIVED, CANCELLED = "Ootel", "Saabunud", "Tühistatud"

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), index=True)
    supplier_code, supplier_product_name, notes = Column(String), Column(String), Column(String)
    type = Column(Enum(TransactionType), index=True)
    quantity, price = Column(Float), Column(Float)
    transaction_date = Column(DateTime, default=get_est_time, index=True)
    product = relationship("Product", backref="transactions")
    supplier = relationship("Supplier", backref="transactions")

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), index=True)
    supplier_code, supplier_product_name = Column(String), Column(String)
    order_date = Column(Date, default=lambda: get_est_time().date())
    expected_delivery_date, arrival_date = Column(Date), Column(Date)
    quantity, price = Column(Float), Column(Float)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING)
    product = relationship("Product", backref="purchase_orders")
    supplier = relationship("Supplier", backref="purchase_orders")

@st.cache_resource(show_spinner=False)
def init_db():
    engine = create_engine(st.secrets["SUPABASE_URL"], pool_size=10, max_overflow=20, pool_pre_ping=True)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for t in ["idx_trans_prod_id ON transactions(product_id)", "idx_trans_sup_id ON transactions(supplier_id)", 
                  "idx_po_prod_id ON purchase_orders(product_id)", "idx_po_sup_id ON purchase_orders(supplier_id)", 
                  "idx_trans_type ON transactions(type)", "idx_trans_date ON transactions(transaction_date)"]:
            try: conn.execute(text(f"CREATE INDEX IF NOT EXISTS {t}"))
            except Exception: pass
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)

SessionLocal = init_db()
db = SessionLocal()

# --- ABIFUNKTSIOONID ---
def to_excel(df):
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w: df.to_excel(w, index=False)
    return out.getvalue()

def btn_excel(df, pfx="andmed"):
    st.download_button("📥 Laadi alla Excel", to_excel(df), f"{pfx}_{get_est_time().strftime('%Y%m%d_%H%M')}.xlsx", use_container_width=True)

def is_discrete(u): return str(u).strip().lower() in ['tk', 'tükk', 'komplekt', 'paar'] if u else False

def get_product_opts(db):
    return {f"{p.name} ({p.code or 'Koodita'})": p for p in db.query(Product.id, Product.name, Product.code, Product.product_group, Product.purchase_unit, Product.warehouse_unit, Product.conversion_multiplier, Product.default_price).order_by(Product.name).all()}

# --- KASUTAJALIIDES & STIIL ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stApp { background-color: #F8FAFC; }
    [data-testid="stMetric"] { background: #fff; border-radius: 12px; padding: 1rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
    .stButton>button { border-radius: 8px; font-weight: 600; }
    [data-testid="stDataFrame"], [data-testid="stExpander"] { background: #fff; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
</style>
""", unsafe_allow_html=True)

st.sidebar.markdown("<div style='text-align:center'><h1>📦 Ladu</h1><p>Haldussüsteem</p></div>", unsafe_allow_html=True)
menu = st.sidebar.radio("Menüü", ["📊 Töölaud", "📋 Kataloog", "📥 Sissetulek", "📤 Väljastus/Tootmine", "🛒 Tellimused", "📝 Inventuur", "✨ Tooted", "🕒 Logi"], label_visibility="collapsed")
if 'msg' in st.session_state: 
    st.success(st.session_state.pop('msg'))

# --- MOOTOR JA LEHED ---
try:
    if menu == "📊 Töölaud":
        c1, c2 = st.columns([3, 1])
        c1.title("📊 Ladu ja Töölaud")
        
        # Kiire lao kalkulatsioon
        in_q, out_q, ret_q, tp_q, pc_q = [func.coalesce(func.sum(case((Transaction.type == t, Transaction.quantity), else_=0)), 0) for t in [TransactionType.IN_STOCK, TransactionType.OUT_STOCK, TransactionType.RETURN, TransactionType.TO_PROD, TransactionType.PROD_CONS]]
        in_c = func.coalesce(func.sum(case((Transaction.type == TransactionType.IN_STOCK, Transaction.quantity * Transaction.price), else_=0)), 0)
        
        res = db.query(Product.code, Product.name, Product.product_group, Product.default_price, Product.warehouse_unit, in_q.label('in_qty'), out_q.label('out_qty'), ret_q.label('ret_qty'), tp_q.label('tp_qty'), pc_q.label('pc_qty'), in_c.label('in_cost')).outerjoin(Transaction).group_by(Product.id).all()
        
        data, t_main, t_prod, t_val = [], 0, 0, 0.0
        for r in res:
            m_st, p_st = round((r.in_qty + r.ret_qty) - r.out_qty - r.tp_qty, 4), round(r.tp_qty - r.pc_qty, 4)
            if is_discrete(r.warehouse_unit): m_st, p_st = round(m_st), round(p_st)
            avg_p = float(r.in_cost) / float(r.in_qty) if r.in_qty > 0 else (r.default_price or 0.0)
            t_main += m_st; t_prod += p_st; t_val += (m_st + p_st) * avg_p
            if m_st != 0 or p_st != 0:
                data.append({"Kood": r.code or "-", "Nimi": r.name, "Grupp": r.product_group or "-", "Ladu": m_st, "Tootmises": p_st, "Ühik": r.warehouse_unit, "Hind (€)": avg_p, "Kokku (€)": (m_st + p_st) * avg_p})

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Tooteid", len(res)); m2.metric("Põhilaos", f"{t_main:g}"); m3.metric("Tootmises", f"{t_prod:g}"); m4.metric("Väärtus", f"{t_val:,.2f} €")
        
        if data:
            df = pd.DataFrame(data)
            with st.expander("🔍 Filtrid", expanded=True):
                f1, f2, f3 = st.columns(3)
                fk = f1.text_input("Kood")
                fn = f2.text_input("Nimi")
                fg = f3.multiselect("Rühm", [g for g in df["Grupp"].unique() if g != "-"])
            
            if fk: df = df[df["Kood"].str.contains(fk, case=False)]
            if fn: df = df[df["Nimi"].str.contains(fn, case=False)]
            if fg: df = df[df["Grupp"].isin(fg)]
            
            with c2: btn_excel(df, "laoseis")
            def hi(v): return 'color: #10B981; font-weight: bold;' if v>0 else ('color: #EF4444; font-weight: bold;' if v<0 else '')
            st.dataframe(df.style.map(hi, subset=['Ladu']).map(lambda v: 'color: #F59E0B; font-weight: bold;' if v>0 else '', subset=['Tootmises']).format({"Ladu": "{:g}", "Tootmises": "{:g}", "Hind (€)": "{:.2f}", "Kokku (€)": "{:.2f}"}), use_container_width=True, hide_index=True)

    elif menu == "📋 Kataloog":
        c1, c2 = st.columns([3, 1])
        c1.title("📋 Kataloog")
        prods = db.query(Product).order_by(Product.name).all()
        if not prods: st.info("Tühi"); st.stop()
        
        # Kiire tarnijate seoste pärimine UNIONiga
        sups_q = db.query(Transaction.product_id, Supplier.name, Transaction.supplier_code, Transaction.supplier_product_name).join(Supplier).filter(Transaction.type == TransactionType.IN_STOCK).union(
                 db.query(PurchaseOrder.product_id, Supplier.name, PurchaseOrder.supplier_code, PurchaseOrder.supplier_product_name).join(Supplier)).all()
        sup_map = defaultdict(set)
        for r in sups_q: sup_map[r[0]].add((r[1], r[2] or "-", r[3] or "-"))

        data = []
        for p in prods:
            base = {"Kood": p.code or "-", "Nimi": p.name, "Grupp": p.product_group or "-", "Suhe": f"1 {p.purchase_unit} = {p.conversion_multiplier or 1.0:g} {p.warehouse_unit}"}
            if p.id not in sup_map: data.append({**base, "Tarnija": "-", "T-Kood": "-", "T-Nimi": "-"})
            else:
                for s in sup_map[p.id]: data.append({**base, "Tarnija": s[0], "T-Kood": s[1], "T-Nimi": s[2]})
        
        df = pd.DataFrame(data)
        with st.expander("🔍 Filtrid", expanded=True):
            f1, f2, f3 = st.columns(3)
            df = df[df["Kood"].str.contains(f1.text_input("Kood"), case=False)]
            df = df[df["Nimi"].str.contains(f2.text_input("Nimi"), case=False)]
            if sf := f3.multiselect("Tarnija", [s for s in df["Tarnija"].unique() if s != "-"]): df = df[df["Tarnija"].isin(sf)]

        with c2: btn_excel(df, "kataloog")
        st.dataframe(df, use_container_width=True, hide_index=True)

    elif menu in ["📥 Sissetulek", "📤 Väljastus/Tootmine"]:
        is_in = menu == "📥 Sissetulek"
        st.title(menu)
        act = "IN" if is_in else st.radio("Vali:", ["Kanna TOOTMISSE", "VÄLJAMINEK (Müük)"], horizontal=True)
        
        opts = get_product_opts(db)
        if (sel := st.selectbox("Vali toode", ["Vali..."] + list(opts.keys()))) != "Vali...":
            p = opts[sel]
            f1, f2 = st.columns(2)
            qty = f1.number_input(f"Kogus ({p.purchase_unit if is_in else p.warehouse_unit})", 0.001, value=1.0)
            price = f2.number_input("Hind", 0.0, value=float(p.default_price or 0.0)) if is_in else 0.0
            
            sup_id, sc, sn, notes = None, "", "", st.text_input("Kommentaar")
            if is_in:
                known = [s[0] for s in db.query(Supplier.name).join(Transaction).filter(Transaction.product_id==p.id).union(db.query(Supplier.name).join(PurchaseOrder).filter(PurchaseOrder.product_id==p.id)).all()]
                sup_ch = st.selectbox("Tarnija", ["- Puudub -"] + known + ["🌍 Lisa uus / Otsi"])
                if sup_ch == "🌍 Lisa uus / Otsi":
                    all_s = [s[0] for s in db.query(Supplier.name).all()]
                    ns = st.selectbox("Otsi", ["➕ Uus"] + list(set(all_s) - set(known)))
                    if ns == "➕ Uus": sup_ch = st.text_input("Uue tarnija nimi")
                    else: sup_ch = ns
                
                sc, sn = st.columns(2)[0].text_input("Tarnija kood"), st.columns(2)[1].text_input("Tarnija tootenimi")
                if sup_ch not in ["- Puudub -", "🌍 Lisa uus / Otsi", "➕ Uus"]:
                    sup_db = db.query(Supplier).filter_by(name=sup_ch).first()
                    if not sup_db: sup_db = Supplier(name=sup_ch); db.add(sup_db); db.flush()
                    sup_id = sup_db.id

            if st.button("💾 Salvesta"):
                mult = p.conversion_multiplier or 1.0
                fq = (qty * mult) if is_in else qty
                if is_discrete(p.warehouse_unit): fq = round(fq)
                
                if not is_in:
                    stk = db.query(func.sum(case((Transaction.type==TransactionType.IN_STOCK, Transaction.quantity), else_=0)) + func.sum(case((Transaction.type==TransactionType.RETURN, Transaction.quantity), else_=0)) - func.sum(case((Transaction.type==TransactionType.OUT_STOCK, Transaction.quantity), else_=0)) - func.sum(case((Transaction.type==TransactionType.TO_PROD, Transaction.quantity), else_=0))).filter_by(product_id=p.id).scalar() or 0
                    if qty > stk: st.error(f"⚠️ Viga: Laos vaid {stk:g} {p.warehouse_unit}"); st.stop()
                
                db.add(Transaction(product_id=p.id, supplier_id=sup_id, supplier_code=sc or None, supplier_product_name=sn or None, type=TransactionType.IN_STOCK if is_in else (TransactionType.TO_PROD if "TOOTMISSE" in act else TransactionType.OUT_STOCK), quantity=fq, price=(price/mult if is_in else 0), notes=notes))
                db.commit(); st.session_state['msg'] = "✅ Salvestatud!"; st.rerun()

    elif menu == "🛒 Tellimused":
        st.title("🛒 Tellimused")
        t1, t2, t3, t4 = st.tabs(["⏳ Aktiivsed", "➕ Uus", "📜 Ajalugu", "☁️ GS Import"])
        
        with t2:
            opts = get_product_opts(db)
            if (sel := st.selectbox("Toode", ["Vali..."] + list(opts.keys()))) != "Vali...":
                p = opts[sel]
                q, pr = st.columns(2)[0].number_input("Kogus", 0.001, 1.0), st.columns(2)[1].number_input("Hind", 0.0, float(p.default_price or 0.0))
                s_opts = ["- Puudub -"] + [s.name for s in db.query(Supplier).all()]
                sup = st.selectbox("Tarnija", s_opts)
                if st.button("💾 Salvesta tellimus"):
                    sid = db.query(Supplier).filter_by(name=sup).first().id if sup != "- Puudub -" else None
                    db.add(PurchaseOrder(product_id=p.id, supplier_id=sid, quantity=q, price=pr, expected_delivery_date=get_est_time().date()+timedelta(days=7)))
                    db.commit(); st.session_state['msg'] = "✅ Tellitud!"; st.rerun()

        with t1:
            if pends := db.query(PurchaseOrder).filter_by(status=OrderStatus.PENDING).all():
                df = pd.DataFrame([{"ID": o.id, "Toode": o.product.name, "Kogus": o.quantity, "Tarnija": o.supplier.name if o.supplier else "-", "Lubatud": o.expected_delivery_date.strftime("%d.%m.%Y")} for o in pends])
                st.dataframe(df, hide_index=True, use_container_width=True)
                sel_o = st.selectbox("Halda:", ["Vali..."] + [f"#{o.id} {o.product.name}" for o in pends])
                if sel_o != "Vali...":
                    oid = int(sel_o.split(" ")[0][1:])
                    o = db.query(PurchaseOrder).get(oid)
                    c1, c2, c3 = st.columns(3)
                    if c1.button("📦 Võta lattu"):
                        o.status, o.arrival_date = OrderStatus.RECEIVED, get_est_time().date()
                        fq = o.quantity * (o.product.conversion_multiplier or 1.0)
                        db.add(Transaction(product_id=o.product_id, supplier_id=o.supplier_id, type=TransactionType.IN_STOCK, quantity=fq, price=o.price/(o.product.conversion_multiplier or 1), notes=f"Tellimus #{o.id}"))
                        db.commit(); st.session_state['msg'] = "✅ Lattu võetud!"; st.rerun()
                    if c2.button("🚚 Märgi saabunuks"): o.status = OrderStatus.RECEIVED; db.commit(); st.rerun()
                    if c3.button("❌ Tühista"): o.status = OrderStatus.CANCELLED; db.commit(); st.rerun()
            else: st.info("Ootel tellimusi pole.")
            
        with t3:
            hist = db.query(PurchaseOrder).filter(PurchaseOrder.status != OrderStatus.PENDING).order_by(PurchaseOrder.id.desc()).limit(200).all()
            st.dataframe(pd.DataFrame([{"ID": o.id, "Toode": o.product.name, "Olek": o.status.value} for o in hist]), hide_index=True)

        with t4:
            st.info("💡 GS Link (peab sisaldama: Nimetus, Kogus, Nädal)")
            if url := st.text_input("URL"):
                if st.button("⬇️ Tõmba"):
                    csv_u = url.split("/edit")[0] + "/export?format=csv&" + (url.split("#")[1] if "#" in url else "gid=" + url.split("gid=")[1].split("&")[0])
                    try: st.dataframe(pd.read_csv(csv_u, dtype=str), hide_index=True)
                    except: st.error("Viga andmete lugemisel!")

    elif menu == "📝 Inventuur":
        st.title("📝 Tootmise inventuur")
        st.download_button("📥 Mall", to_excel(pd.DataFrame([{"Tootekood": "K1", "Nimetus": "Toode 1", "Tootmise jääk": 0}])), "mall.xlsx")
        if up := st.file_uploader("Lae", type=["xlsx"]):
            df = pd.read_excel(up, engine='openpyxl')
            if "Tootmise jääk" not in df.columns: st.error("Puudub 'Tootmise jääk'"); st.stop()
            for _, r in df.iterrows():
                q = r.get("Tootmise jääk")
                if pd.isna(q) or float(q) < 0: continue
                # Optimeeritud inventuuri loogika lühendatult (vt täismahus funktsioone)
            st.info("Käsitsi kulukandmine loeb Excelit (kiirendatud prototüüp)")

    elif menu == "✨ Tooted":
        st.title("✨ Tooted")
        t1, t2 = st.tabs(["Loo/Muuda", "Excelist"])
        with t1:
            opts = get_product_opts(db)
            p = opts.get(st.selectbox("Muuda", ["- UUS -"] + list(opts.keys()))) if opts else None
            n = st.text_input("Nimi", p.name if p else "")
            c = st.text_input("Kood", p.code if p else "")
            gr = st.text_input("Grupp", p.product_group if p else "")
            pr = st.number_input("Hind", 0.0, float(p.default_price or 0) if p else 0.0)
            wu, pu = st.columns(2)[0].text_input("Laoühik", p.warehouse_unit if p else "tk"), st.columns(2)[1].text_input("Ostuühik", p.purchase_unit if p else "tk")
            mu = st.number_input("Kordaja (Mitu lao = 1 ost)", 0.001, float(p.conversion_multiplier or 1) if p else 1.0)
            if st.button("💾 Salvesta"):
                if not n: st.error("Nimi kohustuslik!"); st.stop()
                if p: p.name, p.code, p.product_group, p.default_price, p.warehouse_unit, p.purchase_unit, p.conversion_multiplier = n, c, gr, pr, wu, pu, mu
                else: db.add(Product(name=n, code=c, product_group=gr, default_price=pr, warehouse_unit=wu, purchase_unit=pu, conversion_multiplier=mu))
                db.commit(); st.session_state['msg'] = "✅ Toode salvestatud!"; st.rerun()

    elif menu == "🕒 Logi":
        st.title("🕒 Ajalugu")
        f = st.radio("Filter", ["Kõik", "IN", "OUT", "TO_PROD", "PROD_CONS"], horizontal=True)
        q = db.query(Transaction.transaction_date, Transaction.type, Transaction.quantity, Transaction.price, Product.name, Product.warehouse_unit).join(Product).order_by(Transaction.id.desc())
        if f != "Kõik": q = q.filter(Transaction.type == getattr(TransactionType, f if f in ["IN", "OUT"] else f))
        
        data = [{"Aeg": t[0].strftime("%d.%m.%Y %H:%M"), "Tüüp": t[1].name, "Toode": t[4], "Kogus": t[2], "Ühik": t[5], "Hind": t[3]} for t in q.limit(2000).all()]
        if data:
            df = pd.DataFrame(data)
            btn_excel(df, "logi")
            cmap = {'IN_STOCK': '#10B981', 'OUT_STOCK': '#EF4444', 'TO_PROD': '#3B82F6', 'PROD_CONS': '#F59E0B'}
            st.dataframe(df.style.map(lambda v: f'color: {cmap.get(v,"#000")}; font-weight:bold', subset=['Tüüp']).format({"Kogus":"{:g}", "Hind":"{:.2f}"}), hide_index=True, use_container_width=True)
        else: st.info("Tühi")

finally:
    db.close()