
import sqlite3
from flask import Flask, request, render_template_string

app = Flask(__name__)

# Vulnerability 1: SQL Injection
@app.route('/login', methods=['POST'])
def login():
    username = request.form['username']
    password = request.form['password']
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    # DANGEROUS: Direct string interpolation
    query = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"
    cursor.execute(query)
    user = cursor.fetchone()
    if user:
        return "Welcome!"
    return "Invalid credentials"

# Vulnerability 2: XSS
@app.route('/search')
def search():
    query = request.args.get('q', '')
    # DANGEROUS: Unescaped user input in HTML
    return render_template_string(f"<h1>Results for: {query}</h1>")

# Vulnerability 3: Command Injection
@app.route('/ping', methods=['POST'])
def ping():
    import subprocess
    host = request.form['host']
    # DANGEROUS: Unsanitized input in shell command
    result = subprocess.check_output(f"ping -c 1 {host}", shell=True)
    return result

# Vulnerability 4: Hardcoded Secret
API_KEY = "sk-1234567890abcdef"
DATABASE_PASSWORD = "super_secret_password_123"

# Vulnerability 5: Insecure Deserialization
@app.route('/load', methods=['POST'])
def load_data():
    import pickle
    data = request.data
    # DANGEROUS: Unpickling untrusted data
    obj = pickle.loads(data)
    return str(obj)

if __name__ == '__main__':
    app.run(debug=True)
