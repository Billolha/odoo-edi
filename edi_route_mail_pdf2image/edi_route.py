# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution, third party addon
#    Copyright (C) 2004-2017 Vertel AB (<http://vertel.se>).
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
from openerp.exceptions import except_orm, Warning, RedirectWarning

import base64

import logging
_logger = logging.getLogger(__name__)

class edi_envelope(models.Model):
    _inherit = 'edi.envelope'

    def _check_mail_attachments_image(self,image=None):
        if not image:
            image = super(edi_envelope,self)._check_mail_attachments_image()
        if not image:
            for attachment in self.mail_id.attachment_ids:
                if attachment.file_type == 'application/pdf':
                    if not attachment.image:
                        attachment.pdf2image(800,1200)
                    image = attachment.image
        return image

        
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4: