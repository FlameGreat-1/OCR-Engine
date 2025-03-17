import pandas as pd
import io
import logging
from typing import List
from app.models import Invoice
from app.config import settings
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

class InvoiceExporter:
    def __init__(self):
        self.columns = [
            "Filename", "Invoice Number", "Vendor Name", "Vendor Street", "Vendor City",
            "Vendor State", "Vendor Postal Code", "Vendor Country", "Invoice Date",
            "Grand Total", "Taxes", "Final Total", "Pages"
        ]
        self.item_columns = ["Invoice Number", "Description", "Quantity", "Unit Price", "Total"]
        self.executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)

    async def export_invoices(self, invoices: List[Invoice], format: str) -> io.BytesIO:
        try:
            df, items_df = await self._create_dataframes(invoices)
            if format.lower() == 'csv':
                return await self._export_to_csv(df, items_df)
            elif format.lower() == 'excel':
                return await self._export_to_excel(df, items_df)
            else:
                raise ValueError(f"Unsupported export format: {format}")
        except Exception as e:
            logger.error(f"Error during invoice export: {str(e)}")
            raise

    async def _create_dataframes(self, invoices: List[Invoice]) -> tuple:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._create_dataframes_sync, invoices)

    def _create_dataframes_sync(self, invoices: List[Invoice]) -> tuple:
        data = []
        items_data = []
        for invoice in invoices:
            row = {
                "Filename": invoice.filename,
                "Invoice Number": invoice.invoice_number,
                "Vendor Name": invoice.vendor.name,
                "Vendor Street": invoice.vendor.address.street,
                "Vendor City": invoice.vendor.address.city,
                "Vendor State": invoice.vendor.address.state,
                "Vendor Postal Code": invoice.vendor.address.postal_code,
                "Vendor Country": invoice.vendor.address.country,
                "Invoice Date": invoice.invoice_date,
                "Grand Total": invoice.grand_total,
                "Taxes": invoice.taxes,
                "Final Total": invoice.final_total,
                "Pages": invoice.pages
            }
            data.append(row)
            
            for item in invoice.items:
                items_data.append({
                    "Invoice Number": invoice.invoice_number,
                    "Description": item.description,
                    "Quantity": item.quantity,
                    "Unit Price": item.unit_price,
                    "Total": item.total
                })

        df = pd.DataFrame(data, columns=self.columns)
        items_df = pd.DataFrame(items_data, columns=self.item_columns)
        
        return df, items_df

    async def _export_to_csv(self, df: pd.DataFrame, items_df: pd.DataFrame) -> io.BytesIO:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._export_to_csv_sync, df, items_df)

    def _export_to_csv_sync(self, df: pd.DataFrame, items_df: pd.DataFrame) -> io.BytesIO:
        output = io.BytesIO()
        df.to_csv(output, index=False, float_format='%.2f')
        output.write(b'\n\nLine Items:\n')
        items_df.to_csv(output, index=False, float_format='%.2f', mode='a')
        output.seek(0)
        return output

    async def _export_to_excel(self, df: pd.DataFrame, items_df: pd.DataFrame) -> io.BytesIO:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._export_to_excel_sync, df, items_df)

    def _export_to_excel_sync(self, df: pd.DataFrame, items_df: pd.DataFrame) -> io.BytesIO:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Invoices', index=False)
            items_df.to_excel(writer, sheet_name='Line Items', index=False)
            
            workbook = writer.book
            for sheet in [workbook['Invoices'], workbook['Line Items']]:
                for column in sheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(cell.value)
                        except:
                            pass
                    adjusted_width = (max_length + 2)
                    sheet.column_dimensions[column_letter].width = adjusted_width

        output.seek(0)
        return output

async def export_invoices(invoices: List[Invoice], format: str) -> io.BytesIO:
    exporter = InvoiceExporter()
    return await exporter.export_invoices(invoices, format)
