from typing import List, Dict, Tuple
from datetime import datetime, date
from decimal import Decimal
from app.models import Invoice, Vendor, Address, InvoiceItem
from app.config import settings
import re
import logging
from pydantic import ValidationError

logger = logging.getLogger(__name__)

class InvoiceValidator:
    def __init__(self):
        self.date_format = "%Y-%m-%d"

    def validate_invoice(self, invoice: Invoice) -> Tuple[bool, List[str], Dict[str, List[str]]]:
        warnings = {}
        
        warnings['filename'] = self._validate_filename(invoice.filename)
        warnings['invoice_number'] = self._validate_invoice_number(invoice.invoice_number)
        warnings['vendor'] = self._validate_vendor(invoice.vendor)
        warnings['invoice_date'] = self._validate_date(invoice.invoice_date)
        warnings['grand_total'] = self._validate_amount(invoice.grand_total, "Grand total")
        warnings['taxes'] = self._validate_amount(invoice.taxes, "Taxes")
        warnings['final_total'] = self._validate_amount(invoice.final_total, "Final total")
        warnings['totals'] = self._validate_totals(invoice.grand_total, invoice.taxes, invoice.final_total)
        warnings['pages'] = self._validate_pages(invoice.pages)
        warnings['items'] = self._validate_items(invoice.items)

        all_warnings = [w for sublist in warnings.values() for w in sublist]
        is_valid = len(all_warnings) == 0

        return is_valid, all_warnings, warnings
    
    def validate_invoices(self, invoices: List[Invoice]) -> List[Tuple[Invoice, List[str], Dict[str, List[str]]]]:
        results = []
        for invoice in invoices:
            is_valid, warnings, categorized_warnings = self.validate_invoice(invoice)
            results.append((invoice, warnings, categorized_warnings))
        return results

    def _validate_filename(self, filename: str) -> List[str]:
        warnings = []
        if not filename or not filename.strip():
            warnings.append("Filename is missing")
        return warnings

    def _validate_invoice_number(self, invoice_number: str) -> List[str]:
        warnings = []
        if not invoice_number or not invoice_number.strip():
            warnings.append("Invoice number is missing")
        elif not re.match(r'^[A-Za-z0-9-]{5,}$', invoice_number):
            warnings.append(f"Unusual invoice number format: {invoice_number}")
        return warnings

    def _validate_vendor(self, vendor: Vendor) -> List[str]:
        warnings = []
        if not vendor.name or not vendor.name.strip():
            warnings.append("Vendor name is missing")
        warnings.extend(self._validate_address(vendor.address))
        return warnings

    def _validate_address(self, address: Address) -> List[str]:
        warnings = []
        if not address:
            return ["Vendor address is missing"]
        if not address.street or not address.street.strip():
            warnings.append("Vendor street is missing")
        if not address.city or not address.city.strip():
            warnings.append("Vendor city is missing")
        if not address.state or not address.state.strip():
            warnings.append("Vendor state is missing")
        if not address.postal_code or not address.postal_code.strip():
            warnings.append("Vendor postal code is missing")
        if not address.country or not address.country.strip():
            warnings.append("Vendor country is missing")
        return warnings

    def _validate_date(self, invoice_date: date) -> List[str]:
        warnings = []
        if not invoice_date:
            warnings.append("Invoice date is missing")
        elif invoice_date > date.today():
            warnings.append(f"Invoice date {invoice_date} is in the future")
        return warnings

    def _validate_amount(self, amount: Decimal, field_name: str) -> List[str]:
        warnings = []
        if amount is None:
            warnings.append(f"{field_name} is missing")
        elif amount < 0:
            warnings.append(f"{field_name} is negative")
        return warnings

    def _validate_totals(self, grand_total: Decimal, taxes: Decimal, final_total: Decimal) -> List[str]:
        warnings = []
        if all(amount is not None for amount in [grand_total, taxes, final_total]):
            if abs((grand_total + taxes) - final_total) > Decimal('0.01'):
                warnings.append(f"Total amounts may not match: {grand_total} + {taxes} ≈ {final_total}")
        return warnings

    def _validate_pages(self, pages: int) -> List[str]:
        warnings = []
        if pages is None:
            warnings.append("Number of pages is missing")
        elif pages < 1:
            warnings.append(f"Unusual number of pages: {pages}")
        return warnings

    def _validate_items(self, items: List[InvoiceItem]) -> List[str]:
        warnings = []
        if not items:
            warnings.append("No line items found in the invoice")
        for idx, item in enumerate(items, 1):
            if not item.description or not item.description.strip():
                warnings.append(f"Item {idx}: Description is missing")
            if item.quantity is None:
                warnings.append(f"Item {idx}: Quantity is missing")
            elif item.quantity <= 0:
                warnings.append(f"Item {idx}: Unusual quantity")
            if item.unit_price is None:
                warnings.append(f"Item {idx}: Unit price is missing")
            elif item.unit_price < 0:
                warnings.append(f"Item {idx}: Unusual unit price")
            if item.total is None:
                warnings.append(f"Item {idx}: Total is missing")
            elif item.total < 0:
                warnings.append(f"Item {idx}: Unusual total")
            if all(value is not None for value in [item.quantity, item.unit_price, item.total]):
                if abs(round(item.quantity * item.unit_price, 2) - item.total) > Decimal('0.01'):
                    warnings.append(f"Item {idx}: Total may not match quantity * unit price")
        return warnings

    def validate_extracted_data(self, extracted_data: Dict) -> Tuple[bool, List[str], Dict[str, List[str]]]:
        try:
            invoice = Invoice(**extracted_data)
            return self.validate_invoice(invoice)
        except ValidationError as e:
            return False, [str(e)], {"validation_error": [str(e)]}

invoice_validator = InvoiceValidator()

def validate_invoice_batch(invoices: List[Dict]) -> List[Tuple[Dict, bool, List[str], Dict[str, List[str]]]]:
    results = []
    for invoice_data in invoices:
        is_valid, warnings, categorized_warnings = invoice_validator.validate_extracted_data(invoice_data)
        results.append((invoice_data, is_valid, warnings, categorized_warnings))
    return results

def flag_anomalies(invoices: List[Invoice]) -> List[Dict]:
    flagged_invoices = []
    for invoice in invoices:
        flags = []
        
        # Check for future date
        if invoice.invoice_date is not None and invoice.invoice_date > date.today():
            flags.append("Future date")

        # Check for high total amount
        if invoice.final_total is not None and invoice.final_total > Decimal('10000.00'):
            flags.append("Unusually high total amount")

        # Check for large number of line items
        if invoice.items is not None and len(invoice.items) > 20:
            flags.append("Large number of line items")

        # Only add to flagged_invoices if there are flags
        if flags:
            flagged_invoices.append({**invoice.dict(), 'flags': flags})

    return flagged_invoices

