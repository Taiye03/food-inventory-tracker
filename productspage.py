import tkinter as tk
from tkinter import messagebox, ttk
import csv
import os
import sys
import subprocess
from datetime import datetime

def open_users():
    """
    Opens the users.py file as a separate process.
    """
    try:
        root.destroy()
        subprocess.Popen(['python3', 'users.py'])  # Adjust path if users.py is in another directory
    except FileNotFoundError:
        messagebox.showerror("Error", "The file 'users.py' was not found.")
FILE_NAME = "products.csv"

# Initialize CSV file if not already created
def init_csv():
    """
    Checks if the CSV file exists; if not, creates it with the necessary headers.
    """
    if not os.path.exists(FILE_NAME):
        with open(FILE_NAME, "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["Batch ID", "Product Name", "Price", "Quantity", "Date Added", "Expiry Date"])
from datetime import datetime, timedelta

# Automated Expiry Notification Function
def notify_expiry():
    """
    Checks for products nearing expiry or already expired and shows notifications.
    """
    if not os.path.exists(FILE_NAME):
        return
    
    notifications = []
    with open(FILE_NAME, "r") as file:
        reader = csv.DictReader(file)
        for row in reader:
            expiry_date = datetime.strptime(row["Expiry Date"], "%Y-%m-%d")
            days_left = (expiry_date - datetime.now()).days
            if days_left < 0:
                notifications.append(f"Product '{row['Product Name']}' has expired!")
            elif days_left <= 3:
                notifications.append(f"Product '{row['Product Name']}' is nearing expiry in {days_left} days!")

    if notifications:
        messagebox.showwarning("Expiry Notifications", "\n".join(notifications))
def logout():
    """
    Quits the current window and launches the login page by re-executing 'loginpage.py'.
    """
    root.quit()  # Quit the tkinter mainloop to close the current window
    os.execv(sys.executable, ['python3'] + ['loginpage.py'])  # Relaunch the login page, replacing current process
    sys.exit()  # Ensure the program terminates properly (this line is technically unreachable)

# Function to refresh the products page
def open_products():
    """
    Quits the current page and re-executes the same script to refresh the page.
    """
    root.quit()
    os.execv(sys.executable, ['python3'] + [sys.argv[0]])
    import random
import string

def generate_batch_id():
    """
    Generates a unique alphanumeric Batch ID using a prefix, date, and random code.
    Format: BATCH-YYYYMMDD-XXX (e.g., BATCH-20241103-001)
    """
    # Get today's date in YYYYMMDD format
    date_prefix = datetime.now().strftime("%Y%m%d")
    
    # Read existing batch IDs from the file
    if not os.path.exists(FILE_NAME):
        return f"BATCH-{date_prefix}-001"  # Default starting ID if the file doesn't exist

    with open(FILE_NAME, "r") as file:
        reader = csv.reader(file)
        next(reader)  # Skip the header row
        batch_ids = [row[0] for row in reader if row]  # Collect all existing Batch IDs

    # Filter batch IDs for today's date
    today_batch_ids = [
        int(batch_id.split('-')[-1]) for batch_id in batch_ids if batch_id.startswith(f"BATCH-{date_prefix}")
    ]

    # Determine the next increment number
    next_number = max(today_batch_ids, default=0) + 1
    return f"BATCH-{date_prefix}-{next_number:03d}"
# Save product to CSV
def save_product():
    """
    Saves product details to the CSV file. Prevents duplicate batch IDs.
    """
    if not os.path.exists(FILE_NAME):
        init_csv()
    batch_id = generate_batch_id()
    product_name = entry_product_name.get()
    price = entry_price.get()
    quantity = entry_quantity.get()
    date_added = entry_date_added.get()
    expiry_date = entry_expiry_date.get()

    if not product_name or not price or not quantity or not date_added or not expiry_date:
        messagebox.showerror("Error", "All fields except Batch ID are required")
        return
    try:
        # Ensure Date Added is a valid date
        datetime.strptime(date_added, "%Y-%m-%d")
        # Ensure Expiry Date is a valid date
        expiry_date_obj = datetime.strptime(expiry_date, "%Y-%m-%d")
        # Logical validation: Expiry Date cannot be before Date Added
        if expiry_date_obj < datetime.strptime(date_added, "%Y-%m-%d"):
            messagebox.showerror("Error", "Expiry Date cannot be before Date Added.")
            return
    except ValueError:
        messagebox.showerror("Error", "Invalid date format. Use YYYY-MM-DD.")
        return

    with open(FILE_NAME, "a", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([batch_id, product_name, price, quantity, date_added, expiry_date])

    messagebox.showinfo("Success", f"Product saved successfully with Batch ID: {batch_id}")
    refresh_table()
    notify_expiry()
    clear_form()

# Update product in CSV
def update_product():
    batch_id = entry_batch_id.get()
    product_name = entry_product_name.get()
    price = entry_price.get()
    quantity = entry_quantity.get()
    date_added = entry_date_added.get()
    expiry_date = entry_expiry_date.get()

    if not batch_id:
        messagebox.showerror("Error", "Batch ID is required for update")
        return

    updated = False
    with open(FILE_NAME, "r") as file:
        reader = csv.reader(file)
        rows = list(reader)

    with open(FILE_NAME, "w", newline="") as file:
        writer = csv.writer(file)
        for row in rows:
            if row[0] == batch_id:
                writer.writerow([batch_id, product_name, price, quantity, date_added, expiry_date])
                updated = True
            else:
                writer.writerow(row)

    if updated:
        messagebox.showinfo("Success", "Product updated successfully")
    else:
        messagebox.showerror("Error", "Batch ID not found")

    refresh_table()
    notify_expiry()
    clear_form()

# Delete product from CSV
def delete_product():
    batch_id = entry_batch_id.get()

    if not batch_id:
        messagebox.showerror("Error", "Batch ID is required for deletion")
        return

    deleted = False
    with open(FILE_NAME, "r") as file:
        reader = csv.reader(file)
        rows = list(reader)

    with open(FILE_NAME, "w", newline="") as file:
        writer = csv.writer(file)
        for row in rows:
            if row[0] != batch_id:
                writer.writerow(row)
            else:
                deleted = True

    if deleted:
        messagebox.showinfo("Success", "Product deleted successfully")
    else:
        messagebox.showerror("Error", "Batch ID not found")

    refresh_table()
    clear_form()

# Select product from table
def select_product(event):
    selected_item = tree.focus()
    if not selected_item:
        return

    data = tree.item(selected_item, "values")
    entry_batch_id.delete(0, tk.END)
    entry_batch_id.insert(0, data[0])
    entry_product_name.delete(0, tk.END)
    entry_product_name.insert(0, data[1])
    entry_price.delete(0, tk.END)
    entry_price.insert(0, data[2])
    entry_quantity.delete(0, tk.END)
    entry_quantity.insert(0, data[3])
    entry_date_added.delete(0, tk.END)
    entry_date_added.insert(0, data[4])
    entry_expiry_date.delete(0, tk.END)
    entry_expiry_date.insert(0, data[5])

# Clear input fields
def clear_form():
    entry_batch_id.delete(0, tk.END)
    entry_product_name.delete(0, tk.END)
    entry_price.delete(0, tk.END)
    entry_quantity.delete(0, tk.END)
    entry_date_added.delete(0, tk.END)
    entry_expiry_date.delete(0, tk.END)

# Refresh table view
def refresh_table():
    for row in tree.get_children():
        tree.delete(row)

    if os.path.exists(FILE_NAME):
        with open(FILE_NAME, "r") as file:
            reader = csv.reader(file)
            next(reader)  # Skip the header
            for row in reader:
                tree.insert("", tk.END, values=row)

import shutil  # Add this import for file copying

def export_to_excel():
    """
    Copies the contents of the products.csv file to a new file named products_exported.csv.
    """
    if os.path.exists(FILE_NAME):
        # Copy contents of products.csv to products_exported.csv
        shutil.copy(FILE_NAME, "products_exported.csv")
        messagebox.showinfo("Success", "Data exported to products_exported.csv")
    else:
        messagebox.showerror("Error", "No data to export")

# Initialize CSV
init_csv()

# UI Setup
root = tk.Tk()
root.title("FoodHeroes Stock Management System")
root.attributes('-topmost', False)
# Center the window on the screen
window_width = 800
window_height = 500
screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()
x = (screen_width - window_width) // 2  # Calculate x coordinate
y = (screen_height - window_height) // 2  # Calculate y coordinate
root.geometry(f"{window_width}x{window_height}+{x}+{y}")  # Centered window

# Sidebar
sidebar = tk.Frame(root, width=400, bg="#2b2b2b")
sidebar.pack(side="left", fill="y")

# Sidebar Buttons
products_button = tk.Button(sidebar, text="Products", command=open_products, bg="#FF5555", fg="black", font=("Arial", 12))
products_button.pack(pady=20, padx=10, fill="x")

users_button = tk.Button(sidebar, text="Users", command=open_users, bg="#FF5555", fg="black", font=("Arial", 12))
users_button.pack(pady=10, padx=10, fill="x")

logout_button = tk.Button(sidebar, text="Logout", command=logout, bg="#FF5555", fg="black", font=("Arial", 12))
logout_button.pack(pady=10, padx=10, fill="x",side="bottom")

def validate_numeric_input(value):
    """
    Validates that the input is a positive numeric value (integer or float).
    Returns True if valid, False otherwise.
    """
    if value == "":  # Allow empty input
        return True
    try:
        float(value)  # Check if the value can be converted to a float
        return True
    except ValueError:
        return False
validate_command = root.register(validate_numeric_input)
# Main Content Frame
main_frame = tk.Frame(root, bg="#2b2b2b")
main_frame.pack(side="right", fill="both", expand=True)

# Input Fields and Buttons Frame
input_button_frame = tk.Frame(main_frame, bg="#2b2b2b")
input_button_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
# Entry Fields
tk.Label(main_frame, text="Batch ID").grid(row=0, column=0, padx=10, pady=5, sticky="e")
entry_batch_id = tk.Entry(main_frame)
entry_batch_id.grid(row=0, column=1, padx=10, pady=5)

tk.Label(main_frame, text="Product Name").grid(row=1, column=0, padx=10, pady=5, sticky="e")
entry_product_name = tk.Entry(main_frame)
entry_product_name.grid(row=1, column=1, padx=10, pady=5)

tk.Label(main_frame, text="Price").grid(row=2, column=0, padx=10, pady=5, sticky="e")
entry_price = tk.Entry(main_frame,validate="key", validatecommand=(validate_command, "%P"))
entry_price.grid(row=2, column=1, padx=10, pady=5)

tk.Label(main_frame, text="Quantity").grid(row=3, column=0, padx=10, pady=5, sticky="e")
entry_quantity = tk.Entry(main_frame, validate="key", validatecommand=(validate_command, "%P"))
entry_quantity.grid(row=3, column=1, padx=10, pady=5)

tk.Label(main_frame, text="Date Added").grid(row=4, column=0, padx=10, pady=5, sticky="e")
entry_date_added = tk.Entry(main_frame)
entry_date_added.grid(row=4, column=1, padx=10, pady=5)

tk.Label(main_frame, text="Expiry Date").grid(row=5, column=0, padx=10, pady=5, sticky="e")
entry_date_added.insert(0, datetime.now().strftime("%Y-%m-%d"))
entry_expiry_date = tk.Entry(main_frame)
entry_expiry_date.grid(row=5, column=1, padx=10, pady=5)

# Buttons Frame
button_frame = tk.Frame(main_frame)
button_frame.grid(row=6, column=0, columnspan=2, pady=10)

tk.Button(button_frame, text="Save", command=save_product).grid(row=0, column=0, padx=5)
tk.Button(button_frame, text="Update", command=update_product).grid(row=0, column=1, padx=5)
tk.Button(button_frame, text="Delete", command=delete_product).grid(row=0, column=2, padx=5)
tk.Button(button_frame, text="Clear", command=clear_form).grid(row=0, column=3, padx=5)
tk.Button(button_frame, text="Export Excel", command=export_to_excel).grid(row=0, column=4, padx=5)


# TreeView for Displaying Products
columns = ("Batch ID", "Product Name", "Price", "Quantity", "Date Added", "Expiry Date")
tree = ttk.Treeview(main_frame, columns=columns, show="headings", height=20)
tree.grid(row=7, column=0, columnspan=2, padx=10, pady=10,sticky="nsew")

for col in columns:
    tree.heading(col, text=col)
    tree.column(col, width=200)

main_frame.grid_rowconfigure(7, weight=1)
main_frame.grid_columnconfigure(0, weight=1)
main_frame.grid_columnconfigure(1, weight=1)
tree.bind("<Double-1>", select_product)

# Initial Data Load
refresh_table()
root.after(2000, notify_expiry)
if __name__ == "__main__":
    root.mainloop()
