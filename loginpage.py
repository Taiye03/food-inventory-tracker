# Import necessary libraries
import tkinter
from tkinter import messagebox
import os
import subprocess
import csv

# Path to the CSV file
USERS_FILE = "users.csv"
# Function to open the stock management system
# Ensure the users file exists
def init_users_csv(): 
    """
    Ensures the users.csv file exists. If not, creates it with default credentials.
    """
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, mode="w", newline="") as file:
            writer = csv.writer(file)
def open_stock_management():
    """
    Opens the stock management system as a new process.
    """
    subprocess.Popen(["python3", "productspage.py"])

# Function to handle login
def login():
    """
    Handles the login logic:
    - If valid credentials, opens the stock management system and closes login page.
    - If invalid credentials, displays an error message.
    """

    username = username_entry.get()
    password = password_entry.get()

    # Check credentials from the CSV file
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, mode="r") as file:
            reader = csv.DictReader(file)
            for row in reader:
                if row["username"] == username and row["password"] == password:
                    messagebox.showinfo("Login Success", "You successfully logged in.")
                    window.destroy()  # Close the login window
                    open_stock_management()  # Open the product management system
                    return
    # If no match is found, show error
    messagebox.showerror("Error", "Invalid username or password.")

# Function to remove "always on top" when focus is lost
def remove_topmost(event=None):
    """
    Removes the "always on top" behavior when the window loses focus.
    """
    window.attributes('-topmost', False)
# Initialize the users.csv file
init_users_csv()

# Create the main Tkinter window
window = tkinter.Tk()
window.title("Foodheroes Stock Management System")
window.geometry('800x500')  # Set the window size
window.configure(bg='#333333')  # Set the background color

# Center the window on the screen
window_width = 800
window_height = 500
screen_width = window.winfo_screenwidth()
screen_height = window.winfo_screenheight()
x = (screen_width - window_width) // 2
y = (screen_height - window_height) // 2
window.geometry(f'{window_width}x{window_height}+{x}+{y}')  # Center the window

# Bind the FocusOut event to the remove_topmost function
window.bind("<FocusOut>", remove_topmost)

# Create a frame to organize the widgets
frame = tkinter.Frame(bg='#333333')

# Add a "Login" label to the frame
login_label = tkinter.Label(
    frame, text="Login", bg='#333333', fg="white", font=("Arial", 30)
)

# Add a label and entry field for the username
username_label = tkinter.Label(
    frame, text="Username", bg='#333333', fg="#FFFFFF", font=("Arial", 16)
)
username_entry = tkinter.Entry(frame, font=("Arial", 16))  # Input field for the username

# Add a label and entry field for the password
password_label = tkinter.Label(
    frame, text="Password", bg='#333333', fg="#FFFFFF", font=("Arial", 16)
)
password_entry = tkinter.Entry(
    frame, show="*", font=("Arial", 16)
)  # Input field for the password (masked with "*")

# Add a login button to the frame
login_button = tkinter.Button(
    frame, text="Login", bg="#FF3399", fg="black", font=("Arial", 16), command=login
)

# Arrange the widgets in a grid layout
login_label.grid(row=0, column=0, columnspan=2, sticky="news", pady=40)
username_label.grid(row=1, column=0)  # Place the username label in row 1, column 0
username_entry.grid(row=1, column=1, pady=20)  # Place the username entry field beside it
password_label.grid(row=2, column=0)  # Place the password label in row 2, column 0
password_entry.grid(row=2, column=1, pady=20)  # Place the password entry field beside it
login_button.grid(row=3, column=0, columnspan=2, pady=30)  # Place the login button below

# Add the frame to the window
frame.pack()

# Start the Tkinter main event loop
window.mainloop()
