from pydantic import BaseModel, Field, validator, constr
from typing import List, Optional
from datetime import date, datetime
from decimal import Decimal
import re

class Address(BaseModel):
    street: Optional[str] = ""
    city: Optional[str] = ""
    state: Optional[str] = ""
    country: Optional[str] = ""
    postal_code: Optional[str] = ""

class Vendor(BaseModel):
    name: Optional[str] = "Unknown Vendor"
    address: Address

class InvoiceItem(BaseModel):
    description: Optional[str] = "Unspecified Item"
    quantity: int = Field(default=1, ge=0)
    unit_price: Decimal = Field(default=Decimal('0'), ge=0)
    total: Decimal = Field(default=Decimal('0'), ge=0)

    @validator('total')
    def validate_item_total(cls, v, values):
        if 'quantity' in values and 'unit_price' in values:
            expected_total = values['quantity'] * values['unit_price']
            if abs(v - expected_total) > Decimal('0.01'):
                return v
        return v

class Invoice(BaseModel):
    filename: constr(min_length=1)
    invoice_number: Optional[str] = "UNKNOWN-00001"
    vendor: Vendor
    invoice_date: Optional[date] = date.today()
    grand_total: Decimal = Field(default=Decimal('0'), ge=0)
    taxes: Decimal = Field(default=Decimal('0'), ge=0)
    final_total: Decimal = Field(default=Decimal('0'), ge=0)
    items: List[InvoiceItem] = []
    pages: int = Field(default=1, ge=1)

    @validator('final_total')
    def validate_final_total(cls, v, values):
        if 'grand_total' in values and 'taxes' in values:
            expected_total = values['grand_total'] + values['taxes']
            if abs(v - expected_total) > Decimal('0.01'):
                return v
        return v

    @validator('invoice_date')
    def validate_invoice_date(cls, v):
        if v and v > date.today():
            return date.today()
        return v

class ProcessingResult(BaseModel):
    success: bool
    message: str
    invoices: List[Invoice] = []
    errors: List[str] = []

class FileUpload(BaseModel):
    filename: constr(min_length=1)
    content_type: str
    file_size: int

    @validator('content_type')
    def validate_content_type(cls, v):
        allowed_types = {'application/pdf', 'image/jpeg', 'image/png', 'application/zip'}
        if v not in allowed_types:
            raise ValueError(f"Unsupported file type: {v}")
        return v

    @validator('file_size')
    def validate_file_size(cls, v):
        max_size = 100 * 1024 * 1024  # 100MB
        if v > max_size:
            raise ValueError(f"File size exceeds maximum allowed size of 100MB")
        return v

class ExportFormat(BaseModel):
    format: str = Field(..., regex='^(csv|excel)$')

class ProcessingStatus(BaseModel):
    status: str
    progress: float = Field(ge=0, le=100)
    message: Optional[str]
