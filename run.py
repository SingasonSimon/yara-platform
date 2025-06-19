from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_mysqldb import MySQL
from flask_bcrypt import Bcrypt
from functools import wraps
import os
from werkzeug.utils import secure_filename
from datetime import datetime, timezone, timedelta
import json
from flask import jsonify
import random
import string
import calendar
import imagehash
from PIL import Image

# Initialize the app
app = Flask(__name__, template_folder='app/templates', static_folder='app/static')

# --- Configuration ---
app.config['MYSQL_HOST'] = '127.0.0.1'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = '' 
app.config['MYSQL_DB'] = 'yara_db'
app.config['MYSQL_PORT'] = 3307
app.config['MYSQL_CURSORCLASS'] = 'DictCursor' 
app.config['MYSQL_CHARSET'] = 'utf8mb4'
app.config['SECRET_KEY'] = 'a_super_secret_key_change_later_12345'
app.config['UPLOAD_FOLDER'] = 'app/static/profile_pics'
app.config['PORTFOLIO_UPLOAD_FOLDER'] = 'app/static/portfolio_uploads'
app.config['TEMP_UPLOAD_FOLDER'] = 'app/static/temp_uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# Initialize extensions
mysql = MySQL(app)
bcrypt = Bcrypt(app)

# --- Helper Function & Decorators ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_logged_in(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if 'loggedin' in session:
            return f(*args, **kwargs)
        else:
            flash('Please login to view this page.', 'danger')
            return redirect(url_for('login'))
    return wrap

def is_professional(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if 'loggedin' in session and session['user_type'] == 'professional':
            return f(*args, **kwargs)
        else:
            flash('You must be a non-professional to access this page.', 'danger')
            return redirect(url_for('dashboard')) # Redirect non-professionals
    return wrap

# --- Auth & Core Routes ---
@app.route('/')
def home():
    if 'loggedin' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        user_type = request.form['user_type']
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        try:
            cur = mysql.connection.cursor()
            cur.execute("INSERT INTO users(username, email, password_hash, user_type) VALUES(%s, %s, %s, %s)", (username, email, hashed_password, user_type))
            mysql.connection.commit()
            cur.close()
            flash('You are now registered! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash(f'An error occurred. The username or email might already be taken.', 'danger')
            return redirect(url_for('register'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password_candidate = request.form['password']
        cur = mysql.connection.cursor()
        result = cur.execute("SELECT * FROM users WHERE username = %s", [username])
        if result > 0:
            user = cur.fetchone()
            cur.close()
            if bcrypt.check_password_hash(user['password_hash'], password_candidate):
                session['loggedin'] = True
                session['id'] = user['id']
                session['username'] = user['username']
                session['user_type'] = user['user_type']
                flash('You have successfully logged in!', 'success')
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid login credentials.', 'danger')
                return redirect(url_for('login'))
        else:
            cur.close()
            flash('Invalid login credentials.', 'danger')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
@is_logged_in
def logout():
    session.clear()
    flash('You are now logged out.', 'success')
    return redirect(url_for('login'))

# --- Dashboard Routes ---
@app.route('/dashboard')
@is_logged_in
def dashboard():
    if session['user_type'] == 'professional':
        return redirect(url_for('edit_profile'))
    else:
        # --- NEW LOGIC FOR CLIENT DASHBOARD ---
        cur = mysql.connection.cursor()
        # Fetch 3 random professionals who have a profile picture to feature them
        cur.execute("""
            SELECT u.username, pp.business_name, pp.location, pp.profile_pic_filename
            FROM users u
            JOIN professional_profiles pp ON u.id = pp.user_id
            WHERE u.user_type = 'professional' AND pp.profile_pic_filename IS NOT NULL
            ORDER BY RAND()
            LIMIT 3
        """)
        featured_professionals = cur.fetchall()
        cur.close()
        return render_template('client_dashboard.html', featured=featured_professionals)
    
@app.route('/dashboard/profile', methods=['GET', 'POST'])
@is_logged_in
@is_professional
def edit_profile():
    user_id = session['id']
    cur = mysql.connection.cursor()

    if request.method == 'POST':
        # --- Handle Profile Details ---
        business_name = request.form['business_name']
        phone_number = request.form['phone_number']
        location = request.form['location']
        bio = request.form['bio']
        
        cur.execute("""
            INSERT INTO professional_profiles (user_id, business_name, phone_number, location, bio) 
            VALUES (%s, %s, %s, %s, %s) 
            ON DUPLICATE KEY UPDATE 
            business_name = VALUES(business_name), phone_number = VALUES(phone_number), 
            location = VALUES(location), bio = VALUES(bio)
        """, (user_id, business_name, phone_number, location, bio))
        mysql.connection.commit()

        # --- Handle Availability Schedule ---
        days_of_week = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        availability_data = {}
        for day in days_of_week:
            is_available = f"{day}_available" in request.form
            start_time = request.form.get(f"{day}_start")
            end_time = request.form.get(f"{day}_end")
            
            # --- NEW: Server-side validation ---
            if is_available and (not start_time or not end_time):
                flash(f'If {day.capitalize()} is checked, you must provide both a start and end time.', 'danger')
                # Fetch profile data again to re-render the page with the error
                cur.execute("SELECT * FROM professional_profiles WHERE user_id = %s", [user_id])
                profile = cur.fetchone()
                cur.close()
                return render_template('edit_profile.html', profile=profile)

            availability_data[f"{day}_start"] = start_time if is_available else None
            availability_data[f"{day}_end"] = end_time if is_available else None
        
        # Update query remains the same
        cur.execute("""
            UPDATE professional_profiles SET
                monday_start = %s, monday_end = %s, tuesday_start = %s, tuesday_end = %s,
                wednesday_start = %s, wednesday_end = %s, thursday_start = %s, thursday_end = %s,
                friday_start = %s, friday_end = %s, saturday_start = %s, saturday_end = %s,
                sunday_start = %s, sunday_end = %s
            WHERE user_id = %s
        """, (
            availability_data['monday_start'], availability_data['monday_end'],
            availability_data['tuesday_start'], availability_data['tuesday_end'],
            availability_data['wednesday_start'], availability_data['wednesday_end'],
            availability_data['thursday_start'], availability_data['thursday_end'],
            availability_data['friday_start'], availability_data['friday_end'],
            availability_data['saturday_start'], availability_data['saturday_end'],
            availability_data['sunday_start'], availability_data['sunday_end'],
            user_id
        ))
        mysql.connection.commit()

        # --- Handle File Upload ---
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = str(user_id) + '_' + filename
                file.path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(file.path)
                cur.execute("UPDATE professional_profiles SET profile_pic_filename = %s WHERE user_id = %s", (unique_filename, user_id))
                mysql.connection.commit()

        cur.close()
        flash('Your profile and availability have been updated!', 'success')
        return redirect(url_for('edit_profile'))
    
    # GET request logic is unchanged
    cur.execute("SELECT * FROM professional_profiles WHERE user_id = %s", [user_id])
    profile = cur.fetchone()
    cur.close()
    
    return render_template('edit_profile.html', profile=profile)

@app.route('/dashboard/services')
@is_logged_in
@is_professional
def manage_services():
    user_id = session['id']
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM services WHERE professional_id = %s ORDER BY service_name", [user_id])
    services = cur.fetchall()
    cur.close()
    return render_template('manage_services.html', services=services)

# ---manage_appointments FUNCTION ---
@app.route('/dashboard/appointments')
@is_logged_in
@is_professional
def manage_appointments():
    professional_id = session['id']
    cur = mysql.connection.cursor()

    # Query for Pending Appointments
    cur.execute("""
        SELECT a.id, a.appointment_datetime, a.status, s.service_name, c.username as client_username
        FROM appointments a
        JOIN services s ON a.service_id = s.id
        JOIN users c ON a.client_id = c.id
        WHERE a.professional_id = %s AND a.status = 'pending'
        ORDER BY a.appointment_datetime ASC
    """, (professional_id,))
    pending_appointments = cur.fetchall()

    # Query for Confirmed Appointments (including payment_status)
    cur.execute("""
        SELECT a.id, a.appointment_datetime, a.status, a.payment_status, s.service_name, c.username as client_username
        FROM appointments a
        JOIN services s ON a.service_id = s.id
        JOIN users c ON a.client_id = c.id
        WHERE a.professional_id = %s AND a.status = 'confirmed'
        ORDER BY a.appointment_datetime ASC
    """, (professional_id,))
    confirmed_appointments = cur.fetchall()
    
    # THIS IS THE QUERY THAT WAS MISSING: Fetch History (Completed and Cancelled)
    cur.execute("""
        SELECT a.id, a.appointment_datetime, a.status, s.service_name, c.username as client_username
        FROM appointments a
        JOIN services s ON a.service_id = s.id
        JOIN users c ON a.client_id = c.id
        WHERE a.professional_id = %s AND a.status IN ('completed', 'cancelled')
        ORDER BY a.appointment_datetime DESC
    """, [professional_id])
    history_appointments = cur.fetchall()

    cur.close()

    eat_timezone = timezone(timedelta(hours=3))
    now_in_eat = datetime.now(eat_timezone).replace(tzinfo=None)

    return render_template(
        'dashboard_appointments.html', 
        pending_appointments=pending_appointments, 
        confirmed_appointments=confirmed_appointments,
        history_appointments=history_appointments, # Pass history to template
        now=now_in_eat
    )
# --- Service & Public Profile Routes ---
@app.route('/add_service', methods=['POST'])
@is_logged_in
@is_professional
def add_service():
    professional_id = session['id']
    service_name = request.form['service_name']
    price = request.form['price']
    duration_minutes = request.form['duration_minutes']
    description = request.form['description']
    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO services(professional_id, service_name, price, duration_minutes, description) VALUES (%s, %s, %s, %s, %s)", (professional_id, service_name, price, duration_minutes, description))
    mysql.connection.commit()
    cur.close()
    flash('New service has been added!', 'success')
    return redirect(url_for('manage_services'))

@app.route('/delete_service/<int:service_id>', methods=['POST'])
@is_logged_in
@is_professional
def delete_service(service_id):
    professional_id = session['id']
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM services WHERE id = %s AND professional_id = %s", (service_id, professional_id))
    mysql.connection.commit()
    cur.close()
    flash('Service has been deleted.', 'success')
    return redirect(url_for('manage_services'))

@app.route('/profile/<string:username>')
def view_profile(username):
    cur = mysql.connection.cursor()
    # Query remains the same, as it already fetches the portfolio_gallery column
    cur.execute("SELECT u.id, u.username, u.email, u.user_type, pp.* FROM users u LEFT JOIN professional_profiles pp ON u.id = pp.user_id WHERE u.username = %s", [username])
    profile = cur.fetchone()
    
    services = []
    portfolio_gallery = [] # Initialize empty list

    if profile:
        # If a portfolio gallery exists and is not empty, load it from the JSON string
        if profile.get('portfolio_gallery'):
            portfolio_gallery = json.loads(profile['portfolio_gallery'])

        if profile['user_type'] == 'professional':
            # Fetch services for that professional
            cur.execute("SELECT * FROM services WHERE professional_id = %s ORDER BY service_name", [profile['id']])
            services = cur.fetchall()
        
    cur.close()
    
    if profile:
        return render_template('profile_view.html', profile=profile, services=services, portfolio_gallery=portfolio_gallery)
    else:
        flash('User not found.', 'danger')
        return redirect(url_for('dashboard'))
    
@app.route('/search')
@is_logged_in
def search():
    query = request.args.get('q', '')
    if not query:
        return redirect(url_for('dashboard'))
    search_term = f"%{query}%"
    cur = mysql.connection.cursor()
    cur.execute("SELECT DISTINCT u.id, u.username, pp.business_name, pp.location, pp.profile_pic_filename FROM users u LEFT JOIN professional_profiles pp ON u.id = pp.user_id LEFT JOIN services s ON u.id = s.professional_id WHERE u.user_type = 'professional' AND ( pp.business_name LIKE %s OR u.username LIKE %s OR pp.location LIKE %s OR s.service_name LIKE %s )", (search_term, search_term, search_term, search_term))
    results = cur.fetchall()
    cur.close()
    return render_template('search_results.html', results=results, query=query)

@app.route('/book/<int:service_id>', methods=['GET', 'POST'])
@is_logged_in 
def book_appointment(service_id):
    if session['user_type'] == 'professional':
        flash('Professionals cannot book appointments.', 'danger')
        return redirect(url_for('dashboard'))
    cur = mysql.connection.cursor()
    cur.execute("SELECT s.*, u.username, pp.business_name, pp.monday_start, pp.monday_end, pp.tuesday_start, pp.tuesday_end, pp.wednesday_start, pp.wednesday_end, pp.thursday_start, pp.thursday_end, pp.friday_start, pp.friday_end, pp.saturday_start, pp.saturday_end, pp.sunday_start, pp.sunday_end FROM services s JOIN users u ON s.professional_id = u.id LEFT JOIN professional_profiles pp ON u.id = pp.user_id WHERE s.id = %s", [service_id])
    service_and_profile = cur.fetchone()
    if not service_and_profile:
        flash('Service not found.', 'danger')
        return redirect(url_for('home'))
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    availability = {}
    for day in days:
        availability[day] = {'start': str(service_and_profile[f'{day}_start']) if service_and_profile[f'{day}_start'] else None, 'end': str(service_and_profile[f'{day}_end']) if service_and_profile[f'{day}_end'] else None}
    if request.method == 'POST':
        appointment_date = request.form['appointment_date']
        appointment_time = request.form['appointment_time']
        try:
            appointment_datetime_obj = datetime.strptime(f"{appointment_date} {appointment_time}", '%Y-%m-%d %H:%M')
        except ValueError:
            flash('Invalid date or time format.', 'danger')
            return redirect(url_for('book_appointment', service_id=service_id))
        eat_timezone = timezone(timedelta(hours=3))
        now_in_eat = datetime.now(eat_timezone).replace(tzinfo=None)
        if appointment_datetime_obj < now_in_eat:
            flash('You cannot book an appointment in the past.', 'danger')
            return redirect(url_for('book_appointment', service_id=service_id))
        day_of_week_index = appointment_datetime_obj.weekday()
        day_name = days[day_of_week_index]
        day_schedule = availability[day_name]
        if not day_schedule['start'] or not day_schedule['end']:
            flash(f"The professional is not available on {day_name.capitalize()}s.", 'danger')
            return redirect(url_for('book_appointment', service_id=service_id))
        appt_time = appointment_datetime_obj.time()
        start_time = datetime.strptime(day_schedule['start'], '%H:%M:%S').time()
        end_time = datetime.strptime(day_schedule['end'], '%H:%M:%S').time()
        if not (start_time <= appt_time <= end_time):
            flash(f"The selected time is outside of the professional's working hours for that day.", 'danger')
            return redirect(url_for('book_appointment', service_id=service_id))
        cur.execute("INSERT INTO appointments (client_id, professional_id, service_id, appointment_datetime) VALUES (%s, %s, %s, %s)", (session['id'], service_and_profile['professional_id'], service_id, appointment_datetime_obj))
        mysql.connection.commit()
        cur.close()
        flash('Your appointment request has been sent!', 'success')
        return redirect(url_for('view_profile', username=service_and_profile['username']))
    cur.close()
    availability_json = json.dumps(availability)
    return render_template('book_appointment.html', service=service_and_profile, professional=service_and_profile, availability_json=availability_json)

@app.route('/appointment/update/<int:appointment_id>/<string:new_status>', methods=['POST'])
@is_logged_in
@is_professional
def update_appointment_status(appointment_id, new_status):
    professional_id = session['id']
    if new_status not in ['confirmed', 'cancelled', 'completed']:
        flash('Invalid status update.', 'danger')
        return redirect(url_for('manage_appointments'))
    cur = mysql.connection.cursor()
    cur.execute("UPDATE appointments SET status = %s WHERE id = %s AND professional_id = %s", (new_status, appointment_id, professional_id))
    mysql.connection.commit()
    cur.close()
    flash(f'Appointment has been {new_status}!', 'success')
    return redirect(url_for('manage_appointments'))

@app.route('/api/available_slots')
@is_logged_in
def available_slots():
    date_str = request.args.get('date')
    service_id = request.args.get('service_id')
    if not date_str or not service_id:
        return jsonify({'error': 'Missing date or service_id'}), 400
    cur = mysql.connection.cursor()
    cur.execute("SELECT professional_id, duration_minutes FROM services WHERE id = %s", [service_id])
    service_info = cur.fetchone()
    if not service_info:
        return jsonify({'error': 'Service not found'}), 404
    professional_id = service_info['professional_id']
    duration = timedelta(minutes=service_info['duration_minutes'])
    selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    day_of_week_index = selected_date.weekday()
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    day_name = days[day_of_week_index]
    cur.execute(f"SELECT {day_name}_start, {day_name}_end FROM professional_profiles WHERE user_id = %s", [professional_id])
    availability = cur.fetchone()
    start_time_str = availability.get(f'{day_name}_start')
    end_time_str = availability.get(f'{day_name}_end')
    if not start_time_str or not end_time_str:
        return jsonify([])
    cur.execute("SELECT appointment_datetime FROM appointments WHERE professional_id = %s AND DATE(appointment_datetime) = %s AND status = 'confirmed'", (professional_id, date_str))
    booked_times = [appt['appointment_datetime'] for appt in cur.fetchall()]
    cur.close()
    available_slots = []
    start_time = datetime.strptime(str(start_time_str), '%H:%M:%S').time()
    end_time = datetime.strptime(str(end_time_str), '%H:%M:%S').time()
    current_slot_start_dt = datetime.combine(selected_date, start_time)
    end_dt = datetime.combine(selected_date, end_time)
    while current_slot_start_dt + duration <= end_dt:
        is_booked = False
        for booked_dt in booked_times:
            if current_slot_start_dt < booked_dt + duration and current_slot_start_dt + duration > booked_dt:
                is_booked = True
                break
        if not is_booked:
            available_slots.append(current_slot_start_dt.strftime('%H:%M'))
        current_slot_start_dt += duration
    return jsonify(available_slots)

@app.route('/my_appointments')
@is_logged_in
def my_appointments():
    client_id = session['id']
    cur = mysql.connection.cursor()

    # CORRECTED QUERY: Added a.payment_status to the SELECT statement
    cur.execute("""
        SELECT 
            a.id, a.appointment_datetime, a.status, a.payment_status,
            s.service_name,
            p_user.username as professional_username,
            pp.business_name,
            r.id as review_id
        FROM appointments a
        JOIN services s ON a.service_id = s.id
        JOIN users p_user ON a.professional_id = p_user.id
        LEFT JOIN professional_profiles pp ON a.professional_id = pp.user_id
        LEFT JOIN reviews r ON a.id = r.appointment_id
        WHERE a.client_id = %s
        ORDER BY a.appointment_datetime DESC
    """, [client_id])
    
    all_appointments = cur.fetchall()
    cur.close()

    eat_timezone = timezone(timedelta(hours=3))
    now_in_eat = datetime.now(eat_timezone).replace(tzinfo=None)

    pending = [a for a in all_appointments if a['status'] == 'pending']
    # Confirmed appointments are those in the future
    confirmed = [a for a in all_appointments if a['status'] == 'confirmed' and a['appointment_datetime'] > now_in_eat]
    # History includes completed, cancelled, and past confirmed appointments
    history = [a for a in all_appointments if a['status'] in ['cancelled', 'completed'] or (a['status'] == 'confirmed' and a['appointment_datetime'] <= now_in_eat)]

    return render_template(
        'my_appointments.html', 
        pending=pending, 
        confirmed=confirmed, 
        history=history
    )
    
@app.route('/leave_review/<int:appointment_id>', methods=['GET', 'POST'])
@is_logged_in
def leave_review(appointment_id):
    client_id = session['id']
    cur = mysql.connection.cursor()

    # Security Check: Ensure this appointment exists, belongs to the client, is completed, and not yet reviewed.
    cur.execute("""
        SELECT a.*, s.service_name, p_user.username as professional_username, pp.business_name
        FROM appointments a
        JOIN services s ON a.service_id = s.id
        JOIN users p_user ON a.professional_id = p_user.id
        LEFT JOIN professional_profiles pp ON a.professional_id = pp.user_id
        LEFT JOIN reviews r ON a.id = r.appointment_id
        WHERE a.id = %s AND a.client_id = %s AND a.status = 'completed' AND r.id IS NULL
    """, (appointment_id, client_id))
    appointment = cur.fetchone()

    if not appointment:
        flash('This appointment cannot be reviewed.', 'danger')
        return redirect(url_for('my_appointments'))

    if request.method == 'POST':
        rating = request.form.get('rating')
        review_text = request.form.get('review_text')

        if not rating:
            flash('Please select a star rating.', 'danger')
            return redirect(url_for('leave_review', appointment_id=appointment_id))

        # Insert the new review into the database
        cur.execute("""
            INSERT INTO reviews (appointment_id, client_id, professional_id, rating, review_text)
            VALUES (%s, %s, %s, %s, %s)
        """, (appointment_id, client_id, appointment['professional_id'], rating, review_text))
        
        mysql.connection.commit()
        cur.close()

        flash('Thank you for your review!', 'success')
        return redirect(url_for('my_appointments'))

    # GET request
    cur.close()
    return render_template('leave_review.html', appointment=appointment)
# reviews function
@app.route('/dashboard/reviews')
@is_logged_in
@is_professional
def my_reviews():
    professional_id = session['id']
    cur = mysql.connection.cursor()

    # Get all reviews for this professional
    cur.execute("""
        SELECT 
            r.rating, r.review_text, r.created_at,
            c.username as client_username,
            s.service_name
        FROM reviews r
        JOIN users c ON r.client_id = c.id
        JOIN appointments a ON r.appointment_id = a.id
        JOIN services s ON a.service_id = s.id
        WHERE r.professional_id = %s
        ORDER BY r.created_at DESC
    """, [professional_id])
    reviews = cur.fetchall()

    # Calculate statistics
    cur.execute("""
        SELECT 
            COUNT(*) as total_reviews, 
            AVG(rating) as average_rating 
        FROM reviews 
        WHERE professional_id = %s
    """, [professional_id])
    stats = cur.fetchone()

    cur.close()

    return render_template('dashboard_reviews.html', reviews=reviews, stats=stats)

@app.route('/contact')
def contact():
    return render_template('contact.html')
# payment function
@app.route('/pay/<int:appointment_id>', methods=['GET', 'POST'])
@is_logged_in
def pay_for_appointment(appointment_id):
    client_id = session['id']
    cur = mysql.connection.cursor()
    
    # Security Check: Get the appointment details, ensuring it belongs to the logged-in client
    cur.execute("""
        SELECT a.*, s.service_name, s.price, u.username as professional_username, pp.business_name
        FROM appointments a
        JOIN services s ON a.service_id = s.id
        JOIN users u ON a.professional_id = u.id
        LEFT JOIN professional_profiles pp ON u.id = pp.user_id
        WHERE a.id = %s AND a.client_id = %s
    """, (appointment_id, client_id))
    appointment = cur.fetchone()

    if not appointment:
        flash('Appointment not found.', 'danger')
        return redirect(url_for('my_appointments'))
    
    if appointment['status'] != 'confirmed':
        flash('You can only pay for confirmed appointments.', 'warning')
        return redirect(url_for('my_appointments'))

    if appointment['payment_status'] == 'paid':
        flash('This appointment has already been paid for.', 'info')
        return redirect(url_for('my_appointments'))

    if request.method == 'POST':
        # This is a MOCK payment. We don't process any card details.
        # We just log the payment and update the status.
        
        # Generate a fake transaction ID
        transaction_id = 'YARA-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))

        # Log the payment in the 'payments' table
        cur.execute("""
            INSERT INTO payments (appointment_id, amount_paid, transaction_id)
            VALUES (%s, %s, %s)
        """, (appointment_id, appointment['price'], transaction_id))

        # Update the appointment status to 'paid'
        cur.execute("UPDATE appointments SET payment_status = 'paid' WHERE id = %s", [appointment_id])

        mysql.connection.commit()
        cur.close()

        flash('Payment successful! Thank you.', 'success')
        return redirect(url_for('my_appointments'))

    # GET request renders the payment form
    cur.close()
    return render_template('payment.html', appointment=appointment)

@app.route('/dashboard/revenue')
@is_logged_in
@is_professional
def revenue_dashboard():
    professional_id = session['id']
    cur = mysql.connection.cursor()

    # Get all payments for this professional
    cur.execute("""
        SELECT p.amount_paid, p.payment_date, u.username as client_username, s.service_name
        FROM payments p
        JOIN appointments a ON p.appointment_id = a.id
        JOIN users u ON a.client_id = u.id
        JOIN services s ON a.service_id = s.id
        WHERE a.professional_id = %s
        ORDER BY p.payment_date DESC
    """, [professional_id])
    payments = cur.fetchall()

    # Calculate statistics
    cur.execute("""
        SELECT COUNT(*) as total_payments, SUM(p.amount_paid) as total_revenue
        FROM payments p
        JOIN appointments a ON p.appointment_id = a.id
        WHERE a.professional_id = %s
    """, [professional_id])
    stats = cur.fetchone()

    # Prepare data for the chart (revenue per month for the current year)
    chart_labels = [calendar.month_abbr[i] for i in range(1, 13)]
    chart_data = [0] * 12
    current_year = datetime.now().year
    
    for payment in payments:
        if payment['payment_date'].year == current_year:
            month_index = payment['payment_date'].month - 1
            chart_data[month_index] += payment['amount_paid']

    cur.close()

    return render_template(
        'dashboard_revenue.html', 
        payments=payments, 
        stats=stats,
        chart_data={'labels': chart_labels, 'data': chart_data}
    )
    
@app.route('/dashboard/portfolio')
@is_logged_in
@is_professional
def manage_portfolio():
    user_id = session['id']
    cur = mysql.connection.cursor()
    cur.execute("SELECT filename, tags FROM portfolio_images WHERE professional_id = %s", [user_id])
    portfolio_images = cur.fetchall()
    cur.close()
    return render_template('dashboard_portfolio.html', portfolio_images=portfolio_images)

@app.route('/portfolio/upload', methods=['POST'])
@is_logged_in
@is_professional
def upload_portfolio():
    user_id = session['id']
    if 'portfolio_images' not in request.files:
        flash('No file part in request.', 'danger')
        return redirect(url_for('manage_portfolio'))
        
    files = request.files.getlist('portfolio_images')
    cur = mysql.connection.cursor()

    for file in files:
        if file.filename == '' or not allowed_file(file.filename):
            continue
        
        filename = secure_filename(file.filename)
        unique_filename = f"{user_id}_{int(datetime.now().timestamp())}_{filename}"
        filepath = os.path.join(app.config['PORTFOLIO_UPLOAD_FOLDER'], unique_filename)
        
        file.save(filepath)
        
        # Insert image record into the new table, tags will be added later
        cur.execute(
            "INSERT INTO portfolio_images (professional_id, filename) VALUES (%s, %s)",
            (user_id, unique_filename)
        )
    
    mysql.connection.commit()
    cur.close()
    flash('Your portfolio has been updated!', 'success')
    return redirect(url_for('manage_portfolio'))

@app.route('/portfolio/delete', methods=['POST'])
@is_logged_in
@is_professional
def delete_portfolio_image():
    user_id = session['id']
    filename_to_delete = request.form.get('filename')

    if not filename_to_delete:
        flash('No filename provided.', 'danger')
        return redirect(url_for('manage_portfolio'))

    cur = mysql.connection.cursor()
    # Security check: Ensure the image belongs to the logged-in user before deleting
    cur.execute("DELETE FROM portfolio_images WHERE filename = %s AND professional_id = %s", (filename_to_delete, user_id))
    
    if cur.rowcount > 0: # Check if a row was actually deleted
        mysql.connection.commit()
        try:
            os.remove(os.path.join(app.config['PORTFOLIO_UPLOAD_FOLDER'], filename_to_delete))
            flash('Image deleted successfully.', 'success')
        except OSError as e:
            flash(f'Error deleting file from server: {e}', 'danger')
    else:
        flash('Image not found in your portfolio.', 'danger')
    
    cur.close()
    return redirect(url_for('manage_portfolio'))

@app.route('/portfolio/update_tags', methods=['POST'])
@is_logged_in
@is_professional
def update_tags():
    user_id = session['id']
    filename = request.form.get('filename')
    tags = request.form.get('tags')

    cur = mysql.connection.cursor()
    # Security check: ensure professional owns the image they are tagging
    cur.execute(
        "UPDATE portfolio_images SET tags = %s WHERE filename = %s AND professional_id = %s",
        (tags, filename, user_id)
    )
    mysql.connection.commit()
    cur.close()
    
    flash('Tags updated successfully!', 'success')
    return redirect(url_for('manage_portfolio'))

@app.route('/image_search', methods=['GET', 'POST'])
@is_logged_in
def image_search():
    if request.method == 'POST':
        if 'inspiration_image' not in request.files:
            flash('No file part in request.', 'danger')
            return redirect(request.url)
        
        file = request.files['inspiration_image']

        if file.filename == '':
            flash('No file selected.', 'warning')
            return redirect(request.url)

        if file and allowed_file(file.filename):
            # --- FINAL AI MATCHING LOGIC ---
            
            # 1. Simulate the AI by extracting tags from the filename.
            # E.g., "blonde-box-braids.jpg" -> ['blonde', 'box', 'braids']
            filename = secure_filename(file.filename)
            # Remove the extension and replace hyphens/underscores with spaces
            base_name = os.path.splitext(filename)[0]
            search_tags = base_name.replace('_', ' ').replace('-', ' ').split()
            
            if not search_tags:
                flash("Couldn't identify any keywords from the filename. Please use a descriptive filename.", 'warning')
                return redirect(url_for('image_search'))

            cur = mysql.connection.cursor()
            
            # 2. Build the dynamic part of the SQL query
            # We will search for any portfolio where the 'tags' column contains any of our keywords
            sql_where_clauses = []
            sql_params = []
            for tag in search_tags:
                sql_where_clauses.append("pi.tags LIKE %s")
                sql_params.append(f"%{tag}%")

            if not sql_where_clauses:
                return render_template('search_results.html', results=[], query=base_name.replace(' ', ', '))

            # 3. Construct the full query to find the professionals
            query = f"""
                SELECT DISTINCT u.id, u.username, pp.business_name, pp.location, pp.profile_pic_filename
                FROM users u
                JOIN professional_profiles pp ON u.id = pp.user_id
                JOIN portfolio_images pi ON u.id = pi.professional_id
                WHERE u.user_type = 'professional' AND ({' OR '.join(sql_where_clauses)})
            """
            
            cur.execute(query, tuple(sql_params))
            results = cur.fetchall()
            cur.close()

            # 5. Render the results page
            return render_template('search_results.html', results=results, query=f"inspiration: {', '.join(search_tags)}")

    # GET request just shows the upload form
    return render_template('image_search.html')
# --- Run the App ---
if __name__ == '__main__':
    app.run(debug=True)
