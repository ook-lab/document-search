from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import os

def create_dummy_pdf(filename):
    c = canvas.Canvas(filename, pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(100, 800, "Dummy Invoice for Testing")
    
    c.setFont("Helvetica", 10)
    c.drawString(100, 780, "Date: 2026-04-29")
    c.drawString(100, 765, "Customer: Test User")
    
    # Table Header
    c.drawString(100, 730, "Description")
    c.drawString(300, 730, "Qty")
    c.drawString(400, 730, "Price")
    c.drawString(500, 730, "Total")
    
    # Table Content
    c.drawString(100, 710, "AI Analysis Service")
    c.drawString(300, 710, "1")
    c.drawString(400, 710, "$100.00")
    c.drawString(500, 710, "$100.00")
    
    c.drawString(100, 690, "System Integration")
    c.drawString(300, 690, "5")
    c.drawString(400, 690, "$50.00")
    c.drawString(500, 690, "$250.00")
    
    c.save()

if __name__ == "__main__":
    create_dummy_pdf("test_invoice.pdf")
    print(f"Created {os.path.abspath('test_invoice.pdf')}")
