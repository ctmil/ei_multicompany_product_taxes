from openerp import models, fields, api, _
from openerp.osv import osv
from openerp.exceptions import except_orm, ValidationError
from StringIO import StringIO
import urllib2, httplib, urlparse, gzip, requests, json
import openerp.addons.decimal_precision as dp
import logging
import datetime
from openerp.fields import Date as newdate

#Get the logger
_logger = logging.getLogger(__name__)

class res_company(models.Model):
	_inherit = 'res.company'

	@api.constrains('default_purchase_tax_id')
	def _check_purchase_tax(self):
		if self.default_purchase_tax_id:
			if self.id != self.default_purchase_tax_id.company_id.id:
				raise ValidationError("Impuesto seleccionado no corresponde a la empresa")

	@api.constrains('default_sale_tax_id')
	def _check_purchase_tax(self):
		if self.default_sale_tax_id:
			if self.id != self.default_sale_tax_id.company_id.id:
				raise ValidationError("Impuesto seleccionado no corresponde a la empresa")
		
	
	default_purchase_tax_id = fields.Many2one('account.tax',string='Impuesto Default en Compras')
	default_sale_tax_id = fields.Many2one('account.tax',string='Impuesto Default en Ventas')
	default_sale_account_id = fields.Many2one('account.account',string='Cuenta por default para productos a vender')
	default_purchase_account_id = fields.Many2one('account.account',string='Cuenta por default para productos a comprar')
	default_customer_account_id = fields.Many2one('account.account',string='Cuenta por default para clientes')
	default_supplier_account_id = fields.Many2one('account.account',string='Cuenta por default para proveedores')

class product_taxes(models.Model):
        _name = 'product.taxes'
	_description = 'Impuestos del producto'

	@api.constrains('product_id','company_id','tax_id')
	def _check_tax_unique(self):
		cnt = self.env['product.taxes'].search([('company_id','=',self.company_id.id),\
			('tax_id','=',self.tax_id.id),('product_id','=',self.product_id.id)])
		if cnt and len(cnt) > 1:
			raise ValidationError('Solo se debe tener un impuesto por producto por empresa')

	@api.one
	def _compute_name(self):
		return_value = ''
		if self.company_id and self.tax_id:
			return_value = self.company_id.name + ' - ' + self.tax_id.name
		self.name = return_value
        
	name = fields.Char('Name',compute=_compute_name)
	product_id = fields.Many2one('product.product',string='Product')
	company_id = fields.Many2one('res.company',string='Company')
	tax_id = fields.Many2one('account.tax',string='Tax')
	tax_type = fields.Selection([('sale','Ventas'),('purchase','Compras')])

class product_accounts(models.Model):
	_name = 'product.accounts'
	_description = 'Cuentas del producto'

	name = fields.Char('Name')
	product_id = fields.Many2one('product.product',string='Product')
	company_id = fields.Many2one('res.company',string='Company')
	account_id = fields.Many2one('account.account',string='Account')
	account_type = fields.Selection([('receivable','A cobrar'),('payable','Por pagar')])

class partner_accounts(models.Model):
	_name = 'partner.accounts'
	_description = 'Cuentas del partner'

	name = fields.Char('Name')
	partner_id = fields.Many2one('res.partner',string='Partner')
	company_id = fields.Many2one('res.company',string='Company')
	account_id = fields.Many2one('account.account',string='Account')
	account_type = fields.Selection([('receivable','A cobrar'),('payable','Por pagar')])


class purchase_order_line(models.Model):
	_inherit = 'purchase.order.line'

	@api.model
	def create(self,vals):
		product_id = vals.get('product_id',False)
		order_id = vals.get('order_id',False)
		if product_id and order_id:
			order = self.env['purchase.order'].browse(order_id)
			product_tax = self.env['product.taxes'].search([('product_id','=',product_id),\
					('company_id','=',order.company_id.id)])
			if product_tax:
				return_value = [[6,0,[product_tax.tax_id.id]]]
				vals['taxes_id'] = return_value	
                return super(purchase_order_line, self).create(vals)
	
class sale_order_line(models.Model):
	_inherit = 'sale.order.line'

	@api.model
	def create(self,vals):
		product_id = vals.get('product_id',False)
		order_id = vals.get('order_id',False)
		if product_id and order_id:
			order = self.env['sale.order'].browse(order_id)
			# Busca las restricciones
			restrictions = self.env['product.company.restrictions'].search([('company_id','=',order.company_id.id),\
					('product_id','=',product_id),('partner_id','=',order.partner_id.id)])
			if restrictions:
				for restriction in restrictions:
					if restriction.action  == 'disable':
						raise ValidationError("Al cliente no se le puede facturar el producto seleccionado")
			restrictions = self.env['product.company.restrictions'].search([('company_id','=',order.company_id.id),\
					('product_id','=',product_id)])
			if restrictions:
				for restriction in restrictions:
					if restriction.action  == 'disable':
						raise ValidationError("No se puede facturar el producto por la empresa seleccionada")
			restrictions = self.env['product.company.restrictions'].search([('company_id','=',order.company_id.id),\
					('product_id','=',product_id),('action','=','enable')])
			if not restrictions:
				raise ValidationError("No se puede facturar el producto por la empresa seleccionada")
			# Busca los impuestos

			product_tax = self.env['product.taxes'].search([('product_id','=',product_id),\
					('company_id','=',order.company_id.id),('tax_type','=','sale')])
			if product_tax:
				return_value = [[6,0,[product_tax.tax_id.id]]]
				vals['tax_id'] = return_value	
                return super(sale_order_line, self).create(vals)

class account_invoice_line(models.Model):
	_inherit = 'account.invoice.line'

	invoice_line_tax_ids = fields.Many2many('account.tax','account_invoice_line_tax', 'invoice_line_id', 'tax_id',\
	        string='Taxes', domain=[('type_tax_use','!=','none'), '|', ('active', '=', False), ('active', '=', True)],\
		 oldname='invoice_line_tax_id',readonly=True)


	@api.model
	def create(self,vals):
		product_id = vals.get('product_id',False)
		invoice_id = vals.get('invoice_id',False)
		if product_id and invoice_id:
			invoice = self.env['account.invoice'].browse(invoice_id)
			invoice_type = invoice.type
			if invoice_type in ['in_refund','in_invoice']:
				invoice = self.env['account.invoice'].browse(invoice_id)
				product_tax = self.env['product.taxes'].search([('product_id','=',product_id),\
						('company_id','=',invoice.company_id.id),('tax_type','=','purchase')])
				if product_tax:
					return_value = [[6,0,[product_tax.tax_id.id]]]
					vals['invoice_line_tax_ids'] = return_value	
				product_account = self.env['product.accounts'].search([('product_id','=',product_id),\
						('company_id','=',invoice.company_id.id),('account_type','=','payable')])
				if product_account:
					vals['account_id'] = product_account.id	
			else:
				product_tax = self.env['product.taxes'].search([('product_id','=',product_id),\
						('company_id','=',invoice.company_id.id),('tax_type','=','sale')])
				if product_tax:
					return_value = [[6,0,[product_tax.tax_id.id]]]
					vals['invoice_line_tax_ids'] = return_value	
				product_account = self.env['product.accounts'].search([('product_id','=',product_id),\
						('company_id','=',invoice.company_id.id),('account_type','=','receivable')])
				if product_account:
					vals['account_id'] = product_account.account_id.id	
				
                return super(account_invoice_line, self).create(vals)
		

class product_product(models.Model):
	_inherit = 'product.product'

	product_taxes_ids = fields.One2many(comodel_name='product.taxes',inverse_name='product_id')
	product_accounts_ids = fields.One2many(comodel_name='product.accounts',inverse_name='product_id')
	product_company_restrictions_ids = fields.One2many(comodel_name='product.company.restrictions',inverse_name='product_id')

        @api.model
        def create(self, vals):
                res = super(product_product, self).create(vals)
		companies = self.env['res.company'].search([])
		for company in companies:
			if company.default_purchase_tax_id:
				tax_values = {
					'product_id': res.id,
					'company_id': company.id,
					'tax_id': company.default_purchase_tax_id.id
					}
				return_id = self.env['product.taxes'].create(tax_values)
		return res
	

class res_partner(models.Model):
	_inherit = 'res.partner'

	customer_account_id = fields.One2many(comodel_name='partner.accounts',inverse_name='partner_id')	
	supplier_account_id = fields.One2many(comodel_name='partner.accounts',inverse_name='partner_id')	

class product_company_restrictions(models.Model):
	_name = 'product.company.restrictions'
	_description = 'Product company restrictions'

	name = fields.Char('Name')
	product_id = fields.Many2one('product.product',string='Producto')
	company_id = fields.Many2one('res.company',string='Empresa')
	partner_id = fields.Many2one('res.partner',string='Partner')
	action = fields.Selection([('enable','Permitir'),('disable','No permitir')],default="enable")

class account_invoice(models.Model):
	_inherit = 'account.invoice'

	@api.model
	def create(self, vals):
		company_id = vals.get('company_id',False)
		partner_id = vals.get('partner_id',False)
		invoice_type = vals.get('type',False)
		if partner_id and invoice_type and company_id:
			if invoice_type in ['in_refund','in_invoice']:
				partner_account = self.env['partner.accounts'].search([('partner_id','=',product_id),\
						('company_id','=',company_id),('account_type','=','payable')])
				if partner_account:
					vals['account_id'] = partner_account.account_id.id	
			else:
				partner_account = self.env['partner.accounts'].search([('partner_id','=',partner_id),\
						('company_id','=',company_id),('account_type','=','receivable')])
				if partner_account:
					vals['account_id'] = partner_account.account_id.id	
				
                return super(account_invoice, self).create(vals)
	
