import streamlit as st

import xml.etree.ElementTree as ET

from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

import io

import re


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


# Registrácia priestorov pre korektné prefixy v XML

for prefix, uri in NS.items():

    ET.register_namespace(prefix, uri)


# Inicializácia Session State pre Streamlit

if 'transformed_xml' not in st.session_state:

    st.session_state.transformed_xml = None

if 'errors' not in st.session_state:

    st.session_state.errors = []

if 'count' not in st.session_state:

    st.session_state.count = 0

if 'out_filename' not in st.session_state:

    st.session_state.out_filename = ""


# ==========================================

# HLAVNÁ TRANSFORMAČNÁ FUNKCIA

# ==========================================

def transform_xml(file_bytes, rada, due_days, bank_ids, bank_acc, bank_code, payment_type, sym_const):

    tz_sk = ZoneInfo("Europe/Bratislava")

    now = datetime.now(tz_sk)

    month_map = {1:'JAN', 2:'FEB', 3:'MAR', 4:'APR', 5:'MAY', 6:'JUN', 

                 7:'JUL', 8:'AUG', 9:'SEP', 10:'OCT', 11:'NOV', 12:'DEC'}

    

    pack_id = f"{rada}_{now.day:02d}_{month_map[now.month]}_{now.year}_{now.hour:02d}_{now.minute:02d}"


    try:

        tree = ET.parse(file_bytes)

        root = tree.getroot()

    except ET.ParseError:

        return None, ["Chyba parsovania: Súbor nie je platný XML."], 0, pack_id, ""


    new_root = ET.Element(f'{{{NS["dat"]}}}dataPack', {

        'version': '2.0', 'id': pack_id, 'ico': MY_ICO, 'application': 'import', 'note': 'import'

    })


    invalid_msgs = []

    processed_count = 0

    first_inv_suffix = None

    last_inv_suffix = None


    for i, item in enumerate(root.findall('dat:dataPackItem', NS), 1):

        old_invoice = item.find('inv:invoice', NS)

        if old_invoice is None: continue

        old_header = old_invoice.find('inv:invoiceHeader', NS)

        if old_header is None: continue

        

        # 1. Číslo faktúry (4 cifry)

        inv_number_elem = old_header.find('inv:number/typ:numberRequested', NS)

        current_suffix = ""

        inv_number = "Neznáme"

        if inv_number_elem is not None and inv_number_elem.text:

            orig_num = inv_number_elem.text.strip()

            match = re.match(r"^(.*?)(\d+)$", orig_num)

            if match:

                prefix_val = match.group(1)

                num_part = match.group(2)

                current_suffix = num_part.zfill(4)

                inv_number = f"{prefix_val}{current_suffix}" 

                inv_number_elem.text = inv_number 

                if first_inv_suffix is None: first_inv_suffix = current_suffix

                last_inv_suffix = current_suffix

            else:

                inv_number = orig_num


        # 2. Kontrola Partnera (IČO, Amazon link, Adresa)

        partner = old_header.find('.//typ:address', NS)

        if partner is not None:

            ico_e = partner.find('typ:ico', NS)

            if ico_e is not None and ico_e.text:

                ico_text_lower = ico_e.text.strip().lower()

                if "http" in ico_text_lower or "www." in ico_text_lower or "amazon" in ico_text_lower:

                    partner.remove(ico_e)

                    invalid_msgs.append(f"FA {inv_number}: Odstránené neplatné IČO (internetový odkaz)")

                    ico_e = None

            

            comp = partner.find('typ:company', NS)

            if comp is not None and comp.text and comp.text.strip():

                if ico_e is None or not ico_e.text or not ico_e.text.strip():

                    invalid_msgs.append(f"FA {inv_number} (Firma: {comp.text.strip()})")


            missing_addr = []

            for addr_f in ['name', 'city', 'street', 'zip']:

                e = partner.find(f'typ:{addr_f}', NS)

                if e is None or not e.text or not e.text.strip():

                    missing_addr.append(addr_f)

            if missing_addr:

                transl = {'name': 'Meno', 'city': 'Mesto', 'street': 'Ulica', 'zip': 'PSČ'}

                miss_sk = [transl.get(x, x) for x in missing_addr]

                invalid_msgs.append(f"FA {inv_number}: Neúplná adresa (chýba: {', '.join(miss_sk)})")


        # 3. Štruktúra dataPackItem

        item_id = f"{pack_id} ({i:03d})"

        new_item = ET.SubElement(new_root, f'{{{NS["dat"]}}}dataPackItem', {'version': '2.0', 'id': item_id})

        new_invoice = ET.SubElement(new_item, f'{{{NS["inv"]}}}invoice', {'version': '2.0', 'xmlns:inv': NS['inv']})

        new_header = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceHeader', {'xmlns:typ': NS['typ']})

        

        ET.SubElement(new_header, f'{{{NS["inv"]}}}invoiceType').text = 'issuedInvoice'

        new_header.append(old_header.find('inv:number', NS))

        new_header.append(old_header.find('inv:symVar', NS))

        

        date_val = old_header.find('inv:date', NS).text

        try:

            date_obj = datetime.strptime(date_val, "%Y-%m-%d")

            date_due_val = (date_obj + timedelta(days=due_days)).strftime("%Y-%m-%d")

        except:

            date_due_val = date_val

            

        ET.SubElement(new_header, f'{{{NS["inv"]}}}date').text = date_val

        ET.SubElement(new_header, f'{{{NS["inv"]}}}dateTax').text = date_val

        ET.SubElement(new_header, f'{{{NS["inv"]}}}dateAccounting').text = date_val

        ET.SubElement(new_header, f'{{{NS["inv"]}}}dateDue').text = date_due_val


        # Účtovanie a DPH

        acc = ET.SubElement(new_header, f'{{{NS["inv"]}}}accounting')

        cvat = ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationVAT')

        if rada == 'VFB':

            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tovaru'

        else:

            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tov.DE'

            

        ET.SubElement(cvat, f'{{{NS["typ"]}}}ids').text = 'UN'

        ET.SubElement(cvat, f'{{{NS["typ"]}}}classificationVATType').text = 'nonSubsume'


        ckv = ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationKVDPH')

        ET.SubElement(ckv, f'{{{NS["typ"]}}}ids').text = 'KN'


        text_v = 'Tržby z predaja tovaru' if rada == 'VFB' else 'Predaj tovaru - Nemecko'

        ET.SubElement(new_header, f'{{{NS["inv"]}}}text').text = text_v


        # Partner identity

        if partner is not None:

            has_ico_real = False

            for t in ['company', 'ico', 'dic', 'icDph']:

                e = partner.find(f'typ:{t}', NS)

                if e is not None:

                    if not e.text or not e.text.strip(): partner.remove(e)

                    elif t == 'ico': has_ico_real = True

            partner.set('linkToAddress', 'true' if has_ico_real else 'false')

            ET.SubElement(new_header, f'{{{NS["inv"]}}}partnerIdentity').append(partner)


        # Identita

        my_id = ET.SubElement(ET.SubElement(new_header, f'{{{NS["inv"]}}}myIdentity'), f'{{{NS["typ"]}}}address')

        ET.SubElement(my_id, f'{{{NS["typ"]}}}company').text = 'EPPO BRANDS s. r. o.'

        ET.SubElement(my_id, f'{{{NS["typ"]}}}city').text = 'Zvolen'

        ET.SubElement(my_id, f'{{{NS["typ"]}}}street').text = 'Tulská'

        ET.SubElement(my_id, f'{{{NS["typ"]}}}number').text = '9386/6B'

        ET.SubElement(my_id, f'{{{NS["typ"]}}}zip').text = '960 01'

        ET.SubElement(my_id, f'{{{NS["typ"]}}}ico').text = '57039607'

        ET.SubElement(my_id, f'{{{NS["typ"]}}}dic').text = '2122546481'

        ET.SubElement(my_id, f'{{{NS["typ"]}}}icDph').text = 'SK2122546481'


        # Banka a platba

        pt_n = ET.SubElement(new_header, f'{{{NS["inv"]}}}paymentType')

        ET.SubElement(pt_n, f'{{{NS["typ"]}}}ids').text = payment_type

        ET.SubElement(pt_n, f'{{{NS["typ"]}}}paymentType').text = 'draft'

        bnk = ET.SubElement(new_header, f'{{{NS["inv"]}}}account')

        ET.SubElement(bnk, f'{{{NS["typ"]}}}ids').text = bank_ids

        ET.SubElement(bnk, f'{{{NS["typ"]}}}accountNo').text = bank_acc

        ET.SubElement(bnk, f'{{{NS["typ"]}}}bankCode').text = bank_code

        ET.SubElement(new_header, f'{{{NS["inv"]}}}symConst').text = sym_const


        # 4. Likvidácia a Sumáre

        old_sum = old_invoice.find('inv:invoiceSummary', NS)

        h_sum, f_sum = 0.0, 0.0

        is_f = False

        c_ids, c_rate = "EUR", 1.0


        if old_sum is not None:

            fc_e = old_sum.find('inv:foreignCurrency', NS)

            if fc_e is not None:

                is_f = True

                c_ids = fc_e.find('typ:currency/typ:ids', NS).text

                c_rate = float(fc_e.find('typ:rate', NS).text)

                f_sum = float(old_invoice.find('.//inv:foreignCurrency/typ:priceSum', NS).text)

                h_sum = round(f_sum * c_rate, 2)

            else:

                h_sum = float(old_invoice.find('.//inv:homeCurrency/typ:priceSum', NS).text)


        liq = ET.SubElement(new_header, f'{{{NS["inv"]}}}liquidation')

        ET.SubElement(liq, f'{{{NS["typ"]}}}amountHome').text = f"{h_sum:.2f}"

        if is_f: ET.SubElement(liq, f'{{{NS["typ"]}}}amountForeign').text = f"{f_sum:.2f}"


        ET.SubElement(new_header, f'{{{NS["inv"]}}}lock2').text = 'false'

        ET.SubElement(new_header, f'{{{NS["inv"]}}}markRecord').text = 'false'


        # Položky pre VFD (Rozpad podľa nového vzoru)

        if rada == 'VFD':

            det = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceDetail')

            val_to_split = f_sum if is_f else h_sum

            f_vat_part = round(val_to_split - (val_to_split / 1.19), 2)

            f_base_part = round(val_to_split - f_vat_part, 2)


            vfd_configs = [

                ('Predaj tovaru - Nemecko', f_base_part, 'pred.tov.DE'),

                ('Predaj tovaru - Nemecko DPH 19%', f_vat_part, 'DPH.tov.DE')

            ]


            for t, val, acc_id in vfd_configs:

                it = ET.SubElement(det, f'{{{NS["inv"]}}}invoiceItem')

                ET.SubElement(it, f'{{{NS["inv"]}}}text').text = t

                ET.SubElement(it, f'{{{NS["inv"]}}}quantity').text = '1.0'

                ET.SubElement(it, f'{{{NS["inv"]}}}coefficient').text = '1.0'

                ET.SubElement(it, f'{{{NS["inv"]}}}payVAT').text = 'false'

                ET.SubElement(it, f'{{{NS["inv"]}}}rateVAT').text = 'none'

                ET.SubElement(it, f'{{{NS["inv"]}}}discountPercentage').text = '0.0'

                

                curr_node_name = 'foreignCurrency' if is_f else 'homeCurrency'

                curr = ET.SubElement(it, f'{{{NS["inv"]}}}{curr_node_name}')

                ET.SubElement(curr, f'{{{NS["typ"]}}}unitPrice').text = f"{val:.2f}"

                ET.SubElement(curr, f'{{{NS["typ"]}}}price').text = f"{val:.2f}"

                ET.SubElement(curr, f'{{{NS["typ"]}}}priceVAT').text = '0'

                ET.SubElement(curr, f'{{{NS["typ"]}}}priceSum').text = f"{val:.2f}"

                

                ET.SubElement(ET.SubElement(it, f'{{{NS["inv"]}}}accounting'), f'{{{NS["typ"]}}}ids').text = acc_id

                ET.SubElement(it, f'{{{NS["inv"]}}}PDP').text = 'false'


        # 6. Detailný sumár faktúry (podľa vzoru)

        summary_attrs = {

            'xmlns:rsp': NS['rsp'], 'xmlns:rdc': NS['rdc'], 'xmlns:typ': NS['typ'],

            'xmlns:ftr': NS['ftr'], 'xmlns:lst': NS['lst']

        }

        ns_sum = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceSummary', summary_attrs)

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

            for tag in ['priceLow', 'priceLowVAT', 'priceLowSum', 'priceHigh', 'priceHighVAT', 'priceHighSum', 'price3', 'price3VAT', 'price3Sum']:

                ET.SubElement(fc, f'{{{NS["typ"]}}}{tag}').text = '0'

            ET.SubElement(fc, f'{{{NS["typ"]}}}priceSum').text = f"{f_sum:.2f}"

            ET.SubElement(ET.SubElement(fc, f'{{{NS["typ"]}}}round'), f'{{{NS["typ"]}}}priceRound').text = '0'

        

        processed_count += 1


    range_txt = f"{first_inv_suffix}-{last_inv_suffix}" if first_inv_suffix else ""

    ts = f"{now.day:02d}_{month_map[now.month]}_{now.year}_{now.hour:02d}_{now.minute:02d}"

    out_name = f"{rada}{range_txt}_{ts}.xml"


    out_bio = io.BytesIO()

    ET.ElementTree(new_root).write(out_bio, encoding='Windows-1250', xml_declaration=True)

    return out_bio.getvalue(), invalid_msgs, processed_count, pack_id, out_name


# ==========================================

# STREAMLIT UI

# ==========================================

st.set_page_config(page_title="Pohoda XML Transform", page_icon="📝", layout="wide")

st.title("📦 Base.com -> Pohoda XML Transformátor")


with st.sidebar:

    st.header("⚙️ Nastavenia")

    rada_sel = st.radio("Dokladová rada:", ('VFB', 'VFD'))

    st.markdown("---")

    st.header("🏦 Bankové údaje")

    b_ids = st.text_input("Skratka banky", "TB")

    b_acc = st.text_input("Číslo účtu", "2949268117")

    b_code = st.text_input("Kód banky", "1100")

    p_type = st.text_input("Forma úhrady", "Príkazom")

    s_const = st.text_input("Konštantný symbol", "0308")

    d_days = st.number_input("Splatnosť (dni)", 7)


u_file = st.file_uploader("Nahrajte XML z Base.com", type=["xml"])


if u_file is not None:

    if st.button("🚀 Spustiť transformáciu", type="primary"):

        with st.spinner('Spracovávam...'):

            xml_data, errors, count, pack_id, out_fn = transform_xml(

                io.BytesIO(u_file.getvalue()), rada_sel, d_days, b_ids, b_acc, b_code, p_type, s_const

            )

            st.session_state.transformed_xml, st.session_state.errors = xml_data, errors

            st.session_state.count, st.session_state.out_filename = count, out_fn


if st.session_state.transformed_xml is not None:

    st.divider()

    st.success(f"✅ Spracovaných {st.session_state.count} faktúr.")

    if st.session_state.errors:

        st.warning("⚠️ Skontrolujte tieto upozornenia:")

        for err in st.session_state.errors: st.write(f"- {err}")

    st.download_button(label="💾 Stiahnuť upravené XML", data=st.session_state.transformed_xml, file_name=st.session_state.out_filename, mime="application/xml") 
