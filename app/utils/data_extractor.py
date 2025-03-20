import re
from typing import Dict, List, Tuple, Optional
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
import logging
from google.cloud import vision
from app.models import Invoice, Vendor, Address, InvoiceItem
from app.config import settings
import asyncio
from concurrent.futures import ThreadPoolExecutor
import dateparser
from price_parser import Price
import time
from tenacity import retry, stop_after_attempt, wait_exponential
import aioredis

logger = logging.getLogger(__name__)

class DataExtractor:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)
        self.redis = None

    async def initialize(self):
        self.redis = await aioredis.from_url(settings.REDIS_URL)

    async def extract_data(self, ocr_results: List[Dict]) -> List[Invoice]:
        try:
            start_time = time.time()
            results = await asyncio.gather(*[self._extract_single_result(result) for result in ocr_results])
            end_time = time.time()
            logger.info(f"Extracted data for {len(ocr_results)} documents in {end_time - start_time:.2f} seconds")
            return results
        except Exception as e:
            logger.error(f"Error extracting data: {str(e)}")
            return [Invoice(filename=result.get("filename", "")) for result in ocr_results]

    async def _extract_single_result(self, ocr_result: Dict) -> Invoice:
        try:
            cache_key = f"extracted:{hash(str(ocr_result))}"
            if self.redis:
                cached_result = await self.redis.get(cache_key)
                if cached_result:
                    logger.info(f"Cache hit for {ocr_result.get('filename', '')}")
                    return Invoice.parse_raw(cached_result)

            start_time = time.time()
            invoice = self.extract_invoice_data(ocr_result)
            end_time = time.time()
            logger.info(f"Extracted data for {ocr_result.get('filename', '')} in {end_time - start_time:.2f} seconds")

            if self.redis:
                await self.redis.set(cache_key, invoice.json(), expire=86400)  # Cache for 24 hours
            return invoice
        except Exception as e:
            logger.error(f"Error extracting data for {ocr_result.get('filename', '')}: {str(e)}")
            return Invoice(filename=ocr_result.get("filename", ""))

    def extract_invoice_data(self, ocr_result: Dict, docai_result: Optional[Dict] = None) -> Invoice:
        filename = ocr_result.get('filename', '')
        
        if docai_result and 'entities' in docai_result:
            invoice = self._extract_from_docai(docai_result, filename)
            
            if self._is_invoice_valid(invoice):
                return invoice
        
        return self._extract_from_gcv(ocr_result, filename)
    
    def _is_invoice_valid(self, invoice: Invoice) -> bool:
        return (invoice.invoice_number or 
                invoice.vendor.name or 
                invoice.invoice_date or 
                invoice.grand_total is not None)

    def _extract_from_docai(self, docai_result: Dict, filename: str) -> Invoice:
        entities = docai_result.get('entities', {})
        
        vendor = Vendor(
            name=entities.get('supplier_name', ''),
            address=Address(
                street=entities.get('supplier_address', ''),
                city=entities.get('supplier_city', ''),
                state=entities.get('supplier_state', ''),
                country=entities.get('supplier_country', ''),
                postal_code=entities.get('supplier_zip', '')
            )
        )

        invoice_date = None
        if 'invoice_date' in entities:
            try:
                invoice_date = datetime.strptime(entities.get('invoice_date', ''), '%Y-%m-%d').date()
            except ValueError:
                logger.warning(f"Could not parse invoice date: {entities.get('invoice_date', '')}")

        grand_total = None
        if 'total_amount' in entities:
            try:
                grand_total = self._parse_decimal(entities.get('total_amount', ''))
            except:
                pass
                
        taxes = None
        if 'total_tax_amount' in entities:
            try:
                taxes = self._parse_decimal(entities.get('total_tax_amount', ''))
            except:
                pass
                
        final_total = None
        if 'total_amount' in entities:
            try:
                final_total = self._parse_decimal(entities.get('total_amount', ''))
            except:
                pass

        items = []
        tables = docai_result.get('tables', [])
        for table in tables:
            for row in table:
                try:
                    if len(row) >= 4:
                        item = InvoiceItem(
                            description=row[0],
                            quantity=int(row[1]) if row[1].strip() else None,
                            unit_price=self._parse_decimal(row[2]) if row[2].strip() else None,
                            total=self._parse_decimal(row[3]) if row[3].strip() else None
                        )
                        items.append(item)
                except (ValueError, IndexError, InvalidOperation) as e:
                    logger.warning(f"Error parsing invoice item: {str(e)}")
                    continue

        return Invoice(
            filename=filename,
            invoice_number=entities.get('invoice_id', ''),
            vendor=vendor,
            invoice_date=invoice_date,
            grand_total=grand_total,
            taxes=taxes,
            final_total=final_total,
            items=items,
            pages=1  
        )

    def _extract_from_gcv(self, ocr_result: Dict, filename: str) -> Invoice:
        text = ocr_result.get('text', '')
        if not text and 'words' in ocr_result:
            text = ' '.join(ocr_result.get('words', []))
        
        invoice_number = self._extract_invoice_number(text)
        
        vendor = self._extract_vendor(text)
        
        invoice_date = self._extract_date(text)
        
        grand_total, taxes, final_total = self._extract_totals(text)
        
        items = self._extract_items(ocr_result)
        
        return Invoice(
            filename=filename,
            invoice_number=invoice_number,
            vendor=vendor,
            invoice_date=invoice_date,
            grand_total=grand_total,
            taxes=taxes,
            final_total=final_total,
            items=items,
            pages=ocr_result.get('num_pages', 1)
        )

    def _extract_invoice_number(self, text: str) -> Optional[str]:
        patterns = [
            r'(?i)invoice\s*number?[:\s]*([A-Za-z0-9-]{5,})',
            r'(?i)invoice\s*#[:\s]*([A-Za-z0-9-]{5,})',
            r'(?i)inv[:\s]*([A-Za-z0-9-]{5,})'
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return None

    def _extract_vendor(self, text: str) -> Vendor:
        lines = text.split('\n')
        if not lines:
            return Vendor(name="", address=Address())
            
        name = lines[0] if lines else ""
        address_text = '\n'.join(lines[1:4]) if len(lines) > 1 else ""
        
        return Vendor(
            name=name,
            address=self._extract_address(address_text)
        )

    def _extract_address(self, text: str) -> Address:
        lines = text.split('\n')
        
        street = lines[0] if lines else ""
        city = ""
        state = ""
        postal_code = ""
        country = ""
        
        if len(lines) > 1:
            address_line = lines[1]
            postal_match = re.search(r'\b\d{5}(?:-\d{4})?\b', address_line)
            if postal_match:
                postal_code = postal_match.group(0)
            
            city_state_match = re.search(r'([A-Za-z\s]+),\s*([A-Z]{2})', address_line)
            if city_state_match:
                city = city_state_match.group(1).strip()
                state = city_state_match.group(2)
        
        return Address(
            street=street,
            city=city,
            state=state,
            country=country,
            postal_code=postal_code
        )

    def _extract_date(self, text: str) -> Optional[date]:
        patterns = [
            r'(?i)date[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'(?i)invoice\s*date[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    parsed_date = dateparser.parse(match.group(1))
                    if parsed_date:
                        return parsed_date.date()
                except:
                    pass
        return None

    def _extract_totals(self, text: str) -> Tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
        grand_total = None
        taxes = None
        final_total = None
        
        subtotal_match = re.search(r'(?i)subtotal[:\s]*\$?([\d,]+\.\d{2})', text)
        if subtotal_match:
            grand_total = self._parse_decimal(subtotal_match.group(1))
        
        tax_match = re.search(r'(?i)tax[:\s]*\$?([\d,]+\.\d{2})', text)
        if tax_match:
            taxes = self._parse_decimal(tax_match.group(1))
        
        total_match = re.search(r'(?i)total[:\s]*\$?([\d,]+\.\d{2})', text)
        if total_match:
            final_total = self._parse_decimal(total_match.group(1))
        
        return grand_total, taxes, final_total

    def _extract_items(self, ocr_result: Dict) -> List[InvoiceItem]:
        items = []
        
        tables = ocr_result.get('tables', [])
        for table in tables:
            for row in table[1:] if len(table) > 1 else []:
                try:
                    if len(row) >= 4:
                        description = row[0]
                        quantity = int(row[1]) if row[1].strip() else None
                        unit_price = self._parse_decimal(row[2]) if row[2].strip() else None
                        total = self._parse_decimal(row[3]) if row[3].strip() else None
                        
                        items.append(InvoiceItem(
                            description=description,
                            quantity=quantity,
                            unit_price=unit_price,
                            total=total
                        ))
                except (ValueError, IndexError, InvalidOperation) as e:
                    logger.warning(f"Error parsing item: {str(e)}")
        
        return items

    def _parse_decimal(self, amount_string: str) -> Optional[Decimal]:
        if not amount_string or not amount_string.strip():
            return None
            
        try:
            cleaned = re.sub(r'[^\d.-]', '', amount_string)
            return Decimal(cleaned)
        except (InvalidOperation, TypeError):
            try:
                price = Price.fromstring(amount_string)
                return Decimal(str(price.amount)) if price.amount else None
            except:
                logger.warning(f"Could not parse decimal: {amount_string}")
                return None

    async def cleanup(self):
        self.executor.shutdown(wait=True)
        if self.redis:
            await self.redis.close()

data_extractor = DataExtractor()

async def initialize_data_extractor():
    await data_extractor.initialize()

async def cleanup_data_extractor():
    await data_extractor.cleanup()

def extract_invoice_data(ocr_result: Dict, docai_result: Optional[Dict] = None) -> Invoice:
    return data_extractor.extract_invoice_data(ocr_result, docai_result)
