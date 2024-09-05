from flask import Flask, render_template, request, redirect, url_for, session
from flask_login import login_user, logout_user, login_required, current_user
from flask_bcrypt import check_password_hash
from flask_mail import Mail, Message
import json, requests, random, datetime
from app import get_app, db, bcrypt, mail, login_manager
from models import Users, Company, Create_Shipping_Request

# Initialize the Flask app
app = get_app()

# Set up datetime for current operations
now = datetime.datetime.now()

# User loader function for Flask-Login
@login_manager.user_loader
def load_user(user_id):
    """
    Loads a user by user ID for Flask-Login session management.
    
    Args:
        user_id (int): ID of the user to load.
    
    Returns:
        Users: User object if found, None otherwise.
    """
    return Users.query.get(int(user_id))

class FileDatabase:
    """
    A class to handle user session persistence in a JSON file for 'Remember Me' functionality.
    Stores the email, password, and remember_me flag.

    Attributes:
        user (dict): A dictionary storing user session details.
    """

    def __init__(self):
        """Initializes the FileDatabase object by loading user details from the file."""
        self.user = {"email": None, "password": None, "remember_me": False}
        try:
            with open('db.json', 'r+') as f:
                self.user = json.load(f)
        except FileNotFoundError:
            with open('db.json', 'w') as f:
                json.dump(self.user, f)

    def save(self):
        """Saves the current user session details into the 'db.json' file."""
        with open('db.json', 'w') as f:
            json.dump(self.user, f)

    def login(self, email, password, remember_me):
        """
        Logs in a user by storing their session information in the JSON file.

        Args:
            email (str): User's email.
            password (str): User's hashed password.
            remember_me (bool): Whether the user chose 'Remember Me'.
        """
        self.user.update({"email": email, "password": password, "remember_me": remember_me})
        self.save()

    def logout(self):
        """Clears the stored session information from the JSON file."""
        self.user.update({"email": None, "password": None, "remember_me": False})
        self.save()

    def is_remember_me(self) -> bool:
        """
        Checks if the 'Remember Me' option was selected.

        Returns:
            bool: True if 'Remember Me' is selected, False otherwise.
        """
        return self.user.get('remember_me', False)

# Instantiate file-based database for user persistence
remember_me_db = FileDatabase()

@app.route("/")
@login_required
def home():
    """
    Home route for the logged-in user.

    Returns:
        Response: Renders the homepage template with optional flash messages.
    """
    message = request.args.get('message')
    msg_type = request.args.get('msg_type')
    return render_template('staff/customers.html', message=message, msg_type=msg_type)

@app.route("/login/", methods=['GET', 'POST'])
def login():
    """
    User login route.

    GET: Renders the login page.
    POST: Validates and logs in the user if the credentials are correct.

    Returns:
        Response: Redirects to the home page if login is successful, otherwise reloads login page.
    """
    if request.method == 'GET':
        if current_user.is_authenticated:
            return redirect(url_for("home"))
        elif remember_me_db.is_remember_me():
            user = Users.query.filter_by(email=remember_me_db.user['email']).first()
            if user and check_password_hash(user.hashed_password, remember_me_db.user['password']):
                login_user(user)
                return redirect(url_for("home"))
        return render_template('login.html')

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        remember_me = bool(request.form.get('remember_me'))

        user = Users.query.filter_by(email=email).first()
        if user and check_password_hash(user.hashed_password, password):
            login_user(user)
            remember_me_db.login(email, password, remember_me)
            return redirect(url_for("home"))
        return redirect(url_for("login", message="Invalid email or password", msg_type="danger"))

@app.route("/signup/", methods=['GET', 'POST'])
def signup():
    """
    User signup route.

    GET: Renders the signup page.
    POST: Registers a new user if the details are valid.

    Returns:
        Response: Redirects to login page upon successful signup or reloads signup page with an error.
    """
    if request.method == 'GET':
        return render_template('signup.html')

    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if Users.query.filter_by(email=email).first():
            return redirect(url_for("signup", message="User Already Exists", msg_type="warning"))

        if password != confirm_password:
            return redirect(url_for("signup", message="Passwords do not match", msg_type="danger"))

        # Fetching location information
        response = requests.get('https://httpbin.org/ip')
        public_ip = response.json().get('origin')
        location_response = requests.get(f'https://ipinfo.io/{public_ip}/json')
        location_data = location_response.json()

        # Create and save new user
        new_user = Users(
            username=name,
            email=email,
            hashed_password=bcrypt.generate_password_hash(password),
            role='staff',
            last_login=now,
            created_at=now,
            last_device_info=json.dumps(location_data)
        )
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for("login", message="Registration Successful, Please Login", msg_type="success"))

@app.route("/logout/")
@login_required
def logout():
    """
    Logs out the current user and clears the session.

    Returns:
        Response: Redirects to the login page.
    """
    logout_user()
    remember_me_db.logout()
    return redirect(url_for('login'))

@app.route("/customers/")
@login_required
def customers():
    """
    Displays the customer management page.

    Returns:
        Response: Renders the customers management template.
    """
    return render_template('staff/customers.html')

@app.route('/add_company/', methods=['GET', 'POST'])
@login_required
def add_company():
    """
    Handles the addition of a new company.

    GET: Displays the form for adding a new company.
    POST: Creates a new company in the database.

    Returns:
        Response: Redirects to add user to company page or reloads the form upon success.
    """
    if request.method == 'GET':
        companies = Company.query.all()
        return render_template('add_company.html', companies=companies)

    company_name = request.form.get('company_name')
    new_company = Company(company_name=company_name)
    db.session.add(new_company)
    db.session.commit()
    return redirect(url_for('add_user_to_company', message='Company added successfully', msg_type='success'))

@app.route('/add_user_to_company/', methods=['GET', 'POST'])
@login_required
def add_user_to_company():
    """
    Associates the current user with a company.

    GET: Displays the form for selecting a company.
    POST: Assigns the user to the selected company.

    Returns:
        Response: Redirects to the home page after successful assignment.
    """
    if request.method == 'GET':
        companies = Company.query.all()
        return render_template('company.html', companies=companies)

    company_id = request.form.get('company_id')
    if current_user.is_authenticated:
        current_user.staff_of_company = int(company_id)
        db.session.commit()
        return redirect(url_for('home', message='Company assigned successfully', msg_type='success'))

@app.route("/shipping_request/", methods=['GET', 'POST'])
@login_required
def create_shipping_request():
    """
    Creates a new shipping request.

    GET: Displays the form for creating a shipping request.
    POST: Saves the shipping request data to the database.

    Returns:
        Response: Renders the shipping request creation page.
    """
    shipping_request_no = ''.join(random.choices('0123456789', k=10))

    if request.method == 'POST':
        shipping_data = {key: request.form[key] for key in request.form}
        shipping_data['shipping_request_number'] = shipping_request_no
        new_request = Create_Shipping_Request(**shipping_data)
        db.session.add(new_request)
        db.session.commit()
        return render_template('staff/create_shipping_request.html', message="Request created", msg_type="success", shipping_request_no=shipping_request_no)

    return render_template('staff/create_shipping_request.html', shipping_request_no=shipping_request_no)

@app.route("/shipping_request/view_previous/")
@login_required
def list_shipping_request():
    """
    Lists previous shipping requests.

    Returns:
        Response: Renders the list of previous shipping requests.
    """
    previous_requests = Create_Shipping_Request.query.all()
    return render_template('staff/list_shipping_request.html', previous_requests=previous_requests)

@app.route("/shipping_request/view_previous/<int:request_no>/")
@login_required
def view_shipping_request(request_no):
    """
    Views details of a specific shipping request.

    Args:
        request_no (int): ID of the shipping request to view.

    Returns:
        Response: Renders the detailed shipping request view page.
    """
    shipping_request = Create_Shipping_Request.query.get_or_404(request_no)
    return render_template('staff/view_shipping_request.html', shipping_request=shipping_request)

# Running the app when this script is executed
if __name__ == "__main__":
    app.run(debug=True)
