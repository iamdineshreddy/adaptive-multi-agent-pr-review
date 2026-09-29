def get_user(user_id):
    """Fetch user from database."""
    query = f"SELECT * FROM users WHERE id = {user_id}"
    return db.execute(query)

def process_payment(amount, card_number):
    """Process a payment."""
    # No validation on amount
    result = charge_card(card_number, amount)
    return result

def render_page(user_input):
    """Render user page."""
    # XSS vulnerability
    html = f"<div>{user_input}</div>"
    return html

def login(username, password):
    """Authenticate user."""
    # SQL injection vulnerability
    query = f"SELECT * FROM users WHERE username = '{username}' AND password = '{password}'"
    user = db.execute(query)
    if user:
        session['user_id'] = user.id
        return True
    return False
