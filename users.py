import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
import os
import sys
import csv
import subprocess


# CSV File for storing user details
USERS_FILE = "users.csv"

# Ensure the users file exists
def init_users_csv():
    """
    Initialize the users CSV file if it doesn't exist.
    """
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, mode="w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["username", "fullname", "role", "email", "password"])  # Add headers

# Add User Functionality
def add_user():
    """
    Opens a popup to input and save new user details.
    """
    def is_valid_email(email):
        """
        Validates the email format.
        - Must contain '@' and '.'
        - '@' must appear before the last '.'
        """
        if "@" in email and "." in email:
            at_index = email.index("@")
            last_dot_index = email.rindex(".")
            # Ensure '@' appears before the last '.' and there is at least one character between them
            if at_index < last_dot_index and last_dot_index - at_index > 1:
                return True
        return False
    
    def save_new_user():
        """
        Saves the new user to the CSV file.
        """
        username = username_entry.get()
        fullname = fullname_entry.get()
        role = role_combobox.get()
        email = email_entry.get()
        password = password_entry.get()

        if not username or not fullname or not role or not email or not password:
            messagebox.showerror("Error", "All fields are required!")
            return
        if not is_valid_email(email):
            messagebox.showerror("Error", "Invalid email format! Ensure it contains '@' and '.' in the correct order.")
            return

        # Append user details to the CSV file
        with open(USERS_FILE, mode="a", newline="") as file:
            writer = csv.writer(file)
            writer.writerow([username, fullname, role, email, password])

        messagebox.showinfo("Success", f"User {username} added successfully!")
        popup.destroy()
        refresh_users_table()

    # Create a popup window
    popup = tk.Toplevel()
    popup.title("Add New User")
    popup.geometry("500x500")

    # Input fields for the new user
    tk.Label(popup, text="Username:").pack(pady=5)
    username_entry = tk.Entry(popup)
    username_entry.pack(pady=5)

    tk.Label(popup, text="Full Name:").pack(pady=5)
    fullname_entry = tk.Entry(popup)
    fullname_entry.pack(pady=5)

    tk.Label(popup, text="Role:").pack(pady=5)
    role_combobox = ttk.Combobox(popup, values=["Admin", "User"], state="readonly")
    role_combobox.pack(pady=5)

    tk.Label(popup, text="Email:").pack(pady=5)
    email_entry = tk.Entry(popup)
    email_entry.pack(pady=5)
    

    tk.Label(popup, text="Password:").pack(pady=5)
    password_entry = tk.Entry(popup, show="*")
    password_entry.pack(pady=5)

    # Save Button
    save_button = tk.Button(popup, text="Save", command=save_new_user)
    save_button.pack(pady=20)

# Refresh Users Table
def refresh_users_table():
    """
    Refreshes the users table with the latest data from the CSV file.
    """
    for row in tree.get_children():
        tree.delete(row)

    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, mode="r") as file:
            reader = csv.reader(file)
            next(reader)  # Skip the header
            for row in reader:
                tree.insert("", tk.END, values=row)

def open_products():
    """
    Handles navigation to the Products page.
    Closes the current page and opens the Products page.
    """
    try:
        root.destroy()  # Close the current user page
        subprocess.Popen(['python3', 'productspage.py'])  # Adjust path if 'productspage.py' is in another directory
    except FileNotFoundError:
        messagebox.showerror("Error", "The file 'productspage.py' was not found.")


# Function to refresh the users page
def users():
    """
    Quits the current page and re-executes the same script to refresh the page.
    """
    root.quit()
    os.execv(sys.executable, ['python3'] + [sys.argv[0]])

def logout():
    """
    Quits the current window and launches the login page by re-executing 'loginpage.py'.
    """
    root.quit()  # Quit the tkinter mainloop to close the current window
    os.execv(sys.executable, ['python3'] + ['loginpage.py'])  # Relaunch the login page, replacing current process
    sys.exit()  # Ensure the program terminates properly (this line is technically unreachable)

# Main Window Setup
root = tk.Tk()
root.title("Foodheroes Stock Management System - Users Page")
root.geometry("800x500")  # Window size
root.configure(bg="black")

# Sidebar
sidebar = tk.Frame(root, bg="#2b2b2b", width=400)
sidebar.pack(side="left", fill="y")

# Sidebar Buttons
btn_products = tk.Button(sidebar, text="Products", bg="#F3F3F3", fg="black", font=("Arial", 14),
                          bd=0, padx=20, anchor="w", command=open_products)
btn_products.pack(fill="x", pady=5)

btn_users = tk.Button(sidebar, text="Users", bg="#F3F3F3", fg="black", font=("Arial", 14),
                       bd=0, padx=20, anchor="w")
btn_users.pack(fill="x", pady=5)

btn_logout = tk.Button(sidebar, text="LogOut", bg="#F3F3F3", fg="black", font=("Arial", 14),
                        bd=0, padx=20, anchor="w", command=logout)
btn_logout.pack(side="bottom", fill="x", pady=10)

# Main Content Area
main_content = tk.Frame(root, bg="#2b2b2b", padx=10, pady=10)
main_content.pack(side="right", fill="both", expand=True)

# Header Section
header = tk.Frame(main_content, bg="#2b2b2b")
header.pack(fill="x")


add_user_btn = tk.Button(header, text="Add User", bg="#FFD700", fg="black", font=("Arial", 14), command=add_user)
add_user_btn.pack(side="right", padx=10)

# User Table Section
table_frame = tk.Frame(main_content, bg="#2b2b2b", pady=20)
table_frame.pack(fill="both", expand=True)

# Table Header
header_labels = ["UserName", "FullName", "Role", "Email"]
tree = ttk.Treeview(table_frame, columns=header_labels, show="headings", height=20)

# Define Table Columns
for label in header_labels:
    tree.heading(label, text=label)
    tree.column(label, width=150, anchor="center")

# Attach Table to the Frame
tree.pack(fill="both", expand=True)
# Load user data into the table on application start
refresh_users_table()
# Run Main Loop
root.mainloop()