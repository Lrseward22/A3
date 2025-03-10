from flask import Flask, redirect, request, url_for, session, render_template
import os
import models

# Note for Dr Shepherd. I used chat GPT in this code to make my css for me 
# (I did previously know about css classes and tags and stuff). I also used
# it to enhance my product descriptions.

app = Flask(__name__)
app.secret_key = 'super secrete key'

db_name = 'Congo.db'
sqlite_uri = f'sqlite:///{os.path.abspath(os.path.curdir)}/{db_name}'
app.config['SQLALCHEMY_DATABASE_URI'] = sqlite_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = models.db
db.init_app(app)

with app.app_context():
    db.create_all()

@app.before_request
def check_login():
    allowed_routes = [
        url_for('index'),
        url_for('register'),
        url_for('login_form'),
        url_for('create_user'),
        url_for('login'),
        url_for('get_orders'),
    ]
    if 'username' not in session and request.path not in allowed_routes and not request.path.startswith('/static'):
        return redirect(url_for('login_form'))

@app.route('/')
def index():
    if session.get('username'):
        return redirect(url_for('get_items'))
    else:
        return redirect(url_for('login_form'))

@app.route('/register/', methods=['GET'])
def register():
    return render_template('register_form.html', title='Register User', username=session.get('username'))

@app.route('/users/', methods=['POST'])
def create_user():
    if models.has_user(request.form['username']):
        return render_template('register_form.html', title='Home', username=session.get('username'), username_message='Username already exists. Please choose another.')
    if request.form['password'] != request.form['password_conf']:
        return render_template('register_form.html', title='Home', username=session.get('username'), password_message='Passwords must match.')
    # Create new user in database
    if not models.create_user(request.form['username'], request.form['name'], request.form['email'],
                              request.form['address'], request.form['payment'], request.form['password']):
        return render_template('register_form.html', title='Home', username=session.get('username'), create_message='An unknown error occured when creating this User')
    return redirect(url_for('login_form'))


@app.route('/login/', methods=['GET'])
def login_form():
    return render_template('login_form.html', title='Login', username=session.get('username'))

@app.route('/login/', methods=['POST'])
def login():
    username = request.form["username"]
    user = models.User.query.filter_by(username=username).first()
    if not user or user.password != request.form["password"]:
        return render_template('login_form.html', title='Login', username=session.get('username'), message='Wrong username or password')
    else:
        session['username'] = username
        return redirect(url_for('get_items'))

@app.route('/items/', methods=['GET'])
def get_items():
    session['items'] = models.add_items()
    items = models.Item.query.all()
    return render_template('items.html', title='Shop', username=session.get('username'), items=items)

@app.route('/items/<itemid>/', methods=['GET'])
def get_item(itemid):
    item = models.Item.query.filter_by(id=itemid).first()
    return render_template('get_item.html', title='Shop', username=session.get('username'), item=item)

@app.route('/cart/', methods=['POST'])
def add_to_cart():
    if request.form['item'] in session:
        num_in_cart = session[request.form['item']] + int(request.form['quantity'])
        session[request.form['item']] = num_in_cart
    else:
        session[request.form['item']] = int(request.form['quantity'])
    return redirect(url_for('show_cart'))

@app.route('/cart/', methods=['GET'])
def show_cart():
    items = session['items']
    return render_template('show_cart.html', title='Cart', username=session.get('username'), items=items)


@app.route('/cart/', methods=['DELETE'])
def delete_cart_item():
    item = request.json['item']
    # I hate this so much
    if item not in session:
        return 'Item not in cart.', 404
    del session[item]
    return '', 200


@app.route('/checkout/', methods=['GET'])
def get_checkout():
    items = [
        (x, session[x.name])
        for x in models.Item.query.all()
        if x.name in session and session[x.name]
    ]
    total = sum(x[0].price * x[1] for x in items)
    user = models.User.query.filter_by(username=session['username']).first()
    return render_template('checkout.html', title='Checkout', username=session.get('username'), items=items, total=f"{total:,.2f}", user=user)


@app.route('/orders/', methods=['POST'])
def post_order():
    user = models.User.query.filter_by(username=session['username']).first()
    items = [
        (x, session[x.name])
        for x in models.Item.query.all()
        if x.name in session and session[x.name]
    ]
    total = sum(x[0].price * x[1] for x in items)

    order = models.Order(
        name=user.name,
        address=user.address,
        total=total,
    )
    db.session.add(order)
    db.session.commit()

    for item, qty in items:
        db.session.add(models.OrderItem(order_id=order.id, item_id=item.id, quantity=qty))

    db.session.commit()

    for item in session.get('items'):
        session.pop(item, None)

    return '', 200


@app.route('/orders/', methods=['GET'])
def get_orders():
    orders = models.Order.query.all()
    for order in orders:
        for item in order.items:
            print(item.item)

    return render_template('orders.html', title='Orders', username=session.get('username'), orders=orders)


@app.route('/logout/', methods=['GET'])
def logout_form():
    return render_template('logout.html', title='Logout', username=session.get('username'))

@app.route('/logout/', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login_form'))

app.run()
