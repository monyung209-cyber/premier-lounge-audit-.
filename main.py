import os
import json
import requests
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# Initialize FastAPI App
app = FastAPI(
    title="Premier Lounge Hybrid Audit System",
    description="Real-time payment verification and daily AI reconciliation agent."
)

# ==========================================
# 1. PYDANTIC SCHEMAS
# ==========================================

class RealtimePaymentPayload(BaseModel):
    transaction_ref: str
    bank_source: str
    payer_name: str
    amount_usd: float
    chat_id: str
    received_at: str

class ServiceBooking(BaseModel):
    service_id: str
    client_name: str
    service_title: str
    price_usd: float
    booking_status: str

class ServiceLog(BaseModel):
    service_id: str
    source: str
    customer_name: str
    service_name: str
    amount_usd: float
    timestamp: str
    status: str

class PaymentLog(BaseModel):
    payment_id: str
    source: str
    transaction_ref: str
    customer_name: str
    amount_usd: float
    payment_method: str
    timestamp: str

class BatchAuditPayload(BaseModel):
    target_date: str
    service_logs: List[ServiceLog]
    payment_logs: List[PaymentLog]

class Discrepancy(BaseModel):
    service_id: Optional[str] = Field(description="ID of related booking")
    payment_id: Optional[str] = Field(description="ID of related payment")
    issue_type: str = Field(description="MISMATCHED_AMOUNT, UNPAID_SERVICE, or UNLINKED_PAYMENT")
    description: str = Field(description="Explanation of issue")

class ReconciliationReport(BaseModel):
    reconciliation_status: str = Field(description="BALANCED or DISCREPANCY_DETECTED")
    total_service_revenue_usd: float
    total_payment_collected_usd: float
    variance_usd: float
    matched_transactions_count: int
    discrepancies: List[Discrepancy]
    audit_summary: str

# ==========================================
# 2. TELEGRAM ALERT HELPER
# ==========================================

def send_telegram_notification(bot_token: str, chat_id: str, message_text: str) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message_text,
        "parse_mode": "Markdown"
    }
    response = requests.post(url, json=payload, timeout=10)
    return response.json()

# ==========================================
# 3. OPTION B: REAL-TIME STREAM CHECK
# ==========================================

# Active bookings mock lookup database
ACTIVE_FRESHA_BOOKINGS = [
    ServiceBooking(
        service_id="FSH-2001",
        client_name="Sokha Chen",
        service_title="Executive Salon Service",
        price_usd=25.00,
        booking_status="IN_PROGRESS"
    ),
    ServiceBooking(
        service_id="FSH-2002",
        client_name="Vannak Kouy",
        service_title="Car Detailing",
        price_usd=18.00,
        booking_status="IN_PROGRESS"
    )
]

@app.post("/api/v1/realtime-check")
def handle_realtime_payment(payment: RealtimePaymentPayload):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "789101112:AAExampleTokenForPremierLoungeBot2026")
    
    matched_booking = None
    for booking in ACTIVE_FRESHA_BOOKINGS:
        if booking.client_name.lower() in payment.payer_name.lower() or payment.payer_name.lower() in booking.client_name.lower():
            matched_booking = booking
            break

    if not matched_booking:
        alert_msg = (
            f"🚨 *UNLINKED PAYMENT DETECTED*\n"
            f"• Source: {payment.bank_source}\n"
            f"• Payer: {payment.payer_name}\n"
            f"• Paid: ${payment.amount_usd:.2f}\n"
            f"• Ref: `{payment.transaction_ref}`\n"
            f"⚠️ No active Fresha booking found for this customer!"
        )
        send_telegram_notification(bot_token, payment.chat_id, alert_msg)
        return {"status": "FLAGGED", "reason": "UNLINKED_PAYMENT"}

    if payment.amount_usd < matched_booking.price_usd:
        shortage = matched_booking.price_usd - payment.amount_usd
        alert_msg = (
            f"⚠️ *UNDERPAYMENT ALERT*\n"
            f"• Customer: {payment.payer_name}\n"
            f"• Service Fee: ${matched_booking.price_usd:.2f} ({matched_booking.service_title})\n"
            f"• Received: ${payment.amount_usd:.2f} via {payment.bank_source}\n"
            f"• Shortage: *${shortage:.2f}*\n"
            f"• Ref: `{payment.transaction_ref}`"
        )
        send_telegram_notification(bot_token, payment.chat_id, alert_msg)
        return {"status": "FLAGGED", "reason": "UNDERPAYMENT", "shortage_usd": shortage}

    success_msg = (
        f"✅ *PAYMENT MATCHED*\n"
        f"• Customer: {payment.payer_name}\n"
        f"• Amount: ${payment.amount_usd:.2f}\n"
        f"• Booking Ref: {matched_booking.service_id}"
    )
    send_telegram_notification(bot_token, payment.chat_id, success_msg)
    return {"status": "VERIFIED", "booking_id": matched_booking.service_id}

# ==========================================
# 4. OPTION A: END-OF-DAY BATCH AUDIT
# ==========================================

@app.post("/api/v1/batch-audit", response_model=ReconciliationReport)
def execute_batch_audit(payload: BatchAuditPayload):
    api_key = os.environ.get("GEMINI_API_KEY", "AIzaSyDemoApiKeyZeroPlaceholder2026ValidFormat")
    client = genai.Client(api_key=api_key)

    prompt = f"""
    You are the Premier Lounge Multi-System Reconciliation Agent.
    Perform an end-of-day audit for date: {payload.target_date}.

    Compare Service Logs against Payment Logs:
    Service Logs: {json.dumps([s.model_dump() for s in payload.service_logs], indent=2)}
    Payment Logs: {json.dumps([p.model_dump() for p in payload.payment_logs], indent=2)}

    Cross-examine each customer service entry against payment receipts.
    Identify underpayments, unpaid services, unlinked payments, and calculate net variance.
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ReconciliationReport,
            temperature=0.1
        )
    )

    report = ReconciliationReport.model_validate_json(response.text)
    
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "789101112:AAExampleTokenForPremierLoungeBot2026")
    summary_msg = (
        f"📊 *DAILY AUDIT REPORT: {payload.target_date}*\n"
        f"• Status: *{report.reconciliation_status}*\n"
        f"• Total Billed: ${report.total_service_revenue_usd:.2f}\n"
        f"• Total Collected: ${report.total_payment_collected_usd:.2f}\n"
        f"• Net Variance: *${report.variance_usd:.2f}*\n\n"
        f"📝 *Summary:* {report.audit_summary}"
    )
    send_telegram_notification(bot_token, "-5231903935", summary_msg)

    return report

