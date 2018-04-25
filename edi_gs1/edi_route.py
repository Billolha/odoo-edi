# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution, third party addon
#    Copyright (C) 2004-2016 Vertel AB (<http://vertel.se>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
from openerp import models, fields, api, _
from edifact.helpers import separate_segments, separate_components
import base64
import codecs
from datetime import datetime
import sys

import logging
_logger = logging.getLogger(__name__)

class edi_envelope(models.Model):
    _inherit = 'edi.envelope'

    route_type = fields.Selection(selection_add=[('esap20','ESAP 20')])

    @api.model
    def _get_edi_type_id(self, edi_type):
        t = self.env['edi.message.type'].search([('name', '=', edi_type)])
        if not t:
            t = self.env.ref('edi_route.edi_message_type_plain')
        return t.id

    @api.multi
    def _fold(self, route): # Folds messages in an envelope
        envelope = super(edi_envelope, self)._fold(route)
        if self.route_type == 'esap20':
            interchange_control_ref = self.application or ''
            dt = fields.Datetime.from_string(self.date)
            date = dt.strftime("%y%m%d")
            time = dt.strftime("%H%M")
            if not self.ref:
                self.ref = self.name
            UNA = "UNA:+.? '"
            UNB = "UNB+UNOC:3+{sender}:{qualifier}+{receiver}:14+{date}:{time}+{name}++{ref}'".format(
                sender=envelope.sender.gs1_gln, receiver=envelope.recipient.gs1_gln,
                date=date, time=time, name=self.ref, ref=interchange_control_ref,
                qualifier='ZZ' if self.route_id.test_mode else '14')
            body = ''.join([base64.b64decode(m.body) for m in envelope.edi_message_ids])
            UNZ = "UNZ+%s+%s'" % (len(envelope.edi_message_ids),self.name)
            msg = self.env['edi.message']
            envelope.body = base64.b64encode(msg._gs1_encode_msg(UNA + UNB) + body + msg._gs1_encode_msg(UNZ))
        return envelope

    @api.one
    def _split(self):
        if self.route_type == 'esap20':
            message = ''
            #~ _logger.warn('body: %s' % base64.b64decode(self.body))
            msg_count = 0
            msgs = []
            segment_check = {}
            msg_dict = {}
            for segment_string in separate_segments(base64.b64decode(self.body)):
                segment = separate_components(segment_string)
                if segment[0] == 'UNB':
                    segment_check['UNB'] = True
                    self.ref = segment[5]
                    self.sender = self._get_partner(segment[2],'sender')
                    self.recipient = self._get_partner(segment[3],'recipent')
                    date = segment[4][0]
                    time = segment[4][1]
                    self.date = "20%s-%s-%s %s:%s:00" % (date[:2], date[2:4], date[4:], time[:2], time[2:])
                    if len(segment) > 7:
                        self.application = segment[7]
                elif segment[0] == 'UNH':
                    edi_type = segment[2][0]
                    msg_name = segment[1]
                    message = segment_string
                    segment_count = 1
                elif segment[0] == 'UNT':
                    #skapa message
                    if segment_count + 1 != int(segment[1]):
                        raise TypeError('Wrong number of segments! %s %s' % (segment_count, segment), segment)
                    message += segment_string
                    msgs.append({
                        'name': msg_name,
                        'envelope_id': self.id,
                        'body': base64.b64encode(message),
                        'edi_type': self._get_edi_type_id(edi_type),
                        'sender': self.sender.id,
                        'recipient': self.recipient.id,
                        'route_type': self.route_id.route_type,
                        'route_id': self.route_id.id,
                    })
                    message = ''
                    msg_count += 1
                elif segment[0] == 'UNZ':
                    segment_check['UNZ'] = True
                    if msg_count != int(segment[1]):
                        raise TypeError('Wrong message count!')
                elif message:
                    message += segment_string
                    segment_count += 1

            if not segment_check.get('UNB'):
                raise TypeError('UNB segment missing!')
            elif not segment_check.get('UNZ'):
                raise TypeError('UNZ segment missing!')
            #~ _logger.warn('msgs to create: %s' % msgs)
            for msg_dict in msgs:
                #Large potential for transaction lock when unpacking messages.
                #Commit for every message and rollback on error.
                #Every working message is unpacked.
                try:
                    #self._cr.commit()
                    msg = self.env['edi.message'].create(msg_dict)
                    #~ _logger.warn('msg created: %s' % msg)
                    msg.unpack()
                except Exception as e:
                    #self._cr.rollback()
                    self.route_id.log("Error when reading message '%s' of envelope '%s'" % (msg_dict.get('name'), self.name), sys.exc_info())
                    self.state = 'canceled'
        super(edi_envelope, self)._split()

    @api.model
    def _get_partner(self, l, part_type):
        _logger.info('get partner %s (%s)' % (l, part_type))
        if l[1] == '14' or (l[1] == 'ZZ' and self.route_id.test_mode):
            partner = self.env['res.partner'].search([('gs1_gln', '=', l[0])])
            if len(partner) == 1:
                return partner
        raise ValueError("Unknown part %s" % (len(l) > 0 and l[0] or "[EMPTY LIST!]"), l, part_type)

    def edifact_read(self):
        """
            Creates an attachement with the envelope in readable form
        """
        if self and self.body:
            self.env['ir.attachment'].create({
                    'name': self.name,
                    'type': 'binary',
                    'datas': base64.b64encode(base64.b64decode(self.body).replace("'","'\n")),
                    'res_model': self._name,
                    'res_id': self.id,
                })


class edi_route(models.Model):
    _inherit = 'edi.route'

    route_type = fields.Selection(selection_add=[('esap20','ESAP 20')])


def _escape_string(s):
    if isinstance(s, basestring):
        return s.replace('?', '??').replace('+', '?+').replace(':', '?:').replace("'", "?'") #.replace('\n', '') needed?
    return s



class edi_message(models.Model):
    _inherit='edi.message'

    route_type = fields.Selection(selection_add=[('esap20', 'ESAP 20')])
    nad_dp = fields.Many2one(comodel_name='res.partner',help="Delivery party, party to which goods should be delivered, if not identical with consignee.")
    nad_ito = fields.Many2one(comodel_name='res.partner',help="Invoice party, party to which bill should be invoiced, if not identical with consignee.")

    _seg_count = 0
    _lin_count = 0

    @api.multi
    def _gs1_get_components(self):
        self.ensure_one()
        if self.body:
            segments = []
            for segment in separate_segments(self._gs1_decode_msg(base64.b64decode(self.body))):
                segments.append(separate_components(segment))
            return segments

    @api.model
    def _gs1_encode_msg(self, msg):
        """Encode a string in the format specified by the EDIFACT standard (iso8859-1)."""
        return codecs.encode(msg, 'iso8859-1', 'ignore')

    @api.model
    def _gs1_decode_msg(self, msg):
        """Decode a string from the format specified by the EDIFACT standard (iso8859-1)."""
        return unicode(msg, 'iso8859-1')

    def _get_contract(self, ref):
        contract = self.env['account.analytic.account'].search([('code', '=', ref)])
        if len(contract) > 1:
            contract = contract[0]
        _logger.info('_get_contract %s %s' % (contract, contract.id))
        if contract:
            return contract.id

    def edifact_read(self):
        self.env['ir.attachment'].create({
                'name': self.edi_type,
                'type': 'binary',
                'datas': base64.b64encode(base64.b64decode(self.body).replace("'","'\n")),
                'res_model': 'edi.message',
                'res_id': self.id,
            })
    
    def ALI(self, reason):
        self._seg_count += 1
        return "ALI+++%s'" % reason
    
    def UNH(self,edi_type=False, version='D', release='96A', ass_code='EAN005'):
        self._seg_count += 1
        if not edi_type:
            edi_type = self.edi_type
        return "UNH+{ref_no}+{msg_type}:{version}:{release}:UN:{ass_code}'".format(ref_no=self.name,msg_type=edi_type,version=version,release=release, ass_code=ass_code)

    def BGM(self, doc_code=False, doc_no=False, status=''):
        #TODO: look up test mode on route and add to BGM

        # Beginning of message
        # doc_code = Order, Document/message by means of which a buyer initiates a transaction with a seller involving the supply of goods or services as specified, according to conditions set out in an offer, or otherwise known to the buyer.
        # BGM+220::9+20120215150105472'
        # doc_code 231 Purchase order response, Response to an purchase order already received.
        # BGM+231::9+201101311720471+4'
        # doc_code 351 Despatch advice, Document/message by means of which the seller or consignor informs the consignee about the despatch of goods.
        # BGM+351+SO069412+9'
        self._seg_count += 1
        if not doc_code or not doc_no:
            raise Warning("edi_message.BGM(doc_code=%s, doc_no=%s, status=%s): Missing required arguments." % (doc_code, doc_no, status))
        if doc_code == 231: # Resp agency = EAN/GS1 (9), Message function code = Change (4)
            return "BGM+231+{doc_no}+{status}'".format(doc_no=_escape_string(doc_no), status = status)
        return "BGM+{doc_code}+{doc_no}+9'".format(doc_code=doc_code, doc_no=_escape_string(doc_no))

    def CPS(self, level):
        """To identify the sequence in which physical packing is presented in the consignment,
        and optionally to identify the hierarchical relationship between packing layers."""
        self._seg_count += 1
        return "CPS+%s'" % (level)

    def CNT(self, qualifier, value):
        self._seg_count += 1
        if int(value) == value:
            value = int(value)
        return "CNT+%s:%s'" % (qualifier, value)

    def DTM(self, func_code, dt=False, format=102):
        self._seg_count += 1
        #2   Delivery date/time, requested
        #11  Despatch date and or time - (2170) Date/time on which the goods are or are expected to be despatched or shipped.
        #13  Terms net due date - Date by which payment must be made.
        #35  Delivery date/time, actual - Date/time on which goods or consignment are delivered at their destination.
        #50  Goods receipt date/time - Date/time upon which the goods were received by a given party.
        #132 Transport means arrival date/time, estimated
        #137 Document/message date/time, date/time when a document/message is issued. This may include authentication.
        #167 Charge period start date - The charge period's first date.
        #168 Charge period end date - The charge period's last date.
        #361 Use by date.
        if not dt:
            dt = fields.Datetime.now()
        dt = fields.Datetime.from_string(dt)
        if format == 102:
            dt = dt.strftime('%Y%m%d')
        elif format == 203:
            dt = dt.strftime('%Y%m%d%H%M')
        return "DTM+%s:%s:%s'" % (func_code, dt, format)

    def FTX(self, msg1, msg2='', msg3='', msg4='', msg5='', subj='ZZZ', func=1, ref='001'):
        self._seg_count += 1
        return "FTX+%s+%s+%s+%s:%s:%s:%s:%s'" % (subj, func, ref, _escape_string(msg1), _escape_string(msg2), _escape_string(msg3), _escape_string(msg4), _escape_string(msg5))

    def GIN(self, sscc):
        self._seg_count += 1
        #BJ         Serial shipping container code
        return "GIN+BJ+%s'" % sscc

    #CR = Customer Reference
    def RFF(self, ref, qualifier='CR', line=None):
        # ON    Buyer Order Number
        # CR    Customer reference
        # AAS   Transport document number, Reference assigned by the carrier or his agent to the transport document.
        # CT    Contract Number
        # DQ    Delivery note number
        self._seg_count += 1
        if line:
            return "RFF+%s:%s:%s'" % (qualifier, ref, line)
        return "RFF+%s:%s'" % (qualifier, ref)
    
    @api.model
    def name_to_number(self, name):
        """I am not a number! I am a man!. And don't you eve... oh wait, I'm number 5. Haha. In your face, number 6!"""
        number = ''
        for c in name:
            number += c if c.isdigit() else ''
        return number
    
    def _get_account_tax(self, name):
        tax = self.env['account.tax'].search([('name', '=', name)])
        if len(tax) == 1:
            return tax[0]
        raise Warning("Couldn't find tax with name '%s'." % name)

    def TAX(self, rate, tax_type = 'VAT', qualifier = 7, category = 'S'):
        self._seg_count += 1
        #qualifier
        #   7 = tax
        return "TAX+%s+%s+++:::%.4f+%s'" % (qualifier, tax_type, rate, category)

    def _NAD(self, role, partner, type='GLN'):
        self._seg_count += 1
        if type == 'GLN':
            party_id = partner.gs1_gln
            if not party_id:
                raise Warning('NAD missing GLN role=%s partner=%s' % (role, partner.name))
            code = 9
        return "NAD+%s+%s::%s'" % (role, party_id, code)

    def NAD_SU(self, type='GLN'):
        return self._NAD('SU', self.consignor_id, type)
    def NAD_BY(self, partner, type='GLN'):
        return self._NAD('BY', partner, type)
    def NAD_SH(self, type='GLN'):
        return self._NAD('SH', self.forwarder_id, type)
    def NAD_DP(self, type='GLN'):
        return self._NAD('DP', self.nad_dp, type)
    def NAD_ITO(self, type='GLN'):
        return self._NAD('ITO', self.nad_ito, type)
    def NAD_CN(self, type='GLN'):
        return self._NAD('CN', self.consignee_id, type)  # ????

    #code = error/status code
    def LIN(self, line=None, code=''):
        self._seg_count += 1
        self._lin_count += 1
        item_nr_type = 'EU'
        if not line:
            return "LIN+%s'" % self._lin_count
        elif line._name == 'sale.order.line':
            if line.product_uom_qty <= 0:
                code = 7 # Not accepted
            elif line.product_uom_qty != line.order_qty:
                code = 3 # Quantity changed
            else:
                code = 5 # Accepted without amendment
        return "LIN+%s+%s+%s:%s::%s'" %(self._lin_count, code, line.product_id.gs1_gtin14 or line.product_id.gs1_gtin13, item_nr_type, 9)

    def MOA(self, amount, qualifier = 203):
        self._seg_count += 1
        return "MOA+%s:%s'" % (qualifier, amount)

    def PAC(self, amount=1, packaging_type='PX'):
        self._seg_count += 1
        #PX     Pallet
        return "PAC+%s+:52+%s'" % (amount, packaging_type)

    def PAT(self, pttq=3, ptr=66, tr=1):
        self._seg_count += 1
        #pttq   4279    Payment terms type qualifier
            #3 Fixed date - Payments are due on the fixed date specified.
        #ptr    2475    Payment time reference, coded
            #66 Specified date - Date specified elsewhere.
        #tr     2009    Time relation, coded
            #1  Date of order - Payment time reference is date of order.

        return "PAT+%s++%s:%s'" % (pttq, ptr, tr)

    def PCI(self):
        self._seg_count += 1
        #33E        Marked with serial shipping container code (EAN Code)
        return "PCI+33E'"

    def _get_customer_product_code(self, product, customer):
        #TODO: Create module that hooks this up with product_customer_code
        return None

    #SA = supplier code BP = buyer code
    def PIA(self, product, code, customer=None):
        prod_nr = None
        if code == 'SA':
            prod_nr = product.default_code
        elif code == 'BP':
            prod_nr = self._get_customer_product_code(product, customer)
        elif code == 'NB':
            prod_nr = product
        if prod_nr:
            self._seg_count += 1
            return "PIA+1+%s:%s'" % (prod_nr, code)
        return ""
        #raise Warning("PIA: couldn't find product code (%s) for %s (id: %s)" % (code, product.name, product.id))

    def PRI(self, amount, ptype='CT', qualifier='AAA'):
        #price type
        #   CT  Contract price
        #qualifier
        #   AAA Calculation net
        self._seg_count += 1
        return "PRI+%s:%s:%s'" % (qualifier, amount, ptype)

    def QTY(self, line, code = None):
        self._seg_count += 1

        if line._name == 'account.invoice.line':
            code = 47
            qty = line.quantity
        elif line._name == 'stock.quant':
            code = 12
            qty = line.qty
        elif line._name == 'stock.move':
            code = 12
            qty = line.product_uom_qty
        else:
            qty = line.product_uom_qty

         #~ if line.product_uom_qty != line.order_qty:
            #~ code = 12
        #~ else:
        if int(qty) == qty:
            qty = int(qty)
        if not code:
            code = 21
        return "QTY+%s:%s'" % (code, qty)

    def QVR(self, diff, reason = 'AV'):
        #AS     Artikeln har utgått ur sortimentet
        #AUE    Okänt artikelnummer
        #AV     Artikeln slut i lager
        #PC     Annan förpackningsstorlek
        #X35    Artikeln har dragits tillbaka
        #Z1     Slut för säsongen
        #Z2     Tillfälligt spärrad för försäljning (varan finns men kan ha karensdagar)
        #Z3     Nyhet, ej i lager
        #Z4     Tillfälligt spärrad på grund av konflikt
        #Z5     Restnoterad artikel från tillverkare och måste beställas på nytt
        #Z6     Produktionsproblem
        #Z7     Slut i lager hos tillverkaren
        #Z8     Beställningsvara
        #Z9     Restnoterad från tillverkaren
        #ZZ     Annan orsak
        self._seg_count += 1
        return "QVR+%s:21+CP+%s::9SE'" % (diff, reason)

    def UCI(self, ref, sender, recipient, state=8):
        self._seg_count += 1
        return "UCI+{ref}+{sender}:14+{recipient}:14+{state}'".format(ref=ref, sender=sender.gs1_gln, recipient=recipient.gs1_gln, state=state)

    def UNS(self):
        self._seg_count += 1
        return "UNS+S'"

    def UNT(self):
        self._seg_count += 1
        return "UNT+{count}+{ref}'".format(count=self._seg_count,ref=self.name)


    def _get_partner(self, l):
        #~ _logger.warn('get partner %s' % l)
        partner = None
        if l[2] == '9':
            partner = self.env['res.partner'].search([('gs1_gln', '=', l[0])])
        #~ _logger.warn(partner)
        if len(partner) == 1:
            return partner[0]
        #~ _logger.warn('Warning!')
        raise ValueError("Unknown part %s" % (len(l) >0 and l[0] or "[EMPTY LIST!]"),l[0],l[1])

    def _parse_quantity(self, l):
        #if l[0] == '21':
        return float(l[1])

    def _get_product(self, l):
        product = None
        if l[1] == 'EN' or l[1] == 'EU':  # Axfood ORDERS use EU
            product = self.env['product.product'].search(['|', ('gs1_gtin13', '=', l[0]), ('gs1_gtin14', '=', l[0])])
        elif l[1] == 'SA':
            product = self.env['product.product'].search([('default_code', '=', l[0])])
        if product and product.ensure_one():
            return product
        raise ValueError('Product not found! GTIN: %s' % l)

    def _find_envelope(self, ref, sender, recipient):
        envelope = self.env['edi.envelope'].search([('ref', '=', ref), ('sender', '=', sender.id), ('recipient', '=', recipient.id)])
        if len(envelope) == 1:
            return envelope[0]
        raise ValueError("Couldn't find envelope with reference '%s'" % ref)


    @api.model
    def _parse_date(self, l):
        if l[2] == '102':
            return fields.Datetime.to_string(datetime.strptime(l[1], '%Y%m%d'))
        elif l[2] == '203':
            return fields.Datetime.to_string(datetime.strptime(l[1], '%Y%m%d%H%M'))

