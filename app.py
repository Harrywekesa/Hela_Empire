from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import relationship
from werkzeug.security import generate_password_hash, check_password_hash
import os
import random
import string
import requests
from requests.auth import HTTPBasicAuth
import base64
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# MPesa credentials
MPESA_CONSUMER_KEY = '0VPGtebKC7fA2ZJ3OSFs7kMdAj05l6OdwcxFALmZu7tAFu1y'
MPESA_CONSUMER_SECRET = '90LgyoqXJpTGyq7QxzZIIFy7eigHOR9WQ7jjqIDtWXefqWS3fOmzECKO74uCVlH3'
MPESA_SHORTCODE = '600979'
MPESA_PASSKEY = 'bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919'

app = Flask(__name__, static_folder='static')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.urandom(24)

db = SQLAlchemy(app)

# Define User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone_number = db.Column(db.String(20), unique=True, nullable=False)
    password_hash = db.Column(db.String(100), nullable=False)
    referral_code = db.Column(db.String(20), unique=True, nullable=False)
    referred_by = db.Column(db.String(20))
    account_activated = db.Column(db.Boolean, default=False)
    verification_code = db.Column(db.String(4))
    is_verified = db.Column(db.Boolean, default=False)
    balance = db.Column(db.Float, default=0.0)
    referrals = relationship('Referral', backref='user', lazy=True)

    @property
    def password(self):
        raise AttributeError('Password is not readable')

    @password.setter
    def password(self, password):
        self.password_hash = generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    def refer(self, referred_user):
        if self.id == referred_user.id:
            return False, "You cannot refer yourself."
        
        if referred_user.referred_by:
            return False, "User is already referred by someone else."
        
        referred_user.referred_by = self.referral_code
        db.session.commit()
        
        referral_levels = [
            {'level': 1, 'earning': 100},
            {'level': 2, 'earning': 75},
            {'level': 3, 'earning': 50},
            {'level': 4, 'earning': 25}
        ]
        
        referring_user = self
        for level in referral_levels:
            if level['level'] == 1:
                referring_user.balance += level['earning']
            else:
                referring_user = User.query.filter_by(referral_code=referring_user.referred_by).first()
                if referring_user:
                    referring_user.balance += level['earning']
                    db.session.commit()
        
        return True, "Referral successful."

    def get_referrals_by_level(self, level=1):
        referrals = User.query.filter_by(referred_by=self.referral_code).all()
        referral_data = []
        if level > 1:
            for referral in referrals:
                referral_data.extend(referral.get_referrals_by_level(level-1))
        return referrals, referral_data

    def get_referral_hierarchy(self, level=1):
        hierarchy = {}
        referrals, _ = self.get_referrals_by_level(level)
        for referral in referrals:
            hierarchy[referral.phone_number] = referral.get_referral_hierarchy(level-1)
        return hierarchy

    def get_referral_earnings(self):
        earnings = []
        referral_levels = [
            {'level': 1, 'earning': 100},
            {'level': 2, 'earning': 75},
            {'level': 3, 'earning': 50},
            {'level': 4, 'earning': 25}
        ]
        
        for level, earning in enumerate(referral_levels, 1):
            level_referrals, _ = self.get_referrals_by_level(level)
            for referral in level_referrals:
                earnings.append({'phone_number': referral.phone_number, 'level': level, 'earning': earning['earning']})
        
        return earnings

class Referral(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    referral_level = db.Column(db.Integer, nullable=False)
    amount = db.Column(db.Float, nullable=False)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    referral_code_from_url = request.args.get('referral_code')
    if request.method == 'POST':
        data = request.form
        phone_number = data.get('phone_number')
        password = data.get('password')
        referral_code_input = data.get('referral_code')

        existing_user = User.query.filter_by(phone_number=phone_number).first()
        if existing_user:
            return jsonify({'message': 'Phone number already registered'}), 400

        new_referral_code = generate_referral_code()
        new_user = User(
            phone_number=phone_number,
            password=password,
            referral_code=new_referral_code,
        )

        referral_code = referral_code_input or referral_code_from_url
        if referral_code:
            referring_user = User.query.filter_by(referral_code=referral_code).first()
            if referring_user:
                new_user.referred_by = referring_user.referral_code

        db.session.add(new_user)
        db.session.commit()

        return redirect(url_for('login'))

    return render_template('register.html', referral_code=referral_code_from_url)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.form
        phone_number = data.get('phone_number')
        password = data.get('password')

        if not phone_number or not password:
            return render_template('login.html', error='Please enter both phone number and password.')

        user = User.query.filter_by(phone_number=phone_number).first()
        if not user or not user.verify_password(password):
            return render_template('login.html', error='Invalid phone number or password.')

        session['user_id'] = user.id
        return redirect(url_for('dashboard', user_id=user.id))

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

@app.route('/dashboard/<int:user_id>', methods=['GET'])
def dashboard(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'message': 'User not found'}), 404
    
    if not user.account_activated:
        return render_template('activate.html', user_id=user_id, phone_number=user.phone_number)

    dashboard_data = {
        'phone_number': user.phone_number,
        'balance': user.balance,
        'referral_code': user.referral_code,
        'referred_by': user.referred_by,
        'account_activated': user.account_activated
    }
    
    return render_template('dashboard.html', dashboard_data=dashboard_data, user_id=user_id)

@app.route('/activate/<int:user_id>', methods=['POST'])
def activate_account(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'message': 'User not found'}), 404
    
    # Initiate the MPesa payment
    response = initiate_mpesa_payment(user.phone_number, 500)
    app.logger.info(f"MPesa payment response: {response}")

    if response.get('ResponseCode') == '0':  # Successful request initiation
        return jsonify({'message': 'Payment initiated successfully. Please wait for confirmation.'}), 200
    else:
        return jsonify({'message': 'Failed to initiate payment'}), 400


@app.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    data = request.json
    app.logger.info(f"MPesa callback data: {data}")

    # Process the callback data
    result_code = data['Body']['stkCallback']['ResultCode']
    result_desc = data['Body']['stkCallback']['ResultDesc']
    merchant_request_id = data['Body']['stkCallback']['MerchantRequestID']
    checkout_request_id = data['Body']['stkCallback']['CheckoutRequestID']

    # Log the result
    app.logger.info(f"Result code: {result_code}, Result description: {result_desc}")

    if result_code == 0:
        # Payment was successful
        phone_number = data['Body']['stkCallback']['CallbackMetadata']['Item'][4]['Value']
        user = User.query.filter_by(phone_number=phone_number).first()
        
        if user:
            user.account_activated = True
            user.balance = 0  # Balance is set to 0 after activation
            db.session.commit()

            # Process referral earnings
            referral_earnings = {1: 300, 2: 300, 3: 75}
            referring_user = User.query.filter_by(referral_code=user.referred_by).first()
            
            if referring_user:
                # Level 1 activation
                if referring_user.id != user.id:
                    referring_user.balance += referral_earnings[1]
                    db.session.commit()
                    
                    # Process Level 2
                    if referring_user.referred_by:
                        level_1_referrer = User.query.filter_by(referral_code=referring_user.referred_by).first()
                        if level_1_referrer:
                            level_1_referrer.balance += referral_earnings[2]
                            db.session.commit()

                            # Process Level 3
                            if level_1_referrer.referred_by:
                                level_2_referrer = User.query.filter_by(referral_code=level_1_referrer.referred_by).first()
                                if level_2_referrer:
                                    level_2_referrer.balance += referral_earnings[3]
                                    db.session.commit()

            return jsonify({'message': 'Payment processed successfully'}), 200
        else:
            return jsonify({'message': 'User not found'}), 404
    else:
        return jsonify({'message': f'Payment failed: {result_desc}'}), 400



@app.route('/withdraw/<int:user_id>', methods=['POST'])
def withdraw(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'message': 'User not found'}), 404
    
    data = request.get_json()
    amount = data.get('amount')

    if not amount or amount < 50:
        return jsonify({'message': 'Minimum withdrawal amount is Ksh. 50'}), 400
    
    if user.balance < amount:
        return jsonify({'message': 'Insufficient funds'}), 400
    
    response = initiate_mpesa_payment(user.phone_number, amount)
    if response.get('ResponseCode') == '0':  # Successful request
        user.balance -= amount
        db.session.commit()
        return jsonify({'message': 'Withdrawal successful'}), 200
    else:
        return jsonify({'message': 'Failed to process withdrawal'}), 400

@app.route('/get_referrals_data/<int:user_id>')
def get_referrals_data(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'message': 'User not found'}), 404

    referral_earnings = user.get_referral_earnings()
    referrals_by_level = {1: [], 2: [], 3: [], 4: []}
    for earning in referral_earnings:
        referrals_by_level[earning['level']].append({
            'phone_number': earning['phone_number'],
            'earning': earning['earning']
        })
    return jsonify({'referrals': referrals_by_level})

@app.route('/referrals_page/<int:user_id>')
def referrals_page(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'message': 'User not found'}), 404

    referral_earnings = user.get_referral_earnings()
    referrals_by_level = {1: [], 2: [], 3: [], 4: []}
    for earning in referral_earnings:
        referrals_by_level[earning['level']].append({
            'phone_number': earning['phone_number'],
            'earning': earning['earning']
        })
    return render_template('referrals_page.html', user=user, referrals_by_level=referrals_by_level)

def generate_mpesa_password():
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    password_str = MPESA_SHORTCODE + MPESA_PASSKEY + timestamp
    password = base64.b64encode(password_str.encode()).decode('utf-8')
    return password, timestamp

def get_mpesa_token():
    url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    response = requests.get(url, auth=HTTPBasicAuth(MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET))
    if response.status_code == 200:
        return response.json()['access_token']
    else:
        raise Exception("Failed to generate token")

def initiate_mpesa_payment(phone_number, amount):
    try:
        url = 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
        headers = {
            'Authorization': 'Bearer {}'.format(get_mpesa_token()),
            'Content-Type': 'application/json',
        }
        password, timestamp = generate_mpesa_password()
        payload = {
            "BusinessShortCode": MPESA_SHORTCODE,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount,
            "PartyA": 254741947264,
            "PartyB": "600000",
            "PhoneNumber": phone_number,
            "CallBackURL": "https://7937-197-138-206-10.ngrok-free.app/mpesa/callback",
            "AccountReference": "TestPay",
            "TransactionDesc": "Payment for activation"
        }
        response = requests.post(url, json=payload, headers=headers)
        logging.debug("MPESA Request Payload: %s", payload)
        logging.debug("MPESA Response: %s", response.json())
        return response.json()
    except Exception as e:
        logging.error("Error initiating MPESA payment: %s", e)
        return {'ResponseCode': '1', 'ResponseDescription': 'Error initiating payment'}



@app.route('/referrals/<int:user_id>')
def referrals(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'message': 'User not found'}), 404

    hierarchy = user.get_referral_hierarchy()
    return render_template('referrals.html', hierarchy=hierarchy)

@app.route('/referral_link/<int:user_id>')
def referral_link(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'message': 'User not found'}), 404

    referral_link = url_for('register', referral_code=user.referral_code, _external=True)
    return render_template('referral_link.html', referral_link=referral_link)

def generate_referral_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

# Create the database tables within the application context
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)
