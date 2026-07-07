"""Generate sizable, realistic sample datasets for the MigrationEase demo.

Dependency-free (stdlib only). Run:  python sample-data/generate.py
Produces four CSVs alongside this file. Seeded, so output is reproducible.
"""

from __future__ import annotations

import csv
import os
import random
from datetime import date, timedelta

random.seed(20260707)
HERE = os.path.dirname(os.path.abspath(__file__))
HOME_STATE = "Maharashtra"  # the company's state (for place-of-supply)

FIRST = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan",
         "Rohan", "Ananya", "Diya", "Aadhya", "Saanvi", "Pari", "Ira", "Myra", "Riya", "Neha",
         "Priya", "Kavya", "Meera", "Rahul", "Amit", "Sneha", "Karan", "Nikhil", "Pooja", "Deepak", "Anjali"]
LAST = ["Sharma", "Verma", "Patel", "Gupta", "Reddy", "Nair", "Iyer", "Rao", "Mehta", "Shah",
        "Singh", "Kumar", "Das", "Bose", "Chowdhury", "Menon", "Pillai", "Joshi", "Kulkarni", "Desai"]
COMPANY_CORE = ["Sunrise", "Global", "Prime", "Apex", "Everest", "Metro", "Royal", "Star", "Unity",
                "Bharat", "Ganga", "Shakti", "Nova", "Zenith", "Orbit", "Pioneer", "Crescent", "Vertex"]
COMPANY_KIND = ["Traders", "Enterprises", "Industries", "Exports", "Distributors", "Agencies",
                "Solutions", "Retail", "Wholesale", "& Sons", "Trading Co", "Marketing"]
CITIES = ["Mumbai", "Pune", "Delhi", "Bengaluru", "Chennai", "Hyderabad", "Kolkata", "Ahmedabad",
          "Surat", "Jaipur", "Lucknow", "Indore", "Nagpur", "Coimbatore", "Kochi", "Bhopal"]
# (state name, GST state code)
STATES = [("Maharashtra", "27"), ("Karnataka", "29"), ("Tamil Nadu", "33"), ("Delhi", "07"),
          ("Gujarat", "24"), ("Telangana", "36"), ("West Bengal", "19"), ("Uttar Pradesh", "09"),
          ("Rajasthan", "08"), ("Kerala", "32"), ("Madhya Pradesh", "23"), ("Punjab", "03")]
# (category, HSN, GST rate %)
PRODUCTS = [("Cotton T-Shirt", "61091000", 5), ("Denim Jeans", "62034200", 12), ("Leather Wallet", "42021190", 18),
            ("Steel Water Bottle", "73239390", 18), ("Ceramic Mug", "69120010", 12), ("Yoga Mat", "39181090", 18),
            ("Bluetooth Speaker", "85182200", 18), ("USB Cable", "85444299", 18), ("Notebook A5", "48201010", 12),
            ("Ball Pen", "96081019", 18), ("Basmati Rice 5kg", "10063020", 5), ("Turmeric Powder", "09103020", 5),
            ("Almonds 500g", "08021200", 12), ("Green Tea", "09102090", 18), ("Face Wash", "34013090", 18),
            ("Hand Sanitizer", "38089400", 18), ("LED Bulb 9W", "85395000", 12), ("Extension Board", "85366990", 18),
            ("Wall Clock", "91051900", 18), ("Bedsheet Double", "63041930", 12)]
UNITS = ["Nos", "Pcs", "Kg", "Box", "Pack", "Set"]


def gstin(state_code: str) -> str:
    L = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    D = "0123456789"
    pan = "".join(random.choice(L) for _ in range(5)) + "".join(random.choice(D) for _ in range(4)) + random.choice(L)
    return f"{state_code}{pan}{random.choice('123456789')}Z{random.choice(L + D)}"


def person() -> str:
    return f"{random.choice(FIRST)} {random.choice(LAST)}"


def company() -> str:
    return f"{random.choice(COMPANY_CORE)} {random.choice(COMPANY_KIND)}"


def rand_date(start=date(2026, 4, 1), days=270) -> str:
    return (start + timedelta(days=random.randint(0, days))).isoformat()


def write(name: str, header: list[str], rows: list[list]):
    path = os.path.join(HERE, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  {name:34} {len(rows):>5} rows")


# ---- 1) Ledgers: customers (Sundry Debtors) + suppliers (Sundry Creditors) ----
def gen_ledgers(n=1000):
    rows = []
    for i in range(n):
        is_customer = random.random() < 0.7
        state, code = random.choice(STATES)
        name = (person() if random.random() < 0.4 else company()) + f" {i+1:04d}"
        rows.append([
            name,
            "Sundry Debtors" if is_customer else "Sundry Creditors",
            random.choice([0, 0, round(random.uniform(500, 90000), 2)]),
            random.choice(["Dr", "Cr"]),
            gstin(code) if random.random() < 0.75 else "",
            state, random.choice(CITIES),
        ])
    write("ledgers.csv", ["Ledger Name", "Under", "Opening Balance", "Opening Dr/Cr", "GSTIN", "State", "City"], rows)


# ---- 2) Stock items with HSN + GST rate ----
def gen_stock_items(n=500):
    rows = []
    for i in range(n):
        base, hsn, rate = random.choice(PRODUCTS)
        rows.append([
            f"{base} - Variant {i+1:03d}", random.choice(UNITS), "Finished Goods",
            hsn, rate, random.randint(0, 500), round(random.uniform(20, 3000), 2),
        ])
    write("stock-items.csv", ["Item Name", "Base Units", "Under", "HSN Code", "GST Rate", "Opening Qty", "Opening Rate"], rows)


# ---- 3) E-commerce sales (the GST engine showcase): computed CGST/SGST/IGST, B2B/B2C, refunds ----
def gen_sales(n=2000):
    rows = []
    for i in range(n):
        state, code = random.choice(STATES)
        _, _, rate = random.choice(PRODUCTS)
        is_b2b = random.random() < 0.35
        is_refund = random.random() < 0.06
        amount = round(random.uniform(250, 25000), 2)
        rows.append([
            f"SO-{i+1:05d}", rand_date(), "Sales", "Sales", amount, "Cr",
            (company() if is_b2b else person()) + (f" {i+1:04d}"),
            rate, state, HOME_STATE,
            gstin(code) if is_b2b else "",         # GSTIN present -> B2B; blank -> B2C
            "refund" if is_refund else "sale",     # refund -> Credit Note
        ])
    write("sales-gst.csv",
          ["Order ID", "Date", "Voucher Type", "Ledger", "Amount", "Dr/Cr", "Party",
           "GST Rate", "Shipping State", "Home State", "Party GSTIN", "Transaction Type"], rows)


# ---- 4) Marketplace settlements (settlement mode): Bank + Commission + Fees + TCS = gross ----
def gen_settlements(n=400):
    rows = []
    markets = ["Amazon Marketplace", "Flipkart Seller", "Meesho", "Myntra", "JioMart"]
    for i in range(n):
        gross = round(random.uniform(2000, 120000), 2)
        commission = round(gross * random.uniform(0.05, 0.15), 2)
        fees = round(gross * random.uniform(0.01, 0.04), 2)
        tcs = round(gross * 0.01, 2)
        rows.append([
            f"STL-{i+1:05d}", rand_date(), "HDFC Bank", random.choice(markets),
            gross, commission, fees, tcs,
        ])
    write("marketplace-settlements.csv",
          ["Settlement ID", "Date", "Ledger", "Party", "Amount", "Commission", "Fees", "TCS"], rows)


if __name__ == "__main__":
    print("Generating sample datasets:")
    gen_ledgers()
    gen_stock_items()
    gen_sales()
    gen_settlements()
    print("Done.")
