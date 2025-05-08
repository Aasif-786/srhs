import os
from datetime import datetime
from flask import render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from app import app, db
from models import User, HealthRecord, DoctorAccess
from forms import LoginForm, RegistrationForm, HealthRecordForm, DoctorAccessForm

def save_file(file):
    """Ensure uploaded files are properly saved and accessible"""
    try:
        filename = secure_filename(file.filename)
        # Create a unique filename to avoid overwrites
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
        unique_filename = timestamp + filename

        # Create uploads directory in static folder
        uploads_dir = os.path.join('static', 'uploads')
        os.makedirs(uploads_dir, exist_ok=True)

        # Save file with full path from app root
        full_path = os.path.join(current_app.root_path, uploads_dir, unique_filename)
        file.save(full_path)
        current_app.logger.info(f"File saved at: {full_path}")

        # Return relative path for database storage (relative to static folder)
        return 'uploads/' + unique_filename
    except Exception as e:
        current_app.logger.error(f"Error saving file: {str(e)}")
        raise

@app.route('/home')
def home():
    return render_template('home.html')

@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.role == 'patient':
            return redirect(url_for('patient_dashboard'))
        else:
            return redirect(url_for('doctor_dashboard'))
    return redirect(url_for('home'))  # Now redirects to the home route

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            return redirect(url_for('index'))
        flash('Invalid email or password')
    return render_template('login.html', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data, role=form.role.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash('Registration successful!')
        return redirect(url_for('login'))
    return render_template('register.html', form=form)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/patient/dashboard')
@login_required
def patient_dashboard():
    if current_user.role != 'patient':
        return redirect(url_for('index'))
    records = HealthRecord.query.filter_by(user_id=current_user.id).all()
    doctors = User.query.join(DoctorAccess, DoctorAccess.doctor_id == User.id)\
        .filter(DoctorAccess.patient_id == current_user.id).all()
    return render_template('patient/dashboard.html', records=records, doctors=doctors)

@app.route('/doctor/dashboard')
@login_required
def doctor_dashboard():
    if current_user.role != 'doctor':
        return redirect(url_for('index'))
    # Get all patients who have granted access to this doctor
    access_records = DoctorAccess.query.filter_by(doctor_id=current_user.id).all()
    patients = User.query.filter(User.id.in_([a.patient_id for a in access_records])).all()
    return render_template('doctor/dashboard.html', patients=patients)

@app.route('/patient/<int:patient_id>/records')
@login_required
def view_patient_records(patient_id):
    if current_user.role != 'doctor':
        flash('Only doctors can view patient records')
        return redirect(url_for('index'))

    # Check if the doctor has access to this patient's records
    access = DoctorAccess.query.filter_by(
        patient_id=patient_id,
        doctor_id=current_user.id
    ).first()

    if not access:
        flash('Access denied. You do not have permission to view these records.')
        return redirect(url_for('doctor_dashboard'))

    patient = User.query.get_or_404(patient_id)
    records = HealthRecord.query.filter_by(user_id=patient_id).order_by(HealthRecord.date.desc()).all()

    # Verify file existence and accessibility
    for record in records:
        if record.file_path:
            # Check if file exists relative to the static folder
            static_file_path = os.path.join(current_app.root_path, 'static', record.file_path)
            record.file_exists = os.path.exists(static_file_path)
              # Store just the relative path for template rendering
            record.file_url = record.file_path
            current_app.logger.info(f"Checking file {static_file_path}: exists={record.file_exists},URL={record.file_url}")
        else:
            record.file_exists = False
            record.file_url = None
    return render_template('doctor/patient_records.html', patient=patient, records=records)

@app.route('/record/new', methods=['GET', 'POST'])
@login_required
def new_record():
    if current_user.role != 'patient':
        return redirect(url_for('index'))
    form = HealthRecordForm()
    if form.validate_on_submit():
        try:
            file_path = save_file(form.file.data)
            current_app.logger.info(f"File saved successfully at: {file_path}")

            record = HealthRecord(
                title=form.title.data,
                content=form.content.data,
                record_type=form.record_type.data,
                file_path=file_path,
                file_type=form.file.data.content_type,
                user_id=current_user.id
            )
            db.session.add(record)
            db.session.commit()
            flash('Record added successfully!')
            return redirect(url_for('patient_dashboard'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating record: {str(e)}")
            flash('Error uploading file. Please try again.')
    return render_template('patient/records.html', form=form)

@app.route('/patient/manage-access', methods=['GET', 'POST'])
@login_required
def manage_doctor_access():
    if current_user.role != 'patient':
        return redirect(url_for('index'))
    form = DoctorAccessForm()
    if form.validate_on_submit():
        doctor = User.query.filter_by(email=form.doctor_email.data, role='doctor').first()
        if doctor:
            current_user.grant_access_to_doctor(doctor.id)
            flash(f'Access granted to Dr. {doctor.username}')
        else:
            flash('Doctor not found with this email')
    doctors = User.query.join(DoctorAccess, DoctorAccess.doctor_id == User.id)\
        .filter(DoctorAccess.patient_id == current_user.id).all()
    return render_template('patient/manage_access.html', form=form, doctors=doctors)

@app.route('/patient/revoke-access/<int:doctor_id>')
@login_required
def revoke_doctor_access(doctor_id):
    if current_user.role != 'patient':
        return redirect(url_for('index'))
    current_user.revoke_access_from_doctor(doctor_id)
    flash('Access revoked successfully')
    return redirect(url_for('manage_doctor_access'))