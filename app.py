import streamlit as st
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import io
import re
import os

# ==========================================
# 1. NASTAVENIE STRÁNKY
# ==========================================
st.set_page_config(page_title="Pohoda XML Transform", page_icon="📝", layout="wide")

# ==========================================
# KONFIGURÁCIA A MENNÉ PRIESTORY POHODY
# ==========================================
MY_ICO = "57039607"
NS = {
    'dat': 'http://www.stormware.cz/schema/version_2/data.xsd',
    'inv': 'http://www.stormware.cz/schema/version_2/invoice.xsd',
    'typ': 'http://www.stormware.cz/schema/version_2/type.xsd',
    'rsp': 'http://www.stormware.cz/schema/version_2/response.xsd',
    'rdc': 'http://www.stormware.cz/schema/version_2/documentresponse.xsd',
    'ftr': 'http://www.stormware.cz/schema/version_2/filter.xsd',
    'lst': 'http://www.stormware.cz/schema/version_2/list.xsd'
}

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

# Inicializácia Session State
if 'transformed_xml' not in st.session_state:
    st.session_state.transformed_xml = None
if 'errors' not in st.session_state:
    st.session_state.errors = []
if 'vf_goods_map' not in st.session_state:
    st.session_state.vf_goods_map = {}

# ==========================================
# POMOCNÉ FUNKCIE
# ==========================================
def get_invoice_list(file_bytes):
    """Vytiahne zoznam faktúr a ich TEXTOV pre radu VF."""
    try:
        tree = ET.parse(file_bytes)
        root = tree.getroot()
        invoices = []
        for item in root.findall('dat:dataPackItem', NS):
            inv = item.find('inv:invoice', NS)
            if inv is None: continue
            header = inv.find('inv:invoiceHeader', NS)
            detail = inv.find('inv:invoiceDetail', NS)
            if header is None: continue
            
            num_el = header.find('inv:number/typ:numberRequested', NS)
            num_val = num_el.text.strip() if num_el is not None else "Neznáme"
            
            text_val = "Bez textu"
            if detail is not None:
                first_item = detail.find('inv:invoiceItem', NS)
                if first_item is not None:
                    it_text = first_item.find('inv:text', NS)
                    if it_text is not None and it_text.text:
                        text_val = it_text.text.strip()
            
            invoices.append({'id': num_val, 'text': text_val})
        return invoices
    except:
        return []

# ==========================================
# HLAVNÁ TRANSFORMAČNÁ FUNKCIA
# ==========================================
def transform_xml(file_bytes, rada, due_days, bank_ids, bank_acc, bank_code, payment_type, sym_const, goods_selection):
    tz_sk = ZoneInfo("Europe/Bratislava")
    now = datetime.now(tz_sk)
    month_map = {1:'JAN', 2:'FEB', 3:'MAR', 4:'APR', 5:'MAY', 6:'JUN', 
                 7:'JUL', 8:'AUG', 9:'SEP', 10:'OCT', 11:'NOV', 12:'DEC'}
    
    pack_id = f"{rada}_{now.day:02d}_{month_map[now.month]}_{now.year}_{now.hour:02d}_{now.minute:02d}"

    try:
        tree = ET.parse(file_bytes)
        root = tree.getroot()
    except ET.ParseError:
        return None, ["Chyba parsovania XML."], 0, pack_id, ""

    new_root = ET.Element(f'{{{NS["dat"]}}}dataPack', {
        'version': '2.0', 'id': pack_id, 'ico': MY_ICO, 'application': 'import', 'note': 'import'
    })

    invalid_msgs = []
    processed_count = 0
    first_suffix, last_suffix = None, None

    for i, item in enumerate(root.findall('dat:dataPackItem', NS), 1):
        old_invoice = item.find('inv:invoice', NS)
        if old_invoice is None: continue
        old_header = old_invoice.find('inv:invoiceHeader', NS)
        if old_header is None: continue
        
        # Číslo faktúry
        inv_num_el = old_header.find('inv:number/typ:numberRequested', NS)
        orig_num = inv_num_el.text.strip() if inv_num_el is not None else "0"
        
        match = re.search(r"(\d+)$", orig_num)
        if match:
            num_part = match.group(1)
            suffix = num_part.zfill(4 if rada != 'VF' else 3)
            inv_number = f"{orig_num[:match.start()]}{suffix}"
            if first_suffix is None: first_suffix = suffix
            last_suffix = suffix
        else:
            inv_number = orig_num

        # Partner & Krajina
        partner = old_header.find('.//typ:address', NS)
        country_code = "SK"
        if partner is not None:
            c_el = partner.find('typ:country/typ:ids', NS)
            if c_el is not None: country_code = c_el.text.strip().upper()
            
            # IČO Cleanup (Amazon link)
            ico_e = partner.find('typ:ico', NS)
            if ico_e is not None and ico_e.text:
                if any(x in ico_e.text.lower() for x in ["http", "www.", "amazon"]):
                    partner.remove(ico_e)
                    invalid_msgs.append(f"FA {inv_number}: Odstránené neplatné IČO (internetový odkaz)")
                    ico_e = None
            
            # Adresa Warning
            for f, n in [('name','Meno'), ('city','Mesto'), ('street','Ulica'), ('zip','PSČ')]:
                el = partner.find(f'typ:{f}', NS)
                if el is None or not el.text or not el.text.strip():
                    invalid_msgs.append(f"FA {inv_number}: Neúplná adresa (chýba {n})")

        # Nová faktúra
        item_id = f"{pack_id} ({i:03d})"
        new_item = ET.SubElement(new_root, f'{{{NS["dat"]}}}dataPackItem', {'version': '2.0', 'id': item_id})
        new_invoice = ET.SubElement(new_item, f'{{{NS["inv"]}}}invoice', {'version': '2.0', 'xmlns:inv': NS['inv']})
        new_header = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceHeader', {'xmlns:typ': NS['typ']})
        
        ET.SubElement(new_header, f'{{{NS["inv"]}}}invoiceType').text = 'issuedInvoice'
        new_num_node = ET.SubElement(new_header, f'{{{NS["inv"]}}}number')
        ET.SubElement(new_num_node, f'{{{NS["typ"]}}}numberRequested').text = inv_number
        
        old_sym = old_header.find('inv:symVar', NS)
        ET.SubElement(new_header, f'{{{NS["inv"]}}}symVar').text = old_sym.text if old_sym is not None else inv_number
        
        date_v = old_header.find('inv:date', NS).text
        try:
            date_due_v = (datetime.strptime(date_v, "%Y-%m-%d") + timedelta(days=due_days)).strftime("%Y-%m-%d")
        except:
            date_due_v = date_v
            
        for d in ['date', 'dateTax', 'dateAccounting']:
            ET.SubElement(new_header, f'{{{NS["inv"]}}}{d}').text = date_v
        ET.SubElement(new_header, f'{{{NS["inv"]}}}dateDue').text = date_due_v

        # Účtovanie
        acc = ET.SubElement(new_header, f'{{{NS["inv"]}}}accounting')
        cvat = ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationVAT')
        
        if rada == 'VF':
            # BEZPEČNÝ PRÍSTUP K MAPOVALU (zabraňuje KeyError)
            is_goods = goods_selection.get(orig_num, False)
            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tovaru' if is_goods else 'pred.služ'
            ET.SubElement(cvat, f'{{{NS["typ"]}}}ids').text = 'UN' if country_code == 'SK' else 'UD'
        else:
            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tovaru' if rada == 'VFB' else 'pred.tov.DE'
            ET.SubElement(cvat, f'{{{NS["typ"]}}}ids').text = 'UN'

        ET.SubElement(cvat, f'{{{NS["typ"]}}}classificationVATType').text = 'nonSubsume'
        ET.SubElement(ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationKVDPH'), f'{{{NS["typ"]}}}ids').text = 'KN'

        old_txt = old_header.find('inv:text', NS)
        ET.SubElement(new_header, f'{{{NS["inv"]}}}text').text = old_txt.text if old_txt is not None else "Faktúra"

        if partner is not None:
            for t in ['company', 'ico', 'dic', 'icDph']:
                e = partner.find(f'typ:{t}', NS)
                if e is not None and (not e.text or not e.text.strip()): partner.remove(e)
            partner.set('linkToAddress', 'true' if partner.find('typ:ico', NS) is not None else 'false')
            ET.SubElement(new_header, f'{{{NS["inv"]}}}partnerIdentity').append(partner)

        my_addr = ET.SubElement(ET.SubElement(new_header, f'{{{NS["inv"]}}}myIdentity'), f'{{{NS["typ"]}}}address')
        ET.SubElement(my_addr, f'{{{NS["typ"]}}}company').text = 'EPPO BRANDS s. r. o.'
        ET.SubElement(my_addr, f'{{{NS["typ"]}}}city').text = 'Zvolen'
        ET.SubElement(my_addr, f'{{{NS["typ"]}}}street').text = 'Tulská'
        ET.SubElement(my_addr, f'{{{NS["typ"]}}}number').text = '9386/6B'
        ET.SubElement(my_addr, f'{{{NS["typ"]}}}zip').text = '960 01'
        ET.SubElement(my_addr, f'{{{NS["typ"]}}}ico').text = '57039607'
        ET.SubElement(my_addr, f'{{{NS["typ"]}}}dic').text = '2122546481'
        ET.SubElement(my_addr, f'{{{NS["typ"]}}}icDph').text = 'SK2122546481'

        pt = ET.SubElement(new_header, f'{{{NS["inv"]}}}paymentType')
        ET.SubElement(pt, f'{{{NS["typ"]}}}ids').text = payment_type
        ET.SubElement(pt, f'{{{NS["typ"]}}}paymentType').text = 'draft'
        bnk = ET.SubElement(new_header, f'{{{NS["inv"]}}}account')
        ET.SubElement(bnk, f'{{{NS["typ"]}}}ids').text = bank_ids
        ET.SubElement(bnk, f'{{{NS["typ"]}}}accountNo').text = bank_acc
        ET.SubElement(bnk, f'{{{NS["typ"]}}}bankCode').text = bank_code
        ET.SubElement(new_header, f'{{{NS["inv"]}}}symConst').text = sym_const

        # Likvidácia a Sumáre
        old_sum_node = old_invoice.find('inv:invoiceSummary', NS)
        h_sum, f_sum, is_f = 0.0, 0.0, False
        c_ids, c_rate = "EUR", 1.0

        if old_sum_node is not None:
            fc_e = old_sum_node.find('inv:foreignCurrency', NS)
            if fc_e is not None:
                is_f = True
                c_ids = fc_e.find('typ:currency/typ:ids', NS).text
                c_rate = float(fc_e.find('typ:rate', NS).text)
                ps = fc_e.find('typ:priceSum', NS)
                if ps is None: ps = fc_e.find('typ:priceNone', NS)
                f_sum = float(ps.text)
                h_sum = round(f_sum * c_rate, 2)
            else:
                hc_e = old_sum_node.find('inv:homeCurrency', NS)
                ps = hc_e.find('typ:priceSum', NS)
                if ps is None: ps = hc_e.find('typ:priceNone', NS)
                h_sum = float(ps.text)

        liq = ET.SubElement(new_header, f'{{{NS["inv"]}}}liquidation')
        ET.SubElement(liq, f'{{{NS["typ"]}}}amountHome').text = f"{h_sum:.2f}"
        if is_f: ET.SubElement(liq, f'{{{NS["typ"]}}}amountForeign').text = f"{f_sum:.2f}"

        ET.SubElement(new_header, f'{{{NS["inv"]}}}lock2').text = 'false'
        ET.SubElement(new_header, f'{{{NS["inv"]}}}markRecord').text = 'false'

        # Položky
        old_detail = old_invoice.find('inv:invoiceDetail', NS)
        new_detail = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceDetail')
        
        if rada == 'VFD':
            val_to_split = f_sum if is_f else h_sum
            f_vat = round(val_to_split - (val_to_split / 1.19), 2)
            f_base = round(val_to_split - f_vat, 2)
            for t, v, a_id in [('Predaj tovaru - Nemecko', f_base, 'pred.tov.DE'), ('Predaj tovaru - Nemecko DPH 19%', f_vat, 'DPH.tov.DE')]:
                it = ET.SubElement(new_detail, f'{{{NS["inv"]}}}invoiceItem')
                ET.SubElement(it, f'{{{NS["inv"]}}}text').text = t
                ET.SubElement(it, f'{{{NS["inv"]}}}quantity').text = '1.0'
                ET.SubElement(it, f'{{{NS["inv"]}}}rateVAT').text = 'none'
                curr = ET.SubElement(it, f'{{{NS["inv"]}}}{"foreignCurrency" if is_f else "homeCurrency"}')
                ET.SubElement(curr, f'{{{NS["typ"]}}}unitPrice').text = f"{v:.2f}"
                ET.SubElement(curr, f'{{{NS["typ"]}}}price').text = f"{v:.2f}"
                ET.SubElement(curr, f'{{{NS["typ"]}}}priceSum').text = f"{v:.2f}"
                ET.SubElement(ET.SubElement(it, f'{{{NS["inv"]}}}accounting'), f'{{{NS["typ"]}}}ids').text = a_id
        else:
            if old_detail is not None:
                for old_it in old_detail.findall('inv:invoiceItem', NS):
                    new_it = ET.SubElement(new_detail, f'{{{NS["inv"]}}}invoiceItem')
                    for tn in ['text', 'quantity', 'unit', 'payVAT', 'rateVAT']:
                        v = old_it.find(f'inv:{tn}', NS)
                        if v is not None: ET.SubElement(new_it, f'{{{NS["inv"]}}}{tn}').text = v.text
                    
                    ctype = 'foreignCurrency' if is_f else 'homeCurrency'
                    oc = old_it.find(f'inv:{ctype}', NS)
                    if oc is not None:
                        nc = ET.SubElement(new_it, f'{{{NS["inv"]}}}{ctype}')
                        for pt in ['unitPrice', 'price', 'priceVAT', 'priceSum']:
                            pv = oc.find(f'typ:{pt}', NS)
                            if pv is not None: ET.SubElement(nc, f'{{{NS["typ"]}}}{pt}').text = pv.text
                    
                    if rada == 'VF':
                        is_goods = goods_selection.get(orig_num, False)
                        ET.SubElement(ET.SubElement(new_it, f'{{{NS["inv"]}}}accounting'), f'{{{NS["typ"]}}}ids').text = 'pred.tovaru' if is_goods else 'pred.služ'

        # Sumár
        s_attrs = {'xmlns:rsp': NS['rsp'], 'xmlns:rdc': NS['rdc'], 'xmlns:typ': NS['typ'], 'xmlns:ftr': NS['ftr'], 'xmlns:lst': NS['lst']}
        ns_sum = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceSummary', s_attrs)
        ET.SubElement(ns_sum, f'{{{NS["inv"]}}}roundingDocument').text = 'none'
        ET.SubElement(ns_sum, f'{{{NS["inv"]}}}roundingVAT').text = 'none'
        
        hc = ET.SubElement(ns_sum, f'{{{NS["inv"]}}}homeCurrency')
        ET.SubElement(hc, f'{{{NS["typ"]}}}priceNone').text = f"{h_sum:.2f}"
        for tag in ['priceLow', 'priceLowVAT', 'priceLowSum', 'priceHigh', 'priceHighVAT', 'priceHighSum', 'price3', 'price3VAT', 'price3Sum']:
            ET.SubElement(hc, f'{{{NS["typ"]}}}{tag}').text = '0'
        ET.SubElement(ET.SubElement(hc, f'{{{NS["typ"]}}}round'), f'{{{NS["typ"]}}}priceRound').text = '0'

        if is_f:
            fc = ET.SubElement(ns_sum, f'{{{NS["inv"]}}}foreignCurrency')
            tc = ET.SubElement(fc, f'{{{NS["typ"]}}}currency')
            ET.SubElement(tc, f'{{{NS["typ"]}}}ids').text = c_ids
            ET.SubElement(fc, f'{{{NS["typ"]}}}rate').text = str(c_rate)
            ET.SubElement(fc, f'{{{NS["typ"]}}}amount').text = '1'
            ET.SubElement(fc, f'{{{NS["typ"]}}}priceNone').text = f"{f_sum:.2f}"
            ET.SubElement(fc, f'{{{NS["typ"]}}}priceSum').text = f"{f_sum:.2f}"
            ET.SubElement(ET.SubElement(fc, f'{{{NS["typ"]}}}round'), f'{{{NS["typ"]}}}priceRound').text = '0'
        
        processed_count += 1

    out_name = f"{rada}{first_suffix}-{last_suffix}_{now.day:02d}_{month_map[now.month]}_{now.year}.xml"
    out_bio = io.BytesIO()
    ET.ElementTree(new_root).write(out_bio, encoding='Windows-1250', xml_declaration=True)
    return out_bio.getvalue(), invalid_msgs, processed_count, out_name

# ==========================================
# STREAMLIT UI
# ==========================================
with st.sidebar:
    if os.path.exists("eppobrands.png"):
        st.image("eppobrands.png", width=150)
    
    st.header("⚙️ Nastavenia")
    rada_sel = st.radio("Dokladová rada:", ('VFB', 'VFD', 'VF'))
    st.markdown("---")
    st.header("🏦 Bankové údaje")
    b_ids = st.text_input("Skratka banky", "TB")
    b_acc = st.text_input("Číslo účtu", "2949268117")
    b_code = st.text_input("Kód banky", "1100")
    p_type = st.text_input("Forma úhrady", "Príkazom")
    s_const = st.text_input("Konštantný symbol", "0308")
    d_days = st.number_input("Splatnosť (dni)", 7)

st.title("📦 XML Transformátor (Multi-Source -> Pohoda)")
u_file = st.file_uploader("Nahrajte zdrojové XML", type=["xml"])

if u_file is not None:
    content = u_file.getvalue()
    
    # ŠPECIÁLNA SEKCIA LEN PRE RADU VF
    if rada_sel == 'VF':
        st.subheader("📋 Klasifikácia faktúr rady VF")
        st.info("Označte faktúry s TOVAROM. Neoznačené budú mať predkontáciu 'pred.služ'.")
        inv_list = get_invoice_list(io.BytesIO(content))
        for entry in inv_list:
            # Ukladáme do mapy s bezpečným kľúčom
            st.session_state.vf_goods_map[entry['id']] = st.checkbox(
                f"FA {entry['id']} | {entry['text']}", key=f"check_{entry['id']}"
            )
        st.divider()

    if st.button("🚀 Spustiť transformáciu", type="primary"):
        with st.spinner('Spracovávam...'):
            # Vždy posielame mapu, transform_xml ju pre VFB/VFD bezpečne odignoruje
            xml_data, errors, count, out_fn = transform_xml(
                io.BytesIO(content), rada_sel, d_days, b_ids, b_acc, b_code, p_type, s_const, 
                st.session_state.vf_goods_map
            )
            st.session_state.transformed_xml = xml_data
            st.session_state.errors = errors
            st.session_state.out_filename = out_fn

if st.session_state.transformed_xml is not None:
    st.divider()
    st.success(f"✅ Transformácia úspešná.")
    if st.session_state.errors:
        st.warning("⚠️ Upozornenia:")
        for err in st.session_state.errors: st.write(f"- {err}")
    st.download_button(label="💾 Stiahnuť XML pre Pohodu", data=st.session_state.transformed_xml, file_name=st.session_state.out_filename, mime="application/xml")
